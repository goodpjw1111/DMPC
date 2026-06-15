"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import { marked } from "marked";
import katex from "katex";
import DOMPurify from "dompurify";
import { useStore } from "@/lib/store";
import { LANGS, LANG_BY_ID, downloadText, fmtBytes, SRC_LIMIT, DATA_LIMIT } from "@/lib/langs";
import {
  CH_EVALS, RANKING, STATEMENT_HTML, challengeEval, chSampleRows, chSubCost,
  validateNick, pct, fmt, missionsFor, statementFor, isCustomGen, weightedTotal, stepupScoreFromHist,
  type Contest, type StepSub, type ChSub, type Mission, type ProblemDef,
} from "@/lib/mock";
import {
  parseInst, parseMoves, boardState, referenceCost, gradeStep, genClean, DEFAULT_GEN,
  KEY_MOVES, type Inst, type GenParams,
} from "@/lib/sim";
import { registerSimulator, getSimulator } from "@/lib/simulators";
import { parseMaze, replayMaze, MAZE_KEYS, type MazeInst, type MazeState } from "@/lib/maze";
import {
  getContestDetail, getProblem, getStandings, getMyEval, getMissionInput,
  submitStepup, getStepupSubmissions, submitChallenge, getChallengeSubmissions, createContest,
  getReplays, getMyReplay, postReplay, replayPdfUrl, moderateReplay,
  getRegistration, registerContest, unregisterContest, getTemplates, evaluateNow, endContest, publishContest, deleteContest,
  type ApiContestDetail, type ApiProblem, type StandingRow, type MyEval, type ProblemTemplate,
  type StepupSubmission, type ChallengeSubmission, type Replay, type MyReplay, type Registration,
} from "@/lib/api";

// ===== small bits =====
export function Bar({ g, t, sm }: { g: number; t: number; sm?: boolean }) {
  return <div className={"bar" + (sm ? " sm" : "")}>
    <div className="fill" style={{ width: pct(g, t) + "%" }} />
    <div className="lbl">{sm ? fmt(g) : `${fmt(g)} / ${fmt(t)}`}</div>
  </div>;
}
// Render LaTeX BEFORE markdown: marked otherwise mangles `$...$` content (e.g. an
// underscore `x_1` becomes <em>, breaking KaTeX). We stash each math span as a
// placeholder marked won't touch, run marked, then swap in the KaTeX HTML.
function renderMarkdownWithMath(md: string): string {
  const math: string[] = [];
  const stash = (tex: string, display: boolean) => {
    try { math.push(katex.renderToString(tex.trim(), { displayMode: display, throwOnError: false })); }
    catch { math.push(display ? `$$${tex}$$` : `$${tex}$`); }
    return `@@KMATH${math.length - 1}@@`;
  };
  const protectedMd = md
    .replace(/\$\$([\s\S]+?)\$\$/g, (_m, tex) => stash(tex, true))     // display math first
    .replace(/\$([^$\n]+?)\$/g, (_m, tex) => stash(tex, false));       // then inline
  const html = marked.parse(protectedMd, { breaks: true, gfm: true }) as string;
  // Sanitize the markdown-derived HTML (authored statement_md could contain hostile HTML)
  // BEFORE swapping in the KaTeX spans — KaTeX output is generated locally and trusted, so
  // it is injected after sanitizing. On the server (no window) md is empty (client-fetched),
  // so skipping there is safe; the live render path always sanitizes.
  const safe = typeof window === "undefined" ? html : DOMPurify.sanitize(html);
  return safe.replace(/@@KMATH(\d+)@@/g, (_m, i) => math[+i] ?? "");
}
function MarkdownView({ md }: { md: string }) {
  const html = useMemo(() => renderMarkdownWithMath(md), [md]);
  return <div className="statement" dangerouslySetInnerHTML={{ __html: html }} />;
}
function CustomGenNotice() {
  return <div className="card"><b>커스텀 생성기 문제</b><p className="muted" style={{ margin: "8px 0 0" }}>작성된 생성기/체커 코드로 <b>서버에서 채점</b>됩니다. 브라우저 미리보기·제출은 실 API 연동 후 지원됩니다.</p></div>;
}
function NotLiveNotice({ status }: { status: string }) {
  const msg = status === "ended" ? "이미 종료된 모의고사입니다 — 제출할 수 없습니다."
    : status === "soon" ? "아직 시작 전 모의고사입니다 — 시작되면 제출할 수 있어요."
    : "지금은 제출할 수 없습니다.";
  return <div className="card" style={{ marginBottom: 12, borderColor: "var(--line2)" }}>
    <span className="muted">⛔ {msg} <span style={{ opacity: .85 }}>(시뮬레이터로 연습은 가능합니다.)</span></span>
  </div>;
}
function HelpModal({ onClose }: { onClose: () => void }) {
  return <div className="overlay" onClick={onClose}>
    <div className="modal-card" onClick={(e) => e.stopPropagation()}>
      <div className="modal-head"><b>대회 규칙 · 제출 형식 안내</b><span className="x" onClick={onClose}>✕</span></div>
      <div className="modal-body">
        <h4>두 가지 문제</h4>
        <ul>
          <li><b>스텝 업</b> (만점 1,000,000 · 총점 반영 20%) — 미션마다 <b>입력이 미리 주어지고</b>, 시뮬레이터나
            오프라인 계산으로 만든 <b>출력(결과)만</b> 제출합니다(코드 아님). 기준 비용 이하면 만점,
            아니면 부분점수. 미션마다 따로 제출·채점됩니다.</li>
          <li><b>챌린지</b> (만점 1,000,000 · 총점 반영 80%) — <b>코드를 제출</b>하면 비공개 데이터로 실행되어
            <b>참가자들과의 상대 등수</b>로 채점됩니다. 매일 09:00·18:00(KST)에 새 데이터로 평가합니다.</li>
        </ul>
        <h4>챌린지 제출 형식</h4>
        <ul>
          <li><b>소스 코드</b>: 최대 <b>1&nbsp;MB</b>. 12개 언어 지원, 시간·메모리 제한은
            <b>언어 공통 2초 / 1024MB</b>.</li>
          <li><b>입력</b>은 표준입력(stdin), <b>출력</b>은 표준출력(stdout)으로 주고받습니다.</li>
          <li><b>data.bin</b> (선택): 미리 계산한 데이터를 최대 <b>10&nbsp;MB</b>까지 함께 업로드할 수
            있습니다. 실행 폴더의 <code>data.bin</code> 파일을 <b>파일 입출력</b>으로 읽어 쓰세요.
            (각 언어 예시 코드에 읽는 방법이 주석으로 들어 있습니다.)</li>
          <li>언어별 <b>예시 코드(입출력 스켈레톤)</b>는 챌린지 지문 하단에서 내려받을 수 있습니다.</li>
        </ul>
        <h4>채점·평가</h4>
        <ul>
          <li>제출 직후에는 <b>샘플 비용</b>만 확인됩니다. 챌린지의 <b>점수(상대 등수 환산)는 매일 09·18시 중간 평가 때 산정</b>되며,
            그 전까지는 <b>0점</b>입니다.</li>
          <li>케이스별 점수 = ⌊10⁶·(1 − 0.5·√((n<sub>lose</sub> + 0.5·n<sub>draw</sub>)/n))⌋ — 나보다 잘한(n<sub>lose</sub>)·비긴(n<sub>draw</sub>) 참가자가 적을수록 높습니다.</li>
          <li>상대 등수와 다른 참가자는 <b>대회가 끝난 뒤</b> 공개됩니다. 진행 중에는 본인 결과만 봅니다.</li>
        </ul>
      </div>
      <div className="modal-foot"><button className="btn" onClick={onClose}>확인</button></div>
    </div>
  </div>;
}
function RuleBanner() {
  const [open, setOpen] = useState(false);
  return <>
    <div className="rule" onClick={() => setOpen(true)}>📖 대회 규칙 · 제출 형식 · 개발 도구 안내</div>
    {open && <HelpModal onClose={() => setOpen(false)} />}
  </>;
}
function ChallengeStatement({ md }: { md: string }) {
  return <>
    <MarkdownView md={md} />
    <div className="card" style={{ marginTop: 18 }}>
      <h3 style={{ margin: "0 0 6px" }}>채점 방식</h3>
      <p className="muted" style={{ margin: 0, fontSize: 13, lineHeight: 1.75 }}>
        같은 비공개 데이터에 대해 <b style={{ color: "var(--fg)" }}>다른 참가자들과의 상대 등수</b>로 채점됩니다 (비용이 낮을수록 유리).
        케이스별 점수 = ⌊10⁶·(1 − 0.5·√((<i>n<sub>lose</sub></i> + 0.5·<i>n<sub>draw</sub></i>)/<i>n</i>))⌋ 이며,
        <i> n</i>은 참가자 수, <i>n<sub>lose</sub></i>는 나보다 비용이 낮은(이긴) 참가자 수,
        <i> n<sub>draw</sub></i>는 나와 비용이 같은(비긴) 참가자 수입니다. 케이스 평균이 곧 챌린지 점수(<b style={{ color: "var(--fg)" }}>만점 1,000,000</b>)입니다.
        매일 09·18시(KST)에 새 데이터로 평가하며, <b style={{ color: "var(--fg)" }}>점수는 중간 평가 때 산정 — 그 전까지 0점</b>입니다. (총점에는 <b style={{ color: "var(--fg)" }}>80%</b> 반영)
      </p>
    </div>
    <div className="card" style={{ marginTop: 14 }}>
      <h3 style={{ margin: "0 0 4px" }}>언어별 예시 코드 (입출력 스켈레톤)</h3>
      <p className="muted" style={{ margin: "0 0 12px", fontSize: 12, lineHeight: 1.6 }}>
        12개 언어 공통 · 2초 / 1024MB. 입력=표준입력, 출력=표준출력. 각 예시에는
        업로드한 <code>data.bin</code>(선택, ≤10MB)을 <b>파일 입출력</b>으로 읽는 방법이 주석으로 들어 있어요.
      </p>
      <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fill, minmax(150px, 1fr))", gap: 8 }}>
        {LANGS.map((l) => <button key={l.id} className="btn ghost" style={{ padding: "8px 10px", textAlign: "left", opacity: l.enabled ? 1 : .55 }} onClick={() => downloadText(l.filename, l.starter)}>⬇ {l.label}{l.enabled ? "" : <span className="pill" style={{ marginLeft: 6, fontSize: 10 }}>준비 중</span>}<span className="muted" style={{ fontSize: 11, display: "block" }}>{l.filename}</span></button>)}
      </div>
    </div>
  </>;
}

// Time/memory limits banner (REQ4) — reads the problem's actual limits from the API.
function LimitsBar({ p }: { p: ApiProblem | null }) {
  if (!p) return null;
  const sec = p.time_limit_ms / 1000;
  const secStr = Number.isInteger(sec) ? `${sec}초` : `${sec.toFixed(1)}초`;
  return <div className="card" style={{ marginBottom: 12, display: "flex", gap: 18, alignItems: "center", padding: "9px 14px", flexWrap: "wrap" }}>
    <span className="muted" style={{ fontSize: 12 }}>⏱ 시간 제한 <b style={{ color: "var(--fg)" }}>{secStr}</b></span>
    <span className="muted" style={{ fontSize: 12 }}>💾 메모리 제한 <b style={{ color: "var(--fg)" }}>{p.memory_limit_mb}MB</b></span>
    <span className="muted" style={{ fontSize: 11, opacity: .8 }}>(12개 언어 공통)</span>
  </div>;
}

// Example I/O (REQ3) — auto-generated from a representative seed; input always present,
// output shown only when the problem provides a reference solution. Both downloadable.
function ExampleIO({ p }: { p: ApiProblem | null }) {
  if (!p || !p.example_input) return null;
  const trunc = (s: string, n = 4000) => (s.length > n ? s.slice(0, n) + "\n…(생략 — 다운로드로 전체 확인)" : s);
  const pre = { background: "#0f1117", border: "1px solid var(--line)", borderRadius: 8, padding: 10, fontSize: 12, overflow: "auto", maxHeight: 240, margin: 0, whiteSpace: "pre" } as const;
  return <div className="card" style={{ marginTop: 14 }}>
    <h3 style={{ margin: "0 0 8px" }}>예제 입출력</h3>
    <div className="grid2">
      <div>
        <div className="row" style={{ justifyContent: "space-between", marginBottom: 4 }}>
          <span className="k muted" style={{ fontSize: 12 }}>입력 (예제)</span>
          <button className="btn ghost" style={{ padding: "3px 10px", fontSize: 12 }} onClick={() => downloadText("example_input.txt", p.example_input || "")}>⬇ 입력 다운로드</button>
        </div>
        <pre style={pre}>{trunc(p.example_input)}</pre>
      </div>
      <div>
        <div className="row" style={{ justifyContent: "space-between", marginBottom: 4 }}>
          <span className="k muted" style={{ fontSize: 12 }}>출력 {p.example_output ? <span className="muted">(참조 풀이 — 만점 동급)</span> : ""}</span>
          {p.example_output ? <button className="btn ghost" style={{ padding: "3px 10px", fontSize: 12 }} onClick={() => downloadText("example_output.txt", p.example_output || "")}>⬇ 출력 다운로드</button> : null}
        </div>
        {p.example_output
          ? <pre style={pre}>{trunc(p.example_output)}</pre>
          : <div className="muted" style={{ fontSize: 12, padding: 10, lineHeight: 1.6 }}>이 문제는 정답 출력이 하나로 정해지지 않습니다 — 위 입력에 대한 <b>유효한 결과</b>를 제출하면 됩니다.</div>}
      </div>
    </div>
  </div>;
}
function MissionSelect({ mission, setMission, missions }: { mission: number; setMission: (i: number) => void; missions: Mission[] }) {
  return <select className="dd" value={mission} onChange={(e) => setMission(+e.target.value)}>
    {missions.map((m, i) => <option key={i} value={i}>미션 {m.mission}</option>)}
  </select>;
}
export function NotFound() {
  return <div className="wrap"><h1>찾을 수 없음</h1><p className="muted">존재하지 않는 대회/문제입니다.</p>
    <Link href="/" className="btn ghost">← 모의고사 목록</Link></div>;
}

// ===== board =====
function Board({ g, pos, visited }: { g: Inst; pos: [number, number]; visited: Set<string> }) {
  // clamp the cell so big grids stay visible (the SVG scales via viewBox); without
  // a floor, large H/W made CELL-13 negative and the robot/cells vanished.
  const CELL = Math.max(16, Math.floor(360 / Math.max(g.h, g.w))), PAD = 6;
  const GAP = Math.min(3, CELL - 6), INSET = Math.max(3, Math.round(CELL * 0.28));
  const W = g.w * CELL + PAD * 2, H = g.h * CELL + PAD * 2;
  const els: JSX.Element[] = [];
  for (let r = 0; r < g.h; r++) for (let c = 0; c < g.w; c++) {
    const x = PAD + c * CELL, y = PAD + r * CELL, key = r + "," + c;
    els.push(<rect key={"c" + key} x={x} y={y} width={CELL - GAP} height={CELL - GAP} rx={6} fill="#161a23" stroke="#222838" />);
    if (g.dirty.has(key)) els.push(<circle key={"d" + key} cx={x + (CELL - GAP) / 2} cy={y + (CELL - GAP) / 2} r={Math.max(3, CELL * 0.16)} fill={visited.has(key) ? "var(--green)" : "var(--dirty)"} />);
  }
  const rx = PAD + pos[1] * CELL, ry = PAD + pos[0] * CELL;
  els.push(<rect key="robot" x={rx + INSET} y={ry + INSET} width={CELL - GAP - INSET * 2 + 3} height={CELL - GAP - INSET * 2 + 3} rx={5} fill="var(--robot)" />);
  return <svg viewBox={`0 0 ${W} ${H}`} className="sim-grid" style={{ width: "100%", maxHeight: "52vh" }} preserveAspectRatio="xMidYMid meet">{els}</svg>;
}

// ===== Step Up simulator =====
function StepSimulator({ mission, setMission, onOutput, missions, initial }: { mission: number; setMission: (i: number) => void; onOutput: (s: string) => void; missions: Mission[]; initial: string }) {
  const seed = missions[mission] ?? missions[0];
  const g = useMemo(() => parseInst(seed.input), [seed.input]);
  const ref = useMemo(() => referenceCost(g), [g]);
  // seed moves from the current submit output so opening the sim tab doesn't blank it.
  const [moves, setMoves] = useState<string[]>(() => parseMoves(initial));
  const [redo, setRedo] = useState<string[]>([]);
  // reset only when the mission VALUE actually changes (compare a ref, so StrictMode's
  // double-invoked effects can't wipe the seeded moves on mount).
  const lastMission = useRef(mission);
  useEffect(() => {
    if (lastMission.current === mission) return;
    lastMission.current = mission;
    setMoves([]); setRedo([]);
  }, [mission]);
  useEffect(() => { onOutput(moves.join("")); }, [moves, onOutput]);
  useEffect(() => {
    function onKey(e: KeyboardEvent) {
      const ae = document.activeElement as HTMLElement | null;
      if (ae && ["TEXTAREA", "INPUT", "SELECT"].includes(ae.tagName)) return;
      const k = e.key.toLowerCase();
      if (k === "z") { e.preventDefault(); setMoves((m) => { if (!m.length) return m; setRedo((rd) => [...rd, m[m.length - 1]]); return m.slice(0, -1); }); return; }
      if (k === "x") { e.preventDefault(); setRedo((rd) => { if (!rd.length) return rd; setMoves((m) => [...m, rd[rd.length - 1]]); return rd.slice(0, -1); }); return; }
      if (k === "r") { e.preventDefault(); setMoves([]); setRedo([]); return; }
      const mv = KEY_MOVES[e.key]; if (!mv) return;
      e.preventDefault();
      setMoves((m) => { const cur = boardState(g, m, m.length).pos; const nr = cur[0] + mv[0], nc = cur[1] + mv[1]; if (nr < 0 || nr >= g.h || nc < 0 || nc >= g.w) return m; return [...m, mv[2]]; });
      setRedo([]);
    }
    window.addEventListener("keydown", onKey); return () => window.removeEventListener("keydown", onKey);
  }, [g]);
  const st = boardState(g, moves, moves.length);
  const rem = [...g.dirty].filter((k) => !st.visited.has(k)).length;
  const cost = moves.length, ratio = Math.min(ref / Math.max(cost, 1), 1);
  const undo = () => setMoves((m) => { if (!m.length) return m; setRedo((rd) => [...rd, m[m.length - 1]]); return m.slice(0, -1); });
  const redoMv = () => setRedo((rd) => { if (!rd.length) return rd; setMoves((m) => [...m, rd[rd.length - 1]]); return rd.slice(0, -1); });
  const reset = () => { setMoves([]); setRedo([]); };
  return <>
    <div className="row" style={{ justifyContent: "space-between", marginBottom: 4 }}>
      <h2 style={{ margin: 0 }}>시뮬레이터</h2><MissionSelect mission={mission} setMission={setMission} missions={missions} />
    </div>
    <p className="muted" style={{ margin: "0 0 10px", fontSize: 12 }}>방향키로 청소하면 아래 <b>출력</b>이 만들어지고, 그대로 제출됩니다. <span style={{ opacity: .8 }}>미션을 바꾸면 진행이 초기화돼요.</span></p>
    <div className="grid2">
      <div><Board g={g} pos={st.pos} visited={st.visited} /></div>
      <div>
        <div className="sim-hud"><div><div className="k">남은 먼지</div><div className="v">{rem}</div></div><div><div className="k">이동 수</div><div className="v">{moves.length}</div></div></div>
        <div className="row" style={{ gap: 6, marginTop: 8, flexWrap: "wrap" }}>
          <button className="btn ghost" style={{ padding: "5px 10px", fontSize: 12 }} onClick={undo} disabled={!moves.length}>↩ 되돌리기</button>
          <button className="btn ghost" style={{ padding: "5px 10px", fontSize: 12 }} onClick={redoMv} disabled={!redo.length}>↪ 다시실행</button>
          <button className="btn ghost" style={{ padding: "5px 10px", fontSize: 12 }} onClick={reset} disabled={!moves.length}>⟲ 리셋</button>
        </div>
        <div className="keys">입력란 밖에서 <kbd>↑</kbd><kbd>↓</kbd><kbd>←</kbd><kbd>→</kbd> 이동 · <kbd>z</kbd> 되돌리기 · <kbd>x</kbd> 다시실행 · <kbd>r</kbd> 리셋</div>
        <div className="io"><div><div className="k muted" style={{ fontSize: 11, marginBottom: 4 }}>입력 (주어짐)</div><textarea rows={6} readOnly value={seed.input.trimEnd()} /></div></div>
        <div className="io"><div><div className="k muted" style={{ fontSize: 11, marginBottom: 4 }}>출력 (이동 기록 — 그대로 제출됩니다)</div><textarea rows={3} readOnly value={moves.join("")} /><div className="muted" style={{ fontSize: 11, marginTop: 4 }}>↳ 오른쪽 <b>제출</b> 칸에 자동 반영됩니다.</div></div></div>
        <div className="muted" style={{ marginTop: 8 }}>{rem === 0
          ? <span className={cost <= ref ? "ok" : ""}>{cost <= ref ? "✓ 만점" : "부분점수"} · 비용 {cost} / 기준 {ref} · 점수 {fmt(Math.floor(seed.budget * ratio))}</span>
          : `먼지 ${rem}개 남음`}</div>
      </div>
    </div>
  </>;
}

// ===== Challenge simulator =====
function ChallengeSimulator() {
  const [input, setInput] = useState("");
  const [output, setOutput] = useState("");
  const [frame, setFrame] = useState(0);
  let g: Inst | null = null;
  try { g = input.trim() ? parseInst(input) : null; } catch { g = null; }
  const moves = parseMoves(output);
  useEffect(() => { setFrame(parseMoves(output).length); }, [output, input]);
  useEffect(() => {
    function onKey(e: KeyboardEvent) {
      const ae = document.activeElement as HTMLElement | null;
      if (ae && ["TEXTAREA", "INPUT", "SELECT"].includes(ae.tagName)) return;
      if (e.key.toLowerCase() === "z") { e.preventDefault(); setOutput((o) => o.slice(0, -1)); return; }
      const mv = KEY_MOVES[e.key]; if (!mv) return; e.preventDefault(); setOutput((o) => o + mv[2]);
    }
    window.addEventListener("keydown", onKey); return () => window.removeEventListener("keydown", onKey);
  }, []);
  const fr = Math.min(frame, moves.length);
  const st = g ? boardState(g, moves, fr) : null;
  const rem = g && st ? [...g.dirty].filter((k) => !st.visited.has(k)).length : 0;
  return <>
    <h2 style={{ margin: "0 0 8px" }}>시뮬레이터 <span className="muted" style={{ fontSize: 12, fontWeight: 400 }}>— 입력/출력을 넣으면 단계별로 시각화</span></h2>
    <div className="grid2">
      <div>
        {g && st ? <Board g={g} pos={st.pos} visited={st.visited} />
          : <div className="sim-grid" style={{ height: 240, display: "flex", alignItems: "center", justifyContent: "center", color: "#5b6273" }}>입력을 붙여넣으면 보드가 표시됩니다</div>}
        <div style={{ marginTop: 8 }}>
          <input type="range" min={0} max={moves.length} value={fr} disabled={!moves.length} onChange={(e) => setFrame(+e.target.value)} style={{ width: "100%" }} />
          <div className="row" style={{ justifyContent: "space-between" }}><span className="muted" style={{ fontSize: 12 }}>단계 {fr} / {moves.length}</span><span className="bad" style={{ fontSize: 12 }}>{st?.offGrid ? "⚠ 격자 밖으로 나가는 이동 — 이 출력은 무효" : ""}</span></div>
        </div>
      </div>
      <div>
        <div className="sim-hud"><div><div className="k">남은 먼지</div><div className="v">{g ? rem : "-"}</div></div><div><div className="k">단계</div><div className="v">{fr}</div></div></div>
        <div className="io"><div><div className="k muted" style={{ fontSize: 11, marginBottom: 4 }}>입력 (붙여넣기)</div><textarea rows={6} placeholder="입력 데이터를 붙여넣으면 보드가 표시됩니다" value={input} onChange={(e) => setInput(e.target.value)} /></div></div>
        <div className="io"><div>
          <div className="row" style={{ justifyContent: "space-between", marginBottom: 4 }}><div className="k muted" style={{ fontSize: 11 }}>출력 (붙여넣기 또는 방향키)</div><button className="btn ghost" style={{ padding: "2px 8px", fontSize: 11 }} onClick={() => setOutput("")} disabled={!output}>지우기</button></div>
          <textarea rows={3} placeholder="출력(이동 문자열)을 붙여넣거나, 입력란 밖을 클릭한 뒤 방향키로 조작" value={output} onChange={(e) => setOutput(e.target.value)} /></div></div>
        <div className="keys">입력란 밖에서 <kbd>↑↓←→</kbd> 이동(출력에 추가) · <kbd>z</kbd> 한 칸 지움 · 슬라이더로 단계 이동</div>
      </div>
    </div>
  </>;
}

// Register the clean-robot family simulator (grid + dust + robot). A problem whose
// META.simulator_key === "clean" renders these. A NEW problem calls registerSimulator
// with its own key + components; the dispatch site (ApiProblemView) needs no edits.
registerSimulator("clean", { Step: StepSimulator, Challenge: ChallengeSimulator });

// ===== maze_push simulator (다오와 배찌의 길찾기) =====
// Renders the grid from the parsed instance + the live state from replayMaze (a JS
// mirror of the server checker), so cost/reached shown here match how it's scored.
function MazeBoard({ inst, st }: { inst: MazeInst; st: MazeState }) {
  const CELL = Math.max(16, Math.floor(360 / Math.max(inst.R, inst.C))), PAD = 6;
  const GAP = Math.min(3, CELL - 6);
  const W = inst.C * CELL + PAD * 2, H = inst.R * CELL + PAD * 2;
  const at = (r: number, c: number) => ({ x: PAD + c * CELL, y: PAD + r * CELL, s: CELL - GAP });
  const els: JSX.Element[] = [];
  for (let r = 0; r < inst.R; r++) for (let c = 0; c < inst.C; c++) {
    const k = r + "," + c, a = at(r, c), wall = inst.walls.has(k);
    els.push(<rect key={"c" + k} x={a.x} y={a.y} width={a.s} height={a.s} rx={4}
      fill={wall ? "#3a4256" : "#161a23"} stroke="#222838" />);
  }
  const gg = at(inst.goal[0], inst.goal[1]);
  els.push(<rect key="goal" x={gg.x + 3} y={gg.y + 3} width={gg.s - 6} height={gg.s - 6} rx={4} fill="none" stroke="var(--green)" strokeWidth={2} />);
  els.push(<circle key="goaldot" cx={gg.x + gg.s / 2} cy={gg.y + gg.s / 2} r={Math.max(2, CELL * 0.1)} fill="var(--green)" />);
  [...st.blocks].forEach((bk) => { const [r, c] = bk.split(",").map(Number); const a = at(r, c); els.push(<rect key={"b" + bk} x={a.x + 2} y={a.y + 2} width={a.s - 4} height={a.s - 4} rx={3} fill="#8a5a2b" stroke="#6b4420" />); });
  if (st.bazzi) { const b = at(st.bazzi[0], st.bazzi[1]); els.push(<circle key="bazzi" cx={b.x + b.s / 2} cy={b.y + b.s / 2} r={Math.max(4, CELL * 0.28)} fill="#c9a0ff" />); }
  const d = at(st.dao[0], st.dao[1]), ins = Math.max(3, Math.round(CELL * 0.24));
  els.push(<rect key="dao" x={d.x + ins} y={d.y + ins} width={d.s - ins * 2} height={d.s - ins * 2} rx={4} fill="var(--robot)" />);
  return <svg viewBox={`0 0 ${W} ${H}`} className="sim-grid" style={{ width: "100%", maxHeight: "52vh" }} preserveAspectRatio="xMidYMid meet">{els}</svg>;
}

function MazeStepSim({ mission, setMission, onOutput, missions, initial }: { mission: number; setMission: (i: number) => void; onOutput: (s: string) => void; missions: Mission[]; initial: string }) {
  const seed = missions[mission] ?? missions[0];
  const inst = useMemo(() => parseMaze(seed.input), [seed.input]);
  const [moves, setMoves] = useState(initial || "");
  const lastMission = useRef(mission);
  useEffect(() => { if (lastMission.current === mission) return; lastMission.current = mission; setMoves(""); }, [mission]);
  useEffect(() => { onOutput(moves); }, [moves, onOutput]);
  const st = useMemo(() => replayMaze(inst, moves), [inst, moves]);
  useEffect(() => {
    function onKey(e: KeyboardEvent) {
      const ae = document.activeElement as HTMLElement | null;
      if (ae && ["TEXTAREA", "INPUT", "SELECT"].includes(ae.tagName)) return;
      const k = e.key.toLowerCase();
      if (k === "z" || e.key === "Backspace") { e.preventDefault(); setMoves((m) => m.slice(0, -1)); return; }
      if (k === "r") { e.preventDefault(); setMoves(""); return; }
      const mv = MAZE_KEYS[e.key]; if (!mv) return; e.preventDefault();
      setMoves((m) => (st.reached ? m : m + mv));
    }
    window.addEventListener("keydown", onKey); return () => window.removeEventListener("keydown", onKey);
  }, [st.reached]);
  return <>
    <div className="row" style={{ justifyContent: "space-between", marginBottom: 4 }}>
      <h2 style={{ margin: 0 }}>시뮬레이터</h2><MissionSelect mission={mission} setMission={setMission} missions={missions} />
    </div>
    <p className="muted" style={{ margin: "0 0 10px", fontSize: 12 }}>방향키로 {inst.P === 2 ? "다오/배찌를 번갈아 " : ""}움직여 목표(◎)에 도달하세요. 갈색 블럭은 밀 수 있고, 한 번에 여러 칸을 밀면 비용이 늘어요. <kbd>z</kbd> 한 칸 취소 · <kbd>r</kbd> 리셋</p>
    <div className="grid2">
      <div><MazeBoard inst={inst} st={st} /></div>
      <div>
        <div className="sim-hud"><div><div className="k">비용</div><div className="v">{st.cost}</div></div><div><div className="k">{st.reached ? "상태" : "다음 차례"}</div><div className="v">{st.reached ? "도달!" : (inst.P === 2 ? (st.daoNext ? "다오" : "배찌") : "다오")}</div></div></div>
        <div className="io" style={{ marginTop: 8 }}><div><div className="k muted" style={{ fontSize: 11, marginBottom: 4 }}>입력 (주어짐)</div><textarea rows={5} readOnly value={seed.input.trimEnd()} /></div></div>
        <div className="io"><div><div className="k muted" style={{ fontSize: 11, marginBottom: 4 }}>출력 (이동열 — 그대로 제출됩니다)</div><textarea rows={3} readOnly value={moves} /><div className="muted" style={{ fontSize: 11, marginTop: 4 }}>↳ 오른쪽 <b>제출</b> 칸에 자동 반영됩니다.</div></div></div>
        <div className="muted" style={{ marginTop: 8 }}>{st.reached ? <span className="ok">✓ 목표 도달 · 비용 {st.cost}</span> : `진행 중 · 비용 ${st.cost}`}</div>
        {inst.P === 2 && <p className="muted" style={{ fontSize: 11, marginTop: 6 }}>C=2: 출력의 짝수 번째=다오, 홀수 번째=배찌(자동 교대). 배찌(보라)는 블럭을 밀어 다오를 도와줍니다.</p>}
      </div>
    </div>
  </>;
}

function MazeChallengeSim() {
  const [input, setInput] = useState("");
  const [moves, setMoves] = useState("");
  let inst: MazeInst | null = null;
  try { inst = input.trim() ? parseMaze(input) : null; } catch { inst = null; }
  const st = inst ? replayMaze(inst, moves) : null;
  useEffect(() => {
    function onKey(e: KeyboardEvent) {
      const ae = document.activeElement as HTMLElement | null;
      if (ae && ["TEXTAREA", "INPUT", "SELECT"].includes(ae.tagName)) return;
      const k = e.key.toLowerCase();
      if (k === "z" || e.key === "Backspace") { e.preventDefault(); setMoves((m) => m.slice(0, -1)); return; }
      const mv = MAZE_KEYS[e.key]; if (!mv) return; e.preventDefault(); setMoves((m) => m + mv);
    }
    window.addEventListener("keydown", onKey); return () => window.removeEventListener("keydown", onKey);
  }, []);
  return <>
    <h2 style={{ margin: "0 0 8px" }}>시뮬레이터 <span className="muted" style={{ fontSize: 12, fontWeight: 400 }}>— 입력을 붙여넣고 방향키로 플레이</span></h2>
    <div className="grid2">
      <div>{inst && st ? <MazeBoard inst={inst} st={st} />
        : <div className="sim-grid" style={{ height: 240, display: "flex", alignItems: "center", justifyContent: "center", color: "#5b6273" }}>입력을 붙여넣으면 보드가 표시됩니다</div>}</div>
      <div>
        <div className="sim-hud"><div><div className="k">비용</div><div className="v">{st ? st.cost : "-"}</div></div><div><div className="k">상태</div><div className="v">{st ? (st.reached ? "도달!" : "진행") : "-"}</div></div></div>
        <div className="io" style={{ marginTop: 8 }}><div><div className="k muted" style={{ fontSize: 11, marginBottom: 4 }}>입력 (붙여넣기)</div><textarea rows={6} placeholder="입력 데이터를 붙여넣으면 보드가 표시됩니다" value={input} onChange={(e) => { setInput(e.target.value); setMoves(""); }} /></div></div>
        <div className="io"><div><div className="row" style={{ justifyContent: "space-between", marginBottom: 4 }}><div className="k muted" style={{ fontSize: 11 }}>출력 (방향키로 생성)</div><button className="btn ghost" style={{ padding: "2px 8px", fontSize: 11 }} onClick={() => setMoves("")} disabled={!moves}>지우기</button></div><textarea rows={2} readOnly value={moves} /></div></div>
        <div className="keys">입력란 밖에서 <kbd>↑↓←→</kbd> 이동 · <kbd>z</kbd> 한 칸 취소{inst && inst.P === 2 ? " · C=2 자동 교대(다오/배찌)" : ""}</div>
      </div>
    </div>
  </>;
}

registerSimulator("maze", { Step: MazeStepSim, Challenge: MazeChallengeSim });

// ===== problem view =====
type RightTab = "submit" | "history" | "eval";
// Dispatcher: real-API mode fetches from the backend; mock mode is the localStorage demo.
export function ProblemView(props: { contest: Contest; kind: "stepup" | "challenge" }) {
  const { apiMode } = useStore();
  return apiMode ? <ApiProblemView {...props} /> : <MockProblemView {...props} />;
}

function MockProblemView({ contest, kind }: { contest: Contest; kind: "stepup" | "challenge" }) {
  const { push, stepSubsFor, chSubsFor, addStepSub, addChSub, setRepCh } = useStore();
  const isStep = kind === "stepup";
  const live = contest.status === "live";   // only live contests accept submissions
  const missions = useMemo(() => missionsFor(contest), [contest]);
  const statement = statementFor(contest, kind);
  const simDisabled = isStep && missions.length === 0;   // custom-generator stepup: no browser sim
  const [leftTab, setLeftTab] = useState<"statement" | "sim">("statement");
  const [rightTab, setRightTab] = useState<RightTab>("submit");
  const [mission, setMission] = useState(0);
  const [stepOutput, setStepOutput] = useState("");
  // switching the submit target mission clears the output so a previous mission's
  // answer can't be graded against (and stored under) a different mission.
  useEffect(() => { setStepOutput(""); }, [mission]);
  // submission history lives in the store (persisted) so it survives navigation/refresh.
  const stepHist = stepSubsFor(contest.id);
  const chHist = chSubsFor(contest.id);
  const [subSel, setSubSel] = useState<number | null>(null);
  const [evalSel, setEvalSel] = useState<number | null>(null);
  const [lang, setLang] = useState("cpp20");
  const [code, setCode] = useState(LANGS[0].starter);
  const [dataFile, setDataFile] = useState<File | null>(null);
  const codeBytes = useMemo(() => new TextEncoder().encode(code).length, [code]);
  const srcOver = codeBytes > SRC_LIMIT;
  const dataOver = !!dataFile && dataFile.size > DATA_LIMIT;
  const rightTabs: [RightTab, string][] = isStep ? [["submit", "제출"], ["history", "제출 내역"]] : [["submit", "제출"], ["history", "제출 내역"], ["eval", "중간 평가"]];

  function submitStep() {
    const seed = missions[mission] ?? missions[0];
    if (!seed) return;
    const r = gradeStep(seed.input, stepOutput, seed.budget);
    if (!r.valid) { push("✕", `미션 ${seed.mission} 제출 채점 완료`, `무효 (0점) · ${r.msg}`, `미션 ${seed.mission} 제출 — 채점 불가 (${r.msg})`); return; }
    push("✓", `미션 ${seed.mission} 제출 채점 완료`, `점수 ${fmt(r.score)} · 비용 ${r.cost}/기준 ${r.ref}`, `미션 ${seed.mission} 채점 완료 — 점수 ${fmt(r.score)} (비용 ${r.cost}/기준 ${r.ref})`);
    addStepSub(contest.id, { id: 1000 + Math.floor(Math.random() * 9000), mission: seed.mission, at: "방금", score: r.score!, max: seed.budget });
  }
  function submitChallenge() {
    if (srcOver || dataOver) return;
    const id = 10000 + Math.floor(Math.random() * 900);
    const cost = chSubCost(id);          // sample cost sum (matches the detail view)
    const dn = dataFile ? ` · data.bin ${fmtBytes(dataFile.size)}` : "";
    push("✓", `제출 #${id} 채점 완료`, `샘플 비용 합 ${fmt(cost)}${dn} · 점수는 중간 평가 후 산정`, `제출 #${id} 채점 완료 — 샘플 비용 합 ${fmt(cost)}${dn}. 점수는 다음 중간 평가에서 산정됩니다(그 전 0점).`);
    addChSub(contest.id, { id, at: "방금", cost });
  }
  function setRep(id: number) { setRepCh(contest.id, id); push("✓", "대표 코드 설정", `제출 #${id}을(를) 대표 코드로 설정`, `제출 #${id}을(를) 대표 코드로 설정했습니다.`); }

  return <div className="solve">
    <div className="pane left">
      <div className="pane-head">
        <div className="tabs">
          <button className={"tab" + (leftTab === "statement" ? " active" : "")} onClick={() => setLeftTab("statement")}>문제</button>
          <button className={"tab" + (leftTab === "sim" ? " active" : "")} disabled={simDisabled} title={simDisabled ? "커스텀 생성기 문제는 브라우저 시뮬레이터를 지원하지 않습니다" : undefined} style={simDisabled ? { opacity: .45, cursor: "not-allowed" } : undefined} onClick={() => { if (!simDisabled) setLeftTab("sim"); }}>시뮬레이터</button>
        </div>
        <Link href={`/c/${contest.id}`} className="btn ghost" style={{ padding: "6px 12px" }}>← 문제 목록</Link>
      </div>
      <div className="pane-body">
        {leftTab === "sim"
          ? (isStep ? (missions.length ? <StepSimulator mission={mission} setMission={setMission} onOutput={setStepOutput} missions={missions} initial={stepOutput} /> : <CustomGenNotice />) : <ChallengeSimulator />)
          : (isStep ? <MarkdownView md={statement} /> : <ChallengeStatement md={statement} />)}
      </div>
    </div>
    <div className="pane right">
      <div className="pane-head"><div className="tabs">{rightTabs.map(([k, l]) => <button key={k} className={"tab" + (rightTab === k ? " active" : "")} onClick={() => { setRightTab(k); setSubSel(null); setEvalSel(null); }}>{l}</button>)}</div></div>
      <div className="pane-body">
        {rightTab === "submit" && isStep && (missions.length ? <>
          <RuleBanner />
          {!live && <NotLiveNotice status={contest.status} />}
          <p className="muted" style={{ fontSize: 12, margin: "12px 0 8px", lineHeight: 1.6 }}>
            스텝 업은 <b style={{ color: "var(--fg)" }}>코드가 아니라 '출력'</b>을 제출합니다. 미션별로 따로 채점되며,
            왼쪽 시뮬레이터로 만든 출력은 아래 칸에 <b style={{ color: "var(--fg)" }}>자동 반영</b>돼요.
          </p>
          <div className="k muted" style={{ fontSize: 11, marginBottom: 4 }}>미션 {missions[mission]?.mission ?? 1} 출력</div>
          <textarea rows={12} value={stepOutput} onChange={(e) => setStepOutput(e.target.value)} placeholder="여기에 출력(이동 문자열)을 붙여넣거나, 왼쪽 시뮬레이터로 만드세요" />
          <div className="submitbar" style={{ flexDirection: "column", alignItems: "stretch", gap: 10, paddingLeft: 0, paddingRight: 0, borderTop: "none" }}>
            <div className="row" style={{ gap: 10 }}><span className="muted" style={{ fontSize: 12, whiteSpace: "nowrap" }}>제출 대상</span><MissionSelect mission={mission} setMission={setMission} missions={missions} /><span className="muted" style={{ fontSize: 12 }}>미션별 개별 제출</span></div>
            <div className="row" style={{ gap: 10 }}><button className="btn ghost" style={{ flex: 1 }} onClick={() => push("", "", "", `미션 ${missions[mission]?.mission ?? 1} 입력 다운로드 — 미리보기 모드에서는 제공되지 않습니다`)}>입력 다운로드</button><button className="btn" style={{ flex: 1 }} disabled={!live} onClick={submitStep}>⚲ 제출</button></div>
          </div>
        </> : <CustomGenNotice />)}
        {rightTab === "submit" && !isStep && <>
          <RuleBanner />
          {!live && <NotLiveNotice status={contest.status} />}
          <div className="row" style={{ gap: 10, margin: "12px 0 8px", flexWrap: "wrap" }}>
            <span className="muted" style={{ fontSize: 12 }}>언어</span>
            <select className="dd" value={lang} onChange={(e) => {
              const next = e.target.value;
              const pristine = code.trim() === "" || code === LANG_BY_ID[lang].starter;
              setLang(next);
              if (pristine) setCode(LANG_BY_ID[next].starter);   // never wipe edited code
            }}>
              {LANGS.map((l) => <option key={l.id} value={l.id} disabled={!l.enabled}>{l.label}{l.enabled ? "" : " (준비 중)"}</option>)}
            </select>
            <button className="btn ghost" style={{ padding: "6px 10px" }} onClick={() => setCode(LANG_BY_ID[lang].starter)}>스타터 코드 불러오기</button>
          </div>
          <textarea className="editor" value={code} onChange={(e) => setCode(e.target.value)} spellCheck={false} />
          <div className="row" style={{ justifyContent: "space-between", margin: "6px 2px 0", fontSize: 12 }}>
            <span className="muted">{LANG_BY_ID[lang].filename}</span>
            <span className={srcOver ? "bad" : "muted"}>소스 {fmtBytes(codeBytes)} / 1&nbsp;MB{srcOver ? " · 초과!" : ""}</span>
          </div>
          <label className="muted" style={{ fontSize: 12, display: "block", margin: "14px 0 6px", lineHeight: 1.6 }}>
            추가 데이터 <b style={{ color: "var(--fg)" }}>data.bin</b> (선택 · 최대 10&nbsp;MB) — 미리 계산한 데이터를 동봉하면
            프로그램이 실행 폴더의 <code>data.bin</code> 파일로 읽을 수 있어요(파일 입출력).
          </label>
          {dataFile ? <div className="filechip">
            <span>📦 <b>data.bin</b> · {fmtBytes(dataFile.size)}</span>
            {dataOver && <span className="bad" style={{ marginLeft: 8 }}>10MB 초과!</span>}
            <span className="x" onClick={() => setDataFile(null)}>✕ 제거</span>
          </div> : <label className="btn ghost" style={{ padding: "8px 12px", fontSize: 13, cursor: "pointer", display: "inline-block" }}>📎 data.bin 첨부
            <input type="file" style={{ display: "none" }} onChange={(e) => { const f = e.target.files?.[0]; if (f) setDataFile(f); e.currentTarget.value = ""; }} /></label>}
          <div className="submitbar">
            <span className="muted" style={{ fontSize: 12 }}>2초 / 1024MB (언어 공통)</span>
            <div style={{ flex: 1 }} />
            {(srcOver || dataOver) && <span className="bad" style={{ fontSize: 12 }}>{srcOver ? "소스 1MB 초과" : "data.bin 10MB 초과"}</span>}
            <button className="btn" disabled={srcOver || dataOver || !live} onClick={submitChallenge}>⚲ 제출</button>
          </div>
        </>}
        {rightTab === "history" && isStep && (missions.length ? (subSel == null ? <StepHistory mission={mission} setMission={setMission} hist={stepHist} open={setSubSel} missions={missions} /> : <StepSubDetail id={subSel} hist={stepHist} back={() => setSubSel(null)} push={push} missions={missions} />) : <CustomGenNotice />)}
        {rightTab === "history" && !isStep && (subSel == null ? <ChHistory hist={chHist} open={setSubSel} setRep={setRep} /> : <ChSubDetail id={subSel} hist={chHist} back={() => setSubSel(null)} push={push} />)}
        {rightTab === "eval" && (evalSel == null ? <EvalList open={(i) => { if (CH_EVALS[i].rank == null) { push("", "", "", "아직 중간 평가 대기 중입니다"); return; } setEvalSel(i); }} /> : <EvalDetail i={evalSel} back={() => setEvalSel(null)} push={push} />)}
      </div>
    </div>
  </div>;
}

function StepHistory({ mission, setMission, hist, open, missions }: { mission: number; setMission: (i: number) => void; hist: StepSub[]; open: (id: number) => void; missions: Mission[] }) {
  const mNo = missions[mission]?.mission ?? missions[0]?.mission ?? 1;
  const rows = hist.filter((h) => h.mission === mNo);
  return <>
    <div className="row" style={{ justifyContent: "space-between", marginBottom: 8 }}><h3 style={{ margin: 0 }}>미션 {mNo} 제출 내역</h3><MissionSelect mission={mission} setMission={setMission} missions={missions} /></div>
    <p className="muted" style={{ margin: "0 0 8px", fontSize: 12 }}>스텝 업은 <b>미션마다 따로</b> 제출·채점됩니다. 행을 누르면 제출 상세를 볼 수 있어요.</p>
    <table><tbody><tr><th>#</th><th>제출 시각</th><th>결과</th></tr>
      {rows.length ? rows.map((h) => <tr key={h.id} style={{ cursor: "pointer" }} onClick={() => open(h.id)}><td>{h.id}</td><td className="muted">{h.at}</td><td style={{ minWidth: 90 }}><Bar g={h.score} t={h.max} sm /></td></tr>)
        : <tr><td colSpan={3} className="muted" style={{ padding: 16, textAlign: "center" }}>이 미션엔 아직 제출이 없어요.</td></tr>}
    </tbody></table>
  </>;
}
function StepSubDetail({ id, hist, back, push, missions }: { id: number; hist: StepSub[]; back: () => void; push: any; missions: Mission[] }) {
  const s = hist.find((x) => x.id === id) || hist[0];
  const seed = missions.find((m) => m.mission === s.mission) || missions[0];
  const full = s.score >= s.max;
  const ref = referenceCost(parseInst(seed.input));
  const cost = full ? "≤기준" : Math.round(ref / (s.score / s.max));
  return <>
    <div className="back" onClick={back}>← 제출 내역</div>
    <div className="row" style={{ justifyContent: "space-between", alignItems: "center", margin: "10px 0 4px" }}><h2 style={{ margin: 0 }}>제출 #{s.id} <span className="muted" style={{ fontSize: 14, fontWeight: 400 }}>· 미션 {s.mission}</span></h2><button className="btn ghost" style={{ padding: "7px 12px", whiteSpace: "nowrap" }} onClick={() => push("", "", "", `제출 #${s.id} 내 출력 다운로드 (데모)`)}>⬇ 내 출력</button></div>
    <p className="muted" style={{ margin: "0 0 14px" }}>{s.at} · 스텝 업은 미션 1개에 대한 출력 1건입니다.</p>
    <div className="card" style={{ marginBottom: 12 }}><div className="k muted" style={{ fontSize: 11, marginBottom: 6 }}>미션 {s.mission} 점수</div><Bar g={s.score} t={s.max} /><div className="row" style={{ gap: 20, marginTop: 12, fontSize: 13, flexWrap: "wrap" }}><span>내 비용 <b>{cost}</b></span><span>기준 비용 <b>{ref}</b></span><span>{full ? <span className="ok">✓ 만점</span> : "부분점수"}</span></div></div>
    <button className="btn ghost" onClick={() => push("", "", "", `미션 ${s.mission} 입력 다운로드 (데모)`)}>⬇ 미션 입력 다운로드</button>
  </>;
}
function ChHistory({ hist, open, setRep }: { hist: ChSub[]; open: (id: number) => void; setRep: (id: number) => void }) {
  return <>
    <h2 style={{ margin: "4px 0 8px" }}>최종 제출 목록</h2>
    <p className="muted" style={{ margin: "0 0 8px", fontSize: 12 }}>행을 누르면 샘플 테스트별 점수를 볼 수 있어요. 평가에 쓸 제출을 <b>대표 코드</b>로 지정하세요.</p>
    <table><tbody><tr><th>#</th><th>제출 시각</th><th>결과(비용)</th><th>대표 코드</th></tr>
      {hist.map((h) => <tr key={h.id} className={h.cur ? "sel" : ""} style={{ cursor: "pointer" }} onClick={() => open(h.id)}><td>{h.id}</td><td className="muted">{h.at}</td><td><span className="ok">✓ 성공</span> · <b>{fmt(h.cost)}</b></td>
        <td>{h.cur ? <span className="ok" style={{ fontSize: 12, whiteSpace: "nowrap" }}>★ 현재 대표</span> : <button className="btn ghost" style={{ padding: "4px 10px", whiteSpace: "nowrap" }} onClick={(e) => { e.stopPropagation(); setRep(h.id); }}>대표 코드로 설정</button>}</td></tr>)}
    </tbody></table>
  </>;
}
function ChSubDetail({ id, hist, back, push }: { id: number; hist: ChSub[]; back: () => void; push: any }) {
  const s = hist.find((x) => x.id === id) || hist[0];
  const rows = chSampleRows(id); const sum = rows.reduce((a, r) => a + r.cost, 0);   // == s.cost
  return <>
    <div className="back" onClick={back}>← 제출 내역</div>
    <div className="row" style={{ justifyContent: "space-between", alignItems: "center", margin: "10px 0 4px" }}><h2 style={{ margin: 0 }}>제출 #{s.id}</h2><button className="btn ghost" style={{ padding: "7px 12px", whiteSpace: "nowrap" }} onClick={() => push("", "", "", `제출 #${s.id} 소스 코드 다운로드 — 미리보기 모드에서는 제공되지 않습니다`)}>⬇ 소스</button></div>
    <p className="muted" style={{ margin: "0 0 10px" }}>{s.at} · 샘플 비용 합 <b>{fmt(sum)}</b></p>
    <div style={{ background: "#0f1117", border: "1px solid var(--line)", borderRadius: 8, padding: "10px 12px", margin: "0 0 12px", fontSize: 12, lineHeight: 1.6 }}>
      <span className="muted">샘플은 <b style={{ color: "var(--fg)" }}>비용 확인용</b>입니다. <b style={{ color: "var(--fg)" }}>점수(상대 등수)는 다음 중간 평가에서 산정</b>되며, 그 전까지는 <b style={{ color: "var(--fg)" }}>0점</b>입니다.</span>
    </div>
    <div className="tscroll"><table><tbody><tr><th>샘플</th><th>결과</th><th>비용</th><th>실행시간</th><th>입력</th></tr>
      {rows.map((r) => <tr key={r.i}><td>#{r.i}</td><td><span className="ok">✓ 성공</span></td><td><b>{fmt(r.cost)}</b></td><td className="muted">{r.ms} ms</td><td><button className="btn ghost" style={{ padding: "4px 10px" }} onClick={() => push("", "", "", `샘플 #${r.i} 입력 다운로드 — 미리보기 모드에서는 제공되지 않습니다`)}>⬇</button></td></tr>)}
    </tbody></table></div>
  </>;
}
function EvalList({ open }: { open: (i: number) => void }) {
  return <>
    <h2 style={{ margin: "4px 0 8px" }}>모든 중간 평가 보기</h2>
    <p className="muted" style={{ margin: "0 0 8px", fontSize: 12 }}>행을 누르면 테스트케이스별 점수·등수를 볼 수 있어요.</p>
    <table><tbody><tr><th>중간 평가 시각</th><th>등수</th><th>챌린지 점수</th><th>노트</th></tr>
      {CH_EVALS.map((e, i) => { const waiting = e.rank == null; return <tr key={i} style={{ cursor: waiting ? "default" : "pointer", opacity: waiting ? .6 : 1 }} onClick={() => { if (!waiting) open(i); }}><td>{e.at}</td><td>{waiting ? <span className="muted">집계 중</span> : <>🌸 <b>{e.rank}</b></>}</td><td>{waiting ? <span className="muted">—</span> : <b>{fmt(challengeEval(i).chScore)}</b>}</td><td className="muted">{e.note}</td></tr>; })}
    </tbody></table>
    <div className="card" style={{ marginTop: 14 }}><b>중간 평가 안내</b><p className="muted" style={{ margin: "6px 0 0" }}>매일 09:00·18:00(KST)에 새 비공개 데이터로 평가합니다.</p></div>
  </>;
}
function EvalDetail({ i, back, push }: { i: number; back: () => void; push: any }) {
  const e = CH_EVALS[i];
  if (!e) return <div className="back" onClick={back}>← 중간 평가 목록</div>;
  const ev = challengeEval(i);          // per-case scores; chScore = their MEAN
  return <>
    <div className="back" onClick={back}>← 중간 평가 목록</div>
    <h2 style={{ margin: "8px 0 2px" }}>{e.note}</h2><p className="muted" style={{ margin: "0 0 12px" }}>{e.at}</p>
    <div className="card" style={{ marginBottom: 14 }}><div className="row" style={{ gap: 28, flexWrap: "wrap" }}>
      <div><div className="k muted" style={{ fontSize: 11 }}>참가자 수</div><div style={{ fontSize: 20, fontWeight: 700 }}>{ev.nTotal}명</div></div>
      <div><div className="k muted" style={{ fontSize: 11 }}>내 등수</div><div style={{ fontSize: 20, fontWeight: 700 }}>🌸 {ev.myRank}</div></div>
      <div><div className="k muted" style={{ fontSize: 11 }}>챌린지 점수 <span style={{ fontWeight: 400 }}>(케이스 평균)</span></div><div style={{ fontSize: 20, fontWeight: 700 }}>{fmt(ev.chScore)}</div></div>
      <div><div className="k muted" style={{ fontSize: 11 }}>비용 합</div><div style={{ fontSize: 20, fontWeight: 700 }}>{fmt(ev.costSum)}</div></div>
    </div></div>
    <div className="row" style={{ justifyContent: "space-between", alignItems: "center", margin: "0 0 6px" }}><h3 style={{ margin: 0 }}>테스트케이스별 점수 / 등수</h3><button className="btn ghost" style={{ padding: "6px 12px" }} onClick={() => push("", "", "", `${e.note} 전체 테스트데이터 다운로드 — 미리보기 모드에서는 제공되지 않습니다`)}>⬇ 전체 입력 다운로드</button></div>
    <p className="muted" style={{ fontSize: 12, margin: "0 0 8px", lineHeight: 1.6 }}>케이스 <b style={{ color: "var(--fg)" }}>평균</b>이 챌린지 점수입니다(합이 아님). 케이스 점수 = ⌊10⁶·(1 − 0.5·√((n<sub>lose</sub> + 0.5·n<sub>draw</sub>)/n))⌋.</p>
    <div className="tscroll"><table><tbody><tr><th>테스트케이스</th><th>내 점수</th><th>등수</th><th>비용</th><th>실행시간</th><th>데이터</th></tr>
      {ev.cases.map((c, j) => <tr key={j}><td className="muted">{c.name}</td><td style={{ minWidth: 90 }}><Bar g={c.score} t={1000000} sm /></td><td>{c.rank === 1 ? <span className="ok"><b>1위</b></span> : `${c.rank}위`}</td><td className="muted">{fmt(c.cost)}</td><td className="muted">{c.ms} ms</td><td><button className="btn ghost" style={{ padding: "4px 10px" }} onClick={() => push("", "", "", `케이스 ${c.name} 입력 다운로드 — 미리보기 모드에서는 제공되지 않습니다`)}>입력 ⬇</button></td></tr>)}
    </tbody></table></div>
  </>;
}

// ===== contest list / detail / create (route views) =====
// Step Up is LIVE (recomputed from the user's submissions); Challenge is the last
// evaluated score (demo value, since eval doesn't run in mock mode). total = 2:8.
function partScores(c: Contest, stepHist: StepSub[]) {
  const su = stepHist.length ? stepupScoreFromHist(stepHist) : (c.suScore ?? 0);
  const ch = c.chScore ?? 0;
  return { su, ch, total: weightedTotal(su, ch) };
}

export function ContestListView() {
  const { apiMode, isAdmin, contests, stepSubsFor } = useStore();
  const st = { live: "진행 중", ended: "종료", soon: "예정" } as const;
  async function delContest(id: string, title: string) {
    if (typeof window === "undefined") return;
    if (!window.confirm(`'${title}'를 영구 삭제할까요?\n\n문제·제출·평가·랭킹·리플레이가 모두 함께 삭제되며 되돌릴 수 없습니다.`)) return;
    try { await deleteContest(id); window.location.reload(); }
    catch (err: any) { window.alert("삭제 실패: " + String(err?.message ?? err)); }
  }
  return <div className="wrap">
    <div className="row" style={{ justifyContent: "space-between", marginBottom: 18 }}><h1>모의고사 목록</h1>{isAdmin && <Link href="/create" className="btn">+ 새 모의고사 (관리자)</Link>}</div>
    {contests.length === 0 && <p className="muted" style={{ marginTop: 24 }}>참여할 수 있는 모의고사가 아직 없습니다.</p>}
    {contests.map((c) => <Link key={c.id} href={`/c/${c.id}`} className="card" style={{ marginBottom: 14, display: "block", textDecoration: "none" }}>
      <div className="row" style={{ justifyContent: "space-between", flexWrap: "wrap", rowGap: 12 }}>
        <div style={{ flex: "1 1 280px", minWidth: 0 }}>
          <div className="row" style={{ flexWrap: "wrap", rowGap: 4 }}><h3 style={{ margin: 0 }}>{c.title}</h3><span className={"status " + c.status}>{st[c.status]}</span></div>
          <div className="muted" style={{ marginTop: 6 }}>{c.desc ? `${c.desc} · ` : ""}{c.when}
            {c.status === "ended" && c.rank != null && <> · 최종 등수 <b style={{ color: "#c9a0ff" }}>{c.rank}위</b></>}
            {c.status === "live" && c.rank != null && <> · 현재 등수 <b className="ok">{c.rank}</b>{c.nextEval && <> · 다음 평가 {c.nextEval}</>}</>}</div>
        </div>
        {c.status === "soon"
          ? <span className="btn ghost" style={{ opacity: .6 }}>{c.when}</span>
          : apiMode
            ? <span className="btn ghost" style={{ opacity: .8, whiteSpace: "nowrap" }}>점수 보기 →</span>
            : <div style={{ flex: "1 1 240px", minWidth: 0 }}><Bar g={partScores(c, stepSubsFor(c.id)).total} t={1000000} /></div>}
        {apiMode && isAdmin && <button className="btn ghost" title="모의고사 삭제 (관리자)" style={{ padding: "4px 9px", fontSize: 12, color: "var(--accent)", borderColor: "var(--accent)", whiteSpace: "nowrap" }} onClick={(e) => { e.preventDefault(); e.stopPropagation(); delContest(c.id, c.title); }}>🗑</button>}
      </div>
    </Link>)}
  </div>;
}

export function ContestDetailView(props: { contest: Contest }) {
  const { apiMode } = useStore();
  return apiMode ? <ApiContestDetail contest={props.contest} /> : <MockContestDetail {...props} />;
}

function MockContestDetail({ contest: c }: { contest: Contest }) {
  const { stepSubsFor } = useStore();
  const ended = c.status === "ended";
  const { su, ch, total } = partScores(c, stepSubsFor(c.id));
  const [tab, setTab] = useState<"problems" | "ranking">("problems");
  // demo registration (localStorage) — the API path uses the real /registration endpoints.
  const [reg, setReg] = useState(false);
  useEffect(() => { try { setReg(localStorage.getItem(`dmpc_reg_${c.id}`) === "1"); } catch {} }, [c.id]);
  const regBase = ended ? 70 : 69;
  function toggleReg() { setReg((v) => { const nv = !v; try { localStorage.setItem(`dmpc_reg_${c.id}`, nv ? "1" : "0"); } catch {} return nv; }); }
  return <div className="wrap">
    <Link href="/" className="back">← 모의고사 목록</Link>
    <h2 className="center muted" style={{ margin: "18px 0 8px" }}>예상 총점 <span style={{ fontSize: 12, fontWeight: 400 }}>(스텝업 20% + 챌린지 80%)</span></h2><Bar g={total} t={1000000} />
    <div style={{ margin: "14px 0 18px" }}><RuleBanner /></div>
    <RegistrationCard registered={reg} count={regBase + (reg ? 1 : 0)} open={!ended} onToggle={toggleReg} />
    {ended && <div className="tabs" style={{ justifyContent: "center", margin: "6px 0 20px" }}><button className={"tab" + (tab !== "ranking" ? " active" : "")} onClick={() => setTab("problems")}>문제</button><button className={"tab" + (tab === "ranking" ? " active" : "")} onClick={() => setTab("ranking")}>🏆 랭킹</button></div>}
    {ended && tab === "ranking" ? <>
      <h2 className="center" style={{ marginBottom: 6 }}>최종 랭킹</h2><p className="muted center" style={{ margin: "0 0 16px" }}>대회 종료 후에만 공개됩니다 · 참가자 70명</p>
      <div className="card" style={{ padding: "6px 0" }}><table><tbody><tr><th style={{ paddingLeft: 18 }}>등수</th><th>닉네임</th><th style={{ textAlign: "right", paddingRight: 18 }}>총점</th></tr>
        {RANKING.map((r) => <tr key={r.rank} className={r.me ? "sel" : ""}><td style={{ paddingLeft: 18 }}>{r.rank <= 3 ? ["🥇", "🥈", "🥉"][r.rank - 1] : r.rank}</td><td>{r.nick}{r.me && <span className="pill"> 나</span>}</td><td style={{ textAlign: "right", paddingRight: 18 }}><b>{fmt(r.score)}</b></td></tr>)}
      </tbody></table></div>
      <ReplayShowcase
        podium={RANKING.filter((r) => r.rank <= 3).map((r) => ({ rank: r.rank, nick: r.nick, score: r.score, me: r.me }))}
        replays={[{ id: "demo1", nickname: "neko_master", rank: 1, is_shared: true, moderated: true, is_mine: false,
          body: "먼지를 가까운 것끼리 군집으로 묶고, 시작점에서 가장 가까운 군집부터 그리디로 방문했습니다.\n군집 내부 순서는 2-opt로 다듬어 이동 수를 줄였어요. 군집 경계에서 다음 군집 진입점을 최소화한 게 핵심." }]}
      />
    </> : <>
      <h2 className="center" style={{ marginBottom: 16 }}>문제 목록</h2>
      <Link href={`/c/${c.id}/stepup`} className="card" style={{ marginBottom: 14, display: "block", textDecoration: "none" }}>
        <h3 style={{ marginBottom: 4 }}>{c.desc} — 스텝 업 <span className="pill" style={{ marginLeft: 6 }}>만점 100만 · 반영 20%</span></h3>
        <div className="muted" style={{ marginBottom: 10, fontSize: 13 }}>미션별로 <b>정답 출력을 직접 제출</b> · 기준 비용 대비 절대 점수 · 코드 불필요</div>
        <Bar g={su} t={1000000} /></Link>
      <Link href={`/c/${c.id}/challenge`} className="card" style={{ display: "block", textDecoration: "none" }}>
        <h3 style={{ marginBottom: 4 }}>{c.desc} — 챌린지 <span className="pill" style={{ marginLeft: 6 }}>만점 100만 · 반영 80%</span></h3>
        <div className="muted" style={{ marginBottom: 8, fontSize: 13 }}><b>코드를 제출</b> · 참가자 <b>상대 등수</b>로 채점 · 매일 09·18시 평가 {ch === 0 && <span>(아직 평가 전 — 0점)</span>}</div>
        <div className="row muted" style={{ gap: 18, marginBottom: 10, fontSize: 13 }}><span>🏅 {c.status === "live" ? "현재" : "최종"} 등수 <b className="ok">{c.rank ?? "-"}</b></span>{c.status === "live" && <span>⏱ 다음 중간 평가까지 <b>{c.nextEval}</b></span>}</div><Bar g={ch} t={1000000} /></Link>
    </>}
  </div>;
}

// ===== real-API views (apiMode) — fetch from the backend instead of mock data =====
function ApiLoading({ label }: { label?: string }) {
  return <p className="muted" style={{ marginTop: 24 }}>{label ?? "불러오는 중…"}</p>;
}
function ApiErrorCard({ msg }: { msg: string }) {
  return <div className="card" style={{ marginTop: 18, borderColor: "var(--line2)" }}>
    <b className="bad">불러오지 못했습니다</b><p className="muted" style={{ margin: "8px 0 0", whiteSpace: "pre-wrap" }}>{msg}</p>
  </div>;
}
const fmtAt = (iso: string) => {
  if (!iso) return "";
  const d = new Date(iso);
  if (isNaN(d.getTime())) return iso.slice(0, 16).replace("T", " ");
  const k = new Date(d.getTime() + 9 * 3600 * 1000);   // render in KST (UTC+9), any viewer tz
  const p = (n: number) => String(n).padStart(2, "0");
  return `${k.getUTCFullYear()}-${p(k.getUTCMonth() + 1)}-${p(k.getUTCDate())} ${p(k.getUTCHours())}:${p(k.getUTCMinutes())} KST`;
};

// ===== replays / 시상 (winners' writeups) — used by both mock & API detail =====
type Podium = { rank: number; nick: string; score: number; me?: boolean };
type ReplayView = { id: string; nickname: string; rank: number | null; body: string; has_pdf?: boolean; is_shared: boolean; moderated: boolean; is_mine: boolean };

// user-generated -> render as ESCAPED plain text (React escapes; no HTML/markdown), so
// a shared writeup can never inject script. Line breaks preserved.
function ReplayBody({ text }: { text: string }) {
  return <div style={{ whiteSpace: "pre-wrap", fontSize: 14, lineHeight: 1.7, color: "var(--fg)" }}>{text}</div>;
}

function ReplayEditor({ initial, onSave }: { initial: MyReplay["replay"]; onSave: (body: string, share: boolean, pdf: File | null) => Promise<void> }) {
  const [body, setBody] = useState(initial?.body ?? "");
  const [share, setShare] = useState(initial?.is_shared ?? false);
  const [pdf, setPdf] = useState<File | null>(null);
  const [busy, setBusy] = useState(false);
  const hasExistingPdf = !!initial?.has_pdf;
  const canSave = !!body.trim() || !!pdf || hasExistingPdf;
  return <div className="card" style={{ marginBottom: 16 }}>
    <b>🏅 내 풀이 공유 <span className="muted" style={{ fontWeight: 400, fontSize: 12 }}>(최종 상위 3위)</span></b>
    {initial && <p className="muted" style={{ fontSize: 12, margin: "6px 0 0" }}>현재 상태: {initial.is_shared ? (initial.moderated ? "✓ 공개 승인됨" : "검토 대기 중 (관리자 승인 후 공개)") : "비공개"}{hasExistingPdf && " · PDF 첨부됨"}</p>}
    <textarea rows={5} value={body} onChange={(e) => setBody(e.target.value)} placeholder="접근 방법·핵심 아이디어 (선택 — 서식 없는 텍스트, 최대 20,000자)" style={{ marginTop: 8 }} />
    <div className="row" style={{ gap: 8, marginTop: 8, alignItems: "center", flexWrap: "wrap" }}>
      <label className="btn ghost" style={{ padding: "5px 10px", fontSize: 12, cursor: "pointer" }}>📄 PDF 첨부
        <input type="file" accept="application/pdf,.pdf" style={{ display: "none" }} onChange={(e) => setPdf(e.target.files?.[0] ?? null)} /></label>
      <span className="muted" style={{ fontSize: 12 }}>{pdf ? pdf.name : (hasExistingPdf ? "기존 PDF 유지 (새로 올리면 교체)" : "PDF 보고서 (선택, ≤10MB)")}</span>
    </div>
    <div className="row" style={{ justifyContent: "space-between", marginTop: 8, flexWrap: "wrap", gap: 8 }}>
      <label className="row" style={{ gap: 6, fontSize: 13, cursor: "pointer" }}><input type="checkbox" checked={share} onChange={(e) => setShare(e.target.checked)} /> 다른 참가자에게 공개 (관리자 승인 후)</label>
      <button className="btn" disabled={busy || !canSave} onClick={async () => { setBusy(true); try { await onSave(body, share, pdf); } finally { setBusy(false); } }}>{busy ? "저장 중…" : "저장"}</button>
    </div>
    <p className="muted" style={{ fontSize: 11, margin: "8px 0 0" }}>텍스트·PDF 중 하나 이상. 편집하면 다시 검토가 필요합니다. 텍스트는 안전을 위해 <b>서식 없는 텍스트</b>로 표시됩니다.</p>
  </div>;
}

function ReplayShowcase({ cid, podium, replays, myReplay, isAdmin, onSave, onModerate }: {
  cid?: string; podium: Podium[]; replays: ReplayView[]; myReplay?: MyReplay | null; isAdmin?: boolean;
  onSave?: (body: string, share: boolean, pdf: File | null) => Promise<void>; onModerate?: (id: string, moderated: boolean) => void;
}) {
  const medal = (r: number | null) => (r != null && r >= 1 && r <= 3 ? ["🥇", "🥈", "🥉"][r - 1] : (r ?? "-"));
  return <div style={{ marginTop: 30 }}>
    <h2 className="center" style={{ marginBottom: 6 }}>🏆 시상대</h2>
    <p className="muted center" style={{ margin: "0 0 14px" }}>최종 상위 3위와 공개된 풀이</p>
    <div className="row" style={{ gap: 10, justifyContent: "center", flexWrap: "wrap", marginBottom: 18 }}>
      {podium.slice(0, 3).map((p) => <div key={p.rank} className={"card" + (p.me ? " sel" : "")} style={{ minWidth: 150, textAlign: "center", flex: "0 1 180px" }}>
        <div style={{ fontSize: 26 }}>{medal(p.rank)}</div>
        <div style={{ fontWeight: 700, marginTop: 4 }}>{p.nick}{p.me && <span className="pill"> 나</span>}</div>
        <div className="muted" style={{ fontSize: 13, marginTop: 2 }}>{fmt(p.score)}</div>
      </div>)}
      {podium.length === 0 && <p className="muted">아직 시상 결과가 없습니다.</p>}
    </div>
    {myReplay?.eligible && onSave && <ReplayEditor initial={myReplay.replay} onSave={onSave} />}
    <h3 style={{ margin: "10px 0 8px" }}>풀이 ({replays.length})</h3>
    {replays.length ? replays.map((r) => <div key={r.id} className="card" style={{ marginBottom: 12 }}>
      <div className="row" style={{ justifyContent: "space-between", alignItems: "center", marginBottom: 8, flexWrap: "wrap", gap: 6 }}>
        <b>{medal(r.rank)} {r.nickname}{r.is_mine && <span className="pill"> 나</span>}</b>
        <span className="row" style={{ gap: 8, fontSize: 12, alignItems: "center" }}>
          {r.is_shared ? (r.moderated ? <span className="ok">공개</span> : <span className="muted">검토 대기</span>) : <span className="muted">비공개</span>}
          {isAdmin && onModerate && <button className="btn ghost" style={{ padding: "3px 8px" }} onClick={() => onModerate(r.id, !r.moderated)}>{r.moderated ? "비공개로" : "승인"}</button>}
        </span>
      </div>
      {r.body && <ReplayBody text={r.body} />}
      {r.has_pdf && cid && <a className="btn ghost" style={{ padding: "5px 12px", fontSize: 13, marginTop: r.body ? 8 : 0, display: "inline-block" }} href={replayPdfUrl(cid, r.id)} target="_blank" rel="noreferrer">📄 PDF 풀이 보기 / 다운로드</a>}
    </div>) : <p className="muted">아직 공개된 풀이가 없습니다.</p>}
  </div>;
}

// participant roster + 참가 신청/취소 — shared by mock & API detail.
function RegistrationCard({ registered, count, open, busy, onToggle }: {
  registered: boolean; count: number; open: boolean; busy?: boolean; onToggle: () => void;
}) {
  return <div className="card" style={{ marginBottom: 16, display: "flex", justifyContent: "space-between", alignItems: "center", flexWrap: "wrap", gap: 10 }}>
    <span style={{ fontSize: 14 }}>👥 참가자 <b>{fmt(count)}</b>명{registered && <span className="pill" style={{ marginLeft: 8 }}>참가 중</span>}</span>
    {open
      ? <button className={"btn" + (registered ? " ghost" : "")} disabled={busy} onClick={onToggle}>{busy ? "처리 중…" : registered ? "참가 취소" : "참가 신청"}</button>
      : <span className="muted" style={{ fontSize: 13 }}>{registered ? "참가 신청 완료" : "참가 신청 마감"}</span>}
  </div>;
}

function ApiContestDetail({ contest: c }: { contest: Contest }) {
  const { nick, isAdmin, showToast } = useStore();
  const [detail, setDetail] = useState<ApiContestDetail | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [tab, setTab] = useState<"problems" | "ranking">("problems");
  const [standings, setStandings] = useState<StandingRow[] | null>(null);
  const [stState, setStState] = useState<"idle" | "loading" | "error" | "done">("idle");
  const [stErr, setStErr] = useState("");
  const [replays, setReplays] = useState<Replay[]>([]);
  const [myReplay, setMyReplay] = useState<MyReplay | null>(null);
  const [reg, setReg] = useState<Registration | null>(null);
  const [regBusy, setRegBusy] = useState(false);

  useEffect(() => {
    let on = true;
    getContestDetail(c.id).then((d) => { if (on) setDetail(d); }).catch((e) => { if (on) setErr(String(e?.message ?? e)); });
    getRegistration(c.id).then((r) => { if (on) setReg(r); }).catch(() => {});
    return () => { on = false; };
  }, [c.id]);

  async function toggleReg() {
    if (regBusy) return;
    setRegBusy(true);
    try {
      const r = reg?.registered ? await unregisterContest(c.id) : await registerContest(c.id);
      setReg((x) => (x ? { ...x, registered: r.registered, count: r.count } : x));
      showToast(r.registered ? "참가 신청 완료" : "참가를 취소했습니다");
    } catch (e: any) { showToast("처리 실패", String(e?.message ?? e)); }
    finally { setRegBusy(false); }
  }

  function loadReplays() {
    getReplays(c.id).then(setReplays).catch(() => setReplays([]));
    getMyReplay(c.id).then(setMyReplay).catch(() => setMyReplay(null));
  }
  function loadStandings() {
    if (stState !== "idle") return;
    setStState("loading");
    getStandings(c.id)
      .then((rows) => { setStandings(rows); setStState("done"); })
      .catch((e) => { setStErr(String(e?.message ?? e)); setStState("error"); });
    loadReplays();
  }
  async function saveReplay(body: string, share: boolean, pdf: File | null) {
    try { await postReplay(c.id, body, share, pdf); showToast("풀이를 저장했습니다", share ? "공개는 관리자 승인 후 반영됩니다" : "비공개로 저장됨"); loadReplays(); }
    catch (e: any) { showToast("저장 실패", String(e?.message ?? e)); }
  }
  function moderate(id: string, moderated: boolean) {
    moderateReplay(id, moderated).then(() => { showToast(moderated ? "공개 승인했습니다" : "비공개로 전환했습니다"); loadReplays(); })
      .catch((e) => showToast("처리 실패", String(e?.message ?? e)));
  }
  async function endNow() {
    if (typeof window !== "undefined" && !window.confirm("대회를 지금 종료할까요? 최종 평가를 채점하고 랭킹·리플레이를 공개합니다.")) return;
    try { await endContest(c.id); showToast("대회를 종료했습니다", "최종 평가 채점 후(evals 실행/자동) 랭킹·리플레이가 열립니다"); const d = await getContestDetail(c.id); setDetail(d); }
    catch (e: any) { showToast("종료 실패", String(e?.message ?? e)); }
  }
  async function publishNow() {
    if (typeof window !== "undefined" && !window.confirm("이 초안을 공개(live)로 전환할까요? 이후 모든 참가자에게 보입니다.")) return;
    try { await publishContest(c.id); showToast("대회를 공개했습니다", "이제 진행 중(live) — 참가자에게 보입니다"); const d = await getContestDetail(c.id); setDetail(d); }
    catch (e: any) { showToast("공개 실패", String(e?.message ?? e)); }
  }
  async function deleteNow() {
    if (typeof window === "undefined") return;
    const title = detail?.title ?? "이 모의고사";
    if (!window.confirm(`'${title}'를 영구 삭제할까요?\n\n문제·제출·평가·랭킹·리플레이가 모두 함께 삭제되며 되돌릴 수 없습니다.`)) return;
    try { await deleteContest(c.id); showToast("모의고사를 삭제했습니다", "문제·제출·평가·랭킹이 모두 삭제되었습니다"); window.location.href = "/"; }
    catch (e: any) { showToast("삭제 실패", String(e?.message ?? e)); }
  }
  const podium: Podium[] = (standings ?? []).filter((s) => s.rank != null && s.rank <= 3)
    .map((s) => ({ rank: s.rank as number, nick: s.nickname, score: s.score, me: !!nick && s.nickname === nick }));

  if (err) return <div className="wrap"><Link href="/" className="back">← 모의고사 목록</Link><ApiErrorCard msg={err} /></div>;
  if (!detail) return <div className="wrap"><Link href="/" className="back">← 모의고사 목록</Link><ApiLoading /></div>;

  const su = detail.problems.find((p) => p.kind === "stepup")?.score ?? 0;
  const ch = detail.problems.find((p) => p.kind === "challenge")?.score ?? 0;
  const hasStep = detail.problems.some((p) => p.kind === "stepup");
  const hasCh = detail.problems.some((p) => p.kind === "challenge");
  const ended = detail.status === "ended" || detail.status === "archived";

  return <div className="wrap">
    <Link href="/" className="back">← 모의고사 목록</Link>
    {detail.status === "draft" && <div className="center" style={{ margin: "10px 0 0" }}><span className="pill" style={{ background: "var(--line2)", color: "var(--fg)" }}>🧪 테스터 전용 초안 (비공개)</span></div>}
    <h2 className="center muted" style={{ margin: "18px 0 8px" }}>예상 총점 <span style={{ fontSize: 12, fontWeight: 400 }}>(스텝업 20% + 챌린지 80%)</span></h2>
    <Bar g={detail.total} t={1000000} />
    <div style={{ margin: "14px 0 18px" }}><RuleBanner /></div>
    {isAdmin && detail.status === "draft" && <div className="card" style={{ marginBottom: 12, borderColor: "var(--line2)" }}>
      <div className="row" style={{ justifyContent: "space-between", alignItems: "center", gap: 10, flexWrap: "wrap" }}>
        <span className="muted" style={{ fontSize: 12 }}>🧪 <b>테스터 전용 초안</b> — 테스터/관리자만 보입니다. 검증을 마치면 공개하세요.</span>
        <button className="btn ghost" style={{ padding: "5px 12px", fontSize: 12 }} onClick={publishNow}>공개로 전환</button>
      </div>
    </div>}
    {isAdmin && !ended && detail.status !== "draft" && <div className="row" style={{ justifyContent: "flex-end", marginBottom: 12 }}>
      <button className="btn ghost" style={{ padding: "5px 12px", fontSize: 12 }} onClick={endNow}>🏁 대회 종료 (관리자)</button>
    </div>}
    {isAdmin && <div className="row" style={{ justifyContent: "flex-end", marginBottom: 12 }}>
      <button className="btn ghost" style={{ padding: "5px 12px", fontSize: 12, color: "var(--accent)", borderColor: "var(--accent)" }} onClick={deleteNow}>🗑 모의고사 삭제 (관리자)</button>
    </div>}
    {reg && <RegistrationCard registered={reg.registered} count={reg.count} open={reg.open} busy={regBusy} onToggle={toggleReg} />}
    {ended && <div className="tabs" style={{ justifyContent: "center", margin: "6px 0 20px" }}>
      <button className={"tab" + (tab !== "ranking" ? " active" : "")} onClick={() => setTab("problems")}>문제</button>
      <button className={"tab" + (tab === "ranking" ? " active" : "")} onClick={() => { setTab("ranking"); loadStandings(); }}>🏆 랭킹</button>
    </div>}
    {ended && tab === "ranking" ? <>
      <h2 className="center" style={{ marginBottom: 6 }}>최종 랭킹</h2>
      <p className="muted center" style={{ margin: "0 0 16px" }}>대회 종료 후에만 공개됩니다{standings ? ` · 참가자 ${standings.length}명` : ""}</p>
      {stState === "loading" && <ApiLoading label="랭킹 집계 중…" />}
      {stState === "error" && <ApiErrorCard msg={stErr} />}
      {stState === "done" && (standings && standings.length ? <div className="card" style={{ padding: "6px 0" }}><table><tbody>
        <tr><th style={{ paddingLeft: 18 }}>등수</th><th>닉네임</th><th style={{ textAlign: "right", paddingRight: 18 }}>총점</th></tr>
        {standings.map((r, i) => { const me = !!nick && r.nickname === nick; return <tr key={i} className={me ? "sel" : ""}>
          <td style={{ paddingLeft: 18 }}>{r.rank != null && r.rank <= 3 ? ["🥇", "🥈", "🥉"][r.rank - 1] : (r.rank ?? "-")}</td>
          <td>{r.nickname}{me && <span className="pill"> 나</span>}</td>
          <td style={{ textAlign: "right", paddingRight: 18 }}><b>{fmt(r.score)}</b></td></tr>; })}
      </tbody></table></div> : <p className="muted center">아직 최종 채점 결과가 집계되지 않았습니다.</p>)}
      {stState === "done" && <ReplayShowcase cid={c.id} podium={podium} replays={replays as ReplayView[]} myReplay={myReplay} isAdmin={isAdmin} onSave={saveReplay} onModerate={moderate} />}
    </> : <>
      <h2 className="center" style={{ marginBottom: 16 }}>문제 목록</h2>
      {hasStep && <Link href={`/c/${c.id}/stepup`} className="card" style={{ marginBottom: 14, display: "block", textDecoration: "none" }}>
        <h3 style={{ marginBottom: 4 }}>{detail.title} — 스텝 업 <span className="pill" style={{ marginLeft: 6 }}>만점 100만 · 반영 20%</span></h3>
        <div className="muted" style={{ marginBottom: 10, fontSize: 13 }}>미션별로 <b>정답 출력을 직접 제출</b> · 기준 비용 대비 절대 점수 · 코드 불필요</div>
        <Bar g={su} t={1000000} /></Link>}
      {hasCh && <Link href={`/c/${c.id}/challenge`} className="card" style={{ display: "block", textDecoration: "none" }}>
        <h3 style={{ marginBottom: 4 }}>{detail.title} — 챌린지 <span className="pill" style={{ marginLeft: 6 }}>만점 100만 · 반영 80%</span></h3>
        <div className="muted" style={{ marginBottom: 8, fontSize: 13 }}><b>코드를 제출</b> · 참가자 <b>상대 등수</b>로 채점 · 매일 09·18시 평가 {ch === 0 && <span>(아직 평가 전 — 0점)</span>}</div>
        <Bar g={ch} t={1000000} /></Link>}
      {!hasStep && !hasCh && <p className="muted center">등록된 문제가 없습니다.</p>}
    </>}
  </div>;
}

type ApiMissionFull = { mission: number; seed: number; input: string; budget: number; best: number };

function ApiProblemView({ contest: c, kind }: { contest: Contest; kind: "stepup" | "challenge" }) {
  const { showToast, refreshNotifs, isAdmin } = useStore();
  const isStep = kind === "stepup";
  // status comes from the fetched detail (not the passed stub) so a deep-link / just-created
  // contest gates submission correctly even when it isn't in the cached list.
  const [contestStatus, setContestStatus] = useState(c.status);
  const live = contestStatus === "live";

  const [pid, setPid] = useState<string | null>(null);
  const [problem, setProblem] = useState<ApiProblem | null>(null);
  const [missions, setMissions] = useState<ApiMissionFull[]>([]);
  const [loadErr, setLoadErr] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);

  const [leftTab, setLeftTab] = useState<"statement" | "sim">("statement");
  const [rightTab, setRightTab] = useState<RightTab>("submit");
  const [mission, setMission] = useState(0);
  const [stepOutput, setStepOutput] = useState("");
  useEffect(() => { setStepOutput(""); }, [mission]);
  const [busy, setBusy] = useState(false);
  const busyRef = useRef(false);          // sync guard: blocks a double-submit before setBusy re-renders
  const [simInputOk, setSimInputOk] = useState(true);   // false if a mission input failed to load/parse

  const [lang, setLang] = useState("cpp20");
  const [code, setCode] = useState(LANGS[0].starter);
  const [dataFile, setDataFile] = useState<File | null>(null);
  const codeBytes = useMemo(() => new TextEncoder().encode(code).length, [code]);
  const srcOver = codeBytes > SRC_LIMIT;
  const dataOver = !!dataFile && dataFile.size > DATA_LIMIT;

  const [stepHist, setStepHist] = useState<StepupSubmission[] | null>(null);
  const [chHist, setChHist] = useState<ChallengeSubmission[] | null>(null);
  const [myEval, setMyEval] = useState<MyEval | null>(null);
  const [evalErr, setEvalErr] = useState("");

  // load the problem (and, for stepup, its mission inputs so the simulator works).
  useEffect(() => {
    let on = true;
    setLoading(true); setLoadErr(null);
    (async () => {
      try {
        const d = await getContestDetail(c.id);
        if (on) setContestStatus(d.status as Contest["status"]);
        const ref = d.problems.find((p) => p.kind === kind);
        if (!ref) { if (on) { setLoadErr(`이 모의고사에는 ${isStep ? "스텝 업" : "챌린지"} 문제가 없습니다.`); setLoading(false); } return; }
        if (on) setPid(ref.id);
        const p = await getProblem(ref.id);
        if (!on) return;
        setProblem(p);
        if (isStep && p.missions?.length) {
          const inputs = await Promise.all(p.missions.map((m) => getMissionInput(ref.id, m.seed).catch(() => "")));
          if (!on) return;
          // if any input failed to fetch or won't parse, keep the missions (so the user can
          // still paste an output and submit) but disable the in-browser simulator — feeding
          // an empty/garbage grid to StepSimulator's parseInst would crash the pane.
          const ok = inputs.every((t) => { try { return !!parseInst(t); } catch { return false; } });
          setSimInputOk(ok);
          setMissions(p.missions.map((m, i) => ({ mission: i + 1, seed: m.seed, input: inputs[i], budget: m.budget, best: m.best_score })));
        }
      } catch (e: any) {
        if (on) setLoadErr(String(e?.message ?? e));
      } finally {
        if (on) setLoading(false);
      }
    })();
    return () => { on = false; };
  }, [c.id, kind, isStep]);

  function reloadProblem() { if (pid) getProblem(pid).then(setProblem).catch(() => {}); }
  function loadStepHist() { if (pid) getStepupSubmissions(pid).then(setStepHist).catch(() => setStepHist([])); }
  function loadChHist() { if (pid) getChallengeSubmissions(pid).then(setChHist).catch(() => setChHist([])); }
  function loadEval() { setEvalErr(""); getMyEval(c.id).then(setMyEval).catch((e) => setEvalErr(String(e?.message ?? e))); }

  // lazily load the history/eval when its tab is first opened. The `on` flag drops any
  // in-flight response if the tab changes or the view unmounts (no setState-after-unmount).
  useEffect(() => {
    if (!pid) return;
    let on = true;
    if (rightTab === "history" && isStep && stepHist === null)
      getStepupSubmissions(pid).then((d) => on && setStepHist(d)).catch(() => on && setStepHist([]));
    if (rightTab === "history" && !isStep && chHist === null)
      getChallengeSubmissions(pid).then((d) => on && setChHist(d)).catch(() => on && setChHist([]));
    if (rightTab === "eval") {       // always refetch on open so a just-graded round shows
      setEvalErr("");
      getMyEval(c.id).then((d) => on && setMyEval(d)).catch((e) => on && setEvalErr(String(e?.message ?? e)));
    }
    return () => { on = false; };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [rightTab, pid]);

  async function submitStep() {
    if (!pid || busyRef.current) return;          // busyRef blocks a 2nd click before re-render
    const m = missions[mission];
    if (!m) return;
    if (!stepOutput.trim()) { showToast("출력이 비어 있습니다", "시뮬레이터로 만들거나 직접 붙여넣으세요"); return; }
    busyRef.current = true; setBusy(true);
    try {
      const r = await submitStepup(pid, m.seed, stepOutput);
      if (!r.valid) showToast(`미션 ${m.mission} — 채점 불가`, r.message || "무효 (0점)");
      else showToast(`미션 ${m.mission} 채점 완료 — 점수 ${fmt(r.score)}`, `비용 ${r.cost ?? "-"} · 예산 ${fmt(r.mission_budget)}`);
      loadStepHist(); reloadProblem(); refreshNotifs();
    } catch (e: any) { showToast("제출 실패", String(e?.message ?? e)); }
    finally { busyRef.current = false; setBusy(false); }
  }
  async function submitCh() {
    if (!pid || busyRef.current || srcOver || dataOver) return;
    busyRef.current = true; setBusy(true);
    try {
      const r = await submitChallenge(pid, lang, code, dataFile);
      showToast("제출 접수 — 채점 대기열에 등록", `제출 #${r.submission_id.slice(0, 8)} · 점수는 다음 중간 평가에서 산정됩니다(그 전 0점).`);
      loadChHist(); refreshNotifs();
    } catch (e: any) { showToast("제출 실패", String(e?.message ?? e)); }
    finally { busyRef.current = false; setBusy(false); }
  }
  async function runEvalNow() {                 // admin test helper: enqueue an immediate eval round
    if (busyRef.current) return;
    busyRef.current = true; setBusy(true);
    try {
      const r = await evaluateNow(c.id);
      showToast("평가 라운드 생성됨", `GitHub Actions의 evals 워크플로를 실행하면 채점됩니다(또는 ~15분 내 자동). round #${r.round_id.slice(0, 8)}`);
    } catch (e: any) { showToast("평가 생성 실패", String(e?.message ?? e)); }
    finally { busyRef.current = false; setBusy(false); }
  }

  if (loading) return <div className="wrap"><Link href={`/c/${c.id}`} className="back">← 문제 목록</Link><ApiLoading label="문제 불러오는 중…" /></div>;
  if (loadErr) return <div className="wrap"><Link href={`/c/${c.id}`} className="back">← 문제 목록</Link><ApiErrorCard msg={loadErr} /></div>;

  const statement = problem?.statement_md ?? "";
  const hasMissions = isStep && missions.length > 0;          // can submit an output
  // pick THIS problem's in-browser simulator by its simulator_key. None registered →
  // no sim tab; the user submits output/code directly (the server is always the judge).
  const sim = getSimulator(problem?.simulator_key);
  const SimStep = sim?.Step, SimCh = sim?.Challenge;
  const simReady = isStep ? (hasMissions && simInputOk && !!SimStep) : !!SimCh;
  const rightTabs: [RightTab, string][] = isStep ? [["submit", "제출"], ["history", "제출 내역"]] : [["submit", "제출"], ["history", "제출 내역"], ["eval", "중간 평가"]];
  const curSeed = missions[mission]?.seed;
  const curBudget = missions[mission]?.budget ?? 0;

  return <div className="solve">
    <div className="pane left">
      <div className="pane-head">
        <div className="tabs">
          <button className={"tab" + (leftTab === "statement" ? " active" : "")} onClick={() => setLeftTab("statement")}>문제</button>
          <button className={"tab" + (leftTab === "sim" ? " active" : "")} disabled={!simReady} style={!simReady ? { opacity: .45, cursor: "not-allowed" } : undefined} onClick={() => { if (simReady) setLeftTab("sim"); }}>시뮬레이터</button>
        </div>
        <Link href={`/c/${c.id}`} className="btn ghost" style={{ padding: "6px 12px" }}>← 문제 목록</Link>
      </div>
      <div className="pane-body">
        {leftTab === "sim"
          ? (isStep
              ? (simReady && SimStep ? <SimStep mission={mission} setMission={setMission} onOutput={setStepOutput} missions={missions} initial={stepOutput} /> : <CustomGenNotice />)
              : (SimCh ? <SimCh /> : <CustomGenNotice />))
          : <><LimitsBar p={problem} />{isStep ? (statement ? <MarkdownView md={statement} /> : <CustomGenNotice />) : <ChallengeStatement md={statement} />}<ExampleIO p={problem} /></>}
      </div>
    </div>
    <div className="pane right">
      <div className="pane-head"><div className="tabs">{rightTabs.map(([k, l]) => <button key={k} className={"tab" + (rightTab === k ? " active" : "")} onClick={() => setRightTab(k)}>{l}</button>)}</div></div>
      <div className="pane-body">
        {rightTab === "submit" && isStep && (hasMissions ? <>
          <RuleBanner />
          {!live && <NotLiveNotice status={contestStatus} />}
          <p className="muted" style={{ fontSize: 12, margin: "12px 0 8px", lineHeight: 1.6 }}>
            스텝 업은 <b style={{ color: "var(--fg)" }}>코드가 아니라 '출력'</b>을 제출합니다. 미션별로 따로 채점되며,
            왼쪽 시뮬레이터로 만든 출력은 아래 칸에 <b style={{ color: "var(--fg)" }}>자동 반영</b>돼요.
          </p>
          {!simInputOk && <div className="card" style={{ marginBottom: 10, borderColor: "var(--line2)" }}><span className="muted">⚠ 시뮬레이터 입력을 불러오지 못했어요 — 출력을 직접 붙여넣어 제출하세요.</span></div>}
          <div className="k muted" style={{ fontSize: 11, marginBottom: 4 }}>미션 {missions[mission]?.mission ?? 1} 출력</div>
          <textarea rows={12} value={stepOutput} onChange={(e) => setStepOutput(e.target.value)} placeholder="여기에 출력(이동 문자열)을 붙여넣거나, 왼쪽 시뮬레이터로 만드세요" />
          <div className="submitbar" style={{ flexDirection: "column", alignItems: "stretch", gap: 10, paddingLeft: 0, paddingRight: 0, borderTop: "none" }}>
            <div className="row" style={{ gap: 10 }}><span className="muted" style={{ fontSize: 12, whiteSpace: "nowrap" }}>제출 대상</span><MissionSelect mission={mission} setMission={setMission} missions={missions} /><span className="muted" style={{ fontSize: 12 }}>미션별 개별 제출</span></div>
            <div className="row" style={{ gap: 10 }}>
              <a className="btn ghost" style={{ flex: 1, textAlign: "center" }} href={pid && curSeed != null ? `/api/problems/${pid}/missions/${curSeed}/input` : undefined} target="_blank" rel="noreferrer">입력 다운로드</a>
              <button className="btn" style={{ flex: 1 }} disabled={!live || busy} onClick={submitStep}>{busy ? "채점 중…" : "⚲ 제출"}</button>
            </div>
          </div>
        </> : <CustomGenNotice />)}

        {rightTab === "submit" && !isStep && <>
          <RuleBanner />
          {!live && <NotLiveNotice status={contestStatus} />}
          <div className="row" style={{ gap: 10, margin: "12px 0 8px", flexWrap: "wrap" }}>
            <span className="muted" style={{ fontSize: 12 }}>언어</span>
            <select className="dd" value={lang} onChange={(e) => {
              const next = e.target.value;
              const pristine = code.trim() === "" || code === LANG_BY_ID[lang].starter;
              setLang(next);
              if (pristine) setCode(LANG_BY_ID[next].starter);
            }}>
              {LANGS.map((l) => <option key={l.id} value={l.id} disabled={!l.enabled}>{l.label}{l.enabled ? "" : " (준비 중)"}</option>)}
            </select>
            <button className="btn ghost" style={{ padding: "6px 10px" }} onClick={() => setCode(LANG_BY_ID[lang].starter)}>스타터 코드 불러오기</button>
          </div>
          <textarea className="editor" value={code} onChange={(e) => setCode(e.target.value)} spellCheck={false} />
          <div className="row" style={{ justifyContent: "space-between", margin: "6px 2px 0", fontSize: 12 }}>
            <span className="muted">{LANG_BY_ID[lang].filename}</span>
            <span className={srcOver ? "bad" : "muted"}>소스 {fmtBytes(codeBytes)} / 1&nbsp;MB{srcOver ? " · 초과!" : ""}</span>
          </div>
          <label className="muted" style={{ fontSize: 12, display: "block", margin: "14px 0 6px", lineHeight: 1.6 }}>
            추가 데이터 <b style={{ color: "var(--fg)" }}>data.bin</b> (선택 · 최대 10&nbsp;MB) — 미리 계산한 데이터를 동봉하면
            프로그램이 실행 폴더의 <code>data.bin</code> 파일로 읽을 수 있어요(파일 입출력).
          </label>
          {dataFile ? <div className="filechip">
            <span>📦 <b>data.bin</b> · {fmtBytes(dataFile.size)}</span>
            {dataOver && <span className="bad" style={{ marginLeft: 8 }}>10MB 초과!</span>}
            <span className="x" onClick={() => setDataFile(null)}>✕ 제거</span>
          </div> : <label className="btn ghost" style={{ padding: "8px 12px", fontSize: 13, cursor: "pointer", display: "inline-block" }}>📎 data.bin 첨부
            <input type="file" style={{ display: "none" }} onChange={(e) => { const f = e.target.files?.[0]; if (f) setDataFile(f); e.currentTarget.value = ""; }} /></label>}
          <div className="submitbar">
            <span className="muted" style={{ fontSize: 12 }}>2초 / 1024MB (언어 공통)</span>
            <div style={{ flex: 1 }} />
            {(srcOver || dataOver) && <span className="bad" style={{ fontSize: 12 }}>{srcOver ? "소스 1MB 초과" : "data.bin 10MB 초과"}</span>}
            <button className="btn" disabled={srcOver || dataOver || !live || busy} onClick={submitCh}>{busy ? "제출 중…" : "⚲ 제출"}</button>
          </div>
        </>}

        {rightTab === "history" && isStep && <ApiStepHistory hist={stepHist} mission={mission} setMission={setMission} missions={missions} seed={curSeed} budget={curBudget} />}
        {rightTab === "history" && !isStep && <ApiChHistory hist={chHist} />}
        {rightTab === "eval" && <>
          <div className="row" style={{ justifyContent: "flex-end", marginBottom: 8 }}>
            <button className="btn ghost" style={{ padding: "4px 12px", fontSize: 12 }} onClick={loadEval}>↻ 새로고침</button>
          </div>
          {isAdmin && <div className="card" style={{ marginBottom: 10, borderColor: "var(--line2)" }}>
            <div className="row" style={{ justifyContent: "space-between", alignItems: "center", gap: 10, flexWrap: "wrap" }}>
              <span className="muted" style={{ fontSize: 12 }}>관리자: <b>지금 평가 라운드</b>를 만들어 즉시 채점(테스트). 실제 채점은 GitHub Actions <b>evals</b>가 수행 → 끝나면 ↻ 새로고침.</span>
              <button className="btn ghost" style={{ padding: "5px 12px", fontSize: 12, whiteSpace: "nowrap" }} disabled={busy} onClick={runEvalNow}>지금 평가 실행</button>
            </div>
          </div>}
          <ApiEval data={myEval} err={evalErr} />
        </>}
      </div>
    </div>
  </div>;
}

function ApiStepHistory({ hist, mission, setMission, missions, seed, budget }:
  { hist: StepupSubmission[] | null; mission: number; setMission: (i: number) => void; missions: ApiMissionFull[]; seed: number | undefined; budget: number }) {
  if (hist === null) return <ApiLoading label="제출 내역 불러오는 중…" />;
  const mNo = missions[mission]?.mission ?? 1;
  const rows = hist.filter((h) => h.mission_seed === seed);
  return <>
    <div className="row" style={{ justifyContent: "space-between", marginBottom: 8 }}><h3 style={{ margin: 0 }}>미션 {mNo} 제출 내역</h3><MissionSelect mission={mission} setMission={setMission} missions={missions} /></div>
    <p className="muted" style={{ margin: "0 0 8px", fontSize: 12 }}>스텝 업은 <b>미션마다 따로</b> 제출·채점됩니다. 점수는 즉시 확정됩니다.</p>
    <table><tbody><tr><th>제출 시각</th><th>비용</th><th>결과</th></tr>
      {rows.length ? rows.map((h) => <tr key={h.id}><td className="muted">{fmtAt(h.created_at)}</td><td>{h.valid ? <b>{h.cost}</b> : <span className="muted">무효</span>}</td><td style={{ minWidth: 90 }}><Bar g={h.score} t={budget || 1} sm /></td></tr>)
        : <tr><td colSpan={3} className="muted" style={{ padding: 16, textAlign: "center" }}>이 미션엔 아직 제출이 없어요.</td></tr>}
    </tbody></table>
  </>;
}

const CH_STATE_KO: Record<string, string> = {
  queued: "대기 중", compiling: "컴파일 중", running: "실행 중", sample_running: "샘플 채점 중",
  sample_done: "샘플 완료", done: "완료", compile_error: "컴파일 오류", errored: "오류",
};
function ApiChHistory({ hist }: { hist: ChallengeSubmission[] | null }) {
  if (hist === null) return <ApiLoading label="제출 내역 불러오는 중…" />;
  return <>
    <h2 style={{ margin: "4px 0 8px" }}>최종 제출 목록</h2>
    <p className="muted" style={{ margin: "0 0 8px", fontSize: 12 }}>가장 <b>최근 제출</b>이 다음 중간 평가에 사용됩니다. 점수(상대 등수)는 평가 때 산정돼요(그 전 0점).</p>
    <table><tbody><tr><th>제출 시각</th><th>언어</th><th>상태</th><th>샘플 비용 합</th></tr>
      {hist.length ? hist.map((h, i) => <tr key={h.id} className={i === 0 ? "sel" : ""}>
        <td className="muted">{fmtAt(h.created_at)}{i === 0 && <span className="pill" style={{ marginLeft: 6 }}>대표</span>}</td>
        <td>{h.language_id}</td>
        <td>{h.state === "done" || h.state === "sample_done" ? <span className="ok">{CH_STATE_KO[h.state] ?? h.state}</span> : (h.state === "errored" || h.state === "compile_error") ? <span className="bad">{CH_STATE_KO[h.state] ?? h.state}</span> : <span className="muted">{CH_STATE_KO[h.state] ?? h.state}</span>}</td>
        <td>{h.sample_score_sum != null ? <b>{fmt(h.sample_score_sum)}</b> : <span className="muted">—</span>}</td></tr>)
        : <tr><td colSpan={4} className="muted" style={{ padding: 16, textAlign: "center" }}>아직 제출이 없어요.</td></tr>}
    </tbody></table>
  </>;
}

const VERDICT_KO: Record<string, string> = {
  ok: "정상", tle: "시간 초과", mle: "메모리 초과", re: "런타임 오류", compile_error: "컴파일 오류", illegal: "무효", internal: "내부 오류",
};
function ApiEval({ data, err }: { data: MyEval | null; err: string }) {
  if (err) return <ApiErrorCard msg={err} />;
  if (data === null) return <ApiLoading label="중간 평가 불러오는 중…" />;
  if (!data.round) return <div className="card"><b>아직 중간 평가가 없어요</b><p className="muted" style={{ margin: "8px 0 0" }}>매일 09:00·18:00(KST)에 비공개 데이터로 평가합니다. 결과가 공개되면 여기에 본인 점수·등수가 표시됩니다.</p></div>;
  const r = data.round, s = data.standing;
  return <>
    <h2 style={{ margin: "4px 0 2px" }}>{r.type === "final" ? "최종 평가" : "중간 평가"} 결과</h2>
    <p className="muted" style={{ margin: "0 0 12px" }}>{fmtAt(r.published_at)} 공개 · 채점 시각 {fmtAt(r.scheduled_at)}</p>
    {s && <div className="card" style={{ marginBottom: 14 }}><div className="row" style={{ gap: 28, flexWrap: "wrap" }}>
      <div><div className="k muted" style={{ fontSize: 11 }}>🌸 챌린지 (퍼포먼스 점수)</div><div style={{ fontSize: 20, fontWeight: 700 }}>{fmt(s.challenge_score)}</div></div>
      <div><div className="k muted" style={{ fontSize: 11 }}>총점</div><div style={{ fontSize: 20, fontWeight: 700 }}>{fmt(s.total_score)}</div></div>
      <div><div className="k muted" style={{ fontSize: 11 }}>스텝업</div><div style={{ fontSize: 16, fontWeight: 700 }}>{fmt(s.stepup_score)}</div></div>
    </div><p className="muted" style={{ margin: "8px 0 0", fontSize: 11 }}>※ 등수(상대 순위)는 대회 종료 후에만 공개됩니다 — 진행 중에는 본인 점수만 표시.</p></div>}
    <h3 style={{ margin: "0 0 8px" }}>테스트케이스별 점수</h3>
    {data.cases.length ? <div className="tscroll"><table><tbody><tr><th>시드</th><th>결과</th><th>비용</th><th>내 점수</th><th>실행시간</th></tr>
      {data.cases.map((cs, j) => <tr key={j}>
        <td className="muted">{cs.seed}</td>
        <td>{cs.verdict === "ok" ? <span className="ok">정상</span> : <span className="bad">{VERDICT_KO[cs.verdict] ?? cs.verdict}</span>}</td>
        <td>{cs.raw_cost != null ? fmt(Math.round(cs.raw_cost)) : "—"}</td>
        <td style={{ minWidth: 90 }}>{cs.case_score != null ? <Bar g={cs.case_score} t={1000000} sm /> : <span className="muted">—</span>}</td>
        <td className="muted">{cs.runtime_ms != null ? `${cs.runtime_ms} ms` : "—"}</td></tr>)}
    </tbody></table></div> : <p className="muted">이 평가에 대한 케이스 결과가 없습니다.</p>}
  </>;
}

const GEN_CODE_DEFAULT = `import random
def generate(seed):
    rng = random.Random(seed)
    # TODO: seed -> 입력 문자열을 결정적으로 생성
    return ""`;
const CHECK_CODE_DEFAULT = `def check(input_text, output_text):
    # TODO: (cost, valid, message) 반환 — 최소화(낮을수록 좋음). 무효면 cost=None.
    return (None, False, "TODO")`;

// Fallback feature schema (used before /api/admin/templates loads); the API is the
// source of truth. Step Up cases set EXACT values for these per case.
const CLEAN_FEATURES = [
  { key: "h", label: "행 N", min: 2, max: 50, default: 8 },
  { key: "w", label: "열 M", min: 2, max: 50, default: 8 },
  { key: "dust", label: "먼지 수", min: 1, max: 2400, default: 8 },
];
type StepCase = { seed: number; score: number; features: Record<string, number> };

export function CreateContestView() {
  const { apiMode, isAdmin, addContest, showToast } = useStore();
  const router = useRouter();
  const [creating, setCreating] = useState(false);
  const [title, setTitle] = useState("");
  const [desc, setDesc] = useState("배찌와 다오의 대청소");
  const [statement, setStatement] = useState(STATEMENT_HTML);
  const [chStatement, setChStatement] = useState("## 배찌와 다오의 대청소 — 챌린지\n\n무작위 생성된 격자에 대해 **모든 먼지를 청소하는 이동 문자열**을 출력하세요. **이동 수(=비용)가 낮을수록** 좋습니다.\n\n(채점은 참가자 상대 등수 — 점수 규칙은 문제 화면에 자동 표시되므로 지문에 적지 않아도 됩니다.)");
  const [timeMs, setTimeMs] = useState(2000);
  const [memMb, setMemMb] = useState(1024);
  const [genType, setGenType] = useState<"param" | "code">("param");
  const [gen, setGen] = useState<GenParams>(DEFAULT_GEN);
  const [templates, setTemplates] = useState<ProblemTemplate[] | null>(null);
  const [problemKey, setProblemKey] = useState("clean_robot");
  const [startNow, setStartNow] = useState(false);
  const [draftMode, setDraftMode] = useState(false);   // tester-only private draft
  const [missions, setMissions] = useState<StepCase[]>([]);   // Step Up: per-case features + scores
  const [useSubtasks, setUseSubtasks] = useState(false);      // Challenge: condition-based subtasks
  const [chSubtasks, setChSubtasks] = useState<{ name: string; features: Record<string, number[]>; seedLo: number; seedHi: number; budget: number }[]>([]);
  const [genLang, setGenLang] = useState("python3");
  const [genCode, setGenCode] = useState(GEN_CODE_DEFAULT);
  const [checkCode, setCheckCode] = useState(CHECK_CODE_DEFAULT);
  const [seedsText, setSeedsText] = useState("101, 102, 103");
  const [seedLo, setSeedLo] = useState(1000);
  const [seedHi, setSeedHi] = useState(2000);
  const [roundSeeds, setRoundSeeds] = useState(6);
  const [costEps, setCostEps] = useState(0);
  const [preview, setPreview] = useState(false);

  // load the installed problem templates so the admin can build on ANY of them.
  useEffect(() => {
    if (!apiMode || !isAdmin) return;
    let on = true;
    getTemplates().then((t) => {
      if (!on) return;
      setTemplates(t);
      // keep the selection valid even if the default key isn't installed (future problems).
      if (t.length && !t.some((x) => x.problem_key === problemKey)) setProblemKey(t[0].problem_key);
    }).catch(() => { if (on) setTemplates([]); });
    return () => { on = false; };
  }, [apiMode, isAdmin]);
  const curTemplate = templates?.find((t) => t.problem_key === problemKey) ?? null;
  // the selected problem's authorable features (from META; fallback before templates load).
  const featSchema = (curTemplate?.feature_schema?.length ? curTemplate.feature_schema
    : (problemKey === "clean_robot" ? CLEAN_FEATURES : []));
  const schemaKeys = featSchema.map((f) => f.key).join(",");
  // (re)seed the Step Up case table when the feature schema changes (e.g. problem switch).
  useEffect(() => {
    if (!apiMode || !schemaKeys) { if (!apiMode) return; setMissions([]); return; }
    const keys = schemaKeys.split(",");
    const defFeats = Object.fromEntries(featSchema.map((f) => [f.key, f.default]));
    const n = 3, base = Math.floor(1_000_000 / n);
    setMissions((prev) => {
      if (prev.length && Object.keys(prev[0].features).join(",") === keys.join(",")) return prev;
      return Array.from({ length: n }, (_, i) => ({
        seed: 101 + i, score: i === 0 ? 1_000_000 - base * (n - 1) : base, features: { ...defFeats },
      }));
    });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [apiMode, schemaKeys]);
  // seed one default Challenge subtask the first time the toggle is turned on.
  useEffect(() => {
    if (useSubtasks && chSubtasks.length === 0) {
      const defFeats = Object.fromEntries(featSchema.map((f) => [f.key, [f.default, f.default]]));
      setChSubtasks([{ name: "조건 1", features: defFeats, seedLo, seedHi, budget: 1_000_000 }]);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [useSubtasks]);

  const rawSeedCount = seedsText.split(/[\s,]+/).filter((t) => t.trim().length).length;
  const seeds = Array.from(new Set(seedsText.split(/[\s,]+/).map(Number).filter((n) => Number.isFinite(n) && n >= 0)));
  const dropped = rawSeedCount - seeds.length;
  const genOk = gen.hMin <= gen.hMax && gen.wMin <= gen.wMax && gen.dMin <= gen.dMax && gen.hMin >= 2 && gen.wMin >= 2 && gen.dMin >= 0;
  const codeHasTodo = genType === "code" && (genCode.includes("TODO") || checkCode.includes("TODO"));
  // Real-API Step Up uses the per-case table (exact features + author scores, sum==1e6);
  // mock mode keeps the legacy ranges+seeds path.
  const useMissions = apiMode && genType === "param";
  const scoreSum = missions.reduce((a, m) => a + (Number(m.score) || 0), 0);
  const seedDup = new Set(missions.map((m) => m.seed)).size !== missions.length;
  const featOk = missions.every((m) => featSchema.every((f) => { const v = m.features[f.key]; return Number.isFinite(v) && v >= f.min && v <= f.max; }));
  const missionsOk = missions.length >= 1 && scoreSum === 1_000_000 && featOk && !seedDup;
  // Challenge: optional condition-based subtasks (each its own field + budget; budgets sum to 1e6).
  const useSubs = apiMode && useSubtasks;
  const chBudgetSum = chSubtasks.reduce((a, s) => a + (Number(s.budget) || 0), 0);
  const chFeatOk = chSubtasks.every((s) => featSchema.every((f) => { const r = s.features[f.key]; return Array.isArray(r) && r.length === 2 && r[0] >= f.min && r[0] <= r[1] && r[1] <= f.max; }));
  const chSeedOk = chSubtasks.every((s) => s.seedLo >= 0 && s.seedLo <= s.seedHi && s.seedHi <= 10_000_000);
  const chSubsOk = chSubtasks.length >= 1 && chBudgetSum === 1_000_000 && chFeatOk && chSeedOk;
  const stepOk = genType === "code" ? (genCode.trim().length > 0 && !codeHasTodo) : (useMissions ? missionsOk : genOk);
  const valid = title.trim().length > 0 && (useMissions || seeds.length >= 1) && stepOk && seedLo <= seedHi && (useSubs ? chSubsOk : roundSeeds >= 1);
  // first failing reason, so the disabled-button hint is specific (not a catch-all)
  const reason = !title.trim() ? "대회 제목을 입력하세요."
    : useMissions && missions.length < 1 ? "스텝업 케이스를 1개 이상 추가하세요."
    : useMissions && seedDup ? "스텝업 케이스 시드가 중복됩니다."
    : useMissions && !featOk ? "케이스 피처 값이 허용 범위를 벗어났습니다."
    : useMissions && scoreSum !== 1_000_000 ? `케이스 점수 합이 1,000,000이어야 합니다 (현재 ${scoreSum.toLocaleString()}).`
    : !useMissions && seeds.length < 1 ? "미션 시드를 1개 이상 입력하세요."
    : !useMissions && genType === "param" && !genOk ? "격자/먼지 범위를 확인하세요 (H·W ≥ 2, 최소 ≤ 최대)."
    : genType === "code" && codeHasTodo ? "생성기/체커 코드의 TODO를 채우세요."
    : genType === "code" && !genCode.trim() ? "생성기 코드를 입력하세요."
    : seedLo > seedHi ? "챌린지 시드 범위가 거꾸로입니다 (시작 ≤ 끝)."
    : useSubs && chBudgetSum !== 1_000_000 ? `챌린지 서브태스크 배점 합이 1,000,000이어야 합니다 (현재 ${chBudgetSum.toLocaleString()}).`
    : useSubs && !chFeatOk ? "서브태스크 피처 값이 허용 범위를 벗어났습니다."
    : useSubs && !chSeedOk ? "서브태스크 시드 범위를 확인하세요 (0 ≤ 시작 ≤ 끝)."
    : !useSubs && roundSeeds < 1 ? "평가 라운드당 케이스 수는 1 이상이어야 합니다."
    : "";

  function attachImage(file: File, set: (fn: (s: string) => string) => void) {
    const reader = new FileReader();
    if (file.size > 2_000_000) { showToast("이미지가 너무 큽니다", "2MB 이하 이미지를 사용하세요"); return; }
    reader.onload = () => { const url = reader.result as string; set((s) => s + `\n\n![${file.name}](${url})\n`); showToast("이미지를 지문에 추가했습니다", "게시 후 문제 화면에서 렌더됩니다"); };
    reader.readAsDataURL(file);
  }

  async function create() {
    // real-API mode: persist via the admin endpoint (built-in clean-robot template;
    // custom generators run admin code and need the sandboxed grader — not yet wired).
    if (apiMode) {
      if (creating) return;
      // custom-CODE generators run admin code -> need the sandboxed grader (not yet wired).
      if (genType === "code") { showToast("커스텀 생성기 코드는 준비 중입니다", "지금은 '청소로봇 파라미터' 생성기로 만들어 주세요"); return; }
      setCreating(true);
      try {
        const r = await createContest({
          title: title.trim(), problem_key: problemKey, start_now: startNow, draft: draftMode,
          gen_params: { hMin: gen.hMin, hMax: gen.hMax, wMin: gen.wMin, wMax: gen.wMax, dMin: gen.dMin, dMax: gen.dMax },
          stepup: useMissions
            ? { statement_md: statement, missions, time_limit_ms: timeMs, memory_limit_mb: memMb }
            : { statement_md: statement, given_seeds: seeds, time_limit_ms: timeMs, memory_limit_mb: memMb },
          challenge: useSubs
            ? { statement_md: chStatement, seed_range: [seedLo, seedHi], round_seeds: roundSeeds, cost_eps: costEps, time_limit_ms: timeMs, memory_limit_mb: memMb,
                subtasks: chSubtasks.map((s) => ({ name: s.name, features: s.features, seed_lo: s.seedLo, seed_hi: s.seedHi, budget: s.budget })) }
            : { statement_md: chStatement, seed_range: [seedLo, seedHi], round_seeds: roundSeeds, cost_eps: costEps, time_limit_ms: timeMs, memory_limit_mb: memMb },
        });
        showToast(`'${title.trim()}' 모의고사를 만들었습니다`, r.status === "draft" ? "테스터 전용 초안 — 테스터/관리자만 볼 수 있어요 (검증 후 공개)" : r.status === "live" ? "지금 바로 진행 중(테스트) — 제출 가능" : `시작 ${r.starts_at.slice(0, 10)} · 종료 ${r.ends_at.slice(0, 10)} (예약됨)`);
        router.push(`/c/${r.id}`);
      } catch (e: any) {
        showToast("대회 생성 실패", String(e?.message ?? e));
      } finally { setCreating(false); }
      return;
    }
    // mock mode: keep it client-side (localStorage), playable immediately.
    const base = { timeMs, memMb, gen, genLang } as const;
    const stepup: ProblemDef = genType === "code"
      ? { kind: "stepup", title: `${desc} — 스텝 업`, statement, ...base, genType: "code", genCode, checkCode, missions: seeds, budget: 1000000 }
      : { kind: "stepup", title: `${desc} — 스텝 업`, statement, ...base, genType: "param", missions: seeds, budget: 1000000 };
    const challenge: ProblemDef = { kind: "challenge", title: `${desc} — 챌린지`, statement: chStatement, ...base, genType: "param", seedRange: [seedLo, seedHi], roundSeeds, costEps, budget: 1000000 };
    const id = "c_" + Math.random().toString(36).slice(2, 7);
    addContest({ id, title: title.trim(), status: "live", when: "방금 등록 (데모: 즉시 진행)", desc, total: 1000000, gotten: 0, nextEval: "—", problems: [stepup, challenge] });
    showToast(`'${title.trim()}' 모의고사를 만들었습니다`, "지문·생성기 설정이 적용되었습니다");
    router.push(`/c/${id}`);
  }

  const inputStyle = { width: "100%", background: "#0f1117", color: "var(--fg)", border: "1px solid var(--line)", borderRadius: 8, padding: 10 } as const;
  const numCss = { background: "#0f1117", color: "var(--fg)", border: "1px solid var(--line)", borderRadius: 6, padding: "6px 8px" } as const;
  const num = (v: number, set: (n: number) => void, w = 60) => <input type="number" value={v} onChange={(e) => set(parseInt(e.target.value || "0", 10) || 0)} style={{ width: w, ...numCss }} />;
  const numF = (v: number, set: (n: number) => void, w = 60) => <input type="number" step="any" value={v} onChange={(e) => set(parseFloat(e.target.value || "0") || 0)} style={{ width: w, ...numCss }} />;
  const field = (label: ReactNode, node: ReactNode) => <div style={{ marginBottom: 14 }}><label className="muted" style={{ fontSize: 12, display: "block", marginBottom: 5 }}>{label}</label>{node}</div>;
  // Step Up case-table helpers (real-API authoring: exact features + per-case scores).
  const setCase = (i: number, patch: Partial<StepCase>) => setMissions((ms) => ms.map((m, j) => (j === i ? { ...m, ...patch } : m)));
  const setFeat = (i: number, key: string, v: number) => setMissions((ms) => ms.map((m, j) => (j === i ? { ...m, features: { ...m.features, [key]: v } } : m)));
  const addCase = () => setMissions((ms) => {
    const ns = ms.length ? Math.max(...ms.map((m) => m.seed)) + 1 : 101;
    return [...ms, { seed: ns, score: 0, features: Object.fromEntries(featSchema.map((f) => [f.key, f.default])) }];
  });
  const distributeScores = () => setMissions((ms) => {
    const n = ms.length || 1, base = Math.floor(1_000_000 / n);
    return ms.map((m, i) => ({ ...m, score: i === 0 ? 1_000_000 - base * (n - 1) : base }));
  });
  const hasCleanGrid = featSchema.some((f) => f.key === "h") && featSchema.some((f) => f.key === "w");
  const featuresToGen = (f: Record<string, number>): GenParams => ({ hMin: f.h ?? 8, hMax: f.h ?? 8, wMin: f.w ?? 8, wMax: f.w ?? 8, dMin: f.dust ?? 0, dMax: f.dust ?? 0 });
  // Challenge subtask-table helpers.
  const setSub = (i: number, patch: Partial<{ name: string; seedLo: number; seedHi: number; budget: number }>) => setChSubtasks((s) => s.map((x, j) => (j === i ? { ...x, ...patch } : x)));
  const setSubRange = (i: number, key: string, idx: 0 | 1, v: number) => setChSubtasks((s) => s.map((x, j) => {
    if (j !== i) return x;
    const cur = (x.features[key] ?? [0, 0]).slice(); cur[idx] = v;
    return { ...x, features: { ...x.features, [key]: cur } };
  }));
  const addSub = () => setChSubtasks((s) => [...s, { name: `조건 ${s.length + 1}`, features: Object.fromEntries(featSchema.map((f) => [f.key, [f.default, f.default]])), seedLo, seedHi, budget: 0 }]);
  const distSub = () => setChSubtasks((s) => { const n = s.length || 1, base = Math.floor(1_000_000 / n); return s.map((x, i) => ({ ...x, budget: i === 0 ? 1_000_000 - base * (n - 1) : base })); });
  const imgBtn = (set: (fn: (s: string) => string) => void) => (
    <label className="btn ghost" style={{ padding: "5px 10px", fontSize: 12, cursor: "pointer", display: "inline-block" }}>🖼 이미지 첨부
      <input type="file" accept="image/*" style={{ display: "none" }} onChange={(e) => { const f = e.target.files?.[0]; if (f) attachImage(f, set); e.currentTarget.value = ""; }} /></label>
  );

  if (apiMode && !isAdmin) {
    return <div className="wrap" style={{ maxWidth: 560, marginTop: 40, textAlign: "center" }}>
      <h1>관리자 전용</h1>
      <p className="muted" style={{ lineHeight: 1.7, marginBottom: 22 }}>모의고사 생성은 관리자 계정만 할 수 있어요.</p>
      <Link href="/" className="btn ghost">← 모의고사 목록</Link>
    </div>;
  }
  return <div className="wrap" style={{ maxWidth: 760 }}>
    <Link href="/" className="back">← 모의고사 목록</Link>
    <h1 style={{ marginTop: 12 }}>모의고사 생성 <span className="muted" style={{ fontSize: 14, fontWeight: 400 }}>(관리자)</span></h1>
    <p className="muted" style={{ marginTop: 0, lineHeight: 1.7 }}>실제 운영 시 등록하면 <b>다음날 09:00 시작 · 3일 뒤 09:00 종료</b>(KST)로 일정이 잡힙니다.
      (이 미리보기에서는 <b>즉시 진행</b> 데모로 생성됩니다.) 지문은 <b>마크다운 + LaTeX(<code>$..$</code>) + 이미지</b> 지원.</p>
    {apiMode && <div className="card" style={{ marginBottom: 16, borderColor: "var(--line2)" }}>
      <b>실 API 모드</b>
      <p className="muted" style={{ margin: "6px 0 10px", lineHeight: 1.6 }}>서버에 <b>예약 대회</b>로 저장됩니다(일정 규칙 적용).
        선택한 <b>문제 템플릿</b>의 생성기/체커로 채점되고, 템플릿이 선언한 <b>시뮬레이터</b>가 자동 연결됩니다.
        <b>커스텀 생성기 코드</b>는 샌드박스 채점이 필요해 아직 준비 중입니다.</p>
      {field("문제 템플릿",
        <select className="dd" value={problemKey} onChange={(e) => setProblemKey(e.target.value)} style={{ width: "100%" }}>
          {(templates ?? [{ problem_key: "clean_robot", title: "청소 로봇 (파라미터)", kind: null, simulator_key: "clean", parametric: true, feature_schema: CLEAN_FEATURES }]).map((t) =>
            <option key={t.problem_key} value={t.problem_key}>
              {t.title} — {t.problem_key}{t.parametric ? " · 파라미터" : " · 고정범위"}{t.simulator_key ? "" : " · 시뮬레이터 없음"}
            </option>)}
        </select>)}
      {curTemplate && !curTemplate.parametric && <p className="muted" style={{ margin: "-8px 0 0", fontSize: 12 }}>※ 이 템플릿은 <b>고정 범위</b>라 아래 격자/먼지 파라미터는 무시됩니다.</p>}
      {curTemplate && !curTemplate.simulator_key && <p className="muted" style={{ margin: "-8px 0 0", fontSize: 12 }}>※ 이 템플릿은 브라우저 시뮬레이터가 없어 제출만 가능합니다(서버가 채점).</p>}
      <label className="row" style={{ gap: 8, alignItems: "center", marginTop: 12, cursor: "pointer", fontSize: 13 }}>
        <input type="checkbox" checked={startNow} onChange={(e) => setStartNow(e.target.checked)} />
        <span><b>지금 시작 (테스트용)</b> — 일정 규칙을 건너뛰고 <b>즉시 진행(live)</b>으로 만들어 바로 제출·채점을 확인합니다.</span>
      </label>
      <label className="row" style={{ gap: 8, alignItems: "center", marginTop: 6, cursor: "pointer", fontSize: 13 }}>
        <input type="checkbox" checked={draftMode} onChange={(e) => setDraftMode(e.target.checked)} />
        <span><b>테스터 전용 (초안)</b> — <b>테스터/관리자에게만</b> 보이는 비공개 초안으로 저장. 진행 중인 대회 중에 새 문제를 몰래 테스트할 때 사용하고, 검증 후 "공개로 전환". (지금 시작보다 우선)</span>
      </label>
    </div>}
    <div className="card" style={{ marginBottom: 16 }}>
      {field("대회 제목", <input value={title} onChange={(e) => setTitle(e.target.value)} placeholder="예: 7월 모의고사 #5" style={inputStyle} />)}
      {field("문제 이름", <input value={desc} onChange={(e) => setDesc(e.target.value)} style={inputStyle} />)}
      <div className="row" style={{ gap: 20 }}>{field("시간 제한 (ms)", num(timeMs, setTimeMs, 90))}{field("메모리 (MB)", num(memMb, setMemMb, 90))}</div>
      <p className="muted" style={{ margin: 0, fontSize: 12 }}>※ 제한은 12개 언어 공통으로 적용됩니다.</p>
    </div>

    <h3 style={{ margin: "4px 0 10px" }}>① 스텝 업 (출력 제출 · 만점 1,000,000 · 총점 반영 20%)</h3>
    <div className="card" style={{ marginBottom: 16 }}>
      {field(<>지문 (마크다운/LaTeX) {imgBtn(setStatement)}</>, <textarea rows={6} value={statement} onChange={(e) => setStatement(e.target.value)} />)}
      {field("데이터 생성기 방식", <div className="tabs">
        <button className={"tab" + (genType === "param" ? " active" : "")} onClick={() => setGenType("param")}>청소 로봇 전용 (파라미터)</button>
        <button className={"tab" + (genType === "code" ? " active" : "")} onClick={() => setGenType("code")}>커스텀 코드</button>
      </div>)}
      {genType === "param" && <p className="muted" style={{ margin: "-8px 0 12px", fontSize: 12 }}>※ 파라미터 방식은 청소 로봇 문제 전용입니다. 다른 문제는 <b>커스텀 코드</b>를 사용하세요.</p>}
      {useMissions ? <>
        {genType === "param" && <p className="muted" style={{ margin: "-8px 0 12px", fontSize: 12 }}>※ 케이스마다 <b>정확한 피처값</b>과 <b>점수</b>를 지정합니다. 점수 합은 <b>1,000,000</b>이어야 합니다. (피처는 문제 템플릿이 선언)</p>}
        {field(<>스텝업 케이스 <span className="muted">— 케이스별 피처(정확값)와 점수 직접 지정</span></>,
          <div>
            <div style={{ overflowX: "auto" }}>
              <table style={{ width: "100%", borderCollapse: "collapse", fontSize: 13 }}>
                <thead><tr style={{ textAlign: "left", color: "var(--muted)" }}>
                  <th style={{ padding: "4px 6px", fontWeight: 500 }}>#</th>
                  <th style={{ padding: "4px 6px", fontWeight: 500 }}>시드</th>
                  {featSchema.map((f) => <th key={f.key} style={{ padding: "4px 6px", fontWeight: 500 }}>{f.label} <span style={{ fontSize: 10, opacity: .7 }}>{f.min}~{f.max}</span></th>)}
                  <th style={{ padding: "4px 6px", fontWeight: 500 }}>점수</th>
                  <th></th>
                </tr></thead>
                <tbody>
                  {missions.map((m, i) => <tr key={i}>
                    <td style={{ padding: "3px 6px", color: "var(--muted)" }}>{i + 1}</td>
                    <td style={{ padding: "3px 6px" }}><input type="number" value={m.seed} onChange={(e) => setCase(i, { seed: parseInt(e.target.value || "0", 10) || 0 })} style={{ width: 72, ...numCss }} /></td>
                    {featSchema.map((f) => { const v = m.features[f.key]; const bad = !(Number.isFinite(v) && v >= f.min && v <= f.max); return <td key={f.key} style={{ padding: "3px 6px" }}><input type="number" value={Number.isFinite(v) ? v : ""} onChange={(e) => setFeat(i, f.key, parseInt(e.target.value || "0", 10) || 0)} style={{ width: 66, ...numCss, borderColor: bad ? "var(--bad)" : undefined }} /></td>; })}
                    <td style={{ padding: "3px 6px" }}><input type="number" value={m.score} onChange={(e) => setCase(i, { score: parseInt(e.target.value || "0", 10) || 0 })} style={{ width: 104, ...numCss }} /></td>
                    <td style={{ padding: "3px 6px" }}><button className="btn ghost" style={{ padding: "2px 8px", fontSize: 12 }} onClick={() => setMissions((ms) => ms.filter((_, j) => j !== i))} disabled={missions.length <= 1}>✕</button></td>
                  </tr>)}
                </tbody>
              </table>
            </div>
            <div className="row" style={{ gap: 10, marginTop: 8, flexWrap: "wrap", alignItems: "center" }}>
              <button className="btn ghost" style={{ padding: "5px 10px", fontSize: 12 }} onClick={addCase}>+ 케이스 추가</button>
              <button className="btn ghost" style={{ padding: "5px 10px", fontSize: 12 }} onClick={distributeScores}>점수 균등분배</button>
              <span style={{ fontSize: 12, color: scoreSum === 1_000_000 ? "var(--green)" : "var(--bad)" }}>점수 합 <b>{scoreSum.toLocaleString()}</b> / 1,000,000</span>
            </div>
          </div>)}
        {hasCleanGrid && <div className="row" style={{ gap: 10 }}><button className="btn ghost" disabled={!missions.length} onClick={() => setPreview((s) => !s)}>케이스 1 미리보기 (시드 {missions[0]?.seed ?? "—"})</button></div>}
        {hasCleanGrid && preview && missions[0] && <pre style={{ marginTop: 10, background: "#0f1117", border: "1px solid var(--line)", borderRadius: 8, padding: 10, fontSize: 12, overflow: "auto" }}>{genClean(missions[0].seed, featuresToGen(missions[0].features))}</pre>}
      </> : <>
        {genType === "param" ? <>
          {field("격자/먼지 범위", <div className="row" style={{ gap: 16, flexWrap: "wrap", fontSize: 13 }}>
            <span>행 H {num(gen.hMin, (n) => setGen({ ...gen, hMin: n }))} ~ {num(gen.hMax, (n) => setGen({ ...gen, hMax: n }))}</span>
            <span>열 W {num(gen.wMin, (n) => setGen({ ...gen, wMin: n }))} ~ {num(gen.wMax, (n) => setGen({ ...gen, wMax: n }))}</span>
            <span>먼지 {num(gen.dMin, (n) => setGen({ ...gen, dMin: n }))} ~ {num(gen.dMax, (n) => setGen({ ...gen, dMax: n }))}</span></div>)}
        </> : <>
          {field(<>생성기 코드 — <span className="muted">seed → 입력</span> <span className="pill">현재 python3만 지원</span></>, <textarea className="editor" style={{ minHeight: 130 }} value={genCode} onChange={(e) => setGenCode(e.target.value)} spellCheck={false} />)}
          {field(<>체커 코드 — <span className="muted">입력+출력 → (cost, valid, message)</span></>, <textarea className="editor" style={{ minHeight: 110 }} value={checkCode} onChange={(e) => setCheckCode(e.target.value)} spellCheck={false} />)}
          <p className="muted" style={{ margin: "0 0 12px", fontSize: 12 }}>커스텀 생성기/체커는 <b>서버 그레이더(샌드박스)</b>에서 실행됩니다. (브라우저 미리보기·플레이는 청소 로봇 파라미터 방식만 지원) · TODO를 모두 채워야 생성할 수 있어요.</p>
        </>}
        {field("미션 시드 (쉼표 또는 공백 구분 — 미션마다 하나)", <>
          <input value={seedsText} onChange={(e) => setSeedsText(e.target.value)} style={inputStyle} />
          <div className="muted" style={{ fontSize: 12, marginTop: 5 }}>인식된 시드 <b>{seeds.length}</b>개{dropped > 0 && <span className="bad"> · 무시된 항목 {dropped}개 (숫자가 아니거나 음수·중복)</span>}</div>
        </>)}
        {genType === "param" && <div className="row" style={{ gap: 10 }}><button className="btn ghost" disabled={!seeds.length || !genOk} onClick={() => setPreview((s) => !s)}>생성기 미리보기 (시드 {seeds[0] ?? "—"})</button><span className="muted" style={{ fontSize: 12 }}>미션 {seeds.length}개 · 미션당 배점 {seeds.length ? fmt(Math.floor(1000000 / seeds.length)) : "—"}</span></div>}
        {genType === "param" && preview && seeds.length > 0 && genOk && <pre style={{ marginTop: 10, background: "#0f1117", border: "1px solid var(--line)", borderRadius: 8, padding: 10, fontSize: 12, overflow: "auto" }}>{genClean(seeds[0], gen)}</pre>}
      </>}
    </div>

    <h3 style={{ margin: "4px 0 10px" }}>② 챌린지 (코드 제출 · 만점 1,000,000 · 총점 반영 80%)</h3>
    <div className="card" style={{ marginBottom: 16 }}>
      {field(<>지문 (마크다운/LaTeX) {imgBtn(setChStatement)}</>, <textarea rows={5} value={chStatement} onChange={(e) => setChStatement(e.target.value)} />)}
      {field("시드 범위 (무작위 케이스 생성)", <div className="row" style={{ gap: 12, fontSize: 13, flexWrap: "wrap" }}><span>{num(seedLo, setSeedLo, 90)} ~ {num(seedHi, setSeedHi, 90)}</span><span className="muted">평가마다 이 범위에서 무작위 추출 · 상대 등수 채점</span></div>)}
      {apiMode && <label className="row" style={{ gap: 8, alignItems: "center", marginBottom: 12, cursor: "pointer", fontSize: 13 }}>
        <input type="checkbox" checked={useSubtasks} onChange={(e) => setUseSubtasks(e.target.checked)} />
        <span><b>조건별 서브태스크로 분할</b> — 조건(피처 범위)별로 나눠 각각 상대 등수 채점 후 배점 합산 (배점 합 = 1,000,000)</span>
      </label>}
      {useSubs ? <>
        {field(<>부분문제(서브태스크) <span className="muted">— 피처 범위(min~max) + 시드 범위 + 배점 · 매 평가마다 각 부분문제에서 시드 1개 추출(피처는 범위 내 무작위)</span></>,
          <div>
            <div style={{ overflowX: "auto" }}>
              <table style={{ width: "100%", borderCollapse: "collapse", fontSize: 12 }}>
                <thead><tr style={{ textAlign: "left", color: "var(--muted)" }}>
                  <th style={{ padding: "4px 6px", fontWeight: 500 }}>이름</th>
                  {featSchema.map((f) => <th key={f.key} style={{ padding: "4px 6px", fontWeight: 500 }}>{f.label} <span style={{ fontSize: 10, opacity: .7 }}>{f.min}~{f.max}</span></th>)}
                  <th style={{ padding: "4px 6px", fontWeight: 500 }}>시드 범위</th>
                  <th style={{ padding: "4px 6px", fontWeight: 500 }}>배점</th>
                  <th></th>
                </tr></thead>
                <tbody>
                  {chSubtasks.map((s, i) => <tr key={i}>
                    <td style={{ padding: "3px 6px" }}><input value={s.name} onChange={(e) => setSub(i, { name: e.target.value })} style={{ width: 92, ...numCss }} /></td>
                    {featSchema.map((f) => { const r = s.features[f.key] ?? [f.default, f.default]; const bad = !(r[0] >= f.min && r[0] <= r[1] && r[1] <= f.max); return <td key={f.key} style={{ padding: "3px 6px", whiteSpace: "nowrap" }}><input type="number" value={r[0]} onChange={(e) => setSubRange(i, f.key, 0, parseInt(e.target.value || "0", 10) || 0)} style={{ width: 46, ...numCss, borderColor: bad ? "var(--bad)" : undefined }} />~<input type="number" value={r[1]} onChange={(e) => setSubRange(i, f.key, 1, parseInt(e.target.value || "0", 10) || 0)} style={{ width: 46, ...numCss, borderColor: bad ? "var(--bad)" : undefined }} /></td>; })}
                    <td style={{ padding: "3px 6px", whiteSpace: "nowrap" }}>{num(s.seedLo, (n) => setSub(i, { seedLo: n }), 70)}~{num(s.seedHi, (n) => setSub(i, { seedHi: n }), 70)}</td>
                    <td style={{ padding: "3px 6px" }}>{num(s.budget, (n) => setSub(i, { budget: n }), 96)}</td>
                    <td style={{ padding: "3px 6px" }}><button className="btn ghost" style={{ padding: "2px 8px", fontSize: 12 }} onClick={() => setChSubtasks((cs) => cs.filter((_, j) => j !== i))} disabled={chSubtasks.length <= 1}>✕</button></td>
                  </tr>)}
                </tbody>
              </table>
            </div>
            <div className="row" style={{ gap: 10, marginTop: 8, flexWrap: "wrap", alignItems: "center" }}>
              <button className="btn ghost" style={{ padding: "5px 10px", fontSize: 12 }} onClick={addSub}>+ 부분문제 추가</button>
              <button className="btn ghost" style={{ padding: "5px 10px", fontSize: 12 }} onClick={distSub}>배점 균등분배</button>
              <span style={{ fontSize: 12, color: chBudgetSum === 1_000_000 ? "var(--green)" : "var(--bad)" }}>배점 합 <b>{chBudgetSum.toLocaleString()}</b> / 1,000,000</span>
            </div>
          </div>)}
        {field(<>비용 동점 허용오차 <span className="muted">(cost_eps)</span></>, <div>{numF(costEps, setCostEps, 90)}<div className="muted" style={{ fontSize: 11, marginTop: 4 }}>비용 차가 이 값 이하면 동점. 정수 비용이면 0.</div></div>)}
      </> : <div className="row" style={{ gap: 20, flexWrap: "wrap" }}>
        {field(<>평가 라운드당 케이스 수 <span className="muted">(round_seeds)</span></>, <div>{num(roundSeeds, setRoundSeeds, 90)}<div className="muted" style={{ fontSize: 11, marginTop: 4 }}>매 평가(09·18시)마다 추출할 케이스 수 (예: 6)</div></div>)}
        {field(<>비용 동점 허용오차 <span className="muted">(cost_eps)</span></>, <div>{numF(costEps, setCostEps, 90)}<div className="muted" style={{ fontSize: 11, marginTop: 4 }}>비용 차가 이 값 이하면 동점. 정수 비용이면 0.</div></div>)}
      </div>}
      <div style={{ background: "#0f1117", border: "1px solid var(--line)", borderRadius: 8, padding: 12, fontSize: 12, lineHeight: 1.75, color: "var(--muted)" }}>
        <b style={{ color: "var(--fg)" }}>제출·입출력 (모든 챌린지 공통, 자동 적용)</b><br />
        · 제출 소스 ≤ <b>1&nbsp;MB</b> · 추가 데이터 <code>data.bin</code> ≤ <b>10&nbsp;MB</b>(선택) · 입력=표준입력, 출력=표준출력, <code>data.bin</code>은 파일 입출력.<br />
        · 12개 언어 <b style={{ color: "var(--fg)" }}>예시 코드(입출력 골격)는 플랫폼이 자동 제공</b>합니다 — 작성자가 따로 업로드할 필요 없어요.
      </div>
    </div>

    <div className="row" style={{ gap: 10, flexWrap: "wrap" }}><button className="btn" disabled={!valid || creating} onClick={create}>{creating ? "만드는 중…" : "모의고사 만들기"}</button><Link href="/" className="btn ghost">취소</Link>{!valid && <span className="bad" style={{ fontSize: 12 }}>{reason}</span>}</div>
  </div>;
}

// ===== nickname gate =====
function NicknameGate({ onDone }: { onDone: (n: string) => void }) {
  const [val, setVal] = useState("");
  const err = val ? validateNick(val) : "";
  return <>
    <div className="nav"><div className="brand"><div className="mk" /> DMPC</div></div>
    <div className="wrap" style={{ maxWidth: 460, marginTop: 60 }}>
      <h1 style={{ textAlign: "center" }}>닉네임 설정</h1>
      <p className="muted center" style={{ marginBottom: 22, lineHeight: 1.6 }}>대회에서 표시될 닉네임을 정하세요.<br />영문·숫자·밑줄(_) 2~16자 · 밑줄로 시작/끝 불가 · 중복·예약어 불가 · 변경 불가.</p>
      <div className="card">
        <input placeholder="예: Clean_King_07" value={val} onChange={(e) => setVal(e.target.value)} style={{ width: "100%", background: "#0f1117", color: "var(--fg)", border: "1px solid var(--line)", borderRadius: 9, padding: 12, fontSize: 15 }} />
        <div style={{ minHeight: 20, margin: "8px 2px", fontSize: 13 }}>{val && (err ? <span style={{ color: "var(--accent2)" }}>✕ {err}</span> : <span style={{ color: "var(--green)" }}>✓ 사용 가능한 닉네임입니다.</span>)}</div>
        <button className="btn block" disabled={!val || !!err} onClick={() => onDone(val.trim())}>시작하기</button>
      </div>
    </div>
  </>;
}

// ===== login gate (real-API mode): @dimigo Google login =====
function LoginGate({ url }: { url: string }) {
  return <>
    <div className="nav"><div className="brand"><div className="mk" /> DMPC</div></div>
    <div className="wrap" style={{ maxWidth: 460, marginTop: 64, textAlign: "center" }}>
      <h1>DMPC 로그인</h1>
      <p className="muted" style={{ marginBottom: 22, lineHeight: 1.7 }}>
        디미고 모의 프로그래밍 대회 — <b style={{ color: "var(--fg)" }}>@dimigo.hs.kr</b> 구글 계정만 참여할 수 있어요.
      </p>
      <div className="card">
        <a className="btn block" href={url} style={{ textDecoration: "none", display: "block", padding: "12px" }}>디미고 Google 계정으로 로그인</a>
        <p className="muted" style={{ fontSize: 12, marginTop: 12, marginBottom: 0 }}>로그인하면 학교 계정 도메인이 서버에서 한 번 더 확인됩니다.</p>
      </div>
    </div>
  </>;
}

// ===== Shell: persistent nav + gate + toast + notifications =====
export function Shell({ children }: { children: ReactNode }) {
  const { ready, apiMode, authState, loginUrl, nick, setNick, notifs, notifOpen, setNotifOpen, unseen, markNotifsSeen, toast, closeToast, showToast } = useStore();
  const [help, setHelp] = useState(false);
  useEffect(() => {
    if (!notifOpen) return;
    const h = () => setNotifOpen(false);
    window.addEventListener("click", h);
    return () => window.removeEventListener("click", h);
  }, [notifOpen, setNotifOpen]);
  useEffect(() => {
    function onEsc(e: KeyboardEvent) {
      if (e.key !== "Escape") return;
      setHelp(false); setNotifOpen(false); closeToast();
    }
    window.addEventListener("keydown", onEsc);
    return () => window.removeEventListener("keydown", onEsc);
  }, [setNotifOpen, closeToast]);
  // mock mode resolves synchronously; API mode fetches the session first, so show a
  // brief loading screen (not a blank page) while authState is still "loading".
  if (!ready) {
    if (!apiMode) return null;
    return <>
      <div className="nav"><div className="brand"><div className="mk" /> DMPC</div></div>
      <div className="wrap" style={{ textAlign: "center" }}><p className="muted" style={{ marginTop: 64 }}>불러오는 중…</p></div>
    </>;
  }
  // real-API mode: gate on the server session, then nickname; mock mode: nickname only.
  if (apiMode) {
    if (authState === "login") return <LoginGate url={loginUrl()} />;
    if (authState === "nickname") return <NicknameGate onDone={setNick} />;
  } else if (!nick) {
    return <NicknameGate onDone={(n) => { setNick(n); showToast(`환영합니다, ${n} 님!`); }} />;
  }
  return <>
    <div className="nav">
      <Link href="/" className="brand" style={{ textDecoration: "none" }}><div className="mk" /> DMPC</Link>
      <div className="right">
        <span>연습 세션</span>
        <span className="ic" title="도움말" onClick={(e) => { e.stopPropagation(); setHelp(true); }}>?</span>
        <span className="ic" title="알림" onClick={(e) => { e.stopPropagation(); const open = !notifOpen; setNotifOpen(open); if (open) markNotifsSeen(); }}>🔔{unseen > 0 && !notifOpen && <span className="badge">{unseen}</span>}</span>
        <span style={{ fontSize: 13, color: "var(--fg)" }}>{nick}</span><div className="avatar" />
      </div>
    </div>
    {help && <HelpModal onClose={() => setHelp(false)} />}
    {children}
    {notifOpen && <div className="notif-panel" onClick={(e) => e.stopPropagation()}>
      <div className="notif-head">알림 <span className="muted" style={{ fontSize: 12, cursor: "pointer" }} onClick={() => setNotifOpen(false)}>✕</span></div>
      {notifs.length ? notifs.map((n, i) => <div key={i} className="notif-item"><span className="notif-ic">{n.icon}</span><div><div>{n.msg}</div><div className="muted" style={{ fontSize: 12, marginTop: 2 }}>{n.sub}</div></div></div>) : <div className="notif-empty">알림이 없습니다</div>}
    </div>}
    {toast && <div className="toast"><span className="x" onClick={closeToast}>✕</span><b>✓</b> {toast.msg}{toast.sub && <div className="muted" style={{ marginTop: 4 }}>({toast.sub})</div>}</div>}
  </>;
}
