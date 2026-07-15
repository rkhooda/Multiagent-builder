import os
import time
from dotenv import load_dotenv
from litellm import completion

# Load environment variables from the parent directory of backend/app (i.e. backend/.env)
load_dotenv(dotenv_path=os.path.join(os.path.dirname(os.path.dirname(__file__)), ".env"))

MODELS = {
    "research":     ("gemini/gemini-2.5-flash", "groq/llama-3.3-70b-versatile"),
    "requirements": ("gemini/gemini-2.5-flash", "openrouter/cohere/north-mini-code:free"),
    "architecture": ("openrouter/qwen/qwen3-coder:free", "groq/llama-3.3-70b-versatile"),
    "planning":     ("gemini/gemini-2.5-flash", "groq/llama-3.3-70b-versatile"),
    "frontend_code":("openrouter/qwen/qwen3-coder:free", "openrouter/cohere/north-mini-code:free"),
    "backend_code": ("openrouter/cohere/north-mini-code:free", "openrouter/qwen/qwen3-coder:free"),
    "database":     ("groq/llama-3.3-70b-versatile", "openrouter/qwen/qwen3-coder:free"),
    "qa":           ("openrouter/nvidia/nemotron-3-nano-omni-30b-a3b-reasoning:free", "gemini/gemini-2.5-flash"),
    "devops":       ("groq/llama-3.3-70b-versatile", "gemini/gemini-2.5-flash"),
}

def call_llm(messages: list, agent_type: str, max_tokens=4000, timeout=None) -> str:
    primary, fallback = MODELS.get(agent_type, MODELS["research"])
    last_error = None

    for i, model in enumerate([primary, fallback]):
        for attempt in range(3):
            try:
                resp = completion(
                    model=model,
                    messages=messages,
                    max_tokens=max_tokens,
                    temperature=0.3,
                    timeout=timeout
                )
                tokens_used = 0
                if hasattr(resp, 'usage') and resp.usage:
                    tokens_used = getattr(resp.usage, 'total_tokens', 0)

                print(f"[LLM] agent={agent_type} model={model} tokens_used={tokens_used}", flush=True)
                return resp.choices[0].message.content
            except Exception as e:
                last_error = e
                err_msg = str(e).lower()
                print(
                    f"[LLM ERROR] Model {model} failed for agent={agent_type} attempt={attempt + 1}: {e}",
                    flush=True,
                )

                is_transient = any(k in err_msg for k in ["429", "rate", "503", "unavailable", "limit", "timeout"])
                if attempt < 2 and is_transient:
                    sleep_seconds = 2 * (attempt + 1)
                    print(
                        f"[LLM RETRY] Retrying model {model} in {sleep_seconds}s due to transient error...",
                        flush=True,
                    )
                    time.sleep(sleep_seconds)
                    continue
                break

        if i == 0:
            print(f"[LLM FALLBACK] Falling back to alternative model: {fallback}", flush=True)
            continue

    raise RuntimeError(f"All models failed for agent_type={agent_type}. Last error: {last_error}")
            
    raise RuntimeError(f"All models failed for agent_type={agent_type}")
