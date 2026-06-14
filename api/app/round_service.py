"""
Evaluation-round lifecycle (creation + contest status flips) — DB glue, no sandbox.

The scheduler (worker/scheduler.py) calls these every tick:
  * advance_contest_status: scheduled -> live (at starts_at) -> ended (at ends_at).
    Nothing else in the codebase flips contest.status; the scheduler OWNS it.
  * ensure_due_rounds: for the contest's 09:00/18:00/final moments that are DUE
    (scheduled_at <= now), INSERT the evaluation_rounds row idempotently. A re-fired
    or catch-up tick re-derives the SAME round_idem_key and collides on
    UNIQUE(contest_id, idem_key) -> ON CONFLICT DO NOTHING makes it a true no-op.

Kept free of FastAPI imports and uses an asyncpg-style `conn` so it is unit-testable
with a fake connection (see api/tests/test_round_service.py).
"""

from __future__ import annotations

import os
import sys

from .schedule import KST, eval_times

# judge core (round identity / slot constants)
_JUDGE = os.path.join(os.path.dirname(__file__), "..", "..", "judge")
if _JUDGE not in sys.path:
    sys.path.insert(0, _JUDGE)

from eval_round import (  # noqa: E402
    SLOT_0900, SLOT_1800, SLOT_FINAL, round_idem_key,
)


def _moment_to_slot_type(dt, is_final: bool) -> tuple[str, str]:
    """Map an eval moment to (slot, eval_round_type).

    The final moment falls at 09:00 KST on the closing day; giving it its OWN slot
    (SLOT_FINAL) keeps its idem_key distinct from a same-instant 0900 interim so
    ON CONFLICT can't silently drop one of them. 'provisional' is reserved for the
    per-submit instant grade and is never produced here.
    """
    if is_final:
        return SLOT_FINAL, "final"
    hh = dt.astimezone(KST).hour
    return (SLOT_0900, "interim") if hh == 9 else (SLOT_1800, "interim")


async def advance_contest_status(conn, contest, now) -> str | None:
    """Flip scheduled->live->ended based on the clock. Returns the new status if it
    changed, else None. Only acts on 'scheduled'/'live' (a draft stays a draft)."""
    status = contest["status"]
    target = None
    if status in ("scheduled", "live") and now >= contest["ends_at"]:
        target = "ended"
    elif status == "scheduled" and now >= contest["starts_at"]:
        target = "live"
    if target and target != status:
        await conn.execute("UPDATE contests SET status=$2 WHERE id=$1",
                           contest["id"], target)
        return target
    return None


async def ensure_due_rounds(conn, contest, now) -> list[dict]:
    """Create any DUE evaluation_rounds rows (scheduled_at <= now) that don't exist
    yet. Returns the rows actually created this call (empty if all already existed).
    """
    starts_at, ends_at = contest["starts_at"], contest["ends_at"]
    created: list[dict] = []
    for dt, is_final in eval_times(starts_at, ends_at):
        if dt > now:
            continue                                  # not due yet — create later
        slot, rtype = _moment_to_slot_type(dt, is_final)
        date_iso = dt.astimezone(KST).date().isoformat()
        idem = round_idem_key(str(contest["id"]), date_iso, slot)
        row = await conn.fetchrow(
            """INSERT INTO evaluation_rounds (contest_id, type, idem_key, scheduled_at, status)
               VALUES ($1, $2, $3, $4, 'pending')
               ON CONFLICT (contest_id, idem_key) DO NOTHING
               RETURNING id, type, idem_key, scheduled_at""",
            contest["id"], rtype, idem, dt,
        )
        if row is not None:                           # NULL RETURNING => already existed
            created.append(dict(row))
    return created
