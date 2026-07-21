from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from app.routers.llm import router as llm_router
from app.routers.projects import router as projects_router
from app.routers.ws import router as ws_router
from app.validation.syntax import js_tool_status

app = FastAPI(title="Multi-Agent AI Product Builder API", version="1.0.0")

# Day 22's JS deep validation needs node + @babel/parser. Both are baked into
# the container image, but if either goes missing the validator falls back to
# brace-counting and would otherwise report "no syntax errors" for a file it
# never actually parsed. Say so at startup rather than letting the downgrade be
# discovered later in generated code. Not fatal — running without node is a
# legitimate local mode.
_js_unavailable = js_tool_status()
print(
    f"[startup] JS deep validation UNAVAILABLE, heuristic mode: {_js_unavailable}"
    if _js_unavailable
    else "[startup] JS deep validation ready (node + @babel/parser)"
)

# CORS matters only in dev, where Vite on :5173 is a different origin from the
# API on :8000. In the container nginx serves the app and proxies /api and /ws
# under one origin, so the browser never issues a cross-origin request at all.
# :3000 is listed anyway for the debugging case of hitting :8000 directly while
# the page is served from the container.
origins = [
    "http://localhost:5173",
    "http://127.0.0.1:5173",
    "http://localhost:5174",
    "http://127.0.0.1:5174",
    "http://localhost:3000",
    "http://127.0.0.1:3000",
]

app.add_middleware(
    CORSMiddleware,
    allow_origins=origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(llm_router)
app.include_router(projects_router)
app.include_router(ws_router)

# Both paths, one handler. nginx proxies /api/ but not /health, so the
# containerised health check needs to live under /api — while /health stays for
# direct-to-backend checks in dev and for `docker compose` probes.
@app.get("/health")
@app.get("/api/health")
def health_check():
    # Local-model availability rides along here rather than on its own endpoint:
    # the header already polls health and already renders a status dot, and this
    # is the same class of fact — what capacity the backend currently has. The
    # probe is cached with a short TTL, so polling it costs nothing.
    from app.llm_router import llm_mode, ollama_models

    local = ollama_models()
    return {"status": "ok", "version": "1.0.0", "js_validation": not _js_unavailable,
            "llm_mode": llm_mode(),
            "local_models": [m.split(":")[0] if m.endswith(":latest") else m for m in local]}


