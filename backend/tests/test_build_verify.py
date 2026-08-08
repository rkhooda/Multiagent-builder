"""End-to-end proof of the three-tier verification ladder (Phase 2 of Build
Verification): install -> build -> boot, against a real Docker daemon and the
REAL http boundary — `backend`'s own container calling `sandbox` by service
name, exactly as production does. `backend/app/build_verify/ladder.py` is
never imported directly by this test process for that reason: importing it
here would prove the ladder's Python logic but not that the HTTP path through
the actual compose network works, which is the point of an end-to-end check.

Needs a running Docker daemon and builds/starts the `backend` + `sandbox`
compose services — self-skips loudly (never silently) when Docker is
unreachable, same convention as test_sandbox_hostile.py.

    cd backend && python tests/test_build_verify.py
"""
import json
import shutil
import subprocess
import sys
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
FIXTURE = Path(__file__).resolve().parent / "fixtures" / "build_verify_project"
PROJECT_ID = "test-build-verify-e2e"
OUTPUTS_DIR = REPO_ROOT / "data" / "outputs" / PROJECT_ID

passed = 0
failed = 0


def check(name, cond, detail=""):
    global passed, failed
    if cond:
        passed += 1
        print(f"  PASS  {name}")
    else:
        failed += 1
        print(f"  FAIL  {name}  {detail}")


def _docker_available() -> bool:
    try:
        return subprocess.run(["docker", "info"], capture_output=True, timeout=5).returncode == 0
    except Exception:
        return False


def _compose(*args, timeout=180):
    return subprocess.run(
        ["docker", "compose", *args], cwd=REPO_ROOT,
        capture_output=True, text=True, timeout=timeout,
    )


def _exec_ladder(target_name: str) -> dict:
    """Run verify_target INSIDE the backend container, over the real network
    boundary, and print its JSON result to stdout for this process to parse."""
    script = (
        "import json, sys\n"
        "sys.path.insert(0, '.')\n"
        "from app.profiles import get_profile\n"
        "from app.build_verify.ladder import verify_target\n"
        f"profile = get_profile('react-fastapi')\n"
        f"target = next(t for t in profile.verify_targets if t.name == {target_name!r})\n"
        f"print(json.dumps(verify_target({PROJECT_ID!r}, target)))\n"
    )
    proc = subprocess.run(
        ["docker", "compose", "exec", "-T", "backend", "python3", "-c", script],
        cwd=REPO_ROOT, capture_output=True, text=True, timeout=180,
    )
    if proc.returncode != 0:
        raise RuntimeError(f"exec failed: {proc.stderr[-2000:]}")
    return json.loads(proc.stdout.strip().splitlines()[-1])


def setup():
    if OUTPUTS_DIR.exists():
        shutil.rmtree(OUTPUTS_DIR)
    shutil.copytree(FIXTURE, OUTPUTS_DIR)

    print("-- docker compose build backend sandbox --")
    build = _compose("build", "backend", "sandbox", timeout=600)
    if build.returncode != 0:
        raise RuntimeError(f"build failed: {build.stderr[-3000:]}")

    print("-- docker compose up -d backend sandbox --")
    up = _compose("up", "-d", "backend", "sandbox", timeout=120)
    if up.returncode != 0:
        raise RuntimeError(f"up failed: {up.stderr[-3000:]}")

    deadline = time.monotonic() + 60
    while time.monotonic() < deadline:
        ps = _compose("ps", "backend", "--format", "json")
        if '"State":"running"' in ps.stdout or '"Health":"healthy"' in ps.stdout:
            break
        time.sleep(2)
    time.sleep(3)  # uvicorn's own startup


def teardown():
    _compose("down", "backend", "sandbox", timeout=60)
    if OUTPUTS_DIR.exists():
        shutil.rmtree(OUTPUTS_DIR)


def test_backend_target_passes_all_three_tiers():
    result = _exec_ladder("backend")
    tiers = result.get("tiers", {})
    check("backend install passes", tiers.get("install", {}).get("verdict") == "pass", tiers.get("install"))
    check("backend build (import check) passes", tiers.get("build", {}).get("verdict") == "pass", tiers.get("build"))
    check("backend boot passes (health responded)", tiers.get("boot", {}).get("verdict") == "pass", tiers.get("boot"))


def test_frontend_target_passes_all_three_tiers():
    result = _exec_ladder("frontend")
    tiers = result.get("tiers", {})
    check("frontend install passes", tiers.get("install", {}).get("verdict") == "pass", tiers.get("install"))
    check("frontend build passes", tiers.get("build", {}).get("verdict") == "pass", tiers.get("build"))
    check("frontend boot passes (static index served)", tiers.get("boot", {}).get("verdict") == "pass", tiers.get("boot"))


def test_install_failure_skips_higher_tiers():
    """A broken requirements.txt must fail Tier 1 and SKIP Tiers 2 and 3, not
    attempt them against a workspace with no installed packages."""
    broken_req = OUTPUTS_DIR / "backend" / "requirements.txt"
    original = broken_req.read_text()
    broken_req.write_text("this-package-does-not-exist-anywhere==99.99.99\n")
    try:
        result = _exec_ladder("backend")
    finally:
        broken_req.write_text(original)

    tiers = result.get("tiers", {})
    check("broken requirements.txt fails install as fail_code",
          tiers.get("install", {}).get("verdict") == "fail_code", tiers.get("install"))
    check("build is skipped, not attempted", tiers.get("build", {}).get("verdict") == "skipped", tiers.get("build"))
    check("boot is skipped, not attempted", tiers.get("boot", {}).get("verdict") == "skipped", tiers.get("boot"))


def main() -> int:
    if not _docker_available():
        print("SKIPPED (docker daemon unavailable) — build verification ladder NOT verified this run")
        return 0

    try:
        setup()
        for fn in (
            test_backend_target_passes_all_three_tiers,
            test_frontend_target_passes_all_three_tiers,
            test_install_failure_skips_higher_tiers,
        ):
            print(f"-- {fn.__name__}")
            fn()
    finally:
        teardown()

    print(f"\n{passed} passed, {failed} failed")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
