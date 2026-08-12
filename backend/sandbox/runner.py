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
import json
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


def _base_flags(name: str, workspace: Path, workdir: str, network: str, env: dict) -> list:
    flags = [
        "--name", name,
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
        "-w", f"/workspace/{workdir}".rstrip("/") or "/workspace",
    ]
    for k, v in (env or {}).items():
        flags += ["-e", f"{k}={v}"]
    return flags


def run_in_sandbox(
    workspace: Path,
    command: list,
    *,
    image: str,
    timeout_s: int = DEFAULT_TIMEOUT_S,
    network: str = NETWORK,
    disk_limit_mb: float = DISK_LIMIT_MB,
    workdir: str = "",
    env: dict = None,
) -> RunResult:
    """Run `command` inside `image`, with `workspace` bind-mounted at
    `/workspace` as the ONLY writable path (read-only root filesystem
    elsewhere), non-root, all capabilities dropped, no new privileges, a PID
    cap, a memory cap, and no network beyond `network`. `workdir` is a
    subdirectory of `/workspace` (e.g. "frontend"); `env` is extra
    environment for the command — e.g. PYTHONPATH so a later tier can see
    what an earlier tier `pip install --target`ed into the workspace, since
    system site-packages die with the container that installed into them but
    anything under `/workspace` survives across separate `docker run` calls.

    Always removes the container, however the run ends. Never raises for a
    build failure (a nonzero exit is a normal, expected `RunResult`) — only
    for a setup problem (docker unreachable, image missing), which the caller
    must treat as `unverified`, never `pass`.
    """
    ensure_network(network)
    name = f"sandbox-build-{uuid.uuid4().hex[:12]}"
    cmd = ["docker", "run", "--rm", *_base_flags(name, workspace, workdir, network, env), image, *command]

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


@dataclass
class BootResult:
    healthy: bool
    crashed: bool          # exited on its own before ever becoming healthy
    exit_code: Optional[int]
    logs: str
    duration_s: float
    #: Journey-smoke result, when the caller asked for one. None means it was
    #: not requested; a dict is {"ok": bool, "checked": int, "failures": [...]}.
    journey: Optional[dict] = None


# The journey smoke, as a script executed INSIDE the booted container by the
# same `docker exec python3` mechanism the health probe already uses.
#
# WHY this exists (docs/VERIFICATION_GAP_ANALYSIS.md): in project e8935f86
# /health returned 200 while every real request returned 500. `boot` proves a
# process started and answered one trivial path. It proves nothing about the
# application. Two defects that a boot-only ladder cannot see:
#
#   schemas/tag.py used `datetime` without importing it, so building the
#   OpenAPI schema raised and /openapi.json returned 500 -- while /health
#   stayed 200.
#
#   Contact.tags back-populated a Tag.contacts that did not exist, so
#   SQLAlchemy raised at mapper configuration and EVERY route touching the
#   database returned 500 -- while /health stayed 200.
#
# WHY the journey is derived from the app's own /openapi.json rather than from
# the plan's user story: it needs no per-project configuration, no path
# guessing and no plan parsing, and it necessarily covers whatever the app
# actually exposes. Every parameterless GET is called; only 5xx counts as a
# failure, because 401/403/422 are the app working and declining, which is a
# correct answer to an unauthenticated smoke request.
#
# ponytail: a string executed by the probe we already have, not a new service,
# not a test framework in the image, no new dependency in the boot image.
_JOURNEY_SCRIPT = r"""
import json, sys, urllib.error, urllib.request

BASE = "http://localhost:%d"
failures, checked = [], 0

def fetch(path):
    try:
        with urllib.request.urlopen(BASE + path, timeout=5) as r:
            return r.status, r.read()
    except urllib.error.HTTPError as e:
        return e.code, e.read()
    except Exception as e:
        return 0, str(e).encode()

status, body = fetch("/openapi.json")
if status != 200:
    failures.append({"path": "/openapi.json", "status": status,
                     "detail": body[:400].decode("utf-8", "replace")})
    print(json.dumps({"ok": False, "checked": 1, "failures": failures}))
    sys.exit(0)

try:
    spec = json.loads(body)
except Exception as e:
    print(json.dumps({"ok": False, "checked": 1, "failures": [
        {"path": "/openapi.json", "status": 200,
         "detail": "200 but not valid JSON: %%s" %% e}]}))
    sys.exit(0)

checked = 1
for path, methods in (spec.get("paths") or {}).items():
    get = (methods or {}).get("get")
    if get is None or "{" in path:
        continue
    if any(p.get("required") for p in (get.get("parameters") or [])):
        continue
    code, detail = fetch(path)
    checked += 1
    # 5xx is the app breaking. 401/403/422 is the app working and declining.
    if code >= 500 or code == 0:
        failures.append({"path": path, "status": code,
                         "detail": detail[:400].decode("utf-8", "replace")})

print(json.dumps({"ok": not failures, "checked": checked, "failures": failures[:10]}))
"""


def probe_boot(
    workspace: Path,
    command: list,
    *,
    image: str,
    port: int,
    health_path: str,
    ready_timeout_s: int = 30,
    network: str = NETWORK,
    workdir: str = "",
    env: dict = None,
    journey: bool = False,
) -> BootResult:
    """Tier 3: start `command` detached, poll `health_path` until it answers
    or `ready_timeout_s` runs out, then unconditionally stop the container —
    a smoke test, not a lasting server. A healthy long-running process never
    exits on its own, so this cannot reuse `run_in_sandbox`'s
    subprocess-timeout-means-failure shape: here a full `ready_timeout_s`
    with nothing happening is the SUCCESS path if health comes back late, and
    an early container exit is the interesting failure, not a timeout.

    Polls via `docker exec ... python3`, not a published host port — keeps
    the container off any host-reachable port, consistent with the sandbox
    never exposing build output to the host network. Requires python3 in the
    boot image; every react-fastapi boot image (python:3.11-slim) has it.
    `ponytail: not a generic prober — the one profile that exists needs only
    this, revisit if a future profile's boot image lacks python3.`
    """
    ensure_network(network)
    name = f"sandbox-boot-{uuid.uuid4().hex[:12]}"
    run_cmd = [
        "docker", "run", "-d", *_base_flags(name, workspace, workdir, network, env),
        image, *command,
    ]
    start = time.monotonic()
    started = subprocess.run(run_cmd, capture_output=True, text=True)
    if started.returncode != 0:
        return BootResult(healthy=False, crashed=True, exit_code=None,
                           logs=_truncate(started.stderr), duration_s=time.monotonic() - start)

    probe = [
        "docker", "exec", name, "python3", "-c",
        "import urllib.request,sys\n"
        f"urllib.request.urlopen('http://localhost:{port}{health_path}', timeout=2)\n"
        "sys.exit(0)\n",
    ]
    healthy = False
    try:
        deadline = time.monotonic() + ready_timeout_s
        while time.monotonic() < deadline:
            if subprocess.run(probe, capture_output=True, timeout=5).returncode == 0:
                healthy = True
                break
            inspect = subprocess.run(
                ["docker", "inspect", "-f", "{{.State.Running}}", name],
                capture_output=True, text=True,
            )
            if inspect.returncode != 0 or inspect.stdout.strip() != "true":
                break  # the process exited on its own — a crash, not a slow start
            time.sleep(1)

        # The journey runs only against a container that became healthy —
        # otherwise there is nothing to ask, and the boot verdict already says
        # so. Its own failure NEVER downgrades a healthy boot to crashed: the
        # two are separate rungs with separate verdicts.
        journey_result = None
        if healthy and journey:
            journey_result = _run_journey(name, port)

        logs = subprocess.run(["docker", "logs", name], capture_output=True, text=True)
        inspect_exit = subprocess.run(
            ["docker", "inspect", "-f", "{{.State.Running}}", name],
            capture_output=True, text=True,
        )
        still_running = inspect_exit.returncode == 0 and inspect_exit.stdout.strip() == "true"
        exit_code = None
        crashed = False
        if not still_running and not healthy:
            crashed = True
            code = subprocess.run(
                ["docker", "inspect", "-f", "{{.State.ExitCode}}", name],
                capture_output=True, text=True,
            )
            exit_code = int(code.stdout.strip()) if code.returncode == 0 and code.stdout.strip().isdigit() else None
    finally:
        subprocess.run(["docker", "kill", name], capture_output=True)
        subprocess.run(["docker", "rm", "-f", name], capture_output=True)

    return BootResult(
        healthy=healthy, crashed=crashed, exit_code=exit_code,
        logs=_truncate(logs.stdout + logs.stderr), duration_s=time.monotonic() - start,
        journey=journey_result,
    )


def _run_journey(container: str, port: int) -> dict:
    """Execute the journey smoke inside `container`. Never raises: a smoke that
    cannot run is `unverified`, never a silent pass — the same rule the rest of
    this change enforces."""
    try:
        proc = subprocess.run(
            ["docker", "exec", container, "python3", "-c", _JOURNEY_SCRIPT % port],
            capture_output=True, text=True, timeout=120,
        )
    except (subprocess.TimeoutExpired, OSError) as exc:
        return {"ok": False, "unverified": True, "checked": 0,
                "failures": [{"path": "-", "status": 0, "detail": str(exc)}]}
    try:
        return json.loads((proc.stdout or "").strip().splitlines()[-1])
    except (json.JSONDecodeError, IndexError):
        return {"ok": False, "unverified": True, "checked": 0,
                "failures": [{"path": "-", "status": 0,
                              "detail": _truncate(proc.stderr or proc.stdout)}]}


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

        boot = probe_boot(
            ws, ["python3", "-m", "http.server", "8000"],
            image="python:3.11-slim", port=8000, health_path="/", ready_timeout_s=10,
        )
        assert boot.healthy, boot
        assert not boot.crashed, boot

        boot = probe_boot(
            ws, ["python3", "-c", "import sys; sys.exit(1)"],
            image="python:3.11-slim", port=8000, health_path="/", ready_timeout_s=5,
        )
        assert not boot.healthy and boot.crashed, boot
    finally:
        cleanup_workspace(ws)
    print("runner.demo: OK")


if __name__ == "__main__":
    demo()
