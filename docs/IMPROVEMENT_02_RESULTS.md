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

## Task 6 — Measurement

*(to be filled after implementation; per-arm cost estimate must be written
BEFORE the runs)*

## Verdict

*(KEEP / REVERT / UNPROVEN — recorded after measurement)*
