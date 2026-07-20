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
| _pending_ | simple | | | | | | | | | |
| _pending_ | medium | | | | | | | | | |
| _pending_ | complex | | | | | | | | | |

Rows are filled immediately after each run (or at its pause point), never
batched to the end — a rate-limit pause must not strand uncaptured data.

### Reference point (pre-Day-18 baseline)

Scored to sanity-check the rubric against a known-weak project, not as part of
today's comparison:

| Project | Planned | Generated | Missing | % usable |
|---|---|---|---|---|
| `113cf67c` NotesTags (Day 15 era) | 77 | 12 | 66 | 14.1% |

The dominant defect class there is `missing` — 66 planned files never generated.
A missing file is worse than a broken one, and only the plan reveals it; this is
why `planned` is a column rather than a footnote.

---

## Degradation analysis

_Pending runs._

## Known limits (free-model capability ceiling)

_Pending runs. Recorded honestly — a documented limit is a deliverable; a
chased-and-unfixed ceiling is wasted quota._

## Fixes applied (before / after)

_Pending analysis._

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
