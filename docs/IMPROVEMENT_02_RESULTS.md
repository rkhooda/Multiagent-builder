# Improvement 02 — Incremental QA Stream: Measurements & Verdict

Goal: turn QA from a serial tail (start only after the last file is written)
into a concurrent stream (review batches as files are committed), holding QA
call count and report quality flat. The number this change exists to compress
is the **generation-end → QA-end span**.

All numbers below are offline reads from `backend/metrics.db` and the LangGraph
checkpoints unless marked as a live call.

## Task 0 — Pre-flight verification (2026-08-03)

### Test gate before any change

- `tests/run_all.py` (offline): **18/18 suites green**.
- `ab_prompt_test.py --rescore tests/fixtures/prompt_tuning/golden`: **7/7 pass**, 0 API calls.

### Provider map verified — the brief's premise was stale (third time)

The brief stated QA runs "DeepSeek R1 via OpenRouter". **False.** The verified
map (now recorded in [PROVIDERS.md](PROVIDERS.md)): QA runs
**gemini-2.5-flash** primary / groq fallback since Day 26 (nemotron, the actual
reasoning model that held the slot, was demoted for ignoring max_tokens). The
coders run groq primary / gemini fallback. **QA and the coders draw on
different primary pools**, so the contention problem ponytail #2 was posed
about does not exist at the primary tier — see the ponytail #2 record in the
streaming commit.

### QA output ceiling verified empirically (live calls, 2026-08-03)

5 real QA batches over real coder outputs (the frozen golden fixtures), current
prompt path, `project_id=improvement02-ceiling` in metrics.db:

| Call | max_tokens | Outcome | completion_tokens (reasoning) |
|---|---|---|---|
| batch 1, gemini | 3000 | ok | 2,121 |
| batch 2, gemini | 3000 | **error → silent groq failover** | — |
| batch 3, gemini | 3000 | **error → silent groq failover** | — |
| batch 3 (direct diag) | 3000 | "ok" but answer starved | 2,744 (**2,740 reasoning, 4 text**) |
| batch 3 (direct diag) | 6000 | ok | 2,506 (2,427 reasoning, 79 text) |
| batch 2, gemini | 6000 | ok | **4,949** |

Findings: gemini-2.5-flash thinks for **2,4xx–2,7xx tokens per 3-file batch
before emitting any answer**. At the old ceiling of 3,000 the answer
intermittently had no room, and the failure mode is NOT a visible truncation —
it is an error followed by a silent failover to groq (the coders' scarce pool).
Worst observed complete response: **4,949 tokens**.

Actions taken: `QA_MAX_TOKENS = 6000` (~1.2× worst observed), locked by
`test_token_budgets.test_qa_ceiling_covers_measured_requirement` (asserts
≥ 4,949); `qa` removed from `FAST_MODE_SCALABLE` (halving 6,000 would starve
the answer again, exactly like architecture/requirements at their walls).

### Persisted-data inventory — what comparisons the data actually supports

Metrics.db (started Day 23) per-project coverage of the generation+QA phases:

| Project | Reached | Usable for |
|---|---|---|
| `day23-baseline` | QA ran (2 ok calls) but on the OLD routing (nemotron 354s + gemini 8s); frontend partial (4 files), no database/backend | Old-routing serial-tail illustration only |
| `2901fb46` (TodoSimple) | Gate-3-approved 96-task plan, 47 files committed, halted in backend_code by quota starvation; **no QA rows** | **Frozen-plan replay source** for the Task 6 measurement |
| `local-tier-check` | Full pipeline on Ollama with trivial prompts | Nothing (not representative) |
| `14e1209b` (HabitTest) | Gate 4, 15 files, qa_issues=8 (6 critical) | Issue-count sanity reference only — predates metrics.db |
| everything else | Never reached generation with ok calls | Nothing |

**Plainly: no persisted project supports a current-routing before/after
comparison of the generation-end → QA-end span.** The last improvement's plan
assumed a medium-complexity baseline existed; it did not, and it still does
not. The control arm of the Task 6 phase-scoped replay IS the baseline.

### Baseline numbers (best available, with caveats stated)

- **Old-routing serial tail (day23-baseline, nemotron era):** last generation
  ok call 17:01:29 → QA end 17:07:32 = **363 s generation-end → QA-end**, of
  which the QA calls themselves were 362 s (354 s nemotron + 8 s gemini). QA
  was 100 % serial tail.
- **Current-routing per-batch QA cost (measured today):** gemini ~8–24 s per
  3-file batch, ~2–3k prompt tokens, 2.1–4.9k completion tokens (reasoning
  included). A 15-file project ⇒ 5 batches ⇒ roughly **40–120 s of pure serial
  tail** in batch mode, all of which lands after the last file today.
- **QA calls per run:** ceil(files/3) (+1 auto-fix call per trivial issue
  file). Report shape: `qa_report` markdown (Critical/Warnings/Info) +
  `qa_issues_count`; reference issue mix: HabitTest 8 issues (6 critical /
  2 warnings / 0 info) over 15 files.
- **qa_issues_count on current routing:** no trustworthy sample exists (see
  inventory). The Task 6 control arm must record it.

## Task 6 — Measurement (2026-08-03)

### What shipped, and what the correctness evidence says

- 18 offline correctness tests (`tests/test_qa_stream.py`), zero API calls, all
  green; full offline gate 19/19; golden rescore 7/7. Call-count flatness is
  proven structurally: the stream batches by `_chunk_files`' exact rules over
  commit order, and `test_batch_mode_reviews_end_of_run_with_identical_chunks`
  pins batch composition.
- Shipped default is `QA_MODE=batch` (exact pre-change behaviour), pinned by
  `test_qa_mode_defaults_to_batch`.

### Per-arm cost estimate — written before any run, and it kills the run

The phase-scoped replay (generation phases + QA only, both arms from the one
persisted Gate-3-approved plan, TodoSimple `2901fb46`):

- Plan: **86 file-producing tasks** (47 frontend + 11 database + 28 backend).
- Measured per-file coder cost on groq (ok calls, this very project's history):
  2,637 prompt + 350 completion ≈ **3.0k tokens/file**.
- One arm's generation ≈ 86 × 3.0k ≈ **257k groq tokens** — *before* retries
  and repairs (the original run burned >100k on the frontend phase alone once
  retry storms started). Two arms ≈ **515k groq tokens**.
- Groq's daily allowance — the scarce pool per [PROVIDERS.md](PROVIDERS.md) —
  is **100k tokens/day**. One arm is ~2.6× a full day; the pair is ~5×.
- QA itself is not the constraint: ~29 batches/arm ≈ 160–220k gemini
  tokens/day against a 1M allowance.
- Spreading one arm across 3+ UTC days does not rescue it: the measured
  quantity is a *timing span*, and multi-day rate-limit stalls would dominate
  generation-end → QA-end, making the number meaningless rather than merely
  noisy. Per the brief's rule: say so with the arithmetic and stop — no
  substitute brief, no partial arm.

## Verdict: UNPROVEN (recorded 2026-08-03)

The code ships dark: `QA_MODE=batch` is the default, the 18 correctness tests
stand, and no efficiency claim is made without a number.

**What would settle it** (estimated 515k groq + 440k gemini tokens total):
replay ONLY the code-generation phases + QA from the frozen `2901fb46` plan,
control (`QA_MODE=batch`) and treatment (`QA_MODE=incremental`) as two
single-day runs — which requires either a paid/raised groq tier, or moving the
coders to a pool with ≥300k tokens/day headroom (re-verify PROVIDERS.md
first). Compare: generation-end → QA-end span, total wall-clock, QA call count
(must be flat), QA tokens, `qa_overlap_ratio`, and `qa_issues_count` +
severity mix (watch for the narrower-context quality trap: early batches see
less of the codebase than one end-of-run pass — if issue count drops, report
the trade, not just the speedup).

**Review-by: 2026-08-17.** Measure by then, or delete the incremental code
path — shipped-but-off code rots quietly while everything around it moves.

## Correction & attribution (2026-08-03, ceiling audit)

The QA ceiling fix raised the question of whether the cost figures above were
contaminated by pre-fix QA failover consumption of groq. Re-derived from
metrics.db (see [CEILING_AUDIT.md](CEILING_AUDIT.md)):

- **QA's recorded groq failover spend is 5,043 tokens, all dated 2026-08-03**
  (the two starved batches of the ceiling measurement itself). The per-file
  figure was derived from rows dated 2026-07-20, which QA's failovers
  postdate — so the coder figure was **not** QA-contaminated. Shown rather
  than assumed.
- **The per-file figure was still loose.** Recomputed strictly over the frozen
  plan's own run (32 groq ok `frontend_code` calls, project `2901fb46`,
  2026-07-20): avg 2,574 prompt + 232 completion = **2,806 ≈ 2.8k
  tokens/file**, not 3.0k. Corrected arm estimate: 86 × 2.8k ≈ **241k groq
  tokens/arm** (was 257k); two arms ≈ 483k. Against the 100k/day allowance
  that is still 2.4× / 4.8× — **the UNPROVEN verdict and the settling-run
  requirements are unchanged.**
- **What the audit found instead:** the Improvement-01 reviewer, starving on
  gemini (every attempt truncated, fail-open), silently consumed **88,004
  groq tokens on 2026-08-01** — 26 fallback reviews at ~3.3k prompt each,
  roughly 88 % of a full groq day and nearly equal to the coders' own 89,787
  for the whole frontend phase. The reviewer is default-off and outside the
  replay arms, so the estimate above is unaffected, but Improvement 01's
  observation that "gemini free RPM and groq both refused" during its
  measurement was partly self-inflicted by this defect (now fixed:
  `REVIEW_MAX_TOKENS` 4000).
