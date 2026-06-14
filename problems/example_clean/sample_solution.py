"""A 'good' submission: nearest-neighbour visiting order.

Reads the instance from stdin, prints a move string to stdout. Self-contained
(parses its own input) exactly as a real contestant submission would be.
This matches the reference order, so it earns full Step Up marks.
"""

import sys


def solve(text: str) -> str:
    lines = text.split("\n")
    h, w = map(int, lines[0].split())
    sr, sc = map(int, lines[1].split())
    dirty = []
    for r in range(h):
        row = lines[2 + r]
        for c, ch in enumerate(row):
            if ch == "*":
                dirty.append((r, c))

    def path(a, b):
        (r1, c1), (r2, c2) = a, b
        v = ("D" if r2 > r1 else "U") * abs(r2 - r1)
        hor = ("R" if c2 > c1 else "L") * abs(c2 - c1)
        return v + hor

    pos = (sr, sc)
    remaining = set(dirty)
    out = []
    while remaining:
        nxt = min(remaining, key=lambda d: abs(d[0] - pos[0]) + abs(d[1] - pos[1]))
        out.append(path(pos, nxt))
        pos = nxt
        remaining.discard(nxt)
    return "".join(out)


if __name__ == "__main__":
    print(solve(sys.stdin.read()))
