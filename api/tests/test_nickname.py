"""Tests for nickname format rules (pure, stdlib)."""

from __future__ import annotations

import os
import sys
import types

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "app"))
from nickname import MAX_LEN, validate_nickname  # noqa: E402


def ok(name):
    assert validate_nickname(name) is None, f"{name!r} should be valid"


def bad(name):
    assert validate_nickname(name) is not None, f"{name!r} should be invalid"


def test_valid_handles():
    for n in ["bazzi", "Dao_07", "ab", "a" * MAX_LEN, "x1", "Clean_King_07"]:
        ok(n)


def test_korean_rejected():
    for n in ["디미고", "코딩왕_99", "청소왕", "한글닉네임"]:
        bad(n)


def test_too_short():
    bad("a")
    bad("")


def test_too_long():
    bad("a" * (MAX_LEN + 1))


def test_invalid_chars():
    for n in ["has space", "emoji😀", "dash-name", "dot.name", "slash/x", "quote'x"]:
        bad(n)


def test_no_leading_or_trailing_underscore():
    bad("_lead")
    bad("trail_")


def test_reserved_rejected():
    for n in ["admin", "Admin", "ROOT", "관리자", "dmpc"]:
        bad(n)


def test_strips_whitespace():
    ok("  bazzi  ")  # trimmed to 'bazzi'


if __name__ == "__main__":
    tests = [(n, o) for n, o in sorted(globals().items())
             if n.startswith("test_") and isinstance(o, types.FunctionType)]
    failed = 0
    for name, fn in tests:
        try:
            fn(); print(f"  ok   {name}")
        except AssertionError as e:
            failed += 1; print(f"  FAIL {name}: {e}")
    print(f"\n{len(tests) - failed}/{len(tests)} passed")
    sys.exit(1 if failed else 0)
