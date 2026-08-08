# Stack Coupling Audit — Where React+FastAPI Is Hard-Coded

**Dated 2026-08-08. Phase 0 input for Improvement 03 (Stack Profiles).**
Every location below was read in full, not grepped-and-guessed. This is the
design input for the profile surface: a field earns a place in the profile only
if a row here forces it.

Pre-flight state at time of audit: offline gate **19/19 suites green**, golden
`--rescore` **7/7 pass** (0 API calls). Baseline recorded in §Baseline.

---

## 1. Coder prompts — `prompts/frontend_coder_agent.md`, `prompts/backend_coder_agent.md`

The single biggest quality lever (Day 18–21 tuning). Both are 100% stack-specific:

- **frontend_coder_agent.md** (85 lines): "senior React developer … Vite + React
  + TailwindCSS + axios". Hard rules: Tailwind-only styling, axios only via the
  shared client, `import.meta.env.VITE_API_URL`, functional components + hooks,
  relative-import computation, allowed package list `react, react-dom,
  react-router-dom, axios`, "no `.css` files exist". One worked example: a
  React component with loading/error/empty states.
- **backend_coder_agent.md** (115 lines): "senior Python/FastAPI developer …
  FastAPI + SQLAlchemy + Pydantic v2". Hard rules: `app.` package-root imports,
  SQLAlchemy 2.x style, Pydantic v2 (`ConfigDict(from_attributes=True)`, no
  `class Config`), async routes, `Depends(get_db)`, pagination convention.
  Two worked examples: a CRUD router and a schema file.

**Coupling kind:** entire file. **Profile consequence:** the system prompt is a
per-profile, per-coder-role artifact (moved intact, not templated to fragments —
the tuned content must not be re-flowed).

## 2. `prompts/planning_agent.md` — phase vocabulary and task schema

- §4 fixes the phase set: `database | backend | frontend | devops`, and the ID
  prefixes `db_/be_/fe_/dv_` (three digits).
- §4 fixes the phase ordering: db → be → fe → dv, described as universal.
- §4b (decomposition) is React-specific: `src/pages/`, `App.jsx`,
  `frontend/src/components/<Page>/<Section>.jsx`.
- §5's output template shows `backend/app/models.py` and a FastAPI auth router.
- The runtime user message (planning_agent.py:131-137) restates the fixed
  ordering and the `db_001… be_001… fe_001… dv_001` sequence as CRITICAL RULES.

**Coupling kind:** vocabulary + ordering + examples. **Profile consequence:**
the phase list, per-phase ID prefixes, ordering constraints, and the plan
example must come from the active profile.

## 3. `app/agents/context_builder.py` — file kinds and structural knowledge

- `backend_file_kind()` (line 114): classifies into
  `config/database/main/model/schema/router/service/other` from FastAPI-shaped
  path conventions (`/models/`, `/schemas/`, `/routers/`, `_router.py`, …).
- `_build_backend_context`: section selection keyed on those kinds (SQL schema
  for model/schema, API rows for router/service); full-body injection of the
  same-resource model/schema ("cross-file truth"); closing instruction hard-codes
  "Use absolute `app.` imports (e.g. from app.models.{resource} import ...)".
- `_build_frontend_context`: `phase_prefix="frontend/src"`; consumer detection
  `_is_consumer` = `/pages/` or `App.jsx`; sibling signatures scan
  `/components/`, `/hooks/`; closing instruction says "Import the shared client".
- `_tech_stack_block()` defaults to "React 19 + Vite + TailwindCSS + axios".
- UI contract (`build_ui_contract`, `_CONTRACT_TOKENS`, `_CONTRACT_CONVENTIONS`):
  Tailwind class tokens and React prop conventions, injected into every frontend
  context.
- `build_file_context` dispatches on exactly two phases: `"backend"` else
  frontend.

**Coupling kind:** dispatch, kind-detection regexes, per-kind context recipes,
default strings, UI contract content. **Profile consequence:** the profile must
own (a) file-kind classification rules, (b) which context recipe a phase uses,
(c) the phase prefix for folder maps, (d) the closing import-convention line,
(e) the UI-contract text (or none).

## 4. `app/validation/` — language- and stack-shaped checks

- `syntax.py`: Python via ast/compile (`validate_content`), JS/JSX via batched
  @babel/parser node subprocess (`validate_js_batch`), JSON/YAML artifacts.
  These are *language* dispatch by extension — already mostly profile-agnostic.
- `validate_js_imports`: allowed-package check against
  `frontend/package.json` or `package.json` (path probed in validation_pass.py:241).
- `__init__.py` VALIDATORS registry keys on agent type: `frontend_code`/
  `backend_code` → `min_code_lines(5)`; `planning` → `_valid_plan` which
  enforces MIN_TASKS=8, file-list coverage, decomposition integrity (React
  pages), and duplicate-filepath.
- `_architecture_specificity`: rejects `.py` under `frontend/` and `__init__.js`
  — correct for this stack, wrong for a Python-less or Node project? (No — those
  rules are conditional on `frontend/` trees existing; harmless but audit-noted.)
- `app/utils/import_fixer.py`: Python-only AST rewrite, rooted at the `app`
  package convention. Non-`.py` returns untouched (safe pass-through already).
- `app/utils/backend_infra.py`: `KNOWN_GOOD_VERSIONS` is pip-only;
  `render_requirements/config/database/main` are FastAPI templates;
  `_ALWAYS` pins fastapi/uvicorn/sqlalchemy/pydantic on every project.
- `validation_pass._agent_for()`: repair-model routing by extension
  (`.py` → backend_code, yaml → devops, else frontend_code).

**Coupling kind:** mostly language-keyed (fine); the FastAPI infra renderer and
the pip version map are profile artifacts. **Profile consequence:** profiles
declare their dependency manifest convention (requirements.txt vs package.json,
with a known-good version map) and their deterministic infra files. The syntax
parsers stay global — they dispatch on extension, not stack.

## 5. `prompts/devops_agent.md` + `app/agents/devops_agent.py`

- `DEVOPS_FILES` (devops_agent.py:15): fixed 7-file set — backend Dockerfile
  (Python), frontend Dockerfile (node build → nginx), docker-compose with
  backend+frontend+database services, nginx.conf SPA proxy, CI, .env.example,
  README. Generated "regardless of the task plan".
- The prompt's §4 hard-codes per-file specs: Python 3.11-slim, node:20-alpine,
  postgres:15-alpine, `/api/` + `/ws/` proxying, `try_files` SPA fallback.

**Coupling kind:** the file set and the per-file specs. **Profile consequence:**
the devops file list + per-file descriptions are profile data; the prompt's
role/output rules are generic.

## 6. LangGraph node set — `app/graph/pipeline.py`

- Fixed code-phase nodes wired in a fixed order:
  `frontend_code → database → backend_code → validation → qa → devops`.
- Each coder already **no-ops on an empty phase**: `frontend_coder_agent`
  returns early when `get_tasks_for_phase(...)` is empty (line 122-129), same
  for backend (line 157) and database (line 47). The dynamic-shape machinery is
  therefore ~already present; what is fixed is only which phase names exist.
- `STAGE_ORDER`/`STAGE_ARTIFACTS` treat all coding as one "code" stage —
  invalidation is already phase-agnostic.
- `checkpointer`, `interrupt_before` gates, GATE_ROUTES: none reference coder
  phases except `human_gate_3.approve → frontend_code` (the fixed entry node of
  the code phase).

**Coupling kind:** node names = phase names; entry point after Gate 3.
**Profile consequence:** keep the node set fixed (nodes no-op on absent
phases) — confirmed viable by the early-return paths that already exist.

## 7. `app/models/tech_stack.py` — TechStackSchema

- Fields `frontend/backend/database/auth/hosting/key_libraries` assume every
  project has all of them; `DEFAULT_TECH_STACK` is React+FastAPI+Postgres.
- Produced by the requirements agent (`prompts/requirements_agent.md` §112-113
  shows React/FastAPI examples); consumed by planning, coders (via
  `_tech_stack_block`), devops, database agents as display strings.
- No notion of "can the system build this" — the mismatch problem named in the
  brief.

**Coupling kind:** schema shape + defaults + no profile linkage.
**Profile consequence:** a deterministic TechStack→profile mapping plus a
mismatch rule (Phase 4); the schema itself can stay (its fields are strings,
"none" is expressible).

## 8. `app/models/task_schema.py` — plan schema

- `VALID_PHASES = Literal["database", "backend", "frontend", "devops"]`.
- ID regex `^(db|be|fe|dv)_\d{3}$`.
- `summary()` counts exactly those four phases.

**Coupling kind:** closed enums. **Profile consequence:** phase names and ID
prefixes validate against the *active profile's* declared phases instead of a
global Literal.

## 9. Other locations found during the audit (beyond the brief's seven)

- **`app/agents/database_agent.py`**: prompt is SQLAlchemy-specific
  (`prompts/database_agent.md`), section extraction hard-codes
  "## Database Schema"/"## API Endpoints". A profile with no database phase
  never reaches it (empty-phase no-op) — low-risk.
- **`app/agents/backend_coder_agent.py`**: `INFRA_BASENAMES`
  (config/database/main/requirements deterministic set),
  `backend_implicit_deps` (model/schema→router edges), `_is_lib_file` in the
  frontend coder (lib-first edge). These implicit-dependency shapes are
  per-profile structural ordering.
- **`prompts/architecture_agent.md`**: requires a React component hierarchy
  with props, an erDiagram, API endpoint table — the *document shape* assumes
  full-stack. `_architecture_quality` enforces mermaid + folder tree;
  `_architecture_specificity` enforces API-response cells + props.
  A static site has no API table; these validators must become conditional on
  the profile's declared document requirements.
- **Gate 3 UI** (`frontend/src/components/gates/Gate3Approval.jsx`):
  `PHASES = ['frontend','backend','database','devops']` with per-phase
  color/prefix config and a fixed ordering array (line 70); unknown phases fall
  back to `PHASE_CONFIG.backend` (line 495) — renders, but mislabeled.
  Gate 1 (`Gate1Approval.jsx:30`) renders the three stack rows unconditionally.
- **`app/utils/summary_pdf.py`** and Gate-4/QA panels: phase counts surfaced;
  read from the plan, tolerate any phase names (verified: they iterate the
  plan, no closed enum).
- **`app/llm_router.py` MODELS**: agent types `frontend_code/backend_code/
  database/devops` are routing keys, *not* stack semantics — a profile's
  phases map onto these existing agent types (code-shaped vs prose-shaped
  chains). No change needed if new phases reuse existing agent-type keys.
- **`validation/report.py` + score_project.py**: tier ladder delegates to the
  language parsers — already language-dispatch, no stack assumption beyond
  what §4 covers.

---

## Baseline (no-regression contract, recorded 2026-08-08)

- `python tests/run_all.py` (offline): **19/19 suites green**.
- `ab_prompt_test.py --rescore tests/fixtures/prompt_tuning/golden`:
  **7/7 golden outputs pass** (B_fe_formatdate_1/2, B_fe_notecard_0/2,
  B_fe_notespage_unwired_0/1/2), 0 API calls.
- Ceiling pins: `test_token_budgets.py` 10/10 (includes
  `test_ceilings_cover_measured_requirements`).
- Crafted-breakage / scorer suites: `test_score_project.py` 18/18,
  `test_validation.py` 20/20, `test_validation_pass.py` 14/14.

Anything that moves these later is a regression, not a tradeoff.

---

## What the profile surface must therefore contain (minimum, from evidence above)

Derived strictly from the rows: (1) phase list with ID prefixes, ordering
edges, and per-phase coder agent-type + context recipe; (2) per-coder system
prompt file; (3) file-kind classification rules + per-kind context sections +
structural dependency shape; (4) folder-map phase prefixes + import-convention
closing line; (5) UI contract text (optional); (6) dependency manifest
convention (filename + known-good version map + always-pinned set) and
deterministic infra renderers; (7) devops file set + per-file specs;
(8) architecture-document requirements (which validators apply); (9) plan
example for the planner prompt. Nothing else until a third profile demands it.
