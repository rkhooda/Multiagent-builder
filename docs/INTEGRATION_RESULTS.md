# Integration Results — Cross-Complexity Quality Baseline (Day 25)

The first stress test of the full pipeline at product scale. Three real briefs of
escalating complexity, scored with one fixed rubric, to answer honestly: **where
does this system break, and what breaks first?**

The deliverable is the numbers and the known-limits, not the generated projects.

---

## The rubric (fixed before any run)

Scored by `backend/scripts/score_project.py` — zero API cost, reads persisted
output from disk plus the LangGraph checkpoint, re-runnable any number of times.
It delegates all checking to the Day 22 validators; it adds only the tier ladder.

A file's tier is the highest rung it reaches:

| Tier | Meaning |
|---|---|
| `missing` | planned but absent from disk, or present but empty |
| `present` | non-empty file on disk |
| `syntax` | parses (Python `ast`/`compile`, JS/JSX `@babel/parser`) |
| `imports` | no phantom relative imports, no packages missing from the manifest |
| `substantive` | non-stub **and** in the plan's `file_list` |

**"Usable" = reaching `substantive`.** Fixed once, applied identically to all
three projects. A rubric that moves between runs cannot measure degradation.

Deliberately **not** automated: "would plausibly run". Deciding it requires
executing an arbitrary generated stack. It is recorded separately as a sampled
manual judgement so the automated number stays reproducible and the subjective
one stays visibly subjective.

Pinned by `backend/tests/test_score_project.py` (16 assertions, no network) —
because a silent change to stub detection or the usable threshold would move all
three numbers at once and invalidate the comparison without failing anything.

---

## Quota budget & run plan (ponytail #3)

### The stated premise was stale — corrected before budgeting

The plan for today assumed "OpenRouter free tier ≈50 requests/day shared by both
coders + architecture (DeepSeek/Qwen)". **That has not been true since Day 23.**
`llm_router.MODELS` now routes architecture, both coders, database and devops to
`groq/llama-3.3-70b-versatile` primary with `gemini-2.5-flash` fallback.
OpenRouter survives only as the QA primary (`nemotron`) and a requirements
fallback — roughly **1 call per run**.

Verified against the live API rather than assumed: `usage_daily: 0`,
`is_free_tier: true`. OpenRouter is not today's constraint and budgeting the day
around it would have been budgeting around the wrong provider.

**The real ceiling is Groq**, and specifically tokens-per-minute rather than
requests-per-day: the Day 20 parallel runner issues concurrent coder calls each
carrying ~12k chars of context, so TPM binds long before the ~1000 RPD does.

### Estimated calls per run

| Phase | Provider | Simple | Medium | Complex |
|---|---|---|---|---|
| research / requirements / planning | Gemini | 3 | 3 | 3 |
| architecture | Groq | 1 | 1 | 1 |
| frontend + backend coders | Groq | ~15 | ~22 | ~30 |
| database | Groq | ~3 | ~4 | ~5 |
| devops | Groq | ~6 | ~6 | ~6 |
| qa | OpenRouter | 1 | 1 | 1 |
| validation repair (bounded) | Groq | 0–10 | 0–10 | 0–10 |
| **total** | | **~29** | **~37** | **~46** |

Across all three: ~9 Gemini, ~3 OpenRouter, ~100–130 Groq. Within daily limits;
the risk is a TPM burst mid-coding, which the Day 17 backoff chain turns into a
pause rather than a failure.

**How this estimate was wrong (recorded, not quietly amended).** The single
simple project issued **233 attempts**, roughly 8× the ~29 estimated. Two causes,
both worth carrying into Day 26's budgeting:

- The estimate counted *files*, not *attempts*. Every failure burns up to four
  attempts (two per model across two tiers), so a rate-limited run costs
  multiples of its nominal size — the burn rate rises exactly when quota is
  scarcest.
- It assumed Gemini had generous daily headroom. Its free tier is 20
  requests/day/model, which no per-run estimate could have absorbed.

Correcting the OpenRouter premise was right and necessary, but it replaced one
unverified assumption with another: Gemini's daily limit was never checked
against the live API the way OpenRouter's was. The lesson is narrower than
"budget better" — **verify the limit of every provider on the critical path, not
just the one the plan happens to name.**

### Execution order: simple → medium → complex

Fail fast and learn cheap. The simple run is the quality **ceiling** — if a todo
app scores poorly, every downstream number is noise and the defect is systemic,
so it must be fixed before spending quota on harder briefs. Running all three to
a checkpoint first would maximise wall-clock and yield three half-datasets
instead of one complete comparison.

### Resume contingency

No new snapshot mechanism was built. **The checkpointer already is the
snapshot** — Day 24's cold resume and restart-from-stage persist the approved
Gate-3 plan, so a rate-limit pause mid-coding resumes from
`restart from code_generation` rather than restarting. The only thing needed is
the project id, recorded in the results table below. (YAGNI: building a separate
plan-snapshot file would duplicate state the checkpoint already holds.)

**Minimum viable data from the complex run:** reaching Gate 3 (architecture +
plan approved) already reveals most of what degrades, since the working
hypothesis is that architecture degrades earliest and cascades. A complex run
that stops after planning is an acceptable outcome, not a failed one.

---

## Results

| Project | Complexity | Planned | Generated | Missing | % usable | QA issues | Repair calls | Total tokens | Wall-clock | Top defect classes |
|---|---|---|---|---|---|---|---|---|---|---|
| `2901fb46` TodoSimple | simple | 95 | 77 | 19 | **21.9%** (21/96) | n/a — never reached QA | 0 | 229,156 | 39.9 min | never generated (19), syntax (17), stub (9), unresolvable import (8) |
| _not run_ | medium | — | — | — | — | — | — | — | — | blocked: provider quota exhausted |
| _not run_ | complex | — | — | — | — | — | — | — | — | blocked: provider quota exhausted |

**The simple run is a partial.** It halted at `backend_code` with 0 of 26 backend
files delivered. It never reached database completion, QA, devops or Gate 4,
which is why QA issues and repair spend are empty rather than zero-as-measured.

**The 21.9% is not a measure of model quality.** It is a measure of output under
provider starvation: 233 LLM attempts produced 45 successes and 188 failures,
and essentially every failure was a 429. Three of the four top defect classes —
never-generated, stub, and (before it was fixed) syntax — are all the same
underlying event: the request never reached a model. Reading this as "the coder
writes bad code" would be precisely the misattribution ponytail #2 warns about.

An earlier version of this row read 43.8%. That number was wrong: the rubric
counted JSX failure placeholders as real files. Corrected in `1e2df35`; the
honest figure is 21.9%.

### Reference point (pre-Day-18 baseline)

Scored to sanity-check the rubric against a known-weak project, not as part of
today's comparison:

| Project | Planned | Generated | Missing | % usable |
|---|---|---|---|---|
| `113cf67c` NotesTags (Day 15 era) | 77 | 12 | 66 | 14.1% |

The dominant defect class there is `missing` — 66 planned files never generated.
A missing file is worse than a broken one, and only the plan reveals them; this
is why `planned` is a column rather than a footnote.

---

## What actually happened: the day did not measure what it set out to measure

The plan was a degradation curve across three complexity tiers. **That curve does
not exist in this data**, and presenting one would be inventing it. One project
ran, partially. The binding constraint was never model capability at complexity —
it was that the pipeline could not obtain enough provider calls to finish a
single *simple* project.

What the day produced instead is more actionable: **four systemic defects, each
of which blocked the pipeline outright**, found only by running a real brief end
to end. Three would have blocked the medium and complex runs identically, so no
amount of extra quota would have yielded a complexity comparison first.

The degradation question is deferred, not answered. It is Day 26 AM work.

## Provider ceiling (measured, inherent — do not chase)

The single most important number found today:

| Provider | Limit that binds | Observed today |
|---|---|---|
| Gemini 2.5 Flash | **20 requests/day/model** (`GenerateRequestsPerDayPerProjectPerModel-FreeTier`) | 15 ok / 77 rate-limited, then exhausted for the day |
| Groq llama-3.3-70b | **12,000 tokens/minute** | 32 ok / 109 rate-limited |
| OpenRouter | ~50/day | ~unused; QA primary `nemotron` returns an upstream error |

Two structural consequences, both verified rather than inferred:

1. **The pipeline cannot complete one simple project per day on free tiers.** A
   single simple project planned ~95 files and issued 233 attempts. The Gemini
   daily allowance is 20.
2. **Planning is structurally unservable by Groq.** It emits ~26,900 completion
   tokens in one call; Groq's ceiling is 12,000 tokens *per minute*, so the
   request can never fit regardless of pacing or retries. With Gemini exhausted,
   nothing gets past planning — the pipeline's largest call has a hard
   single-provider dependency.

This is the **inherent** side of the ponytail #2 split. No prompt, context or
validation change moves it. Documented, and left alone.

The tell separating inherent from systemic was clean today: for every defect
below, the model either never received the request or received one our own code
had corrupted. Nothing failed because a model saw a well-formed problem and
answered it badly. That is why all four fixes are systemic, and why "the free
models aren't good enough" would have been the wrong conclusion to draw from a
21.9% score.

## Fixes applied (before / after)

All four were found by running a real brief; none was visible in unit tests.
Each was committed separately with a regression test and re-verified offline.

| # | Defect | Impact | Evidence after fix | Commit |
|---|---|---|---|---|
| 1 | Uniform 90s timeout vs non-uniform output size | Planning could not reliably complete **at all**; its only recorded success ever took 84.5s against a 90s ceiling | Same project's planning call succeeded at **109.2s / 26,894 tokens** — 21% past the old ceiling | `877407d` |
| 2 | Implicit layering forged a dependency cycle | **47/47 frontend tasks unschedulable** on a plan that was correct and acyclic | All 47 schedule; ordering still correct (config → api client → consumers) | `7081d39` |
| 3 | No request pacing; 429 treated as fatal | 59 rate-limits vs 13 successes; **34 of 47 files lost** | Same phase: **26 ok (2×)**, 8 failed (was 11), 13 blocked (was 23) — and the run cleared the 50% halt gate instead of aborting | `26a0e1f` |
| 4 | Failure placeholders were syntactically invalid | **17 of 96 files** counted as syntax defects the model never produced | Stubs parse; defect reclassified from "syntax error" to "generation failed" | `ac3ba89` |

Fix 4 deliberately does **not** raise `% usable` — it moves 17 files from a false
defect class into their true one. That reclassification *is* the value: it stops
a phantom "the coder emits broken syntax" investigation that the raw numbers
would otherwise have justified.

A fifth commit (`1e2df35`) corrects the rubric rather than the pipeline: once
stubs parsed, the JSX placeholder cleared the size floor and scored as a real
file, inflating the run from a true 21.9% to a flattering 43.8%.

## Known limits

- **Free-tier throughput (inherent).** Quantified above. The pipeline needs
  roughly an order of magnitude more daily calls than free tiers allow. Day 29's
  local Ollama is the structural answer; nothing before it is.
- **Planning's single-call size (systemic, deferred).** ~26,900 tokens in one
  request is what makes it unservable by any TPM-limited provider. Splitting the
  plan across calls risks incoherence — a real design change, not a tuning knob,
  and deliberately not attempted under a ceiling where it could not be A/B'd.
- **QA primary model is dead.** `openrouter/nvidia/nemotron-3-nano-omni-...:free`
  returns an upstream error on every call; its fallback is Gemini, which is
  exhausted, so QA cannot run at all. This is the Day 23 delisting failure mode
  recurring — `failures.md` already warns these free slugs vanish without notice.
- **Gate 3 plan validation accepts cyclic `requires`.** Defect 2 was fixed in the
  scheduler, which is the right place for robustness, but validation still does
  not reject cycles — the old error message even asserted that it should. Low
  priority now the scheduler is cycle-proof.
- **Planning over-decomposes.** A todo app produced a **96-task, 95-file** plan.
  Whether that hurts quality is unmeasured, but it multiplies cost directly
  against the binding constraint.

## Roadmap signals

- **Day 26 (optimisation):** the pacing layer landed here early out of necessity.
  What remains is load distribution — OpenRouter's allowance sat unused while
  Gemini and Groq were hammered, because routing pins each agent to a fixed pair.
- **Day 29 (Ollama):** promoted from convenience to prerequisite. It is the only
  path to a repeatable full-pipeline run, and therefore to the degradation curve
  this day could not produce.
- **Day 30 (roadmap):** planning's output size and the 96-task decomposition are
  the two candidates with the clearest cost/quality leverage.

---

## Tooling caveat: LangSmith attribution unavailable

Day 23's tracing workflow (`docs/OBSERVABILITY.md`) is the intended attribution
tool for today. It is **off in this environment**: `LANGCHAIN_TRACING_V2=false`
and `LANGCHAIN_API_KEY` is a placeholder, not an `lsv2` key. Attribution
therefore uses the local substitute — `metrics.db` records per-attempt model,
tokens, latency, outcome and `context_chars`, and the exact prompt any agent
received is reconstructible offline from the persisted state plus the prompt
templates. This is weaker than a trace for seeing *what the model saw*, and that
limitation is noted wherever it affected a fix decision below.
