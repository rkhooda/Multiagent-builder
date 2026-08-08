# Improvement 03 — Stack Profiles & Dynamic Plan Shapes: Measurements & Verdict

Goal: make the system able to scaffold projects of genuinely different shapes,
by extracting the implicit React+FastAPI assumptions into an explicit Stack
Profile and letting the planner emit plan shapes that vary by project type —
without regressing the tuned react-fastapi output.

Provider premise verified against [PROVIDERS.md](PROVIDERS.md) before designing
the experiment: **planning runs on gemini-2.5-flash** (1M tokens/day, never the
binding limit), the coders run on **Groq's 100k/day**, which is the scarce pool.
That asymmetry is why Phase 2 is measurable where Improvements 01 and 02 were
not — plan shape is a property of a JSON document produced by a non-scarce
model, so it can be asserted for free.

---

## Phase 0 — Baseline (2026-08-08)

| Gate | Result |
|---|---|
| `tests/run_all.py` (offline) | **19/19 green** |
| `ab_prompt_test.py --rescore .../golden` | **7/7 pass**, 0 API calls |
| Ceiling pins (`test_token_budgets.py`) | 10/10 including `test_ceilings_cover_measured_requirements` |

Coupling inventory: [STACK_COUPLING_AUDIT.md](STACK_COUPLING_AUDIT.md). The
profile surface was derived from that document, not from speculation.

---

## Phase 1 — Extraction proven behaviour-identical

The extraction claim is stronger than a sampled generation: the profile is
proven to feed the coders **byte-identical inputs**, so the output distribution
cannot have moved.

| Check | Result |
|---|---|
| `prompt_for(phase)` vs `prompts/*.md` on disk | byte-identical, all 4 phases |
| Profile-driven `build_file_context` vs pre-profile default call | byte-identical, both recipes |
| Implicit dependency edges (lib-first, model→router) | identical sets |
| `tests/run_all.py` | **20/20 green** (19 + new identity suite) |
| Golden `--rescore` | **7/7 pass**, unchanged |

A live re-generation of the frozen TodoSimple frontend phase was deliberately
**not** run. Same system prompt bytes + same context bytes + same model = the
same distribution; sampling it would spend the scarce Groq pool to re-measure
noise, which is the spend pattern this improvement exists to avoid. The
byte-identity assertions are the stronger evidence and they are free to re-run.

---

## Phase 2 — Plan shapes vary by project type

### Method

`backend/scripts/plan_shape_test.py`. Runs the **planner alone** over four
briefs of clearly different shapes and asserts properties of the returned JSON.
No code generation, no coder calls, no full-pipeline run — the Groq pool is
never touched. The architecture document is synthesised locally rather than
generated, both to avoid a Groq call and to remove it as a confound: the
variable under test is what the planner does with a given project shape.

Each brief's expected shape was written down **before** any plan was seen.

### Results — two independent replicates, 2026-08-08

| Brief | Shape assertion | Tasks | Phase counts | Verdict |
|---|---|---|---|---|
| `static_site` (one-page studio site) | 0 database, 0 backend | 6 | frontend 6 | **PASS** |
| `api_only` (parcel tracking REST API) | 0 frontend | 5 | database 2, backend 3 | **PASS** |
| `full_stack` (TodoSimple — control) | all phases real | 9 | database 2, backend 3, frontend 4 | **PASS** |
| `cli_tool` (log filter CLI) | 0 frontend, 0 database | 3 | backend 3 | **PASS** |

**4/4 on both replicates, with identical task counts and phase counts each
time.** The two questions the brief posed are answered directly: a static-site
brief yields **zero** database tasks, and an API-only brief yields **zero**
frontend tasks.

Task count tracks scope monotonically — 3 (CLI) < 5 (API) < 6 (static site) <
9 (full-stack) — rather than converging on a fixed size.

### The floor was fighting the correct answer (found by this experiment)

The first run was **2/4**, and both failures were the same defect. The planner
produced correctly-shaped plans — 6 tasks for the 6-file static site, 3 for the
3-file CLI tool — and the global `MIN_TASKS = 8` floor **rejected them**, forcing
a repair whose only way to comply was to pad. One of those repairs padded into a
4,495-token response that hit the token ceiling and returned no parseable JSON
at all, so a correct 6-task plan became a hard `LLMOutputError`.

The floor exists to catch a *truncated* plan, not to impose a size, and coverage
of `file_list` already checks the real invariant exactly. It now yields to the
file list whenever one is known:

    floor = min(profile.min_tasks, len(file_list)) if file_list else profile.min_tasks

react-fastapi behaviour is unchanged for any project with ≥ 8 planned files —
which is every real application, and every golden fixture. Re-run: **4/4**.

This is worth recording as a general shape: a guardrail calibrated on one
project shape reads as a correctness rule until a different shape arrives, and
then it *manufactures* the failure it was meant to prevent.

### Over-fragmentation (related lead — recorded, not claimed)

The fragmentation diagnostic recorded **86 tasks** for a simple todo app. The
`full_stack` TodoSimple brief here plans **9**. That is a 9.5× difference in the
right direction, but it is **not** a clean before/after: this run uses a
synthesised 9-file architecture rather than the original's generated one, and
the file list bounds the plan. Recorded as encouraging, claimed as nothing. A
real comparison needs the same architecture document through both prompts.

### Shape validation

Plan shape is enforced against the active profile in the **existing** validator
registry (`_plan_shape`, called from `_valid_plan`), so a bad shape gets the same
one-shot repair every other agent gets, and a surviving failure surfaces loudly
at Gate 3 where a human is already reading the plan. Four rules, each pinned by
a test: phase must be one the profile declares; task-id prefix must match its
phase; no dependency cycle; exactly one task owns each filepath.

Absent phases are deliberately **not** an error — that is the feature.

---

## Regression status

| Gate | Phase 0 | After Phase 2 |
|---|---|---|
| Offline suites | 19/19 | **20/20** |
| Golden `--rescore` | 7/7 | **7/7** |

No react-fastapi regression at any checkpoint.
