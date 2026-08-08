# Build Verification — Sandbox Threat Model

**Dated 2026-08-08. Phase 0 output for the Build Verification improvement.**
Written before any sandbox code exists — this document is the acceptance
criteria for Phase 1. Every claim below is tested by a named hostile fixture
in `backend/tests/test_sandbox_hostile.py`; a claim with no fixture is not
trusted.

Pre-flight state: offline gate 20/20 suites green (`python tests/run_all.py`),
0 API calls. Stack profiles (Improvement 03) have landed and are wired into
every coder agent (`active_profile(state)` called from `devops_agent.py`,
`backend_coder_agent.py`, `planning_agent.py`, `database_agent.py`,
`frontend_coder_agent.py`) — build recipes in this work are declared as new
`StackProfile`/`PhaseSpec`-adjacent fields, not a parallel config surface.

## Why isolation comes before usefulness

The backend container (`docker-compose.yml`, service `backend`) holds
`backend/.env` (live API keys for every LLM provider — Gemini, Groq,
OpenRouter, NVIDIA NIM), `projects.db`, `metrics.db`, and the real `outputs/`
tree. Generated code is unreviewed by any human at the moment it would run,
and `npm install` executes arbitrary lifecycle scripts (`preinstall`,
`postinstall`) from the public npm registry on every `npm ci`. Running that
code inside — or with any reachable path to — the container that holds those
secrets turns a bad dependency into a credential-theft incident. Phase 1
exists to make that path unreachable before Phase 2 makes it useful.

## Architecture decision (ponytail #1)

**A separate `sandbox` Compose service, holding `/var/run/docker.sock`, with
no access to secrets — never a socket mount on `backend`.**

Four options were considered:

| Option | Verdict |
|---|---|
| Mount `docker.sock` into `backend` | **Rejected.** Socket access is host root. The same container holding every API key would also be one `docker run -v /:/host` away from reading `.env` off the mount, or reading it directly since it's already in that container. Same-container compromise = everything. |
| Docker-in-Docker | **Rejected.** Needs `--privileged` and a nested daemon — more moving parts (storage driver quirks, daemon startup races) than the socket option, for zero additional isolation: the privilege boundary is identical to giving a separate container the host socket. |
| Separate privileged build-service, backend talks to it over an API | **Chosen.** Splits "holds secrets" from "holds Docker access" into two containers that share no filesystem and, per the network policy below, cannot reach each other's internals. An attacker who fully compromises a build has Docker access and nothing worth stealing. |
| Sandbox runs on the host, outside Compose | **Rejected.** Breaks the single `docker compose up` distribution model this repo ships (`Makefile`: `make start` = `docker compose up --build -d`); host-side state has no guaranteed teardown at `docker compose down`, and orphan reaping would need to survive independently of the app's own lifecycle. |

`sandbox` is a thin FastAPI shim (FastAPI is already a dependency — rung 5 of
the ladder, not a new one) over the `docker` CLI. No job queue, no
orchestration framework: one process, one endpoint, `subprocess` calls to
`docker run`.

## What the sandbox may read, write, and reach

- **Read**: a copy of the project's generated files, placed into an ephemeral
  workspace directory created fresh per verification run. Never the real
  `outputs/{project_id}` tree — that is copied *from*, never mounted.
- **Write**: only inside that same ephemeral workspace (so `npm ci` can create
  `node_modules`, `npm run build` can create `dist/`, `pip install` can
  populate a venv). The workspace is deleted after the run regardless of
  outcome.
- **Reach (network)**: the public package registries (npm registry, PyPI) —
  unavoidable, since `npm ci`/`pip install` need them. Nothing else. See
  Network policy below.

## What the sandbox is denied

Enforced structurally (not by convention), each with a hostile fixture:

| Denied | Mechanism | Fixture |
|---|---|---|
| `backend/.env`, any API key | Never mounted; workspace copy contains only the generated project's own files | `fixture_read_env` |
| `/etc/passwd`, other host files | Bind mount is scoped to the ephemeral workspace only, `--read-only` root filesystem elsewhere | `fixture_read_etc_passwd` |
| `projects.db`, `metrics.db` | Never mounted — the `sandbox` service has no volume from `./data/db` | (structural — no DB path exists inside the container to test against; covered by `fixture_read_etc_passwd`'s general host-escape check) |
| The real `outputs/` tree, writable | Runner copies project files out, never mounts `outputs/` into a build container | `fixture_write_outside_workspace` |
| The Docker socket itself | Build containers (the ones running generated code) never receive `docker.sock` — only the `sandbox` service process does, one layer up | `fixture_reach_docker_socket` |
| The `sandbox` service's own API / the `backend` API | Network policy below | `fixture_reach_builder_api` |
| Unbounded CPU/memory/PIDs/disk/time | Resource limits below | `fixture_infinite_loop`, `fixture_fork_bomb`, `fixture_disk_fill` |
| Root inside the container | `--user`, non-root | covered by every fixture (asserted as a precondition) |

## Network policy

The hard problem: a fully offline sandbox cannot `npm install` or
`pip install`, so *some* egress is required. The narrowest allowance that
still works:

**A dedicated bridge network (`sandbox-net`) that build containers run on,
disjoint from the Compose default network `backend`/`frontend`/`ollama` share.**
This is Docker's own network isolation (rung 4 — native platform feature),
not a custom egress proxy. Build containers get normal internet egress (so the
public registries resolve and connect) but no route to `backend`, `frontend`,
`ollama`, or any other project's workspace, because they are never attached to
the network those services are on. Docker's per-network bridge isolation
denies that at the kernel (netfilter) level, not the application level.

**What this leaves exposed**: a malicious or compromised registry package
can still exfiltrate whatever is in the build workspace (the generated
source itself) to attacker-controlled infrastructure during install, and can
scan/probe the open internet from the container's IP for the duration of the
build. This is accepted, bounded by: (1) the workspace holds no secrets to
exfiltrate — see "What the sandbox is denied" — so the worst case is
leaking code that will be shown to the user anyway; (2) the wall-clock
timeout below bounds how long that egress window stays open; (3) resource
limits bound how much damage a compromised process can do locally.

**Explicitly not built**: a registry allowlist proxy (mitmproxy/squid) or a
pre-warmed dependency cache. Both would close the exfiltration-during-install
window further, but both are new long-lived services with their own failure
modes, for a residual risk that's already bounded to "leak code we're about
to show the user." Revisit if a future project's dependency file names an
internal/private registry — today only public npm/PyPI are ever needed.

## Resource and time limits

Applied via `docker run` flags on every build container, verified by the
corresponding fixture:

- **CPU**: `--cpus=2`
- **Memory**: `--memory=2g --memory-swap=2g` (no swap headroom — a memory
  bomb hits the limit, not the disk)
- **PIDs**: `--pids-limit=256` — `fixture_fork_bomb`
- **Disk**: no `overlay2` storage-opt quota exists on the default driver, so
  this is enforced by the runner: a watcher polls the workspace directory
  size every 2s and kills the container if it exceeds `2g`.
  `ponytail: poll-based, not a cgroup quota — upgrade to --storage-opt size=
  if the storage driver changes to one that supports it.` — `fixture_disk_fill`
- **Wall clock**: a per-tier timeout (Phase 2 declares the values) enforced by
  `subprocess.run(..., timeout=T)` in the runner, not a shell `timeout`
  binary the base image may lack — `fixture_infinite_loop`
- **Capabilities**: `--cap-drop=ALL --security-opt=no-new-privileges`
- **User**: `--user 1000:1000`, never root
- **Filesystem**: `--read-only` on the container root, with the workspace as
  the one writable bind mount

## Teardown guarantee

Every build container is launched with `--rm` and a deterministic name
(`sandbox-build-<uuid>`), so:

- **Normal/timeout/error exit**: the runner's `finally` block issues
  `docker kill <name>` unconditionally (idempotent if already exited);
  `--rm` guarantees removal once stopped, however it stopped.
- **Orphan case — the `sandbox` service itself crashes or is killed
  mid-build**: nothing above helps, since the process holding the
  `subprocess` handle is gone. Covered by a startup sweep: on boot, `sandbox`
  runs `docker ps --filter label=mab-sandbox -q | xargs -r docker kill`,
  reaping anything left from an unclean previous exit. Every build container
  carries `--label mab-sandbox=1` for exactly this query. Tested by
  `test_orphan_reaping.py`: start a build, SIGKILL the `sandbox` process
  mid-build, restart it, assert the sweep leaves zero `mab-sandbox`-labeled
  containers running.
- **The `backend` container crashing mid-build** does not orphan anything —
  `backend` never holds the Docker connection in this architecture. An
  in-flight build it kicked off continues under `sandbox`'s own timeout and
  is reaped exactly as above regardless of `backend`'s state. This is the
  same "a crash upstream must never leak a resource downstream" property
  `stage_node`'s degraded-events draining already relies on
  (`backend/app/graph/pipeline.py:188-262`).

## What an escape would cost

If every control above failed simultaneously and generated code broke out of
a build container: the attacker lands in the `sandbox` service's container,
which holds Docker access and *zero* application secrets — no `.env`, no
`projects.db`, no `metrics.db`, no `outputs/`. From there, reaching `backend`
requires crossing the network policy above (a separate, unattached bridge
network) — not merely being denied by convention, but unrouable at the
kernel level. The realistic worst case is Docker-daemon abuse from within
`sandbox` (e.g., launching more containers) — bounded by the same resource
ceiling every build container gets, and by `sandbox` never being reachable
from the public internet (no host port published for it in
`docker-compose.yml`).

## Verdict semantics (forward reference to Phase 2)

A sandbox that cannot start (Docker unavailable, image pull failed, `sandbox`
service unreachable) must report **`unverified`**, never `pass` and never
silently nothing — see Constraints in the improvement brief ("silence is the
enemy"). Classification detail lives in Phase 2's own design; this document
only pins the non-negotiable: unverified is not pass.
