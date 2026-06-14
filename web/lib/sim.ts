// Cleaning-robot simulator logic (mirrors judge/problem.py + the prototype).
// Pure functions; React components render from these.

export type Inst = { h: number; w: number; start: [number, number]; dirty: Set<string> };

export const KEY_MOVES: Record<string, [number, number, string]> = {
  ArrowUp: [-1, 0, "U"], ArrowDown: [1, 0, "D"], ArrowLeft: [0, -1, "L"], ArrowRight: [0, 1, "R"],
};
const DELTA: Record<string, [number, number]> = { U: [-1, 0], D: [1, 0], L: [0, -1], R: [0, 1] };

export const MAX_GRID = 2000;       // per-dimension cap
export const MAX_CELLS = 1_000_000; // h*w cap — guards the O(h*w) fill loop from a freeze

export function parseInst(text: string): Inst {
  const L = text.split("\n");
  const [h, w] = L[0].split(" ").map(Number);
  const [sr, sc] = L[1].split(" ").map(Number);
  if (!(h > 0 && w > 0) || Number.isNaN(sr) || Number.isNaN(sc)) throw new Error("bad input");
  // bound the grid so a pasted "100000 100000" can't lock the main thread.
  if (h > MAX_GRID || w > MAX_GRID || h * w > MAX_CELLS) throw new Error("grid too large");
  const dirty = new Set<string>();
  for (let r = 0; r < h; r++) {
    const row = L[2 + r] || "";
    for (let c = 0; c < w; c++) if (row[c] === "*") dirty.add(r + "," + c);
  }
  return { h, w, start: [sr, sc], dirty };
}

export function parseMoves(out: string): string[] {
  return [...(out || "")].map((c) => c.toUpperCase()).filter((c) => "UDLR".includes(c));
}

export function boardState(g: Inst, moves: string[], k: number) {
  let pos: [number, number] = [g.start[0], g.start[1]];
  const visited = new Set<string>([pos.join(",")]);
  let offGrid = false;
  for (let i = 0; i < k && i < moves.length; i++) {
    const d = DELTA[moves[i]];
    if (!d) continue;
    const nr = pos[0] + d[0], nc = pos[1] + d[1];
    if (nr < 0 || nr >= g.h || nc < 0 || nc >= g.w) { offGrid = true; break; }
    pos = [nr, nc];
    visited.add(pos.join(","));
  }
  return { pos, visited, offGrid };
}

export function greedy(g: Inst): string {
  let pos: [number, number] = [g.start[0], g.start[1]];
  const rem = new Set(g.dirty);
  let s = "";
  const path = (a: [number, number], b: [number, number]) => {
    let r = a[0], c = a[1], t = "";
    while (r < b[0]) { t += "D"; r++; }
    while (r > b[0]) { t += "U"; r--; }
    while (c < b[1]) { t += "R"; c++; }
    while (c > b[1]) { t += "L"; c--; }
    return t;
  };
  while (rem.size) {
    let best: [number, number] | null = null, bd = 1e9;
    for (const key of rem) {
      const p = key.split(",").map(Number) as [number, number];
      const d = Math.abs(p[0] - pos[0]) + Math.abs(p[1] - pos[1]);
      if (d < bd) { bd = d; best = p; }
    }
    s += path(pos, best!);
    pos = best!;
    rem.delete(best!.join(","));
  }
  return s;
}

export function referenceCost(g: Inst): number {
  return greedy(g).length; // no walls -> greedy tour length == move count
}

export type CheckResult = { valid: boolean; cost: number | null; msg: string };

export function checkOutput(g: Inst, out: string): CheckResult {
  const moves = parseMoves(out);
  if (!moves.length) return { valid: false, cost: null, msg: "출력이 비어 있습니다" };
  const st = boardState(g, moves, moves.length);
  if (st.offGrid) return { valid: false, cost: null, msg: "격자 밖으로 나가는 이동" };
  const rem = [...g.dirty].filter((k) => !st.visited.has(k)).length;
  if (rem > 0) return { valid: false, cost: null, msg: `먼지 ${rem}개가 남았습니다` };
  return { valid: true, cost: moves.length, msg: "ok" };
}

// Parametric generator (seed + ranges -> input). Deterministic per seed so an
// authored contest is reproducible. (The server has the real Python generator;
// this mirror lets created contests be fully playable in the browser.)
export type GenParams = { hMin: number; hMax: number; wMin: number; wMax: number; dMin: number; dMax: number };
export const DEFAULT_GEN: GenParams = { hMin: 6, hMax: 9, wMin: 6, wMax: 9, dMin: 6, dMax: 10 };

function mulberry32(seed: number) {
  let a = seed >>> 0;
  return function () {
    a = (a + 0x6d2b79f5) | 0;
    let t = Math.imul(a ^ (a >>> 15), 1 | a);
    t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t;
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}

export function genClean(seed: number, p: GenParams): string {
  const rnd = mulberry32(seed);
  const ri = (lo: number, hi: number) => lo + Math.floor(rnd() * (hi - lo + 1));
  const h = ri(p.hMin, p.hMax), w = ri(p.wMin, p.wMax);
  const cells: [number, number][] = [];
  for (let r = 0; r < h; r++) for (let c = 0; c < w; c++) if (!(r === 0 && c === 0)) cells.push([r, c]);
  for (let i = cells.length - 1; i > 0; i--) { const j = Math.floor(rnd() * (i + 1)); [cells[i], cells[j]] = [cells[j], cells[i]]; }
  const k = Math.min(ri(p.dMin, p.dMax), cells.length);
  const dirty = new Set(cells.slice(0, k).map((x) => x.join(",")));
  const lines = [`${h} ${w}`, `0 0`];
  for (let r = 0; r < h; r++) { let row = ""; for (let c = 0; c < w; c++) row += dirty.has(r + "," + c) ? "*" : "."; lines.push(row); }
  return lines.join("\n") + "\n";
}

// Step Up scoring for one mission (mirrors judge/grader.grade_stepup_mission).
export function gradeStep(input: string, output: string, missionBudget: number) {
  const g = parseInst(input);
  const r = checkOutput(g, output);
  const ref = referenceCost(g);
  const ratio = r.valid && r.cost ? Math.min(ref / r.cost, 1) : 0;
  return { valid: r.valid, cost: r.cost, ref, ratio, score: Math.floor(missionBudget * ratio), msg: r.msg };
}

// Difficulty weight of a mission = its achievable reference cost (mirrors
// judge/grader.mission_weights). Harder instance -> higher optimal cost -> more points.
export function referenceCostOf(input: string): number {
  try { return Math.max(referenceCost(parseInst(input)), 1); } catch { return 1; }
}

// Difficulty-weighted per-mission budgets summing EXACTLY to `total`
// (mirrors judge/grader.mission_budgets: cumulative-weight staircase).
export function weightedBudgets(weights: number[], total: number): number[] {
  let w = weights.map((x) => Math.max(x, 0));
  let sum = w.reduce((a, b) => a + b, 0);
  if (sum <= 0) { w = weights.map(() => 1); sum = w.length; }
  const out: number[] = [];
  let prev = 0, cum = 0;
  for (let i = 0; i < w.length; i++) { cum += w[i]; const cut = Math.floor((total * cum) / sum); out.push(cut - prev); prev = cut; }
  return out;
}
