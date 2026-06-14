"""
Evaluation-round evaluator — grades ONE evaluation_rounds row to standings.

Runs on the grader host (Linux + isolate + DB). For a due round it:
  1. CLAIMS the round atomically (status pending/failed -> generating) so a re-fired
     scheduler / catch-up run is a no-op, not a double grade.
  2. Derives the round's HIDDEN Challenge seeds deterministically from a server secret
     + the STORED idem_key (eval_round.derive_seeds), so a re-run regenerates the exact
     same seeds.
  3. Selects each contestant's REPRESENTATIVE Challenge submission and Step Up scores
     AS OF round.scheduled_at (created_at <= scheduled_at) — so a late/catch-up run
     reproduces the field exactly as it stood at the scheduled instant.
  4. Runs each rep over the hidden seeds in the sandbox (INJECTED runner, so the
     orchestration is unit-tested without isolate). An isolate INTERNAL is infra, not
     a contestant loss — and because relative scoring shares n_total across the field,
     one flake would corrupt EVERY score on that seed, so we ABORT the round (status
     'failed') and let it be re-run, never recording an infra None.
  5. Assembles results via judge/round_scoring.score_round (pure) and writes
     case_results + standings + per-user notifications + status='done' in ONE
     transaction (delete-by-round_id then insert, so a changed representative on
     re-run doesn't leave stale rows).

The pure scoring lives in judge/round_scoring.py; this module is the DB+sandbox glue.
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
import uuid

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "judge"))
from eval_round import derive_seeds  # noqa: E402
from languages import get as get_language  # noqa: E402
from registry import load_problem  # noqa: E402
from round_scoring import CaseRaw, score_round  # noqa: E402
from sandbox import IsolateInternalError, Limits  # noqa: E402
from sandbox_runner import run_over_seeds  # noqa: E402

BOX_ID = int(os.environ.get("ISOLATE_BOX_ID", "0"))
# Unpredictable-but-reproducible hidden seeds need a server secret (NOT the API
# cookie-signing key — different trust tier). Fail loud if a Challenge round needs it.
EVAL_SEED_SECRET = os.environ.get("EVAL_SEED_SECRET")
DEFAULT_ROUND_SEEDS = int(os.environ.get("DMPC_ROUND_SEEDS", "20"))
ROUND_REP_RETRIES = int(os.environ.get("DMPC_ROUND_REP_RETRIES", "3"))
# Hard ceiling on grade attempts per round so a persistently-failing round drops out of
# the scheduler's rotation (it gates the due query on this) instead of looping forever.
MAX_ROUND_ATTEMPTS = int(os.environ.get("DMPC_ROUND_MAX_ATTEMPTS", "8"))

# sandbox verdict string -> case_verdict enum value
_VERDICT_ENUM = {
    "OK": "ok", "TLE": "tle", "MLE": "mle", "RE": "re",
    "CE": "compile_error", "COMPILE_ERROR": "compile_error", "ILLEGAL": "illegal",
}


class RoundConfigError(Exception):
    """The round/problem is misconfigured (bad seed_range, missing secret) — fail
    the round loudly rather than score on garbage."""


# atomic claim: only the runner that flips pending/failed -> generating proceeds, and
# stamps an owner token ($2) so terminal writes can prove they still hold the lease.
CLAIM_SQL = """
UPDATE evaluation_rounds
   SET status='generating', claimed_at=now(), claimed_by=$2, attempts=attempts+1
 WHERE id=$1 AND status IN ('pending','failed')
RETURNING id, contest_id, type, idem_key, scheduled_at, attempts;
"""

# Representative = each user's LATEST Challenge submission AS OF the round time.
# NOTE: assumes free-tier INLINE storage (source_text set). If object-storage mode
# (source_key) is ever enabled, this predicate would silently DROP those reps from the
# relative field (deflating n_total) — update this query AND the source fetch together.
REPS_SQL = """
SELECT DISTINCT ON (user_id) id, user_id, language_id, source_text, data_bin
  FROM submissions
 WHERE problem_id=$1 AND created_at <= $2 AND source_text IS NOT NULL
 ORDER BY user_id, created_at DESC, id DESC;
"""

STEPUP_SQL = """
SELECT user_id, mission_seed, max(score) AS best
  FROM stepup_submissions
 WHERE problem_id=$1 AND created_at <= $2
 GROUP BY user_id, mission_seed;
"""


def _challenge_config(scoring_config: dict) -> tuple[int, int, int, float]:
    """(lo, hi, k, cost_eps) for a Challenge problem from its scoring_config jsonb."""
    cfg = scoring_config or {}
    rng = cfg.get("seed_range")
    if not rng or len(rng) != 2:
        raise RoundConfigError("challenge problem missing scoring_config.seed_range [lo,hi]")
    lo, hi = int(rng[0]), int(rng[1])
    k = int(cfg.get("round_seeds", DEFAULT_ROUND_SEEDS))
    eps = float(cfg.get("cost_eps", 0.0))
    return lo, hi, k, eps


def _challenge_subtasks(scoring_config: dict):
    """Author-defined Challenge subtasks `[{name, gen_params, num_seeds, budget}]`, or
    None for the legacy single-pool path. Each subtask is its own relative field + budget."""
    subs = (scoring_config or {}).get("challenge_subtasks")
    return subs if subs else None


def _grade_rep_default(problem_key: str, language_id: str, source: str,
                       seeds: list[int], limits: Limits, data_bin: bytes | None = None,
                       gen_params: dict | None = None):
    """Production rep grader: compile+run the source (+ optional data.bin) over `seeds`
    in isolate. Returns (outcomes | None, compiled_ok, compile_log); raises IsolateInternalError."""
    problem = load_problem(problem_key)
    lang = get_language(language_id)
    return run_over_seeds(BOX_ID, problem, lang, source.encode(), seeds, limits, data_bin, gen_params)


def _grade_rep_with_retry(grade_rep, *args):
    """Retry a rep's whole seed-run on transient isolate INTERNAL; give up after N."""
    last = None
    for _ in range(max(1, ROUND_REP_RETRIES)):
        try:
            return grade_rep(*args)
        except IsolateInternalError as e:  # infra flake — fresh box next try
            last = e
    raise last if last else IsolateInternalError("rep grading failed")


def _append_rep_cases(case_raws, pid, rep, sid, seeds, outcomes, compiled_ok):
    """Append one rep's per-seed CaseRaws (compile-fail -> CE on every seed)."""
    if not compiled_ok:
        for s in seeds:
            case_raws.append(CaseRaw(pid, str(rep["user_id"]), sid, s, None, "CE"))
        return
    for o in outcomes:
        case_raws.append(CaseRaw(pid, str(rep["user_id"]), sid, o.seed, o.cost,
                                 o.verdict, runtime_ms=o.runtime_ms))


async def _challenge_case_raws(conn, round_row, problems, *, grade_rep, secret):
    """Run every rep of every Challenge problem over its hidden seeds. Returns
    (case_raws, seeds_by_problem, eps_by_problem, subtasks_by_problem). Raises on infra/config.

    Author-defined subtasks: derive ALL seeds for the problem ONCE then PARTITION them
    in order into subtasks — distinct seeds (no cross-subtask collision) with a
    deterministic, recomputable seed->subtask membership — and run each partition with
    its own feature ranges (gen_params). Each subtask is scored as its own relative field."""
    case_raws: list[CaseRaw] = []
    seeds_by_problem: dict[str, list[int]] = {}
    eps_by_problem: dict[str, float] = {}
    subtasks_by_problem: dict[str, list[dict]] = {}
    scheduled_at = round_row["scheduled_at"]
    if not secret:
        raise RoundConfigError("EVAL_SEED_SECRET is required to grade a Challenge round")

    for p in problems:
        if p["kind"] != "challenge":
            continue
        pid = str(p["id"])
        cfg = _as_dict(p["scoring_config"])
        subs = _challenge_subtasks(cfg)
        base = Limits(time_ms=p["time_limit_ms"], memory_mb=p["memory_limit_mb"])
        reps = await conn.fetch(REPS_SQL, p["id"], scheduled_at)

        if subs:
            # Each subtask is a fixed-feature PART (exact N/M/dust) with its own seed RANGE;
            # ONE fresh seed is drawn per round from that range (different each eval, but
            # reproducible). Seeds are kept distinct ACROSS parts so case_results
            # (UNIQUE round,submission,seed) never collides on overlapping ranges.
            used: set[int] = set()
            groups = []
            for i, st in enumerate(subs):
                lo, hi = int(st.get("seed_lo", 0)), int(st.get("seed_hi", 0))
                cands = derive_seeds(secret, round_row["idem_key"], f"{p['problem_key']}#{i}", 64, lo, hi)
                pick = next((s for s in cands if s not in used), None)
                if pick is None:
                    raise RoundConfigError(f"challenge subtask {i} seed range [{lo},{hi}] exhausted (overlap)")
                used.add(pick)
                groups.append({"seeds": [pick], "budget": int(st["budget"]),
                               "eps": float(st.get("cost_eps", 0.0)), "gen_params": st.get("features")})
            seeds_by_problem[pid] = [g["seeds"][0] for g in groups]
            subtasks_by_problem[pid] = [{"seeds": g["seeds"], "budget": g["budget"], "eps": g["eps"]} for g in groups]
            for rep in reps:
                sid = str(rep["id"])
                for g in groups:
                    # one compile+run per subtask (each has its own fixed features) —
                    # correct over efficient; a handful of subtasks per problem is fine.
                    outcomes, compiled_ok, _log = await asyncio.to_thread(
                        _grade_rep_with_retry, grade_rep, p["problem_key"],
                        rep["language_id"], rep["source_text"], g["seeds"], base, rep["data_bin"], g["gen_params"],
                    )
                    _append_rep_cases(case_raws, pid, rep, sid, g["seeds"], outcomes, compiled_ok)
            continue

        # ---- legacy single-pool path ----
        lo, hi, k, eps = _challenge_config(cfg)
        gen_params = cfg.get("gen_params")        # parametric (authored) problems
        seeds = derive_seeds(secret, round_row["idem_key"], p["problem_key"], k, lo, hi)
        if len(seeds) < k:
            raise RoundConfigError(
                f"seed_range [{lo},{hi}] too small for {k} distinct round seeds"
            )
        seeds_by_problem[pid] = seeds
        eps_by_problem[pid] = eps
        for rep in reps:
            sid = str(rep["id"])
            # offload the blocking isolate compile+run so the event loop (scheduler)
            # stays responsive; the retry loop runs inside the worker thread.
            outcomes, compiled_ok, _log = await asyncio.to_thread(
                _grade_rep_with_retry, grade_rep, p["problem_key"],
                rep["language_id"], rep["source_text"], seeds, base, rep["data_bin"], gen_params,
            )
            _append_rep_cases(case_raws, pid, rep, sid, seeds, outcomes, compiled_ok)
    return case_raws, seeds_by_problem, eps_by_problem, subtasks_by_problem


async def _stepup_by_user(conn, round_row, problems) -> dict[str, int]:
    """Per-user Step Up total AS OF scheduled_at = sum of best-per-mission across all
    Step Up problems. Includes Step-Up-only users (they must still rank)."""
    scheduled_at = round_row["scheduled_at"]
    totals: dict[str, int] = {}
    for p in problems:
        if p["kind"] != "stepup":
            continue
        rows = await conn.fetch(STEPUP_SQL, p["id"], scheduled_at)
        # best per (user, mission) -> sum per user for this problem -> add to total.
        for r in rows:
            u = str(r["user_id"])
            totals[u] = totals.get(u, 0) + int(r["best"])
    return totals


def _verdict_enum(v: str) -> str:
    """Map a sandbox verdict to the case_verdict enum. Fail LOUD on an unmapped value
    rather than coercing to the reserved 'internal' (which must mean infra-abort/no-row)."""
    try:
        return _VERDICT_ENUM[v]
    except KeyError:
        raise RoundConfigError(f"unmapped verdict {v!r}") from None


async def _fail_round(conn, round_id: str, token: str, e: Exception, *, terminal: bool):
    """Mark a round failed (claimed_at cleared so the lease frees) with an operator
    reason. terminal=True burns the attempts budget so a DETERMINISTIC failure (config
    error / unmapped verdict) drops out of the scheduler rotation immediately, instead
    of re-grading the whole field every tick. Guarded on the owner token so a grader
    whose lease was stolen can't stomp a round another grader now owns."""
    reason = f"{type(e).__name__}: {e}"
    if terminal:
        await conn.execute(
            "UPDATE evaluation_rounds SET status='failed', claimed_at=NULL, "
            "attempts=$3, error=$2 WHERE id=$1 AND claimed_by=$4",
            round_id, reason, MAX_ROUND_ATTEMPTS, token,
        )
    else:
        await conn.execute(
            "UPDATE evaluation_rounds SET status='failed', claimed_at=NULL, "
            "error=$2 WHERE id=$1 AND claimed_by=$3",
            round_id, reason, token,
        )


async def evaluate_round(conn, round_id: str, *, grade_rep=_grade_rep_default,
                         secret: str | None = None) -> str:
    """Grade one evaluation round to standings. Idempotent: a re-run reproduces the
    same seeds/field and overwrites the round's rows in place. Returns a status string
    ('done' | 'noop' | 'failed')."""
    if secret is None:
        secret = EVAL_SEED_SECRET

    token = str(uuid.uuid4())                 # owner token for this grade attempt
    rnd = await conn.fetchrow(CLAIM_SQL, round_id, token)
    if rnd is None:
        return "noop"          # already owned by another runner, or already done

    try:
        problems = await conn.fetch(
            """SELECT id, kind, problem_key, time_limit_ms, memory_limit_mb, scoring_config
                 FROM problems WHERE contest_id=$1""",
            rnd["contest_id"],
        )

        # ---- sandbox phase (NO open DB transaction across isolate) ----
        case_raws, seeds_by_problem, eps_by_problem, subtasks_by_problem = await _challenge_case_raws(
            conn, rnd, problems, grade_rep=grade_rep, secret=secret
        )
        stepup = await _stepup_by_user(conn, rnd, problems)

        contest = await conn.fetchrow(
            "SELECT challenge_budget FROM contests WHERE id=$1", rnd["contest_id"]
        )
        budget = int(contest["challenge_budget"]) if contest else 1000000
        result = score_round(case_raws, seeds_by_problem, stepup,
                             challenge_budget=budget, eps_by_problem=eps_by_problem,
                             subtasks_by_problem=subtasks_by_problem)
        is_final = rnd["type"] == "final"

        # ---- single write transaction: replace this round's rows atomically ----
        async with conn.transaction():
            await conn.execute("DELETE FROM case_results WHERE round_id=$1", round_id)
            await conn.execute("DELETE FROM standings WHERE round_id=$1", round_id)

            for c in result.cases:
                # memory_kb is intentionally left NULL: the injected runner contract
                # (challenge_grader.SourceRunner) carries no per-case memory today.
                # Display-only (not part of cost); thread it through the runner if needed.
                await conn.execute(
                    """INSERT INTO case_results
                         (round_id, submission_id, problem_id, user_id, seed, verdict,
                          raw_cost, runtime_ms, case_score, case_rank)
                       VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10)""",
                    round_id, c.submission_id, c.problem_id, c.user_id, c.seed,
                    _verdict_enum(c.verdict), c.cost, c.runtime_ms,
                    c.case_score, c.case_rank,
                )

            for s in result.standings:
                await conn.execute(
                    """INSERT INTO standings
                         (round_id, contest_id, user_id, stepup_score, challenge_score,
                          total_score, rank)
                       VALUES ($1,$2,$3,$4,$5,$6,$7)""",
                    round_id, rnd["contest_id"], s.user_id, s.stepup, s.challenge,
                    s.total, s.rank,
                )
                await conn.execute(
                    "INSERT INTO notifications (user_id, type, payload) VALUES ($1,'round_published',$2)",
                    s.user_id, json.dumps({
                        "round_id": round_id, "type": rnd["type"], "rank": s.rank,
                        "total_score": s.total, "stepup_score": s.stepup,
                        "challenge_score": s.challenge,
                    }),
                )
                if is_final:
                    await conn.execute(
                        "INSERT INTO notifications (user_id, type, payload) VALUES ($1,'contest_ended',$2)",
                        s.user_id, json.dumps({
                            "contest_id": str(rnd["contest_id"]),
                            "final_rank": s.rank, "total_score": s.total,
                        }),
                    )

            # Commit only if WE still own the lease — a stolen-lease grader (its round
            # was lease-recovered and re-claimed) finds 0 rows and the txn rolls back.
            res = await conn.execute(
                "UPDATE evaluation_rounds SET status='done', claimed_at=NULL, "
                "claimed_by=NULL, error=NULL, published_at=now() "
                "WHERE id=$1 AND status='generating' AND claimed_by=$2",
                round_id, token,
            )
            if isinstance(res, str) and res.endswith(" 0"):
                raise RoundConfigError("lost lease before publish (round re-claimed)")
        return "done"
    except RoundConfigError as e:
        # deterministic (bad config / unmapped verdict / lost lease) -> don't retry.
        await _fail_round(conn, round_id, token, e, terminal=True)
        return "failed"
    except Exception as e:  # noqa: BLE001 — infra/transient (isolate, asyncpg, bad pkg)
        # bounded retry: stays re-claimable until attempts hits MAX_ROUND_ATTEMPTS.
        await _fail_round(conn, round_id, token, e, terminal=False)
        return "failed"


def _as_dict(v) -> dict:
    """scoring_config may arrive as a jsonb dict (asyncpg) or a JSON string."""
    if v is None:
        return {}
    if isinstance(v, str):
        return json.loads(v) if v.strip() else {}
    return dict(v)
