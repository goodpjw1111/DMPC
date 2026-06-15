"""
Grader worker — pulls queued Challenge submissions and runs them in the sandbox.

RUNS ON A DEDICATED GRADER HOST (Linux + isolate), NEVER the web tier. Claims one
submission at a time via Postgres `FOR UPDATE SKIP LOCKED`, runs the sample seeds
in isolate (no-net, time/mem limits, calibrated to c7a), runs the trusted per-problem
checker, and writes per-sample results back. Interim/final relative scoring is a
separate stage (judge/standings.py) driven by the scheduler.

Reliability model (single-operator, free-tier):
  * LEASE: claiming a row stamps claimed_at and bumps attempts. A periodic recovery
    sweep re-queues rows stuck in an in-flight state past a timeout (worker crash /
    OOM / host reboot), so a submission is never permanently stranded.
  * INTERNAL: a sandbox/infra verdict (isolate XX, empty meta, hung isolate) is
    RE-QUEUED, not scored — a grader hiccup must never cost the contestant points.
    Capped by MAX_ATTEMPTS so a genuinely broken submission eventually errors out.

The grading PIPELINE is tested in judge/test_challenge_grader.py with a local
runner; here we swap in the isolate-backed runner. Local dev on Windows: run in
WSL2/a Linux VM with `isolate` installed.

Box-id safety: each worker PROCESS must own a unique ISOLATE_BOX_ID (the sandbox
box is not shared-safe). Run one process per box id; to scale, start N processes
with ISOLATE_BOX_ID=0..N-1.
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
import time

import asyncpg

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "judge"))
from languages import get as get_language  # noqa: E402
from registry import load_problem  # noqa: E402
from sandbox import IsolateInternalError, Limits  # noqa: E402
if os.environ.get("DMPC_UNSAFE_NO_SANDBOX") == "1":      # TEST ONLY: run without isolate
    from local_runner import run_over_seeds  # noqa: E402
else:
    from sandbox_runner import run_over_seeds  # noqa: E402

DATABASE_URL = os.environ["DATABASE_URL"]
POLL_INTERVAL_S = float(os.environ.get("WORKER_POLL_S", "1.0"))
BOX_ID = int(os.environ.get("ISOLATE_BOX_ID", "0"))  # MUST be unique per worker process
# Re-queue a row stuck in an in-flight state longer than this (seconds). Should be
# comfortably above a worst-case grade (compile + all sample cases at full wall).
LEASE_TIMEOUT_S = float(os.environ.get("WORKER_LEASE_TIMEOUT_S", "300"))
RECOVERY_EVERY_S = float(os.environ.get("WORKER_RECOVERY_EVERY_S", "60"))
MAX_ATTEMPTS = int(os.environ.get("WORKER_MAX_ATTEMPTS", "5"))  # cap INTERNAL re-queues

CLAIM_SQL = """
UPDATE submissions
   SET state = 'compiling', claimed_at = now(), attempts = attempts + 1
WHERE id = (
    SELECT id FROM submissions WHERE state = 'queued'
    ORDER BY created_at FOR UPDATE SKIP LOCKED LIMIT 1
)
RETURNING id, problem_id, user_id, language_id, source_key, source_text, data_bin, attempts;
"""

# Recover rows whose worker died mid-grade: in-flight past the lease timeout -> queued.
RECOVER_SQL = """
UPDATE submissions
   SET state = 'queued', claimed_at = NULL
 WHERE state IN ('compiling', 'sample_running')
   AND claimed_at IS NOT NULL
   AND claimed_at < now() - ($1 || ' seconds')::interval
RETURNING id;
"""


class ConfigError(Exception):
    """Problem package is misconfigured (e.g. no sample seeds) — fail loudly."""


def sample_seeds(problem) -> list[int]:
    m = problem.META
    seeds = m.get("sample_seeds") or m.get("given_seeds")
    if not seeds:
        raise ConfigError(
            f"problem {getattr(problem, '__name__', '?')} has no sample_seeds/given_seeds in META"
        )
    return list(seeds)


def _run_in_sandbox(problem, lang, source: bytes, seeds: list[int], base: Limits,
                    data_bin: bytes | None = None, gen_params: dict | None = None):
    """Sync: compile + run sample cases in a fresh isolate box (shared runner).
    Returns (outcomes | None, compiled_ok, compile_log); raises IsolateInternalError
    on sandbox/infra failure (the caller re-queues)."""
    return run_over_seeds(BOX_ID, problem, lang, source, seeds, base, data_bin, gen_params)


def _scoring_dict(v) -> dict:
    """scoring_config as a dict (asyncpg jsonb codec OR a JSON string)."""
    if isinstance(v, str):
        try:
            return json.loads(v)
        except (ValueError, TypeError):
            return {}
    return v or {}


async def load_source(sub) -> bytes:
    if sub["source_text"]:
        return sub["source_text"].encode()
    raise NotImplementedError("object-storage fetch (source_key) not configured")


async def _requeue(conn, sub, reason: str) -> None:
    """Put a submission back on the queue (infra flake) unless it has exhausted its
    attempts, in which case mark it errored so it can't loop forever."""
    if sub["attempts"] >= MAX_ATTEMPTS:
        print(f"[worker] {sub['id']} exhausted {MAX_ATTEMPTS} attempts ({reason}); marking errored")
        await conn.execute(
            "UPDATE submissions SET state='errored', claimed_at=NULL WHERE id=$1", sub["id"]
        )
    else:
        print(f"[worker] re-queue {sub['id']} (attempt {sub['attempts']}, {reason})")
        await conn.execute(
            "UPDATE submissions SET state='queued', claimed_at=NULL WHERE id=$1", sub["id"]
        )


async def grade_submission(conn: asyncpg.Connection, sub) -> None:
    prob = await conn.fetchrow(
        "SELECT problem_key, time_limit_ms, memory_limit_mb, scoring_config FROM problems WHERE id=$1",
        sub["problem_id"],
    )
    problem = load_problem(prob["problem_key"])
    lang = get_language(sub["language_id"])
    source = await load_source(sub)
    base = Limits(time_ms=prob["time_limit_ms"], memory_mb=prob["memory_limit_mb"])
    seeds = sample_seeds(problem)
    gen_params = _scoring_dict(prob["scoring_config"]).get("gen_params")   # parametric problems

    outcomes, compiled_ok, compile_log = await asyncio.to_thread(
        _run_in_sandbox, problem, lang, source, seeds, base, sub["data_bin"], gen_params
    )
    if not compiled_ok:
        await conn.execute(
            "UPDATE submissions SET state='compile_error', claimed_at=NULL, compile_log=$2 WHERE id=$1",
            sub["id"], compile_log or None,
        )
        await _notify(conn, sub["user_id"], compile_error=True)
        return

    # (An isolate INTERNAL during the run raises IsolateInternalError out of
    # _run_in_sandbox -> caught in the main loop -> re-queued; never reaches here.)
    results = [{"seed": o.seed, "cost": o.cost, "valid": o.valid,
                "verdict": o.verdict, "runtime_ms": o.runtime_ms} for o in outcomes]
    cost_sum = sum(o.cost for o in outcomes if o.valid and o.cost is not None)
    await conn.execute(
        "UPDATE submissions SET state='sample_done', claimed_at=NULL, "
        "sample_score_sum=$2, sample_results=$3 WHERE id=$1",
        sub["id"], int(cost_sum), json.dumps(results),
    )
    await _notify(conn, sub["user_id"], cost_sum=int(cost_sum),
                  passed=sum(1 for o in outcomes if o.valid), total=len(outcomes))


async def _notify(conn, user_id, *, cost_sum: int = 0, passed: int = 0, total: int = 0,
                  compile_error: bool = False) -> None:
    payload = {"kind": "challenge", "compile_error": compile_error,
               "cost_sum": cost_sum, "passed": passed, "total": total}
    await conn.execute(
        "INSERT INTO notifications (user_id, type, payload) VALUES ($1,'grading_done',$2)",
        user_id, json.dumps(payload),
    )


async def recover_stuck(pool) -> None:
    """Re-queue submissions abandoned by a crashed/killed worker."""
    async with pool.acquire() as conn:
        rows = await conn.fetch(RECOVER_SQL, str(int(LEASE_TIMEOUT_S)))
    if rows:
        print(f"[worker] recovered {len(rows)} stuck submission(s) -> queued")


async def main() -> None:
    pool = await asyncpg.create_pool(DATABASE_URL, min_size=1, max_size=4)
    print(f"[worker box={BOX_ID}] draining queue every {POLL_INTERVAL_S}s "
          f"(lease {LEASE_TIMEOUT_S:.0f}s, max attempts {MAX_ATTEMPTS})")
    await recover_stuck(pool)            # sweep on startup (recover our own prior crash)
    last_recovery = time.monotonic()
    while True:
        # periodic recovery sweep on a REAL clock — under load the loop grades without
        # sleeping, so a loop-count proxy would drift the cadence ~10x; use wall time.
        if time.monotonic() - last_recovery >= RECOVERY_EVERY_S:
            await recover_stuck(pool)
            last_recovery = time.monotonic()

        async with pool.acquire() as conn:
            async with conn.transaction():
                sub = await conn.fetchrow(CLAIM_SQL)
            if sub is None:
                await asyncio.sleep(POLL_INTERVAL_S)
                continue
            try:
                await grade_submission(conn, sub)
            except IsolateInternalError as e:
                await _requeue(conn, sub, f"isolate infra failure: {e}")
                await asyncio.sleep(POLL_INTERVAL_S)
            except (NotImplementedError, ConfigError) as e:
                # not-configured / bad package: re-queue won't help. Error it out.
                print(f"[worker] config error on {sub['id']}: {e}")
                await conn.execute(
                    "UPDATE submissions SET state='errored', claimed_at=NULL WHERE id=$1", sub["id"]
                )
            except Exception as e:  # noqa: BLE001
                print(f"[worker] error grading {sub['id']}: {e}")
                await conn.execute(
                    "UPDATE submissions SET state='errored', claimed_at=NULL WHERE id=$1", sub["id"]
                )


async def drain_once(max_grade: int = 500) -> int:
    """Grade every currently-queued submission, then exit — the one-shot mode for
    EPHEMERAL runners (GitHub Actions). Recovers stuck rows first; bounded by
    `max_grade` so a flood (or a flaky re-queue) can't run past the job's time budget."""
    pool = await asyncpg.create_pool(DATABASE_URL, min_size=1, max_size=4)
    try:
        await recover_stuck(pool)
        graded = 0
        while graded < max_grade:
            async with pool.acquire() as conn:
                async with conn.transaction():
                    sub = await conn.fetchrow(CLAIM_SQL)
                if sub is None:
                    break                     # queue drained
                try:
                    await grade_submission(conn, sub)
                    graded += 1
                except IsolateInternalError as e:
                    await _requeue(conn, sub, f"isolate infra failure: {e}")
                except (NotImplementedError, ConfigError) as e:
                    print(f"[worker] config error on {sub['id']}: {e}")
                    await conn.execute("UPDATE submissions SET state='errored', claimed_at=NULL WHERE id=$1", sub["id"])
                except Exception as e:  # noqa: BLE001
                    print(f"[worker] error grading {sub['id']}: {e}")
                    await conn.execute("UPDATE submissions SET state='errored', claimed_at=NULL WHERE id=$1", sub["id"])
        print(f"[worker] drain complete: graded {graded} submission(s)")
        return graded
    finally:
        await pool.close()


if __name__ == "__main__":
    if "--once" in sys.argv or os.environ.get("DMPC_WORKER_ONCE") == "1":
        asyncio.run(drain_once())
    else:
        asyncio.run(main())
