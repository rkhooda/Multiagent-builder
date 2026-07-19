"""Orchestration + budget tests for the Day 22 validation pass.

ZERO LLM calls: call_llm is monkeypatched with a fake repairer, so this belongs
in the fast suite. Covers the parts test_validation.py cannot — the node's
repair loop, the per-file cap, the run ceiling, report aggregation, threshold
semantics, and the node-missing degradation path.
"""
import os
import shutil
import sys

BACKEND_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BACKEND_DIR)

import app.agents.validation_pass as vp  # noqa: E402
from app.validation import report as vreport  # noqa: E402

PROJECT_ID = "test-validation-pass"
OUT_DIR = os.path.join(BACKEND_DIR, "..", "outputs", PROJECT_ID)

GOOD_PY = "def add(a, b):\n    return a + b\n"
BAD_PY = "def add(a, b)\n    return a + b\n"
GOOD_JSX = "export default function A() {\n  return <div>hi</div>;\n}\n"
BAD_JSX = "export default function A() {\n  return <div>hi</span>;\n}\n"
BAD_YAML = "services:\n  web:\n   image: x\n  bad\n"


class FakeLLM:
    """Stands in for call_llm. Returns a fixed repaired body, counts calls."""

    def __init__(self, repaired_body=GOOD_PY, fail=False):
        self.repaired_body = repaired_body
        self.fail = fail
        self.calls = []

    def __call__(self, messages, agent_type, **kwargs):
        self.calls.append((agent_type, messages[0]["content"]))
        if self.fail:
            raise RuntimeError("simulated model failure")
        return self.repaired_body


def _state(files, **extra):
    state = {"project_id": PROJECT_ID, "generated_files": dict(files),
             "log": [], "errors": [], "retry_counts": {}}
    state.update(extra)
    return state


def _install(monkey_llm):
    vp.call_llm = monkey_llm


def _cleanup():
    shutil.rmtree(os.path.abspath(OUT_DIR), ignore_errors=True)


def _check(name, cond, detail=""):
    print(f"  {'ok  ' if cond else 'FAIL'} {name}{'' if cond else ' -> ' + detail}")
    return cond


# ── Repair loop ──────────────────────────────────────────────────────────────

def test_clean_project_spends_nothing():
    fake = FakeLLM()
    _install(fake)
    out = vp.validation_pass(_state({"a.py": GOOD_PY, "b.jsx": GOOD_JSX}))
    r = out["validation_report"]
    assert fake.calls == [], "spent a repair call on a clean project"
    assert r["syntax_errors_found"] == 0 and r["auto_repaired"] == 0
    assert r["failure_rate"] == 0.0 and not r["below_threshold"]


def test_broken_python_repaired():
    fake = FakeLLM(repaired_body=GOOD_PY)
    _install(fake)
    out = vp.validation_pass(_state({"a.py": BAD_PY}))
    r = out["validation_report"]
    assert len(fake.calls) == 1, f"expected 1 repair call, got {len(fake.calls)}"
    assert r["auto_repaired"] == 1 and r["syntax_errors_found"] == 0
    # strip_code_fences rstrips, as everywhere else in the pipeline.
    assert out["generated_files"]["a.py"] == GOOD_PY.strip(), "repaired content not committed"
    assert out["retry_counts"]["repair:a.py"] == 1


def test_repair_prompt_carries_line_and_message():
    fake = FakeLLM(repaired_body=GOOD_PY)
    _install(fake)
    vp.validation_pass(_state({"a.py": BAD_PY}))
    prompt = fake.calls[0][1]
    assert "line 1" in prompt, f"no line number in repair prompt: {prompt[:200]}"
    assert "Python" in prompt


def test_failed_repair_marked_unresolved():
    fake = FakeLLM(repaired_body=BAD_PY)  # "repair" is still broken
    _install(fake)
    out = vp.validation_pass(_state({"a.py": BAD_PY}))
    r = out["validation_report"]
    assert r["auto_repaired"] == 0 and r["repair_failed"] == 1
    assert r["syntax_errors_found"] == 1
    assert "a.py" in r["unresolved_files"]


def test_crashed_repair_call_still_charges_budget():
    """A model that throws must still consume its slot, or a persistently
    failing file retries forever."""
    fake = FakeLLM(fail=True)
    _install(fake)
    out = vp.validation_pass(_state({"a.py": BAD_PY}))
    assert out["retry_counts"]["repair:a.py"] == 1
    assert out["validation_report"]["repair_failed"] == 1


def test_broken_jsx_repaired_via_batch():
    fake = FakeLLM(repaired_body=GOOD_JSX)
    _install(fake)
    out = vp.validation_pass(_state({"a.jsx": BAD_JSX}))
    r = out["validation_report"]
    assert r["auto_repaired"] == 1, f"JSX not repaired: {r}"
    assert fake.calls[0][0] == "frontend_code", "wrong model routed for .jsx"


# ── Budget enforcement ───────────────────────────────────────────────────────

def test_per_file_cap_stops_at_two():
    """A file that already spent its write-time repair gets exactly one more."""
    fake = FakeLLM(repaired_body=BAD_PY)
    _install(fake)
    # 1 repair already charged at write time -> cap of 2 allows exactly 1 here.
    out = vp.validation_pass(_state({"a.py": BAD_PY}, retry_counts={"repair:a.py": 1}))
    assert len(fake.calls) == 1, f"expected 1 call under cap, got {len(fake.calls)}"
    assert out["retry_counts"]["repair:a.py"] == 2

    # Already at the cap -> no call at all.
    fake2 = FakeLLM(repaired_body=BAD_PY)
    _install(fake2)
    out2 = vp.validation_pass(_state({"a.py": BAD_PY}, retry_counts={"repair:a.py": 2}))
    assert fake2.calls == [], "per-file cap not enforced"
    assert "a.py" in out2["validation_report"]["repair_failed_files"]


def test_run_ceiling_stops_repairs():
    fake = FakeLLM(repaired_body=GOOD_PY)
    _install(fake)
    files = {f"f{i}.py": BAD_PY for i in range(15)}
    out = vp.validation_pass(_state(files))
    r = out["validation_report"]
    assert len(fake.calls) == vreport.REPAIR_CEILING_PER_RUN, (
        f"ceiling not enforced: {len(fake.calls)} calls")
    assert r["repair_budget_exhausted"] is True
    assert r["repair_calls_spent"] == vreport.REPAIR_CEILING_PER_RUN


def test_budget_exhaustion_is_visible_in_summary():
    fake = FakeLLM(repaired_body=GOOD_PY)
    _install(fake)
    out = vp.validation_pass(_state({f"f{i}.py": BAD_PY for i in range(15)}))
    summary = vreport.render_summary(out["validation_report"])
    assert "Repair budget exhausted" in summary
    assert f"/{vreport.REPAIR_CEILING_PER_RUN}" in summary


# ── Threshold semantics ──────────────────────────────────────────────────────

def test_threshold_counts_each_file_once():
    """A file with syntax AND import problems is ONE unresolved file."""
    fake = FakeLLM(repaired_body=BAD_JSX)
    _install(fake)
    files = {
        "a.jsx": BAD_JSX + "import x from './nope';\n",
        "ok1.py": GOOD_PY, "ok2.py": GOOD_PY, "ok3.py": GOOD_PY,
    }
    r = vp.validation_pass(_state(files))["validation_report"]
    assert r["unresolved_files"] == ["a.jsx"], r["unresolved_files"]
    assert r["failure_rate"] == 0.25, r["failure_rate"]


def test_failed_generation_counts_toward_threshold():
    """Day 20 stubs parse cleanly, so failed files must come from stage_history
    or they would be invisible to the threshold."""
    fake = FakeLLM()
    _install(fake)
    state = _state({"a.py": GOOD_PY, "b.py": GOOD_PY},
                   stage_history=[{"stage": "backend_code", "failed_files": ["b.py"]}])
    r = vp.validation_pass(state)["validation_report"]
    assert r["generation_failed"] == 1
    assert r["failure_rate"] == 0.5 and r["below_threshold"] is True


def test_import_warnings_do_not_trigger_repairs():
    """FLAG-only policy: phantom imports must never buy an LLM call."""
    fake = FakeLLM()
    _install(fake)
    files = {"a.jsx": "import x from './nope';\nexport default 1;\n"}
    r = vp.validation_pass(_state(files))["validation_report"]
    assert fake.calls == [], "spent a repair call on a FLAG-only import warning"
    assert r["phantom_imports"] == 1


# ── Degradation ──────────────────────────────────────────────────────────────

def test_node_missing_degrades_and_reports():
    fake = FakeLLM()
    _install(fake)
    import app.validation.syntax as syn
    real = syn.shutil.which
    syn.shutil.which = lambda n: None
    try:
        out = vp.validation_pass(_state({"a.jsx": GOOD_JSX}))
    finally:
        syn.shutil.which = real
    r = out["validation_report"]
    assert r["degraded_tools"], "degradation not recorded in report"
    assert "JS deep validation unavailable" in r["degraded_tools"][0]
    assert "JS deep validation unavailable" in vreport.render_summary(r)


# ── QA hand-off ──────────────────────────────────────────────────────────────

def test_qa_context_tells_model_not_to_relitigate():
    fake = FakeLLM(repaired_body=BAD_PY)
    _install(fake)
    r = vp.validation_pass(_state({"a.py": BAD_PY}))["validation_report"]
    block = vreport.qa_context_block(r)
    assert "Do NOT list them again" in block
    assert "a.py" in block


def _run_all():
    real_call_llm = vp.call_llm
    tests = [(n, f) for n, f in sorted(globals().items())
             if n.startswith("test_") and callable(f)]
    passed = failed = 0
    for name, fn in tests:
        _cleanup()
        try:
            fn()
            passed += _check(name, True)
        except AssertionError as e:
            failed += 1
            _check(name, False, str(e))
        except Exception as e:
            failed += 1
            _check(name, False, f"{type(e).__name__}: {e}")
        finally:
            vp.call_llm = real_call_llm
    _cleanup()
    print(f"\n{passed} passed, {failed} failed")
    return failed


if __name__ == "__main__":
    sys.exit(1 if _run_all() else 0)
