"""Profile 3: node-express-api — Express 4 + Prisma + PostgreSQL, ES modules.

Breaks the LANGUAGE assumption. Everything react-fastapi encodes about Python
is wrong here: the syntax checker is the batched @babel/parser path rather than
ast/compile (already extension-dispatched, so it needs no new code), the
dependency manifest is package.json rather than requirements.txt with a
different version map, the import convention is relative-with-`.js` rather than
an absolute `app.` package root, and the structural ordering runs
schema → lib → service → route rather than model → schema → router.

import_fixer stays Python-only, so this profile's JS imports are FLAG-only via
validate_js_imports. That is the existing policy, not a gap: Day 19's safe-fix
analysis was Python-specific because dotted modules resolve unambiguously,
while JS specifiers are ambiguous across extensions and index files. Inventing
a JS auto-fix here would be new unmeasured surface.
"""
from . import PhaseSpec, StackProfile


def file_kind(filepath: str, description: str = "") -> str:
    """Classify an Express project file from its path, then its description.

    Returns the same kind vocabulary the backend context recipe understands
    (model/schema/router/service/config/main/other) so that recipe works
    unchanged — the paths differ, the roles do not.
    """
    low = (filepath or "").lower()
    name = low.rsplit("/", 1)[-1]
    d = (description or "").lower()

    if name == "schema.prisma" or low.endswith(".prisma"):
        return "model"
    if name in ("server.js", "app.js", "index.js") and "/routes/" not in low:
        return "main"
    if "/lib/" in low or name in ("prisma.js", "db.js"):
        return "config"
    if "/routes/" in low or "/router/" in low or "/controllers/" in low:
        return "router"
    # Services and middleware map to the recipe's "schema" role deliberately.
    # In the backend context recipe, "schema" means "the same-resource file
    # whose exact exported names this router must import correctly" — which is
    # precisely what a service module is here. Measured 2026-08-08: classified
    # as "service" instead, the recipe injected only a symbol summary and a
    # generated router imported `{ parcels }` from a module exporting
    # `listItems`. The role is what the recipe keys on, not the folder name.
    if "/services/" in low or "/repositories/" in low:
        return "schema"
    if "/middleware/" in low or "/validators/" in low or "/schemas/" in low:
        return "schema"
    if "route" in d or "endpoint" in d:
        return "router"
    if "service" in d or "business logic" in d:
        return "schema"
    if "prisma model" in d or "database model" in d:
        return "model"
    return "other"


# Structural ordering as real dependency edges: the Prisma schema defines the
# types everything else queries, and services wrap the access routes call. A
# route generated before its service invents the service's function names.
def backend_implicit_deps(task: dict, by_id: dict) -> list:
    kind = file_kind(task.get("filepath", ""), task.get("description", ""))
    if kind == "router":
        return [tid for tid, t in by_id.items()
                if file_kind(t.get("filepath", ""), t.get("description", ""))
                in ("model", "schema")]
    if kind == "schema":
        return [tid for tid, t in by_id.items()
                if file_kind(t.get("filepath", ""), t.get("description", "")) == "model"]
    return []


IMPORT_NOTE = (
    "This file is at {filepath}. Import local modules by a RELATIVE path that "
    "INCLUDES the `.js` extension (e.g. `../lib/prisma.js`), computed from the "
    "folder map — Node's ES module resolver does not add extensions. Output "
    "only the file's code.")

STRUCTURE_NOTE = (
    "PROJECT STRUCTURE ({prefix}) — import ONLY from files here, by relative "
    "path with the .js extension")

# Infra rendered deterministically — excluded from the per-task LLM loop even
# if the planner lists them, so they are never double-generated or hallucinated.
INFRA_BASENAMES = frozenset({"package.json", "server.js", "prisma.js"})


def generate_infra(state: dict, generated_files: dict, ok_route_paths: list,
                   project_id: str, project_name: str, log: list, errors: list) -> int:
    """Render + write package.json, the shared Prisma client, and server.js.
    server.js mounts ONLY the routes that generated successfully."""
    from app.utils.node_infra import (
        render_package_json, render_prisma_client, render_server)
    from app.utils.file_writer import write_project_file

    file_list = state.get("file_list", [])

    def infra_path(basename: str, default: str) -> str:
        for p in file_list or []:
            if p.rsplit("/", 1)[-1] == basename:
                return p
        return default

    pkg_content, pkg_warnings = render_package_json(project_name, generated_files)
    for w in pkg_warnings:
        errors.append(f"import_warning: {w}")

    infra = [
        (infra_path("package.json", "package.json"), pkg_content),
        (infra_path("prisma.js", "src/lib/prisma.js"), render_prisma_client()),
        (infra_path("server.js", "src/server.js"),
         render_server(project_name, ok_route_paths)),
    ]

    from app.core.connection_manager import manager
    written = 0
    for filepath, content in infra:
        result = write_project_file(project_id, filepath, content)
        if result["success"]:
            generated_files[filepath] = content
            written += 1
            log.append(f"backend_coder_agent: wrote infra {filepath} "
                       f"({result['size_bytes']} bytes)")
            manager.broadcast_sync(project_id, {
                "type": "file_written", "filename": filepath.rsplit("/", 1)[-1],
                "filepath": filepath, "phase": "backend", "task_id": "infra",
            })
        else:
            errors.append(f"backend_coder_agent: failed to write infra "
                          f"{filepath}: {result['error']}")
    return written


DEVOPS_FILES = (
    {
        "filepath": "Dockerfile",
        "description": "Dockerfile on node:20-alpine. Copy package.json and package-lock.json first and run npm ci --omit=dev for layer caching, then copy the source and run npx prisma generate. Switch to the image's existing non-root `node` user, expose 3000, and start with `node src/server.js` directly (not npm start, which swallows SIGTERM)."
    },
    {
        "filepath": "docker-compose.yml",
        "description": "Docker Compose with exactly two services: `api` and a `db` on postgres:16-alpine. The db gets a named volume for /var/lib/postgresql/data and a pg_isready healthcheck; the api uses depends_on with condition: service_healthy and reads DATABASE_URL from the environment. This project is API-only — do NOT add a frontend or nginx service."
    },
    {
        "filepath": ".github/workflows/ci.yml",
        "description": "GitHub Actions workflow on push and pull_request to main: checkout, actions/setup-node with node 20 and npm caching, npm ci, npx prisma generate, lint, test. Provide a postgres service container for the steps that touch the database."
    },
    {
        "filepath": ".env.example",
        "description": "Template environment file listing every variable the API needs, each with an explanatory comment: DATABASE_URL in Prisma's Postgres URL form, PORT, NODE_ENV, CORS_ORIGIN. Placeholder values only, never a real secret or connection string."
    },
    {
        "filepath": "README.md",
        "description": "Project README with the title and one-line description, prerequisites, setup steps (clone, configure .env, npm install, npx prisma migrate dev, docker compose up), how to call the API including the health endpoint, and a note that this project was generated by an AI multi-agent pipeline and should be reviewed before production use."
    },
)


PLAN_EXAMPLE = '''[
  {
    "id": "db_001",
    "phase": "database",
    "filename": "schema.prisma",
    "filepath": "prisma/schema.prisma",
    "description": "Prisma schema with the postgresql datasource and prisma-client-js generator, the Parcel model (trackingCode unique, destination, weightGrams, timestamps) and the Scan model related to Parcel with an index on parcelId.",
    "requires": [],
    "context_sections": ["Database Schema"],
    "estimated_complexity": "medium"
  },
  {
    "id": "be_001",
    "phase": "backend",
    "filename": "parcels.js",
    "filepath": "src/routes/parcels.js",
    "description": "Express router for /api/parcels implementing list with bounded skip/take pagination, get by id with integer validation and a 404, create returning 201, and delete returning 204. Uses the shared Prisma client from ../lib/prisma.js.",
    "requires": ["db_001"],
    "context_sections": ["Database Schema", "API Endpoints"],
    "estimated_complexity": "high"
  }
]'''


PROFILE = StackProfile(
    name="node-express-api",
    label="Node + Express REST API",
    summary=("An HTTP JSON API with no front end: Express 4 on Node 20 in ES "
             "modules, Prisma ORM over PostgreSQL, containerised with Docker "
             "Compose."),
    phases=(
        PhaseSpec(name="database", id_prefix="db", label="Schema",
                  agent_type="database", prompt_file="express_coder_agent.md",
                  context_recipe="backend", context_prefix="",
                  import_note=IMPORT_NOTE, structure_note=STRUCTURE_NOTE,
                  plan_guidance=("The Prisma schema at `prisma/schema.prisma`. "
                                 "Normally exactly one task — Prisma keeps "
                                 "every model in a single schema file.")),
        PhaseSpec(name="backend", id_prefix="be", label="API",
                  agent_type="backend_code", prompt_file="express_coder_agent.md",
                  context_recipe="backend", context_prefix="src",
                  import_note=IMPORT_NOTE, structure_note=STRUCTURE_NOTE,
                  plan_guidance=("Express routers under `src/routes/`, business "
                                 "logic under `src/services/`, middleware under "
                                 "`src/middleware/`. Do NOT plan package.json, "
                                 "src/server.js or src/lib/prisma.js — those "
                                 "are generated deterministically.")),
        PhaseSpec(name="devops", id_prefix="dv", label="DevOps",
                  agent_type="devops", prompt_file="express_devops_agent.md",
                  plan_guidance=("Deployment and CI files at the project root. "
                                 "The devops stage generates its own set, so "
                                 "plan devops tasks only for files beyond it.")),
    ),
    file_kind=file_kind,
    implicit_deps={"backend": backend_implicit_deps},
    # No shared UI vocabulary — this stack renders nothing.
    ui_contract=None,
    infra=generate_infra,
    infra_basenames=INFRA_BASENAMES,
    devops_files=DEVOPS_FILES,
    review_supported=False,     # the reviewer prompt judges React components
    plan_example=PLAN_EXAMPLE,
    min_tasks=4,
)
