#!/usr/bin/env python3
"""Plan-shape experiment — Improvement 03, Phase 2.

Runs the PLANNER ALONE over short briefs of clearly different shapes and
asserts properties of the plan it returns. No code generation, no coder calls,
no full pipeline: the scarce Groq pool is never touched.

Why this is affordable where the last two improvements were not. The planner
runs on gemini-2.5-flash (docs/PROVIDERS.md — 1M tokens/day, never the binding
limit), and a plan is JSON, so "does a static-site brief yield zero database
tasks?" is a free assertion over a cheap call. Improvements 01 and 02 both
ended UNPROVEN because their evidence required full-pipeline runs.

Each brief needs an architecture doc to plan against, so the harness runs
architecture → planning. Architecture runs on Groq, so --stub-architecture
synthesises a minimal architecture doc locally instead (zero Groq spend); that
is the default. --live-architecture uses the real agent when the quota allows.

  python3 scripts/plan_shape_test.py                    # all briefs
  python3 scripts/plan_shape_test.py --brief static_site
  python3 scripts/plan_shape_test.py --out /tmp/shapes.json
"""
import argparse
import json
import os
import sys
from collections import Counter

BACKEND_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BACKEND_DIR)

os.environ.setdefault("LLM_CACHE", "false")   # a cache hit would prove nothing

from app.agents.planning_agent import planning_agent          # noqa: E402
from app.profiles import get_profile                          # noqa: E402


# ── The briefs ───────────────────────────────────────────────────────────────
# Chosen so the CORRECT plan shape differs sharply between them. Each carries
# the shape assertion it exists to test, stated before any plan is seen.

BRIEFS = {
    "static_site": {
        "profile": "react-fastapi",
        "name": "Fernwood Studio",
        "brief": (
            "A one-page marketing site for a small architecture studio. A hero "
            "with the studio name and tagline, a gallery of six past projects "
            "with photo and caption, a short about section, and a contact "
            "block with an email address and phone number. No accounts, no "
            "database, no forms that submit anywhere, nothing to log into. "
            "Purely informational content that never changes at runtime."
        ),
        "expect": {"database": 0, "backend": 0},
        "why": "A static marketing site stores nothing and serves no API.",
        "files": ["frontend/src/App.jsx", "frontend/src/components/Hero.jsx",
                  "frontend/src/components/Gallery.jsx",
                  "frontend/src/components/About.jsx",
                  "frontend/src/components/Contact.jsx",
                  "frontend/src/main.jsx"],
    },
    "api_only": {
        "profile": "react-fastapi",
        "name": "Parcel Tracking API",
        "brief": (
            "A REST API only — no user interface of any kind. It tracks "
            "parcels: create a parcel with a destination address and weight, "
            "record scan events against it as it moves through depots, and "
            "query a parcel's current status and full scan history. Consumed "
            "by third-party clients over HTTP. There is no web front end and "
            "none should be built."
        ),
        "expect": {"frontend": 0},
        "why": "An API-only service has no UI to generate.",
        "files": ["backend/app/models/parcel.py", "backend/app/models/scan.py",
                  "backend/app/schemas/parcel.py",
                  "backend/app/routers/parcels.py",
                  "backend/app/routers/scans.py"],
    },
    "full_stack": {
        "profile": "react-fastapi",
        "name": "TodoSimple",
        "brief": (
            "A small todo application. Users sign up and log in, create todo "
            "items with a title and optional due date, mark them complete, "
            "filter by status, and delete them. Each user sees only their own "
            "todos."
        ),
        "expect": {},   # every phase legitimately present
        "why": "The tuned control case — all four phases are real here.",
        "files": ["backend/app/models/user.py", "backend/app/models/todo.py",
                  "backend/app/schemas/todo.py", "backend/app/routers/todos.py",
                  "backend/app/routers/auth.py", "frontend/src/App.jsx",
                  "frontend/src/lib/api.js", "frontend/src/pages/TodosPage.jsx",
                  "frontend/src/components/TodoItem.jsx"],
    },
    # ── The two new targets, planned under their OWN profiles ────────────────
    "static_site_profile": {
        "profile": "static-site",
        "name": "Fernwood Studio",
        "brief": (
            "A small marketing site for an architecture studio: a home page "
            "with a hero and short intro, a projects page with a gallery of "
            "past work, and a contact page with an email address and studio "
            "location. Purely informational."
        ),
        "expect": {"database": 0, "backend": 0},
        "why": "The static-site profile declares no database or backend phase at all.",
        "files": ["src/index.html", "src/projects.html", "src/contact.html",
                  "src/styles/main.css", "src/scripts/main.js"],
    },
    "express_profile": {
        "profile": "node-express-api",
        "name": "Parcel Tracking API",
        "brief": (
            "A REST API that tracks parcels. Create a parcel with a "
            "destination and weight, record scan events against it as it "
            "moves through depots, and query a parcel's status and full scan "
            "history. No user interface."
        ),
        "expect": {"frontend": 0},
        "why": "The node-express-api profile declares no frontend phase.",
        # Includes the deterministic infra paths, as a real architecture doc
        # would: the profile resolves them from file_list, and the scorer's
        # top tier requires a file to be in the plan's file list.
        "files": ["prisma/schema.prisma", "src/routes/parcels.js",
                  "src/routes/scans.js", "src/services/parcels.js",
                  "src/middleware/errors.js",
                  "package.json", "src/lib/prisma.js", "src/server.js"],
        "schema": (
            "```prisma\n"
            "model Parcel {\n"
            "  id           Int      @id @default(autoincrement())\n"
            "  trackingCode String   @unique\n"
            "  destination  String\n"
            "  weightGrams  Int\n"
            "  createdAt    DateTime @default(now())\n"
            "  updatedAt    DateTime @updatedAt\n"
            "  scans        Scan[]\n"
            "}\n\n"
            "model Scan {\n"
            "  id        Int      @id @default(autoincrement())\n"
            "  parcel    Parcel   @relation(fields: [parcelId], references: [id], onDelete: Cascade)\n"
            "  parcelId  Int\n"
            "  depot     String\n"
            "  scannedAt DateTime @default(now())\n\n"
            "  @@index([parcelId])\n"
            "}\n```"
        ),
        "api": (
            "| Method | Path | Auth | Description | Response |\n"
            "|--------|------|------|-------------|----------|\n"
            "| GET | /parcels | none | list parcels, paginated | `[{\"id\":1,\"trackingCode\":\"AB12\",\"destination\":\"Leeds\",\"weightGrams\":900}]` |\n"
            "| GET | /parcels/:id | none | one parcel by id | `{\"id\":1,\"trackingCode\":\"AB12\",\"destination\":\"Leeds\",\"weightGrams\":900}` |\n"
            "| POST | /parcels | none | create a parcel | `{\"id\":2,\"trackingCode\":\"CD34\",\"destination\":\"Hull\",\"weightGrams\":450}` |\n"
            "| DELETE | /parcels/:id | none | delete a parcel | empty body, 204 |\n"
            "| GET | /scans | none | scan history for a parcel | `[{\"id\":7,\"parcelId\":1,\"depot\":\"Leeds North\",\"scannedAt\":\"2026-01-04T09:12:00Z\"}]` |\n"
            "| POST | /scans | none | record a scan against a parcel | `{\"id\":8,\"parcelId\":1,\"depot\":\"Hull Central\"}` |"
        ),
    },
    "cli_tool": {
        "profile": "react-fastapi",
        "name": "logsift",
        "brief": (
            "A command-line tool that filters and summarises log files. It "
            "reads a log file from a path argument, filters lines by level and "
            "by a time range, and prints either the matching lines or a count "
            "summary grouped by level. Runs entirely in a terminal. No web "
            "server, no browser interface, no database."
        ),
        "expect": {"frontend": 0, "database": 0},
        "why": "A CLI tool has neither a UI nor persistent storage.",
        "files": ["backend/app/cli.py", "backend/app/filters.py",
                  "backend/app/summarise.py"],
    },
}


def stub_architecture(spec: dict) -> str:
    """A minimal architecture doc in the shape the planner expects.

    Deliberately NOT LLM-generated: the variable under test is what the PLANNER
    does with a given project shape, so holding the architecture fixed and
    local removes both a Groq call and a confound. It states the folder tree
    and, where the project has one, an API table — and states plainly when the
    project has neither a database nor an API.
    """
    tree = "\n".join(f"  {p}" for p in spec["files"])
    has_api = any("/routes/" in p or "/routers/" in p for p in spec["files"])
    has_db = any("/models/" in p or p.endswith(".prisma") for p in spec["files"])

    # Domain-specific when the brief supplies it. A generic `items` table here
    # is not neutral — measured 2026-08-08, the Express coders faithfully
    # implemented an `Item` model for a parcel-tracking brief, which scores as
    # a coder defect when it is really the architecture the harness handed
    # them. The stub must describe the project the brief describes.
    api_section = (
        f"## API Endpoints\n\n{spec['api']}\n" if spec.get("api") and has_api else
        "## API Endpoints\n\n"
        "| Method | Path | Auth | Description | Response |\n"
        "|--------|------|------|-------------|----------|\n"
        "| GET | /items | none | list items | `[{\"id\": 1}]` |\n"
        if has_api else
        "## API Endpoints\n\nThis project exposes no HTTP API.\n"
    )
    db_section = (
        f"## Database Schema\n\n{spec['schema']}\n" if spec.get("schema") and has_db else
        "## Database Schema\n\n```sql\nCREATE TABLE items (id INTEGER PRIMARY KEY, "
        "title TEXT NOT NULL);\n```\n"
        if has_db else
        "## Database Schema\n\nThis project stores no data and has no database.\n"
    )
    return (
        f"# Architecture — {spec['name']}\n\n"
        f"## Overview\n\n{spec['brief']}\n\n"
        f"## Folder Structure\n\n```text\n{tree}\n```\n\n"
        f"{db_section}\n{api_section}\n"
        f"## Component Hierarchy\n\n- App (props: none)\n\n"
    )


def run_brief(key: str, spec: dict) -> dict:
    profile = get_profile(spec["profile"])
    state = {
        "project_id": f"shape-{key}",
        "project_name": spec["name"],
        "brief": spec["brief"],
        "architecture_doc": stub_architecture(spec),
        "file_list": spec["files"],
        "tech_stack": json.dumps({"frontend": "React 19 + Vite + TailwindCSS",
                                  "backend": "FastAPI (Python 3.11)",
                                  "database": "SQLite"}),
        "stack_profile": spec["profile"],
        "log": [], "errors": [],
    }
    result = planning_agent(state)
    tasks = json.loads(result["implementation_plan"])
    counts = Counter(t["phase"] for t in tasks)
    complexity = Counter(t.get("estimated_complexity", "medium") for t in tasks)

    failures = []
    for phase, expected in spec["expect"].items():
        actual = counts.get(phase, 0)
        if actual != expected:
            failures.append(f"{phase}: expected {expected}, got {actual}")

    return {
        "brief": key,
        "profile": spec["profile"],
        "why": spec["why"],
        "total_tasks": len(tasks),
        "phase_counts": dict(counts),
        "complexity": dict(complexity),
        "expected": spec["expect"],
        "shape_ok": not failures,
        "failures": failures,
        "declared_phases": profile.phase_names(),
        "task_ids": [t["id"] for t in tasks],
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--brief", action="append", choices=sorted(BRIEFS),
                    help="run only these briefs (repeatable)")
    ap.add_argument("--out", help="write the results JSON here")
    args = ap.parse_args()

    keys = args.brief or sorted(BRIEFS)
    results = []
    for key in keys:
        print(f"\n=== {key} ===", flush=True)
        try:
            row = run_brief(key, BRIEFS[key])
        except Exception as e:                       # noqa: BLE001
            print(f"  ERROR {type(e).__name__}: {e}", flush=True)
            results.append({"brief": key, "error": f"{type(e).__name__}: {e}",
                            "shape_ok": False})
            continue
        results.append(row)
        verdict = "PASS" if row["shape_ok"] else "FAIL"
        print(f"  {verdict}  {row['total_tasks']} tasks  {row['phase_counts']}")
        for f in row["failures"]:
            print(f"        {f}")

    ok = sum(1 for r in results if r.get("shape_ok"))
    print(f"\n{ok}/{len(results)} briefs produced the expected plan shape")

    if args.out:
        with open(args.out, "w") as fh:
            json.dump(results, fh, indent=2)
        print(f"wrote {args.out}")
    return 0 if ok == len(results) else 1


if __name__ == "__main__":
    sys.exit(main())
