#!/usr/bin/env python3
"""Local calls must size their own context window (Day 30).

WHY THIS EXISTS. Ollama gives every model a 4,096-token context regardless of
what the model supports, and silently truncates whatever does not fit:

    msg="truncating input prompt" limit=2050 prompt=4375 keep=4 new=2050

The available input budget is num_ctx MINUS the requested output, so a 4k
window asked for ~2k tokens leaves ~2k for the prompt — and every agent here
sends 11-15k characters. More than half of each prompt was being discarded,
keeping only the first 4 tokens, while the call returned 200 with plausible
prose. No error, no finish_reason, no metric: it looked like weak models rather
than a truncated question.

These assertions pin the sizing rule, because a regression here is invisible in
output — it would quietly degrade every local run again.

Zero API calls.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.llm_router import (OLLAMA_MAX_CONTEXT_TOKENS,
                            OLLAMA_MIN_CONTEXT_TOKENS, ollama_num_ctx)


def test_small_request_gets_the_floor():
    # Never go below ollama's own default — a tiny window costs nothing to
    # avoid and a too-small one truncates.
    assert ollama_num_ctx(500, 500) == OLLAMA_MIN_CONTEXT_TOKENS


def test_real_pipeline_prompt_exceeds_the_4k_default():
    """The exact case that was silently truncated in production."""
    # 12,846 chars + 4,000 output — a measured Day 30 research call.
    ctx = ollama_num_ctx(12846, 4000)
    assert ctx > 4096, "this is the regression: 4096 truncates this prompt"
    # Must hold the prompt AND the requested output simultaneously.
    assert ctx >= 12846 / 3.0 + 4000


def test_window_covers_prompt_plus_output():
    for chars, out in [(6000, 1000), (14327, 2000), (20000, 3000)]:
        ctx = ollama_num_ctx(chars, out)
        if ctx < OLLAMA_MAX_CONTEXT_TOKENS:      # unclamped cases only
            assert ctx >= chars / 3.0 + out, f"{chars} chars + {out} out"


def test_clamped_to_the_ceiling():
    # The KV cache is real memory on a shared machine; an unbounded window
    # would OOM the box rather than fail the call.
    assert ollama_num_ctx(10_000_000, 8000) == OLLAMA_MAX_CONTEXT_TOKENS


def test_rounded_to_1024_multiples():
    # Changing num_ctx forces ollama to RELOAD the model. Rounding means many
    # near-identical prompts share one window size instead of thrashing.
    for chars in range(5000, 40000, 2500):
        assert ollama_num_ctx(chars, 2000) % 1024 == 0


def test_monotonic_in_prompt_size():
    """A bigger prompt must never get a smaller window."""
    sizes = [ollama_num_ctx(c, 2000) for c in range(1000, 60000, 3000)]
    assert sizes == sorted(sizes)


def test_monotonic_in_output_size():
    sizes = [ollama_num_ctx(12000, o) for o in range(500, 12000, 500)]
    assert sizes == sorted(sizes)


def _check(name, ok, detail=""):
    print(f"  {'PASS' if ok else 'FAIL'}  {name}{(' — ' + detail) if detail else ''}")
    return 1 if ok else 0


def _run_all():
    tests = [(n, f) for n, f in sorted(globals().items())
             if n.startswith("test_") and callable(f)]
    passed = failed = 0
    for name, fn in tests:
        try:
            fn()
            passed += _check(name, True)
        except AssertionError as e:
            failed += 1
            _check(name, False, str(e))
        except Exception as e:                      # noqa: BLE001
            failed += 1
            _check(name, False, f"{type(e).__name__}: {e}")
    print(f"\n{passed} passed, {failed} failed. (0 API calls)")
    return failed


if __name__ == "__main__":
    sys.exit(1 if _run_all() else 0)
