"""
UNSAFE no-sandbox runner — TEST ONLY (DMPC_UNSAFE_NO_SANDBOX=1).

Runs a submission as a plain subprocess with a wall-clock timeout, WITHOUT isolate.
A drop-in replacement for sandbox_runner.run_over_seeds so the sample worker and the
round evaluator can exercise the full pipeline (submit -> sample -> eval -> score) on
hosts where isolate can't run (e.g. GitHub Actions / cgroup-v2 quirks).

⚠️ SECURITY: there is NO sandbox — contestant code runs with the host's CPU and FULL
network, as the SAME OS user as this worker. Scrubbing the child's own environment
(below) is NOT a security boundary: a same-uid child can still read the PARENT worker's
secrets via /proc/<ppid>/environ and exfiltrate them over the network. Therefore this
runner MUST run only in a ZERO-SECRET environment — it refuses to start if a real
EVAL_SEED_SECRET / non-local DATABASE_URL is present (see _refuse_if_secrets_present).
Use it ONLY to beta-test YOUR OWN submissions on a throwaway, secret-less runner —
NEVER a real contest, and NEVER on a host that holds the grader's secrets.
"""

from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "judge"))
from challenge_grader import grade_cases  # noqa: E402

# Minimal env handed to the child. NOTE: this is hygiene, NOT isolation — see the module
# docstring. The real boundary is _refuse_if_secrets_present() + using isolate in prod.
_CLEAN_ENV = {"PATH": os.environ.get("PATH", ""), "LANG": "C.UTF-8", "LC_ALL": "C.UTF-8"}


def _refuse_if_secrets_present() -> None:
    """Fail CLOSED: the no-sandbox path must never coexist with real secrets, because an
    unsandboxed same-uid child can read them from /proc/<ppid>/environ. If a grader secret
    is present we abort loudly instead of silently exposing it to untrusted code."""
    db_url = os.environ.get("DATABASE_URL", "")
    has_real_db = bool(db_url) and "localhost" not in db_url and "127.0.0.1" not in db_url
    if os.environ.get("EVAL_SEED_SECRET") or has_real_db:
        raise RuntimeError(
            "DMPC_UNSAFE_NO_SANDBOX is set but real secrets (EVAL_SEED_SECRET / non-local "
            "DATABASE_URL) are present. The no-sandbox runner must run ONLY in a zero-secret "
            "environment — an unsandboxed same-uid child can read the parent's "
            "/proc/<ppid>/environ. Use isolate for any secrets-bearing grader."
        )


_refuse_if_secrets_present()
print("[local_runner] ⚠️ DMPC_UNSAFE_NO_SANDBOX active — running submissions WITHOUT "
      "isolate (test only; NOT a security boundary — see module docstring).", file=sys.stderr)


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
