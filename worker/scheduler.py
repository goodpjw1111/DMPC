"""
Evaluation scheduler — drives contest status + 09:00/18:00 KST evaluation rounds.

Runs on VM1 (the app/grader host). Each tick:
  1. advance every contest's status (scheduled -> live -> ended) — the scheduler OWNS
     these flips; nothing else writes contest.status.
  2. ensure DUE evaluation_rounds rows exist (idempotent; catch-up safe — a round
     missed because the host was down is created on a later tick since scheduled_at
     stays <= now).
  3. recover rounds wedged in an in-flight status past a lease (grader crashed mid-run).
  4. grade due rounds locally via grade_round.evaluate_round (Oracle-VM grader mode,
     this host has DB + isolate). ALTERNATIVE (split mode): instead of grading inline,
     repository_dispatch each due round_id to .github/workflows/grade.yml and let the
     ephemeral runner POST results back via the HMAC callback — no DB DSN in that job.

Catch-up + idempotency come from round identity (eval_round.idem_key) and the AS-OF
cutoff (round.scheduled_at), so a late tick reproduces an on-time round exactly.
Run:  DATABASE_URL=... EVAL_SEED_SECRET=... python worker/scheduler.py   (loop)
      DMPC_SCHED_ONCE=1 ... python worker/scheduler.py                    (single tick)
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
from datetime import datetime, timedelta, timezone

import asyncpg

_HERE = os.path.dirname(__file__)
sys.path.insert(0, os.path.join(_HERE, "..", "api"))   # app.round_service
sys.path.insert(0, _HERE)                               # grade_round (same dir)

from app.round_service import advance_contest_status, ensure_due_rounds  # noqa: E402
from grade_round import EVAL_SEED_SECRET, MAX_ROUND_ATTEMPTS, evaluate_round  # noqa: E402

DATABASE_URL = os.environ["DATABASE_URL"]
KST = timezone(timedelta(hours=9))
SCHED_INTERVAL_S = float(os.environ.get("DMPC_SCHED_INTERVAL_S", "300"))   # 5 min
ROUND_LEASE_S = float(os.environ.get("DMPC_ROUND_LEASE_S", "1800"))        # 30 min

# Reset rounds whose grader crashed mid-run (in-flight past the lease) so they re-claim.
# Clearing claimed_by invalidates the dead grader's owner token: if it somehow comes back,
# its guarded terminal/commit writes find 0 rows and roll back (no double-publish).
RECOVER_ROUNDS_SQL = """
UPDATE evaluation_rounds
   SET status='pending', claimed_at=NULL, claimed_by=NULL
 WHERE status IN ('generating','judging','scoring')
   AND claimed_at IS NOT NULL
   AND claimed_at < now() - ($1 || ' seconds')::interval
RETURNING id;
"""

# A single-row HEARTBEAT so the API (and the admin UI) can SEE that grading is actually
# running — every tick stamps now() + a small summary (incl. whether EVAL_SEED_SECRET is
# present, which only this in-Actions process can know). Self-provisioned (CREATE IF NOT
# EXISTS) so no manual schema migration is needed on the live DB. Single-writer by design:
# the evals workflow uses concurrency:{group:evals} so ticks never overlap; the upsert is
# last-write-wins (a status row), which self-corrects on the next tick anyway.
HEARTBEAT_DDL = """
CREATE TABLE IF NOT EXISTS scheduler_heartbeat (
    id           boolean PRIMARY KEY DEFAULT TRUE CHECK (id),
    last_tick_at timestamptz NOT NULL,
    detail       jsonb NOT NULL DEFAULT '{}'::jsonb
);
"""
HEARTBEAT_UPSERT = """
INSERT INTO scheduler_heartbeat (id, last_tick_at, detail)
VALUES (TRUE, now(), $1::jsonb)
ON CONFLICT (id) DO UPDATE SET last_tick_at = now(), detail = EXCLUDED.detail;
"""


async def tick(conn) -> None:
    now = datetime.now(KST)

    # 1+2. status flips + due-round creation for any active/just-closed contest.
    contests = await conn.fetch(
        """SELECT id, status, starts_at, ends_at FROM contests
            WHERE status IN ('scheduled','live','ended')
              AND ends_at > now() - interval '2 days'"""
    )
    for c in contests:
        await advance_contest_status(conn, c, now)
        created = await ensure_due_rounds(conn, c, now)
        if created:
            print(f"[sched] contest {c['id']}: created {len(created)} round(s)")

    # 3. recover wedged rounds.
    recovered = await conn.fetch(RECOVER_ROUNDS_SQL, str(int(ROUND_LEASE_S)))
    if recovered:
        print(f"[sched] recovered {len(recovered)} stuck round(s)")

    # 4. grade due rounds (local grader mode). Exclude rounds that have burned their
    # attempts budget — a persistently-failing round drops out for operator triage
    # (its `error` column says why) instead of re-grading the whole field every tick.
    due = await conn.fetch(
        """SELECT id FROM evaluation_rounds
            WHERE status IN ('pending','failed') AND scheduled_at <= $1
              AND attempts < $2
            ORDER BY scheduled_at""",
        now, MAX_ROUND_ATTEMPTS,
    )
    graded = 0
    for r in due:
        rid = str(r["id"])
        try:
            outcome = await evaluate_round(conn, rid, secret=EVAL_SEED_SECRET)
            graded += 1
            print(f"[sched] round {rid}: {outcome}")
        except Exception as e:  # noqa: BLE001  — one bad round must not kill the tick
            print(f"[sched] round {rid} errored: {e}")

    # 5. stamp the heartbeat so the API can show "grading is alive" without GitHub Actions.
    # secret_present lets the admin UI catch a missing EVAL_SEED_SECRET (the #1 silent failure).
    try:
        await conn.execute(HEARTBEAT_UPSERT, json.dumps({
            "secret_present": bool(EVAL_SEED_SECRET),
            "due": len(due), "graded": graded,
            "contests": len(contests), "recovered": len(recovered),
        }))
    except Exception as e:  # noqa: BLE001 — a heartbeat failure must not fail the tick
        print(f"[sched] heartbeat write failed: {e}")


async def main() -> None:
    pool = await asyncpg.create_pool(DATABASE_URL, min_size=1, max_size=4)
    async with pool.acquire() as conn:           # self-provision the heartbeat table (idempotent)
        await conn.execute(HEARTBEAT_DDL)
    once = os.environ.get("DMPC_SCHED_ONCE") == "1"
    if not EVAL_SEED_SECRET:                      # loud boot warning — Challenge rounds can't grade
        print("[sched] WARNING: EVAL_SEED_SECRET is empty — Challenge rounds will fail to grade")
    print(f"[sched] start (interval {SCHED_INTERVAL_S:.0f}s, lease {ROUND_LEASE_S:.0f}s, "
          f"once={once})")
    while True:
        async with pool.acquire() as conn:
            await tick(conn)
        if once:
            break
        await asyncio.sleep(SCHED_INTERVAL_S)


if __name__ == "__main__":
    asyncio.run(main())
