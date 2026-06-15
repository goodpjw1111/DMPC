// In-browser simulator logic for "다오와 배찌의 길찾기" (problems/maze_push).
// A faithful JS port of the server checker's push rules so the board + cost shown to
// the player match how the server scores. Pure functions; React renders from them.

export type Pos = [number, number];
export type MazeInst = {
  R: number; C: number; P: number;
  walls: Set<string>; blocks0: Set<string>;
  dao0: Pos; bazzi0: Pos | null; goal: Pos;
};

export const MAZE_DIRS: Record<string, Pos> = { U: [-1, 0], D: [1, 0], L: [0, -1], R: [0, 1] };
export const MAZE_KEYS: Record<string, string> = { ArrowUp: "U", ArrowDown: "D", ArrowLeft: "L", ArrowRight: "R" };
const k = (r: number, c: number) => r + "," + c;

export function parseMaze(text: string): MazeInst {
  const lines = text.replace(/\n+$/, "").split("\n");
  const [R, C, P] = lines[0].split(/\s+/).map(Number);
  const walls = new Set<string>(), blocks0 = new Set<string>();
  let dao0: Pos = [0, 0], bazzi0: Pos | null = P === 2 ? [0, 0] : null, goal: Pos = [0, 0];
  for (let r = 0; r < R; r++) {
    const row = lines[1 + r] || "";
    for (let c = 0; c < C; c++) {
      const ch = row[c] ?? ".";
      if (ch === "#") walls.add(k(r, c));
      else if (ch === "O") blocks0.add(k(r, c));
      else if (ch === "D") dao0 = [r, c];
      else if (ch === "Z") bazzi0 = [r, c];
      else if (ch === "G") goal = [r, c];
    }
  }
  return { R, C, P, walls, blocks0, dao0, bazzi0, goal };
}

// Apply one move; mirrors problem.py _apply. `other` is the OTHER player's cell key (or null):
// a block may never overlap a player, so a push whose chain end lands on `other` FAILS.
// (Players may still share a cell with each other — only block<->player overlap is forbidden.)
export function applyMazeMove(R: number, C: number, walls: Set<string>, blocks: Set<string>,
                              pos: Pos, d: Pos, other: string | null = null): { pos: Pos; blocks: Set<string>; cost: number } {
  const [dr, dc] = d, nr = pos[0] + dr, nc = pos[1] + dc;
  if (nr < 0 || nr >= R || nc < 0 || nc >= C || walls.has(k(nr, nc))) return { pos, blocks, cost: 1 };
  if (blocks.has(k(nr, nc))) {
    let len = 0, cr = nr, cc = nc;
    while (blocks.has(k(cr, cc))) { len++; cr += dr; cc += dc; }
    if (cr < 0 || cr >= R || cc < 0 || cc >= C || walls.has(k(cr, cc)) || k(cr, cc) === other) return { pos, blocks, cost: 1 };
    const nb = new Set(blocks); nb.delete(k(nr, nc)); nb.add(k(cr, cc));
    return { pos: [nr, nc], blocks: nb, cost: 1 + len };
  }
  return { pos: [nr, nc], blocks, cost: 1 };
}

export type MazeState = {
  dao: Pos; bazzi: Pos | null; blocks: Set<string>;
  cost: number; reached: boolean; steps: number; daoNext: boolean;
};

// Replay a move string from the start (mirrors the server checker). `daoNext` is whose
// turn the NEXT move belongs to (for the C=2 alternation hint).
export function replayMaze(inst: MazeInst, moveStr: string): MazeState {
  let dao: Pos = [inst.dao0[0], inst.dao0[1]];
  let bazzi: Pos | null = inst.bazzi0 ? [inst.bazzi0[0], inst.bazzi0[1]] : null;
  let blocks = new Set(inst.blocks0);
  const moves = [...moveStr].filter((c) => MAZE_DIRS[c]);
  let cost = 0;
  for (let i = 0; i < moves.length; i++) {
    const daoTurn = inst.P === 1 || i % 2 === 0;
    const other = daoTurn ? (bazzi ? k(bazzi[0], bazzi[1]) : null) : k(dao[0], dao[1]);
    const res = applyMazeMove(inst.R, inst.C, inst.walls, blocks, daoTurn ? dao : (bazzi as Pos), MAZE_DIRS[moves[i]], other);
    blocks = res.blocks; cost += res.cost;
    if (daoTurn) {
      dao = res.pos;
      if (dao[0] === inst.goal[0] && dao[1] === inst.goal[1]) {
        return { dao, bazzi, blocks, cost, reached: true, steps: i + 1, daoNext: true };
      }
    } else {
      bazzi = res.pos;
    }
  }
  const daoNext = inst.P === 1 || moves.length % 2 === 0;
  return { dao, bazzi, blocks, cost, reached: false, steps: moves.length, daoNext };
}
