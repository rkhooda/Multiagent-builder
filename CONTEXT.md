# multiagent-builder — Project CLAUDE.md

Extends `~/.claude/CLAUDE.md`. Only project-specific context lives here — do not repeat global engineering standards, git rules, or workflow steps.

---

## What This Is

An AI multi-agent pipeline that turns a one-paragraph project brief into a scaffolded, reviewed software project. A LangGraph `StateGraph` runs a fixed sequence of specialized LLM agents (research → requirements → architecture → planning → code generation → QA → devops), pausing at four human approval gates so the user can approve, edit, or reject each stage before the pipeline continues. A React frontend shows the live agent stream over WebSocket and renders each gate's approval UI.

Long-term vision: a reliable "brief in, working scaffolded codebase out" tool — not full autonomous coding, but a structured, human-supervised assembly line where every stage is inspectable and correctable before the next one builds on it.

Intended user: solo/small-team builders who want a fast, reviewed first pass at a new project's docs, architecture, and boilerplate rather than starting from a blank repo.

**Current maturity: active prototype, not production.** The pipeline runs end-to-end and generates real files for research/requirements/architecture/planning/database/devops/qa, but `frontend_code` and `backend_code` are still stub nodes (see Current Development Status).

---

## Architecture

```
backend/app/graph/pipeline.py   — LangGraph StateGraph: node wiring, conditional routing, interrupt_before gates, SqliteSaver checkpointing (backend/projects.db)
backend/app/graph/state.py      — ProjectState TypedDict: the single source of truth threaded through every node
backend/app/agents/*.py         — one module per pipeline stage (research, requirements, architecture, planning, database, devops, qa)
backend/app/llm_router.py       — call_llm(): per-agent-type (primary, fallback) model pair with retry/backoff, via litellm
backend/app/routers/projects.py — REST API + run_graph_background() (streams graph.stream(), broadcasts events, drives status in SQLite)
backend/app/routers/ws.py       — WebSocket endpoint clients subscribe to for live events
backend/app/core/connection_manager.py — per-project broadcast + last-10-events buffer for reconnects
backend/app/core/database.py    — plain SQLite table for project list/status (separate from the LangGraph checkpoint DB)
prompts/*.md                    — system prompts per agent, loaded at import time — the actual "prompt engineering" surface
frontend/src/hooks/useProjectStream.js — WebSocket client, event dedup, status derivation
frontend/src/pages/ProjectDetailPage.jsx — gate detection, live event feed, log-replay-on-reload fallback
frontend/src/components/gates/*  — per-gate approval UI (Gate1Approval, Gate2Approval, TaskPlanViewer, DiffView, etc.)
```

Two persistence layers exist side by side and both matter when debugging state issues:
- **LangGraph checkpoint** (`backend/projects.db`, via `SqliteSaver`) — the authoritative `ProjectState`, keyed by `thread_id = project_id`.
- **Plain SQLite `projects` table** (`app/core/database.py`) — a thin status/stage mirror used for the projects list and fast status reads. Keep both in sync when changing status semantics.

**Pipeline shape** (`backend/app/graph/pipeline.py`): `research → requirements → human_gate_1 → architecture → human_gate_2 → planning → human_gate_3 → frontend_code → database → backend_code → validation → qa → devops → human_gate_4 → END`, plus a `cancelled` terminal node every gate can route to. Two orderings are load-bearing: `database` sits BETWEEN the coders so ORM model files exist when the backend coder writes routers that import them (Day 19 reorder), and the `validation` node (Day 22, no LLM) runs after the last file-producing agent so no broken file reaches QA or the download ZIP. Gate routing is a decision→target map (`GATE_ROUTES` in `pipeline.py`). Gates 1–3 all support `edit` (regenerate the gate's own document) and `back` (regenerate the previous stage, then flow forward and re-pause at the SAME gate — gate 2's back skips gate 1 via `skip_gate_1`, gate 3's skips gate 2 via `replan_after_architecture`); gate 4 supports approve/reject (allowed decisions per gate are derived from `GATE_ROUTES` in the resume endpoint — no separate constant). Every edit/back resume runs `invalidate_downstream` (in `pipeline.py`): everything produced after the re-run target is snapshotted into `previous_versions` (last snapshot only) and cleared. Feedback detection is one shared helper (`regeneration_target` in `agents/utils.py`); snapshotting, feedback-field clearing, and `stage_history` recording live in the `stage_node` wrapper, not in individual agents. `retry_counts` (per target stage, plus `file_fix:{path}` mirrors) drives the soft-cap-of-3 amber warnings in the UI and never resets. A resume against a non-gate `next` node (crash/error mid-run) restarts streaming from the checkpoint. Gate 3 is a full plan editor: the frontend PATCHes the edited `implementation_plan` (validated server-side: task schema, unique ids, in-plan dependencies → 422) before resuming, and cut tasks are archived in `excluded_tasks`. Gate 4 is the final review screen: file browser + QA panel + ZIP download (`GET /files`, `/files/content`, `/download` read from `outputs/{project_id}/` on disk), plus per-file AI fixes (`POST /files/fix`) that run as a standalone `call_llm` + `graph.update_state()` while the gate stays paused — capped at 3 per file via `fix_counts`. A fence-cleanup pass at the end of the devops node (`code_cleaner.clean_project_files`) keeps state, disk, and the ZIP identical; `strip_code_fences` lives in `app/utils/code_cleaner.py` and is the only copy.

**Tech stack**: FastAPI + LangGraph (Python) backend, React 19 + Vite + Tailwind 4 frontend, litellm as the LLM abstraction layer (Gemini / Groq / OpenRouter free-tier models per agent, see `llm_router.py` `MODELS` dict).

---

## Current Development Status

- **All nine agents are real** — research, requirements, architecture, planning, frontend_code, database, backend_code, qa, devops — plus a non-LLM `validation` node. The coder agents were built on Days 18–21 (focused context + tuned few-shot prompts) and generate files in parallel with dependency-aware scheduling (Day 20, `agents/parallel_runner.py`). There are no stub nodes left.
- **The binding constraint is provider quota, not capability.** Free tiers cannot reliably complete one project per day: a single simple project issued 233 LLM attempts against a Gemini allowance of ~20 requests/day and a Groq ceiling of 100k tokens/day. Read `docs/INTEGRATION_RESULTS.md` "Provider ceiling" before attributing a thin result to model quality — under starvation, "never generated" and "stub" defects look exactly like bad code.
- **Free-tier LLM routing is fragile**: OpenRouter's free model slugs disappear/change without notice (already happened once — see `docs/build-journal/failures.md`). If an agent starts silently falling back or erroring with 404s, check `llm_router.py`'s `MODELS` dict against OpenRouter's live `/api/v1/models` list before assuming a logic bug.
- `docs/build-journal/failures.md` is a running log of full-pipeline-test observations (root causes, severity, fixes applied). Read it before re-running a full end-to-end test — it documents known rate-limit and routing failure modes so they aren't re-discovered from scratch.

---

## Project-Specific Engineering Conventions

- **`_agent_event: True`**: any agent that broadcasts its own `agent_complete` WebSocket event (via `manager.broadcast_sync`) must include this flag in its final return dict. `run_graph_background` in `projects.py` uses it to suppress a generic fallback broadcast — omitting it produces a duplicate/mislabeled card in the UI (this was the Day 14 bug). Early-exit returns that don't broadcast (e.g. missing-input guards) should NOT set this flag.
- **`current_stage` names the *next* stage, not the one that just completed.** This convention is easy to misread when adding a new agent — check an existing agent's final return before assuming otherwise.
- **Agent return contract**: every agent function takes the full `ProjectState` dict and returns only the keys it changed (LangGraph merges partial updates). Always thread through and re-return `log` and `errors` (append, don't replace) so earlier agents' traces survive.
- **File generation pattern**: agents that write files (`database_agent`, `devops_agent`) generate one file at a time via `call_llm`, write it with `write_project_file` (handles code-fence stripping + header comments), broadcast a `file_written` progress event per file, and accumulate into `generated_files`. Follow this pattern for `frontend_code`/`backend_code` when implementing them rather than inventing a new one.
- **Frontend event dedup**: `useProjectStream.js` dedups WebSocket messages by comparing the *full* JSON-stringified payload (minus timestamp), not a fixed field subset — gate/lifecycle events (`gate_reached`, `pipeline_complete`, etc.) don't carry `agent`/`stage`/`preview`/`content` fields, so a narrower comparison silently drops all but the first such event. Keep this in mind before "simplifying" that comparison.
- **Reload continuity**: `ProjectDetailPage.jsx` reconstructs an initial event list from `state.log` on page load (WebSocket buffer only holds the last 10 events). When adding new log lines, keep the `agentKey` regex-extraction convention (`^([a-zA-Z0-9]+?)(?:_agent)?:\s*`) in mind — it collapses each agent's log lines down to one card per agent, showing only the last line.
- **Gate routing is conditional-edge based**, not just linear `interrupt_before`. When adding a new gate or agent, update both the node/edge wiring in `pipeline.py` and the corresponding `route_gate_*` function.

---

## Prompt-Change Discipline

**Never edit a file in `prompts/` without a `docs/PROMPT_CHANGELOG.md` entry and an A/B run.**
The protocol (attribute the defect to its layer → write the hypothesis first →
change one thing → A/B with `backend/scripts/ab_prompt_test.py` → keep or revert
by the stated rule → record the verdict either way) is documented at the top of
`docs/PROMPT_CHANGELOG.md`. Run `python3 backend/tests/test_prompt_regression.py`
(zero API cost) after any prompt edit. Layer attribution and the current defect
taxonomy live in `docs/QUALITY_BASELINE.md`.

## Documentation Rules

- `docs/build-journal/failures.md` — append-only log of full-pipeline-test runs: what broke, root cause, fix, severity. Add an entry after any full end-to-end test, not after every small fix.
- `README.md` / `docs/USAGE.md` / `docs/ARCHITECTURE.md` are the shipped, user-and-contributor-facing docs — the front path. Keep them true; they are the only docs a new reader is expected to open.
- `docs/` also holds the measured-evidence docs (`INTEGRATION_RESULTS.md`, `QUALITY_BASELINE.md`, `OBSERVABILITY.md`, `PROMPT_CHANGELOG.md`). These are dated records — extend them, don't rewrite history in them.
- `docs/build-journal/` is the internal build log, kept for history and off the user's front path. Don't create docs speculatively.
- Prompt files (`prompts/*.md`) are living specs for agent behavior — when an agent's output quality or format changes, the prompt file is the fix, not a post-processing hack in the agent's Python code.

---

## Developer Brain Integration

Promote to Developer Brain only patterns with value beyond this repo — e.g. "LangGraph interrupt_before gate + conditional edge" as a reusable human-in-the-loop pipeline pattern, or "free-tier LLM routing needs a fallback pair + live model-list check" as a general litellm/OpenRouter lesson. Keep project-specific specifics (this pipeline's exact node names, this project's agent list) in this repo.

---

## Session Startup

1. Read this file and `docs/build-journal/failures.md`.
2. Check `git log --oneline -15` for the most recent work — this project moves in daily increments (commit messages are dated "Day N" in intent even when not in text).
3. If touching the pipeline or an agent, read `backend/app/graph/pipeline.py` and the specific agent file before changing either — the state contract is implicit in the return dicts, not documented separately.
