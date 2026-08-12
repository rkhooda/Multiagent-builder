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

from . import PhaseSpec, StackProfile, TierSpec, VerifyTarget

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


# ── Deterministic frontend infra (2026-08-11) ───────────────────────────────

# Never planned, always rendered. Mirrors INFRA_BASENAMES for the backend: the
# planner is told not to produce these (see the frontend PhaseSpec below), and
# any that slip in are filtered out before generation.
FRONTEND_INFRA_BASENAMES = frozenset({
    "package.json", "vite.config.js", "index.html",
    "tailwind.config.js", "postcss.config.js",
})


def generate_frontend_infra(state: dict, generated_files: dict, project_id: str,
                            project_name: str, log: list, errors: list) -> int:
    """Render + write the frontend manifest, build config and entry point.

    Runs AFTER the frontend phase for the same reason the backend's runs after
    its own: package.json's dependency list is derived from what the generated
    files ACTUALLY import, so it cannot be written before they exist.

    Two classes of file, and the distinction matters:
      * always written — manifest, build config, HTML entry, Tailwind config.
        The planner is instructed not to produce these, so there is nothing to
        overwrite and a stale one would break the build.
      * written only if ABSENT — src/main.jsx, src/index.css, src/App.jsx. A
        coder that produced its own entry point knows things this template does
        not (route setup, providers, layout), and clobbering it would discard
        real work to install a worse version.
    """
    from app.utils import frontend_infra as FI
    from app.utils.file_writer import write_project_file
    from app.core.connection_manager import manager

    pkg_content, pkg_warnings = FI.render_package_json(project_name, generated_files)
    for w in pkg_warnings:
        errors.append(f"import_warning: {w}")

    always = [
        ("frontend/package.json", pkg_content),
        ("frontend/vite.config.js", FI.render_vite_config()),
        ("frontend/index.html", FI.render_index_html(project_name)),
        ("frontend/tailwind.config.js", FI.render_tailwind_config()),
        ("frontend/postcss.config.js", FI.render_postcss_config()),
    ]

    # An App component may live at any of these; only fall back when the coder
    # produced none of them, and point main.jsx at whichever one exists.
    app_candidates = ("frontend/src/App.jsx", "frontend/src/App.js",
                      "frontend/src/App.tsx")
    existing_app = next((p for p in app_candidates if p in generated_files), None)
    if_absent = [
        ("frontend/src/index.css", FI.render_index_css()),
        ("frontend/src/main.jsx",
         FI.render_main_jsx(f"./{existing_app.split('/')[-1]}" if existing_app
                            else "./App.jsx")),
    ]
    if not existing_app:
        if_absent.append(("frontend/src/App.jsx", FI.render_fallback_app()))
        errors.append("frontend_infra: no App component was generated — a "
                      "placeholder was written so the build still succeeds")

    written = 0
    for filepath, content in always + [(p, c) for p, c in if_absent
                                       if p not in generated_files]:
        # strip_fences stays ON: these are source files, and the guard costs
        # nothing on template output that contains no fences.
        result = write_project_file(project_id, filepath, content, add_header=False)
        if result["success"]:
            generated_files[filepath] = content
            written += 1
            log.append(f"frontend_infra: wrote {filepath} ({result['size_bytes']} bytes)")
            manager.broadcast_sync(project_id, {
                "type": "file_written", "filename": filepath.rsplit("/", 1)[-1],
                "filepath": filepath, "phase": "frontend", "task_id": "infra",
            })
        else:
            errors.append(f"frontend_infra: failed to write {filepath}: {result['error']}")
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


# The planner's worked example — the same shape the pre-profile prompt's §5
# template showed, kept verbatim so plan output does not drift.
PLAN_EXAMPLE = '''[
  {
    "id": "db_001",
    "phase": "database",
    "filename": "models.py",
    "filepath": "backend/app/models.py",
    "description": "Detailed description of exactly what this file must contain, referencing specific tables, fields, relationships, and any business logic from the architecture document.",
    "requires": [],
    "context_sections": ["Database Schema"],
    "estimated_complexity": "medium"
  },
  {
    "id": "be_001",
    "phase": "backend",
    "filename": "auth.py",
    "filepath": "backend/app/routers/auth.py",
    "description": "Implement authentication endpoints, login, register, token generation, matching the requirements document specifications.",
    "requires": ["db_001"],
    "context_sections": ["Database Schema", "API Endpoints"],
    "estimated_complexity": "high"
  }
]'''


# ── Build verification recipes (Build Verification improvement) ─────────────
#
# Two independently-verified units: the deterministic infra above guarantees
# `backend/requirements.txt` and `backend/app/main.py` (with a `/health`
# route, backend_infra.py:230) exist on every project this profile builds, so
# both targets' recipes can assume the same fixed shape every time.
#
# `pip install --target .deps` rather than a plain install: each tier is a
# SEPARATE container (Phase 1's isolation boundary — a fresh disposable
# container per `docker run`, never a lasting one), so packages installed to
# system site-packages would die with the container that installed them.
# Anything under /workspace survives across tiers because it's the same bind
# mount; PYTHONPATH=.deps is how build/boot see what install produced. npm
# needs no such trick — `npm ci` already installs into `node_modules` inside
# the project directory by default.
VERIFY_TARGETS = (
    VerifyTarget(
        name="backend", root="backend",
        install=TierSpec(
            command=("pip", "install", "--target", ".deps", "-r", "requirements.txt"),
            image="python:3.11-slim", timeout_s=180),
        build=TierSpec(
            command=("python3", "-c", "import app.main"),
            image="python:3.11-slim", timeout_s=60, env={"PYTHONPATH": ".deps"}),
        boot=TierSpec(
            command=("python3", "-m", "uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"),
            image="python:3.11-slim", timeout_s=30, env={"PYTHONPATH": ".deps"},
            port=8000, health_path="/health", ready_timeout_s=20,
            # Boot is not proof. In project e8935f86 this exact health_path
            # returned 200 while /openapi.json returned 500 (a schema module
            # used `datetime` without importing it) and every route touching
            # the database returned 500 (a back_populates naming an attribute
            # that did not exist). The journey rung is what sees that.
            journey=True),
    ),
    VerifyTarget(
        name="frontend", root="frontend",
        # `npm install`, NOT `npm ci`. ci refuses to run without a
        # package-lock.json and this pipeline cannot produce a truthful one —
        # a lockfile pins a fully resolved transitive tree, which is only
        # knowable by actually resolving it against the registry. Fabricating
        # one would be worse than having none. So the manifest is deterministic
        # and resolution happens at install time.
        install=TierSpec(
            command=("npm", "install"), image="node:20-slim", timeout_s=240),
        build=TierSpec(
            command=("npm", "run", "build"), image="node:20-slim", timeout_s=180),
        boot=TierSpec(
            # Serving dist/ needs no node_modules, so python:3.11-slim (already
            # pulled for the backend targets) covers it — one fewer image to
            # pull, and it's what the exec-based health probe needs anyway
            # (runner.probe_boot requires python3 in the boot image).
            command=("python3", "-m", "http.server", "8000"), workdir="dist",
            image="python:3.11-slim", timeout_s=30,
            port=8000, health_path="/", ready_timeout_s=15),
    ),
)


PROFILE = StackProfile(
    name="react-fastapi",
    label="React + FastAPI full-stack app",
    summary=("A full-stack web application: React 19 + Vite + TailwindCSS front "
             "end, FastAPI + SQLAlchemy + Pydantic back end, a relational "
             "database, containerised with Docker Compose."),
    phases=(
        PhaseSpec(name="database", id_prefix="db", label="Database",
                  agent_type="database", prompt_file="database_agent.md",
                  context_recipe="backend", context_prefix="backend",
                  plan_guidance=("SQLAlchemy ORM model files and Alembic "
                                 "migrations under `backend/app/models/`.")),
        PhaseSpec(name="backend", id_prefix="be", label="Backend",
                  agent_type="backend_code", prompt_file="backend_coder_agent.md",
                  context_recipe="backend", context_prefix="backend",
                  import_note=BACKEND_IMPORT_NOTE,
                  structure_note=BACKEND_STRUCTURE_NOTE,
                  plan_guidance=("Pydantic schemas, FastAPI routers and service "
                                 "modules under `backend/app/`. Do NOT plan "
                                 "config.py, database.py, main.py or "
                                 "requirements.txt — those are generated "
                                 "deterministically.")),
        PhaseSpec(name="frontend", id_prefix="fe", label="Frontend",
                  agent_type="frontend_code", prompt_file="frontend_coder_agent.md",
                  context_recipe="frontend", context_prefix="frontend/src",
                  import_note=FRONTEND_IMPORT_NOTE,
                  structure_note=FRONTEND_STRUCTURE_NOTE,
                  plan_guidance=("React components, pages, hooks and the shared "
                                 "axios client under `frontend/src/`. Do NOT "
                                 "plan package.json, vite.config.js, "
                                 "index.html, tailwind.config.js, "
                                 "postcss.config.js, main.jsx or index.css — "
                                 "those are generated deterministically.")),
        PhaseSpec(name="devops", id_prefix="dv", label="DevOps",
                  agent_type="devops", prompt_file="devops_agent.md",
                  plan_guidance=("Deployment and CI files at the project root. "
                                 "The devops stage generates a fixed set "
                                 "itself, so plan devops tasks only for files "
                                 "beyond it.")),
    ),
    plan_example=PLAN_EXAMPLE,
    file_kind=backend_file_kind,
    implicit_deps={"frontend": frontend_implicit_deps,
                   "backend": backend_implicit_deps},
    ui_contract=build_ui_contract,
    infra=generate_infra,
    infra_basenames=INFRA_BASENAMES,
    frontend_infra=generate_frontend_infra,
    frontend_infra_basenames=FRONTEND_INFRA_BASENAMES,
    devops_files=DEVOPS_FILES,
    review_supported=True,
    verify_targets=VERIFY_TARGETS,
)
