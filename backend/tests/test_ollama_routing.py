"""Day 29: local-tier detection, per-agent model choice, chain placement and the
local concurrency cap. Zero API calls — the probe is stubbed, never dialled."""
import contextlib
import io
import json
import os
import sys
import threading
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app import llm_router as R


def _serving(*names):
    """Pretend Ollama is serving exactly `names`, bypassing the HTTP probe."""
    R._ollama_cache.update(models=sorted(names), checked_at=time.monotonic())


def _absent():
    R._ollama_cache.update(models=[], checked_at=time.monotonic())


def _reset():
    R._ollama_cache.update(models=[], checked_at=0.0)
    os.environ.pop("LLM_MODE", None)


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


def _expire():
    R._ollama_cache["checked_at"] = time.monotonic() - R.OLLAMA_PROBE_TTL_SECONDS - 1


@contextlib.contextmanager
def _daemon_returning(payload):
    """Stub the HTTP probe without opening a socket."""
    original = R.urllib.request.urlopen

    @contextlib.contextmanager
    def fake(url, timeout=None):
        yield io.BytesIO(json.dumps(payload).encode())

    R.urllib.request.urlopen = fake
    try:
        yield
    finally:
        R.urllib.request.urlopen = original


def test_probe_picks_up_a_model_pulled_mid_session():
    """The reason the probe is not a one-shot startup check: pulling a model
    while the server runs must make it usable without a restart."""
    _reset()
    with _daemon_returning({"models": [{"name": "phi4-mini:latest"}]}):
        assert R.ollama_models() == ["phi4-mini:latest"]
        _expire()
        with _daemon_returning({"models": [{"name": "phi4-mini:latest"},
                                           {"name": "qwen3:4b"}]}):
            assert R.ollama_models() == ["phi4-mini:latest", "qwen3:4b"]
    _reset()


def test_probe_retains_known_models_when_a_reprobe_fails():
    """Regression, Day 29: a 1s probe timeout made the tier vanish under its own
    load. A re-probe landing during a 101s local completion timed out, the model
    list came back empty, and the next agent skipped a working Ollama entirely
    and raised. A failed re-probe must mean "busy", not "gone"."""
    _reset()
    _serving("phi4-mini:latest")
    _expire()
    original = R.OLLAMA_URL
    R.OLLAMA_URL = "http://127.0.0.1:1"          # probe cannot succeed
    try:
        assert R.ollama_models() == ["phi4-mini:latest"], \
            "a failed re-probe erased a known-good local tier"
        assert R.get_ollama_model("database") == "ollama/phi4-mini:latest"
    finally:
        R.OLLAMA_URL = original
        _reset()


def test_probe_timeout_survives_a_busy_daemon():
    """The floor exists because the probe competes with our own generation."""
    assert R.OLLAMA_PROBE_TIMEOUT_SECONDS >= 3


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


# ── chain placement / LLM_MODE ───────────────────────────────────────────────

def _chain_for(agent_type, mode):
    """Rebuild the tier list exactly as call_llm does, without calling out."""
    primary, fallback = R.MODELS[agent_type]
    ollama = None if mode == "cloud-only" else R.get_ollama_model(agent_type)
    chain = [primary, fallback]
    if ollama:
        chain.insert(0, ollama) if mode == "prefer-local" else chain.append(ollama)
    return chain


def test_auto_appends_local_as_last_resort():
    _reset()
    _serving("qwen3:4b")
    assert _chain_for("research", "auto")[-1] == "ollama/qwen3:4b"
    assert len(_chain_for("research", "auto")) == 3
    _reset()


def test_cloud_only_omits_local_even_when_available():
    """The honest alternative to hardcoding which agents are 'too important' for
    local: the user says so per run, and the chain pauses instead of degrading."""
    _reset()
    _serving("qwen3:4b", "qwen2.5-coder:7b")
    chain = _chain_for("architecture", "cloud-only")
    assert len(chain) == 2 and not any(m.startswith("ollama/") for m in chain)
    _reset()


def test_prefer_local_puts_local_first_with_cloud_still_behind_it():
    _reset()
    _serving("qwen2.5-coder:7b")
    chain = _chain_for("backend_code", "prefer-local")
    assert chain[0] == "ollama/qwen2.5-coder:7b"
    assert len(chain) == 3, "cloud must remain as the safety net, not be removed"
    _reset()


def test_absent_ollama_leaves_the_pre_ollama_chain_untouched():
    """Local is a new tier, not a replacement for the pause-as-last-resort."""
    _reset()
    _absent()
    for mode in ("auto", "prefer-local", "cloud-only"):
        assert _chain_for("qa", mode) == list(R.MODELS["qa"])
    _reset()


def test_llm_mode_rejects_junk_and_defaults_to_auto():
    _reset()
    os.environ["LLM_MODE"] = "banana"
    assert R.llm_mode() == "auto"
    os.environ["LLM_MODE"] = "PREFER-LOCAL"      # case/whitespace tolerant
    assert R.llm_mode() == "prefer-local"
    _reset()
    assert R.llm_mode() == "auto"


# ── local concurrency cap ────────────────────────────────────────────────────

def test_local_semaphore_serialises_but_cloud_is_untouched():
    """The cap is a hardware limit, not a rate limit: two 5GB models cannot be
    resident at once on 8GB. Cloud calls must not pay for that."""
    peak = [0]
    live = [0]
    lock = threading.Lock()

    def work(is_local):
        with (R._ollama_sem if is_local else __import__("contextlib").nullcontext()):
            with lock:
                live[0] += 1
                peak[0] = max(peak[0], live[0])
            time.sleep(0.05)
            with lock:
                live[0] -= 1

    threads = [threading.Thread(target=work, args=(True,)) for _ in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert peak[0] <= R.OLLAMA_MAX_CONCURRENT, f"local peak {peak[0]} exceeded cap"

    peak[0] = live[0] = 0
    threads = [threading.Thread(target=work, args=(False,)) for _ in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert peak[0] == 4, "cloud calls were serialised by the local cap"


def test_local_tier_gets_a_timeout_floor():
    """A 7B model on an M1 emits tens of tokens/sec; the cloud budgets would
    abort correct work in progress."""
    assert R.OLLAMA_TIMEOUT_FLOOR_SECONDS > max(R.AGENT_TIMEOUT_SECONDS.values())
    assert R.OLLAMA_TIMEOUT_FLOOR_SECONDS > R.DEFAULT_TIMEOUT_SECONDS


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
