# Verification Gap Analysis

**Dated 2026-08-13.** Written *before* implementing, as the specification for
the integration-check layer. Subject: project `e8935f86-12f8-4a95-b330-89918f8fa4db`
("CRM system II"), generated 2026-08-12, status `completed`, handed over as a ZIP.

The as-generated artifact is `~/Desktop/CRM-system-II.zip`; the hand-repaired,
verified-working copy is `~/Desktop/CRM-system-II/`. The diff between them is
the specification this document turns into checks.

---

## The one-sentence diagnosis

**Every broken file parses.**

The existing validation layer asks one question — *does this file parse?* —
and every defect that shipped answers yes:

| File | Defect | `ast.parse` / babel |
|---|---|---|
| `backend/app/schemas/tag.py` | `datetime` used, never imported | **parses** |
| `backend/app/models/contact.py` | ends on a bare `notes` | **parses** (expression statement) |
| `backend/alembic/env.py` | truncated at line 13, mid-comment | **parses** (a comment is valid) |
| `frontend/.../ReminderForm.jsx` | truncated at line 7, mid-comment | **parses** (a comment is valid) |

Confirmed by execution: all three Python files above return clean from
`ast.parse`. A file cut off mid-comment is syntactically complete in both
languages, and a truncated file that happens to end at a statement boundary is
indistinguishable from a short file.

That is why per-file review found nothing and why the pipeline had no signal.
The missing layer is not a better parser — it is the two rungs above parsing:
**name resolution within a file** (a linter) and **agreement between files**
(integration checks). Neither was ever run.

---

## The two root causes

### Root cause 1 — `unverified` is counted, labelled, displayed, and has no consequence

The brief asked whether build verification is unwired from `degraded_events`,
or whether `unverified` simply is not distinguished from `pass` downstream.
**It is the second.** The wiring is present and correct:

- `build_verify_agent.py:112-115` records `build_verify_unavailable` for every
  target that came back unverified.
- The persisted state for the CRM run contains it:
  `"build_verify_unavailable": 2`.
- `Gate4Approval.jsx:584` renders it in `DegradedEventsBanner`.

So the abstention was detected, counted, persisted and shown — and the project
was still marked `completed`, still scored, still zipped, still handed over.
`degraded_events` is a **warning surface with no consequence**: nothing
downstream reads it to decide anything.

The exact path, for a targeted fix:

```
ladder.py:85-87    /workspace/start unreachable (DNS: the backend ran outside
                   docker, so http://sandbox:8100 did not resolve)
                   -> return {"target": ..., "unverified_reason": str(exc)}
                   ^ note: no tiers key at all
build_verify_agent.py:114   degraded.record(project_id, "build_verify_unavailable")
pipeline.py:344             drained into state["degraded_events"]
projects.py:1395            surfaced in the summary
Gate4Approval.jsx:584       rendered as a warning
                   -> and then nothing. status stays "completed".
```

Two secondary defects on the same path:

- **`ladder.py` has a second, quieter abstention.** A tier-level `UNVERIFIED`
  (`ladder.py:50-51`, `70-71`) is returned *inside* a `tiers` dict, so it never
  sets target-level `unverified_reason` — and `build_verify_agent.py:113`
  only tests `if "unverified_reason" in r`. A sandbox that accepts
  `/workspace/start` and then becomes unreachable produces an unverified tier
  that **is not counted at all**. The CRM did not hit this path; it is the same
  bug one level down, and it is unfixed.
- **The Gate 4 wording misrepresents the case it is describing.** The banner
  reads *"Build verification found a problem — the generated code did not fully
  install/build/boot."* Nothing was ever run. "We checked and it failed" and
  "we never checked" are rendered identically, and the one that shipped was the
  second. `DEGRADED_LABELS` also has no entry for `build_verify_unavailable`,
  so it renders as a raw key.

### Root cause 2 — truncation is detected per call, and nothing reacts

Truncation is **not** undetected. `llm_router.py:1122` reads
`finish_reason == "length"`, `:884` records `llm_truncated:{agent}`, and the
metrics store has a `truncated` column. The CRM run's persisted state:

```json
"llm_truncated:frontend_code": 5,
"llm_truncated:database": 3,
"llm_truncated:requirements": 1
```

Nine truncation events. `metrics.db` names the files:

| File | Agent | Completion | Ceiling | Flag |
|---|---|---|---|---|
| `backend/app/models/contact.py` | database | 2496 | 2500 | ✔ |
| `backend/alembic.ini` | database | 2496 | 2500 | ✔ |
| `backend/alembic/env.py` | database | 2496 | 2500 | ✔ |
| `frontend/package-lock.json` | frontend_code | 1500 | 1500 | ✔ (×2, two models) |
| `frontend/src/assets/crm-logo.svg` | frontend_code | 1496 | 1500 | ✔ |
| `frontend/src/components/common/Sidebar.jsx` | frontend_code | 1496 | 1500 | ✔ |
| `frontend/src/components/forms/ReminderForm.jsx` | frontend_code | 1496 | 1500 | ✔ |

The flagged set is **exactly** the set of files the human found truncated or
degenerate. The system identified every one of them, by name, at generation
time, and shipped all of them.

Ceilings, measured against requirement:

- `DATABASE_MAX_TOKENS = 2500` (`database_agent.py:11`) — 3 of 12 files hit it.
- `FRONTEND_FILE_MAX_TOKENS = 1500` (`frontend_coder_agent.py:32`) — 5 hit it.
- `BACKEND_FILE_MAX_TOKENS = 1500` (`backend_coder_agent.py:30`) — max observed
  943, no truncation. Adequate for this run; no headroom evidence beyond it.

`fast_mode` was **False** for this run, so this is the *normal* profile
starving, not the fast one. `docs/CEILING_AUDIT.md` (2026-08-03) already
predicted this shape for the fallback tier and explicitly deferred it
("NOT fixed today"); the CRM is that deferral coming due.

One defect here is not a ceiling problem at all: **`package-lock.json` should
never be model-generated.** It is a derived artifact of `npm install`. No
ceiling makes an LLM-authored lockfile correct. It belongs to the install rung,
not the coder.

### The report that said everything was fine

The ZIP shipped with `docs/validation-report.md`. It is worth reading as the
system's own account of this project:

```json
"files_checked": 94,
"syntax_errors_found": 0,        <- 94 files, zero syntax errors. True, and useless.
"phantom_imports": 1,
"missing_packages": 1,           <- intl-datetimeformat, named correctly
"generation_failed": 2,          <- two files never generated at all
"repair_calls_spent": 6,
"failure_rate": 0.0426,
"quality_threshold": 0.2,
"below_threshold": false         <- verdict: this project is fine
```

Three things follow from it.

1. **`syntax_errors_found: 0` is accurate.** The parse-only layer had nothing to
   report, because every broken file parses. The metric was measuring the wrong
   property, and it measured it correctly.
2. **The failure rate is computed over the wrong denominator.** Four unresolved
   files out of 94 is 4.26%, comfortably under the 20% threshold — while the
   project could not install, import, build or boot. A ratio of *files with
   findings* to *files* cannot express "this artifact does not run", because
   that is not a property of any file.
3. **`generation_failed: 2` did not stop anything either.** Two planned files
   were never produced and the run completed.

The three files listed as `repaired` include `package-lock.json` and
`Sidebar.jsx` — both truncated at their ceiling. "Repaired" here means *now
parses*, not *now correct*: both are still defective in the shipped artifact.
This is the concrete argument for regenerating truncated files rather than
repairing them.

---

## Defect-class table — the Phase 1 specification

`Would a linter catch it?` is answered against `ruff`/`pyflakes` for Python and
`eslint` for JS. **Neither is run anywhere in this pipeline today.**

### A. Intra-file defects a linter catches for free

| # | Defect | Should have been caught by | Why it was not |
|---|---|---|---|
| A1 | `schemas/tag.py` uses `datetime`, never imports it → `/openapi.json` 500 | `ruff`/`pyflakes` F821 undefined name | **No linter is run on generated projects at all.** `validation/syntax.py` runs `ast.parse`, which asks only whether the file parses. An undefined name parses. |

**Check to build:** run the ecosystem linter per project (`ruff check`,
`eslint`), report `F821`/`no-undef` class findings as errors. Cheapest layer in
the whole document and entirely absent.

### B. Truncation and degeneracy (ceiling starvation)

| # | Defect | Should have been caught by | Why it was not |
|---|---|---|---|
| B1 | `models/contact.py` ends on bare `notes` | Truncation check on the recorded `finish_reason` flag | The flag fired and was recorded. **Nothing consumes it.** `ast.parse` passes the file. |
| B2 | `alembic/env.py` truncated at 13 lines mid-comment | same | same; a trailing comment is valid Python |
| B3 | `ReminderForm.jsx` 7 lines, cut mid-comment | same | same; a trailing comment is valid JS |
| B4 | `alembic.ini` — same comment block ×13 over 207 lines, then truncated, no `[loggers]` → `alembic upgrade` dies on `KeyError` | Degeneracy (repetition-loop) detection | No such check exists. `.ini` is not parsed by `validate_artifact` (which covers JSON/YAML), so it was never examined at all. |

**Checks to build:** (1) truncation — read the flag already in state/metrics,
report per file, **regenerate rather than repair**; (2) degeneracy — normalised
repeated-block detection, reported as a generation failure, distinct from a
syntax error. Both remedies are upstream (ceiling), not local.

### C. Cross-file integration

No single file is wrong; no two agree.

| # | Defect | Should have been caught by | Why it was not |
|---|---|---|---|
| C1 | `main.py` imports `app.config`; module is at `app/core/config.py` | Whole-tree Python import resolution | `validate_js_imports` resolves **JS only**. There is no Python equivalent. `import_fixer.py` parses Python imports but only safe-fixes and flags phantoms within its own narrow scope, and does not run over the whole tree as a gate. |
| C2 | `Settings` defines `database_url`; call sites read `settings.DATABASE_URL` | Config-key agreement | No check. Requires attribute-level resolution against the settings class. |
| C3 | `Contact.tags` back-populates `Tag.contacts`, which does not exist → every DB request 500s | ORM relationship symmetry | No check. Pure cross-file agreement; invisible per file. |
| C4 | Routers registered twice (directly in `main.py` **and** via `api.py`) at doubled prefixes → real path `/api/v1/auth/auth/register` | Route registration uniqueness | No check. Confirmed: `main.py:59-68` includes 9 endpoint routers **and** `app_api_v1_api_router`, which includes the same 9 again. |
| C5 | `/contacts/search` declared after `/{contact_id}` → parsed as an id → 422 | Route ordering (static shadowed by dynamic) | No check. |
| C6 | `crud.get(db, id=...)` vs `def get(db, contact_id)`; `CRUDTag(Tag)` where the class defines no `__init__` | Call-signature agreement | No check. Needs a symbol table with signatures. |
| C7 | No `__init__.py` anywhere → namespace packages → `import app.models` registered nothing → `create_all` created no tables | Packaging-structure check | A `test_package_inits.py` suite exists for the **builder's own** repo. Nothing enforces package markers in **generated** projects. The repaired copy adds 9 `__init__.py` files. |
| C8 | Two competing DB layers shipped side by side (sync `database.py` + async `db/session.py`); `get_current_user` injects an `AsyncSession` into sync CRUD | Not directly checkable statically; surfaces at journey smoke | Boot succeeds; the first real request fails. Needs the runtime rung. |
| C9 | `db/base_class.py` — dead module opening a Postgres engine at import time | Import-time I/O check | No check. |
| C10 | `services/export_service.py` exports `first_name`/`email`/`company`/`body` — none are columns — and `await`s synchronous functions | ORM attribute resolution + async/sync agreement | No check. |

### D. Frontend ↔ backend contract — the largest class

| # | Defect | Should have been caught by | Why it was not |
|---|---|---|---|
| D1 | Frontend calls `/api/v1/dashboard`; backend serves `/dashboard/summary` | API contract: unknown path | **No check of any kind exists between the two halves of the project.** The `ui_contract` state field is a *design* document, not a verified contract. |
| D2 | Frontend calls `/contacts/{id}/notes`; backend serves `/notes/{contact_id}` | same | same |
| D3 | Login posts JSON; endpoint expects an OAuth2 form with `username` → 422 | API contract: request content-type/shape | same |
| D4 | Reads `res.data.activities` (actual `recent_activities`), `reminder.due_date` (actual `remind_at`), `note.text` (actual `content`) | API contract: response fields absent from schema | same |
| D5 | `/contacts/{id}/chat-history` called; no route, no model, no table — never built | API contract: unknown path | same |
| D6 | 6+ missing-export import errors | JS import resolution | `validate_js_imports` exists and resolves JS imports. Needs checking whether it verifies **named exports** or only module existence — the repaired copy adds `AppLayout.jsx` and `useAuth.jsx`, so at least some were unresolved-module, not just unresolved-symbol. |
| D7 | `AuthProvider` never wrapped the app; `index.css` never imported so Tailwind never loaded | Entry-point wiring (per profile) | No check. Confirmed by the diff: generated `frontend/index.css` sits at the wrong level and the repaired copy moves it to `src/`. |
| D8 | `main.jsx` uses `ReactDOM.render()` (removed in React 18) while `package.json` pins React 19 → blank page | Runtime/version matrix: framework API vs pinned major | No check. |

### E. Dependency and runtime correctness

| # | Defect | Should have been caught by | Why it was not |
|---|---|---|---|
| E1 | `requirements.txt` lists `csv` — stdlib, not a PyPI package → `pip install -r` fails outright | Manifest sanity: stdlib entry | No check. The generator **knew** it was unrecognised — it emitted `csv  # unpinned: not in known-good map, verify version` — and shipped it as a dependency rather than recognising a stdlib module. |
| E2 | `email-validator`, `python-multipart` imported but absent from the manifest | Manifest sanity: unlisted third-party import | No check for Python. |
| E3 | `intl-datetimeformat` imported, absent from `package.json` | same, JS | **It WAS caught.** The shipped `docs/validation-report.md` contains it verbatim: `"imports package 'intl-datetimeformat' which is not in package.json dependencies"`, `kind: missing_package`. `missing_package` is not in `REPAIRABLE_KINDS` and does not move the quality threshold, so it was flagged and shipped. See "The report that said everything was fine" below. |
| E4 | `package-lock.json` a 7-line stub → `npm ci` installs nothing | Lockfile non-emptiness/consistency | No check. Root fix: **do not generate lockfiles** — derive from `npm install`. |
| E5 | `DATABASE_URL` uses `postgresql+asyncpg://`; `asyncpg` never a dependency | Manifest sanity + env/config agreement | No check. |
| E6 | Pinned `pydantic` had no wheels for the host Python; nothing pinned the dev runtime | Runtime/version matrix | No check. |

### F. Config files referencing things that do not exist

| # | Defect | Should have been caught by | Why it was not |
|---|---|---|---|
| F1 | `docker-compose.yml` builds `./backend/Dockerfile`; the file is at repo root | Config cross-reference: named path exists | `validate_artifact` parses compose YAML for **syntax only**. It never resolves the paths inside it. Confirmed by the diff: generated has root `Dockerfile`, repaired has `backend/Dockerfile`. |
| F2 | `Dockerfile` CMD runs `uvicorn main:app`; app is `app.main:app` | Config cross-reference: entrypoint module resolves | No check. |
| F3 | `nginx.conf` is a full config (`user`/`events`/`http`) copied to `conf.d/default.conf`, nesting those inside `http` → nginx refuses to start | Config destination correctness for the image | No check. |
| F4 | CI runs `npm run lint` (no such script), `pytest` (zero test files), `docker-compose build` (no Dockerfile) → fails at step one | CI script existence; test step implies tests exist | No check. |
| F5 | Three `.env.example` files disagree on names; compose sets `VITE_API_URL` at runtime, but Vite inlines at build time | Env var consistency; build-time vs runtime placement | No check. |
| F6 | No `.gitignore` → `.env` and `app.db` committable | Required-file check | No check. The repaired copy adds one. |

### G. Migration correctness

| # | Defect | Should have been caught by | Why it was not |
|---|---|---|---|
| G1 | Migration diverges from ORM (`whatsapp_id` NOT NULL in migration, nullable in model) | Migration ↔ model parity (autogenerate diff empty) | No check. Requires a live metadata comparison — the migrate rung. |
| G2 | Postgres-only `JSONB` in a project defaulting to SQLite → uncompilable on the default target | Default-dialect portability | No check. |
| G3 | `whatsapp_messages` table created with no corresponding model | Table-without-model check | No check. |

### H. Security

| # | Defect | Should have been caught by | Why it was not |
|---|---|---|---|
| H1 | `crud_user.py:18` assigns the raw password into `hashed_password`; `get_password_hash()` exists and is never called. Login would also always fail. | Narrow security smoke: defined hashing helper never called on the path that needs it | No check — **and QA found it.** It is in the QA report and the project shipped. A finding of any severity cannot currently stop a handoff. |

### I. The failure that let all of the above ship

| # | Defect | Should have been caught by | Why it was not |
|---|---|---|---|
| I1 | `docs/build-verification.md` records both targets as `"unverified_reason": "<urlopen error...>"`; the pipeline handed over a ZIP as if fine | The handoff gate | See **Root cause 1**. Detected, counted, displayed — and consequence-free. Third instance of this class (700-token reviewer; 3,000-token QA ceiling; now this). |

---

## What each class needs, and when it can run

The classes split cleanly by whether they need a running application. This
split, not the defect taxonomy, is what determines the implementation phases.

**Static — free, no network, Phase 1** (A, B, C1–C7, C9–C10, D6–D8, E1–E6,
F1–F6, G2–G3): every one is answerable from the file tree with a linter, an AST
symbol table, and path resolution.

**Runtime — needs egress and a booted app, Phase 2** (C8, D1–D5, G1): the
frontend↔backend contract and migration parity are the two classes that cannot
be answered honestly by static analysis.

That second point is the load-bearing design decision, so it is stated
explicitly: **the API contract is verified against the real `/openapi.json`,
not a static model of FastAPI's routing semantics.** Statically emulating
decorator prefixes, `include_router` nesting and Pydantic response models is
precisely the static-analysis platform this work is meant not to build — and it
would be an approximation of something the boot rung produces exactly and for
free. The static route table is still built (C4/C5 need it regardless), and
when boot succeeds it is cross-checked against the real OpenAPI, which
validates the table as a side effect. One mechanism, three checks.

---

## Ceiling actions (Phase 0 outcome)

1. `DATABASE_MAX_TOKENS` 2500 → measured requirement. Truncated at 2496 on
   `models/contact.py`, `alembic.ini`, `alembic/env.py`.
2. `FRONTEND_FILE_MAX_TOKENS` 1500 → measured requirement. Truncated at 1496 on
   three source files.
3. `package-lock.json` removed from the generated file set — a lockfile is an
   install artifact, not a coder output. No ceiling fixes an authored lockfile.
4. Both pinned by the existing parameterised test in `test_token_budgets.py`,
   in **every** profile including fast mode.

Truncated files are **regenerated after the ceiling fix, never patched** —
paying repair tokens to complete a file the ceiling cut is treating a symptom.

---

## Design decisions (ponytail #1 — the integration-check surface)

**Where the checks live.** `validation_pass` is already the whole-tree batch
node: it runs after the last coder, before QA, already does cross-file JS import
resolution, already aggregates into `validation_report`, already charges a
repair budget, and already renders at Gate 4 with `file:line`. Every check in
this document is a new `SyntaxIssue.kind` in that node. No new registry, no new
report, no new budget, no new panel, no new endpoint.

**Per-language or per-profile — both, split by concern.** The *mechanics* are
per-language (a Python AST walker, a JS one). The *rules* are per-profile:
`back_populates` symmetry means nothing without SQLAlchemy, provider-mounting
means nothing without React, migration parity means nothing without alembic.
So: detectors are language modules, and **profiles declare which detectors
apply plus their parameters** (entry-point path, settings module, router root)
— the same declarative shape `verify_targets` already has. Integration rules
belong in stack profiles beside build recipes, as data, not as code.

**Blocking or advisory.** Neither, and the question dissolves once the two
categories are separated. A style warning is an *opinion about code that works*.
A truncated file, a stdlib entry in `requirements.txt`, an unresolvable import
and a plaintext password are *deterministic, falsifiable claims that the
artifact does not work* — the first is a matter of taste, the second is a matter
of fact. But the consequence of a fact is not to remove the human's choice.
These findings change **what the artifact is called**, not **what the human is
allowed to do**: download stays available, and the words "completed" and "ready"
stop being applied. Respecting autonomy means letting someone knowingly download
a broken artifact; it never meant handing it over labelled as working. So
warn-never-block survives intact, and nothing needs to block.

### What I chose NOT to build, and why

1. **No new validation registry.** `app/validation/` + `validation_pass` covers it.
2. **No new report or UI panel.** New `kind` values in `validation_report`;
   Gate 4 already renders unknown kinds rather than hiding them.
3. **No new repair ledger or ceiling.** `vreport.may_repair` / `retry_counts`.
4. **No curated stdlib lists.** `sys.stdlib_module_names` (Python 3.10+) and
   `require('module').builtinModules` are exact and free. A hand-maintained list
   would be wrong the day a runtime version changes.
5. **No content-heuristic truncation detector as the primary signal.** The
   provider already reports `finish_reason == "length"`, and it is already
   recorded per call. A heuristic is at best a fallback for files whose flag was
   lost; building it first would be re-deriving something we are told exactly.
6. **No static emulator of FastAPI routing/response semantics.** The boot rung's
   real `/openapi.json` is exact and free. Static route extraction is built only
   because duplicate-registration and ordering checks need it anyway.
7. **No type inference.** Name-level resolution only. `x.foo` is checked when
   `x` is resolvably a known class instance; otherwise it is not reported. A
   false positive buys a paid repair of correct code — the expensive direction
   to be wrong in.
8. **No pytest.** This project has no test framework; suites are standalone
   `test_*.py` with `_run_all()` and an exit code.
9. **No generated lockfiles.** Deleting `package-lock.json` from the coder's
   file set is a smaller change than any check that would validate one.

## The rule this burns in

Any path where the system continues in a reduced mode must be **counted,
surfaced, and unable to present as success.** The first two were already built
and worked exactly as designed. Only the third was missing, and it is the only
one that would have stopped this ZIP.
