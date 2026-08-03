import contextlib
import json
import os
import threading
import time
import urllib.request

from dotenv import load_dotenv
from litellm import completion
import litellm.exceptions as llm_exc

from app.exceptions import LLMAuthError, LLMError, LLMRateLimitError, LLMTimeoutError
from app.observability import llm_cache, metrics_store

# Load environment variables from the parent directory of backend/app (i.e. backend/.env)
load_dotenv(dotenv_path=os.path.join(os.path.dirname(os.path.dirname(__file__)), ".env"))

MODELS = {
    "research":     ("gemini/gemini-2.5-flash", "groq/llama-3.3-70b-versatile"),
    "requirements": ("gemini/gemini-2.5-flash", "openrouter/cohere/north-mini-code:free"),
    # Day 23: openrouter/qwen/qwen3-coder:free was DELISTED ("This model is
    # unavailable"), so it was a guaranteed-failing primary for every coder
    # agent — one dead round trip per call before the groq fallback caught it.
    # Groq llama-3.3-70b was already the proven fallback here, so it is promoted
    # rather than swapping in an untested slug: the surviving free coder model
    # (cohere/north-mini-code) still returns EMPTY responses (Day 18), and a new
    # model would contaminate today's baseline latency/token measurements.
    # Gemini is the cross-provider fallback so a groq rate limit is recoverable.
    "architecture": ("groq/llama-3.3-70b-versatile", "gemini/gemini-2.5-flash"),
    "planning":     ("gemini/gemini-2.5-flash", "groq/llama-3.3-70b-versatile"),
    "frontend_code":("groq/llama-3.3-70b-versatile", "gemini/gemini-2.5-flash"),
    # Improvement 01, ponytail #2 — routed deliberately OFF the coders' primary.
    #
    # The brief's premise was that OpenRouter (~50 req/day) is the pinch point.
    # That stopped being true on Day 23: qwen3-coder:free was delisted and both
    # coders moved to groq primary, so the only OpenRouter slug left in this
    # table is requirements' fallback. The scarce pool TODAY is groq's daily
    # TOKEN allowance — measured refusal at 95,966 of 100,000 — and that is the
    # coders' primary. So the reviewer takes gemini first: 1M tracked daily
    # tokens, and because gemini is the coder's FALLBACK, review only contends
    # with generation once groq is already dead, at which point nothing is
    # generating anyway.
    #
    # Judgement over code-generation strength is the right axis here: a critic
    # emits a short structured verdict, not source. gemini-2.5-flash already
    # serves the pipeline's other two judgement/strict-JSON agents (planning, qa).
    "frontend_review": ("gemini/gemini-2.5-flash", "groq/llama-3.3-70b-versatile"),
    "backend_code": ("groq/llama-3.3-70b-versatile", "gemini/gemini-2.5-flash"),
    "database":     ("groq/llama-3.3-70b-versatile", "gemini/gemini-2.5-flash"),
    # Day 26: the nemotron reasoning model was demoted OFF this slot. It does not
    # honour max_tokens: asked for 3,000 it returned 32,768 (its own ceiling) and
    # took 354s. The gemini fallback then reviewed the same batch in 7.9s using
    # 1,452 completion tokens. That is a 45x latency tax and a 22x token tax for
    # output that was discarded anyway, and it made QA the slowest stage in the
    # pipeline by a factor of four. Reasoning tokens are not free here, and the
    # budget mechanism cannot cap a provider that ignores the parameter.
    "qa":           ("gemini/gemini-2.5-flash", "groq/llama-3.3-70b-versatile"),
    "devops":       ("groq/llama-3.3-70b-versatile", "gemini/gemini-2.5-flash"),
}

# ── LangSmith tracing (optional, never required) ─────────────────────────────
# Traces the completion call itself so prompts, responses and token usage appear
# per ATTEMPT, nested under whatever parent run is active — parallel coder
# workers therefore land under their phase instead of scattering as orphans.
# When tracing is off or the SDK is missing this is a plain passthrough, so the
# call path is identical and costs nothing.
def _plain_completion(**kwargs):
    return completion(**kwargs)


def _tracing_enabled() -> bool:
    if not metrics_store.is_enabled():
        return False
    if os.getenv("LANGCHAIN_TRACING_V2", "").lower() not in ("true", "1"):
        return False
    key = os.getenv("LANGCHAIN_API_KEY", "")
    # The Day 1 placeholder was never replaced; treat it as absent rather than
    # emitting 403s on every call.
    return bool(key) and key != "your_key_here"


_traced_completion = _plain_completion
if _tracing_enabled():
    try:
        from langsmith import traceable
        os.environ.setdefault("LANGCHAIN_PROJECT", "multiagent-builder")
        _traced_completion = traceable(run_type="llm", name="llm_attempt")(_plain_completion)
        print("[Observability] LangSmith tracing ENABLED "
              f"(project={os.environ['LANGCHAIN_PROJECT']})", flush=True)
    except Exception as e:                      # noqa: BLE001
        print(f"[Observability] LangSmith unavailable, continuing untraced: {e}", flush=True)
elif not metrics_store.is_enabled():
    print("[Observability] DISABLED via OBSERVABILITY_ENABLED — no tracing, no metrics",
          flush=True)
else:
    print("[Observability] LangSmith tracing disabled "
          "(set LANGCHAIN_TRACING_V2=true and a real LANGCHAIN_API_KEY to enable) "
          "— local metrics still recorded", flush=True)


# ── Output token budgets (Day 26) ────────────────────────────────────────────
# WHY these are a resolver and not a table of numbers. Every call site already
# passes a tuned max_tokens (architecture 12000, requirements 4500, coder files
# 1500, planning scaled by file count). Day 26 measured those budgets to be
# BINDING, not slack: architecture stopped at 11,996/12,000 twice, requirements
# at 4,496/4,500 on both recorded calls, two coder files at 1,49x/1,500.
#
# So replacing them with a central table of flat defaults would truncate output
# that is already at its ceiling. Instead the call site's value stays the
# baseline and this resolves the two things it cannot know: an operator override
# and the run's speed profile. One choke point, no call-site edits.
FAST_MODE_SCALE = 0.5
# Raised 800 -> 1000 (ceiling audit, 2026-08-03): the largest measured complete
# coder file is 829 tokens (TaskForm.jsx, groq), so the old floor truncated the
# biggest real file in fast mode — the same starvation defect one profile over.
# 1.2 x 829 ~= 995 -> 1000. Pinned by test_token_budgets in BOTH profiles.
FAST_MODE_FLOOR = 1000         # below this even a small file stops mid-function

# Agents whose budget fast mode is allowed to scale.
#
# WHY a whitelist and not "all of them". max_tokens is a guillotine, not an
# instruction to be brief: a model asked for half the ceiling does not write a
# more concise document, it writes the same document and stops mid-sentence.
# Day 26 measured which agents have headroom and which are already at the wall:
#
#   architecture  11,996 / 12,000   at the wall — halving truncates the doc
#   requirements   4,496 / 4,500    at the wall, on BOTH recorded calls
#   research       4,368 / 4,500    97% of budget
#   planning       sized per file count and validated for MIN_TASKS=8 — a short
#                  plan is not a faster plan, it is invalid JSON
#
# Those four are excluded: scaling them trades a broken artifact for no gain.
# What is left has real headroom (coder files average 356 tokens against 1,500),
# which is also why fast mode's budget lever is honestly a minor one — the
# measured wall-clock savings come from skipping the review stages below.
# qa was removed 2026-08-03 (Improvement 02): its gemini primary spends
# 2.4-2.7k completion tokens THINKING per batch before any answer text, so QA
# has no halving headroom — scaling it re-creates the empty-answer failure the
# measured QA_MAX_TOKENS exists to prevent. Same logic as the exclusions above.
FAST_MODE_SCALABLE = {"frontend_code", "backend_code", "database", "devops"}


def resolve_max_tokens(agent_type: str, requested: int, fast_mode: bool = False) -> int:
    """Effective output budget: env override > fast-mode scaling > call site."""
    env = os.getenv(f"LLM_MAX_TOKENS_{(agent_type or '').upper()}")
    if env and env.strip().isdigit():
        return max(1, int(env.strip()))
    if fast_mode and agent_type in FAST_MODE_SCALABLE:
        return max(FAST_MODE_FLOOR, int(requested * FAST_MODE_SCALE))
    return requested


def _finish_reason(resp) -> str:
    try:
        return getattr(resp.choices[0], "finish_reason", "") or ""
    except Exception:                           # noqa: BLE001 — never fail a call
        return ""


DEFAULT_TIMEOUT_SECONDS = 90

# Per-agent timeout, in seconds. Absent = DEFAULT_TIMEOUT_SECONDS.
#
# WHY (Day 25): a single uniform timeout is wrong because completion size is not
# uniform. Measured from metrics.db, average completion tokens per agent:
# requirements 4.5k, research 4.1k, architecture 10.6k, planning 21.5k, qa 17.1k.
# Planning emits ~5x what requirements does and cannot be shortened without
# splitting the plan across calls, which risks incoherent plans.
#
# The consequence was not a slow path but a BLOCKED one. The only planning call
# that ever succeeded took 84.5s against the 90s ceiling — 94% of budget — and
# every other attempt across two days timed out at exactly 90s. Planning sat on
# a coin flip, and it gets worse with complexity because more files means more
# plan tokens, so the harder the project the likelier it never plans at all.
#
# These numbers are measured headroom over observed successes, not guesses.
AGENT_TIMEOUT_SECONDS = {
    "planning": 240,  # observed success 84.5s at 21.5k tokens; complex briefs run longer
    "qa":       360,  # observed success 354s — batched review over many files
}
# Wait before the single same-model retry on a 429, per tier: primary, fallback, ollama.
RATE_LIMIT_WAITS = [2, 10, 5]

# ── Per-model request pacing (Day 25) ────────────────────────────────────────
# WHY. The parallel coder phases fire one call per file with no rate awareness.
# On the Day 25 simple run that produced 59 rate-limit outcomes against 13
# successes and lost 34 of 47 frontend files — not because any model rejected
# the WORK, but because it never received the request. Both providers were
# limited at once, so failing over could not help either.
#
# A 429 is not a dead model. It is a recoverable condition whose only remedy is
# waiting, which makes the old policy — two retries seconds apart, then mark the
# file failed — exactly wrong: it converts a slowdown into permanent data loss
# and, by retrying instantly, deepens the burst that caused it.
#
# So requests to a model are spaced by a minimum interval, enforced across ALL
# threads (the coder pool is a thread pool, so this must be process-wide, not
# per-worker). Defaults are derived from the free-tier limits that actually
# bind: Gemini 2.5 Flash by requests/minute, Groq by TOKENS/minute (12k TPM
# against ~4k-token coder calls is ~3 requests/min, far tighter than its 30 RPM).
#
# ponytail: a fixed interval, not a token bucket. It needs no token accounting,
# no per-response bookkeeping, and is impossible to get subtly wrong. Upgrade to
# a real bucket only if bursting into unused headroom is ever worth the
# complexity. Tune with LLM_MIN_INTERVAL_<PROVIDER> without a code change.
_PACE_DEFAULTS = {
    "gemini": 6.5,   # ~10 RPM free tier
    "groq": 8.0,     # TPM-bound; empirically survivable for ~4k-token calls
    "openrouter": 3.0,
    "ollama": 0.0,   # local, unmetered
}
_pace_lock = threading.Lock()
_next_allowed = {}


def _provider_of(model: str) -> str:
    return (model or "").split("/", 1)[0].lower()


def min_interval_for(model: str) -> float:
    """Configured interval for this model, widened by anything it has learned
    from 429s it received despite being paced."""
    provider = _provider_of(model)
    base = _PACE_DEFAULTS.get(provider, 0.0)
    env = os.getenv(f"LLM_MIN_INTERVAL_{provider.upper()}")
    if env:
        try:
            base = max(0.0, float(env))
        except ValueError:
            pass
    return base * _backoff_factor.get(model, 1.0)


def _pace(model: str):
    """Block until this model's next slot, then reserve the one after it.

    The reservation happens under the lock BEFORE sleeping, so N concurrent
    workers queue into distinct slots instead of all sleeping the same interval
    and then stampeding together — which would reproduce the exact burst this
    exists to prevent.
    """
    interval = min_interval_for(model)
    if interval <= 0:
        return
    with _pace_lock:
        now = time.monotonic()
        slot = max(now, _next_allowed.get(model, 0.0))
        _next_allowed[model] = slot + interval
    delay = slot - time.monotonic()
    if delay > 0:
        time.sleep(delay)


# Per-model multiplier grown by _penalise when a 429 lands despite pacing.
_backoff_factor: dict = {}


def _penalise(model: str):
    """After a 429, push this model's next slot out AND widen its interval.

    Pushing the slot alone only re-spaces the next request; it assumes the
    configured interval was right and this 429 was bad luck. A 429 that arrives
    despite pacing is evidence the configured interval is too optimistic — the
    real limit is tighter than the guess — so the interval itself is widened for
    the rest of the process. This is the limiter learning the actual limit
    instead of repeating the same mistake at the same cadence.

    Growth is capped so a burst of 429s cannot stall a model indefinitely, and
    the factor is per-model because that is the grain providers enforce at.
    """
    with _pace_lock:
        _backoff_factor[model] = min(_MAX_BACKOFF_FACTOR,
                                     _backoff_factor.get(model, 1.0) * 1.5)
    interval = min_interval_for(model)
    if interval <= 0:
        return
    with _pace_lock:
        _next_allowed[model] = max(_next_allowed.get(model, 0.0),
                                   time.monotonic()) + interval


# ── Daily token budget (Day 26) ──────────────────────────────────────────────
# WHY this and not the planned RPM token bucket. Requests-per-minute was never
# the limit that stopped this pipeline. What stopped it was Groq's tokens-per-DAY
# allowance: 95,966 of 100,000 consumed, every subsequent call refused for 45
# minutes. RPM is already handled by the pacer above.
#
# A daily allowance cannot be paced around — once it is gone it is gone until
# UTC midnight. The only useful behaviour is to STOP SENDING to that provider and
# fail over to one with budget left, which turns a guaranteed 429 into a
# successful call on another tier. Without this the chain still burns a doomed
# round trip per tier before finding that out.
#
# Counting is seeded once from metrics.db (which already records every attempt's
# usage) and then kept in memory, so this costs one query per process rather than
# one per call. Reseeded when the UTC day rolls over.
_DAILY_TOKEN_LIMITS = {
    "groq": 100_000,        # observed refusal: "tokens per day (TPD): Limit 100000"
    "gemini": 1_000_000,    # generous free daily ceiling; tracked, rarely binding
    "openrouter": 0,        # 0 = untracked: OpenRouter free tier limits REQUESTS
    "ollama": 0,            # local, unmetered
}
_MAX_BACKOFF_FACTOR = 8.0
_budget_lock = threading.Lock()
_spent_today: dict = {}
_budget_day: list = [None]


def daily_limit_for(provider: str) -> int:
    env = os.getenv(f"LLM_DAILY_TOKENS_{provider.upper()}")
    if env and env.strip().isdigit():
        return int(env.strip())
    return _DAILY_TOKEN_LIMITS.get(provider, 0)


def _utc_day() -> str:
    return time.strftime("%Y-%m-%d", time.gmtime())


def _seed_budget():
    """Load today's spend from metrics.db once, or reset it on a new UTC day."""
    day = _utc_day()
    if _budget_day[0] == day:
        return
    _spent_today.clear()
    try:
        for provider, row in metrics_store.tokens_used_today().items():
            _spent_today[provider] = row.get("tokens", 0)
    except Exception:                           # noqa: BLE001 — never block a call
        pass
    _budget_day[0] = day


# ── Abandoned projects ───────────────────────────────────────────────────────
# Deleting or cancelling a RUNNING project cancels the asyncio task, but the
# coder phase dispatches each file through asyncio.to_thread — and cancelling
# the coroutine that awaits a thread does not stop the thread. Day 30 measured
# the consequence: a deleted project kept issuing LLM calls for minutes
# afterwards and regrew its own metrics rows (0 -> 5 after deletion), spending
# the scarcest resource in this project on files nobody can ever download.
#
# Every request in the codebase goes through call_llm, so one check there stops
# the cascade at the NEXT call. The call already in flight still completes —
# a blocking provider request cannot be interrupted cleanly — so this bounds
# the waste at one call per worker instead of a whole phase.
#
# ponytail: unbounded set of ids. ~36 bytes per deleted project on a
# single-user tool; add eviction if this ever runs multi-tenant.
_abandoned_projects: set = set()


def abandon_project(project_id: str) -> None:
    """Stop serving LLM calls for a project whose run was deleted/cancelled."""
    if project_id:
        _abandoned_projects.add(project_id)


def is_abandoned(project_id: str) -> bool:
    return bool(project_id) and project_id in _abandoned_projects


def budget_exhausted(model: str) -> bool:
    provider = _provider_of(model)
    limit = daily_limit_for(provider)
    if limit <= 0:
        return False                            # 0 = untracked, never blocks
    with _budget_lock:
        _seed_budget()
        return _spent_today.get(provider, 0) >= limit


def _spend(model: str, usage: dict):
    total = (usage.get("prompt_tokens") or 0) + (usage.get("completion_tokens") or 0)
    if not total:
        return
    provider = _provider_of(model)
    with _budget_lock:
        _seed_budget()
        _spent_today[provider] = _spent_today.get(provider, 0) + total


def daily_budget_report() -> list:
    """Per-provider remaining daily allowance, for the metrics panel."""
    with _budget_lock:
        _seed_budget()
        snapshot = dict(_spent_today)
    out = []
    for provider in sorted(set(list(_DAILY_TOKEN_LIMITS) + list(snapshot))):
        limit = daily_limit_for(provider)
        used = snapshot.get(provider, 0)
        out.append({
            "provider": provider,
            "used": used,
            "limit": limit or None,
            "remaining": max(0, limit - used) if limit > 0 else None,
            "pct_used": round(used / limit * 100, 1) if limit > 0 else None,
            "tracked": limit > 0,
        })
    return out


# ── Ollama tier-3 detection ──────────────────────────────────────────────────
# The URL differs by deployment and both are real: host Ollama during dev
# (localhost) and the docker-compose `ollama` profile service (http://ollama:11434),
# which is why this is env-driven rather than a constant.
OLLAMA_URL = (os.getenv("OLLAMA_BASE_URL") or "http://localhost:11434").rstrip("/")
OLLAMA_PROBE_TTL_SECONDS = 60
# Generous enough to survive a daemon that is busy generating. Measured: a 1s
# timeout made the tier DISAPPEAR under its own load — a re-probe landing during
# a 101s local completion timed out, the model list came back empty, and the
# next agent skipped straight past a working Ollama to a pause. An absent daemon
# still fails fast regardless of this value, because nothing listening means a
# connection refusal rather than a timeout.
OLLAMA_PROBE_TIMEOUT_SECONDS = 3
_ollama_cache = {"models": [], "checked_at": 0.0}


def ollama_models() -> list:
    """Model names a running Ollama is actually serving; [] when it is not.

    Enumerating rather than answering a yes/no, because "the daemon is up" and
    "the model I want is pulled" are different facts and only the second one
    makes a tier usable. Re-probed on a short TTL so a model pulled mid-session
    becomes usable without restarting the process, and a failed re-probe keeps
    what was last seen rather than declaring the tier gone.
    """
    now = time.monotonic()
    if _ollama_cache["checked_at"] and now - _ollama_cache["checked_at"] < OLLAMA_PROBE_TTL_SECONDS:
        return _ollama_cache["models"]
    try:
        with urllib.request.urlopen(f"{OLLAMA_URL}/api/tags",
                                    timeout=OLLAMA_PROBE_TIMEOUT_SECONDS) as resp:
            names = sorted(m["name"] for m in json.load(resp).get("models", []) if m.get("name"))
    except Exception:                           # noqa: BLE001 — absence is normal
        # A failed re-probe keeps the last known models rather than erasing them.
        # Once the daemon has been seen serving, a failure means "busy" far more
        # often than "gone", and treating it as "gone" removes the fallback at
        # the exact moment it is under load and most needed. If it really has
        # stopped, the next call fails over and the chain still pauses safely —
        # a recoverable cost, unlike silently losing the tier mid-run.
        names = _ollama_cache["models"]
    _ollama_cache.update(models=names, checked_at=now)
    return names


# Per-agent local preference, resolved against what is ACTUALLY pulled.
#
# WHY two lists and not nine. The temptation is a bespoke model per agent, but
# on a single 8GB machine the binding cost is not capability, it is the MODEL
# SWAP: switching between two ~5GB models unloads and reloads weights, and a
# pipeline that alternates per stage pays that on every transition. Fewer
# distinct models is both simpler and measurably faster here.
#
# So the split is the one that actually earns its reload: a coding-tuned model
# for the agents emitting source files, a general model for the agents emitting
# prose and JSON. phi4-mini is nobody's first choice — it is the floor that
# keeps the tier alive on hardware that cannot hold anything larger.
#
# Prefixes, not exact tags, because Ollama names carry tags (`qwen3:4b`,
# `phi4-mini:latest`) that vary with what was pulled.
_LOCAL_CODE = ["qwen2.5-coder", "qwen3", "phi4-mini"]
_LOCAL_PROSE = ["qwen3", "qwen2.5-coder", "phi4-mini"]
LOCAL_FALLBACKS = {agent: _LOCAL_CODE for agent in
                   ("frontend_code", "backend_code", "database", "devops")}
LOCAL_FALLBACKS.update({agent: _LOCAL_PROSE for agent in
                        ("research", "requirements", "architecture", "planning", "qa",
                         # Reviewing is judgement + strict JSON, not code emission.
                         "frontend_review")})


# Local generation is slow enough that the cloud timeouts are a guillotine: the
# measured cloud budgets (90s default, 240s planning) assume a datacentre GPU,
# and a 7B model on an M1 emits tens of tokens per second, so a document that
# takes gemini 8s takes minutes here. Without a floor the local tier would
# "fail" on every long document while it was in fact working correctly.
OLLAMA_TIMEOUT_FLOOR_SECONDS = 900

# Ollama defaults every model to a 4,096-token context REGARDLESS of what the
# model supports, and then silently truncates anything that does not fit:
#
#   msg="truncating input prompt" limit=2050 prompt=4375 keep=4 new=2050
#
# Measured on Day 30. The available input budget is num_ctx MINUS the requested
# output, so a 4k context asked for ~2k tokens leaves ~2k for the prompt — and
# this pipeline sends 11-15k characters (3-4.5k tokens) to every agent. More
# than half of each prompt was being discarded, keeping only the first 4 tokens.
#
# The call still returns 200 with plausible-looking prose, so nothing surfaces
# it: no error, no finish_reason, no metric. It presents as "local models are
# weak" when the model never saw the question. This is the real root cause
# behind the Day 29 conclusion that local output is thin and that planning
# cannot emit a valid plan.
#
# So size the window to the actual request instead of accepting the default.
# CHARS_PER_TOKEN is deliberately pessimistic (ollama's tokenizer counted 3.3
# chars/token on this pipeline's prompts where litellm reported 4.9) because
# under-estimating silently truncates again, while over-estimating only costs
# KV-cache memory.
OLLAMA_CHARS_PER_TOKEN = 3.0
OLLAMA_MIN_CONTEXT_TOKENS = 4096
# A ceiling, because the KV cache is real memory on the one machine this shares
# with everything else — an 8GB box cannot hold a 32k window for a 4B model on
# top of the weights. Raise it on a machine with headroom.
OLLAMA_MAX_CONTEXT_TOKENS = int(os.getenv("OLLAMA_MAX_CONTEXT_TOKENS", "16384"))


def ollama_num_ctx(context_chars: int, max_tokens: int) -> int:
    """Context window sized to this request, clamped to what the box can hold."""
    needed = int(context_chars / OLLAMA_CHARS_PER_TOKEN) + max_tokens
    # Round up to a 1,024 multiple so near-identical prompts reuse one cache
    # size instead of forcing a reallocation per call.
    rounded = -(-needed // 1024) * 1024
    return max(OLLAMA_MIN_CONTEXT_TOKENS,
               min(rounded, OLLAMA_MAX_CONTEXT_TOKENS))

# Local models share ONE machine's RAM and GPU. Past a small number of
# concurrent requests they do not degrade gracefully like a cloud endpoint —
# they contend for the same weights and get SLOWER than running serially, and on
# 8GB two ~5GB models cannot be resident at once at all, so the machine swaps.
# Hence a cap distinct from the cloud concurrency: this is a hardware limit, not
# a rate limit. Raise it on a machine with headroom.
#
# It lives HERE rather than in the parallel runner because the runner cannot know
# which tier a call will land on — cloud may serve two files while a third falls
# through to local. The tier is only known at the moment of the call.
OLLAMA_MAX_CONCURRENT = int(os.getenv("OLLAMA_MAX_CONCURRENT") or 1)
_ollama_sem = threading.Semaphore(max(1, OLLAMA_MAX_CONCURRENT))


def llm_mode() -> str:
    """auto (escalate cloud -> local) | cloud-only | prefer-local."""
    mode = (os.getenv("LLM_MODE") or "auto").strip().lower()
    return mode if mode in ("auto", "cloud-only", "prefer-local") else "auto"


def get_ollama_model(agent_type: str = None):
    """Best pulled local model for this agent, as a litellm `ollama/…` string.

    Degrades rather than errors: preferred model absent -> next in the list ->
    whatever IS pulled. Routing to an unpulled model would make Ollama's own
    404 the last error of the chain, which reads as "local tier broken" when the
    truth is "that one model was never downloaded". Returns None when nothing is
    served at all, and the chain simply ends before this tier.
    """
    available = ollama_models()
    if not available:
        return None
    prefs = LOCAL_FALLBACKS.get(agent_type, _LOCAL_PROSE)
    pick = next((name for pref in prefs for name in available
                 if name.startswith(pref)), available[0])
    return f"ollama/{pick}"


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
        # Day 22: substantial-but-unparseable output. `garbage` returns 2 chars
        # and trips the LENGTH validator, which exercises the Day 17 path; this
        # produces a plausible-looking file that only a real parser rejects, so
        # it is the fault that actually reaches the syntax validator + repair.
        if kind == "syntaxerr":
            return _BROKEN_JS if "front" in agent_type else _BROKEN_PY
    return None


_BROKEN_PY = '''from fastapi import APIRouter, HTTPException
from sqlalchemy.orm import Session

router = APIRouter(prefix="/api/notes", tags=["notes"])


@router.get("/")
def list_notes(db: Session)
    """Missing colon on the line above — parses only after repair."""
    return db.query(Note).all()
'''

_BROKEN_JS = '''import React, { useState } from 'react';

export default function NoteList({ notes, onDelete }) {
  const [query, setQuery] = useState('');
  return (
    <div className="list">
      <input value={query} onChange={(e) => setQuery(e.target.value)} />
      {notes.map((note) => (
        <span key={note.id}>{note.title}</div>
      ))}
    </div>
  );
}
'''


# Providers that omitted usage — warned once each, not per call.
_usage_warned: set = set()


def _extract_usage(resp, model: str) -> dict:
    """Pull normalised token counts off a litellm response.

    Free-tier providers occasionally omit `usage` entirely; that is recorded as
    None rather than 0 so averages can exclude it instead of being dragged down
    by fake zeros. Verified populated for gemini and groq (Day 23).
    """
    u = getattr(resp, "usage", None)
    if u is None:
        if model not in _usage_warned:
            _usage_warned.add(model)
            print(f"[LLM] WARNING: {model} returned no usage — tokens recorded as null",
                  flush=True)
        return {"prompt_tokens": None, "completion_tokens": None, "total_tokens": None}
    return {k: getattr(u, k, None)
            for k in ("prompt_tokens", "completion_tokens", "total_tokens")}


def _log_attempt(agent_type: str, model: str, attempt: int, outcome: str, started: float,
                 usage: dict = None, project_id: str = None, label: str = None,
                 context_chars: int = None, truncated: bool = None, cache: str = None):
    """Structured per-attempt record — the single observability choke point.

    Every attempt on every outcome path (ok/timeout/rate_limit/auth/error)
    already routed through here, so this is where usage and identity are
    captured too: one function instead of tracing calls in nine agents. A failed
    attempt and the fallback attempt that succeeded are separate records, so
    latency for both survives and only the successful one carries tokens.

    `project_id`/`label` are passed down explicitly from the caller — coder
    workers run in threads via asyncio.to_thread, where thread-locals and
    ambient context do not reliably map back to the originating file.
    """
    entry = {
        "agent_type": agent_type,
        "model": model,
        "attempt": attempt,
        "outcome": outcome,
        "latency_ms": int((time.monotonic() - started) * 1000),
    }
    if project_id:
        entry["project_id"] = project_id
    if label:
        entry["label"] = label
    if context_chars is not None:
        entry["context_chars"] = context_chars
    if truncated:
        entry["truncated"] = True
    if cache:
        entry["cache"] = cache
    entry.update(usage or {"prompt_tokens": None, "completion_tokens": None,
                           "total_tokens": None})
    print(f"[LLM] {json.dumps(entry)}", flush=True)
    if truncated:
        # Loud, because the call SUCCEEDED — nothing downstream can tell that the
        # document or source file it just received stops mid-sentence.
        print(f"[LLM] WARNING: {agent_type} output TRUNCATED at the token ceiling"
              f" ({entry.get('completion_tokens')} tokens, model={model}"
              f"{', ' + label if label else ''}) — raise its max_tokens",
              flush=True)
        # Improvement 02: a truncation is a silent degradation — the run keeps
        # going with a cut-off artifact. Counted per run, not only logged.
        from app.observability import degraded
        degraded.record(project_id, f"llm_truncated:{agent_type}")
    metrics_store.record_agent_run(
        agent=agent_type, model=model, attempt=attempt, outcome=outcome,
        project_id=project_id, label=label, context_chars=context_chars,
        latency_ms=entry["latency_ms"], truncated=truncated, cache=cache,
        **(usage or {}))
    return entry


def call_llm(messages: list, agent_type: str, max_tokens=4000, timeout=None,
             project_id: str = None, label: str = None, fast_mode: bool = False,
             use_cache: bool = True, mode: str = None) -> str:
    """LLM call with a classified-error retry policy over the provider chain.

    Per model: one same-model retry on 429 (after a per-tier wait) or timeout;
    auth errors fail over to the next tier immediately (retrying a bad key is
    pure waste); unclassified errors (404s, 5xx) also move to the next tier.
    Chain: primary -> fallback -> Ollama (when detected). Raises a typed
    LLMError subclass when the whole chain is exhausted.

    `project_id`/`label` are optional attribution passed straight through to the
    per-attempt record; callers that omit them still work, their rows just carry
    no project. Returns the content string — the signature stays compatible so
    all eleven existing call sites are untouched, and usage is captured as a
    side effect rather than by widening the return type.
    """
    primary, fallback = MODELS.get(agent_type, MODELS["research"])
    context_chars = sum(len(m.get("content") or "") for m in messages)
    mode = (mode or llm_mode())
    # cloud-only omits the local tier entirely, so a run that must not be
    # silently degraded pauses instead — that choice is the honest alternative
    # to hardcoding which agents are "too important" for local models.
    # prefer-local puts it first, making free unlimited iteration the default
    # path and cloud the safety net rather than the other way round.
    ollama = None if mode == "cloud-only" else get_ollama_model(agent_type)
    chain = [primary, fallback]
    if ollama:
        chain.insert(0, ollama) if mode == "prefer-local" else chain.append(ollama)
    base_timeout = timeout or AGENT_TIMEOUT_SECONDS.get(agent_type, DEFAULT_TIMEOUT_SECONDS)
    max_tokens = resolve_max_tokens(agent_type, max_tokens, fast_mode)

    # Before the cache and before the chain: an abandoned project should not
    # consume quota, a local slot, or a cache lookup. Raised (not returned
    # empty) so the worker unwinds instead of writing a placeholder file for a
    # project that no longer exists.
    if is_abandoned(project_id):
        raise LLMError(f"project {project_id} was cancelled or deleted "
                       f"while this call was queued", agent_type, "none")

    # Checked before the chain, not per tier: the cached value is the answer to
    # the question, so which tier would have produced it is irrelevant. Recorded
    # as an attempt with zero tokens so hit-rate and the tokens the cache SAVED
    # are both visible in the same table as everything else.
    cache_key = llm_cache.make_key(agent_type, messages, max_tokens) if use_cache else None
    if cache_key:
        hit = llm_cache.get(cache_key)
        if hit is not None:
            _log_attempt(agent_type, "cache", 1, "ok", time.monotonic(),
                         project_id=project_id, label=label,
                         context_chars=context_chars, cache="hit")
            return hit

    auth_failures = 0
    last_error = None

    for tier, model in enumerate(chain):
        # A provider whose daily allowance is gone will refuse every request
        # until UTC midnight, so sending one is a guaranteed-wasted round trip.
        # Skipping straight to the next tier converts that certain failure into
        # a call that can actually succeed.
        if budget_exhausted(model):
            started = time.monotonic()
            last_error = LLMRateLimitError(
                f"{model} skipped: daily token budget for "
                f"{_provider_of(model)} exhausted", agent_type, model)
            _log_attempt(agent_type, model, 1, "budget_exhausted", started,
                         project_id=project_id, label=label,
                         context_chars=context_chars)
            continue

        is_local = _provider_of(model) == "ollama"
        timeout = max(base_timeout, OLLAMA_TIMEOUT_FLOOR_SECONDS) if is_local else base_timeout

        for attempt in (1, 2):
            started = time.monotonic()
            try:
                injected = _fault_injection(agent_type, model)
                if injected is not None:
                    # Recorded like any other attempt (with null usage — no real
                    # provider replied) so the zero-cost fake-generator suite can
                    # exercise the metrics path without spending quota.
                    _log_attempt(agent_type, model, attempt, "ok", started,
                                 project_id=project_id, label=label,
                                 context_chars=context_chars)
                    return injected
                _pace(model)
                # Serialises local calls only. Cloud tiers pass straight through,
                # so the Day 20 pool still runs 3 cloud files at once and only
                # narrows to 1 for whichever workers fall through to Ollama.
                with (_ollama_sem if is_local else contextlib.nullcontext()):
                    resp = _traced_completion(
                        model=model,
                        messages=messages,
                        max_tokens=max_tokens,
                        temperature=0.3,
                        timeout=timeout,
                        # Sends local calls to the SAME daemon the probe found,
                        # so host (localhost) and the compose profile service
                        # (http://ollama:11434) both work off one env var
                        # instead of litellm's own hardcoded default.
                        # num_ctx is what stops ollama silently truncating the
                        # prompt to fit its 4k default — see ollama_num_ctx.
                        **({"api_base": OLLAMA_URL,
                            "num_ctx": ollama_num_ctx(context_chars, max_tokens)}
                           if is_local else {}),
                        # Filterable in the dashboard by project, agent and attempt.
                        # Ignored by the passthrough when tracing is off.
                        **({"langsmith_extra": {
                            "name": f"{agent_type}:{model}",
                            "tags": [f"agent:{agent_type}", f"model:{model}",
                                     f"attempt:{attempt}"]
                                    + ([f"project:{project_id}"] if project_id else []),
                            "metadata": {"agent": agent_type, "model": model,
                                         "attempt": attempt, "project_id": project_id,
                                         "label": label, "context_chars": context_chars},
                        }} if _traced_completion is not _plain_completion else {}),
                    )
                # finish_reason is the provider's own statement that it stopped
                # at the ceiling — exact, unlike comparing token counts to the
                # budget, which needs a fudge factor and still misses providers
                # that under-report usage.
                usage = _extract_usage(resp, model)
                _spend(model, usage)
                content = resp.choices[0].message.content or ""
                truncated = _finish_reason(resp) == "length"
                # A truncated completion is not cached: it would make one cut-off
                # document permanent and hide the defect behind a cache hit.
                if cache_key and not truncated:
                    llm_cache.put(cache_key, agent_type, content, project_id)
                _log_attempt(agent_type, model, attempt, "ok", started,
                             usage=usage, project_id=project_id,
                             label=label, context_chars=context_chars,
                             truncated=truncated,
                             cache="miss" if cache_key else None)
                return content
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
                _log_attempt(agent_type, model, attempt, "auth", started,
                             project_id=project_id, label=label,
                             context_chars=context_chars)
                break  # bad key never fixes itself — next tier immediately
            except Exception as e:
                last_error = LLMError(f"{model} failed: {e}", agent_type, model)
                _log_attempt(agent_type, model, attempt, "error", started,
                             project_id=project_id, label=label,
                             context_chars=context_chars)
                break  # unclassified (dead slug 404, 5xx, ...) — next tier
            _log_attempt(agent_type, model, attempt, outcome, started,
                         project_id=project_id, label=label,
                         context_chars=context_chars)
            if attempt == 1:
                if outcome == "rate_limit":
                    # Push the model's next slot out, then wait at least a full
                    # interval. The old flat 2s retried well inside the window
                    # that had just rejected us, so the retry was near-certain
                    # to fail too and cost the file its last attempt.
                    _penalise(model)
                    time.sleep(max(RATE_LIMIT_WAITS[min(tier, len(RATE_LIMIT_WAITS) - 1)],
                                   min_interval_for(model)))
                continue  # one same-model retry for 429/timeout
            break  # second failure on this model — next tier

    if auth_failures == len(chain):
        raise LLMAuthError(
            f"All providers rejected their API keys for agent={agent_type} — fix backend/.env",
            agent_type, ",".join(chain))
    raise last_error
