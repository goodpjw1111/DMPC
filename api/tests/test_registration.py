"""Registration-open logic (the only pure part; register/unregister need a DB).
Run:  python api/tests/test_registration.py"""

from __future__ import annotations

import os
import sys
import types
from datetime import datetime, timedelta, timezone

HERE = os.path.dirname(__file__)
sys.path.insert(0, os.path.join(HERE, ".."))

from app.routers.registrations import registration_open  # noqa: E402

NOW = datetime(2026, 6, 14, 12, 0, tzinfo=timezone.utc)
PAST = NOW - timedelta(hours=1)
FUTURE = NOW + timedelta(hours=1)


def test_open_when_scheduled_or_live_and_time_passed():
    assert registration_open("scheduled", None, NOW)
    assert registration_open("live", None, NOW)
    assert registration_open("scheduled", PAST, NOW)        # opened in the past
    assert registration_open("live", PAST, NOW)


def test_closed_when_ended_archived_or_draft():
    assert not registration_open("ended", None, NOW)
    assert not registration_open("archived", None, NOW)
    assert not registration_open("draft", None, NOW)


def test_closed_before_registration_opens_at():
    assert not registration_open("scheduled", FUTURE, NOW)  # not open yet
    assert registration_open("scheduled", NOW, NOW)         # exactly at open time -> open


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
