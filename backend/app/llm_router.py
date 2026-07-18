import json
import os
import time
import urllib.request

from dotenv import load_dotenv
from litellm import completion
import litellm.exceptions as llm_exc

from app.exceptions import LLMAuthError, LLMError, LLMRateLimitError, LLMTimeoutError

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

DEFAULT_TIMEOUT_SECONDS = 90
# Wait before the single same-model retry on a 429, per tier: primary, fallback, ollama.
RATE_LIMIT_WAITS = [2, 10, 5]


# ── Ollama tier-3 detection ──────────────────────────────────────────────────
OLLAMA_URL = "http://localhost:11434"
OLLAMA_PROBE_TTL_SECONDS = 60
_ollama_cache = {"model": None, "checked_at": 0.0}


def get_ollama_model():
    """Tier-3 local fallback: first available model from a running Ollama.

    Probed with a 1s timeout and cached for a short TTL. Returns None silently
    when Ollama is absent — the chain simply ends before it.
    """
    now = time.monotonic()
    if now - _ollama_cache["checked_at"] < OLLAMA_PROBE_TTL_SECONDS:
        return _ollama_cache["model"]
    model = None
    try:
        with urllib.request.urlopen(f"{OLLAMA_URL}/api/tags", timeout=1) as resp:
            tags = json.load(resp)
        names = [m.get("name") for m in tags.get("models", []) if m.get("name")]
        if names:
            preferred = next((n for n in names if n.startswith("qwen3:14b")), names[0])
            model = f"ollama/{preferred}"
    except Exception:
        model = None
    _ollama_cache.update(model=model, checked_at=now)
    return model


# ── Fault injection (sabotage/regression testing) ────────────────────────────
# FAULT_INJECTION="429:gemini:3,timeout:research:2" — comma-separated rules
# kind:target:count. Target matches the agent_type exactly or a model-name
# substring; "*" matches everything. Each rule fires at most `count` times.
_fault_counts: dict = {}


def _fault_injection(agent_type: str, model: str):
    spec = os.getenv("FAULT_INJECTION", "")
    if not spec:
        return None
    for rule in spec.split(","):
        try:
            kind, target, count = rule.strip().split(":")
        except ValueError:
            continue
        if target != "*" and target != agent_type and target not in model:
            continue
        if _fault_counts.get(rule, 0) >= int(count):
            continue
        _fault_counts[rule] = _fault_counts.get(rule, 0) + 1
        print(f"[FAULT] {kind} injected for agent={agent_type} model={model} "
              f"({_fault_counts[rule]}/{count})", flush=True)
        if kind == "429":
            raise llm_exc.RateLimitError(message="fault-injected 429", llm_provider="fault", model=model)
        if kind == "timeout":
            raise llm_exc.Timeout(message="fault-injected timeout", model=model, llm_provider="fault")
        if kind == "auth":
            raise llm_exc.AuthenticationError(message="fault-injected auth failure", llm_provider="fault", model=model)
        if kind == "garbage":
            return "ok"
    return None


def _log_attempt(agent_type: str, model: str, attempt: int, outcome: str, started: float):
    entry = {
        "agent_type": agent_type,
        "model": model,
        "attempt": attempt,
        "outcome": outcome,
        "latency_ms": int((time.monotonic() - started) * 1000),
    }
    print(f"[LLM] {json.dumps(entry)}", flush=True)


def call_llm(messages: list, agent_type: str, max_tokens=4000, timeout=None) -> str:
    """LLM call with a classified-error retry policy over the provider chain.

    Per model: one same-model retry on 429 (after a per-tier wait) or timeout;
    auth errors fail over to the next tier immediately (retrying a bad key is
    pure waste); unclassified errors (404s, 5xx) also move to the next tier.
    Chain: primary -> fallback -> Ollama (when detected). Raises a typed
    LLMError subclass when the whole chain is exhausted.
    """
    primary, fallback = MODELS.get(agent_type, MODELS["research"])
    chain = [primary, fallback]
    ollama = get_ollama_model()
    if ollama:
        chain.append(ollama)
    timeout = timeout or DEFAULT_TIMEOUT_SECONDS

    auth_failures = 0
    last_error = None

    for tier, model in enumerate(chain):
        for attempt in (1, 2):
            started = time.monotonic()
            try:
                injected = _fault_injection(agent_type, model)
                if injected is not None:
                    return injected
                resp = completion(
                    model=model,
                    messages=messages,
                    max_tokens=max_tokens,
                    temperature=0.3,
                    timeout=timeout,
                )
                _log_attempt(agent_type, model, attempt, "ok", started)
                return resp.choices[0].message.content or ""
            except llm_exc.Timeout:
                last_error = LLMTimeoutError(
                    f"{model} timed out after {timeout}s", agent_type, model)
                outcome = "timeout"
            except llm_exc.RateLimitError as e:
                last_error = LLMRateLimitError(
                    f"{model} rate-limited: {e}", agent_type, model)
                outcome = "rate_limit"
            except (llm_exc.AuthenticationError, llm_exc.PermissionDeniedError) as e:
                last_error = LLMAuthError(f"{model} auth failed: {e}", agent_type, model)
                auth_failures += 1
                _log_attempt(agent_type, model, attempt, "auth", started)
                break  # bad key never fixes itself — next tier immediately
            except Exception as e:
                last_error = LLMError(f"{model} failed: {e}", agent_type, model)
                _log_attempt(agent_type, model, attempt, "error", started)
                break  # unclassified (dead slug 404, 5xx, ...) — next tier
            _log_attempt(agent_type, model, attempt, outcome, started)
            if attempt == 1:
                if outcome == "rate_limit":
                    time.sleep(RATE_LIMIT_WAITS[min(tier, len(RATE_LIMIT_WAITS) - 1)])
                continue  # one same-model retry for 429/timeout
            break  # second failure on this model — next tier

    if auth_failures == len(chain):
        raise LLMAuthError(
            f"All providers rejected their API keys for agent={agent_type} — fix backend/.env",
            agent_type, ",".join(chain))
    raise last_error
