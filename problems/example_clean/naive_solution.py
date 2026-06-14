"""A 'worse but valid' submission: visit dirty cells in row-major order.

Cleans everything (so it's valid) but usually takes more moves than the
nearest-neighbour reference, so it earns PARTIAL Step Up marks — useful for
demonstrating the partial-credit branch of the scoring formula.
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
    dirty.sort()  # row-major, ignoring distance -> generally suboptimal

    def path(a, b):
        (r1, c1), (r2, c2) = a, b
        return ("D" if r2 > r1 else "U") * abs(r2 - r1) + ("R" if c2 > c1 else "L") * abs(c2 - c1)

    pos = (sr, sc)
    out = []
    for d in dirty:
        out.append(path(pos, d))
        pos = d
    return "".join(out)


if __name__ == "__main__":
    print(solve(sys.stdin.read()))
