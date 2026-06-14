"""Tests for api/app/round_service.py — idempotent round creation + status flips.

Fake conn, no Postgres. Run:  python api/tests/test_round_service.py
"""

from __future__ import annotations

import asyncio
import os
import sys
import types
from datetime import date, timedelta

HERE = os.path.dirname(__file__)
sys.path.insert(0, os.path.join(HERE, ".."))          # import app.*  (package)

from app.round_service import advance_contest_status, ensure_due_rounds  # noqa: E402
from app.schedule import contest_window, eval_times    # noqa: E402

REG = date(2026, 6, 13)
STARTS, ENDS = contest_window(REG)                      # 6/14 09:00 KST .. 6/17 09:00 KST


class FakeConn:
    def __init__(self):
        self.existing: set[str] = set()      # idem_keys already inserted
        self.contest_status_updates: list[str] = []
        self._n = 0

    async def fetchrow(self, q, *a):
        if "INSERT INTO evaluation_rounds" in q:
            contest_id, rtype, idem, dt = a
            if idem in self.existing:
                return None                  # ON CONFLICT DO NOTHING
            self.existing.add(idem)
            self._n += 1
            return {"id": f"round-{self._n}", "type": rtype, "idem_key": idem,
                    "scheduled_at": dt}
        return None

    async def execute(self, q, *a):
        if "UPDATE contests SET status" in q:
            self.contest_status_updates.append(a[1])
        return "OK"


def _contest(status="live"):
    return {"id": "contest-1", "status": status, "starts_at": STARTS, "ends_at": ENDS}


def run(coro): return asyncio.run(coro)


def test_creates_all_due_rounds_at_end():
    conn = FakeConn()
    created = run(ensure_due_rounds(conn, _contest(), ENDS))
    moments = eval_times(STARTS, ENDS)
    assert len(created) == len(moments)               # every moment due at end
    finals = [c for c in created if c["type"] == "final"]
    assert len(finals) == 1                            # exactly one final
    assert finals[0]["idem_key"].endswith(":final")    # SLOT_FINAL keeps it distinct


def test_only_due_moments_created_midway():
    conn = FakeConn()
    midday = STARTS + timedelta(days=1, hours=3)        # 6/15 12:00 KST
    created = run(ensure_due_rounds(conn, _contest(), midday))
    expected = [t for t, _ in eval_times(STARTS, ENDS) if t <= midday]
    assert len(created) == len(expected)               # 6/14 09,18 + 6/15 09 = 3
    assert all(c["type"] == "interim" for c in created)


def test_creation_is_idempotent_on_refire():
    conn = FakeConn()
    first = run(ensure_due_rounds(conn, _contest(), ENDS))
    again = run(ensure_due_rounds(conn, _contest(), ENDS))   # same tick re-fired
    assert len(first) > 0 and again == []              # second pass creates nothing


def test_final_idem_key_distinct_from_same_day_0900():
    # The final is at 09:00 on the closing day; its idem_key must differ from a 0900 slot.
    conn = FakeConn()
    created = run(ensure_due_rounds(conn, _contest(), ENDS))
    final = next(c for c in created if c["type"] == "final")
    day0900_keys = [c["idem_key"] for c in created if c["idem_key"].endswith(":0900")]
    assert final["idem_key"] not in day0900_keys


def test_status_scheduled_to_live():
    conn = FakeConn()
    c = _contest(status="scheduled")
    out = run(advance_contest_status(conn, c, STARTS))        # exactly at start
    assert out == "live" and conn.contest_status_updates == ["live"]


def test_status_live_to_ended():
    conn = FakeConn()
    out = run(advance_contest_status(conn, _contest(status="live"), ENDS))
    assert out == "ended"


def test_status_no_change_before_start():
    conn = FakeConn()
    before = STARTS - timedelta(hours=1)
    out = run(advance_contest_status(conn, _contest(status="scheduled"), before))
    assert out is None and conn.contest_status_updates == []


def test_status_jumps_straight_to_ended_if_past_end():
    conn = FakeConn()
    out = run(advance_contest_status(conn, _contest(status="scheduled"), ENDS + timedelta(hours=1)))
    assert out == "ended"


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
