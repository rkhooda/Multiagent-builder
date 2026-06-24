from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from app.routers.llm import router as llm_router

app = FastAPI(title="Multi-Agent AI Product Builder API", version="0.1")

# Configure CORS
origins = [
    "http://localhost:5173",
    "http://127.0.0.1:5173",
    "http://localhost:5174",
    "http://127.0.0.1:5174",
]

app.add_middleware(
    CORSMiddleware,
    allow_origins=origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(llm_router)

@app.get("/health")
def health_check():
    return {"status": "ok", "version": "0.1"}

