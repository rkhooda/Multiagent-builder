# Improvement 01 — Frontend section decomposition + reviewer-critic loop

Measured record for the first post-v1.0 capability change. Dated, append-only,
same discipline as `INTEGRATION_RESULTS.md`: hypothesis first, one change,
measure, keep or revert, record either way.

**Hypothesis.** Complex frontend files score lowest because one call writes a
whole page one-shot with no second opinion. Two bounded changes should raise
frontend quality: (a) decompose large pages into section-level tasks so each
call has a small, precise job, (b) a reviewer agent that judges a generated file
against its spec, with exactly one targeted revision when it fails.

**Keep/revert rule, fixed BEFORE any treatment number was seen.** Keep only if
all three hold:

1. frontend tier quality improves measurably (score_project.py, tier ≥ 4), AND
2. the extra LLM calls per run stay within the ceiling agreed in ponytail #2
   (≤ +35% total calls, ≤ +0% on the scarcest pool), AND
3. coherence warnings (Task 4, deterministic) do not rise.

If quality rises but coherence degrades, the UI contract is too thin — fix that
before accepting. If it costs more than it gains, revert and record why.

---

## Task 0 — Pre-flight and baseline (2026-08-01)

### Suites green before any generation behaviour changed

| Suite | Result |
|---|---|
| `backend/tests/run_all.py` (16 offline suites, Day 20/21/22 included) | **16/16 green** |
| `test_parallel_runner.py` — Day 20 fake-generator scheduler suite | 12 passed, 0 failed |
| `test_validation.py` + `test_validation_pass.py` — Day 22 crafted breakage | 20 + 14 passed, 0 failed |
| `test_prompt_regression.py` — Day 21 golden outputs | 7/7 pass, 0 API calls |
| `ab_prompt_test.py --rescore tests/fixtures/prompt_tuning/golden` | 7/7 still pass, 0 API calls |

### Baseline: `score_project.py`, offline, zero API cost

Scored from persisted output on disk plus the LangGraph checkpoint. This is the
number the change must beat.

**`2901fb46` TodoSimple** — the Day 25 simple run, the only persisted project
whose frontend phase actually produced files.

| Scope | Scored | Usable (tier 4) | % usable |
|---|---|---|---|
| Whole project | 96 | 21 | **21.6%** |
| **Frontend only** | **51** | **18** | **35.3%** |

> The whole-project figure was first recorded as 21.9% (21/95). Task 1 changed
> `planned_files()` to union the plan's filepaths rather than use them only as a
> fallback — required so decomposition's section components are not scored as
> "unplanned files", which would have made the treatment look worse by
> construction. That admits `backend/Dockerfile` to the planned set, so the
> denominator is 96 and the same 21 usable files read as 21.6%. **The frontend
> column — the number under test — is byte-identical before and after:** 51
> scored, 18 usable, 35.3%, same histogram.

Frontend tier histogram (the number under test):

| Tier | Count | Share |
|---|---|---|
| 0 missing | 4 | 7.8% |
| 1 present (parses? no) | 8 | 15.7% |
| 2 syntax ok, imports broken | 8 | 15.7% |
| 3 imports ok, stub/unplanned | 13 | 25.5% |
| 4 substantive | 18 | 35.3% |

Defect composition of the 33 non-usable frontend files:

- **13 tier-3 "stub"** — Day 20 failure placeholders. These are quota
  starvation, *not* code quality: the file was never generated at all.
- **8 tier-1** — all one defect, `Missing semicolon` at line 4, i.e. a fence or
  header artifact at the top of the file.
- **8 tier-2** — unresolved relative imports (`./api`, `./api.js`,
  `../common/NavLink`, `../taskService`) against files that were never
  generated. This is the *cross-file coherence* class that Task 4 targets.
- **4 tier-0** — build config (`package.json`, `vite.config.js`,
  `tailwind.config.js`, `postcss.config.js`) never generated.

### The medium baseline the plan assumed does not exist

Task 0 asked for "the Day 25 simple **and medium** persisted projects". There is
no medium project to score. Day 25 recorded the medium and complex runs as
`_not run_ — blocked: provider quota exhausted`, and Day 30's capstone halted in
`research` before writing a single file. Every other persisted project scored
below is a pre-Gate-3 halt, not a comparable run:

| Project | Planned | Generated | Usable | Note |
|---|---|---|---|---|
| `14e1209b` HabitTest | 86 | 15 | 17.4% | halted before frontend phase |
| `f1a063f8` FreelanceInvoicer | 70 | 13 | 18.6% | halted before frontend phase |
| `341b1dc2` NotesTags Day15 | 59 | 13 | 20.3% | halted before frontend phase |
| `113cf67c` NotesTags | 77 | 12 | 14.1% | halted before frontend phase |
| `ffd448ab` SplitTest | 48 | 1 | 2.1% | halted before frontend phase |

Each generated 12–15 files, all backend/database, and 0 substantive frontend
files. Their `% usable` is dominated by `missing`, so comparing a treatment run
against them would measure *how far the run got*, not *how good the frontend
was*. They are recorded here for completeness and excluded from the A/B.

**Consequence for Task 6:** the A/B baseline is `2901fb46`'s frontend column —
**35.3% usable, 51 files** — and the treatment must be a medium brief run
end-to-end for a like-for-like comparison. That is a live-run cost, and the
constraint that stopped Days 25 and 30 has not changed.

---

## Ponytail conclusions, and what was deliberately NOT built

The three decision points, and the reuse audit's output. The "not built" list is
part of the deliverable: every line is something a naive implementation would
have added, with the existing thing it would have duplicated.

### #1 — decomposition granularity and coherence

- **Threshold**: reuse `estimated_complexity`, already on `TaskSchema` and already
  user-editable at Gate 3. Decompose iff the task is a page/screen AND complexity
  ≥ threshold (`high` default). Fan-out bounded 2–5.
- **Contract authorship**: **derived deterministically** from `tech_stack` + the
  validated plan. Not the architecture agent (Day 26 measured it at 11,996/12,000
  output tokens — a new section truncates the doc it barely fits), and not a cheap
  call (quota is the binding constraint of the whole system).
- **Minimum contract**: styling system, one fixed token scale, the shared
  primitives that already exist, prop conventions, naming rules. Measured at
  813–861 chars (~215 tokens, ~5% of the 4K per-file budget). Dropped **last** in
  the degradation order — it is the coherence anchor the way the API section is
  the hallucination anchor.

### #2 — critic economics, placement, budget

- **A correction to the brief's premise.** OpenRouter is no longer the coders'
  pool. Day 23 delisted `qwen3-coder:free` and both coders moved to groq primary;
  the only OpenRouter slug left in `MODELS` is `requirements`' fallback. **The
  scarce pool today is groq's 100k tokens/day** (measured refusal at 95,966), and
  that is the coders' primary. The design intent survives — route review off the
  scarce pool — but the provider it names changed.
- **Placement**: inside the existing worker, under the permit it already holds for
  its whole lifetime. No second wave, no new semaphore, `parallel_runner`
  untouched. A dependent awaiting this file's future therefore sees the *revised*
  content, which a post-hoc pass could not offer.
- **Routing**: `frontend_review` → gemini primary → groq fallback → Ollama.
- **Agreed ceiling, fixed before measuring**: ≤ +35% total calls per run, **+0 on
  the scarce pool's primary tier**.
- **Budget**: `validation/report.py` already *is* the one account. A revision
  charges the identical `repair:{path}` key. One atomic `try_reserve_repair` added
  because the workers are a thread pool. No second ledger, no `REVIEW_CEILING`.

### #3 — minimalism and reuse audit: what was NOT built

| Not built | Because |
|---|---|
| A `decomposition.py` module / section-splitter | It is a planning-prompt section (§ 4b) + one validator in the existing registry + one optional schema field |
| An LLM call to author the UI contract | `build_ui_contract` is pure and derived; zero calls, cannot drift from the plan |
| An architecture-prompt change | Measured at its token wall; Day 21 Entry 4 — a specificity rule and its ceiling ship together, and there is no ceiling left |
| A second review wave in `parallel_runner` | The worker already holds one permit across two LLM calls; a wave needs its own commit and broadcast bookkeeping |
| A new semaphore / provider permit for the reviewer | The worker's existing permit covers it; `OLLAMA_MAX_CONCURRENT` still applies under prefer-local |
| A `review_counts` ledger or `REVIEW_CEILING` | Two counters over one budget is exactly how a file spends 2 repairs *and* 2 revisions |
| A multi-round critic loop | 1 review + 1 revision, enforced by construction — no loop to bound |
| A new Gate 4 review panel | Counts fold into `render_summary`, which already prefixes the QA report; marks ride the existing file-tree row |
| Any `metrics_store` change | `GROUP BY agent` has no whitelist, so `frontend_review` rows already carry calls/tokens/latency/outcome |
| A JS import auto-fixer for coherence warnings | `import_fixer` is Python-only by construction and Day 22 deliberately left JS rewriting undone; there is no existing "safe class" to reuse |
| A re-implementation of "do sections import primitives that exist" | `validate_js_imports` already answers it for every relative import |
| `context_files` on `TaskSchema` | The brief named it, but the real field is `context_sections` and it already exists |
| Mutation of `state["file_list"]` by planning | Re-plan hazard: `_valid_plan` would then require the *previous* run's sections. The union is computed where it is read instead |

---

## Task 2 measurement — reviewer calibration (2026-08-01)

Zero-risk, affordable, and the brief's own stated failure mode: *"if the pass
rate is near zero on clearly-fine files, the prompt is wrong, not the code."*

**Sample**: the 14 tier-4 (substantive) `.jsx`/`.js` frontend files from
`2901fb46` — files `score_project.py` already judged usable. A reviewer that
sends these back is miscalibrated. Run against real providers with the cache off.

### Round 1 found a defect I had shipped

| | Round 1 (`REVIEW_MAX_TOKENS=700`) |
|---|---|
| Reviews that completed | **4 / 14** |
| Failure mode | every gemini attempt stopped at **exactly 696 completion tokens**, `finish_reason=length` |

The verdict never got emitted: `gemini-2.5-flash` reasons before answering, so
the budget was consumed by reasoning. The one-shot repair truncated identically.
**Fail-open worked exactly as designed and that is what made this dangerous** —
all ten failures resolved to "pass", so the reviewer was silently doing nothing
while the run looked healthy.

This is Day 21 Entry 4's lesson restated: **an output requirement and its token
ceiling ship together.** Sizing a budget from how long the *answer* looks is
wrong whenever the model thinks first. Fixed at 2000, with a regression test
(`test_review_budget_clears_the_measured_truncation_point`) naming the measured
number so nobody re-lowers it on the reasoning that "a verdict is short".

Incidental finding worth recording: the **groq fallback returns the identical
verdict in 36–95 completion tokens and ~0.5s**, versus gemini's ~10s. That is a
real argument for flipping the routing, and it loses anyway — groq's 100k/day is
the pool that halts this pipeline and it is the coders' primary, so ~32 reviews
would consume the entire generation budget. Spending gemini's 1M/day headroom on
reasoning tokens is the cheaper mistake.

### Round 2 — calibrated, and one false-positive class found

| | Round 2 (`REVIEW_MAX_TOKENS=2000`) |
|---|---|
| Reviews that completed | **14 / 14** |
| verdict = pass | 7 (**50%**) |
| verdict = revise | 7 |
| Mean issues per file | 1.14 |

Inspecting all 7 `revise` verdicts by hand:

**4 are true positives**, and three are the anti-hallucination rule doing its job:

- `TaskForm.jsx`, `Select.jsx` — call `/api/v1/categories`, absent from the
  endpoints table given to them.
- `CategoryItem.jsx` — calls `DELETE /api/v1/categories/{id}`, likewise absent.
- `common/Select.jsx` additionally fetches its own data, which is a genuine
  design defect in a *shared primitive*.

**3 are false positives, and 2 share one cause**: `Modal.jsx` and `TaskCard.jsx`
were both failed for "does not handle loading, error and empty states" — neither
fetches anything. The prompt already said not to ask presentational components
for those states; the instruction was simply too weak and too late in the list.
Fixed by making it a **precondition test applied first**, then re-measured
(round 3 below).

### Round 3 — after the prompt fix

One change: criterion 5 became a **precondition test applied first** ("does this
file contain an actual data fetch? if not, skip this criterion entirely") instead
of a caveat at the end of the paragraph.

Round 3 hit the quota wall partway through — 8 of 14 completed before gemini's
free RPM and groq both refused. Comparing on the **8 files that completed in both
rounds** (the only honest comparison):

| | Round 2 | Round 3 |
|---|---|---|
| verdict = pass | 3/8 (38%) | 3/8 (38%) |
| total blocking issues | 9 | **7** |
| false "missing loading/error/empty" on non-fetching files | 2 | **0** |

Per file, blocking issues: `CategoryItem.jsx` **3 → 1**, everything else
unchanged. `Modal.jsx` kept one blocking issue but its *reason* changed from the
false positive to a contract-compliance point.

**VERDICT on the prompt change: KEEP.** It removed a false-positive class without
touching the pass rate on true positives — strictly better on the targeted
criterion, no worse on any other.

### Is the reviewer a budget leak? No.

The brief's failure mode was "a critic that always finds something". It doesn't:

- **3 of 8 files pass with zero issues**, and they are the right ones —
  `Checkbox`, `DatePicker`, `Input` are simple presentational components, and it
  waved them through unprompted. `Textarea`, `AuthLayout`, `config.js` and
  `authUtils.js` also passed cleanly in round 2.
- Its `critical` findings are concentrated in one class and look correct: three
  separate files call `/api/v1/categories` or `DELETE /api/v1/categories/{id}`,
  endpoints absent from the table they were given. That is the anti-hallucination
  rule doing exactly its job.
- `common/Select.jsx` was flagged for fetching its own data — a genuine design
  defect in a *shared primitive*, and precisely the kind of thing parsers cannot see.

**One caveat, stated because it cuts against the number.** The remaining
"does not follow the UI contract's token classes" flags on `Button.jsx` and
`Modal.jsx` are a **measurement artifact**: these files come from a run that
predates the UI contract, so they cannot possibly follow it. The reviewer is
literally correct and the sample is unfair on that one criterion. Files generated
in a treatment run would have the contract in context. The true pass rate on
contract-aware files is therefore **higher than 38%**, by an unmeasured amount.

---

## Task 6 — A/B result and verdict

### The A/B did not run. Here is the arithmetic.

| | |
|---|---|
| Cost of one full pipeline run | **~229,000 tokens** (Day 25, `2901fb46`, a *simple* brief, 95 files) |
| Arms required (same brief, control + treatment) | 2 |
| Estimated cost | **~460,000 tokens**, medium brief ≥ that |
| Groq allowance | 100,000 tokens/day — **and groq is the coders' primary** |
| Groq remaining after calibration | **8,396 (91.6% consumed)** |
| Gemini | 1M/day tokens, but free-tier **RPM** exhausted during a 14-call job |

The requirement exceeds the day's binding allowance by roughly **an order of
magnitude**. This is not a scheduling problem that a retry fixes.

**This is the same wall, for the third time.** Day 25 could not run its medium or
complex arms (`_not run_ — blocked: provider quota exhausted`). Day 30's capstone
halted in `research` having generated zero files. Improvement 01 now joins them.
`INTEGRATION_RESULTS.md` already states the conclusion this run re-confirms:
*provider quota — not model capability and not project complexity — remains the
thing that stops this pipeline.*

Attempting a partial run anyway was considered and rejected: it would have spent
the remaining quota to produce another unscored halt, which is what the two prior
attempts produced, and it would have destroyed the reviewer calibration's
reproducibility. **A documented non-measurement is worth more than a third
unscored halt.**

### The verdict, under the rule fixed before any number was seen

The rule required **all three** of: measured frontend quality gain, calls within
the agreed ceiling, coherence warnings not rising. **None of the three has been
measured.** So this is not a keep.

It is also not a revert: nothing has been shown to cost more than it gains
either. The honest status is **UNPROVEN**, and the disposition that follows is:

> **The code ships. The behaviour does not.**
> `DECOMPOSE_FRONTEND` and `REVIEW_MODE` both default to **off**, so v1.0
> behaviour is what actually runs. The feature is complete, tested (63 offline
> assertions across two new suites), reversible, and one env var from being
> measured — but an unproven change does not get to be the default, which is the
> entire point of pre-registering the rule.

`test_both_features_default_to_off` enforces this, so the default cannot drift
back on without someone deliberately deleting the test.

### What was measured, and what it is worth

| Measured | Result | Worth |
|---|---|---|
| Reviewer completion rate | 4/14 → **14/14** after the token fix | Found a real shipped defect |
| Reviewer pass rate on known-good files | **38%**, lower bound (sample predates the contract) | The critic is not a budget leak |
| Reviewer true-positive class | 3 files calling endpoints that do not exist | The critic finds what parsers cannot |
| Prompt fix (criterion 5) | blocking issues 9 → 7, false-positive class eliminated | KEEP |
| Composition coherence checks | 10 offline cases, zero API cost | Catches decomposition's main risk for free |
| **Frontend tier quality, treatment vs control** | **not measured** | **the verdict this change actually needed** |

### What would flip the default

One full pipeline run per arm on the same medium brief, needing roughly 460k
tokens across providers whose combined free daily allowance is ~1.1M but which
rate-limit long before that. Concretely, any one of:

1. a paid tier or a second day's allowance for both arms;
2. a local tier on ≥16GB hardware (Day 30: the 8GB machine pages at the context
   size local models need to be correct);
3. accepting a weaker design — reusing `2901fb46` as the control against a fresh
   treatment run of a *different* brief. **Rejected**: it confounds the brief with
   the treatment, and a confounded number is worse than none because it looks like
   evidence.

Then: `DECOMPOSE_FRONTEND=true REVIEW_MODE=selective`, score both arms with
`score_project.py`, compare frontend-only tier 4 against **35.3% (18/51)**, and
check total calls (ceiling +35%) and coherence warnings (must not rise).

## Addendum (2026-08-03, ceiling audit)

Two facts about this improvement's measurement were established later by
[CEILING_AUDIT.md](CEILING_AUDIT.md):

1. **The 700 → 2000 fix was still short.** No gemini review has ever
   completed: 21 attempts truncated at the 700-era ceiling and one at 1,996
   against the raised 2000 — gemini-2.5-flash spends ~2.4–2.7k tokens
   *reasoning* before its answer (the same behaviour measured on QA), so a
   2000 ceiling sized from answer length alone starves it.
   `REVIEW_MAX_TOKENS` is now 4000, pinned by test in both profiles.
2. **This measurement's fallback traffic consumed 88,004 groq tokens on
   2026-08-01** — 26 silent gemini→groq failover reviews at ~3.3k prompt
   each, ~88 % of the scarce pool's daily allowance. The rate-limit pressure
   recorded above ("gemini free RPM and groq both refused") was therefore
   partly self-inflicted by the starvation defect, not purely ambient quota
   scarcity. Failovers are now counted per run with cause and token cost
   (degraded_events, 2026-08-03).
