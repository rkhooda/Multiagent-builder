from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from app.llm_router import call_llm

router = APIRouter(prefix="/api", tags=["llm"])

class LLMTestRequest(BaseModel):
    prompt: str
    agent_type: str

class LLMTestResponse(BaseModel):
    response: str
    agent_type: str

@router.post("/test-llm", response_model=LLMTestResponse)
def test_llm(request: LLMTestRequest):
    try:
        messages = [{"role": "user", "content": request.prompt}]
        response_text = call_llm(messages, request.agent_type)
        return LLMTestResponse(response=response_text, agent_type=request.agent_type)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
