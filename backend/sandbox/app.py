"""The sandbox control service — the only container in this stack that holds
the Docker socket. Holds no secret: no .env, no projects.db/metrics.db, no
writable outputs/. Reached by `backend` over the internal compose network
only (no host port published). See docs/SANDBOX_THREAT_MODEL.md.

One endpoint. Not a job framework: each request runs one command, waits, and
returns the result.
"""
from pathlib import Path

from fastapi import FastAPI
from pydantic import BaseModel

from runner import DEFAULT_TIMEOUT_S, cleanup_workspace, make_workspace, reap_orphans, run_in_sandbox

app = FastAPI(title="mab-sandbox")

OUTPUTS_ROOT = Path("/outputs")  # mounted read-only — see docker-compose.yml


@app.on_event("startup")
def _startup() -> None:
    reap_orphans()


@app.get("/health")
def health():
    return {"status": "ok"}


class RunRequest(BaseModel):
    project_id: str
    command: list
    image: str
    timeout_s: int = DEFAULT_TIMEOUT_S


@app.post("/run")
def run(req: RunRequest):
    """Copy the project's generated files into a fresh workspace, run one
    command against them, tear down. `project_id` is looked up under the
    read-only outputs mount — this endpoint never accepts an arbitrary host
    path, so a caller cannot point it outside the projects tree."""
    source = OUTPUTS_ROOT / req.project_id
    workspace = make_workspace(source)
    try:
        result = run_in_sandbox(
            workspace, req.command, image=req.image, timeout_s=req.timeout_s,
        )
    finally:
        cleanup_workspace(workspace)
    return result.__dict__
