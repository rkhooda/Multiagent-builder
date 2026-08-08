"""The sandbox control service — the only container in this stack that holds
the Docker socket. Holds no secret: no .env, no projects.db/metrics.db, no
writable outputs/. Reached by `backend` over the internal compose network
only (no host port published). See docs/SANDBOX_THREAT_MODEL.md.

Four endpoints. Not a job framework: no in-memory job registry either — a
workspace's own path (returned by /workspace/start) IS its handle for the
`/run` and `/probe` calls that follow, validated against the scratch root
before every use so a caller can never point either endpoint outside it.
Splitting workspace lifecycle out of /run exists for one reason: the
three-tier ladder needs Tier 1's installed packages to survive into Tier 2/3,
and each tier is a SEPARATE container (Phase 1's isolation boundary), so
packages installed to a container's own filesystem would die with it —
only what lands under the shared workspace persists across tiers.
"""
import tempfile
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

from runner import (DEFAULT_TIMEOUT_S, SCRATCH_ROOT, cleanup_workspace,
                     make_workspace, probe_boot, reap_orphans, run_in_sandbox)

app = FastAPI(title="mab-sandbox")

OUTPUTS_ROOT = Path("/outputs")  # mounted read-only — see docker-compose.yml


@app.on_event("startup")
def _startup() -> None:
    reap_orphans()


@app.get("/health")
def health():
    return {"status": "ok"}


def _validate_workspace(path_str: str) -> Path:
    root = Path(SCRATCH_ROOT or tempfile.gettempdir()).resolve()
    p = Path(path_str).resolve()
    if p != root and root not in p.parents:
        raise HTTPException(400, "workspace is not under the scratch root")
    return p


class StartRequest(BaseModel):
    project_id: str


@app.post("/workspace/start")
def workspace_start(req: StartRequest):
    source = OUTPUTS_ROOT / req.project_id
    return {"workspace": str(make_workspace(source))}


class CleanupRequest(BaseModel):
    workspace: str


@app.post("/workspace/cleanup")
def workspace_cleanup(req: CleanupRequest):
    cleanup_workspace(_validate_workspace(req.workspace))
    return {"ok": True}


class RunRequest(BaseModel):
    workspace: str
    command: list
    image: str
    timeout_s: int = DEFAULT_TIMEOUT_S
    workdir: str = ""
    env: Optional[dict] = None


@app.post("/run")
def run(req: RunRequest):
    workspace = _validate_workspace(req.workspace)
    result = run_in_sandbox(
        workspace, req.command, image=req.image, timeout_s=req.timeout_s,
        workdir=req.workdir, env=req.env,
    )
    return result.__dict__


class ProbeRequest(BaseModel):
    workspace: str
    command: list
    image: str
    port: int
    health_path: str = "/health"
    ready_timeout_s: int = 30
    workdir: str = ""
    env: Optional[dict] = None


@app.post("/probe")
def probe(req: ProbeRequest):
    workspace = _validate_workspace(req.workspace)
    result = probe_boot(
        workspace, req.command, image=req.image, port=req.port,
        health_path=req.health_path, ready_timeout_s=req.ready_timeout_s,
        workdir=req.workdir, env=req.env,
    )
    return result.__dict__
