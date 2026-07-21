#!/usr/bin/env python3
"""One entry point for the test suites — the regression gate.

Each suite here is a standalone script with its own `_run_all()` and an exit
code (there is no pytest in this project), so this runner is a subprocess loop,
not a framework. It exists for one reason: "all suites green" is a claim, and a
claim needs a single command that prints per-suite pass/fail as evidence.

The split that matters is OFFLINE vs LIVE. Offline suites call no provider and
run in seconds — that is the gate to run before every commit, and it is the
default. Live suites issue real LLM requests, so they fail on provider quota
rather than on a code defect, and a red one proves nothing about the code. They
run only under --live.

LIVE is the hardcoded list so that a NEWLY ADDED suite is treated as offline and
therefore actually runs. The opposite default would let a new test be silently
skipped forever, which is the failure mode worth engineering against.

    python tests/run_all.py           # offline gate (default)
    python tests/run_all.py --live    # everything, needs provider quota
"""
import subprocess
import sys
import time
from pathlib import Path

TESTS_DIR = Path(__file__).resolve().parent

# Suites that issue real provider requests. Everything else is offline.
LIVE = {
    "test_api.py",                      # needs a running server
    "test_architecture_agent.py",
    "test_database_agent.py",
    "test_devops_agent.py",
    "test_gemini.py",
    "test_groq.py",
    "test_openrouter.py",
    "test_pipeline.py",
    "test_planning_agent.py",
    "test_requirements_agent.py",
    "test_research_agent.py",
    "test_research_agent_sections.py",
}

# A live agent smoke can outlast any sensible gate; offline suites finish in
# seconds. One ceiling covers both because the point is to fail loudly rather
# than hang a release checklist.
TIMEOUT_SECONDS = 300


def run(path: Path) -> tuple[str, float, str]:
    """Run one suite. Returns (status, seconds, last meaningful output line)."""
    started = time.time()
    try:
        proc = subprocess.run(
            [sys.executable, str(path)],
            capture_output=True, text=True,
            timeout=TIMEOUT_SECONDS, cwd=str(TESTS_DIR.parent),
        )
        status = "PASS" if proc.returncode == 0 else f"FAIL({proc.returncode})"
        output = proc.stdout + proc.stderr
    except subprocess.TimeoutExpired as exc:
        status = "TIMEOUT"
        output = (exc.stdout or b"").decode(errors="replace")

    lines = [ln for ln in output.strip().splitlines() if ln.strip()]
    return status, time.time() - started, lines[-1][:100] if lines else ""


def main() -> int:
    include_live = "--live" in sys.argv
    suites = sorted(p for p in TESTS_DIR.glob("test_*.py")
                    if include_live or p.name not in LIVE)

    print(f"{len(suites)} suites ({'offline + live' if include_live else 'offline'})\n")
    failed = []
    for path in suites:
        status, secs, tail = run(path)
        if status != "PASS":
            failed.append(path.name)
        print(f"{status:9} {secs:6.1f}s  {path.name:34} {tail}")

    print(f"\n{len(suites) - len(failed)}/{len(suites)} green")
    if failed:
        print("failed: " + ", ".join(failed))
        if not include_live:
            print("\nOffline suites must never be red — this is a stop-the-line failure.")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
