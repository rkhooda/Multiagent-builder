# Multi-Agent Builder

A multi-agent AI system that turns a product brief into a working, reviewed codebase. A LangGraph pipeline of specialized agents runs research, requirements, architecture, planning, code generation, QA review, and devops packaging in sequence — pausing at human-approval gates along the way.

See `docs/architecture.md` for how the pipeline is wired together.

## Stack

- **Backend:** FastAPI, LangGraph (StateGraph + SQLite checkpointing), litellm (Gemini / Groq / OpenRouter with per-agent fallback), WebSockets
- **Frontend:** React 19, Vite, Tailwind CSS

## Running Locally

### Backend

```bash
cd backend
python -m venv venv && source venv/bin/activate
pip install -r requirements.txt
cp .env.example .env   # fill in GEMINI_API_KEY, GROQ_API_KEY, OPENROUTER_API_KEY, LANGCHAIN_API_KEY
uvicorn main:app --reload
```

The API serves on `http://localhost:8000`. Health check: `GET /health`.

### Frontend

```bash
cd frontend
npm install
npm run dev
```

The dev server serves on `http://localhost:5173` (CORS is pre-configured for `5173`/`5174`).

## Project Layout

```
backend/
  app/graph/       — LangGraph pipeline definition and shared state
  app/agents/       — one module per pipeline stage
  app/routers/      — FastAPI routes (LLM, projects, WebSocket)
  app/llm_router.py — per-agent model routing with fallback
prompts/            — system prompts loaded by each agent
frontend/src/       — React app (pages, components, hooks)
docs/               — architecture and decision records
```
