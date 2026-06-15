// Single place that knows where the API lives + how to call it with cookies.
// Empty default => same-origin (relative URLs), which is the single-origin
// reverse-proxy deployment (see next.config.js rewrites). Set NEXT_PUBLIC_API_BASE
// only for a separate API origin (then the API must use SameSite=None;Secure).
export const API_BASE = process.env.NEXT_PUBLIC_API_BASE ?? "";

// Real-API mode is OPT-IN so the localStorage-backed mock demo stays the default.
// Enabled by a build-time env (NEXT_PUBLIC_API=1) OR a runtime localStorage flag
// (`dmpc_api`=1) — the latter lets you flip it in the browser to test against a
// running backend without a rebuild.
export function apiEnabled(): boolean {
  if (process.env.NEXT_PUBLIC_API === "1") return true;
  if (typeof window === "undefined") return false;
  try { return localStorage.getItem("dmpc_api") === "1"; } catch { return false; }
}

export function loginUrl(): string {
  return `${API_BASE}/auth/login`;
}

// 401 from the API surfaces as this, so callers can redirect to login.
export class Unauthorized extends Error {}

// Reads the double-submit CSRF token the API set as a readable cookie.
// Matches both the dev name and the prod __Host-/__Secure- prefixed name.
function csrfToken(): string {
  const m = document.cookie.match(/(?:^|;\s*)(?:__Host-|__Secure-)?dmpc_csrf=([^;]+)/);
  return m ? decodeURIComponent(m[1]) : "";
}

async function unwrap<T>(res: Response, path: string): Promise<T> {
  if (res.status === 401) throw new Unauthorized(path);
  if (!res.ok) {
    let detail = "";
    try { detail = (await res.json())?.detail ?? ""; } catch { /* non-JSON */ }
    throw new Error(detail || `${path} -> ${res.status}`);
  }
  return res.status === 204 ? (undefined as T) : res.json();
}

export async function apiGet<T>(path: string): Promise<T> {
  return unwrap<T>(await fetch(`${API_BASE}${path}`, { credentials: "include" }), path);
}

// raw text/plain GET (mission input download) — same auth/401 handling as apiGet.
export async function apiGetText(path: string): Promise<string> {
  const res = await fetch(`${API_BASE}${path}`, { credentials: "include" });
  if (res.status === 401) throw new Unauthorized(path);
  if (!res.ok) throw new Error(`${path} -> ${res.status}`);
  return res.text();
}

export async function apiPost<T>(path: string, body?: unknown): Promise<T> {
  const res = await fetch(`${API_BASE}${path}`, {
    method: "POST",
    credentials: "include",
    headers: { "content-type": "application/json", "x-csrf-token": csrfToken() },
    body: body ? JSON.stringify(body) : undefined,
  });
  return unwrap<T>(res, path);
}

// multipart POST (Challenge submit: source + optional data.bin file).
export async function apiPostForm<T>(path: string, form: FormData): Promise<T> {
  const res = await fetch(`${API_BASE}${path}`, {
    method: "POST",
    credentials: "include",
    headers: { "x-csrf-token": csrfToken() },   // do NOT set content-type; browser adds the boundary
    body: form,
  });
  return unwrap<T>(res, path);
}

// ---- typed shapes returned by the API (match api/app/routers) ----
export type Me = { id: string; email: string; nickname: string | null; needs_nickname: boolean; role: string; is_tester?: boolean };
export type ApiContest = { id: string; title: string; status: string; starts_at: string; ends_at: string };
export type ApiProblemRef = { id: string; kind: "stepup" | "challenge"; title: string; score: number };
export type ApiContestDetail = ApiContest & {
  stepup_budget: number; challenge_budget: number; total: number; problems: ApiProblemRef[];
};
export type ApiMission = { seed: number; budget: number; best_score: number };
export type ApiProblem = {
  id: string; kind: "stepup" | "challenge"; title: string; statement_md: string;
  time_limit_ms: number; memory_limit_mb: number; simulator_key: string | null;
  example_input?: string | null; example_output?: string | null;
  missions?: ApiMission[];
  subtasks?: { name: string; budget: number }[];   // challenge: condition-based subtask names + weights
};
export type StepupSubmitResult = {
  submission_id: string; mission_seed: number; valid: boolean;
  cost: number | null; score: number; mission_budget: number; ratio: number; message: string;
};
export type StepupSubmission = {
  id: string; mission_seed: number; cost: number | null; valid: boolean; score: number; created_at: string;
};
export type ChallengeSample = { seed: number; cost: number | null; valid?: boolean; verdict?: string; runtime_ms?: number };
export type ChallengeSubmission = {
  id: string; language_id: string; state: string; code_bytes: number; data_bytes: number | null;
  sample_score_sum: number | null; sample_results: ChallengeSample[] | null; created_at: string;
};
export type StandingRow = { nickname: string; score: number; rank: number | null };
export type MyEvalCase = {
  problem_id: string; seed: number; verdict: string; raw_cost: number | null;
  runtime_ms: number | null; case_score: number | null; case_rank: number | null;
};
export type MyEval = {
  // status of the latest round if it isn't a published 'done' one yet (pending/grading/failed) —
  // lets the eval tab say "채점 대기 중" after 지금 평가 실행 instead of looking empty. null when up to date.
  pending?: string | null;
  round: { id: string; type: string; scheduled_at: string; published_at: string } | null;
  standing: { stepup_score: number; challenge_score: number; total_score: number; rank: number | null } | null;
  cases: MyEvalCase[];
};
export type ApiNotif = { id: string; type: string; payload: Record<string, unknown>; read: boolean; created_at: string };

export const getMe = () => apiGet<Me>("/api/me");
export const getContests = () => apiGet<ApiContest[]>("/api/contests");
export const setNickname = (nickname: string) => apiPost<{ nickname: string }>("/api/nickname", { nickname });

// ---- contest / problem reads ----
export const getContestDetail = (cid: string) => apiGet<ApiContestDetail>(`/api/contests/${cid}`);
export const getProblem = (pid: string) => apiGet<ApiProblem>(`/api/problems/${pid}`);
export type ProblemExample = { example_input: string | null; example_output: string | null };
export const getProblemExample = (pid: string) => apiGet<ProblemExample>(`/api/problems/${pid}/example`);
// Step Up "만점 기준 비용" per mission: { seed(string): optimal cost | null }. Lazy + cached server-side.
export type RefCosts = { ref_costs: Record<string, number | null> };
export const getProblemRefCosts = (pid: string) => apiGet<RefCosts>(`/api/problems/${pid}/ref-costs`);
export const getStandings = (cid: string) => apiGet<StandingRow[]>(`/api/contests/${cid}/standings`);
export const getMyEval = (cid: string) => apiGet<MyEval>(`/api/contests/${cid}/my-eval`);
export const missionInputUrl = (pid: string, seed: number) => `${API_BASE}/api/problems/${pid}/missions/${seed}/input`;
export const getMissionInput = (pid: string, seed: number) => apiGetText(`/api/problems/${pid}/missions/${seed}/input`);

// ---- submissions ----
export const submitStepup = (pid: string, mission_seed: number, output: string) =>
  apiPost<StepupSubmitResult>(`/api/problems/${pid}/stepup/submit`, { mission_seed, output });
export const getStepupSubmissions = (pid: string) =>
  apiGet<StepupSubmission[]>(`/api/problems/${pid}/stepup/submissions`);
export const submitChallenge = (pid: string, language_id: string, source: string, data: File | null) => {
  const fd = new FormData();
  fd.set("language_id", language_id);
  fd.set("source", source);
  if (data) fd.set("data", data, "data.bin");
  return apiPostForm<{ submission_id: string; state: string }>(`/api/problems/${pid}/challenge/submit`, fd);
};
export const getChallengeSubmissions = (pid: string) =>
  apiGet<ChallengeSubmission[]>(`/api/problems/${pid}/challenge/submissions`);

// ---- notifications ----
export const getNotifications = () => apiGet<ApiNotif[]>("/api/notifications");
export const markNotificationsRead = () => apiPost<{ ok: boolean }>("/api/notifications/read");

// ---- admin authoring ----
export type FeatureField = { key: string; label: string; min: number; max: number; default: number };
export type ProblemTemplate = {
  problem_key: string; title: string; kind: string | null; simulator_key: string | null;
  parametric: boolean; feature_schema: FeatureField[];
  statement_md?: string; time_limit_ms?: number; memory_limit_mb?: number; given_seeds?: number[];
};
export const getTemplates = () => apiGet<ProblemTemplate[]>("/api/admin/templates");
export type GenParamsPayload = { hMin: number; hMax: number; wMin: number; wMax: number; dMin: number; dMax: number };
export type CreateContestPayload = {
  title: string;
  problem_key: string;
  start_now?: boolean;
  draft?: boolean;
  gen_params: GenParamsPayload;
  stepup: { statement_md: string; given_seeds?: number[]; missions?: { seed: number; score: number; features: Record<string, number> }[]; time_limit_ms: number; memory_limit_mb: number };
  challenge: { statement_md: string; seed_range: [number, number]; round_seeds: number; cost_eps: number; subtasks?: { name: string; features: Record<string, number[]>; seed_lo: number; seed_hi: number; budget: number }[]; time_limit_ms: number; memory_limit_mb: number };
};
export const createContest = (payload: CreateContestPayload) =>
  apiPost<{ id: string; status: string; starts_at: string; ends_at: string }>("/api/admin/contests", payload);
export const evaluateNow = (cid: string) =>
  apiPost<{ round_id: string; scheduled_at: string }>(`/api/admin/contests/${cid}/evaluate-now`);
export const endContest = (cid: string) =>
  apiPost<{ status: string; final_round_id: string; ends_at: string }>(`/api/admin/contests/${cid}/end`);
export const publishContest = (cid: string) =>
  apiPost<{ status: string; starts_at: string; ends_at: string }>(`/api/admin/contests/${cid}/publish`);
export const deleteContest = (cid: string) =>
  apiPost<{ deleted: boolean; id: string }>(`/api/admin/contests/${cid}/delete`);

// ---- replays (winners' writeups / 시상) ----
export type Replay = {
  id: string; nickname: string; rank: number | null; body: string;
  has_pdf?: boolean; pdf_name?: string | null;
  is_shared: boolean; moderated: boolean; is_mine: boolean; created_at: string;
};
export type MyReplay = {
  eligible: boolean; rank: number | null;
  replay: { body: string; has_pdf: boolean; pdf_name: string | null; is_shared: boolean; moderated: boolean } | null;
};
export const getReplays = (cid: string) => apiGet<Replay[]>(`/api/contests/${cid}/replays`);
export const getMyReplay = (cid: string) => apiGet<MyReplay>(`/api/contests/${cid}/replay/me`);
export const postReplay = (cid: string, body: string, is_shared: boolean, pdf?: File | null) => {
  const fd = new FormData();
  fd.set("body", body);
  fd.set("is_shared", String(is_shared));
  if (pdf) fd.set("pdf", pdf, pdf.name);
  return apiPostForm<{ id: string; moderated: boolean; is_shared: boolean }>(`/api/contests/${cid}/replay`, fd);
};
export const replayPdfUrl = (cid: string, rid: string) => `${API_BASE}/api/contests/${cid}/replays/${rid}/pdf`;
export const moderateReplay = (rid: string, moderated: boolean) =>
  apiPost<{ id: string; moderated: boolean }>(`/api/admin/replays/${rid}/moderate`, { moderated });

// ---- registration (참가 신청) ----
export type Registration = { registered: boolean; count: number; open: boolean };
export const getRegistration = (cid: string) => apiGet<Registration>(`/api/contests/${cid}/registration`);
export const registerContest = (cid: string) => apiPost<{ registered: boolean; count: number }>(`/api/contests/${cid}/register`);
export const unregisterContest = (cid: string) => apiPost<{ registered: boolean; count: number }>(`/api/contests/${cid}/unregister`);
