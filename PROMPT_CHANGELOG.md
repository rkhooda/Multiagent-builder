# Prompt Changelog

Every change to a file in `prompts/` is recorded here. Prompts are the agents'
actual behaviour spec — a git diff shows *what* a line became but never *why* it
exists, so next week's "improvement" can silently undo this week's measured fix.

## Protocol

Follow it in order. The discipline is the deliverable as much as the fixes are.

1. **Attribute before editing.** Establish which layer actually caused the defect
   (`docs/QUALITY_BASELINE.md` §1). A prompt edit for a context-layer defect
   bloats every call and fixes nothing. Re-run `build_file_context` — it is pure —
   and check whether the truth was in the context.
2. **Write the hypothesis here first**, before touching the prompt:
   *defect X occurs because Y; changing Z should reduce it; measured by criterion
   C on fixtures F.*
3. **One change at a time.** Two simultaneous edits make the result unattributable.
4. **A/B it**: `backend/scripts/ab_prompt_test.py`, N=3 samples per variant per
   fixture, frozen fixtures.
5. **Decision rule**: keep only if the new variant is ≥ the old on *every*
   criterion **and** strictly better on the targeted one. A tie on the target is a
   **revert** — the change cost tokens and bought nothing measurable.
6. **Record the verdict either way.** A revert is a result, not a failure; it is
   the institutional knowledge this file exists to hold.
7. **Commit the change (or the revert + finding) on its own.**

Regression protection: winning variants' sampled outputs are committed as golden
files under `backend/tests/fixtures/prompt_tuning/golden/` and re-scored offline
with `--rescore` at zero API cost. Run it after any prompt edit.

### Measurement notes

- The harness calls `groq/llama-3.3-70b-versatile` **directly**, not through
  `call_llm`. That is the model which produced the entire Day 18–20 evidence base
  (`qwen3-coder:free` has returned 429 on every attempt for three sessions), so
  the A/B measures the baseline's own model while spending zero OpenRouter quota.
- Pass rate for a criterion = passing samples / (fixtures × samples).
- `guards_api_data` is an acknowledged **heuristic**: it detects wholesale
  omission of `?.`/`??`, not every unguarded access. Treat movement on it as a
  weak signal.

---

## 2026-07-19 (Day 21)

Budget plan set before the first call: A/B runs cost **0 OpenRouter calls** (groq
direct); the only OpenRouter spend today is Task 5's single architecture
regeneration. Guard warns at 25, stops at 30.

### Entry 1 — frontend coder: import-resolution rule

- **Prompt file**: `prompts/frontend_coder_agent.md`
- **Defect**: D5 — imports of things that do not exist. `NoteCard.jsx:6`
  `import './NoteCard.css'`; `formatDate.js:3-4` `from 'intl'` /
  `'intl-datetimeformat'`. Severity **breaks-startup** (Vite fails to resolve).
- **Attribution**: **PROMPT**. The folder map was in context and untrimmed, and
  the no-CSS rule already exists at line 8 — the model saw both and violated
  them. The `intl` case is a prompt *gap*: no rule covers importing a package
  that is not a dependency.
- **Hypothesis**: the existing rule fails because it is stated **positively and
  in passing** ("use Tailwind for ALL styling"), buried in a list of nine other
  hard rules, and the few-shot example demonstrates only correct behaviour — the
  model never sees the forbidden form written down. Stating the wrong form
  explicitly beside the right one, as a dedicated block, should reduce it.
  Adding a matching rule for JS globals should close the `intl` gap.
- **Measured by**: `imports_resolve` (target) and `no_css_import`, on fixtures
  `fe_notecard` and `fe_formatdate`. N=3.
- **Result** (`groq/llama-3.3-70b-versatile`, N=3, 12 samples):

  | Criterion | A | B | Δ |
  |---|---|---|---|
  | `imports_resolve` **(target)** | 3/6 (50%) | 5/6 (83%) | **+33 pts** |
  | `no_fences` | 5/6 (83%) | 5/6 (83%) | 0 |
  | `no_css_import` | 6/6 (100%) | 6/6 (100%) | 0 |
  | `min_lines` | 6/6 | 6/6 | 0 |
  | `has_default_export` | 3/3 | 3/3 | 0 |
  | `guards_api_data` | 3/3 | 3/3 | 0 |
  | `endpoints_subset` | 3/3 | 3/3 | 0 |

- **VERDICT: KEEP.** Target improved, nothing regressed.
- **Two honest caveats.**
  1. The **CSS import defect did not reproduce** — variant A passed
     `no_css_import` 3/3. So the measured value of this change is entirely on
     *phantom package imports*, not on the CSS rule. The recurring defect is
     broader than Day 18 recorded: A invented `luxon` (×2) and `date-fns` (×1)
     for `formatDate.js`, none of which appear in `failures.md`. `intl` was one
     instance of a general class: **the model reaches for a date library**.
  2. **Negative examples can be echoed.** B's single failure imported `intl`
     *and* `intl-datetimeformat` — the exact two strings written in the WRONG
     block. Naming a forbidden package teaches the model the package name. Net
     effect was still strongly positive, but the next iteration should test
     counter-examples that describe the wrong shape without naming a real
     package. Logged as a follow-up hypothesis, not folded into this entry —
     one variable at a time.
- **Shipped == measured**: `prompts/frontend_coder_agent.md` is byte-identical to
  the A/B'd variant B. (An earlier attempt to ship an improved-but-unmeasured
  wording was reverted; ship what you measured, or measure what you ship.)
- **Golden files**: 4 passing B samples under
  `backend/tests/fixtures/prompt_tuning/golden/`, all re-scoring clean.
- **Cost**: 12 groq calls, **0 OpenRouter**.

### Entry 2 — context builder (NOT a prompt): sibling component interfaces

- **File**: `backend/app/agents/context_builder.py` — recorded here because it
  changes what the coder sees, which is the same contract a prompt edit changes.
- **Defect**: D2 — prop/interface mismatch across a file seam. `NotesPage` passes
  `onCreated=` to a `NoteForm` that accepts `onCreate`, and `count=` to a `Header`
  that accepts `noteCount`. Severity **breaks-feature** (note creation silently
  no-ops).
- **Attribution**: **CONTEXT BUILDER / PLANNER — not the prompt.** `failures.md`
  Day 18 blamed the exports extractor for dropping prop names. Re-running it
  proves otherwise: it emits `export default function NoteForm({ onCreate }) {`
  verbatim, and `Header.jsx` (448 chars) fell under the full-injection threshold
  and was injected whole. Acting on the Day 18 note would have rewritten an
  extractor that already works.
  The real second cause is the planner: frontend `requires` wiring measured
  **3/17 (17%)** on the NotesTags run vs **21/24 (87%)** on FreelanceInvoicer.
  With `requires` empty the consumer gets **no dependency block at all**, so no
  prompt rule can help — the truth is simply absent.
- **Hypothesis**: injecting the one-line interface of every already-generated
  component/hook into consumer files (pages, App), independent of `requires`,
  puts the prop contract in front of the model in the 83% of cases the planner
  misses. Mirrors what `_build_backend_context` already does for models/schemas
  ("cross-file truth, not trust") rather than inventing a new mechanism.
- **Measured by**: `props_match` (target) on `fe_notespage_unwired` — the task
  exactly as the real planner emitted it, `requires=[]`. N=3.
- **Deterministic pre-check** (0 API calls): rebuilding the unwired fixture's
  context grows it 2389 → 3282 chars and `onCreate`, `noteCount`, `onDelete` go
  from absent to present. Signatures only, never bodies: ~890 chars against a
  16K budget.
- **Result**: see below.
- **Result** (`groq/llama-3.3-70b-versatile`, N=3, 6 samples, `--rebuild-context b`):

  | Criterion | A (frozen ctx) | B (rebuilt ctx) | Δ |
  |---|---|---|---|
  | `props_complete` **(target)** | 0/3 (0%) | 3/3 (100%) | **+100 pts** |
  | `props_match` | 3/3 (100%) | 3/3 (100%) | 0 |
  | `no_fences` | 3/3 | 3/3 | 0 |
  | `min_lines` | 3/3 | 3/3 | 0 |
  | `imports_resolve` | 3/3 | 3/3 | 0 |
  | `has_default_export` | 3/3 | 3/3 | 0 |

- **VERDICT: KEEP.**
- **The first run returned the wrong verdict, and why that matters.** Scored only
  on `props_match`, A passed 3/3 and the rule said REVERT. Inspecting the samples
  showed A rendering `<Header />`, `<NoteForm />`, `<NoteList notes={notes} />` —
  passing **no props at all**. `props_match` only fails on a *wrong* prop, so
  omitting the seam entirely scored as success while being just as broken: the
  header renders no count and the form cannot submit.
  The criterion was one-sided, not the change. Added `props_complete` (every prop
  in a rendered child's signature must actually be passed) and **re-scored the
  already-saved samples offline — zero additional API calls**. This is precisely
  what `--rescore` exists for, and it is the day's strongest argument for saving
  raw outputs rather than only pass/fail counts.
  Lesson for the protocol: a criterion that can only detect a *wrong* value will
  quietly reward *absent* values. Pair every "not wrong" check with a
  "present and complete" check.
- **Golden files**: 3 passing B samples added; 7/7 golden re-score clean.
- **Regression**: 9/9 scheduler, 14/14 import fixer.
- **Cost**: 6 groq calls, **0 OpenRouter**. (Re-scoring: 0.)

### Entry 3 — backend coder: router/schema separation + annotation syntax — **REVERTED**

- **Prompt file**: `prompts/backend_coder_agent.md` (change NOT applied)
- **Defect**: D6 — `day19-fullphase/routers/notes.py:14-34` redefines
  `NoteCreate`/`NoteUpdate`/`NoteResponse`, shadowing its own line-11 imports.
  D11 — `models/note.py:16,20` use `Mapped[int | None]` against the prompt's
  explicit `Optional[X]` preference.
- **Attribution**: **PROMPT**. The router had its schema's full body injected and
  redefined it anyway, against l.16 *"Do NOT redefine models in a router."*
- **Hypothesis**: same shape as the Entry 1 change that worked — both rules are
  stated positively and in passing among many others, so a dedicated WRONG/RIGHT
  block should raise adherence. Counter-examples written *structurally* (a class
  statement in a router) rather than by naming forbidden identifiers, per the
  echo finding in Entry 1.
- **Measured by**: `no_schema_redefinition` (target), plus `no_pep604_union`,
  `py_compile`, `uses_session_dependency`, on `be_router_notes` and
  `be_schema_note`. N=3.
- **Result** (`groq/llama-3.3-70b-versatile`, 10 of 12 samples — see cost note):

  | Criterion | A | B | Δ |
  |---|---|---|---|
  | `no_schema_redefinition` **(target)** | 3/3 (100%) | 3/3 (100%) | **0** |
  | `no_pep604_union` | 6/6 (100%) | 4/4 (100%) | 0 |
  | `uses_session_dependency` | 3/3 (100%) | 3/3 (100%) | 0 |
  | `py_compile` | 6/6 (100%) | 4/4 (100%) | 0 |
  | `no_fences`, `min_lines` | 6/6 | 4/4 | 0 |

- **VERDICT: REVERT.** The existing prompt is already at **100% on the target**.
  There is no headroom: the change would add ~15 lines to every backend
  generation call, forever, for zero measurable gain.
- **The real lesson — rule-violation clarity is not defect frequency.** D6 was
  ranked #3 largely because it was an *unambiguous* violation of an explicit
  rule, which made it feel like an easy, attributable win. But it appeared in
  exactly **one file** in the whole reviewed corpus and did not reproduce once in
  6 fresh samples. The clarity of the violation made it look more common than it
  was. Rank by measured frequency, not by how cleanly a defect can be blamed.
- **Missing samples do not change the verdict.** The two lost samples were
  `be_schema_note`, a fixture that does not carry the target criterion at all;
  the target comparison (`be_router_notes`, 3 vs 3) is complete. A cannot be
  beaten from 100%.
- **Cost**: 10 groq calls, **0 OpenRouter**. The run was cut short by groq's
  **daily** token cap (100k TPD, 99423 used) — the first time this project has
  hit the daily rather than per-minute groq limit. Budget note for future
  sessions: ~3.3k tokens per coder A/B sample means the day's practical ceiling
  is ~30 samples, and that is the binding constraint, not OpenRouter.

### Entry 4 — architecture agent: response shapes + component props

- **Prompt file**: `prompts/architecture_agent.md` (+ the duplicate CRITICAL
  REQUIREMENTS list in `architecture_agent.py`, which competes with the system
  prompt and had to be updated in step)
- **Defect**: D7 — the frontend and backend invent two different shapes for the
  same resource (`schemas/note.py` exposes a scalar `tag_id`; `NoteCard.jsx`
  iterates `note.tags` as an array; `TagFilter.jsx` renders `/tags` objects as
  strings). D2's upstream half and D17 (`__init__.js` in a JS tree).
- **Attribution**: **ARCHITECTURE (upstream)** — the only top-5 class no coder
  prompt can fix. The real endpoints table is `| Endpoint | Method | Description |`:
  three columns, **no response shape anywhere**, so the truth does not exist for
  either coder to read.
- **Deliberately NOT changed**: the folder-tree specificity rules. Real output
  measured **0** occurrences of `...` and **0** of `[more`. They already work;
  strengthening them would have been effort spent on a solved problem.
- **Change**: require a 5-column endpoints table with a mandatory one-line
  example JSON `Response` per row; require every component in the hierarchy to
  name its props; forbid language-foreign files. Each rule is phrased to be
  mechanically checkable, and each is backed by a validator in
  `_architecture_specificity` — a prompt rule alone does not survive model drift.
- **Verification** (one regeneration, `gemini-2.5-flash`, 0 OpenRouter):

  | | endpoint rows | rows with `Response` | props named | validator problems |
  |---|---|---|---|---|
  | OLD | 21 | **0** | no | 3 |
  | NEW | 16 | **16/16** | yes | **0** |

  Doc grew 7287 → 18497 chars, 71 → 95 files, 2 diagrams retained.
- **VERDICT: KEEP.**
- **The change would have shipped broken.** The first regeneration *failed
  validation*: at `max_tokens=5000` the 2.5×-longer document truncated mid-way
  and lost its trailing Component/Security sections — the sharper prompt produced
  strictly worse output than the vague one. Raised to 12000. **A specificity rule
  and its token ceiling ship together**; adding output requirements without
  raising the ceiling silently truncates. This is the general lesson, not a
  one-off.
- **Honest trade-off**: endpoint count fell 21 → 16 (still above the 15 minimum).
  More detail per row, slightly fewer rows.
- **Verification vehicle caveat**: gemini-2.5-flash was used because groq hit its
  daily token cap and `qwen3-coder:free` (the production architecture primary)
  429s on every attempt. Gemini is a *thinking* model, so part of the first
  truncation may be reasoning-token consumption rather than pure output length —
  the raised ceiling covers both. Re-verify on the production primary when
  OpenRouter capacity returns.
- **Fixture staleness**: today's fixtures embed the OLD three-column table. They
  must be re-frozen from a run using this architecture before the next tuning
  session, or they will measure against a world that no longer exists.
