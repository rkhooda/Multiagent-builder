# Stack Profiles — What They Are and How to Add One

A **Stack Profile** is everything the pipeline used to assume about React +
FastAPI, made explicit: which phases exist, which prompt each coder reads, how
files are classified, which structural dependency edges are implied, what
deterministic infrastructure is rendered, and what the devops stage produces.

Everything below is derived from the three profiles that actually exist. It
describes what they needed, not what a profile could hypothetically want — if a
field is not here, nothing has required it yet, and adding one speculatively is
how this abstraction stops fitting anything.

---

## Supported targets

| Profile | Builds | Phases |
|---|---|---|
| `react-fastapi` (default) | React 19 + Vite + Tailwind front end, FastAPI + SQLAlchemy + Pydantic back end, relational DB | database → backend → frontend → devops |
| `static-site` | Semantic HTML pages, one shared stylesheet, vanilla-JS behaviour. No build step, no server, no database | frontend → devops |
| `node-express-api` | Express 4 on Node 20 (ES modules) + Prisma over PostgreSQL. API only, no front end | database → backend → devops |

**Anything else is unsupported.** Mobile apps, other languages, other
frameworks, desktop apps, data pipelines: the system does not build them, and it
says so rather than producing a poor approximation. See *Mismatch handling*.

---

## How a profile is selected

1. **Explicit choice on the New Project form.** Pick a target directly. An
   explicit choice is never second-guessed later.
2. **`auto` (the default).** The requirements agent recommends a stack; a
   deterministic table (`resolve_profile` in `app/profiles/__init__.py`) maps
   that recommendation onto a profile. It is a lookup over three entries, not a
   model call — a model asked to pick would add a failure mode a table cannot
   have.
3. **Override at Gate 1.** The chosen target and any mismatch are shown at
   Gate 1, where the recommendation first exists and a change is still cheap.

### Mismatch handling

When `auto` finds no match, the system does **not** silently pick the nearest
profile. It records why, names every target it does support, states that
nothing has been generated for the recommendation, and surfaces that at Gate 1
where a human is already reading. The event also increments `degraded_events`,
so a run that proceeded on a defaulted profile is visible in the metrics rather
than only in a gate someone may have clicked past.

The reasoning: a reviewer who is told "your stack is not supported, here is what
is" can act. A reviewer handed a plausible-looking React app they never asked
for has to work out what happened first. Nearest-with-warning optimises for the
run finishing; naming it optimises for the person reading the output.

A profile change after Gate 1 routes through the existing
`invalidate_downstream(state, "requirements")` — the architecture names a folder
tree and an API surface, so everything from architecture onward is stack-shaped
and must be rebuilt. No new invalidation logic exists for this.

---

## Absent phases are correct

A `static-site` plan contains **zero** database and backend tasks. That is the
feature, not a gap. The graph node set is fixed and nodes no-op when their phase
filter yields nothing, so an absent phase costs nothing and breaks nothing.

The planner is told this explicitly, with examples, in section 4a of
`prompts/planning_agent.md` — which is generated from the active profile's
declarations, so the prompt can never describe a phase the validator would
reject.

---

## Adding a profile

A profile is one module in `backend/app/profiles/` plus its prompt files, and
one line in the registry. There is no plugin system to learn.

### 1. Declare it

```python
# backend/app/profiles/my_target.py
from . import PhaseSpec, StackProfile

PROFILE = StackProfile(
    name="my-target",
    label="Human-readable name",
    summary="One sentence: what kind of project this builds.",
    phases=(
        PhaseSpec(name="backend", id_prefix="be", label="API",
                  agent_type="backend_code",       # an llm_router MODELS key
                  prompt_file="my_target_coder_agent.md",
                  context_recipe="backend",        # 'backend' | 'frontend'
                  context_prefix="src",            # folder-map root
                  import_note=IMPORT_NOTE,         # {filepath} {resource}
                  structure_note=STRUCTURE_NOTE,   # {prefix}
                  plan_guidance="What belongs in this phase, for this stack."),
    ),
    file_kind=file_kind,                  # (filepath, description) -> kind
    implicit_deps={"backend": deps_fn},   # structural edges the planner omits
    ui_contract=None,                     # or a (stack, plan) -> str builder
    infra=generate_infra,                 # or None
    infra_basenames=frozenset({"..."}),   # excluded from the LLM loop
    devops_files=DEVOPS_FILES,
    review_supported=False,               # the reviewer prompt is React-specific
    plan_example=PLAN_EXAMPLE,
    min_tasks=4,
)
```

Register it in `app/profiles/__init__.py`'s `PROFILES` dict.

**Phase names must be a subset of `CANONICAL_PHASES`** (`database`, `backend`,
`frontend`, `devops`). Each maps 1:1 onto an existing LangGraph node; a new
phase name would need a new node, a new checkpoint identity, and gate rework.
Pick the canonical slot whose *role* matches — `static-site` uses `frontend`
for HTML/CSS/JS, `node-express-api` uses `database` for its Prisma schema.

### 2. Write the coder prompt — this is the quality, not the plumbing

Days 18–21 established that **a worked example beats a page of rules**. A
profile with elegant configuration and a lazy example will generate worse code
than the current stack does. Budget real effort here.

Each existing coder prompt carries **three worked examples** covering its
distinct file kinds, each annotated with what to study in it. Both new profiles
were tuned by generating, reading the output, and fixing the specific rule that
failed — see the defect log in `IMPROVEMENT_03_RESULTS.md`. Two of three defects
came from the *example*, not the rules: a model copies an example's literal
content as readily as its shape.

### 3. Classify files by their ROLE, not their folder

`file_kind` returns the vocabulary the context recipes understand:
`model`, `schema`, `router`, `service`, `config`, `main`, `other`.

The backend recipe injects **full file content** only for `model` and `schema`
kinds — those are "the same-resource files whose exact symbols this file must
import correctly". Map to the role, not the name. `node-express-api` classifies
`src/services/*.js` as `schema` for exactly this reason: measured, classified as
`service`, a generated router imported `{ parcels }` from a module exporting
`listItems`.

### 4. Render infrastructure deterministically

Anything that must be *exactly* right and is boilerplate — a dependency
manifest, a shared DB client, an entrypoint — is rendered from a template and a
curated version map, never generated. A hallucinated version breaks the install
for the whole project. See `app/utils/backend_infra.py` (pip) and
`app/utils/node_infra.py` (npm); a new one should mirror their structure and
their policy: **never invent a version — warn and omit**.

Entrypoints are rendered *after* the phase, from the files that actually
delivered, so a route that failed to generate is never mounted.

### 5. Measure its ceiling, do not inherit one

Generate a real project, read the worst completion size out of
`backend/metrics.db`, and add it to the table in
`test_token_budgets.py::test_ceilings_cover_measured_requirements`. A prompt
does **not** inherit a ceiling from a differently-shaped prompt, even when it
reuses the same routing key. A ceiling and its output requirement are one
decision.

### 6. Prove it

- `python3 backend/scripts/plan_shape_test.py --brief <your_brief>` — plan shape,
  planner only, cheap (planning runs on a non-scarce provider).
- `python3 backend/scripts/generate_profile.py <profile>` — a real generation.
- `python3 backend/scripts/score_project.py <project_id>` — score it, zero API cost.
- `python3 backend/tests/run_all.py` — must stay green, including the golden
  `--rescore`, which is the react-fastapi no-regression contract.

Add the profile's own assertions to `tests/test_profiles.py`. Every existing
profile is checked there for prompt-file existence, clean prompt injection, and
that its own worked plan example validates against its own shape rules.

---

## What deliberately does not vary per profile

Reused whole, never forked: `parallel_runner` (it already executes a real
dependency DAG), the validation registry and its syntax parsers (they dispatch
on file extension, which is language, not stack), the repair budget account,
`metrics_store`, the report pipeline, `context_builder`'s recipes, and
`invalidate_downstream`.

`import_fixer` is Python-only by construction and stays that way. JS imports are
flagged, never rewritten: the safe-fix analysis was specific to unambiguously
resolvable dotted modules, and JS specifiers are ambiguous across extensions and
index files.
