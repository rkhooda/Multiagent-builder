"""Day 29: prove the local tier serves every agent when cloud is unavailable.

Cloud is disabled by EXHAUSTING each provider's daily token budget rather than
by breaking API keys. That is deliberate on both counts:

  * it costs nothing — budget_exhausted() is checked before the request is
    built, so not one cloud call leaves the machine;
  * it exercises the trigger that actually stops this pipeline in practice. A
    spent daily allowance is the common case; a 429 that survives the pacer is
    the rare one. Testing the rare path would prove the wrong thing.

Run:  python3 backend/scripts/local_tier_check.py [agent ...]
"""
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

os.environ["LLM_CACHE"] = "false"          # a cache hit would prove nothing

from app import llm_router as R            # noqa: E402

# DERIVED, not listed. The hardcoded tuple this replaces was
# ("gemini", "groq", "openrouter") and had silently missed nvidia_nim since the
# deep tiers landed on 2026-08-06, so "cloud disabled" was not true: the chain
# reached NVIDIA and never fell through to local at all. A list of providers
# maintained beside the router's own list is a standing drift bug, and this one
# was already drifting — so take the router's tables as the source of truth.
# Union of both, because a provider may appear in one and not the other.
CLOUD_PROVIDERS = tuple(sorted(
    (set(R._DAILY_TOKEN_LIMITS) | set(R._PACE_DEFAULTS)) - {"ollama"}))
OUT_DIR = os.getenv("LOCAL_CHECK_OUT_DIR")     # set to keep the generated text


def exhaust_cloud_budgets():
    """Put every cloud provider in the state it reaches late in a free-tier day.

    Setting LLM_DAILY_TOKENS_* alone does NOT do this, which is worth stating
    because it is the obvious-looking approach and it silently fails: the
    counter seeds from what has ACTUALLY been spent today, so a limit of 1 still
    lets the first call through and only blocks the ones after it. Measured —
    the first version of this script let groq serve a real request while
    reporting the call as local.

    Marking the spend directly is both truthful to the scenario (the allowance
    is already gone) and airtight from call zero.
    """
    for provider in CLOUD_PROVIDERS:
        os.environ[f"LLM_DAILY_TOKENS_{provider.upper()}"] = "1"
    with R._budget_lock:
        R._budget_day[0] = R._utc_day()        # pin: block a reseed from metrics.db
        R._spent_today.update({p: 10 ** 9 for p in CLOUD_PROVIDERS})
    unblocked = [p for p in CLOUD_PROVIDERS
                 if not R.budget_exhausted(f"{p}/probe")]
    assert not unblocked, f"cloud still reachable: {unblocked}"


def served_by(agent):
    """The model that ACTUALLY answered, read back from metrics.

    Never report the model this agent WOULD route to — that is a prediction, and
    printing it beside a real call makes a cloud response look local.
    """
    rows = R.metrics_store._query(
        "SELECT model FROM agent_runs WHERE project_id = ? AND agent = ?"
        " AND outcome = 'ok' ORDER BY id DESC LIMIT 1", ("local-tier-check", agent))
    return rows[0]["model"] if rows else "?"

# One short, agent-appropriate prompt each. Kept tiny on purpose: this checks
# ROUTING, not output quality — quality is what the full pipeline run measures.
PROMPTS = {
    "research":      "Name three competitors to a note-taking app. One line each.",
    "requirements":  "Write three user stories for a note-taking app.",
    "architecture":  "Name the components of a React + FastAPI note app. One line each.",
    "planning":      "List four files needed for a note-taking API. Filenames only.",
    "frontend_code": "Write a React component that renders a list of note titles.",
    "backend_code":  "Write a FastAPI route that returns a list of notes.",
    "database":      "Write a SQL CREATE TABLE for notes (id, title, body, created_at).",
    "qa":            "List three things to test in a note-taking app.",
    "devops":        "Write a Dockerfile for a FastAPI app on python:3.11-slim.",
}


def main(agents):
    available = R.ollama_models()
    print(f"Ollama at {R.OLLAMA_URL}: "
          f"{', '.join(available) if available else 'NOTHING SERVED'}")
    if not available:
        print("No local models pulled — the chain would pause here, which is the "
              "correct pre-Ollama behaviour but proves nothing. Pull a model first.")
        return 1

    # Start from a clean slate so a previous run's rows cannot be mistaken for
    # this one's evidence.
    R.metrics_store.delete_project_metrics("local-tier-check")
    exhaust_cloud_budgets()
    print(f"cloud disabled: {', '.join(CLOUD_PROVIDERS)} daily budgets marked spent\n")

    results, failures = [], 0
    for agent in agents:
        started = time.monotonic()
        try:
            out = R.call_llm([{"role": "user", "content": PROMPTS[agent]}],
                             agent_type=agent, max_tokens=400,
                             project_id="local-tier-check", use_cache=False)
            elapsed = time.monotonic() - started
            model = served_by(agent)
            ok = bool(out and out.strip()) and model.startswith("ollama/")
            if OUT_DIR:
                # Routing correctness is not usability. Which agents produce
                # something worth keeping on a small local model is a judgement
                # that needs the actual text in front of you.
                with open(os.path.join(OUT_DIR, f"{agent}.txt"), "w") as fh:
                    fh.write(f"# {agent} via {model} in {elapsed:.1f}s\n\n{out or ''}")
            results.append((agent, model, elapsed, len(out or ""), ok))
            print(f"  {'ok  ' if ok else 'BAD '} {agent:<14} {model:<28} "
                  f"{elapsed:6.1f}s  {len(out or ''):>5} chars")
            if not ok:
                failures += 1
        except Exception as e:                  # noqa: BLE001
            elapsed = time.monotonic() - started
            results.append((agent, "-", elapsed, 0, False))
            failures += 1
            print(f"  FAIL {agent:<14} {'-':<28} {elapsed:6.1f}s  {type(e).__name__}: {e}")

    # The routing claim is only proven if the RECORDED tier is local — reading it
    # back from metrics is what the UI does, so this checks the same source.
    usage = R.metrics_store.local_tier_usage("local-tier-check")
    total = sum(r[2] for r in results)
    print(f"\n{len(results) - failures}/{len(results)} agents served locally, "
          f"{total:.0f}s total ({total / max(len(results), 1):.0f}s avg)")
    print(f"metrics attribution: {usage['local_calls']}/{usage['calls']} calls local "
          f"via {', '.join(usage['models']) or 'none'}")
    if usage["calls"] and usage["local_calls"] != usage["calls"]:
        print("WARNING: a call was served by a cloud tier — budgets did not hold")
        failures += 1
    return 1 if failures else 0


if __name__ == "__main__":
    picked = [a for a in sys.argv[1:] if a in PROMPTS] or list(PROMPTS)
    sys.exit(main(picked))
