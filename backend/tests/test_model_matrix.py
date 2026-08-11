"""Provider expansion (2026-08-11): matrix admission, the per-provider depth
cap, the context-window pre-flight filter and ROUTING_MODE.

Zero API calls — every row is a fixture. The point of the matrix is that a model
is admitted on EVIDENCE, so a test that needed the network to check admission
would be testing the provider rather than the gate.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app import llm_router as R                                    # noqa: E402
from app import model_matrix as M                                  # noqa: E402

CODE_ONLY = {"model": "fake/coder", "provider": "fake", "admitted": True,
             "role": "expansion", "latency_ms": 100, "max_input_tokens": 8192,
             "contract_code": True, "contract_json": False,
             "agents": list(R.CODE_AGENTS)}
BOTH = {"model": "fake/both", "provider": "fake", "admitted": True,
        "role": "expansion", "latency_ms": 900, "max_input_tokens": 32000,
        "contract_code": True, "contract_json": True,
        "agents": list(R.CODE_AGENTS) + list(R.PROSE_AGENTS)}
NO_WINDOW = {"model": "fake/unmapped", "provider": "fake", "admitted": True,
             "role": "expansion", "latency_ms": 200, "max_input_tokens": None,
             "contract_code": True, "contract_json": True,
             "agents": list(R.CODE_AGENTS) + list(R.PROSE_AGENTS)}


def _install(rows, matrix=None):
    M._ROWS = list(rows)
    M._BY_MODEL = {r["model"]: r for r in rows}
    M._MATRIX = matrix if matrix is not None else {"models": rows,
                                                   "probed_at": "2026-08-11"}
    os.environ["FAKE_API_KEY"] = "present"


def _reset():
    os.environ.pop("FAKE_API_KEY", None)
    os.environ.pop("ROUTING_MODE", None)


# ── admission ────────────────────────────────────────────────────────────────

def test_only_eligible_agents_get_a_model():
    """A model barred from code agents must not appear in a coder's chain —
    that bar is the whole quality floor. codestral-latest really does fence its
    output, so this is the live case, not a hypothetical."""
    _install([CODE_ONLY])
    assert M.models_for("frontend_code") == ["fake/coder"]
    assert M.models_for("qa") == []
    _reset()


def test_absent_key_admits_nothing():
    """A row in the matrix is necessary but not sufficient — no key, no tier,
    exactly as NVIDIA and OpenRouter already behave."""
    _install([BOTH])
    os.environ.pop("FAKE_API_KEY", None)
    assert M.models_for("qa") == []
    _reset()


def test_unadmitted_and_incumbent_rows_are_never_routed():
    rejected = dict(BOTH, model="fake/rejected", admitted=False)
    incumbent = dict(BOTH, model="fake/incumbent", role="incumbent")
    rows = [r for r in (rejected, incumbent) if r.get("admitted")
            and r.get("role") != "incumbent"]
    _install(rows, matrix={"models": [rejected, incumbent]})
    assert M.models_for("qa") == []
    _reset()


def test_missing_matrix_fails_open_to_the_old_chain():
    """An unreadable capability file must cost DEPTH, never the run."""
    _install([], matrix={})
    before = R.build_chain("frontend_code", "cloud-only")
    assert all(not m.startswith("fake/") for m in before)
    assert R.MODELS["frontend_code"][0] in before
    _reset()


# ── ordering and the depth cap ───────────────────────────────────────────────

def test_both_contracts_outrank_faster_single_contract():
    """Usable by more of the pipeline beats 800ms, because the slot is the
    scarce thing, not the millisecond."""
    _install([CODE_ONLY, BOTH])
    assert M.models_for("frontend_code") == ["fake/both", "fake/coder"]
    _reset()


def test_depth_is_capped_per_provider():
    """Thirteen models on one account is not depth: they share one rate limit,
    so a provider-wide 429 costs one round trip each to discover and adds no
    capacity at all."""
    rows = [dict(BOTH, model=f"fake/m{i}", latency_ms=i) for i in range(10)]
    _install(rows)
    picked = M.models_for("qa")
    assert len(picked) == M.MAX_MODELS_PER_PROVIDER
    assert picked == ["fake/m0", "fake/m1", "fake/m2"]      # fastest first
    _reset()


def test_cap_is_per_provider_not_global():
    other = [dict(BOTH, model=f"other/m{i}", provider="other", latency_ms=i)
             for i in range(5)]
    mine = [dict(BOTH, model=f"fake/m{i}", latency_ms=i) for i in range(5)]
    _install(mine + other)
    os.environ["OTHER_API_KEY"] = "present"
    assert len(M.models_for("qa")) == 2 * M.MAX_MODELS_PER_PROVIDER
    os.environ.pop("OTHER_API_KEY", None)
    _reset()


# ── context-window pre-flight filter ─────────────────────────────────────────

def test_a_prompt_that_cannot_fit_skips_the_model():
    """An overflow is not a rate limit: waiting never fixes it and the round
    trip is pure loss, so it must be caught BEFORE the request."""
    _install([CODE_ONLY])                        # 8,192-token window
    assert M.fits_context("fake/coder", 3_000, 1_500) is True
    assert M.fits_context("fake/coder", 100_000, 1_500) is False
    _reset()


def test_the_output_budget_counts_against_the_window():
    """A window holds prompt AND completion. Ignoring the output half is the
    mistake the ollama num_ctx work already paid for once."""
    _install([CODE_ONLY])
    chars = int(8_000 * M.CHARS_PER_TOKEN)       # ~8,000 prompt tokens
    assert M.fits_context("fake/coder", chars, 100) is True
    assert M.fits_context("fake/coder", chars, 1_000) is False
    _reset()


def test_an_unknown_window_is_never_treated_as_small():
    """Skipping on absence of evidence is how a working tier disappears."""
    _install([NO_WINDOW])
    assert M.fits_context("fake/unmapped", 10 ** 7, 8_000) is True
    assert M.fits_context("model/never-probed", 10 ** 7, 8_000) is True
    _reset()


def test_the_router_filters_the_chain_on_context():
    _install([CODE_ONLY])
    chain = R.build_chain("frontend_code", "cloud-only")
    assert "fake/coder" in chain
    fits = [m for m in chain if M.fits_context(m, 100_000, 1_500)]
    assert "fake/coder" not in fits
    _reset()


# ── ROUTING_MODE ─────────────────────────────────────────────────────────────

def test_pinned_removes_the_expansion_tier():
    """A/B arms must not differ by which tier happened to answer."""
    _install([BOTH])
    os.environ["ROUTING_MODE"] = "pinned"
    assert "fake/both" not in R.build_chain("frontend_code", "cloud-only")
    os.environ["ROUTING_MODE"] = "auto"
    assert "fake/both" in R.build_chain("frontend_code", "cloud-only")
    _reset()


def test_unknown_routing_mode_falls_back_to_auto():
    os.environ["ROUTING_MODE"] = "nonsense"
    assert R.routing_mode() == "auto"
    _reset()


def test_expansion_sits_behind_the_incumbents_and_ahead_of_the_free_pools():
    """Ranked by how much evidence there is: production history, then one
    probe, then reachability-only lists."""
    _install([BOTH])
    chain = R.build_chain("frontend_code", "cloud-only")
    primary, fallback = R.MODELS["frontend_code"]
    assert chain.index(primary) < chain.index("fake/both")
    assert chain.index(fallback) < chain.index("fake/both")
    for later in chain:
        if later.startswith(("nvidia_nim/", "openrouter/")):
            assert chain.index("fake/both") < chain.index(later)
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
        finally:
            _reset()
    print(f"\n{passed} passed, {failed} failed. (0 API calls)")
    sys.exit(1 if failed else 0)
