# Optimisation Targets & Results Ledger (Day 26)

Every optimisation on Day 26 traces to a line in this file. Nothing is optimised
on intuition. Where the measured data contradicted the day's planned assumption,
the data wins and the contradiction is recorded rather than quietly dropped.

Source: `backend/metrics.db` (258 attempt rows, Day 23 baseline + Day 25 runs),
`docs/QUALITY_BASELINE.md`, `docs/INTEGRATION_RESULTS.md`.

---

## 0. Pre-flight state

| Suite | Result |
|---|---|
| `test_prompt_regression.py` (7 golden outputs, 0 API calls) | PASS |
| `test_parallel_runner.py` | PASS |
| `test_validation_pass.py` | PASS |
| `test_metrics_store.py` | PASS |
| `test_score_project.py` | PASS |
| `test_import_fixer.py` | PASS |
| `test_validation.py` | PASS |
| `test_metrics_attribution.py` | PASS |
| live-API agent tests (`test_research_agent_sections`, `test_architecture_agent`, …) | BLOCKED — not a regression |

**The live tests are blocked by an exhausted quota, not by a code defect:**

```
GroqException: Rate limit reached for model `llama-3.3-70b-versatile` …
on tokens per day (TPD): Limit 100000, Used 95966, Requested 7168.
Please try again in 45m7.776s
```

This is itself the day's headline evidence: the pipeline exhausted a provider's
**daily token** allowance. All Day 26 validation is therefore offline
(re-score + fixture suites), as the day's quota guidance anticipated.

---

## 1. Measured baseline

Averaged over `outcome='ok'` only, so failed attempts do not distort cost.
Sample sizes are small (`n` per agent below) — every claim is qualified by it.

| Agent | n | avg input tok | avg output tok | avg latency | max latency | ctx chars |
|---|---:|---:|---:|---:|---:|---:|
| planning | 3 | **10,484** | 21,200 | 84.1 s | 109.2 s | 35,861 |
| architecture | 4 | 3,609 | 10,616 | 43.3 s | 51.6 s | 14,560 |
| requirements | 2 | 2,789 | 4,496 | 22.3 s | 22.5 s | 12,624 |
| frontend_code | 43 | 2,563 | 356 | 16.0 s | 32.9 s | 10,170 |
| research | 2 | 2,399 | 4,078 | 24.9 s | 26.9 s | 11,517 |
| qa | 2 | 1,840 | **17,110** | **181.2 s** | **354.4 s** | 7,557 |

### Outcome distribution — the dominant failure mode

| Outcome | Count | Share |
|---|---:|---:|
| `rate_limit` | 194 | **75.2 %** |
| `ok` | 56 | 21.7 % |
| `timeout` | 5 | 1.9 % |
| `error` | 3 | 1.2 % |

By model: `groq/llama-3.3-70b-versatile` 117, `gemini/gemini-2.5-flash` 77.

Three quarters of all LLM attempts are rejected before doing any work. This,
not token count, is the pipeline's binding constraint.

---

## 2. Targets, derived

### T1 — QA's primary model ignores its output budget (biggest wall-clock win)

The single slowest thing in the pipeline, and it is not context bloat.

| Attempt | Model | in | out | latency |
|---|---|---:|---:|---:|
| 1 | `openrouter/nvidia/nemotron-…-reasoning:free` | 1,952 | **32,768** | **354.4 s** |
| 2 | same | — | — | error (0.6 s) |
| 3 | `gemini/gemini-2.5-flash` | 1,728 | 1,452 | **7.9 s** |

The QA call site asks for `max_tokens=3000`. The reasoning model returned
**32,768** — it did not honour the cap (32768 = 2^15, its own ceiling). It spent
**354 seconds** to produce output the Gemini fallback produced in **7.9 seconds**
with 22× fewer tokens.

> **Contradiction with the day's plan.** The plan assumed QA was slow because of
> a bloated ~20k-token *input* needing batch summarisation. Measured QA input is
> **1,840 tokens** — among the *leanest* in the pipeline. QA is slow because of
> uncapped reasoning *output*. Trimming QA's context would have removed real
> signal to fix a problem that does not exist.

**Action:** demote the reasoning model; it is a 45× latency tax for no measured
quality gain. Not a context-trimming task.

### T2 — Output budgets are already per-call-site, and they are *binding*

Every call site already passes a tuned `max_tokens`. There is no generic default
to fix.

| Agent | budget | max observed | at limit? |
|---|---:|---:|---|
| architecture | 12,000 | 11,996 | **yes ×2** |
| requirements | 4,500 | 4,496 | **yes ×2 (both calls)** |
| research | 4,500 | 4,368 | near (97 %) |
| frontend_code | 1,500 | 1,496 / 1,495 | **yes ×2** |
| qa | 3,000 | 32,768 | **budget ignored by provider** |
| planning | dynamic (`min(32000, max(4500, files×300))`) | 26,894 | no |

> **Contradiction with the day's plan.** The plan assumed several agents used a
> generic default and needed budgets *introduced*, with the PDF's starting points
> (research 3000, requirements 2500, architecture 4000) as defaults. Those numbers
> are **below what these agents already produce** — adopting them would truncate
> every architecture and requirements document the pipeline generates. Both
> requirements calls on record already stop 4 tokens short of their cap.

**Action:** the deliverable is **truncation detection**, not budget reduction.
Lowering budgets here is a quality regression, so it is not done.

### T3 — Daily token exhaustion is untracked

The limit that actually halted work today was Groq **TPD** (100,000 tokens/day,
95,966 consumed), not requests-per-minute. Nothing in the system tracks or
surfaces daily consumption; the pipeline discovers exhaustion only by failing.

**Action:** track and expose per-provider daily token spend.

### T4 — Rate limiting already exists and is at the correct grain

`llm_router._pace()` (Day 25) already enforces a per-model minimum interval,
process-wide, reserving each slot under the lock *before* sleeping so concurrent
workers queue into distinct slots rather than stampeding together.

> **Contradiction with the day's plan.** The plan called for a token bucket keyed
> **per provider**. The provider errors prove limits are enforced **per model**
> (`Rate limit reached for model llama-3.3-70b-versatile`). A per-provider bucket
> would be *less* accurate — it would lump independently-quota'd models into one
> allowance and over-throttle. Day 20's runner is also a single **global**
> semaphore, not the per-provider semaphore the plan assumed.

**Action:** keep the per-model pacer; add the genuinely missing piece (T3) rather
than replacing working code with a differently-shaped equivalent.

### T5 — Highest-input agent is planning, not QA

planning at 10,484 input tokens (35,861 ctx chars) is 2.9× the next agent. It is
also the only agent whose input is plausibly compressible (it receives the full
architecture document). This is the *only* evidence-supported trimming candidate.

**Action:** evaluate; keep only if tokens drop meaningfully **and** offline
re-score holds. n=3 — treat any result as provisional.

---

## 3. Where the wall-clock actually is

Per-call latency, worst first: **qa 181 s → planning 84 s → architecture 43 s**.
Everything else is under 25 s.

Fast mode and any latency work must target these three. Shaving `frontend_code`
(16 s, but ×43 files) is a throughput question the parallel runner already owns,
and is gated by rate limits (T4), not by model speed.

---

## 4. Results ledger

| # | Change | Before | After | Quality delta | Verdict |
|---|---|---|---|---|---|
| T1 | QA primary model demoted | 354.4 s / 32,768 out | 7.9 s / 1,452 out (measured fallback) | none — same batch, same reviewer output | **kept** |
| T2 | truncation detection via `finish_reason` | invisible | recorded + warned per attempt | detection only | **kept** |
| T2b | budget *reduction* | — | — | would truncate 4 agents | **rejected** |
| T3 | daily token budget skip | doomed round trip per tier | 0 ms skip, fails over | none | **kept** |
| T4 | adaptive interval widening on 429 | fixed interval | ×1.5 per 429, capped ×8 | none | **kept** |
| — | response cache | restart re-spends every call | 0 calls across 4 upstream agents | none (bypass verified) | **kept** |
| T5 | coder folder map grouped | 1,882 chars | 1,103 chars (−42%) | none — provably lossless | **kept** |
| T5b | planning context trim | 10,484 in | — | not attempted | **deferred** |
| T5c | architecture block trim (51% of ctx) | — | — | endpoint-hallucination risk | **rejected** |
| — | fast mode | — | budget whitelist + repair skip | unmeasured (no quota) | **kept, unvalidated** |

Frontend coder input: **87.5k → 77.5k tokens per run** (−11.4%), ~10% of Groq's
daily allowance recovered per run.

### Quality baseline: unchanged

Offline re-score (`score_project.py`, 0 API calls) against Day 25's persisted runs:

| Project | Day 25 baseline | Day 26 re-score |
|---|---|---|
| `2901fb46` TodoSimple | 21.9% usable (21/96) | **21.9% usable (21/96)** |
| `341b1dc2` NotesTags | — | 20.3% usable (12/59) |

No optimisation regressed the baseline. This is by construction for changes that
do not alter generation (cache, budgets, limiter); for the folder-map change it
is supported by the losslessness assertion rather than by a regenerated run,
which quota did not permit.

---

## 5. What could not be measured today, and why

Honesty about the gaps matters more than a full-looking table.

- **Fast mode's speed/quality tradeoff is unvalidated.** The PDF claims ~50%
  faster at ~80% quality. Validating that needs two live runs of the same brief;
  Groq's daily allowance was already exhausted before the day's work began. The
  claim is neither confirmed nor refuted here. What *is* established by
  measurement is that the mechanism the claim assumes — halving `max_tokens` —
  cannot deliver it, because a lower ceiling truncates rather than condenses.
- **The folder-map trim is proven lossless, not proven neutral on output.** No
  path is lost, so the model receives the same information in a denser form. A
  regenerated run would be needed to confirm the model reads the grouped form as
  well as the flat one.
- **Sample sizes are small.** Most per-agent averages rest on n=2–4 successful
  calls, since 75% of attempts were rate-limited. Directionally the signals are
  large (a 45× latency gap, a 4-token-from-the-cap truncation) but the precise
  figures should not be treated as stable.

---

## 6. Ponytail decisions

Three mandated design reviews; all three rejected the planned approach on
measured evidence.

1. **Rate limiter.** Planned: new per-provider RPM token bucket. Shipped: kept
   the existing per-model pacer, added daily budget tracking. Provider refusals
   name a model, so per-provider is the *less* accurate grain, and RPM was never
   the limit that bit — tokens-per-day was.
2. **Cache key.** Planned: `hash(model + messages + max_tokens + temperature)`.
   Shipped: `hash(agent_type + messages + max_tokens)`. Model excluded because
   the fallback chain varies it, which would defeat restart-from-stage;
   temperature excluded as a hardcoded constant.
3. **Fast mode.** Planned: halve all per-agent budgets. Shipped: scale only
   agents with measured headroom, and skip the LLM repair pass. Blanket halving
   either does nothing or truncates.
</content>
</invoke>
