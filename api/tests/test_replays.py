"""Replay eligibility + body validation + visibility gating (pure parts; the SQL
upsert/list needs a DB). Run:  python api/tests/test_replays.py"""

from __future__ import annotations

import os
import sys
import types

HERE = os.path.dirname(__file__)
sys.path.insert(0, os.path.join(HERE, ".."))

from fastapi import HTTPException  # noqa: E402

from app.deps import CurrentUser, assert_replay_visible  # noqa: E402
from app.routers.replays import is_eligible_rank, validate_body  # noqa: E402


def _user(uid="u1", role="student"):
    return CurrentUser(id=uid, email="x@dimigo.hs.kr", display_name="x", nickname="x", role=role)


def _expect(code, fn):
    try:
        fn()
    except HTTPException as e:
        assert e.status_code == code, e.status_code
        return
    raise AssertionError(f"expected HTTPException({code})")


# --- eligibility (final top-3 only) ---------------------------------------
def test_eligible_top3_only():
    assert is_eligible_rank(1) and is_eligible_rank(2) and is_eligible_rank(3)
    assert not is_eligible_rank(4)
    assert not is_eligible_rank(None)      # never ranked
    assert not is_eligible_rank(0)


# --- body validation ------------------------------------------------------
def test_validate_body_trims_and_requires_content():
    assert validate_body("  hi  ") == "hi"
    _expect(400, lambda: validate_body("   "))      # empty after trim
    _expect(400, lambda: validate_body(""))


def test_validate_body_length_cap():
    _expect(400, lambda: validate_body("a" * 20_001))
    assert validate_body("a" * 20_000) == "a" * 20_000


# --- visibility gate (deps.assert_replay_visible) -------------------------
ENDED = {"status": "ended"}
LIVE = {"status": "live"}


def test_owner_and_admin_always_visible():
    rep = {"user_id": "owner", "is_shared": False, "moderated": False}
    assert_replay_visible(rep, LIVE, _user("owner"))          # owner: ok even if unshared/live
    assert_replay_visible(rep, LIVE, _user("someone", role="admin"))   # admin: ok


def test_other_user_needs_shared_moderated_ended():
    other = _user("other")
    _expect(404, lambda: assert_replay_visible({"user_id": "o", "is_shared": True, "moderated": True}, LIVE, other))   # not ended
    _expect(404, lambda: assert_replay_visible({"user_id": "o", "is_shared": False, "moderated": True}, ENDED, other))  # not shared
    _expect(404, lambda: assert_replay_visible({"user_id": "o", "is_shared": True, "moderated": False}, ENDED, other))  # not moderated
    # all three satisfied -> visible
    assert_replay_visible({"user_id": "o", "is_shared": True, "moderated": True}, ENDED, other)


if __name__ == "__main__":
    tests = [(n, o) for n, o in sorted(globals().items())
             if n.startswith("test_") and isinstance(o, types.FunctionType)]
    failed = 0
    for name, fn in tests:
        try:
            fn(); print(f"  ok   {name}")
        except AssertionError as e:
            failed += 1; print(f"  FAIL {name}: {e}")
        except Exception as e:  # noqa: BLE001
            failed += 1; print(f"  ERR  {name}: {type(e).__name__}: {e}")
    print(f"\n{len(tests) - failed}/{len(tests)} passed")
    sys.exit(1 if failed else 0)
