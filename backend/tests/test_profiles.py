#!/usr/bin/env python3
"""Stack Profile extraction — behaviour-identity pins (Improvement 03, Phase 1).

The extraction's whole contract is "no behaviour change": the react-fastapi
profile must describe exactly what the hard-coded pipeline did. These checks
pin the equivalences that make that claim mechanical rather than hopeful:
prompt bytes, context bytes, implicit-edge sets, infra/devops declarations.
Offline, zero API calls.
"""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.profiles import (  # noqa: E402
    CANONICAL_PHASES, DEFAULT_PROFILE, PROFILES, active_profile, get_profile,
    PROMPTS_DIR,
)
from app.agents.context_builder import build_file_context  # noqa: E402

PASS, FAIL = 0, 0


def check(name, cond):
    global PASS, FAIL
    if cond:
        PASS += 1
    else:
        FAIL += 1
        print(f"  FAIL {name}")


RF = PROFILES["react-fastapi"]


# ── Loader ───────────────────────────────────────────────────────────────────
check("default profile is react-fastapi", DEFAULT_PROFILE == "react-fastapi")
check("unknown name falls back to default", get_profile("no-such") is RF)
check("empty state resolves to default", active_profile({}) is RF)
check("state key resolves", active_profile({"stack_profile": "react-fastapi"}) is RF)
check("every declared phase is canonical",
      all(p.name in CANONICAL_PHASES for prof in PROFILES.values() for p in prof.phases))
check("id prefixes unique within a profile",
      all(len({p.id_prefix for p in prof.phases}) == len(prof.phases)
          for prof in PROFILES.values()))

# ── Prompt bytes: the profile serves EXACTLY the tuned files ─────────────────
for phase, fname in (("frontend", "frontend_coder_agent.md"),
                     ("backend", "backend_coder_agent.md"),
                     ("database", "database_agent.md"),
                     ("devops", "devops_agent.md")):
    on_disk = (PROMPTS_DIR / fname).read_text(encoding="utf-8")
    check(f"prompt_for({phase}) == prompts/{fname}", RF.prompt_for(phase) == on_disk)

# ── Context bytes: profile-driven call == pre-profile default call ───────────
_STATE = {
    "architecture_doc": (
        "## API Endpoints\n"
        "| Method | Path | Auth | Description | Response |\n"
        "|--------|------|------|-------------|----------|\n"
        "| GET | /notes | none | list notes | [{\"id\":1}] |\n\n"
        "## Component Hierarchy\n- NotesPage (props: none)\n  - NoteCard (props: note, onDelete)\n\n"
        "## Database Schema\n```sql\nCREATE TABLE notes (id INTEGER PRIMARY KEY, title TEXT);\n```\n"
    ),
    "tech_stack": '{"frontend": "React 19 + Vite + TailwindCSS", "backend": "FastAPI", "database": "SQLite"}',
    "implementation_plan": "[]",
    "generated_files": {},
    "file_list": ["frontend/src/lib/api.js", "frontend/src/components/NoteCard.jsx",
                  "backend/app/models/note.py", "backend/app/routers/notes.py"],
    "log": [],
}
_FE_TASK = {"id": "fe_001", "phase": "frontend", "filepath": "frontend/src/components/NoteCard.jsx",
            "description": "Note card component", "requires": []}
_BE_TASK = {"id": "be_001", "phase": "backend", "filepath": "backend/app/routers/notes.py",
            "description": "Notes CRUD router", "requires": []}

fe_spec = RF.phase("frontend")
be_spec = RF.phase("backend")

default_fe = build_file_context(dict(_FE_TASK), dict(_STATE), phase_prefix="frontend/src")
profile_fe = build_file_context(dict(_FE_TASK), dict(_STATE),
                                phase_prefix=fe_spec.context_prefix,
                                phase=fe_spec.context_recipe,
                                file_kind=RF.file_kind,
                                import_note=fe_spec.import_note,
                                structure_note=fe_spec.structure_note)
check("frontend context byte-identical via profile", default_fe == profile_fe)

default_be = build_file_context(dict(_BE_TASK), dict(_STATE), phase_prefix="backend",
                                phase="backend")
profile_be = build_file_context(dict(_BE_TASK), dict(_STATE),
                                phase_prefix=be_spec.context_prefix,
                                phase=be_spec.context_recipe,
                                file_kind=RF.file_kind,
                                import_note=be_spec.import_note,
                                structure_note=be_spec.structure_note)
check("backend context byte-identical via profile", default_be == profile_be)
check("backend context keeps the app-root closing line",
      "Use absolute `app.` imports" in profile_be)

# ── Implicit dependency edges match the pre-profile shapes ───────────────────
fe_tasks = [
    {"id": "fe_001", "filepath": "frontend/src/lib/api.js"},
    {"id": "fe_002", "filepath": "frontend/src/components/NoteCard.jsx"},
]
by_id = {t["id"]: t for t in fe_tasks}
fe_deps = RF.implicit_deps["frontend"]
check("frontend: non-lib waits on lib", fe_deps(fe_tasks[1], by_id) == ["fe_001"])
check("frontend: lib waits on nothing", fe_deps(fe_tasks[0], by_id) == [])

be_tasks = [
    {"id": "be_001", "filepath": "backend/app/schemas/note.py", "description": ""},
    {"id": "be_002", "filepath": "backend/app/routers/notes.py", "description": ""},
    {"id": "be_003", "filepath": "backend/app/routers/users.py", "description": ""},
]
be_by_id = {t["id"]: t for t in be_tasks}
be_deps = RF.implicit_deps["backend"]
check("backend: router waits on same-resource schema",
      be_deps(be_tasks[1], be_by_id) == ["be_001"])
check("backend: other-resource router does not",
      be_deps(be_tasks[2], be_by_id) == [])
check("backend: schema itself waits on nothing", be_deps(be_tasks[0], be_by_id) == [])

# ── Infra + devops declarations match what the agents hard-coded ─────────────
check("infra basenames unchanged",
      RF.infra_basenames == frozenset({"config.py", "database.py", "main.py",
                                       "requirements.txt"}))
check("infra renderer declared", callable(RF.infra))
check("devops set has the 7 pre-profile files",
      [d["filepath"] for d in RF.devops_files] == [
          "Dockerfile", "frontend/Dockerfile", "docker-compose.yml",
          "frontend/nginx.conf", ".github/workflows/ci.yml", ".env.example",
          "README.md"])
check("ui contract builder is the Day-26 one",
      RF.ui_contract is not None and
      "Tokens — use these exact classes" in RF.ui_contract(_STATE["tech_stack"], "[]"))
check("review supported on the tuned stack only", RF.review_supported is True)


# ── Planning prompt: profile vocabulary is injected, not hard-coded ──────────
from app.agents.planning_agent import _system_prompt  # noqa: E402
from app.validation import _valid_plan  # noqa: E402

prompt = _system_prompt(RF)
check("no placeholder survives injection",
      "{PHASE_TABLE}" not in prompt and "{PLAN_EXAMPLE}" not in prompt
      and "{PROFILE_LABEL}" not in prompt and "{PROFILE_NAME}" not in prompt)
check("prompt names every declared phase",
      all(f"`{p.name}`" in prompt for p in RF.phases))
check("prompt states absent phases are valid",
      "MUST BE ABSENT" in prompt and "not a failure" in prompt)
check("prompt carries the profile's worked example",
      RF.plan_example in prompt)


def _plan(*tasks):
    return json.dumps(list(tasks))


def _t(tid, phase, filepath, requires=None):
    return {
        "id": tid, "phase": phase, "filename": filepath.rsplit("/", 1)[-1],
        "filepath": filepath, "requires": requires or [],
        "context_sections": ["Database Schema"], "estimated_complexity": "low",
        "description": "A sufficiently long description of exactly what this "
                       "file contains, naming concrete tables and endpoints.",
    }


_BASE = [_t(f"fe_{i:03d}", "frontend", f"frontend/src/components/C{i}.jsx")
         for i in range(1, 9)]
_ST = {"file_list": [], "stack_profile": "react-fastapi"}

check("a well-formed single-phase plan passes shape checks",
      _valid_plan(_plan(*_BASE), _ST) == [])

# 1. a phase the profile does not declare. Two distinct rejections, both loud:
#    a NON-canonical name never survives the TaskSchema Literal, and a
#    canonical name the active profile omits is caught by _plan_shape. The
#    second is the case Improvement 03 introduces, so it is checked against a
#    profile trimmed to one phase rather than waiting for a real second target.
bad_phase = _BASE[:-1] + [dict(_BASE[-1], phase="mobile", id="mb_001")]
errs = _valid_plan(_plan(*bad_phase), _ST)
check("non-canonical phase never validates",
      any("mb_001" in e or "phase" in e.lower() for e in errs))

from dataclasses import replace  # noqa: E402
from app.agents.utils import parse_and_validate_plan  # noqa: E402
from app.validation import _plan_shape  # noqa: E402

frontend_only = replace(RF, phases=(RF.phase("frontend"),))
mixed, _ = parse_and_validate_plan(_plan(
    *_BASE[:-1],
    _t("db_001", "database", "backend/app/models/note.py")))
errs = _plan_shape(mixed, frontend_only)
check("canonical phase the profile omits is rejected at plan time",
      any("does not build" in e for e in errs))
check("...and the message names the allowed phases",
      any("frontend" in e for e in errs))

# 2. id prefix disagreeing with phase
bad_prefix = _BASE[:-1] + [dict(_BASE[-1], id="be_001")]
errs = _valid_plan(_plan(*bad_prefix), _ST)
check("id prefix must match its phase",
      any("id prefix" in e for e in errs))

# 3. dependency cycle
cyc = _BASE[:-2] + [
    _t("fe_007", "frontend", "frontend/src/components/A.jsx", ["fe_008"]),
    _t("fe_008", "frontend", "frontend/src/components/B.jsx", ["fe_007"]),
]
errs = _valid_plan(_plan(*cyc), _ST)
check("dependency cycle is caught before generation",
      any("cycle" in e.lower() for e in errs))

# 4. two tasks owning one filepath
dup = _BASE[:-1] + [dict(_BASE[-1], filepath=_BASE[0]["filepath"])]
errs = _valid_plan(_plan(*dup), _ST)
check("duplicate filepath rejected regardless of decomposition flag",
      any("Duplicate filepath" in e for e in errs))

# 5. absent phases are NOT an error — the whole point of Phase 2
check("a plan with only frontend tasks is legal",
      not any("database" in e or "absent" in e.lower()
              for e in _valid_plan(_plan(*_BASE), _ST)))

# ── Profiles 2 and 3: the seams they exist to break ─────────────────────────
SS = PROFILES["static-site"]
EX = PROFILES["node-express-api"]

# static-site breaks "every phase is always present"
check("static-site declares no database phase", SS.phase("database") is None)
check("static-site declares no backend phase", SS.phase("backend") is None)
check("static-site declares frontend + devops only",
      SS.phase_names() == ["frontend", "devops"])
check("static-site renders no deterministic infra", SS.infra is None)
check("static-site has its own coder prompt",
      SS.phase("frontend").prompt_file == "static_site_coder_agent.md")
check("static-site prompt forbids frameworks",
      all(s in SS.prompt_for("frontend")
          for s in ("No React", "no build step", "<!DOCTYPE html>")))
check("static-site prompt carries worked examples",
      SS.prompt_for("frontend").count("EXAMPLE —") >= 3)
check("static-site contract is CSS tokens, not Tailwind",
      "--color-accent" in SS.ui_contract("", "[]")
      and "Tailwind" not in SS.ui_contract("", "[]"))
check("static-site floor is below the react-fastapi floor",
      SS.min_tasks < RF.min_tasks)
check("static-site: pages wait on the stylesheet",
      SS.implicit_deps["frontend"](
          {"id": "fe_002", "filepath": "src/index.html"},
          {"fe_001": {"id": "fe_001", "filepath": "src/styles/main.css"},
           "fe_002": {"id": "fe_002", "filepath": "src/index.html"}}) == ["fe_001"])
check("static-site: the stylesheet waits on nothing",
      SS.implicit_deps["frontend"](
          {"id": "fe_001", "filepath": "src/styles/main.css"},
          {"fe_001": {"id": "fe_001", "filepath": "src/styles/main.css"}}) == [])
check("static-site file kinds are extension-driven",
      [SS.file_kind(p) for p in ("src/index.html", "src/styles/main.css",
                                 "src/scripts/main.js", "src/data/x.json")]
      == ["page", "style", "script", "data"])

# node-express-api breaks the LANGUAGE assumption
check("express declares no frontend phase", EX.phase("frontend") is None)
check("express keeps database + backend + devops",
      EX.phase_names() == ["database", "backend", "devops"])
check("express has its own coder prompt",
      EX.phase("backend").prompt_file == "express_coder_agent.md")
check("express prompt mandates ESM with .js extensions",
      all(s in EX.prompt_for("backend")
          for s in ("never `require()`", "MUST include the `.js` extension")))
check("express prompt forbids a second PrismaClient",
      "NEVER call `new PrismaClient()`" in EX.prompt_for("backend"))
check("express prompt carries worked examples",
      EX.prompt_for("backend").count("EXAMPLE —") >= 3)
check("express import note is relative-with-extension, not app-rooted",
      ".js` extension" in EX.phase("backend").import_note
      and "app." not in EX.phase("backend").import_note)
check("express declares no UI contract", EX.ui_contract is None)
check("express infra basenames are the JS ones",
      EX.infra_basenames == frozenset({"package.json", "server.js", "prisma.js"}))
check("express: route waits on schema and service",
      sorted(EX.implicit_deps["backend"](
          {"id": "be_002", "filepath": "src/routes/parcels.js", "description": ""},
          {"db_001": {"id": "db_001", "filepath": "prisma/schema.prisma", "description": ""},
           "be_001": {"id": "be_001", "filepath": "src/services/parcels.js", "description": ""},
           "be_002": {"id": "be_002", "filepath": "src/routes/parcels.js", "description": ""}}))
      == ["be_001", "db_001"])
check("express file kinds map onto the backend recipe vocabulary",
      [EX.file_kind(p) for p in ("prisma/schema.prisma", "src/server.js",
                                 "src/lib/prisma.js", "src/routes/p.js",
                                 "src/services/p.js")]
      == ["model", "main", "config", "router", "schema"])
# A service module IS this stack's "schema" role: the same-resource file whose
# exact exported names the router must import. Classified as "service" the
# recipe injects only a symbol summary, and a generated router imported a name
# the service does not export (measured 2026-08-08).
check("express services get full-content injection as schema-role files",
      EX.file_kind("src/services/parcels.js") == "schema")

# Neither new profile inherits the React reviewer
check("new profiles do not claim React review support",
      SS.review_supported is False and EX.review_supported is False)

# Every profile must be self-consistent enough to actually run
for prof in PROFILES.values():
    for spec in prof.phases:
        check(f"{prof.name}/{spec.name}: prompt file exists",
              (PROMPTS_DIR / spec.prompt_file).is_file())
    check(f"{prof.name}: has a plan example", bool(prof.plan_example.strip()))
    check(f"{prof.name}: has a summary", bool(prof.summary.strip()))
    check(f"{prof.name}: prompt injects cleanly",
          "{PHASE_TABLE}" not in _system_prompt(prof))
    check(f"{prof.name}: example plan validates against its own profile",
          _plan_shape(parse_and_validate_plan(prof.plan_example)[0], prof) == [])

# ── Node infra renderers ────────────────────────────────────────────────────
from app.utils.node_infra import (  # noqa: E402
    render_package_json, render_prisma_client, render_server)

pkg, warns = render_package_json("Parcel Tracking API", {
    "src/routes/parcels.js": "import express from 'express';\n"
                             "import { prisma } from '../lib/prisma.js';\n"
                             "import helmet from 'helmet';\n",
})
pkg_data = json.loads(pkg)
check("package.json declares ES modules", pkg_data["type"] == "module")
check("package.json always pins the core stack",
      all(p in pkg_data["dependencies"]
          for p in ("express", "@prisma/client", "cors", "dotenv")))
check("package.json pins a detected known package",
      "helmet" in pkg_data["dependencies"])
check("package.json never invents a version",
      all(v != "" and v.startswith("^") for v in pkg_data["dependencies"].values()))
check("package.json ignores relative imports", "../lib/prisma.js" not in pkg)
check("package.json name is npm-safe",
      pkg_data["name"] == "parcel-tracking-api")

pkg2, warns2 = render_package_json("x", {"a.js": "import weird from 'not-a-real-pkg';"})
check("unknown package is warned about, not fabricated",
      "not-a-real-pkg" not in json.loads(pkg2)["dependencies"]
      and any("not-a-real-pkg" in w for w in warns2))

check("shared prisma client is a single instance",
      "new PrismaClient" in render_prisma_client()
      and "globalForPrisma" in render_prisma_client())

server = render_server("Parcel API", ["src/routes/parcels.js", "src/routes/scans.js"])
check("server mounts every delivered route",
      "parcelsRouter" in server and "scansRouter" in server
      and "/api/parcels" in server and "/api/scans" in server)
check("server imports routes with the .js extension",
      "'./routes/parcels.js'" in server)
check("server keeps the 4-arg error middleware",
      "(err, req, res, next)" in server)
empty_server = render_server("Parcel API", [])
check("server with no delivered routes still starts",
      "no routes to mount" in empty_server and "app.listen" in empty_server)

print(f"\n{PASS} passed, {FAIL} failed. (0 API calls)")
sys.exit(1 if FAIL else 0)
