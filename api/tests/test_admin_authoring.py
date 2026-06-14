"""Admin authoring — request validation + scoring_config payloads (pure parts).
The INSERT itself needs a DB; here we test everything testable without one, including
that the authored seeds flow through effective_meta to the grader.

Run:  python api/tests/test_admin_authoring.py
"""

from __future__ import annotations

import os
import sys
import types

HERE = os.path.dirname(__file__)
sys.path.insert(0, os.path.join(HERE, ".."))                       # import app.*

from fastapi import HTTPException  # noqa: E402

from app.routers.admin import (  # noqa: E402
    ChallengeSpec, CreateContestIn, GenParams, StepUpSpec, problem_configs, validate_create,
)


def _body(**kw) -> CreateContestIn:
    base = dict(
        title="6월 모의고사 #X",            # problem_key defaults to "clean_robot"
        stepup=StepUpSpec(given_seeds=[101, 102, 103]),
        challenge=ChallengeSpec(seed_range=[1000, 2000]),
    )
    base.update(kw)
    return CreateContestIn(**base)


def _expect_400(body: CreateContestIn):
    try:
        validate_create(body)
    except HTTPException as e:
        assert e.status_code == 400, e.status_code
        return
    raise AssertionError("expected HTTPException(400)")


def test_valid_passes():
    validate_create(_body())          # must not raise


def test_unknown_template_rejected():
    _expect_400(_body(problem_key="evil_custom"))


def test_duplicate_seeds_rejected():
    _expect_400(_body(stepup=StepUpSpec(given_seeds=[1, 1, 2])))


def test_empty_seeds_rejected():
    _expect_400(_body(stepup=StepUpSpec(given_seeds=[])))


def test_negative_seed_rejected():
    _expect_400(_body(stepup=StepUpSpec(given_seeds=[-1, 2])))


def test_reversed_seed_range_rejected():
    _expect_400(_body(challenge=ChallengeSpec(seed_range=[2000, 1000])))


def test_bad_seed_range_length_rejected():
    _expect_400(_body(challenge=ChallengeSpec(seed_range=[5])))


def test_seed_range_above_cap_rejected():
    _expect_400(_body(challenge=ChallengeSpec(seed_range=[0, 20_000_000])))


def test_seed_range_too_small_for_round_seeds_rejected():
    # range of 3 seeds [1..3] cannot supply 6 distinct round seeds -> reject at creation
    _expect_400(_body(challenge=ChallengeSpec(seed_range=[1, 3], round_seeds=6)))


def test_seed_range_exactly_fits_round_seeds_ok():
    validate_create(_body(challenge=ChallengeSpec(seed_range=[1, 6], round_seeds=6)))


def test_problem_configs_shapes():
    su, ch = problem_configs(_body())
    assert su["given_seeds"] == [101, 102, 103] and su["stepup_budget"] == 1_000_000
    assert ch["seed_range"] == [1000, 2000]
    assert ch["round_seeds"] == 6 and ch["cost_eps"] == 0.0
    # gen_params (grid/dust ranges) ride on BOTH problems so the parametric generator runs
    assert su["gen_params"]["hMin"] == 6 and su["gen_params"]["dMax"] == 10
    assert ch["gen_params"] == su["gen_params"]


def test_gen_params_validation():
    _expect_400(_body(gen_params=GenParams(hMin=9, hMax=6)))           # min > max
    _expect_400(_body(gen_params=GenParams(hMin=1)))                   # below grid floor (2)
    _expect_400(_body(gen_params=GenParams(hMax=60, wMax=60)))         # above grid cap (50)
    _expect_400(_body(gen_params=GenParams(dMin=5, dMax=2)))           # dust min > max
    _expect_400(_body(gen_params=GenParams(hMax=4, wMax=4, dMax=16)))  # dust >= cells (16)


def test_default_template_is_parametric():
    assert _body().problem_key == "clean_robot"


def test_authored_seeds_flow_to_effective_meta():
    # the scoring_config the endpoint persists, merged over the module META, yields the
    # authored missions — closing the loop with judge/grader (gradeable authored contest).
    sys.path.insert(0, os.path.join(HERE, "..", "..", "judge"))
    from registry import effective_meta, load_problem  # noqa: E402
    su, _ = problem_configs(_body(stepup=StepUpSpec(given_seeds=[9, 8, 7])))
    eff = effective_meta(load_problem("example_clean").META, su)
    assert eff["given_seeds"] == [9, 8, 7]
    assert eff["stepup_budget"] == 1_000_000


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
