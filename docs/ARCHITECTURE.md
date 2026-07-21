# Architecture

How the system works, and how to change it. For *using* it, read
[USAGE.md](USAGE.md) instead.

---

## The core idea: software drives, LLMs fill in

The pipeline is a deterministic state machine. Control flow, ordering,
retries, validation, budgets and persistence are all plain Python; the LLM is
called only to produce *content* for one clearly-bounded step at a time.

This matters because it decides where failures land. A model that returns
nonsense produces one bad document behind one human gate — it cannot
mis-route the pipeline, skip a stage, or silently corrupt state. Nothing in
the graph is decided by a model.

The second idea: **a human approves before anything builds on the result.**
Four gates split the run into segments where a mistake is cheap to fix,
because nothing downstream exists yet.

## The pipeline

A LangGraph `StateGraph` (`backend/app/graph/pipeline.py`):

```
START
  └─ research ─ requirements ─┤GATE 1├─ architecture ─┤GATE 2├─ planning ─┤GATE 3├─┐
                                                                                   │
   ┌───────────────────────────────────────────────────────────────────────────────┘
   └─ frontend_code ─ database ─ backend_code ─ validation ─ qa ─ devops ─┤GATE 4├─ END
```

Two orderings are deliberate and load-bearing:

- **`database` runs between the two coders.** The backend coder writes routers
  that import ORM models, so those model files must already exist on disk and
  in state when it runs (Day 19 reorder).
- **`validation` runs after the last file-producing agent and before QA.** It
  is a batch syntax/import/artifact check in plain Python, so no broken file
  reaches the QA agent or the download ZIP.

There is also a `cancelled` terminal node — every gate can route to it.

### Nodes, agents and the gates

| Node | Module | Produces |
|---|---|---|
| `research` | `agents/research_agent.py` | Research report |
| `requirements` | `agents/requirements_agent.py` | Requirements doc |
| `architecture` | `agents/architecture_agent.py` | Architecture doc + tech stack |
| `planning` | `agents/planning_agent.py` | Task plan (JSON) + file list |
| `frontend_code` | `agents/frontend_coder_agent.py` | Frontend files |
| `database` | `agents/database_agent.py` | Schema / ORM models |
| `backend_code` | `agents/backend_coder_agent.py` | Backend files |
| `validation` | `agents/validation_pass.py` | Validation report (no LLM) |
| `qa` | `agents/qa_agent.py` | QA report, issue count |
| `devops` | `agents/devops_agent.py` | Dockerfiles, CI, README |

Gates are empty pass-through functions. The pause is `interrupt_before` on the
compiled graph — LangGraph stops *before* entering the node, checkpoints, and
returns control. Resuming means writing a decision into state and streaming
the graph again from the checkpoint.

Routing out of a gate is a conditional edge driven by `GATE_ROUTES`, a
decision → target map. Gates 1–3 accept `approve`, `edit` (regenerate this
gate's own document) and `back` (regenerate the *previous* stage, then flow
forward and re-pause at the **same** gate). Gate 4 accepts approve/reject. The
resume endpoint derives the allowed decisions from `GATE_ROUTES` rather than
keeping a second list in sync.

Any `edit` or `back` runs `invalidate_downstream`: everything produced after
the re-run target is snapshotted into `previous_versions` (last snapshot only)
and cleared, so a stale architecture can never survive a requirements rewrite.

## State

One `TypedDict` — `backend/app/graph/state.py` — is threaded through every
node. **Every agent takes the full state and returns only the keys it
changed**; LangGraph merges partial updates.

Two rules that are easy to get wrong:

- **Always re-return `log` and `errors`, appending rather than replacing.**
  Otherwise earlier agents' traces vanish.
- **`current_stage` names the *next* stage, not the one that just finished.**

Bookkeeping that agents must *not* do themselves lives in the `stage_node`
wrapper: snapshotting, clearing feedback fields, and recording `stage_history`.
Feedback detection is one shared helper, `regeneration_target` in
`agents/utils.py`.

### Two databases, both of which matter

| Store | Contains | Authority |
|---|---|---|
| `projects.db` via `SqliteSaver` | The full `ProjectState`, keyed by `thread_id = project_id` | **Authoritative** |
| `projects` table (`core/database.py`) | Status/stage mirror for the project list | Fast reads only |

They are separate on purpose, and they must be kept in sync when status
semantics change. When debugging "the UI shows the wrong stage", check which
one you are reading.

Generated files live on disk under `outputs/{project_id}/` (mounted to
`./data/outputs` in Docker) *and* in `generated_files` in state. Gate 4's file
browser, ZIP download and per-file fixes all read from disk.

## The LLM layer

`backend/app/llm_router.py` is the only place that talks to a model.
`call_llm(messages, agent_type, ...)` resolves a per-agent chain and walks it:

```
primary → fallback → local (Ollama, when detected)
```

`MODELS` maps each agent type to a `(primary, fallback)` pair, deliberately
across *different providers* so one provider's rate limit stays recoverable.

Error handling is classified rather than uniform: one same-model retry on 429
or timeout, immediate failover on auth errors (retrying a bad key is pure
waste), and failover on unclassified errors such as 404s. Chain exhausted
raises a typed `LLMError` subclass.

Layered on top, all in the same module: per-agent output token caps, per-agent
timeouts, a per-provider minimum call interval, per-provider daily token
budgets, and a content-addressed response cache in `metrics.db`.

`LLM_MODE` reorders the chain — `auto` (default), `cloud-only` (never degrade;
pause instead), `prefer-local` (free iteration first).

> **Free model slugs vanish without notice.** This has already broken the
> pipeline twice. If an agent starts 404-ing or silently falling back, check
> `MODELS` against OpenRouter's live `/api/v1/models` list *before* assuming a
> logic bug. See `build-journal/failures.md`.

## Live updates

`run_graph_background` (`routers/projects.py`) consumes `graph.stream()`,
broadcasts events, and drives status in SQLite. Clients subscribe over the
WebSocket endpoint in `routers/ws.py`; `core/connection_manager.py` does
per-project broadcast and buffers the last 10 events for reconnects.

Two conventions the UI depends on:

- **`_agent_event: True`** — any agent that broadcasts its own
  `agent_complete` event must set this in its final return dict, so the
  generic fallback broadcast is suppressed. Omitting it produces a duplicate
  card. Early-exit returns that broadcast nothing must *not* set it.
- **The frontend dedups on the full JSON payload minus timestamp**, not a
  field subset — lifecycle events like `gate_reached` carry no `agent`/`stage`
  field, so a narrower comparison silently drops all but the first.

Because the WS buffer only holds 10 events, `ProjectDetailPage.jsx`
reconstructs the event list from `state.log` on page load.

## Extending it

### Adding or changing an agent

1. Write `backend/app/agents/your_agent.py`. Take the full state, return only
   changed keys, append to `log`/`errors`, set `current_stage` to the **next**
   stage.
2. Add a system prompt in `prompts/`. Prompts load at import time.
3. Register the node **and** its edges in `pipeline.py` — wiring a node without
   updating the routing function is the classic mistake here.
4. Add the agent to `MODELS` in `llm_router.py`.
5. If it writes files, follow the existing pattern: generate one file at a
   time, write with `write_project_file` (handles fence-stripping and header
   comments), broadcast a `file_written` event per file, accumulate into
   `generated_files`. Do not invent a second pattern — `database_agent.py` is
   the reference.

### Changing a prompt

**Never edit `prompts/` without a `PROMPT_CHANGELOG.md` entry and an A/B run.**
The protocol is at the top of that file: attribute the defect to its layer →
write the hypothesis first → change one thing → A/B with
`backend/scripts/ab_prompt_test.py` → keep or revert by the stated rule →
record the verdict either way, including reverts.

This exists because prompt changes feel productive and are frequently neutral
or harmful. [QUALITY_BASELINE.md](QUALITY_BASELINE.md) holds the defect
taxonomy and, crucially, **layer attribution** — most defects that look like
prompt problems are context-builder or planner problems, and editing the
prompt cannot fix those.

After any prompt edit: `python backend/tests/test_prompt_regression.py`
(zero API cost).

### Tests

```bash
cd backend
./venv/bin/python tests/run_all.py          # 15 offline suites, ~60s, no API calls
./venv/bin/python tests/run_all.py --live   # adds live-provider smokes; needs quota
```

Offline suites must never be red. Live suites fail on provider quota rather
than code defects, which is exactly why they are not the default gate.

Two things make fixes cheap to verify without spending quota:

- `backend/scripts/score_project.py` re-scores any persisted project from disk
  plus the checkpoint. **Re-generating to check a fix is the anti-pattern.**
- `FAULT_INJECTION=syntaxerr|truncate|empty` deliberately corrupts agent output
  to exercise the validation and repair paths.

### Observability

Local metrics (`metrics.db`) are always on and need no keys — per-attempt
model, tokens, latency, outcome and `context_chars`. LangSmith tracing is
optional. See [OBSERVABILITY.md](OBSERVABILITY.md).

## Where things live

```
backend/app/graph/pipeline.py   Node wiring, gate routing, interrupts, checkpointing
backend/app/graph/state.py      ProjectState — the contract every agent shares
backend/app/agents/             One module per stage
backend/app/llm_router.py       Provider chain, retries, budgets, limits, cache
backend/app/routers/projects.py REST API + the background graph runner
backend/app/routers/ws.py       WebSocket event stream
backend/app/core/               Connection manager, status database
backend/app/utils/code_cleaner.py  Fence stripping — the only copy
prompts/                        One system prompt per agent
frontend/src/hooks/useProjectStream.js   WS client, dedup, status derivation
frontend/src/pages/ProjectDetailPage.jsx Gate detection, event feed, log replay
frontend/src/components/gates/  Per-gate approval UI
```

## History

The 30-day build log is in [build-journal/](build-journal/) — `failures.md` is
an append-only record of what broke in every full-pipeline test, with root
causes. It is the fastest way to find out whether a failure you are seeing is
already known.
