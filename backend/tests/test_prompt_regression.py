"""Golden-output regression check for prompt changes (Day 21).

Re-scores the committed golden outputs — the sampled generations that WON their
A/B — against today's automated checklist. Zero API calls: it reads files off
disk and runs the same pure checks the A/B harness uses.

What it catches: a prompt edit that silently undoes a measured fix. Git shows
what a line became but not that it mattered; PROMPT_CHANGELOG.md records why a
rule exists; this asserts the rule still produces conforming output.

What it does NOT catch: a regression in the MODEL's behaviour. The golden files
are fixed text, so this proves the checks and fixtures still agree with the
outputs we blessed — it is a guard against breaking the harness or the fixtures,
not a live quality measurement. Re-running the real A/B is the only way to
measure the model, and that costs calls.

SKIPPED BY DEFAULT under pytest — it asserts on committed artefacts rather than
code, so it should not fail an unrelated CI run. Run it deliberately:

    python3 tests/test_prompt_regression.py          # always runs
    RUN_PROMPT_REGRESSION=1 pytest tests/test_prompt_regression.py
"""
import os
import sys

BACKEND_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BACKEND_DIR)
sys.path.insert(0, os.path.join(BACKEND_DIR, "scripts"))

GOLDEN_DIR = os.path.join(BACKEND_DIR, "tests", "fixtures", "prompt_tuning", "golden")


def _run():
    from ab_prompt_test import load_fixtures, score

    fixtures = {f["name"]: f for f in load_fixtures()}
    names = sorted(f for f in os.listdir(GOLDEN_DIR) if f.endswith(".txt"))
    assert names, f"no golden outputs in {GOLDEN_DIR}"

    failures = []
    for fn in names:
        # Filenames are {variant}_{fixture}_{sample}.txt
        fixture_name = fn[:-4].split("_", 1)[1].rsplit("_", 1)[0]
        fx = fixtures.get(fixture_name)
        assert fx, f"{fn} references unknown fixture {fixture_name!r}"
        with open(os.path.join(GOLDEN_DIR, fn)) as f:
            out = f.read()
        bad = [f"{k}: {v[1]}" for k, v in score(out, fx).items() if not v[0]]
        if bad:
            failures.append(f"{fn}\n    " + "\n    ".join(bad))
    return names, failures


def test_golden_outputs_still_pass():
    """Every blessed output still satisfies every check its fixture declares."""
    if not os.getenv("RUN_PROMPT_REGRESSION"):
        try:
            import pytest
            pytest.skip("set RUN_PROMPT_REGRESSION=1 to run the golden re-score")
        except ImportError:
            pass
    names, failures = _run()
    assert not failures, (
        f"{len(failures)}/{len(names)} golden outputs regressed:\n"
        + "\n".join(failures)
        + "\n\nA prompt or check changed. If the change is intended, re-run the "
          "A/B, re-bless the winning samples, and add a PROMPT_CHANGELOG entry.")


if __name__ == "__main__":
    names, failures = _run()
    for fn in names:
        print(f"  {'FAIL' if any(fn in f for f in failures) else 'ok  '} {fn}")
    if failures:
        print("\n" + "\n".join(failures))
    print(f"\n{len(names) - len(failures)}/{len(names)} golden outputs pass. "
          f"(0 API calls)")
    sys.exit(1 if failures else 0)
