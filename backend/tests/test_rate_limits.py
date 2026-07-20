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
