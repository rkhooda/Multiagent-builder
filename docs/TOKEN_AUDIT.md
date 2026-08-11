# Token Audit — Findings (BEFORE any change)

**Dated 2026-08-10.** Everything below the "Generated report" line is emitted by
`backend/scripts/audit_tokens.py` (read-only over `metrics.db` + the LangGraph
checkpoints; re-run any time). This header is the interpretation, written once
against the 2026-08-10 baseline. Every optimisation task cites a line from this
document; anything it cannot cite does not get built.

## Verdicts on the three hypotheses

**H1 — "cache is not used consistently during iteration": PARTLY TRUE, wrong
mechanism.** Real-run cache hit rate is **1% (3 hits / 165 misses)** vs 267
hits on the suite/harness rows — the mechanism works, production traffic never
earns from it. But the assumed failure ("a re-run regenerates
research/requirements/architecture") is structurally impossible for
restart-from-stage: the checkpoint **keeps** upstream artifacts and the graph
never re-enters those nodes (section 5c — no upstream stage has ever run twice
on a real project). The one real multi-day project (87f7061b) was a mid-phase
**resume**, and its misses were legitimate: day-1 quota starvation left failure
stubs in `generated_files`, so day-2 contexts embedded different dependency
content — the input genuinely changed. The cache's restart-replay path is
proven by test (`test_llm_cache.py`, restart e2e) but has never fired in
production because no real restart has completed.

**H2 — "fast_mode is not applied on iteration runs": TRUE, trivially.** Fast
mode was **never enabled on any recorded real run** (section 6 — it is opt-in
at project creation and was never selected). Note its token effect is bounded
by design: it scales only the four coder/devops ceilings down to the 1000
floor, and measured coder completions average 307–560 tokens — far below
either ceiling — so fast mode saves wall-clock (skips review stages), not
meaningful tokens. It is not the lever this audit was looking for.

**H3 — "Groq's budget goes to prompt tokens across many small coder calls":
TRUE, and it is the headline.** Groq spend is **90% prompt-side** (330,772
prompt vs 33,929 completion tokens, section 2). The three coder-shaped agents
(frontend_code 159.7k, frontend_review 85.3k, backend_code 82.9k prompt
tokens) are ~99% of all Groq tokens. Each real run-day consumed **88–96k Groq
tokens — essentially the whole 100k daily allowance** (section 3), which is
exactly why no two-arm comparison has ever fit in a day.

## Where each coder call's prompt actually goes

Average successful frontend_code call: **2,523 prompt tokens** = repeated
system prompt (~940 tok measured; the file is 1,087 tok by chars/4) + built
user context (avg 1,579 tok from checkpoint logs). Average backend_code call:
**2,831 prompt tokens**, of which the system prompt is ~1,700 tok — **~60% of
every backend call is the same fixed preamble, re-sent per file.**

The per-file context builder is NOT the problem: zero trim events, max context
2,823 tok against the 4K budget, no >16k-char calls on the frontend (one
16,665-char backend call, within tolerance). The degradation path has never
fired on a real run. The runaway is the **fixed payload repeated across ~100+
calls per run**, times the call count itself (the frozen TodoSimple plan has
96 tasks, 11 of them `__init__.py` markers — `docs/CEILING_AUDIT.md`,
fragmentation note).

## Defect checks (both answered by experiment)

1. **Cache key vs system prompt: NO DEFECT.** `make_key` hashes the full
   `messages` list, and every agent sends its system prompt as `messages[0]`.
   Proven by experiment in section 7a: editing one line of the real backend
   coder prompt changes the key. Prompt tuning has never been silently
   defeated by the cache; the A/B harness stands.
2. **Fast-mode ceilings vs measured requirements: NO DEFECT.** Every agent's
   fast-mode effective ceiling sits above its measured worst complete output
   (section 7b), already pinned in both profiles by
   `test_token_budgets.py::test_ceilings_cover_measured_requirements`. The QA
   starvation shape cannot currently recur under fast mode.

## What this licenses (and what it does not)

- **Task 2 (dominant payload)** acts on the repeated fixed preamble of the
  coder calls — system prompt share and call count — NOT on the dependency
  content (which is small here and load-bearing for import correctness) and
  NOT on the context builder's budget (already respected with headroom).
  Native provider prompt caching must be verified against live provider docs
  before trimming content (routing: coders' primary is groq llama-3.3-70b).
- **Task 3 (call count)** is licensed by the fragmentation numbers: 11
  `__init__.py` tasks × ~2.6k prompt tokens ≈ 29k tokens/run (~13% of a
  replay arm) for near-empty files.
- **Task 1 (cache)** is corrective polish, not the headline: the key is
  correct and the bypasses are tested. What remains: cache faults degrade
  silently (print-only — not counted in `degraded_events`), and no real
  restart has ever validated the replay path end-to-end outside the suite.
- **Task 4 (budgets)**: no starvation defect exists; the remaining work is
  keeping the pin green and not inventing cuts the measurements don't ask for.

## Task 2 decision record (2026-08-10) — dominant payload, and why no content was cut

The dominant payload is the fixed preamble repeated on every coder call
(system prompt ~940 tok frontend / ~1,700 tok backend, 37–60% of each call's
prompt), times the call count. The three remedies, priced:

1. **Native provider prompt caching — VERIFIED UNAVAILABLE on the scarce
   pool.** Groq supports it only on GPT-OSS models (cached tokens exempt from
   rate limits there — see `PROVIDERS.md`, dated 2026-08-10); the coders'
   primary llama-3.3-70b is not supported. Gemini 2.5 Flash implicit caching
   needs a 2048-token common prefix; today's task-first message shape never
   reaches it. **No action possible without a routing change**, which is out
   of scope here (routing drift has invalidated premises twice).
2. **Trim the coder system prompts** (backend −~850 tok/call ≈ 25–34k
   tok/run; frontend −~300 tok/call ≈ 18k tok/run). NOT DONE: the prompt
   bulk is worked examples that encode measured defect classes (Pydantic v2
   traps, `app.` import convention), and a prompt change's quality effect
   cannot be verified offline — golden `--rescore` re-scores *saved*
   outputs; scoring *new* generations needs live quota, the very resource
   that is blocked. Shipping a blind trim would trade a measured quality
   basis for an unmeasured saving — the exact failure mode this project has
   twice paid for. **Deferred with review-by 2026-08-24**: if Task 3's
   call-count reduction makes a single-agent A/B affordable (~90–110k
   tokens, one day's Groq), run it then.
3. **Trim per-call context blocks** (tech-stack block ~40 tok, API section
   for non-fetching leaf components ~450 tok). NOT DONE, same reason — the
   API block is the Day 18 anti-hallucination anchor; cutting it for
   "obviously non-fetching" files is a heuristic whose failure mode is the
   exact defect it fixed. The ≤4K context budget is already enforced,
   logged, and has never been violated on a real run (section 4b).

The measurable, quality-safe reduction is **whole-call elimination**
(Task 3): a call not made saves its entire 2.6k-token prompt, needs no
quality proof beyond the emitted files being deterministic, and the
fragmentation numbers already price it.

## Task 3 decision record (2026-08-10) — call-count reduction

**Shipped: deterministic `__init__.py` package markers** (default on;
`LLM_INIT_FILES=true` restores the old path). The frozen TodoSimple plan
spends 11 of its 96 tasks on `__init__.py` files — at the measured ~2.6k
prompt tokens per backend/database call that is **~29k prompt tokens per cold
run (~30% of a day's Groq allowance)** for near-empty files. A package marker
is not judgement work: like `main.py`, its correct content is derivable from
what was actually generated, and the derived form re-exports every class that
really exists — covering `from app.models import Note`, the one local import
shape the AST fixer cannot rewrite — while an LLM marker can hallucinate
re-exports that crash at startup. Quality effect measured within budget:
10-test offline suite asserts the rendered markers parse, re-export only real
names, survive broken/stub siblings, and leave every non-`__init__` task
untouched. Only other `__init__` tasks depend on `__init__` tasks in the
frozen plan, and `run_phase` drops out-of-set dependency edges, so scheduling
is unaffected.

**Not built: batching several trivial files into one call.** Ponytail #3
conclusion: a multi-file response breaks the one-file-per-call machinery
(`process_generated_file`, `fix_imports`, the single-owner assertion, per-file
QA offer) unless the response is split before processing — new parsing surface
with its own failure modes — and whether a model writing four files in one
response does each worse is exactly the kind of quality question that needs a
live A/B this budget cannot fund. The remaining batchable clusters after the
`__init__` fix (config stubs, the 7-file UI kit) are worth ~15–25 calls ≈
40–65k prompt tokens/run — real, but priced and deferred rather than shipped
unproven. **Review-by 2026-09-10**, or sooner if a planning-prompt change
regroups tasks at the source (the fragmentation is planner-made: its
one-task-per-file rule maps plan granularity 1:1 onto filesystem granularity).

## Suites baseline (2026-08-10, before any change)

- Offline gate: **23/23 green** (includes QA-stream, crafted-breakage
  `test_validation.py`, ceiling pins `test_token_budgets.py`, cache suite).
  `test_build_verify.py` and `test_sandbox_hostile.py` self-skipped (Docker
  daemon unavailable on this machine).
- Golden fixtures: **7/7 pass** (`ab_prompt_test.py --rescore`).

---

# Generated report

# Token Consumption Audit

Generated 2026-08-10 18:05Z by `backend/scripts/audit_tokens.py` (read-only; re-run any time).

## Scope

- 1,720 attempt rows total; **758 belong to real pipeline runs** (UUID project ids: 2901fb46, 87f7061b, 972e7066, a40f5895).
- The remaining rows are suites/harnesses/dev scripts (35 ids, e.g. (null), cachetest, day23-baseline, e2e, gen-express-v2, gen-node-express-api…) — excluded from every 'real run' number below, reported separately where relevant.

## 1. Prompt vs completion split per agent (real runs, successful calls)

| agent | calls | prompt tok | completion tok | avg prompt | avg compl | prompt share of agent |
|---|---|---|---|---|---|---|
| frontend_code | 101 | 254,810 | 56,581 | 2,523.0 | 560.0 | 81% |
| frontend_review | 48 | 165,696 | 19,291 | 3,452.0 | 402.0 | 89% |
| planning | 4 | 55,842 | 84,560 | 13,961.0 | 21,140.0 | 39% |
| backend_code | 31 | 87,756 | 12,869 | 2,831.0 | 415.0 | 87% |
| qa | 16 | 49,694 | 30,322 | 3,106.0 | 1,895.0 | 62% |
| architecture | 3 | 12,897 | 31,063 | 4,299.0 | 10,354.0 | 29% |
| requirements | 3 | 8,165 | 10,991 | 2,722.0 | 3,664.0 | 42% |
| research | 2 | 5,292 | 6,421 | 2,646.0 | 3,211.0 | 45% |
| database | 2 | 4,743 | 3,395 | 2,372.0 | 1,698.0 | 58% |
| **total** | 210 | **644,895** | **255,493** | | | **71%** |

## 2. Provider view — where the scarce pool goes (all attempts, incl. failed)

| provider | attempts | prompt tok | completion tok | prompt share |
|---|---|---|---|---|
| gemini | 323 | 311,542 | 218,939 | 58% |
| groq | 404 | 330,772 | 33,929 | 90% |
| openrouter | 1 | 2,581 | 2,625 | 49% |
| nvidia_nim | 27 | 0 | 0 | 0% |

**Groq (100k/day, scarce) by agent:**

| agent | attempts | prompt tok | completion tok | prompt share |
|---|---|---|---|---|
| frontend_code | 187 | 159,699 | 17,162 | 90% |
| backend_code | 101 | 82,859 | 12,035 | 87% |
| frontend_review | 38 | 85,325 | 2,679 | 96% |
| research | 1 | 2,889 | 2,053 | 58% |
| architecture | 8 | 0 | 0 | 0% |
| database | 48 | 0 | 0 | 0% |
| planning | 2 | 0 | 0 | 0% |
| qa | 19 | 0 | 0 | 0% |

## 3. Per-run cost (per real project, per UTC day — a day is the session proxy)

| project | day | attempts | ok | cache hits | prompt tok | completion tok | total | groq share |
|---|---|---|---|---|---|---|---|---|
| 2901fb46 | 2026-07-20 | 233 | 45 | 0 | 143,990 | 85,166 | 229,156 | 89,787 |
| 2901fb46 | 2026-08-01 | 125 | 48 | 0 | 165,696 | 19,291 | 184,987 | 88,004 |
| 87f7061b | 2026-08-05 | 258 | 59 | 2 | 193,780 | 100,300 | 294,080 | 91,067 |
| 87f7061b | 2026-08-06 | 138 | 60 | 1 | 138,848 | 48,111 | 186,959 | 95,843 |
| 972e7066 | 2026-07-20 | 3 | 0 | 0 | 0 | 0 | 0 | 0 |
| a40f5895 | 2026-08-05 | 1 | 1 | 0 | 2,581 | 2,625 | 5,206 | 0 |

## 4. Coder context sizes

### 4a. context_chars from metrics (full messages incl. system prompt)

| agent | ok calls | avg chars | p95 chars | max chars | >16k chars |
|---|---|---|---|---|---|
| frontend_code | 101 | 9,824 | 12,016 | 14,706 | 0 |
| backend_code | 33 | 11,389 | 13,048 | 16,665 | 1 |
| database | 3 | 9,137 | 10,131 | 10,131 | 0 |
| devops | 0 | — | — | — | — |

### 4b. context_builder log lines from checkpoints (user context only, ≤4k-token budget)

| project | files logged | avg tok | max tok | over 4k budget | trim events |
|---|---|---|---|---|---|
| 2901fb46 | 34 | 1,580 | 2,197 | 0 | 0 |
| 87f7061b | 80 | 1,579 | 2,823 | 0 | 0 |
| 972e7066 | 0 | — | — | — | — |
| a40f5895 | 0 | — | — | — | — |

### 4c. System prompt sizes (sent verbatim on EVERY call of that agent)

| prompt file | chars | ~tokens (chars/4) |
|---|---|---|
| architecture_agent.md | 6,154 | 1,538 |
| backend_coder_agent.md | 6,890 | 1,722 |
| database_agent.md | 5,029 | 1,257 |
| devops_agent.md | 3,916 | 979 |
| express_coder_agent.md | 7,259 | 1,814 |
| express_devops_agent.md | 3,054 | 763 |
| frontend_coder_agent.md | 4,350 | 1,087 |
| frontend_reviewer_agent.md | 5,792 | 1,448 |
| planning_agent.md | 8,934 | 2,233 |
| qa_agent.md | 4,358 | 1,089 |
| requirements_agent.md | 7,151 | 1,787 |
| research_agent.md | 4,959 | 1,239 |
| static_site_coder_agent.md | 8,716 | 2,179 |
| static_site_devops_agent.md | 2,741 | 685 |

## 5. Cache effectiveness

### 5a. Real pipeline runs, by agent

| agent | hits | misses | hit rate |
|---|---|---|---|
| architecture | 0 | 1 | 0% |
| backend_code | 2 | 31 | 6% |
| database | 1 | 2 | 33% |
| frontend_code | 0 | 62 | 0% |
| frontend_review | 0 | 48 | 0% |
| planning | 0 | 2 | 0% |
| qa | 0 | 16 | 0% |
| requirements | 0 | 2 | 0% |
| research | 0 | 1 | 0% |
| **total** | **3** | **165** | **1%** |

Suite/harness rows for contrast: 267 hits / 319 misses — the cache mechanism works under test; real runs are where it is not earning.

### 5b. Re-run case study — the one real project that ran twice

| project | day | agent | hits | misses |
|---|---|---|---|---|
| 87f7061b | 2026-08-05 | architecture | 0 | 1 |
| 87f7061b | 2026-08-05 | backend_code | 1 | 1 |
| 87f7061b | 2026-08-05 | database | 1 | 2 |
| 87f7061b | 2026-08-05 | frontend_code | 0 | 49 |
| 87f7061b | 2026-08-05 | planning | 0 | 2 |
| 87f7061b | 2026-08-05 | requirements | 0 | 1 |
| 87f7061b | 2026-08-05 | research | 0 | 1 |
| 87f7061b | 2026-08-06 | backend_code | 1 | 30 |
| 87f7061b | 2026-08-06 | frontend_code | 0 | 13 |

### 5c. What the multi-day runs actually were (stage_history from checkpoints)

**2901fb46:**
- 2026-07-20T13:17 stage=research attempt=1 trigger=initial
- 2026-07-20T13:17 stage=requirements attempt=1 trigger=initial
- 2026-07-20T13:19 stage=architecture attempt=1 trigger=initial
- 2026-07-20T13:28 stage=planning attempt=1 trigger=initial
- 2026-07-20T13:47 stage=frontend_code attempt=1 trigger=partial
- 2026-07-20T13:52 stage=database attempt=1 trigger=initial

**87f7061b:**
- 2026-08-05T06:01 stage=research attempt=1 trigger=initial
- 2026-08-05T06:01 stage=requirements attempt=1 trigger=initial
- 2026-08-05T06:32 stage=architecture attempt=1 trigger=initial
- 2026-08-05T06:44 stage=planning attempt=1 trigger=initial
- 2026-08-05T08:05 stage=frontend_code attempt=1 trigger=partial
- 2026-08-05T08:37 stage=database attempt=1 trigger=initial
- 2026-08-06T09:06 stage=backend_code attempt=1 trigger=initial
- 2026-08-06T09:23 stage=validation attempt=1 trigger=initial

**972e7066:**
- 2026-07-20T12:36 stage=architecture attempt=1 trigger=restart

**a40f5895:**
- 2026-08-05T05:58 stage=research attempt=1 trigger=initial
- 2026-08-05T06:00 stage=requirements attempt=1 trigger=initial

## 6. Fast mode application

| project | fast_mode | metrics attempts |
|---|---|---|
| 2901fb46 | None | 358 |
| 87f7061b | False | 396 |
| 972e7066 | None | 3 |
| a40f5895 | False | 1 |

**Fast mode was NEVER used on any recorded real run.**

## 7. Defect checks

### 7a. Cache key vs system-prompt content (experiment, not code reading)

- key(real backend coder prompt): `c8d47f24f0fc7de0…`
- key(same prompt + one edited line): `b3099191ce2a062a…`
- **PASS — a prompt edit changes the key; no stale hit possible.** The key hashes the full `messages` list (`llm_cache.make_key`), and every agent sends its system prompt as `messages[0]` — so system-prompt content is inside the hash.

### 7b. Fast-mode ceilings vs measured output requirements

| agent | call-site ceiling | fast-mode effective | measured requirement | headroom (fast) | scaled? |
|---|---|---|---|---|---|
| research | 4500 | 4500 | 4368 | 132 | no |
| requirements | 4500 | 4500 | 4496 | 4 | no |
| architecture | 12000 | 12000 | 11996 | 4 | no |
| planning | 32000 | 32000 | 26894 | 5106 | no |
| frontend_code | 1500 | 1000 | 829 | 171 | yes |
| frontend_review | 4000 | 4000 | 3075 | 925 | no |
| backend_code | 1500 | 1000 | 96 | 904 | yes |
| database | 2500 | 1250 | 722 | 528 | yes |
| devops | 2000 | 1000 | 64 | 936 | yes |
| qa | 6000 | 6000 | 4949 | 1051 | no |

**PASS — no agent starves under fast mode** (pinned by `test_token_budgets.py::test_ceilings_cover_measured_requirements`, which asserts both profiles).

