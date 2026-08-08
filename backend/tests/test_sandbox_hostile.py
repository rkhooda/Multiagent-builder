"""Hostile fixtures proving the sandbox isolation boundary — Phase 1 of Build
Verification. Every claim in docs/SANDBOX_THREAT_MODEL.md is tested here; a
claim with no fixture below is not trusted.

Needs a running Docker daemon — NOT part of the offline gate's guarantees
(0 API calls, seconds, always green) in the same sense, but it costs no LLM
tokens either, so it self-skips loudly rather than failing when Docker is
unreachable: "SKIPPED" must never be silent, and must never read as "PASS".

    cd backend && python tests/test_sandbox_hostile.py
"""
import os
import subprocess
import sys
import time
import uuid
from pathlib import Path

BACKEND_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BACKEND_DIR)
sys.path.insert(0, os.path.join(BACKEND_DIR, "sandbox"))

import runner  # noqa: E402

IMAGE = "python:3.11-slim"
VICTIM_NET = "sandbox-test-victim-net"
VICTIM_NAME = "sandbox-test-victim"

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


def _container_running(name: str) -> bool:
    out = subprocess.run(
        ["docker", "ps", "-q", "--filter", f"name=^{name}$"],
        capture_output=True, text=True,
    )
    return bool(out.stdout.strip())


# ── 1. No host secrets reachable, no path outside /workspace writable ───────

def test_no_secrets_no_escape():
    ws = runner.make_workspace(Path("/nonexistent"))
    try:
        result = runner.run_in_sandbox(ws, [
            "python3", "-c",
            "import os\n"
            "found = [p for base in ('/root', '/home', '/') "
            "for p in (os.path.join(base, '.env'), os.path.join(base, 'backend.env')) "
            "if os.path.exists(p)]\n"
            "print('FOUND:' + ','.join(found) if found else 'CLEAN')\n",
        ], image=IMAGE, timeout_s=30)
        check("no .env reachable inside container", "CLEAN" in result.stdout, result.stdout)

        result = runner.run_in_sandbox(ws, [
            "python3", "-c",
            "try:\n"
            "    open('/root/pwned', 'w').write('x')\n"
            "    print('WRITE_SUCCEEDED')\n"
            "except Exception as e:\n"
            "    print('WRITE_BLOCKED:' + type(e).__name__)\n",
        ], image=IMAGE, timeout_s=30)
        check("write outside /workspace blocked (read-only root fs)",
              "WRITE_BLOCKED" in result.stdout, result.stdout)
    finally:
        runner.cleanup_workspace(ws)


# ── 2. Docker socket unreachable from inside a build container ──────────────

def test_no_docker_socket():
    ws = runner.make_workspace(Path("/nonexistent"))
    try:
        result = runner.run_in_sandbox(ws, [
            "python3", "-c",
            "import os\n"
            "print('SOCKET_PRESENT' if os.path.exists('/var/run/docker.sock') else 'SOCKET_ABSENT')\n",
        ], image=IMAGE, timeout_s=30)
        check("docker.sock not mounted into build container",
              "SOCKET_ABSENT" in result.stdout, result.stdout)
    finally:
        runner.cleanup_workspace(ws)


# ── 3. Network segmentation: cannot resolve/reach a service on another network

def _victim_setup():
    subprocess.run(["docker", "network", "create", VICTIM_NET], capture_output=True)
    subprocess.run(["docker", "rm", "-f", VICTIM_NAME], capture_output=True)
    subprocess.run([
        "docker", "run", "-d", "--rm", "--name", VICTIM_NAME,
        "--network", VICTIM_NET,
        IMAGE, "python3", "-m", "http.server", "9999",
    ], check=True, capture_output=True)
    time.sleep(1)


def _victim_teardown():
    subprocess.run(["docker", "rm", "-f", VICTIM_NAME], capture_output=True)
    subprocess.run(["docker", "network", "rm", VICTIM_NET], capture_output=True)


def test_network_segmentation():
    _victim_setup()
    ws = runner.make_workspace(Path("/nonexistent"))
    try:
        result = runner.run_in_sandbox(ws, [
            "python3", "-c",
            "import socket\n"
            "socket.setdefaulttimeout(3)\n"
            "try:\n"
            "    socket.gethostbyname('sandbox-test-victim')\n"
            "    print('RESOLVED')\n"
            "except socket.gaierror:\n"
            "    print('UNRESOLVED')\n",
        ], image=IMAGE, timeout_s=30)
        check("victim on a different network is unreachable by name",
              "UNRESOLVED" in result.stdout, result.stdout)
    finally:
        runner.cleanup_workspace(ws)
        _victim_teardown()


# ── 4. Infinite loop: timeout fires, container is killed and removed ────────

def test_infinite_loop_reaped():
    ws = runner.make_workspace(Path("/nonexistent"))
    try:
        result = runner.run_in_sandbox(
            ws, ["python3", "-c", "while True: pass"],
            image=IMAGE, timeout_s=5,
        )
        check("infinite loop times out", result.timed_out)
        check("infinite loop never returns an exit code", result.exit_code is None)
        check("duration bounded near the timeout, not left running",
              result.duration_s < 15, result.duration_s)
    finally:
        runner.cleanup_workspace(ws)


# ── 5. Fork bomb: capped by --pids-limit, does not take down the host ───────

def test_fork_bomb_capped():
    ws = runner.make_workspace(Path("/nonexistent"))
    try:
        result = runner.run_in_sandbox(ws, [
            "python3", "-c",
            "import os, time\n"
            "n = 0\n"
            "try:\n"
            "    while True:\n"
            "        os.fork()\n"
            "        n += 1\n"
            "except BlockingIOError:\n"
            "    print('CAPPED_AT:' + str(n))\n"
            "except OSError as e:\n"
            "    print('CAPPED_AT:' + str(n) + ':' + str(e))\n",
        ], image=IMAGE, timeout_s=20)
        check("fork bomb hits the PID cap rather than running unbounded",
              "CAPPED_AT" in result.stdout or result.timed_out, result.stdout)
    finally:
        runner.cleanup_workspace(ws)


# ── 6. Disk fill: watcher kills the container before it grows unbounded ─────

def test_disk_fill_capped():
    # A poll-based watchdog cannot catch a write that completes faster than
    # one poll window — it catches unbounded/steady growth, the realistic
    # shape of a runaway log or exploding dependency tree, not a single-burst
    # write. `time.sleep` between chunks simulates that shape instead of
    # racing the watcher. See docs/SANDBOX_THREAT_MODEL.md.
    ws = runner.make_workspace(Path("/nonexistent"))
    try:
        result = runner.run_in_sandbox(ws, [
            "python3", "-c",
            "import time\n"
            "f = open('/workspace/bigfile', 'wb')\n"
            "chunk = b'0' * (1024 * 1024)\n"
            "for _ in range(200):\n"
            "    f.write(chunk)\n"
            "    f.flush()\n"
            "    time.sleep(0.05)\n",
        ], image=IMAGE, timeout_s=60, disk_limit_mb=50)
        check("oversized write trips the disk watchdog", result.disk_exceeded)
    finally:
        runner.cleanup_workspace(ws)


# ── 7. Teardown: every run above leaves zero containers behind ──────────────

def test_no_leftover_containers():
    out = subprocess.run(
        ["docker", "ps", "-a", "-q", "--filter", runner.LABEL_FILTER],
        capture_output=True, text=True,
    )
    check("no mab-sandbox-labeled containers survive the suite",
          out.stdout.strip() == "", out.stdout)


# ── 8. Orphan reaping: a container that outlives its managing process ───────

def test_orphan_reaping():
    name = f"sandbox-build-orphantest-{uuid.uuid4().hex[:8]}"
    subprocess.run([
        "docker", "run", "-d", "--rm", "--name", name,
        "--label", f"{runner.LABEL}=1",
        IMAGE, "sleep", "60",
    ], check=True, capture_output=True)
    time.sleep(1)
    running_before = _container_running(name)

    killed = runner.reap_orphans()
    time.sleep(1)
    running_after = _container_running(name)

    check("orphan was actually running before the sweep", running_before)
    check("startup sweep reaped at least one container", len(killed) >= 1, killed)
    check("orphan is gone after reap_orphans()", not running_after, f"still running: {name}")


def main() -> int:
    if not _docker_available():
        print("SKIPPED (docker daemon unavailable) — sandbox isolation NOT verified this run")
        return 0

    for fn in (
        test_no_secrets_no_escape,
        test_no_docker_socket,
        test_network_segmentation,
        test_infinite_loop_reaped,
        test_fork_bomb_capped,
        test_disk_fill_capped,
        test_orphan_reaping,
        test_no_leftover_containers,  # last: asserts everything above cleaned up
    ):
        print(f"-- {fn.__name__}")
        fn()

    print(f"\n{passed} passed, {failed} failed")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
