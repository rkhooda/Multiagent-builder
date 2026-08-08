"""Profile 1: react-fastapi — an exact description of what the system did
before profiles existed (Phase 1 of Improvement 03).

Nothing here is new behaviour. The prompt files are the SAME tuned Day 18-21
artifacts at their original prompts/ paths (so the A/B harness and golden
fixtures keep working untouched); the note strings are the context_builder
constants re-declared as explicit profile data; the infra renderer and devops
file list moved here verbatim from the coder agents. Behaviour-identity is
pinned by tests/test_profiles.py and the golden --rescore gate.
"""
from pathlib import Path

from app.agents.context_builder import (
    BACKEND_IMPORT_NOTE,
    BACKEND_STRUCTURE_NOTE,
    FRONTEND_IMPORT_NOTE,
    FRONTEND_STRUCTURE_NOTE,
    backend_file_kind,
    build_ui_contract,
    _resource_of,
    _same_resource,
)

from . import PhaseSpec, StackProfile

# ── Structural dependency shapes (moved from the coder agents, Day 19/20) ────

# Backend structural ordering (model -> schema -> router) enforced as real
# dependency edges, not a sort key.
STRUCTURAL_DEP_KINDS = {"model", "schema"}


def _is_lib_file(filepath: str) -> bool:
    """The API client / shared lib everything imports — generate these first."""
    low = filepath.lower()
    return "/lib/" in low or low.endswith("api.js") or low.endswith("api.jsx")


def frontend_implicit_deps(task: dict, by_id: dict) -> list:
    """Every non-lib file waits on the shared API client(s): a component that
    imports the client must not generate before it exists."""
    if _is_lib_file(task.get("filepath", "")):
        return []
    return [tid for tid, t in by_id.items() if _is_lib_file(t.get("filepath", ""))]


def backend_implicit_deps(task: dict, by_id: dict) -> list:
    """A router/service is generated only after the SAME-resource model/schema
    tasks, so its FULL model/schema context is real before it imports them.
    Same-resource (not all-schemas) keeps blocking precise."""
    kind = backend_file_kind(task.get("filepath", ""), task.get("description", ""))
    if kind not in ("router", "service"):
        return []
    resource = _resource_of(task.get("filepath", ""))
    return [tid for tid, t in by_id.items()
            if backend_file_kind(t.get("filepath", ""), t.get("description", "")) in STRUCTURAL_DEP_KINDS
            and _same_resource(_resource_of(t.get("filepath", "")), resource)]


# ── Deterministic backend infra (moved verbatim from backend_coder_agent) ────

INFRA_BASENAMES = frozenset({"config.py", "database.py", "main.py", "requirements.txt"})


def _basename(filepath: str) -> str:
    return filepath.rsplit("/", 1)[-1]


def _infra_path(file_list: list, basename: str, default: str) -> str:
    """Resolve an infra file's path from the planned tree if present, else the
    conventional default, so infra lands where the architecture expects it."""
    for p in file_list or []:
        if _basename(p) == basename:
            return p
    return default


def generate_infra(state: dict, generated_files: dict, ok_router_paths: list,
                   project_id: str, project_name: str, log: list, errors: list) -> int:
    """Render + write the deterministic Python infra files. Returns count written.
    main.py registers ONLY the routers that generated successfully."""
    from app.utils.backend_infra import (
        render_config, render_database, render_main, render_requirements)
    from app.utils.file_writer import write_project_file

    file_list = state.get("file_list", [])
    tech_stack = state.get("tech_stack", "")

    req_content, req_warnings = render_requirements(tech_stack, generated_files)
    for w in req_warnings:
        errors.append(f"import_warning: {w}")

    infra = [
        (_infra_path(file_list, "config.py", "backend/app/config.py"), render_config()),
        (_infra_path(file_list, "database.py", "backend/app/database.py"), render_database()),
        (_infra_path(file_list, "main.py", "backend/app/main.py"),
         render_main(project_name, ok_router_paths)),
        (_infra_path(file_list, "requirements.txt", "backend/requirements.txt"), req_content),
    ]

    from app.core.connection_manager import manager
    written = 0
    for filepath, content in infra:
        result = write_project_file(project_id, filepath, content)
        if result["success"]:
            generated_files[filepath] = content
            written += 1
            log.append(f"backend_coder_agent: wrote infra {filepath} ({result['size_bytes']} bytes)")
            manager.broadcast_sync(project_id, {
                "type": "file_written", "filename": _basename(filepath),
                "filepath": filepath, "phase": "backend", "task_id": "infra",
            })
        else:
            errors.append(f"backend_coder_agent: failed to write infra {filepath}: {result['error']}")
    return written


# ── DevOps file set (moved verbatim from devops_agent) ───────────────────────

DEVOPS_FILES = (
    {
        "filepath": "Dockerfile",
        "description": "Multi-stage Dockerfile for the backend service. Python base image matching the tech stack, non-root user, proper layer caching (copy requirements.txt first, install, then copy code), expose the correct port, correct CMD to run the app."
    },
    {
        "filepath": "frontend/Dockerfile",
        "description": "Multi-stage Dockerfile for the frontend. Node build stage, nginx-alpine serve stage. Build the app then serve the static files."
    },
    {
        "filepath": "docker-compose.yml",
        "description": "Docker Compose file with backend, frontend, and database services (matching the tech stack's database choice). Include healthchecks, named volumes for data persistence, and environment variable references from .env."
    },
    {
        "filepath": "frontend/nginx.conf",
        "description": "Nginx config that proxies /api/ and /ws/ to the backend service, serves frontend static files, handles SPA routing with try_files, and enables gzip compression."
    },
    {
        "filepath": ".github/workflows/ci.yml",
        "description": "GitHub Actions CI workflow with steps for: install dependencies, lint, run tests, build. Trigger on push and pull_request to main branch."
    },
    {
        "filepath": ".env.example",
        "description": "Template environment file listing every environment variable the application needs, with descriptive comments explaining each one, and no real secret values (use placeholder text like 'your_key_here')."
    },
    {
        "filepath": "README.md",
        "description": "Project README with: project title and one-line description, prerequisites, setup instructions (clone, configure .env, docker compose up), development workflow notes, and a note that this project was generated by an AI multi-agent pipeline and should be reviewed before production use."
    },
)


PROFILE = StackProfile(
    name="react-fastapi",
    label="React + FastAPI full-stack app",
    phases=(
        PhaseSpec(name="database", id_prefix="db", label="Database",
                  agent_type="database", prompt_file="database_agent.md",
                  context_recipe="backend", context_prefix="backend"),
        PhaseSpec(name="backend", id_prefix="be", label="Backend",
                  agent_type="backend_code", prompt_file="backend_coder_agent.md",
                  context_recipe="backend", context_prefix="backend",
                  import_note=BACKEND_IMPORT_NOTE,
                  structure_note=BACKEND_STRUCTURE_NOTE),
        PhaseSpec(name="frontend", id_prefix="fe", label="Frontend",
                  agent_type="frontend_code", prompt_file="frontend_coder_agent.md",
                  context_recipe="frontend", context_prefix="frontend/src",
                  import_note=FRONTEND_IMPORT_NOTE,
                  structure_note=FRONTEND_STRUCTURE_NOTE),
        PhaseSpec(name="devops", id_prefix="dv", label="DevOps",
                  agent_type="devops", prompt_file="devops_agent.md"),
    ),
    file_kind=backend_file_kind,
    implicit_deps={"frontend": frontend_implicit_deps,
                   "backend": backend_implicit_deps},
    ui_contract=build_ui_contract,
    infra=generate_infra,
    infra_basenames=INFRA_BASENAMES,
    devops_files=DEVOPS_FILES,
    review_supported=True,
)
