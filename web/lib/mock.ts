// Mock data + helpers so the app runs fully on localhost with no backend.
// (Flip to the real API later via lib/api.ts; Step Up grading here mirrors the
// server's judge/grader.py exactly.)

import type { GenParams } from "./sim";
import { genClean, referenceCostOf, weightedBudgets } from "./sim";

// An authored problem (filled by the admin "모의고사 생성" form).
export type ProblemDef = {
  kind: "stepup" | "challenge";
  title: string;
  statement: string;          // Markdown + LaTeX ($..$) + images; shown in the 문제 tab
  timeMs: number; memMb: number;
  // generator: "param" = built-in cleaning-robot generator (browser-playable);
  // "code" = author-supplied generator/checker code (run on the server grader).
  genType?: "param" | "code";
  gen: GenParams;             // used when genType == "param"
  genLang?: string;           // language of the custom generator/checker
  genCode?: string;           // custom generator source (seed -> input)
  checkCode?: string;         // custom checker source (input+output -> cost)
  missions?: number[];        // stepup: one seed per mission
  seedRange?: [number, number]; // challenge: ranged random cases
  roundSeeds?: number;        // challenge: # hidden cases drawn per 09·18시 round (scoring_config.round_seeds)
  costEps?: number;           // challenge: float-cost tie tolerance (scoring_config.cost_eps; 0 for integer costs)
  budget: number;
};

export type Contest = {
  id: string; title: string; status: "live" | "ended" | "soon" | "draft"; when: string;
  desc: string; total: number; gotten: number; rank?: number; nextEval?: string;
  // per-part scores, each out of 1,000,000 (Step Up live, Challenge last-evaluated).
  suScore?: number; chScore?: number;
  problems?: ProblemDef[];    // present for admin-created contests; absent -> use the demo example
};

// final total = 2:8 weighted blend of the two parts (each out of 1e6). Mirrors
// judge/scoring.py:weighted_total so the frontend total matches the server.
export function weightedTotal(su: number, ch: number): number {
  return Math.floor((2 * su + 8 * ch) / 10);
}

// Minimal Contest placeholder for API-mode deep-links: the Api* views only need the
// id and re-fetch everything (status/scores/problems) from the backend by that id.
export function stubContest(id: string): Contest {
  return { id, title: "", status: "live", when: "", desc: "", total: 0, gotten: 0 };
}

// Step Up score for a contest = sum of the BEST score per mission from the user's
// submission history (mirrors judge/grader.stepup_problem_score_for_user). Recomputed
// live so a new submission updates the total.
export function stepupScoreFromHist(hist: StepSub[]): number {
  const best: Record<number, number> = {};
  for (const s of hist) best[s.mission] = Math.max(best[s.mission] ?? 0, s.score);
  return Object.values(best).reduce((a, b) => a + b, 0);
}

export type Mission = { mission: number; input: string; budget: number };

// Resolve a contest's Step Up missions: authored (generated from seeds+params) or
// the built-in demo (STEP_SEEDS). Lets created contests be fully playable/gradeable.
export function missionsFor(c: Contest): Mission[] {
  const p = c.problems?.find((x) => x.kind === "stepup");
  if (p && p.genType === "code") return []; // custom generator runs on the server, not in-browser
  if (p && p.missions && p.missions.length) {
    const inputs = p.missions.map((seed) => genClean(seed, p.gen));
    const budgets = weightedBudgets(inputs.map(referenceCostOf), p.budget);   // harder mission = more points
    return inputs.map((input, i) => ({ mission: i + 1, input, budget: budgets[i] }));
  }
  return STEP_SEEDS;
}
export function isCustomGen(c: Contest, kind: "stepup" | "challenge"): boolean {
  return c.problems?.find((x) => x.kind === kind)?.genType === "code";
}
export function statementFor(c: Contest, kind: "stepup" | "challenge"): string {
  const authored = c.problems?.find((x) => x.kind === kind)?.statement;
  if (authored) return authored;
  // Challenge has NO reference cost (cost') and is relatively ranked — never reuse
  // the Step Up statement (which shows the Cost'/Cost formula).
  return kind === "challenge" ? CHALLENGE_STATEMENT_HTML : STATEMENT_HTML;
}

// ===== Challenge relative scoring — mirrors judge/scoring.py exactly =====
// per-case score = ⌊1e6·(1 − 0.5·√((n_lose + 0.5·n_draw)/n_total))⌋, range [500k, 1e6].
export function challengeCaseScore(nLose: number, nDraw: number, nTotal: number): number {
  const ratio = Math.min(Math.max((nLose + 0.5 * nDraw) / Math.max(nTotal, 1), 0), 1);
  return Math.floor(1_000_000 * (1 - 0.5 * Math.sqrt(ratio)));
}

export const CH_PARTICIPANTS = 70;
export const CH_CASE_NAMES = ["B~U(100,399)", "B~U(400,700)", "B~U(701,1000)", "W~U(250,333)", "W~U(334,416)", "W~U(417,500)"];
export type EvalCase = { name: string; nLose: number; nDraw: number; score: number; rank: number; ms: number; cost: number };
export type EvalResult = { cases: EvalCase[]; chScore: number; myRank: number; nTotal: number; costSum: number };

// One eval round (deterministic). idx 1 = most recent (best); larger idx = older (worse).
// The challenge score is the MEAN of the per-case scores (not the sum) — and every
// displayed total (chScore, my rank, cost sum) is DERIVED from these cases, so the
// detail view always reconciles with the headline numbers.
export function challengeEval(idx: number, nTotal = CH_PARTICIPANTS): EvalResult {
  const cases: EvalCase[] = CH_CASE_NAMES.map((name, j) => {
    const nLose = Math.max(0, 4 + idx * 2 + ((idx * 7 + j * 13) % 6));   // fewer beat me on recent rounds
    const nDraw = (idx + j) % 3;
    const score = challengeCaseScore(nLose, nDraw, nTotal);
    const cost = 480 + ((idx * 31 + j * 53) % 90);                       // per-case moves
    const ms = 28 + ((idx * 7 + j * 19) % 90);
    return { name, nLose, nDraw, score, rank: nLose + 1, ms, cost };
  });
  const chScore = Math.floor(cases.reduce((a, c) => a + c.score, 0) / cases.length);
  const avgLose = cases.reduce((a, c) => a + c.nLose, 0) / cases.length;
  return { cases, chScore, myRank: Math.round(avgLose) + 1, nTotal, costSum: cases.reduce((a, c) => a + c.cost, 0) };
}
const C1_EVAL = challengeEval(1);   // c1's "last evaluated" challenge result drives its card

// Challenge sample grading (post-submit): per-sample COST only (score is 0 until an eval).
export function chSampleRows(id: number) {
  return [0, 1, 2, 3].map((i) => ({ i: i + 1, ms: 25 + ((id + i * 13) % 80), cost: 480 + ((id * 3 + i * 37) % 90) }));
}
export function chSubCost(id: number): number {
  return chSampleRows(id).reduce((a, r) => a + r.cost, 0);   // history cost == sum of its samples
}

// ===== Step Up demo missions — DIFFICULTY-WEIGHTED budgets (harder = more points) =====
// Per-mission budget ∝ reference cost (mirrors judge/grader.mission_weights/budgets),
// summing exactly to 1,000,000.
const STEP_SEED_INPUTS = [
  "7 8\n0 0\n....*...\n......**\n...*....\n........\n**.....*\n...*.*..\n........\n",
  "6 7\n0 0\n...*..*\n.*.....\n....*..\n*....*.\n..*....\n.....*.\n",
  "8 6\n0 0\n..*...\n....*.\n*.....\n...*.*\n.*....\n....*.\n*...*.\n......\n",
];
const STEP_BUDGETS = weightedBudgets(STEP_SEED_INPUTS.map(referenceCostOf), 1_000_000);
export const STEP_SEEDS = STEP_SEED_INPUTS.map((input, i) => ({ mission: i + 1, input, budget: STEP_BUDGETS[i] }));

export type StepSub = { id: number; mission: number; at: string; score: number; max: number };
// demo best-per-mission: m1 & m2 full marks, m3 partial — all relative to the weighted budgets.
export const STEP_HISTORY: StepSub[] = [
  { id: 9426, mission: 1, at: "6월 11일 17:01", score: STEP_BUDGETS[0], max: STEP_BUDGETS[0] },
  { id: 9392, mission: 1, at: "6월 11일 16:22", score: Math.floor(STEP_BUDGETS[0] * 0.947), max: STEP_BUDGETS[0] },
  { id: 9293, mission: 2, at: "6월 11일 11:49", score: STEP_BUDGETS[1], max: STEP_BUDGETS[1] },
  { id: 9255, mission: 3, at: "6월 11일 11:45", score: Math.floor(STEP_BUDGETS[2] * 0.854), max: STEP_BUDGETS[2] },
];
const C1_SU = stepupScoreFromHist(STEP_HISTORY);   // sum of best-per-mission (m1+m2 full, m3 partial)

export const CONTESTS: Contest[] = [
  // each part out of 1e6; gotten = weightedTotal(suScore, chScore).
  { id: "c1", title: "6월 모의고사 #3", status: "live", when: "2026-06-13 ~ 06-20",
    desc: "배찌와 다오의 대청소", total: 1000000, suScore: C1_SU, chScore: C1_EVAL.chScore,
    gotten: weightedTotal(C1_SU, C1_EVAL.chScore), rank: C1_EVAL.myRank, nextEval: "01:29:51" },
  { id: "c2", title: "6월 모의고사 #2", status: "ended", when: "2026-06-01 ~ 06-08",
    desc: "네코의 보물 탐사", total: 1000000, suScore: 880000, chScore: 832663,
    gotten: 842130, rank: 14 },
  { id: "c3", title: "7월 모의고사 #4", status: "soon", when: "2026-07-04 09:00 시작 ~ 07-07 09:00 종료",
    desc: "준비 중", total: 1000000, suScore: 0, chScore: 0, gotten: 0 },
];

export type ChSub = { id: number; at: string; cost: number; cur?: boolean };
// cost == sum of the submission's sample costs (so the detail view reconciles).
export const CH_HISTORY: ChSub[] = [
  { id: 10767, at: "6월 13일 14:29", cur: true },
  { id: 10005, at: "6월 12일 14:58" },
  { id: 9533, at: "6월 11일 18:59" },
  { id: 9376, at: "6월 11일 16:08" },
].map((h) => ({ ...h, cost: chSubCost(h.id) }));

export type ChEval = { at: string; rank: number | null; note: string };
// rank is the user's OVERALL rank in that round (≤ participants), derived from the eval.
export const CH_EVALS: ChEval[] = [
  { at: "6월 13일 18:00", rank: null, note: "연습 중간평가 #10" },
  { at: "6월 12일 18:00", rank: challengeEval(1).myRank, note: "연습 중간평가 #9" },
  { at: "6월 11일 18:00", rank: challengeEval(2).myRank, note: "연습 중간평가 #8" },
  { at: "6월 10일 18:00", rank: challengeEval(3).myRank, note: "연습 중간평가 #7" },
];

export type Rank = { rank: number; nick: string; score: number; me?: boolean };
export const RANKING: Rank[] = [
  { rank: 1, nick: "neko_master", score: 998120 },
  { rank: 2, nick: "Dao_07", score: 991340 },
  { rank: 3, nick: "clean_god", score: 980210 },
  { rank: 4, nick: "heuristic_lover", score: 975500 },
  { rank: 5, nick: "bazzi2", score: 969870 },
  { rank: 14, nick: "me_07", score: 842130, me: true },
];

export type Notif = { icon: string; msg: string; sub: string };
export const INITIAL_NOTIFS: Notif[] = [
  { icon: "✓", msg: "제출 #10767 채점 완료", sub: "샘플 점수 합 2,142 · 방금" },
  { icon: "🏅", msg: "연습 중간평가 #9 결과 공개", sub: "등수 2852 / 70명 · 1시간 전" },
  { icon: "📢", msg: "6월 모의고사 #3 진행 중", sub: "다음 중간평가 18:00 · 어제" },
];

export const STATEMENT_HTML = `<h2>배찌와 다오의 대청소</h2>
<p>배찌는 봉을 잡고 대청소를 하려고 합니다. 격자는 <code>N</code>행 <code>M</code>열이며 각 칸은 빈/먼지/블록 중 하나입니다.
방향키로 배찌를 움직여 모든 먼지를 청소하되 <b>이동 수(=비용)를 최소화</b>하세요.</p>
<ul>
<li>이동: 상하좌우. 격자 밖으로 나가면 무효입니다.</li>
<li>먼지를 밟으면 청소됩니다. 블록은 밀 수 있습니다(문제에 따라).</li>
</ul>
<h2 style="font-size:16px">입력 형식</h2>
<p>첫 줄에 <code>N M</code>, 둘째 줄에 시작 좌표, 이후 <code>N</code>개의 줄에 격자가 주어집니다.</p>

비용은 이동 횟수의 합 $\\text{cost} = \\sum_{t} 1$ 으로 정의되며, 점수는
$$\\text{score} = \\left\\lfloor S \\cdot \\min\\!\\left(\\tfrac{\\text{Cost}'}{\\text{Cost}},\\, 1\\right) \\right\\rfloor$$
입니다. ($\\text{Cost}'$ = 도달 가능한 기준 비용. LaTeX 렌더 예시)`;

// Challenge statement: code submission, relatively ranked. NO reference cost (cost'),
// and NO scoring formula in the statement — the cost is simply the move count
// (출력 길이), and the platform shows the relative-ranking rule separately.
export const CHALLENGE_STATEMENT_HTML = `<h2>배찌와 다오의 대청소 — 챌린지</h2>
<p>스텝 업과 같은 청소 문제지만, 이번엔 <b>코드를 제출</b>합니다. 비공개로 무작위 생성된 여러 격자에 대해
여러분의 프로그램이 <b>모든 먼지를 청소하는 이동 문자열</b>(<code>U/D/L/R</code>)을 출력하면,
<b>이동 수(=비용)</b>를 측정합니다. <b>비용이 낮을수록 좋습니다.</b> 먼지를 남기거나 격자 밖으로
나가면 그 케이스는 무효입니다.</p>
<h2 style="font-size:16px">입력 / 출력</h2>
<p>입력(표준입력) 형식은 스텝 업과 같습니다 — 첫 줄 <code>N M</code>, 둘째 줄 시작 좌표, 이후 <code>N</code>개의 격자 줄.
이동 문자열을 표준출력으로 내보냅니다. 미리 계산한 데이터가 있다면 <code>data.bin</code>으로 함께 올려
파일로 읽을 수 있습니다(소스 ≤ 1MB · data.bin ≤ 10MB).</p>`;

const RESERVED = ["admin", "administrator", "root", "system", "dmpc", "nypc"];
const TAKEN = ["bazzi", "dao", "cleanking", "admin"];

export function validateNick(name: string): string | null {
  name = (name || "").trim();
  if (name.length < 2) return "닉네임은 2자 이상이어야 합니다.";
  if (name.length > 16) return "닉네임은 16자 이하여야 합니다.";
  if (!/^[A-Za-z0-9_]+$/.test(name)) return "영문·숫자·밑줄(_)만 사용할 수 있습니다. (한글 불가)";
  if (name[0] === "_" || name.slice(-1) === "_") return "밑줄(_)로 시작하거나 끝날 수 없습니다.";
  if (RESERVED.includes(name.toLowerCase())) return "사용할 수 없는 닉네임입니다.";
  if (TAKEN.includes(name.toLowerCase())) return "이미 사용 중인 닉네임입니다.";
  return null;
}

export function pct(g: number, t: number) {
  return Math.max(0, Math.min(100, (100 * g) / t));
}
export function fmt(n: number) {
  return n.toLocaleString();
}
