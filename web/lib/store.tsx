"use client";

// Shared client state that must survive route changes AND refresh. Lives in a
// context provider mounted in the root layout. Persisted to localStorage (mock mode):
// nickname, admin-created contests, notifications + "seen" marker, and per-contest
// submission history — so a submission doesn't vanish on navigate/refresh and the
// notification badge stays cleared once read.

import { createContext, useContext, useEffect, useRef, useState, type ReactNode } from "react";
import {
  CONTESTS, INITIAL_NOTIFS, STEP_HISTORY, CH_HISTORY, fmt,
  type Contest, type Notif, type StepSub, type ChSub,
} from "@/lib/mock";
import {
  apiEnabled, getMe, getContests, setNickname as apiSetNickname, Unauthorized, loginUrl,
  getNotifications, markNotificationsRead,
  type ApiContest, type ApiNotif,
} from "@/lib/api";

type Toast = { msg: string; sub?: string } | null;
type SubMap<T> = Record<string, T[]>;
// "loading" until resolved; "mock" = localStorage demo; api: login/nickname/ready gates.
type AuthState = "loading" | "mock" | "login" | "nickname" | "ready";

// /api/contests rows are minimal; fill the richer demo fields with sane defaults until
// the detail/standings/submission endpoints are wired (those carry scores).
function mapApiContest(a: ApiContest): Contest {
  const status: Contest["status"] = a.status === "live" ? "live"
    : (a.status === "ended" || a.status === "archived") ? "ended" : "soon";
  const when = `${a.starts_at.slice(0, 10)} ~ ${a.ends_at.slice(0, 10)}`;
  // desc/rank/nextEval/scores aren't in the list payload — the detail page fills them.
  return { id: a.id, title: a.title, status, when, desc: "", total: 1000000, gotten: 0 };
}

// Render a server notification row as the bell-panel {icon,msg,sub}. Payload keys
// mirror what the API/worker write (grading.py, worker.py, grade_round.py); read
// them defensively so a schema tweak degrades to a generic line, never a crash.
function mapNotif(n: ApiNotif): Notif {
  const p = (n.payload ?? {}) as Record<string, any>;
  const when = n.created_at?.slice(0, 16).replace("T", " ") ?? "";
  if (n.type === "grading_done") {
    if (p.kind === "stepup") {
      const ok = p.valid !== false;
      return { icon: ok ? "✓" : "✕", msg: `미션 ${p.mission_seed} 채점 완료`,
        sub: (ok ? `점수 ${fmt(Number(p.score) || 0)} · 비용 ${p.cost}` : "무효 (0점)") + ` · ${when}` };
    }
    const sum = Number(p.cost_sum ?? p.sample_score_sum ?? 0);
    return { icon: "✓", msg: "챌린지 제출 채점 완료",
      sub: `샘플 비용 합 ${fmt(sum)} · 점수는 중간 평가 후 산정 · ${when}` };
  }
  if (n.type === "round_published") {
    return { icon: "🏅", msg: `${p.type === "final" ? "최종" : "중간"} 평가 결과 공개`,
      sub: `등수 ${p.rank ?? "-"} · 총점 ${fmt(Number(p.total_score) || 0)} · ${when}` };
  }
  if (n.type === "contest_ended") {
    return { icon: "🏁", msg: "대회 종료 — 최종 결과 공개",
      sub: `최종 등수 ${p.final_rank ?? p.rank ?? "-"} · ${when}` };
  }
  return { icon: "📢", msg: String(p.title ?? p.message ?? "알림"), sub: when };
}

type Store = {
  ready: boolean;
  apiMode: boolean;
  authState: AuthState;
  isAdmin: boolean;               // gate admin-only UI (create); mock mode = true (demo)
  isTester: boolean;              // may access tester-only (draft) contests
  loginUrl: () => string;
  nick: string | null;
  setNick: (n: string) => void;
  contests: Contest[];
  addContest: (c: Contest) => void;
  notifs: Notif[];
  unseen: number;                 // count not yet viewed (badge); clears on panel open
  markNotifsSeen: () => void;
  refreshNotifs: () => void;      // API mode: re-pull server notifications (after a submit)
  push: (icon: string, msg: string, sub: string, toastSub?: string) => void;
  // per-contest submission history (persisted) — survives navigation + refresh.
  stepSubsFor: (cid: string) => StepSub[];
  chSubsFor: (cid: string) => ChSub[];
  addStepSub: (cid: string, sub: StepSub) => void;
  addChSub: (cid: string, sub: ChSub) => void;
  setRepCh: (cid: string, id: number) => void;
  toast: Toast;
  showToast: (msg: string, sub?: string) => void;
  closeToast: () => void;
  notifOpen: boolean;
  setNotifOpen: (o: boolean) => void;
};

const Ctx = createContext<Store | null>(null);
export function useStore(): Store {
  const c = useContext(Ctx);
  if (!c) throw new Error("useStore must be used within <StoreProvider>");
  return c;
}

const NICK_KEY = "dmpc_nick";
const CONTESTS_KEY = "dmpc_contests"; // only admin-created contests (besides the defaults)
const NOTIFS_KEY = "dmpc_notifs";
const SEEN_KEY = "dmpc_seen";
const SUBS_KEY = "dmpc_subs";          // { step: {cid: StepSub[]}, ch: {cid: ChSub[]} }

// Demo seed history (only the example contest c1 has prior submissions).
const DEMO_STEP: SubMap<StepSub> = { c1: STEP_HISTORY };
const DEMO_CH: SubMap<ChSub> = { c1: CH_HISTORY };

export function StoreProvider({ children }: { children: ReactNode }) {
  const [ready, setReady] = useState(false);
  const [apiMode, setApiMode] = useState(false);
  const [authState, setAuthState] = useState<AuthState>("loading");
  const [isAdmin, setIsAdmin] = useState(false);
  const [isTester, setIsTester] = useState(false);
  const [nick, setNickState] = useState<string | null>(null);
  const [contests, setContests] = useState<Contest[]>(CONTESTS);
  const [notifs, setNotifs] = useState<Notif[]>(INITIAL_NOTIFS);
  const [seen, setSeen] = useState(0);              // how many notifs the user has viewed
  const [stepSubs, setStepSubs] = useState<SubMap<StepSub>>({});
  const [chSubs, setChSubs] = useState<SubMap<ChSub>>({});
  const [toast, setToast] = useState<Toast>(null);
  const [notifOpen, setNotifOpen] = useState(false);
  const tRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  // ---- load state once: REAL API (opt-in) or the localStorage mock (default) ----
  useEffect(() => {
    if (apiEnabled()) {
      setApiMode(true);
      (async () => {
        try {
          const me = await getMe();           // 401 -> Unauthorized -> show login
          setIsAdmin(me.role === "admin");
          setIsTester(!!me.is_tester);
          if (me.needs_nickname) { setNickState(null); setAuthState("nickname"); }
          else { setNickState(me.nickname); setAuthState("ready"); }
          try { setContests((await getContests()).map(mapApiContest)); } catch { /* keep empty */ }
          try {
            const ns = await getNotifications();
            setNotifs(ns.map(mapNotif));
            setSeen(ns.filter((x) => x.read).length);   // unseen badge = unread count
          } catch { setNotifs([]); }
        } catch (e) {
          setAuthState(e instanceof Unauthorized ? "login" : "login");
        } finally { setReady(true); }
      })();
      return;
    }
    // ---- mock mode (default): hydrate from localStorage ----
    try {
      const n = localStorage.getItem(NICK_KEY);
      if (n) setNickState(n);
      const raw = localStorage.getItem(CONTESTS_KEY);
      if (raw) {
        const created = JSON.parse(raw) as Contest[];
        if (Array.isArray(created) && created.length) setContests([...created, ...CONTESTS]);
      }
      const nf = localStorage.getItem(NOTIFS_KEY);
      if (nf) { const arr = JSON.parse(nf); if (Array.isArray(arr)) setNotifs(arr); }
      const sn = localStorage.getItem(SEEN_KEY);
      if (sn) setSeen(parseInt(sn, 10) || 0);
      const sb = localStorage.getItem(SUBS_KEY);
      if (sb) {
        const { step, ch } = JSON.parse(sb) || {};
        if (step && typeof step === "object") setStepSubs(step);
        if (ch && typeof ch === "object") setChSubs(ch);
      }
    } catch {}
    setIsAdmin(true);              // mock demo has no auth — let the operator try authoring
    setIsTester(true);
    setAuthState("mock");
    setReady(true);
  }, []);

  // ---- persist on change (mock mode only; API mode is server-backed) ----
  useEffect(() => {
    if (!ready || apiMode) return;
    try {
      localStorage.setItem(NOTIFS_KEY, JSON.stringify(notifs));
      localStorage.setItem(SEEN_KEY, String(seen));
    } catch {}
  }, [ready, apiMode, notifs, seen]);
  useEffect(() => {
    if (!ready || apiMode) return;
    try { localStorage.setItem(SUBS_KEY, JSON.stringify({ step: stepSubs, ch: chSubs })); } catch {}
  }, [ready, apiMode, stepSubs, chSubs]);

  // API mode: re-pull server notifications (after a submit, or on a poll tick). The
  // unseen badge = number of still-unread rows; opening the panel marks them read.
  function refreshNotifs() {
    if (!apiMode) return;
    getNotifications()
      .then((ns) => { setNotifs(ns.map(mapNotif)); setSeen(ns.filter((x) => x.read).length); })
      .catch(() => {});
  }
  useEffect(() => {
    if (!ready || !apiMode || authState !== "ready") return;
    const id = setInterval(refreshNotifs, 30000);   // pick up grading_done / round_published
    return () => clearInterval(id);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [ready, apiMode, authState]);

  function markNotifsSeen() {
    setSeen(notifs.length);
    if (apiMode) markNotificationsRead().catch(() => {});   // persist read server-side
  }

  function setNick(n: string) {
    if (apiMode) {
      apiSetNickname(n)
        .then(() => { setNickState(n); setAuthState("ready"); })
        .catch((e) => showToast("닉네임을 설정하지 못했습니다", String(e?.message ?? e)));
      return;
    }
    try { localStorage.setItem(NICK_KEY, n); } catch {} setNickState(n);
  }

  function addContest(c: Contest) {
    setContests((cs) => [c, ...cs]);
    // persist created contests; warn loudly if the browser quota is exceeded (large
    // base64 images) instead of silently dropping the just-created contest on refresh.
    setContests((cs) => {
      const defaults = new Set(CONTESTS.map((x) => x.id));
      try {
        localStorage.setItem(CONTESTS_KEY, JSON.stringify(cs.filter((x) => !defaults.has(x.id))));
      } catch {
        showToast("대회가 영구 저장되지 못했습니다", "브라우저 저장 용량 초과 — 지문 이미지를 줄이거나 빼고 다시 만드세요");
      }
      return cs;
    });
  }

  function showToast(msg: string, sub?: string) {
    if (!msg && !sub) return;
    setToast({ msg: msg || sub!, sub: msg ? sub : undefined });
    if (tRef.current) clearTimeout(tRef.current);
    tRef.current = setTimeout(() => setToast(null), 6000);
  }

  function push(icon: string, msg: string, sub: string, toastSub?: string) {
    if (icon) setNotifs((n) => [{ icon, msg, sub }, ...n]);
    showToast(toastSub !== undefined ? toastSub : msg, toastSub !== undefined ? undefined : sub);
  }

  const stepSubsFor = (cid: string) => stepSubs[cid] ?? DEMO_STEP[cid] ?? [];
  const chSubsFor = (cid: string) => chSubs[cid] ?? DEMO_CH[cid] ?? [];

  function addStepSub(cid: string, sub: StepSub) {
    setStepSubs((m) => ({ ...m, [cid]: [sub, ...(m[cid] ?? DEMO_STEP[cid] ?? [])] }));
  }
  function addChSub(cid: string, sub: ChSub) {
    setChSubs((m) => ({ ...m, [cid]: [sub, ...(m[cid] ?? DEMO_CH[cid] ?? [])] }));
  }
  function setRepCh(cid: string, id: number) {
    setChSubs((m) => {
      const cur = m[cid] ?? DEMO_CH[cid] ?? [];
      return { ...m, [cid]: cur.map((x) => ({ ...x, cur: x.id === id })) };
    });
  }

  const value: Store = {
    ready, apiMode, authState, isAdmin, isTester, loginUrl,
    nick, setNick, contests, addContest, notifs,
    unseen: Math.max(0, notifs.length - seen),
    markNotifsSeen, refreshNotifs,
    push, stepSubsFor, chSubsFor, addStepSub, addChSub, setRepCh,
    toast, showToast, closeToast: () => setToast(null), notifOpen, setNotifOpen,
  };
  return <Ctx.Provider value={value}>{children}</Ctx.Provider>;
}
