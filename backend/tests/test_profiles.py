#!/usr/bin/env python3
"""Stack Profile extraction — behaviour-identity pins (Improvement 03, Phase 1).

The extraction's whole contract is "no behaviour change": the react-fastapi
profile must describe exactly what the hard-coded pipeline did. These checks
pin the equivalences that make that claim mechanical rather than hopeful:
prompt bytes, context bytes, implicit-edge sets, infra/devops declarations.
Offline, zero API calls.
"""
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

print(f"\n{PASS} passed, {FAIL} failed. (0 API calls)")
sys.exit(1 if FAIL else 0)
