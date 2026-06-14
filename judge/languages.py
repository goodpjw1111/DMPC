"""
Supported languages — the single source of truth for the grader.

The owner chose to support all 12 NYPC-style languages. With an `isolate`-based
grader a language is *just configuration*: a source filename, an optional compile
command, a run command, and per-language time/memory multipliers. So supporting
12 languages is mostly toolchain installation on the grader image plus the rows
below — NOT 12x application code.

ROLLOUT NOTE (see README "12 languages"): ship all 12 in the grader image but
enable them in waves. C / C++ / Python validate the whole pipeline first; the
rest flip `enabled=True` once their toolchain is baked into the worker image and
their time/mem multipliers are calibrated against a reference machine.

Command templates use these placeholders, substituted by the sandbox runner:
    {src}      source file name inside the box (e.g. "main.cpp")
    {exe}      compiled artifact name inside the box (e.g. "main")
    {mainclass} Java/Kotlin main class name ("Main")

Every command runs *inside* the sandbox (compilation is untrusted too).
NYPC policy: time/memory limits are the SAME for all 12 languages, so the
per-language multipliers below are all 1.0 / 0 (the fields are kept for
flexibility but neutral by default).
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class Language:
    id: str                       # stable key stored on submissions
    label: str                    # shown in the UI
    source_name: str              # filename written into the sandbox
    run: list[str]                # argv to execute the solution
    compile: list[str] | None = None   # argv to build; None = interpreted
    time_multiplier: float = 1.0       # x base time limit
    memory_extra_mb: int = 0           # added to base memory limit (VM runtimes)
    processes: int = 1                 # process/thread cap at run time; bump for
                                       # JVM/.NET/Go which spawn GC/JIT/runtime threads
    compile_time_limit_ms: int = 15_000
    enabled: bool = False              # gated rollout; True = live to users
    notes: str = ""


# Ordered roughly by how common they are in heuristic contests.
LANGUAGES: list[Language] = [
    Language(
        id="cpp20", label="C++20", source_name="main.cpp", enabled=True,
        compile=["g++", "-O2", "-std=gnu++20", "-pipe", "-s", "-o", "{exe}", "{src}"],
        run=["./{exe}"],
        notes="Primary language. -O2 to mirror typical contest builds.",
    ),
    Language(
        id="c17", label="C17", source_name="main.c", enabled=True,
        compile=["gcc", "-O2", "-std=gnu17", "-pipe", "-s", "-lm", "-o", "{exe}", "{src}"],
        run=["./{exe}"],
    ),
    Language(
        id="python3", label="Python 3.12", source_name="main.py", enabled=True,
        compile=["python3", "-c", "import py_compile,sys; py_compile.compile('{src}', doraise=True)"],
        run=["python3", "{src}"],
        time_multiplier=1.0, memory_extra_mb=0,
        notes="Compile step = syntax check only. Limits equal to other langs (NYPC).",
    ),
    Language(
        id="java21", label="Java 21", source_name="Main.java",
        compile=["javac", "-encoding", "UTF-8", "{src}"],
        run=["java", "-XX:+UseSerialGC", "-Xss64m", "{mainclass}"],
        time_multiplier=1.0, memory_extra_mb=0, processes=64, compile_time_limit_ms=20_000,
        notes="JVM startup + GC headroom. mainclass=Main.",
    ),
    Language(
        id="csharp", label="C# (.NET)", source_name="Main.cs",
        compile=["dotnet", "build", "-c", "Release", "-o", "out"],
        run=["dotnet", "out/app.dll"],
        time_multiplier=1.0, memory_extra_mb=0, processes=64, compile_time_limit_ms=30_000,
        notes="Project-file based; image must pre-restore the SDK offline.",
    ),
    Language(
        id="kotlin", label="Kotlin", source_name="Main.kt",
        compile=["kotlinc", "{src}", "-include-runtime", "-d", "{exe}.jar"],
        run=["java", "-jar", "{exe}.jar"],
        time_multiplier=1.0, memory_extra_mb=0, processes=64, compile_time_limit_ms=40_000,
        notes="kotlinc is slow; generous compile limit.",
    ),
    Language(
        id="go", label="Go", source_name="main.go",
        compile=["go", "build", "-o", "{exe}", "{src}"],
        run=["./{exe}"],
        time_multiplier=1.0, memory_extra_mb=0, processes=32, compile_time_limit_ms=30_000,
        notes="GOFLAGS=-mod=vendor, GOCACHE in writable /tmp. Go runtime spawns GC/sched threads.",
    ),
    Language(
        id="rust", label="Rust", source_name="main.rs",
        compile=["rustc", "-O", "-o", "{exe}", "{src}"],
        run=["./{exe}"],
        time_multiplier=1.0, compile_time_limit_ms=30_000,
    ),
    Language(
        id="node", label="JavaScript (Node)", source_name="main.js",
        compile=["node", "--check", "{src}"],
        run=["node", "{src}"],
        time_multiplier=1.0, memory_extra_mb=0,
    ),
    Language(
        id="ruby", label="Ruby", source_name="main.rb",
        compile=["ruby", "-c", "{src}"],
        run=["ruby", "{src}"],
        time_multiplier=1.0, memory_extra_mb=0,
    ),
    Language(
        id="swift", label="Swift", source_name="main.swift",
        compile=["swiftc", "-O", "-o", "{exe}", "{src}"],
        run=["./{exe}"],
        time_multiplier=1.0, memory_extra_mb=0, processes=32, compile_time_limit_ms=40_000,
    ),
    Language(
        id="php", label="PHP", source_name="main.php",
        compile=["php", "-l", "{src}"],
        run=["php", "{src}"],
        time_multiplier=1.0, memory_extra_mb=0,
    ),
]

assert len(LANGUAGES) == 12, "spec calls for 12 languages"

BY_ID: dict[str, Language] = {l.id: l for l in LANGUAGES}


def enabled_languages() -> list[Language]:
    return [l for l in LANGUAGES if l.enabled]


def get(language_id: str) -> Language:
    try:
        return BY_ID[language_id]
    except KeyError:
        raise ValueError(f"unknown language: {language_id!r}") from None
