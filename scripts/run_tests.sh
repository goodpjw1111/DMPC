#!/usr/bin/env bash
# Run the full backend test suite (pure stdlib — no deps). Mirrors CI.
#   bash scripts/run_tests.sh
# NOTE: no `set -e` — we want to run ALL tests and report a single pass/fail,
# capturing each test's real exit status (a pipe would mask it; see below).
cd "$(dirname "$0")/.."

tests=(
  judge/test_scoring.py
  judge/test_calibration.py
  judge/test_grader.py
  judge/test_standings.py
  judge/test_eval_round.py
  judge/test_challenge_grader.py
  judge/test_round_scoring.py
  judge/test_authoring.py
  api/tests/test_oidc_claims.py
  api/tests/test_dev_login.py
  api/tests/test_admin_authoring.py
  api/tests/test_replays.py
  api/tests/test_registration.py
  api/tests/test_prod_config.py
  api/tests/test_nickname.py
  api/tests/test_schedule.py
  api/tests/test_grading_service.py
  api/tests/test_round_service.py
  worker/test_grade_round.py
  problems/example_clean/test_pipeline.py
  problems/clean_robot/test_gen.py
)

fail=0
for t in "${tests[@]}"; do
  printf '%-46s ' "$t"
  # Capture python's REAL exit status: a pipe (python | tail) would report tail's
  # status (always 0) and silently pass a broken test. Run, then show the summary.
  out="$(python "$t" 2>&1)"; rc=$?
  printf '%s\n' "$out" | tail -n 1
  [ "$rc" -ne 0 ] && { fail=1; printf '%s\n' "$out"; }   # on failure, dump full output
done

if python -m compileall -q judge worker api/app; then
  echo "compile OK"
else
  fail=1
fi

exit $fail
