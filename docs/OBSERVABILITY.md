# Observability

Two independent backends, added Day 23.

| | Local metrics store | LangSmith |
|---|---|---|
| Where | `backend/metrics.db` (gitignored) | smith.langchain.com |
| Needs a key | no | yes |
| Survives | forever, queryable by your own code | vendor-hosted |
| Good for | token/latency analytics, the in-app panel, Day 26 | interactive prompt debugging |

Neither can break a run. Both are off when `OBSERVABILITY_ENABLED=false`.

---

## What is captured

One row per **attempt**, not per call. A call that times out on the primary and
succeeds on the fallback writes two rows, so the failed attempt keeps its
latency and only the successful one carries tokens. Averaging over
`outcome='ok'` gives per-call cost; averaging over everything gives true spend.

Every row: `project_id, agent, model, attempt, outcome, prompt_tokens,
completion_tokens, total_tokens, latency_ms, context_chars, label, created_at`.

`label` is the target filepath for coder and repair calls, NULL elsewhere.
It is passed explicitly down the call chain — the parallel coder workers run in
a thread pool, so thread-locals and ambient context cannot identify which file
a call belongs to.

All of this is captured in **one place**: `_log_attempt` in
`app/llm_router.py`. Do not add tracing calls inside individual agents — every
model call already passes through this function exactly once.

---

## Local metrics: querying

```python
from app.observability import metrics_store as ms

ms.run_summary(project_id)            # UI rollup: tokens, latency, per-agent
ms.avg_tokens_by_agent()              # Day 26's headline question
ms.latency_percentiles_by_agent()     # p50 / p95 / max per agent
ms.slowest_agents(2)
```

Or straight SQL — it is a plain SQLite file:

```sh
sqlite3 backend/metrics.db \
  "SELECT agent, COUNT(*), ROUND(AVG(prompt_tokens)) FROM agent_runs
   WHERE outcome='ok' GROUP BY agent ORDER BY 3 DESC;"
```

Attempts whose provider omitted usage are stored as `NULL`, **not** `0`, so
they are excluded from averages rather than dragging them down. `run_summary`
surfaces the count as `missing_usage` and the UI panel says so explicitly.

Projects from before Day 23 have no rows: `has_metrics` is `false` and the UI
explains rather than showing zeros.

### Deletion

Metrics are **not** deleted with a project. Day 24's delete endpoint should
call `ms.delete_project_metrics(project_id)` explicitly if that is the desired
policy — the hook exists and is tested. Keeping them is the default so the
Day 26 evidence base survives project cleanup.

---

## LangSmith

### Status: unverified — no API key has ever been configured

`LANGCHAIN_API_KEY` was the literal placeholder `your_key_here` from Day 1
through Day 23, and `LANGCHAIN_TRACING_V2` was `false`. **No trace has ever
been emitted from this project, and the dashboard has never been opened.** The
emit path below is implemented and its failure behaviour is tested, but the
rendered dashboard is unverified. Treat this section as untested until someone
adds a real key and confirms.

### Enabling

```sh
# backend/.env
LANGCHAIN_API_KEY=ls__your_real_key   # from smith.langchain.com
LANGCHAIN_TRACING_V2=true
LANGCHAIN_PROJECT=multiagent-builder
```

Tracing stays off while the key is the placeholder, so an unconfigured checkout
never emits a 403 on every call. On startup the backend logs exactly one line
stating which backends are live.

### What LangSmith does and does not auto-capture here

This is the trap the PDF's "no code changes needed" claim hides:

> **LangGraph auto-tracing covers LangChain-native LLM calls. This project calls
> LiteLLM's `completion()` directly, so node transitions would trace but the
> actual prompts, responses and tokens would NOT.**

That is why `completion()` is wrapped in `@traceable(run_type="llm")` rather
than relying on the LangGraph integration. Attempts are tagged
`agent:<name>`, `model:<name>`, `attempt:<n>`, `project:<id>` and carry the same
metadata as the metrics row.

### The prompt-debugging workflow

This is the point of LangSmith — when Day 25's integration runs surface a bad
agent output, this is how you find out why:

1. Open the `multiagent-builder` project at smith.langchain.com.
2. Filter to one run: `metadata.project_id = <id>`.
3. Filter to the suspect agent: tag `agent:architecture`.
4. Open the attempt and read the **exact** system + user message the agent
   received — including the injected context the context builder assembled,
   which is usually where the real defect is.
5. For failures, filter `outcome` via the tag list — `attempt:2` rows are
   retries, and a `timeout`/`rate_limit` row next to a successful one shows a
   fallover rather than a prompt problem.

To find the slowest call, sort by latency in the run table. Cross-check against
`ms.latency_percentiles_by_agent()` — the local store is authoritative, since it
records even when tracing is off.

---

## Overhead

Measured over 60 steady-state calls (SDK init excluded):

| | median | p95 |
|---|---|---|
| tracing on | 1.49 ms | 1.53 ms |
| tracing off | 1.28 ms | 1.30 ms |

**~0.21 ms added per call**, against LLM calls of 2–30 s. No batching or async
queue is warranted; the local SQLite write stays per-call, which also means
nothing is lost if the process dies mid-phase.

## Failure isolation

- A bad/missing LangSmith key logs a `403` from the SDK's background uploader
  and **the traced function returns normally** — verified by direct probe.
- A metrics write failure prints `[Metrics] write failed (run unaffected)` and
  returns; it never propagates into the agent or the Day 17 error boundary.
- A metrics query failure degrades to `[]` / zeroed rollups.
- The UI renders "metrics unavailable, the run itself is unaffected" rather than
  an error state.
