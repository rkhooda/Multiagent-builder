"""Day 29: local-tier detection and per-agent local model choice.
Zero API calls — the probe is stubbed, never dialled."""
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app import llm_router as R


def _serving(*names):
    """Pretend Ollama is serving exactly `names`, bypassing the HTTP probe."""
    R._ollama_cache.update(models=sorted(names), checked_at=time.monotonic())


def _reset():
    R._ollama_cache.update(models=[], checked_at=0.0)


# ── probe ────────────────────────────────────────────────────────────────────

def test_probe_returns_empty_when_daemon_absent():
    """Absence is the normal case on a machine without Ollama, and it must be
    silent — no raise, no retry storm — so the chain just ends a tier early."""
    _reset()
    original = R.OLLAMA_URL
    R.OLLAMA_URL = "http://127.0.0.1:1"          # nothing listens here
    try:
        assert R.ollama_models() == []
        assert R.get_ollama_model("research") is None
    finally:
        R.OLLAMA_URL = original
        _reset()


def test_probe_caches_then_reprobes_after_ttl():
    """A model pulled mid-session must become usable without a restart, which is
    the whole reason the probe is not a one-shot startup check."""
    _reset()
    _serving("phi4-mini:latest")
    assert R.ollama_models() == ["phi4-mini:latest"]   # served from cache
    # Expire the cache and point at a dead URL: a re-probe must actually happen.
    R._ollama_cache["checked_at"] = time.monotonic() - R.OLLAMA_PROBE_TTL_SECONDS - 1
    original = R.OLLAMA_URL
    R.OLLAMA_URL = "http://127.0.0.1:1"
    try:
        assert R.ollama_models() == [], "TTL expiry did not force a re-probe"
    finally:
        R.OLLAMA_URL = original
        _reset()


# ── per-agent assignment ─────────────────────────────────────────────────────

def test_coder_agents_get_the_coding_model_others_get_the_general_one():
    _reset()
    _serving("qwen2.5-coder:7b", "qwen3:4b", "phi4-mini:latest")
    assert R.get_ollama_model("backend_code") == "ollama/qwen2.5-coder:7b"
    assert R.get_ollama_model("frontend_code") == "ollama/qwen2.5-coder:7b"
    assert R.get_ollama_model("devops") == "ollama/qwen2.5-coder:7b"
    assert R.get_ollama_model("architecture") == "ollama/qwen3:4b"
    assert R.get_ollama_model("research") == "ollama/qwen3:4b"
    _reset()


def test_falls_to_next_best_when_preferred_model_is_not_pulled():
    """Daemon up but the ideal model missing is the common half-configured
    state. Routing to it anyway would surface Ollama's 404 as the chain's final
    error, which reads as a broken tier rather than an undownloaded model."""
    _reset()
    _serving("qwen3:4b", "phi4-mini:latest")     # no coder model
    assert R.get_ollama_model("backend_code") == "ollama/qwen3:4b"
    _serving("phi4-mini:latest")                 # only the floor remains
    assert R.get_ollama_model("backend_code") == "ollama/phi4-mini:latest"
    assert R.get_ollama_model("architecture") == "ollama/phi4-mini:latest"
    _reset()


def test_unknown_model_still_used_rather_than_refusing():
    """Something pulled but unrecognised beats nothing at all — the tier exists
    to keep the pipeline alive, not to enforce a curated model list."""
    _reset()
    _serving("mistral:7b")
    assert R.get_ollama_model("backend_code") == "ollama/mistral:7b"
    _reset()


def test_ollama_is_unmetered_and_unpaced():
    """Local has no daily allowance and no per-minute limit to respect — if it
    inherited either, the escape hatch would inherit the problem it escapes."""
    assert R.budget_exhausted("ollama/qwen3:4b") is False
    assert R.min_interval_for("ollama/qwen3:4b") == 0.0


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
