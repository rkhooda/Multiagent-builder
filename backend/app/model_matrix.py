"""Admission control for expansion models, read from the probed matrix.

THE RULE THIS ENFORCES: no expansion model routes traffic without a row in
`config/model_capabilities.json` proving it was contacted and passed the
contract its agent needs. A key in .env is not evidence; a model that fences its
output writes files that do not parse, and it will do that on real work exactly
as it did on the probe.

WHAT THIS DOES NOT GOVERN. The incumbents (groq, gemini, the NVIDIA and
OpenRouter tiers) keep their hardcoded routing. Their evidence is daily
production use recorded in metrics.db plus the dated docs/PROVIDERS.md, which is
strictly stronger than a single probe — and making the working chain contingent
on a JSON file would mean one bad file deletes the pipeline. The matrix is a
gate on ADDING capacity, not a licence to remove it.

FAIL-OPEN, DELIBERATELY. A missing, unreadable or empty matrix yields no models,
so build_chain() produces exactly the chain it produced before this module
existed. A capability file that fails to load must cost depth, never the run.

ponytail: a module-level dict loaded once at import. No service, no cache
invalidation, no network at boot — the file is committed and changes only when
someone re-runs the probe.
"""
import json
import os

MATRIX_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                           "config", "model_capabilities.json")

# Per-provider depth cap inside the expansion tier.
#
# WHY a cap at all: the probe admitted 13 Mistral models, and appending all 13
# would be false depth. They share ONE account and ONE rate limit, so a
# provider-wide 429 costs thirteen round trips to discover — cooldown is per
# MODEL — while adding no capacity whatsoever, because the pool being exhausted
# is the account's, not the model's. Three is enough for a bad slug or a
# per-model limit to be routed around, and past that every extra entry is
# latency paid for nothing. Real depth comes from another PROVIDER, not another
# model on the same one.
MAX_MODELS_PER_PROVIDER = 3

# Prompt-size estimate for the context-window filter. Deliberately pessimistic
# (litellm reports ~4.9 chars/token on this pipeline's prompts): over-estimating
# skips a model that would have fitted, which costs one tier of depth, while
# under-estimating sends a prompt that overflows and returns an error — the
# expensive direction, and the one the filter exists to prevent.
CHARS_PER_TOKEN = 3.0

# Agents barred from the expansion tier because their OUTPUT ceiling is already
# saturated by the incumbent, so a more verbose model cannot fit inside it.
#
# Measured 2026-08-11 by the probe's fixed sizing task. Every admitted Mistral
# model needs 0.91-1.17x what groq needs for the same file — near parity, and
# comfortably inside every agent ceiling except one:
#
#   architecture   ceiling 12,000, measured requirement 11,996 (99.97% — it is
#                  AT the wall on groq today). At 1.17x it would need ~14,035
#                  and truncate by ~2,000 tokens.
#
# Truncation there is the expensive kind: the architecture document is the input
# to planning, and at a gemini-shaped ceiling it presents as an error plus a
# silent failover rather than as visible truncation. So architecture keeps its
# incumbent chain, and depth for it must come from raising the ceiling on
# measured evidence — not from quietly routing it somewhere it does not fit.
#
# This is the quality floor applied to token budgets rather than to output
# contracts: the same rule that "never stalls" must not become "never stops
# producing garbage". Pinned by test_token_budgets.
# test_expansion_models_fit_every_agent_ceiling, which recomputes the arithmetic
# from the live ceilings and fails if this set is ever stale in EITHER direction.
CEILING_SATURATED_AGENTS = {"architecture"}


def _load() -> dict:
    try:
        with open(MATRIX_PATH) as fh:
            return json.load(fh)
    except FileNotFoundError:
        return {}
    except Exception as e:                       # noqa: BLE001 — never block startup
        print(f"[Matrix] {MATRIX_PATH} unreadable, expansion tier disabled: {e}",
              flush=True)
        return {}


_MATRIX = _load()
# Only rows the probe ADMITTED, and only expansion rows: incumbent rows exist in
# the file to supply the verbosity baseline, not to be routed from here.
_ROWS = [r for r in _MATRIX.get("models", [])
         if r.get("admitted") and r.get("role") != "incumbent"]
_BY_MODEL = {r["model"]: r for r in _ROWS}


def probed_at() -> str:
    return _MATRIX.get("probed_at", "")


def _key_configured(provider: str) -> bool:
    """Same convention every provider in this project follows. An absent key
    contributes nothing, exactly as NVIDIA and OpenRouter already behave."""
    return bool(os.getenv(f"{provider.upper()}_API_KEY"))


def _rank(row: dict) -> tuple:
    """Both contracts before one, then fastest. A model that satisfies code AND
    JSON is usable by more of the pipeline for the same slot, and latency is the
    only other property here that was actually measured rather than assumed."""
    both = row.get("contract_code") and row.get("contract_json")
    # Explicit None check, not `or`: a measured 0 is a measurement and must sort
    # first, whereas `x or default` silently demotes it to "unknown".
    latency = row.get("latency_ms")
    return (not both, 10 ** 9 if latency is None else latency)


def models_for(agent_type: str) -> list:
    """Admitted models this agent may use, best first, capped per provider."""
    if agent_type in CEILING_SATURATED_AGENTS:
        return []
    eligible = [r for r in _ROWS
                if agent_type in (r.get("agents") or []) and _key_configured(r["provider"])]
    eligible.sort(key=_rank)
    out, seen = [], {}
    for row in eligible:
        provider = row["provider"]
        if seen.get(provider, 0) >= MAX_MODELS_PER_PROVIDER:
            continue
        seen[provider] = seen.get(provider, 0) + 1
        out.append(row["model"])
    return out


def context_window(model: str) -> int:
    """Input-token capacity, or 0 when unknown.

    The MATRIX WINS over litellm.get_model_info: the probe contacted the model,
    the mapping did not, and litellm has no entry at all for several live slugs
    (cerebras/qwen-3-coder-480b raises "This model isn't mapped yet"). 0 means
    unknown, and unknown must not be treated as small — an unmapped model is
    filtered on nothing rather than skipped on a guess.
    """
    row = _BY_MODEL.get(model)
    if row and row.get("max_input_tokens"):
        return int(row["max_input_tokens"])
    try:
        import litellm
        return int(litellm.get_model_info(model).get("max_input_tokens") or 0)
    except Exception:                            # noqa: BLE001 — unmapped is normal
        return 0


def fits_context(model: str, context_chars: int, max_tokens: int) -> bool:
    """Whether this model can hold the prompt AND the requested output.

    Checked BEFORE the request rather than discovering the overflow as an error,
    because an overflow is not a rate limit: waiting does not fix it, and the
    round trip is pure loss. Both halves count — a 8k window asked for 4k of
    output has only 4k left for the prompt, which is the mistake the Ollama
    num_ctx work already paid for once.

    Unknown windows return True. Never skip a model on an absence of evidence:
    that is how a working tier silently disappears.
    """
    window = context_window(model)
    if not window:
        return True
    needed = int(context_chars / CHARS_PER_TOKEN) + (max_tokens or 0)
    return needed <= window


def report() -> dict:
    """Provenance for the metrics panel and the run summary."""
    providers = {}
    for row in _ROWS:
        providers.setdefault(row["provider"], {"admitted": 0, "key_configured":
                                               _key_configured(row["provider"])})
        providers[row["provider"]]["admitted"] += 1
    return {
        "probed_at": probed_at(),
        "path": MATRIX_PATH,
        "admitted_models": len(_ROWS),
        "probed_models": len(_MATRIX.get("models", [])),
        "providers": providers,
        "baseline_model": _MATRIX.get("baseline_model"),
        "baseline_output_tokens": _MATRIX.get("baseline_output_tokens"),
    }
