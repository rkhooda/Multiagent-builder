"""Thin wrapper over the Docker CLI — the entire sandbox runner.

One function runs one command in a disposable, resource-limited container and
reports what happened. No job queue, no orchestration framework: `docker run`
via subprocess, a wall-clock timeout, a polling disk-size watchdog (no cgroup
quota exists on the default overlay2 driver), and an unconditional teardown.

This module is the ONLY thing in the codebase that shells out to `docker`. It
lives in `backend/sandbox/`, a separate service from `backend/app/` — the
`backend` container never imports this, by construction, not just convention.
See docs/SANDBOX_THREAT_MODEL.md for the isolation contract this enforces and
backend/tests/test_sandbox_hostile.py for the proof.
"""
import os
import shutil
import subprocess
import tempfile
import threading
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

LABEL = "mab-sandbox"
LABEL_FILTER = f"label={LABEL}=1"
NETWORK = "sandbox-net"
DEFAULT_TIMEOUT_S = 120
DISK_LIMIT_MB = 2048
DISK_POLL_S = 1
OUTPUT_LIMIT_CHARS = 20_000

# `run_in_sandbox` talks to the Docker daemon over the mounted socket, so a
# path it hands to `docker run -v` is resolved by the DAEMON, i.e. on the
# HOST — not inside whatever container this module happens to be running in.
# Called directly (tests, on a dev machine with a local daemon), the process
# IS the host, so the system temp dir is already correct: leave this unset.
# Called from inside the `sandbox` service container, docker-compose.yml
# mounts the scratch dir at the SAME absolute path on both sides and sets
# this env var to that path, so a path this process creates is valid on the
# host too. See docs/SANDBOX_THREAT_MODEL.md.
SCRATCH_ROOT = os.environ.get("SANDBOX_SCRATCH_ROOT") or None


@dataclass
class RunResult:
    exit_code: Optional[int]   # None = killed (timeout or disk limit), never ran to completion
    stdout: str
    stderr: str
    timed_out: bool
    disk_exceeded: bool
    duration_s: float


def _truncate(s: str) -> str:
    if len(s) <= OUTPUT_LIMIT_CHARS:
        return s
    return s[:OUTPUT_LIMIT_CHARS] + f"\n... truncated ({len(s)} chars total)"


def _dir_size_mb(path: Path) -> float:
    # ponytail: O(n) walk per poll, not a cgroup quota — the default overlay2
    # storage driver has no --storage-opt size= support. Upgrade if the driver
    # changes to one that does (devicemapper/btrfs/zfs).
    total = 0
    if path.exists():
        for f in path.rglob("*"):
            if f.is_file():
                try:
                    total += f.stat().st_size
                except OSError:
                    pass
    return total / (1024 * 1024)


def ensure_network(name: str = NETWORK) -> None:
    """Idempotent: compose creates this network in production; standalone
    callers (tests) need it to exist too."""
    exists = subprocess.run(
        ["docker", "network", "inspect", name], capture_output=True
    ).returncode == 0
    if not exists:
        subprocess.run(["docker", "network", "create", name], check=True, capture_output=True)


def make_workspace(source_dir: Path) -> Path:
    """Copy a project's generated files into a fresh, disposable workspace.
    Never returns a path under the real outputs/ tree — the caller must copy
    OUT of it, never hand a build container the real tree writable."""
    workspace = Path(tempfile.mkdtemp(prefix="mab-sandbox-", dir=SCRATCH_ROOT))
    if source_dir.exists():
        shutil.copytree(source_dir, workspace, dirs_exist_ok=True)
    return workspace


def cleanup_workspace(workspace: Path) -> None:
    shutil.rmtree(workspace, ignore_errors=True)


def reap_orphans() -> list:
    """Kill every container left over from an unclean previous exit (the
    sandbox service itself crashing mid-build, so no `finally` block ever ran).
    Call once at process startup. Returns the ids killed."""
    out = subprocess.run(
        ["docker", "ps", "-q", "--filter", LABEL_FILTER],
        capture_output=True, text=True,
    )
    ids = out.stdout.split()
    for cid in ids:
        subprocess.run(["docker", "kill", cid], capture_output=True)
    return ids


def run_in_sandbox(
    workspace: Path,
    command: list,
    *,
    image: str,
    timeout_s: int = DEFAULT_TIMEOUT_S,
    network: str = NETWORK,
    disk_limit_mb: float = DISK_LIMIT_MB,
) -> RunResult:
    """Run `command` inside `image`, with `workspace` bind-mounted at
    `/workspace` as the ONLY writable path (read-only root filesystem
    elsewhere), non-root, all capabilities dropped, no new privileges, a PID
    cap, a memory cap, and no network beyond `network`.

    Always removes the container, however the run ends. Never raises for a
    build failure (a nonzero exit is a normal, expected `RunResult`) — only
    for a setup problem (docker unreachable, image missing), which the caller
    must treat as `unverified`, never `pass`.
    """
    ensure_network(network)
    name = f"sandbox-build-{uuid.uuid4().hex[:12]}"
    cmd = [
        "docker", "run", "--rm", "--name", name,
        "--label", f"{LABEL}=1",
        "--network", network,
        "--user", "1000:1000",
        "--cap-drop", "ALL",
        "--security-opt", "no-new-privileges",
        "--pids-limit", "256",
        "--cpus", "2",
        "--memory", "2g", "--memory-swap", "2g",
        "--read-only",
        "--tmpfs", "/tmp:size=256m",
        "-v", f"{workspace}:/workspace",
        "-w", "/workspace",
        image, *command,
    ]

    disk_exceeded = threading.Event()
    stop_watch = threading.Event()

    def _watch_disk():
        while not stop_watch.is_set():
            if _dir_size_mb(workspace) > disk_limit_mb:
                disk_exceeded.set()
                subprocess.run(["docker", "kill", name], capture_output=True)
                return
            stop_watch.wait(DISK_POLL_S)

    watcher = threading.Thread(target=_watch_disk, daemon=True)
    watcher.start()

    start = time.monotonic()
    timed_out = False
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout_s)
        exit_code = proc.returncode
        stdout, stderr = proc.stdout, proc.stderr
    except subprocess.TimeoutExpired as exc:
        timed_out = True
        subprocess.run(["docker", "kill", name], capture_output=True)
        exit_code = None
        stdout = exc.stdout.decode(errors="replace") if isinstance(exc.stdout, bytes) else (exc.stdout or "")
        stderr = exc.stderr.decode(errors="replace") if isinstance(exc.stderr, bytes) else (exc.stderr or "")
    finally:
        stop_watch.set()
        watcher.join(timeout=DISK_POLL_S + 1)
        # Belt and suspenders: --rm handles the normal/killed exit, but a
        # daemon hiccup could leave the name registered. Best-effort, the name
        # is disposable either way.
        subprocess.run(["docker", "rm", "-f", name], capture_output=True)

    return RunResult(
        exit_code=exit_code,
        stdout=_truncate(stdout),
        stderr=_truncate(stderr),
        timed_out=timed_out,
        disk_exceeded=disk_exceeded.is_set(),
        duration_s=time.monotonic() - start,
    )


def demo() -> None:
    """ponytail self-check: a benign run must produce sane output; a container
    that outlives its timeout must be killed and removed."""
    ensure_network()
    ws = make_workspace(Path("/nonexistent"))
    try:
        result = run_in_sandbox(ws, ["echo", "hello"], image="python:3.11-slim", timeout_s=30)
        assert result.exit_code == 0, result
        assert "hello" in result.stdout, result
        assert not result.timed_out and not result.disk_exceeded

        result = run_in_sandbox(ws, ["python3", "-c", "import time; time.sleep(30)"],
                                 image="python:3.11-slim", timeout_s=2)
        assert result.timed_out
        assert result.exit_code is None
    finally:
        cleanup_workspace(ws)
    print("runner.demo: OK")


if __name__ == "__main__":
    demo()
