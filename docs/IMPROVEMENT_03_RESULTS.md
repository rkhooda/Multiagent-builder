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

---

## Phase 3 — Two new profiles, generated and scored

Both were **generated for real and scored**, not asserted. `scripts/
generate_profile.py` runs plan → coder phases for one profile and persists
state so `score_project.py` reads it offline.

### Plan shape under the new profiles (planner-only)

| Profile | Tasks | Phase counts | Assertion | Verdict |
|---|---|---|---|---|
| `static-site` | 5 | frontend 5 | 0 database, 0 backend | **PASS** |
| `node-express-api` | 5 | database 1, backend 4 | 0 frontend | **PASS** |

The Express plan produced **exactly one** database task — the single
`prisma/schema.prisma`, which is what its phase guidance asks for and what
Prisma actually wants. Shape guidance is being followed, not just phase
presence.

### Generation scores (`score_project.py`, zero API cost)

| Profile | Project | Files | USABLE (tier 4) |
|---|---|---|---|
| `static-site` | `gen-static-site-v3` | 5/5 generated, 0 failed, 0 blocked | **5/5 = 100%** |
| `node-express-api` | `gen-express-v2` | 8/8 generated (4 LLM + 3 infra + 1 schema), 0 failed | **8/8 = 100%** |

Both **KEEP**.

The tier ladder has no HTML parser, so tier 4 is weaker evidence for a static
site than for JS or Python. The static-site result was therefore additionally
checked against the rules its prompt actually sets, all of which hold on the
final run: **0 broken href/src references**, **0 empty attribute values**, one
`<h1>` and one `<main>` per page, `lang` and viewport present on every page, no
inline `<style>` or `style=` anywhere, no framework or CDN reference anywhere,
and 16 `var()` uses against exactly 5 unique hex colours — all 5 inside
`:root`.

The Express result was checked the same way: routers import the **exact named
exports** their service declares (`getParcels, getParcel, createParcel,
deleteParcel`), at the correct relative depth with `.js` extensions, over the
real `Parcel`/`Scan` domain, with 201/204/400/404 handled and pagination
bounded.

### Three defects found by generating, and what each cost

Neither profile worked first time. All three failures were found only because
the output was generated and read — not by any static check.

1. **Static site referenced images nobody generated.** The coder copied the
   few-shot example's literal filenames (`holloway-house.jpg`) at a wrong depth,
   producing 2 broken `<img>` references. A few-shot example teaches its
   *content* as readily as its shape. Fixed by naming the case in the prompt:
   when the folder map lists no images, write no `<img>` at all.
2. **The fix produced `src=""`.** Complying with "reference no images" the
   wrong way — an empty `src` makes browsers re-request the document. Fixed by
   forbidding empty attribute values explicitly. Broken references then went to
   **0** and stayed there.
3. **Express routers imported names their services do not export.** The router
   imported `{ parcels }` from a module exporting `listItems`. Root cause was
   structural, not a prompt failure: the backend context recipe injects FULL
   content only for files it classifies as `model` or `schema`, and the Express
   profile classified service modules as `service` — so the router saw a symbol
   summary instead of the real signatures. A service module in this stack plays
   exactly the role a Pydantic schema plays in react-fastapi: *the
   same-resource file whose exact exported names the router must import*. The
   profile now classifies it by that role, and the regenerated router imports
   the four real names. Pinned by a test.

Also fixed while there: `_resource_of` stripped only `.py`, so a JS router
matched its JS service only by the accident of both keeping their suffix. It
now strips any extension — no change to react-fastapi, correctness for the rest.

### One harness defect, recorded because it distorted a score

The first Express run scored **37.5%**, and most of that was the measurement,
not the code. Two causes, both in the harness rather than the profile: the stub
architecture handed every brief a generic `items` table, so the coders
faithfully built an `Item` model for a parcel-tracking brief; and the brief's
file list omitted the deterministic infra paths, so three correct
generated-by-template files scored as "unplanned". Both fixed in
`plan_shape_test.py`. Recorded because a scoring harness that flatters or
punishes by accident invalidates every number it produces, and the honest
sequence — 37.5% → diagnose → 100% — is the evidence that the second number
means something.

### Ceilings, measured not inherited

Measured from `backend/metrics.db` over the real runs, **zero truncation flags
on any call**, and pinned in `test_token_budgets.py`:

| Prompt | Routing key | Ceiling | Measured worst | Headroom |
|---|---|---|---|---|
| `static_site_coder_agent.md` | `frontend_code` | 1500 | 562 | 2.7× |
| `express_coder_agent.md` (backend) | `backend_code` | 1500 | 464 | 3.2× |
| `express_coder_agent.md` (prisma) | `database` | 2500 | 154 | 16× |

Each is measured on its own output rather than inheriting the ceiling its
routing key already carried. The finding is that none needed raising — which is
now pinned rather than assumed.

### Degradation paths

No new fail-open path was added. The one new degradation is **loud, not
silent**: an npm package imported by generated code but absent from the
known-good version map is recorded as a warning and **left out** of
package.json rather than pinned to an invented version. A fabricated version
fails `npm ci` for the whole project; a named missing dependency does not. This
is the same policy `render_requirements` already applies to pip.

---

## Regression status

| Gate | Phase 0 | After Phase 2 | After Phase 3 |
|---|---|---|---|
| Offline suites | 19/19 | 20/20 | **21/21** |
| Golden `--rescore` | 7/7 | 7/7 | **7/7** |

No react-fastapi regression at any checkpoint.

## Verdicts

| Target | Verdict | Evidence |
|---|---|---|
| `react-fastapi` | **KEEP** | Byte-identical inputs after extraction; golden 7/7 unchanged at every checkpoint |
| `static-site` | **KEEP** | Generated and scored 5/5 = 100% usable; 0 broken refs, 0 empty attrs, all prompt rules hold |
| `node-express-api` | **KEEP** | Generated and scored 8/8 = 100% usable; correct cross-file imports over the real domain |

Neither new profile is claimed beyond what was measured: each was generated
**once** at its final prompt revision, on one brief, with one model. That is
enough to say the seam works and the plumbing is right; it is not enough to
claim a quality level comparable to react-fastapi's, which has the Day 18–21
A/B evidence behind it. The honest statement is that both targets now *work*
and are *measured*, and that neither is *tuned*.
