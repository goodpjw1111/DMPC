"""Seed a live demo contest with the example Step Up problem.

Usage (needs DATABASE_URL + asyncpg + an applied schema):
    DATABASE_URL=postgresql://dmpc:dmpc@localhost:5432/dmpc python api/seed_example.py

Idempotent-ish: skips if a contest with the same title already exists.
"""

from __future__ import annotations

import asyncio
import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "judge"))
from registry import load_problem  # noqa: E402

TITLE = "예시 모의고사 (배찌와 다오의 대청소)"


async def main() -> None:
    import asyncpg

    conn = await asyncpg.connect(os.environ["DATABASE_URL"])
    try:
        exists = await conn.fetchval("SELECT id FROM contests WHERE title=$1", TITLE)
        if exists:
            print(f"already seeded: contest {exists}")
            return

        mod = load_problem("example_clean")
        m = mod.META

        # Both INSERTs in ONE transaction: if the problem insert fails, the contest
        # row rolls back too — otherwise a half-seeded contest (0 problems) would be
        # detected as "already seeded" on re-run and never repaired.
        async with conn.transaction():
            # Starts yesterday, ends in 2 days -> 'live' now (see api/app/schedule.py rule).
            cid = await conn.fetchval(
                """INSERT INTO contests (title, status, starts_at, ends_at)
                   VALUES ($1, 'live', now() - interval '1 day', now() + interval '2 days')
                   RETURNING id""",
                TITLE,
            )
            await conn.execute(
                """INSERT INTO problems
                     (contest_id, kind, problem_key, title, statement_md,
                      time_limit_ms, memory_limit_mb, simulator_key, scoring_config)
                   VALUES ($1, 'stepup', $2, $3, $4, $5, $6, $7, $8)""",
                cid, "example_clean", m["title"], m["statement_md"],
                m["time_limit_ms"], m["memory_limit_mb"], m.get("simulator_key"),
                json.dumps({"given_seeds": m["given_seeds"],
                            "stepup_budget": m["stepup_budget"]}),
            )
        print(f"seeded contest {cid} + Step Up problem (example_clean)")
    finally:
        await conn.close()


if __name__ == "__main__":
    asyncio.run(main())
