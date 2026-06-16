"""
Sandboxed execution primitive — reference implementation over `isolate`.

THIS RUNS ON LINUX ONLY (isolate is a Linux/cgroups tool) and is intended to run
on a DEDICATED GRADER HOST, never the web tier. The recommended deployment runs
this inside a microVM (Firecracker via Kata) or gVisor so the kernel itself is
not the security boundary; isolate then enforces the fine-grained per-run limits.

Hard rules encoded here (every one is a DoS hole if dropped):
  * no network         -> isolate runs in an isolated net namespace by default
                          (we never pass --share-net)
  * CPU *and* wall time -> --time and --wall-time both; wall catches sleeps/hangs
  * memory             -> cgroups v2 --cg-mem (counts the whole process tree)
  * process/thread cap -> --processes (fork-bomb defense)
  * output/file size   -> --fsize; captured stdout is also length-capped
  * open files / stack -> --open-files, --stack
  * read-only FS       -> isolate's default; only the box dir + /tmp are writable
  * compilation is untrusted too -> compiled in the same sandbox with its own
                          time/mem/output limits.

The web tier NEVER imports or calls this. A worker process on the grader host
pulls a job from the queue, calls `grade()`, and writes results back through a
narrow results channel.
"""

from __future__ import annotations

import os
import shlex
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

from calibration import effective_time_ms, load_factor
from languages import Language

ISOLATE = os.environ.get("ISOLATE_BIN", "isolate")
# cgroup v2 needs a delegated control group that GitHub-hosted runners don't grant, so
# `isolate --cg --init` fails there ("isolate init failed"). DMPC_ISOLATE_NO_CG=1 drops --cg
# and limits memory with the --mem rlimit (RLIMIT_AS) instead of --cg-mem. The SECURITY
# boundary is UNCHANGED — isolate still runs the code in fresh PID/network/mount namespaces,
# so the sandboxed program cannot read the parent worker's /proc/<ppid>/environ (the secrets)
# nor reach the network. Only memory accounting changes (RLIMIT_AS address space vs cgroup RSS).
USE_CG = os.environ.get("DMPC_ISOLATE_NO_CG") != "1"

# Per-host time scaling vs the NYPC c7a reference (DMPC_CALIBRATION_FACTOR env).
# 1.0 = no scaling (assume this host == c7a). See judge/calibration.py.
CALIBRATION_FACTOR = load_factor()

# Time-limit safety margin in the production run path. The calibration factor is a
# noisy median estimate of host-vs-c7a speed; this margin keeps a borderline-accepted
# solution from being unfairly TLE'd when the host runs a touch slower than measured.
# Single reviewed constant (was silently 1.0; calibration.effective_time_ms documents 1.05).
RUN_SAFETY_MARGIN = float(os.environ.get("DMPC_RUN_SAFETY", "1.05"))

# Hard host-side wall guard so a hung/zombied isolate invocation can't block the
# worker forever (isolate's own --wall-time is per-run; this kills isolate itself).
# Seconds added on top of the run's wall budget before we SIGKILL the subprocess.
ISOLATE_KILL_GRACE_S = float(os.environ.get("DMPC_ISOLATE_KILL_GRACE_S", "10"))


class IsolateInternalError(RuntimeError):
    """isolate itself failed/hung (not the user's code) — the worker should
    re-queue, not mark the submission errored or penalize the contestant."""


# --- result types ----------------------------------------------------------

# isolate `status` field meanings (empty status = OK):
#   RE = died on a non-zero exit / runtime error
#   SG = killed by a signal (often = memory/limit kill)
#   TO = timed out (CPU or wall)
#   XX = internal isolate error
class Verdict:
    OK = "OK"
    TLE = "TLE"               # time limit exceeded (CPU or wall)
    MLE = "MLE"               # memory limit exceeded
    RE = "RE"                 # runtime error / non-zero exit / signal
    COMPILE_ERROR = "CE"
    ILLEGAL = "ILLEGAL"       # output rejected by the checker (set by caller)
    INTERNAL = "INTERNAL"     # sandbox/infra failure — re-queue, don't penalize


@dataclass
class RunResult:
    verdict: str
    exit_code: int | None = None
    time_ms: int = 0          # CPU time
    wall_ms: int = 0
    memory_kb: int = 0
    stdout: bytes = b""
    stderr_tail: str = ""     # last few KB of stderr, for compile errors etc.
    message: str = ""         # isolate's human-readable message
    meta: dict[str, str] = field(default_factory=dict)


@dataclass
class Limits:
    # NYPC reference environment: 2s CPU / 1024MB. time_ms is the c7a-equivalent
    # CPU budget; the grader multiplies it by CALIBRATION_FACTOR for slower hosts
    # (see judge/calibration.py) so the *computational* budget stays fair.
    time_ms: int = 2000       # CPU time per run (c7a-equivalent)
    wall_ms: int = 0          # 0 -> derived as 2x time + 1s
    memory_mb: int = 1024
    processes: int = 1        # bump for JVM/.NET which spawn threads/helpers
    fsize_kb: int = 65_536    # max bytes any single file may grow to
    open_files: int = 64
    stack_kb: int = 65_536
    stdout_cap_bytes: int = 8 * 1024 * 1024

    def wall(self) -> int:
        return self.wall_ms if self.wall_ms > 0 else self.time_ms * 2 + 1000


# --- the isolate box context ----------------------------------------------

class IsolateBox:
    """Owns one isolate box id for the duration of a grade. Use as a context
    manager so the box is always cleaned up even on error."""

    def __init__(self, box_id: int):
        self.box_id = box_id
        self.path: Path | None = None

    def __enter__(self) -> "IsolateBox":
        out = self._isolate("--init")
        # `isolate --init` prints the box root path on stdout.
        self.path = Path(out.strip()) / "box"
        return self

    def __exit__(self, *exc) -> None:
        try:
            self._isolate("--cleanup")
        except Exception:
            pass  # best-effort; a stuck box must not mask the real error

    def _isolate(self, *args: str) -> str:
        cmd = [ISOLATE, *(["--cg"] if USE_CG else []), f"--box-id={self.box_id}", *args]
        try:
            proc = subprocess.run(cmd, capture_output=True, text=True,
                                  timeout=ISOLATE_KILL_GRACE_S * 3)
        except subprocess.TimeoutExpired as e:
            # init/cleanup hung — surface as an infra failure the worker can re-queue.
            raise IsolateInternalError(f"isolate {' '.join(args)} timed out") from e
        if proc.returncode != 0 and "--run" not in args:
            raise RuntimeError(f"isolate {' '.join(args)} failed: {proc.stderr}")
        return proc.stdout

    # -- write a file into the box ----------------------------------------
    def put(self, name: str, data: bytes) -> None:
        assert self.path is not None
        (self.path / name).write_bytes(data)

    # -- run one command under limits -------------------------------------
    def run(
        self,
        argv: list[str],
        limits: Limits,
        *,
        stdin: bytes = b"",
        env: dict[str, str] | None = None,
    ) -> RunResult:
        assert self.path is not None
        meta_path = self.path.parent / "meta.txt"
        (self.path / "_stdin").write_bytes(stdin)

        flags = [
            *(["--cg"] if USE_CG else []),
            f"--box-id={self.box_id}",
            f"--meta={meta_path}",
            f"--time={limits.time_ms / 1000:.3f}",
            f"--wall-time={limits.wall() / 1000:.3f}",
            "--extra-time=0.5",
            # cgroup RSS limit when available; else the RLIMIT_AS address-space limit.
            (f"--cg-mem={limits.memory_mb * 1024}" if USE_CG else f"--mem={limits.memory_mb * 1024}"),  # KB
            f"--processes={limits.processes}",
            f"--fsize={limits.fsize_kb}",
            f"--open-files={limits.open_files}",
            f"--stack={limits.stack_kb}",
            "--stdin=_stdin",
            "--stdout=_stdout",
            "--stderr=_stderr",
            # NOTE: no --share-net  => network namespace is isolated (no egress)
        ]
        for k, v in (env or {}).items():
            flags.append(f"--env={k}={v}")
        # minimal, locale-stable environment
        flags += ["--env=PATH=/usr/local/bin:/usr/bin:/bin", "--env=HOME=/box"]

        cmd = [ISOLATE, *flags, "--run", "--", *argv]
        try:
            # isolate enforces --wall-time internally; this is a backstop in case
            # isolate itself wedges. Kill grace beyond the run's wall budget.
            proc = subprocess.run(cmd, capture_output=True,  # exit code reflected in meta
                                  timeout=limits.wall() / 1000 + ISOLATE_KILL_GRACE_S)
        except subprocess.TimeoutExpired as e:
            raise IsolateInternalError("isolate --run hung past wall budget") from e

        meta = _parse_meta(meta_path)
        stdout = _read_capped(self.path / "_stdout", limits.stdout_cap_bytes)
        stderr_tail = _read_capped(self.path / "_stderr", 16 * 1024).decode(
            "utf-8", "replace"
        )
        # isolate's OWN stderr (e.g. "Cannot ...", a cgroup/userns/mount error) — kept so an
        # INTERNAL failure surfaces WHY instead of a generic "isolate failure".
        isolate_err = (proc.stderr or b"").decode("utf-8", "replace").strip()
        return _classify(meta, stdout, stderr_tail, limits, isolate_err)


# --- helpers ---------------------------------------------------------------

def _parse_meta(path: Path) -> dict[str, str]:
    meta: dict[str, str] = {}
    try:
        for line in path.read_text().splitlines():
            if ":" in line:
                k, _, v = line.partition(":")
                meta[k.strip()] = v.strip()
    except FileNotFoundError:
        pass
    return meta


def _read_capped(path: Path, cap: int) -> bytes:
    try:
        with open(path, "rb") as f:
            return f.read(cap)
    except FileNotFoundError:
        return b""


def _classify(meta: dict, stdout: bytes, stderr_tail: str, limits: Limits,
              isolate_err: str = "") -> RunResult:
    status = meta.get("status", "")
    time_ms = int(float(meta.get("time", "0")) * 1000)
    wall_ms = int(float(meta.get("time-wall", "0")) * 1000)
    mem_kb = int(meta.get("cg-mem", meta.get("max-rss", "0")) or 0)
    exit_code = int(meta["exitcode"]) if meta.get("exitcode", "").isdigit() else None
    message = meta.get("message", "")

    if not meta or ("status" not in meta and "time" not in meta):
        # isolate wrote no usable meta (e.g. cgroup setup failed): treat as an
        # infra failure to re-queue, NOT a clean empty-output OK run (which the
        # checker would then mark ILLEGAL — a wrongful loss for the contestant).
        verdict = Verdict.INTERNAL
        if isolate_err and not message:
            message = isolate_err[-300:]    # surface isolate's own reason (XX/setup failure)
    elif status == "TO":
        verdict = Verdict.TLE
    elif status == "SG":
        # signal kill; OOM (cg-mem near the cap) -> MLE, otherwise RE. Use a small
        # tolerance band: the kernel often reports cg-mem a few KB under the hard
        # limit when it kills on the allocation that would exceed it.
        verdict = Verdict.MLE if mem_kb >= int(0.99 * limits.memory_mb * 1024) else Verdict.RE
    elif status == "RE":
        verdict = Verdict.RE
    elif status == "XX":
        verdict = Verdict.INTERNAL
    else:
        verdict = Verdict.OK

    return RunResult(
        verdict=verdict, exit_code=exit_code, time_ms=time_ms, wall_ms=wall_ms,
        memory_kb=mem_kb, stdout=stdout, stderr_tail=stderr_tail,
        message=message, meta=meta,
    )


def _subst(template: list[str], lang: Language) -> list[str]:
    exe = "main"
    mapping = {"src": lang.source_name, "exe": exe, "mainclass": "Main"}
    return [t.format(**mapping) for t in template]


# --- high-level grade flow -------------------------------------------------

@dataclass
class CaseInput:
    seed: int
    stdin: bytes


@dataclass
class CaseOutput:
    seed: int
    run: RunResult            # raw execution result
    # `cost`/`valid` are filled by the PROBLEM CHECKER (separate, trusted code
    # that reads stdin+stdout and computes the minimization cost). The sandbox
    # only guarantees safe execution + a verdict; it never trusts user output.


def compile_solution(box: IsolateBox, lang: Language, source: bytes) -> RunResult:
    box.put(lang.source_name, source)
    if lang.compile is None:
        return RunResult(verdict=Verdict.OK)
    res = box.run(
        _subst(lang.compile, lang),
        Limits(time_ms=lang.compile_time_limit_ms, memory_mb=1024,
               processes=max(16, lang.processes), fsize_kb=256 * 1024, open_files=512),
    )
    if res.verdict == Verdict.INTERNAL:
        return res                       # infra failure during compile -> re-queue, not CE
    if res.verdict != Verdict.OK or (res.exit_code not in (0, None)):
        res.verdict = Verdict.COMPILE_ERROR
    return res


def run_case(box: IsolateBox, lang: Language, case: CaseInput, base: Limits) -> RunResult:
    # CPU budget = base (c7a-equivalent) x language multiplier x host calibration.
    lang_scaled = int(base.time_ms * lang.time_multiplier)
    limits = Limits(
        time_ms=effective_time_ms(lang_scaled, CALIBRATION_FACTOR, safety=RUN_SAFETY_MARGIN),
        wall_ms=base.wall_ms,
        memory_mb=base.memory_mb + lang.memory_extra_mb,
        # process cap is driven by an explicit per-language field (JVM/.NET/Go spawn
        # GC/JIT/runtime threads), NOT inferred from memory_extra_mb.
        processes=max(base.processes, lang.processes),
        fsize_kb=base.fsize_kb, open_files=base.open_files, stack_kb=base.stack_kb,
        stdout_cap_bytes=base.stdout_cap_bytes,
    )
    return box.run(_subst(lang.run, lang), limits, stdin=case.stdin)


def grade(
    box_id: int,
    lang: Language,
    source: bytes,
    cases: list[CaseInput],
    base_limits: Limits,
) -> tuple[RunResult, list[RunResult]]:
    """Compile once, then run every case in the SAME box. Returns the compile
    result and one RunResult per case. Cost/validity are computed downstream by
    the per-problem checker — this function is execution only."""
    with IsolateBox(box_id) as box:
        compiled = compile_solution(box, lang, source)
        if compiled.verdict == Verdict.COMPILE_ERROR:
            return compiled, []
        results = [run_case(box, lang, c, base_limits) for c in cases]
        return compiled, results


if __name__ == "__main__":
    # Smoke-doc: shows intended usage; only runs on a Linux host with isolate.
    print("sandbox.py is a Linux/isolate reference runner. Import grade() from a "
          "grader worker on a dedicated host. Shell to verify isolate is present:")
    print("  " + shlex.join([ISOLATE, "--version"]))
