"""Day 26: pacing, adaptive backoff and daily token budget. Zero API calls."""
import os
import sys
import threading
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app import llm_router as R


def _reset():
    R._backoff_factor.clear()
    R._next_allowed.clear()
    R._spent_today.clear()
    R._budget_day[0] = R._utc_day()             # seeded: skip the metrics.db read


def test_pacing_spaces_concurrent_workers_into_distinct_slots():
    """The coder pool fires N calls at once. They must queue into separate slots,
    not all sleep the same interval and stampede together afterwards."""
    _reset()
    os.environ["LLM_MIN_INTERVAL_GEMINI"] = "0.2"
    try:
        fired = []
        lock = threading.Lock()

        def worker():
            R._pace("gemini/gemini-2.5-flash")
            with lock:
                fired.append(time.monotonic())

        threads = [threading.Thread(target=worker) for _ in range(4)]
        start = time.monotonic()
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        fired.sort()
        gaps = [b - a for a, b in zip(fired, fired[1:])]
        assert all(g > 0.15 for g in gaps), f"workers bunched up: {gaps}"
        assert fired[-1] - start >= 0.55, "four calls went out faster than the pace"
    finally:
        del os.environ["LLM_MIN_INTERVAL_GEMINI"]
        _reset()


def test_a_429_widens_the_interval_not_just_the_next_slot():
    """A 429 despite pacing means the configured interval was too optimistic."""
    _reset()
    os.environ["LLM_MIN_INTERVAL_GEMINI"] = "1.0"
    try:
        model = "gemini/gemini-2.5-flash"
        assert R.min_interval_for(model) == 1.0
        R._penalise(model)
        assert R.min_interval_for(model) == 1.5
        R._penalise(model)
        assert R.min_interval_for(model) == 2.25
    finally:
        del os.environ["LLM_MIN_INTERVAL_GEMINI"]
        _reset()


def test_learned_backoff_is_capped():
    """A burst of 429s must not stall a model for the rest of the run."""
    _reset()
    os.environ["LLM_MIN_INTERVAL_GEMINI"] = "1.0"
    try:
        model = "gemini/gemini-2.5-flash"
        for _ in range(50):
            R._penalise(model)
        assert R.min_interval_for(model) == R._MAX_BACKOFF_FACTOR
    finally:
        del os.environ["LLM_MIN_INTERVAL_GEMINI"]
        _reset()


def test_backoff_is_per_model_not_global():
    """Providers enforce per model, so one model's 429 must not slow another."""
    _reset()
    os.environ["LLM_MIN_INTERVAL_GEMINI"] = "1.0"
    os.environ["LLM_MIN_INTERVAL_GROQ"] = "1.0"
    try:
        R._penalise("groq/llama-3.3-70b-versatile")
        assert R.min_interval_for("groq/llama-3.3-70b-versatile") == 1.5
        assert R.min_interval_for("gemini/gemini-2.5-flash") == 1.0
    finally:
        del os.environ["LLM_MIN_INTERVAL_GEMINI"]
        del os.environ["LLM_MIN_INTERVAL_GROQ"]
        _reset()


def test_budget_blocks_only_once_the_allowance_is_gone():
    _reset()
    os.environ["LLM_DAILY_TOKENS_GROQ"] = "1000"
    try:
        model = "groq/llama-3.3-70b-versatile"
        assert not R.budget_exhausted(model)
        R._spend(model, {"prompt_tokens": 600, "completion_tokens": 300})
        assert not R.budget_exhausted(model), "900 of 1000 must still be allowed"
        R._spend(model, {"prompt_tokens": 200, "completion_tokens": 0})
        assert R.budget_exhausted(model), "1100 of 1000 must block"
    finally:
        del os.environ["LLM_DAILY_TOKENS_GROQ"]
        _reset()


def test_budget_is_per_provider():
    """Exhausting groq must not block the gemini failover — that failover is the
    entire reason the budget check exists."""
    _reset()
    os.environ["LLM_DAILY_TOKENS_GROQ"] = "100"
    try:
        R._spend("groq/llama-3.3-70b-versatile", {"prompt_tokens": 500})
        assert R.budget_exhausted("groq/llama-3.3-70b-versatile")
        assert not R.budget_exhausted("gemini/gemini-2.5-flash")
    finally:
        del os.environ["LLM_DAILY_TOKENS_GROQ"]
        _reset()


def test_untracked_provider_never_blocks():
    """limit 0 means 'not token-metered' (openrouter caps requests, not tokens)
    and must never gate a call."""
    _reset()
    R._spend("openrouter/some/model", {"prompt_tokens": 10_000_000})
    assert not R.budget_exhausted("openrouter/some/model")
    assert not R.budget_exhausted("ollama/qwen3:14b")
    _reset()


def test_spend_accumulates_across_threads():
    """Coder workers spend concurrently; a lost update under-counts the budget,
    which is the dangerous direction."""
    _reset()
    os.environ["LLM_DAILY_TOKENS_GROQ"] = "100000"
    try:
        model = "groq/llama-3.3-70b-versatile"

        def worker():
            for _ in range(100):
                R._spend(model, {"prompt_tokens": 1, "completion_tokens": 1})

        threads = [threading.Thread(target=worker) for _ in range(8)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        assert R._spent_today["groq"] == 1600, R._spent_today
    finally:
        del os.environ["LLM_DAILY_TOKENS_GROQ"]
        _reset()


def test_report_shows_remaining_allowance():
    _reset()
    os.environ["LLM_DAILY_TOKENS_GROQ"] = "1000"
    try:
        R._spend("groq/llama-3.3-70b-versatile", {"prompt_tokens": 250})
        row = next(r for r in R.daily_budget_report() if r["provider"] == "groq")
        assert row["used"] == 250 and row["remaining"] == 750
        assert row["pct_used"] == 25.0 and row["tracked"] is True
        orow = next(r for r in R.daily_budget_report() if r["provider"] == "openrouter")
        assert orow["tracked"] is False and orow["remaining"] is None
    finally:
        del os.environ["LLM_DAILY_TOKENS_GROQ"]
        _reset()


# ── Failover accounting (ceiling audit, 2026-08-03) ─────────────────────────
# QA starved on its primary for weeks and every failover silently spent the
# scarce groq pool; these pin that a call succeeding off its first-choice tier
# is counted, with its cause and (for scarce destinations) its token cost.

class _Msg:
    content = "GENERATED"


class _FoChoice:
    finish_reason = "stop"
    message = _Msg()


class _FoUsage:
    prompt_tokens, completion_tokens, total_tokens = 100, 50, 150


class _FoResp:
    choices = [_FoChoice()]
    usage = _FoUsage()


def _stub_chain(script):
    """Fake completion consuming one scripted behaviour per call:
    'ok' returns a response, '429' rate-limits, 'err' raises unclassified."""
    def fake(**kwargs):
        step = script.pop(0)
        if step == "429":
            raise R.llm_exc.RateLimitError(
                message="stub 429", llm_provider="stub", model=kwargs["model"])
        if step == "err":
            raise RuntimeError("stub provider exploded")
        return _FoResp()
    original = R._traced_completion
    R._traced_completion = fake
    return lambda: setattr(R, "_traced_completion", original)


def _failover_env():
    os.environ["OBSERVABILITY_ENABLED"] = "false"   # keep stub rows out of metrics.db
    os.environ["LLM_MIN_INTERVAL_GROQ"] = "0"
    os.environ["LLM_MIN_INTERVAL_GEMINI"] = "0"


def _failover_env_clear():
    for k in ("OBSERVABILITY_ENABLED", "LLM_MIN_INTERVAL_GROQ", "LLM_MIN_INTERVAL_GEMINI"):
        os.environ.pop(k, None)


def test_failover_onto_scarce_pool_records_cause_and_tokens():
    """qa: gemini primary errors, groq (scarce) rescues — the QA defect shape.
    The count carries the cause; the token entry carries the cost."""
    _reset()
    _failover_env()
    from app.observability import degraded
    restore = _stub_chain(["err", "ok"])
    try:
        out = R.call_llm([{"role": "user", "content": "x"}], "qa",
                         project_id="fo-scarce", use_cache=False, mode="cloud-only")
        assert out == "GENERATED"
        events = degraded.drain("fo-scarce")
        assert events.get("failover:error:gemini->groq") == 1
        assert events.get("failover_scarce_tokens:groq") == 150
    finally:
        restore()
        _failover_env_clear()
        _reset()


def test_failover_to_non_scarce_pool_has_no_token_entry():
    _reset()
    _failover_env()
    from app.observability import degraded
    restore = _stub_chain(["err", "ok"])
    try:
        R.call_llm([{"role": "user", "content": "x"}], "frontend_code",
                   project_id="fo-plain", use_cache=False, mode="cloud-only")
        events = degraded.drain("fo-plain")
        assert events.get("failover:error:groq->gemini") == 1
        assert not any(k.startswith("failover_scarce_tokens") for k in events)
    finally:
        restore()
        _failover_env_clear()
        _reset()


def test_rate_limit_failover_is_distinguishable_by_cause():
    """A 429-driven fallback is the Day 17 design working — counted, but its
    key must say so, or the counter trains people to ignore it."""
    _reset()
    _failover_env()
    from app.observability import degraded
    restore = _stub_chain(["429", "429", "ok"])
    try:
        R.call_llm([{"role": "user", "content": "x"}], "frontend_code",
                   project_id="fo-429", use_cache=False, mode="cloud-only")
        events = degraded.drain("fo-429")
        assert events.get("failover:rate_limit:groq->gemini") == 1
    finally:
        restore()
        _failover_env_clear()
        _reset()


def test_primary_success_records_no_failover():
    _reset()
    _failover_env()
    from app.observability import degraded
    restore = _stub_chain(["ok"])
    try:
        R.call_llm([{"role": "user", "content": "x"}], "qa",
                   project_id="fo-clean", use_cache=False, mode="cloud-only")
        assert degraded.drain("fo-clean") == {}
    finally:
        restore()
        _failover_env_clear()
        _reset()


def test_broken_accounting_cannot_break_a_call():
    """The guard, proven: even a raising collector must not fail the call."""
    _reset()
    _failover_env()
    from app.observability import degraded

    def boom(*args, **kwargs):
        raise RuntimeError("accounting exploded")

    original = degraded.record
    degraded.record = boom
    restore = _stub_chain(["err", "ok"])
    try:
        out = R.call_llm([{"role": "user", "content": "x"}], "qa",
                         project_id="fo-boom", use_cache=False, mode="cloud-only")
        assert out == "GENERATED"
    finally:
        degraded.record = original
        restore()
        _failover_env_clear()
        _reset()


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
