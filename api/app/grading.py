"""
Step Up grading service (API side) — record an output submission and score it.

Step Up needs no sandbox: regenerate the mission input, run the trusted checker,
score, store, and notify. Reuses the tested judge core (registry + grader +
scoring). Kept free of FastAPI imports so the flow is unit-testable with a fake
connection (see api/tests/test_grading_service.py).
"""

from __future__ import annotations

import json
import os
import sys
from dataclasses import asdict, dataclass

# Reuse the framework-independent judge core.
_JUDGE = os.path.join(os.path.dirname(__file__), "..", "..", "judge")
if _JUDGE not in sys.path:
    sys.path.insert(0, _JUDGE)

from grader import grade_stepup_mission, stepup_problem_score  # noqa: E402
from registry import effective_meta, load_problem  # noqa: E402


def _as_dict(v) -> dict:
    """scoring_config arrives as a jsonb dict (asyncpg codec) or a JSON string."""
    if isinstance(v, str):
        try:
            return json.loads(v)
        except (ValueError, TypeError):
            return {}
    return v or {}


class SubmitError(Exception):
    """Bad request (e.g. unknown mission) — the router maps this to HTTP 400."""


@dataclass
class StepUpSubmitResult:
    submission_id: str
    mission_seed: int
    valid: bool
    cost: float | None
    score: int
    mission_budget: int
    ratio: float
    message: str


async def submit_stepup(conn, *, user_id: str, problem: dict, mission_seed: int,
                        output: str) -> StepUpSubmitResult:
    """Grade one Step Up output for one mission, persist it, and notify the user.

    `problem` is the DB row (needs id, problem_key). `conn` is an asyncpg-style
    connection exposing async fetchrow/execute.
    """
    mod = load_problem(problem["problem_key"])
    meta = effective_meta(mod.META, _as_dict(problem.get("scoring_config")))
    given = meta.get("given_seeds") or []
    if mission_seed not in given:
        raise SubmitError(f"mission seed {mission_seed} is not part of this problem")
    if not output or not output.strip():
        raise SubmitError("output is empty")

    r = grade_stepup_mission(mod, mission_seed, output, meta=meta)

    row = await conn.fetchrow(
        """
        INSERT INTO stepup_submissions
            (problem_id, user_id, mission_seed, output_text, cost, valid, score)
        VALUES ($1, $2, $3, $4, $5, $6, $7)
        RETURNING id
        """,
        problem["id"], user_id, mission_seed, output, r.cost, r.valid, r.score,
    )
    await conn.execute(
        "INSERT INTO notifications (user_id, type, payload) VALUES ($1, 'grading_done', $2)",
        user_id,
        json.dumps({"kind": "stepup", "mission_seed": mission_seed,
                    "score": r.score, "cost": r.cost, "valid": r.valid}),
    )
    return StepUpSubmitResult(
        submission_id=str(row["id"]), mission_seed=mission_seed, valid=r.valid,
        cost=r.cost, score=r.score, mission_budget=r.mission_budget,
        ratio=r.ratio, message=r.message,
    )


async def stepup_problem_score_for_user(conn, *, problem_id: str, user_id: str) -> int:
    """User's Step Up score for a problem = sum of their BEST score per mission."""
    rows = await conn.fetch(
        """
        SELECT mission_seed, max(score) AS best
        FROM stepup_submissions
        WHERE problem_id = $1 AND user_id = $2
        GROUP BY mission_seed
        """,
        problem_id, user_id,
    )
    return stepup_problem_score([r["best"] for r in rows])


def result_dict(r: StepUpSubmitResult) -> dict:
    return asdict(r)
