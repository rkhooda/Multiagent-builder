# Architecture

## Pipeline

The core of the system is a LangGraph `StateGraph` (`backend/app/graph/pipeline.py`) threading a single `ProjectState` (`backend/app/graph/state.py`) through a fixed sequence of agent nodes, checkpointed to SQLite so a run can pause and resume across human gates.

```
research → [gate 1] → requirements → [gate 2] → architecture → planning → [gate 3]
  → frontend_code → backend_code → database → qa → devops → [gate 4] → END
```

- **Human gates** (`human_gate_1..4`) are empty pass-through nodes; LangGraph pauses execution before each one and resumes when the frontend sends approval over the WebSocket connection.
- **Agents** (`backend/app/agents/*.py`) each read prior `ProjectState` fields, produce their own output field (e.g. `research_report`, `architecture_doc`, `generated_files`), and append to `log`.
- **File-generating agents** (frontend/backend/database/devops) stream output per file through `backend/app/utils/file_writer.py`, which strips code fences and adds header comments before writing to disk.

## LLM Routing

`backend/app/llm_router.py` holds a `MODELS` table mapping each agent to a `(primary, fallback)` model pair (Gemini, Groq, or OpenRouter). `call_llm` tries the primary model with retries, falls back to the secondary on transient errors (rate limits, timeouts, 503s), and logs token usage per call. Adding a new agent means adding its entry to this table.

## API Surface

- `backend/app/routers/projects.py` — project CRUD and pipeline state
- `backend/app/routers/llm.py` — LLM-facing endpoints
- `backend/app/routers/ws.py` — WebSocket channel for live pipeline progress (file-written events, gate approvals)
- `backend/app/core/connection_manager.py` — WebSocket connection registry
- `backend/app/core/database.py` — SQLite access outside the LangGraph checkpointer (project metadata)

## QA Stage

`backend/app/agents/qa_agent.py` batches generated files through DeepSeek R1 for code review before the devops stage runs, producing a `qa_report` and `qa_issues_count` surfaced in the frontend at gate 4.

## Frontend

React 19 + Vite + Tailwind. `frontend/src/pages/` holds the three top-level views (home, new project, project detail); `frontend/src/hooks/` wraps the WebSocket connection and pipeline state.
