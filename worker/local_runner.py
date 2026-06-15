"""
UNSAFE no-sandbox runner — TEST ONLY (DMPC_UNSAFE_NO_SANDBOX=1).

Runs a submission as a plain subprocess with a wall-clock timeout, WITHOUT isolate.
A drop-in replacement for sandbox_runner.run_over_seeds so the sample worker and the
round evaluator can exercise the full pipeline (submit -> sample -> eval -> score) on
hosts where isolate can't run (e.g. GitHub Actions / cgroup-v2 quirks).

⚠️ SECURITY: there is NO sandbox — contestant code runs with the host's CPU/network.
We DO scrub the child environment (PATH/HOME/LANG only) so the worker's secrets
(DATABASE_URL, EVAL_SEED_SECRET, ...) are NOT visible to the program, which closes the
secret-exfiltration vector. But this is still unsafe for untrusted code: use it ONLY
to beta-test with YOUR OWN submissions on a throwaway runner — NEVER a real contest.
"""

from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "judge"))
from challenge_grader import grade_cases  # noqa: E402

# Minimal env handed to the child — deliberately omits the worker's secrets.
_CLEAN_ENV = {"PATH": os.environ.get("PATH", ""), "LANG": "C.UTF-8", "LC_ALL": "C.UTF-8"}

print("[local_runner] ⚠️ DMPC_UNSAFE_NO_SANDBOX active — running submissions WITHOUT "
      "isolate (test only; secrets scrubbed from the child env).", file=sys.stderr)


def _subst(argv, src: str, exe: str, mainclass: str) -> list[str]:
    return [a.replace("{src}", src).replace("{exe}", exe).replace("{mainclass}", mainclass) for a in argv]


def run_over_seeds(box_id, problem, lang, source: bytes, seeds, base,
                   data_bin=None, gen_params=None):
    """Same contract as sandbox_runner.run_over_seeds:
    returns (outcomes | None, compiled_ok, compile_log). No isolate."""
    src_name, exe, mainclass = lang.source_name, "main", "Main"
    workdir = tempfile.mkdtemp(prefix="dmpc_ns_")
    env = {**_CLEAN_ENV, "HOME": workdir}
    with open(os.path.join(workdir, src_name), "wb") as f:
        f.write(source)
    if data_bin:
        with open(os.path.join(workdir, "data.bin"), "wb") as f:
            f.write(data_bin)

    if lang.compile:
        cmd = _subst(lang.compile, src_name, exe, mainclass)
        try:
            cp = subprocess.run(cmd, cwd=workdir, env=env, capture_output=True,
                                timeout=max(1.0, lang.compile_time_limit_ms / 1000))
        except subprocess.TimeoutExpired:
            return None, False, "compile timed out"
        except FileNotFoundError as e:
            return None, False, f"toolchain not found: {e}"
        if cp.returncode != 0:
            return None, False, cp.stderr.decode("utf-8", "replace")[-4000:]

    run_cmd = _subst(lang.run, src_name, exe, mainclass)
    wall = max(1.0, base.time_ms / 1000) + 1.0          # a little headroom over the limit

    def runner(input_text: str):
        t0 = time.monotonic()
        try:
            cp = subprocess.run(run_cmd, cwd=workdir, env=env, input=input_text.encode(),
                                capture_output=True, timeout=wall)
        except subprocess.TimeoutExpired:
            return ("", int(wall * 1000), "TLE")
        except OSError:                       # missing interpreter/binary -> a run failure, not a crash
            return ("", 0, "RE")
        ms = int((time.monotonic() - t0) * 1000)
        if cp.returncode != 0:
            return ("", ms, "RE")
        return (cp.stdout.decode("utf-8", "replace"), ms, "OK")

    return grade_cases(problem, seeds, runner, gen_params), True, ""
