import os
import time
from dotenv import load_dotenv
from litellm import completion

# Load environment variables from the parent directory of backend/app (i.e. backend/.env)
load_dotenv(dotenv_path=os.path.join(os.path.dirname(os.path.dirname(__file__)), ".env"))

MODELS = {
    "research":     ("gemini/gemini-2.5-flash", "groq/llama-3.3-70b-versatile"),
    "requirements": ("gemini/gemini-2.5-flash", "openrouter/deepseek/deepseek-chat:free"),
    "architecture": ("openrouter/deepseek/deepseek-chat:free", "gemini/gemini-2.5-flash"),
    "planning":     ("gemini/gemini-2.5-flash", "groq/llama-3.3-70b-versatile"),
    "frontend_code":("openrouter/qwen/qwen3-coder:free", "openrouter/deepseek/deepseek-chat:free"),
    "backend_code": ("openrouter/deepseek/deepseek-chat:free", "openrouter/qwen/qwen3-coder:free"),
    "database":     ("groq/llama-3.3-70b-versatile", "openrouter/deepseek/deepseek-chat:free"),
    "qa":           ("openrouter/deepseek/deepseek-r1:free", "gemini/gemini-2.5-flash"),
    "devops":       ("groq/llama-3.3-70b-versatile", "gemini/gemini-2.5-flash"),
}

def call_llm(messages: list, agent_type: str, max_tokens=4000) -> str:
    primary, fallback = MODELS.get(agent_type, MODELS["research"])
    for model in [primary, fallback]:
        try:
            resp = completion(
                model=model,
                messages=messages,
                max_tokens=max_tokens,
                temperature=0.3
            )
            # Extract token usage
            tokens_used = 0
            if hasattr(resp, 'usage') and resp.usage:
                tokens_used = getattr(resp.usage, 'total_tokens', 0)
            
            # Log exact formatting: [LLM] agent=research model=gemini/gemini-2.5-flash tokens_used=42
            print(f"[LLM] agent={agent_type} model={model} tokens_used={tokens_used}", flush=True)
            
            return resp.choices[0].message.content
        except Exception as e:
            print(f"[LLM ERROR] Model {model} failed for agent={agent_type}: {str(e)}", flush=True)
            # Fallback specifically on rate limits
            if "429" in str(e) or "rate" in str(e).lower():
                print(f"[LLM FALLBACK] Rate limited. Sleeping 2 seconds before retry...", flush=True)
                time.sleep(2)
                continue
            raise
    raise RuntimeError(f"All models failed for agent_type={agent_type}")
