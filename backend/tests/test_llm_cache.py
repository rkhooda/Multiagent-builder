"""Day 26: response cache key + bypass correctness. Zero API calls.

The saving is easy; the correctness is the whole risk. These tests exist to
prove the bypass rules hold, because a cache that replays a stale answer when
the user asked for something different is a correctness bug, not a saving.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.agents.utils import build_feedback_prompt
from app.observability import llm_cache

MSGS = [{"role": "system", "content": "You are an architect."},
        {"role": "user", "content": "Design a notes app."}]


def _clear():
    llm_cache.clear()


def test_identical_input_hits():
    _clear()
    key = llm_cache.make_key("architecture", MSGS, 12000)
    assert llm_cache.get(key) is None, "cold cache must miss"
    llm_cache.put(key, "architecture", "# Architecture\n...", "p1")
    assert llm_cache.get(key) == "# Architecture\n..."
    _clear()


def test_key_ignores_the_model_so_a_failover_restart_still_hits():
    """The chain may answer from groq one run and gemini the next. The key must
    not encode that, or restart-from-stage misses exactly when it should hit."""
    a = llm_cache.make_key("architecture", MSGS, 12000)
    b = llm_cache.make_key("architecture", MSGS, 12000)
    assert a == b


def test_different_agent_does_not_collide():
    assert llm_cache.make_key("architecture", MSGS, 4000) != \
           llm_cache.make_key("research", MSGS, 4000)


def test_fast_mode_budget_does_not_share_with_normal_mode():
    """Halved max_tokens produces a shorter document; serving one for the other
    would silently mix the two modes' output."""
    assert llm_cache.make_key("architecture", MSGS, 12000) != \
           llm_cache.make_key("architecture", MSGS, 6000)


def test_feedback_regeneration_misses_naturally():
    """The gate 'edit'/'back' path must NOT be served the output it is trying to
    replace. Verified against the real feedback builder, not assumed."""
    _clear()
    original_key = llm_cache.make_key("architecture", MSGS, 12000)
    llm_cache.put(original_key, "architecture", "ORIGINAL", "p1")

    regen = MSGS[:-1] + [{
        "role": "user",
        "content": MSGS[-1]["content"] + "\n\n" + build_feedback_prompt(
            "ORIGINAL", "Use Postgres, not Mongo."),
    }]
    regen_key = llm_cache.make_key("architecture", regen, 12000)
    assert regen_key != original_key, "feedback regeneration would replay stale output"
    assert llm_cache.get(regen_key) is None
    _clear()


def test_different_feedback_produces_different_keys():
    base = MSGS[-1]["content"]
    k1 = llm_cache.make_key("architecture", [{"role": "user", "content": base + build_feedback_prompt("X", "Use Postgres.")}], 12000)
    k2 = llm_cache.make_key("architecture", [{"role": "user", "content": base + build_feedback_prompt("X", "Use MySQL.")}], 12000)
    assert k1 != k2


def test_empty_response_is_never_cached():
    """Day 18 saw a provider return "". Caching that makes one bad call permanent."""
    _clear()
    key = llm_cache.make_key("qa", MSGS, 3000)
    llm_cache.put(key, "qa", "   ", "p1")
    assert llm_cache.get(key) is None
    _clear()


def test_disabling_the_cache_forces_a_live_call():
    _clear()
    key = llm_cache.make_key("architecture", MSGS, 12000)
    llm_cache.put(key, "architecture", "CACHED", "p1")
    os.environ["LLM_CACHE"] = "false"
    try:
        assert llm_cache.get(key) is None
        assert not llm_cache.enabled()
    finally:
        del os.environ["LLM_CACHE"]
    assert llm_cache.get(key) == "CACHED"
    _clear()


def test_clear_is_scopable_to_one_project():
    _clear()
    k1 = llm_cache.make_key("architecture", MSGS, 1)
    k2 = llm_cache.make_key("architecture", MSGS, 2)
    llm_cache.put(k1, "architecture", "A", "p1")
    llm_cache.put(k2, "architecture", "B", "p2")
    llm_cache.clear("p1")
    assert llm_cache.get(k1) is None
    assert llm_cache.get(k2) == "B"
    _clear()


def test_hits_are_counted_for_the_hit_rate_line():
    _clear()
    key = llm_cache.make_key("research", MSGS, 4500)
    llm_cache.put(key, "research", "R", "p1")
    llm_cache.get(key)
    llm_cache.get(key)
    assert llm_cache.stats()["hits"] == 2
    _clear()


def _stub_provider():
    """Replace the completion call with a counter, so 'did this cost a request?'
    is answerable without spending quota."""
    from app import llm_router as R

    calls = []

    class _Msg:
        content = "GENERATED"

    class _Choice:
        finish_reason = "stop"
        message = _Msg()

    class _Usage:
        prompt_tokens, completion_tokens, total_tokens = 100, 50, 150

    class _Resp:
        choices = [_Choice()]
        usage = _Usage()

    def fake(**kwargs):
        calls.append(kwargs)
        return _Resp()

    original = R._traced_completion
    R._traced_completion = fake
    return R, calls, (lambda: setattr(R, "_traced_completion", original))


def test_end_to_end_second_identical_call_costs_no_request():
    """The headline saving: an unchanged upstream stage must not re-spend quota."""
    _clear()
    R, calls, restore = _stub_provider()
    os.environ["LLM_DAILY_TOKENS_GROQ"] = "999999"
    os.environ["LLM_DAILY_TOKENS_GEMINI"] = "999999"
    try:
        R._budget_day[0] = R._utc_day()
        R._spent_today.clear()
        R._next_allowed.clear()
        msgs = [{"role": "user", "content": "design a notes app"}]
        first = R.call_llm(msgs, "architecture", max_tokens=12000, project_id="e2e")
        assert first == "GENERATED" and len(calls) == 1

        second = R.call_llm(msgs, "architecture", max_tokens=12000, project_id="e2e")
        assert second == "GENERATED"
        assert len(calls) == 1, "cache hit still issued a provider request"
    finally:
        restore()
        del os.environ["LLM_DAILY_TOKENS_GROQ"], os.environ["LLM_DAILY_TOKENS_GEMINI"]
        R._spent_today.clear()
        _clear()


def test_end_to_end_use_cache_false_always_spends():
    """The 'Request AI Fix' bypass — same instruction must reach the provider."""
    _clear()
    R, calls, restore = _stub_provider()
    os.environ["LLM_DAILY_TOKENS_GROQ"] = "999999"
    os.environ["LLM_DAILY_TOKENS_GEMINI"] = "999999"
    try:
        R._budget_day[0] = R._utc_day()
        R._spent_today.clear()
        R._next_allowed.clear()
        msgs = [{"role": "user", "content": "fix this file"}]
        R.call_llm(msgs, "frontend_code", max_tokens=4000, use_cache=False)
        R.call_llm(msgs, "frontend_code", max_tokens=4000, use_cache=False)
        assert len(calls) == 2, "bypass replayed a cached answer"
    finally:
        restore()
        del os.environ["LLM_DAILY_TOKENS_GROQ"], os.environ["LLM_DAILY_TOKENS_GEMINI"]
        R._spent_today.clear()
        _clear()


def test_end_to_end_restart_from_stage_hits_every_unchanged_upstream_agent():
    """Restart-from-code_generation: research/requirements/architecture/planning
    inputs are unchanged, so all four must come from cache and cost nothing."""
    _clear()
    R, calls, restore = _stub_provider()
    os.environ["LLM_DAILY_TOKENS_GROQ"] = "999999"
    os.environ["LLM_DAILY_TOKENS_GEMINI"] = "999999"
    upstream = [("research", 4500), ("requirements", 4500),
                ("architecture", 12000), ("planning", 9000)]
    try:
        R._budget_day[0] = R._utc_day()
        R._spent_today.clear()
        R._next_allowed.clear()
        for agent, budget in upstream:                      # first run
            R.call_llm([{"role": "user", "content": f"{agent} work"}],
                       agent, max_tokens=budget, project_id="restart")
        assert len(calls) == 4
        for agent, budget in upstream:                      # restart
            R.call_llm([{"role": "user", "content": f"{agent} work"}],
                       agent, max_tokens=budget, project_id="restart")
        assert len(calls) == 4, f"restart re-spent {len(calls) - 4} calls"
    finally:
        restore()
        del os.environ["LLM_DAILY_TOKENS_GROQ"], os.environ["LLM_DAILY_TOKENS_GEMINI"]
        R._spent_today.clear()
        _clear()


if __name__ == "__main__":
    passed = failed = 0
    for name, fn in sorted(globals().items()):
        if not name.startswith("test_") or not callable(fn):
            continue
        try:
            fn()
            print(f"  ok   {name}")
            passed += 1
        except Exception as e:                  # noqa: BLE001
            print(f"  FAIL {name}: {type(e).__name__}: {e}")
            failed += 1
    print(f"\n{passed} passed, {failed} failed. (0 API calls)")
    sys.exit(1 if failed else 0)
