# Multi-Agent Builder — Claude Operating Manual

## Developer Brain

Developer Brain lives at: `/Users/rkhooda/Documents/Rkxee Obsidian/Developer's brain`

At the start of every session, read in this order:

1. `/Users/rkhooda/Documents/Rkxee Obsidian/Developer's brain/CLAUDE.md` — governing operating principles (read before acting, quality gates, session protocol)
2. `/Users/rkhooda/Documents/Rkxee Obsidian/Developer's brain/ARCHITECTURE.md` — zone structure and content boundaries
3. `/Users/rkhooda/Documents/Rkxee Obsidian/Developer's brain/projects/multiagent-builder/overview.md` — this project's context in Developer Brain
4. This repository's `README.md` and `docs/architecture.md` — current implementation state
5. `git log --oneline -20` and `git status` — recent work and anything in flight

The principles in Developer Brain (read before acting, improve don't duplicate, quality over quantity, record decisions, prefer reversibility) govern every session in this repository and are not restated here. If anything in this file conflicts with Developer Brain, follow Developer Brain and flag the conflict to the user.

Never assume context from a previous chat. Recover it from the documents above — they are kept current for exactly this reason.

---

## Project

**Name:** `multiagent-builder`
*Must match `Developer Brain/projects/multiagent-builder/`.*

**What it is:** A multi-agent AI system that turns a product brief into a working, reviewed codebase. A LangGraph pipeline runs research → requirements → architecture → planning → codegen (frontend/backend/database) → QA review → devops packaging, pausing at four human-approval gates along the way.

**Stack:** FastAPI + LangGraph + SQLite (checkpointing) backend, litellm-routed LLM calls (Gemini/Groq/OpenRouter with per-agent primary+fallback models), React 19 + Vite + Tailwind frontend, WebSocket live feed.

**Key modules:**
- `backend/app/graph/pipeline.py` — the StateGraph: nodes, edges, human gates
- `backend/app/graph/state.py` — `ProjectState`, the single shape threaded through every agent
- `backend/app/agents/*.py` — one file per pipeline stage
- `backend/app/llm_router.py` — `MODELS` table (per-agent primary/fallback) and retry logic
- `prompts/*.md` — the system prompts each agent loads

---

## Rules

- Run backend: `cd backend && uvicorn main:app --reload`. Run frontend: `cd frontend && npm run dev`.
- `backend/projects.db` is a live SQLite checkpoint store — do not hand-edit it; treat it like any other runtime database.
- When adding or changing a pipeline stage, update both `pipeline.py` (graph wiring) and `state.py` (any new `ProjectState` fields) in the same change — they must stay in sync.
- New agents follow the existing pattern: a prompt in `prompts/`, an agent module in `backend/app/agents/`, a model entry in `llm_router.py`'s `MODELS` table.
- Never commit `.env`, `projects.db*`, or `server.log`.

---

## Development Workflow

- Keep `README.md` and `docs/architecture.md` accurate as the pipeline evolves — update them when a stage, gate, or major dependency changes, not on every commit.
- Before implementing a large feature (a new agent, a new gate, a graph restructuring), re-read `docs/architecture.md` and the relevant agent modules first.
- Record significant architecture-level decisions (why a stage was added, why a model was swapped, why the graph was restructured) in `docs/decisions/decision-[topic].md`. Only for decisions whose reasoning isn't obvious from the diff.

---

## Knowledge Promotion

At session end, or after a milestone, ask: does anything from this session generalize beyond this project?

- **Yes** (a reusable engineering pattern, a LangGraph/agent-orchestration technique, an LLM-routing lesson that would hold on a different stack) → promote to Developer Brain (`knowledge/` or `playbook/`), per the promotion criteria in `Developer Brain/WORKFLOW.md`.
- **No** (specific to this codebase's structure or brief-to-code domain) → leave it here, in `docs/` or a decision note.

Also update `Developer Brain/projects/multiagent-builder/overview.md` when this project's technical state changes substantially, and add a decision note there for significant architectural choices — per `Developer Brain/CLAUDE.md` → Session Protocol.

Do not promote or document routine task completion, or anything already visible from the code or git history.

---

## Git Workflow

- Conventional Commits (`feat`, `fix`, `docs`, `refactor`, `chore`, `test`), matching this repo's existing history.
- Keep commits focused; separate documentation-only changes from feature work when practical.
- Never rewrite pushed history.
