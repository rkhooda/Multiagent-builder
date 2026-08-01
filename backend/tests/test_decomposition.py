"""Improvement 01: frontend section decomposition + the UI contract. Zero API calls.

Covers the two things that must not drift silently:

  1. The decomposition VALIDATOR. Day 21's lesson is that a prompt rule alone does
     not survive model drift — it quietly stops being followed and nothing
     notices until generated code is wrong. Every rule stated in
     prompts/planning_agent.md section 4b is therefore enforced mechanically
     here, and each test below names the failure it prevents.

  2. The ROLLBACK guarantee. DECOMPOSE_FRONTEND=false must restore exact v1.0
     behaviour: the prompt section is not sent AND the validator stands down, so
     a perfectly good v1.0 plan is not rejected for lacking sections it was never
     asked for. That is the instant revert and the A/B control, so it is tested,
     not assumed.

Runnable directly (`python3 tests/test_decomposition.py`) and under pytest.
"""
import json
import os
import sys

BACKEND_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BACKEND_DIR)

from app.agents.context_builder import (  # noqa: E402
    MAX_CONTRACT_CHARS, build_file_context, build_ui_contract,
)
from app.agents.utils import (  # noqa: E402
    decomposition_enabled, decompose_threshold, is_page_task, meets_complexity,
)
from app.validation import _decomposition_integrity, run_validators  # noqa: E402


# ── Fixtures ─────────────────────────────────────────────────────────────────

def _task(tid, filepath, *, phase="frontend", requires=None, section_of=None,
          complexity="medium"):
    task = {
        "id": tid,
        "phase": phase,
        "filename": filepath.rsplit("/", 1)[-1],
        "filepath": filepath,
        "description": ("Concrete description of this file referencing the Landing "
                        "page, its sections and the /api/plans endpoint."),
        "requires": requires or [],
        "context_sections": ["Component Hierarchy"],
        "estimated_complexity": complexity,
    }
    if section_of:
        task["section_of"] = section_of
    return task


# A well-formed decomposed landing page: four sections + the shell that requires
# all four. Everything below mutates a copy of this to create one defect at a time.
GOOD_PLAN = [
    _task("fe_001", "frontend/src/lib/api.js"),
    _task("fe_010", "frontend/src/components/Landing/Hero.jsx", section_of="fe_014"),
    _task("fe_011", "frontend/src/components/Landing/FeatureGrid.jsx", section_of="fe_014"),
    _task("fe_012", "frontend/src/components/Landing/PricingTable.jsx", section_of="fe_014"),
    _task("fe_013", "frontend/src/components/Landing/Footer.jsx", section_of="fe_014"),
    _task("fe_014", "frontend/src/pages/LandingPage.jsx",
          requires=["fe_010", "fe_011", "fe_012", "fe_013"]),
]


def _plan_of(tasks):
    from app.models.task_schema import ImplementationPlan, TaskSchema
    return ImplementationPlan(tasks=[TaskSchema(**t) for t in tasks])


def _errors(tasks):
    return _decomposition_integrity(_plan_of(tasks))


# ── The validator: one test per rule, named by the failure it prevents ───────

def test_wellformed_decomposition_passes():
    """A correct decomposition must produce NO errors. If this ever fails, every
    decomposed plan burns its repair attempt fixing a plan that was already
    right — the most expensive possible false positive."""
    assert _errors(GOOD_PLAN) == [], _errors(GOOD_PLAN)


def test_section_pointing_at_a_missing_shell_is_rejected():
    """Prevents: the section is generated and NOTHING renders it — a whole
    generation call whose output never reaches the running app."""
    tasks = [t for t in GOOD_PLAN if t["id"] != "fe_014"]
    errors = _errors(tasks)
    assert any("not a task in this plan" in e for e in errors), errors


def test_shell_that_does_not_require_its_sections_is_rejected():
    """Prevents: the scheduler is free to build the shell BEFORE its sections, so
    the shell imports files that do not exist yet and Day 22's import check flags
    the entire page."""
    tasks = [dict(t) for t in GOOD_PLAN]
    tasks[-1] = {**tasks[-1], "requires": ["fe_010", "fe_011"]}
    errors = _errors(tasks)
    assert any("does not list its own" in e for e in errors), errors
    assert "fe_012" in " ".join(errors) and "fe_013" in " ".join(errors), errors


def test_sections_hanging_off_a_non_page_are_rejected():
    """Prevents: decomposing the wrong thing. Sections belong to pages; a section
    of a component means the model split a leaf instead of a screen."""
    tasks = [dict(t) for t in GOOD_PLAN]
    tasks[-1] = {**tasks[-1], "filepath": "frontend/src/components/Landing/Shell.jsx"}
    errors = _errors(tasks)
    assert any("is not a page" in e for e in errors), errors


def test_single_section_is_not_a_decomposition():
    """Prevents: paying an extra generation call to split a page in two for no
    granularity gain."""
    tasks = [t for t in GOOD_PLAN if t["id"] not in ("fe_011", "fe_012", "fe_013")]
    tasks[-1] = {**tasks[-1], "requires": ["fe_010"]}
    errors = _errors(tasks)
    assert any("needs between" in e for e in errors), errors


def test_atomised_page_is_rejected():
    """Prevents: the exact budget blow-up this feature is bounded against — one
    page fanning out into eight calls."""
    extra = [_task(f"fe_0{20 + i}", f"frontend/src/components/Landing/Bit{i}.jsx",
                   section_of="fe_014") for i in range(4)]
    tasks = [dict(t) for t in GOOD_PLAN] + extra
    tasks[5] = {**tasks[5], "requires": [t["id"] for t in GOOD_PLAN[1:5]] +
                [t["id"] for t in extra]}
    errors = _errors(tasks)
    assert any("needs between" in e for e in errors), errors


def test_duplicate_filepath_is_rejected():
    """The Day 19 single-owner assertion. Decomposition is the likeliest way to
    break it: a section and the shell both claiming the page's path would be
    generated twice, each 'working' in isolation while clobbering the other."""
    tasks = [dict(t) for t in GOOD_PLAN]
    tasks[1] = {**tasks[1], "filepath": "frontend/src/pages/LandingPage.jsx"}
    errors = _errors(tasks)
    assert any("Duplicate filepath" in e for e in errors), errors


def test_undecomposed_plan_produces_no_decomposition_errors():
    """A plain v1.0 plan has no sections at all and must sail through — the
    validator only judges what decomposition actually produced."""
    plain = [_task("fe_001", "frontend/src/lib/api.js"),
             _task("fe_002", "frontend/src/pages/HomePage.jsx", requires=["fe_001"])]
    assert _errors(plain) == []


# ── The rollback guarantee ───────────────────────────────────────────────────

def _with_env(**env):
    """Context-manager-free env swap; returns a restore callable."""
    old = {k: os.environ.get(k) for k in env}

    def restore():
        for k, v in old.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
    for k, v in env.items():
        os.environ[k] = v
    return restore


def test_flag_off_stands_the_validator_down():
    """The control arm of the A/B must not be judged by treatment rules."""
    broken = json.dumps([t for t in GOOD_PLAN if t["id"] != "fe_014"])
    state = {"file_list": []}
    restore = _with_env(DECOMPOSE_FRONTEND="false")
    try:
        assert not decomposition_enabled()
        off = [e for e in run_validators("planning", broken, state)
               if "section" in e.lower()]
        assert off == [], off
    finally:
        restore()
    on = [e for e in run_validators("planning", broken, state) if "section" in e.lower()]
    assert on, "validator should fire again once the flag is restored"


def test_flag_off_strips_the_prompt_section():
    """A feature flag that still sends the instructions is not a rollback."""
    from app.agents.planning_agent import SYSTEM_PROMPT, _system_prompt
    restore = _with_env(DECOMPOSE_FRONTEND="false")
    try:
        stripped = _system_prompt()
        assert "Frontend Page Decomposition" not in stripped
        assert "section_of" not in stripped.split("## 5. Output Format")[1]
        assert "## 5. Output Format" in stripped
        assert len(stripped) < len(SYSTEM_PROMPT)
    finally:
        restore()
    assert "Frontend Page Decomposition" in _system_prompt()


def test_threshold_is_env_tunable():
    restore = _with_env(DECOMPOSE_COMPLEXITY_THRESHOLD="medium")
    try:
        assert decompose_threshold() == "medium"
        assert meets_complexity("medium")
        assert not meets_complexity("low")
    finally:
        restore()
    assert decompose_threshold() == "high"
    assert not meets_complexity("medium")
    assert meets_complexity("high")


def test_only_pages_are_decomposition_candidates():
    assert is_page_task("frontend/src/pages/LandingPage.jsx")
    assert is_page_task("frontend/src/App.jsx")
    assert not is_page_task("frontend/src/components/Landing/Hero.jsx")
    assert not is_page_task("frontend/src/lib/api.js")


# ── The UI contract ──────────────────────────────────────────────────────────

TECH_STACK = json.dumps({"frontend": "React 19 + Vite + TailwindCSS + axios",
                         "backend": "FastAPI", "database": "PostgreSQL"})


def test_contract_is_deterministic_and_bounded():
    """It is paid on EVERY frontend file, so its size is a per-file tax. A
    contract that bloats the context buys coherence with the import accuracy it
    was meant to protect."""
    plan = json.dumps(GOOD_PLAN)
    first = build_ui_contract(TECH_STACK, plan)
    assert first == build_ui_contract(TECH_STACK, plan), "must be pure"
    assert 0 < len(first) <= MAX_CONTRACT_CHARS, len(first)


def test_contract_names_shared_primitives_from_the_plan():
    """Sections re-inventing their own Button is the classic drift of fragmented
    creative work — the contract's job is to name what already exists."""
    plan = json.dumps(GOOD_PLAN + [
        _task("fe_020", "frontend/src/components/ui/Button.jsx"),
        _task("fe_021", "frontend/src/components/Card.jsx"),
        _task("fe_022", "frontend/src/components/Landing/Extra.jsx", requires=["fe_021"]),
        _task("fe_023", "frontend/src/components/Landing/Other.jsx", requires=["fe_021"]),
    ])
    contract = build_ui_contract(TECH_STACK, plan)
    # Paths are src-relative: that is the form a component actually imports by.
    assert "components/ui/Button.jsx" in contract
    assert "components/Card.jsx" in contract, "2+ dependents = shared"
    assert "Landing/Hero.jsx" not in contract, "a section is not a shared primitive"


def test_primitives_are_dropped_whole_never_sliced():
    """Regression: the first cut truncated the joined contract, which cut this
    line mid-path. A half-written import path is worse than a shorter list."""
    many = [_task(f"fe_1{i:02d}", f"frontend/src/components/ui/VeryLongPrimitiveName{i}.jsx")
            for i in range(12)]
    contract = build_ui_contract(TECH_STACK, json.dumps(GOOD_PLAN + many))
    assert len(contract) <= MAX_CONTRACT_CHARS
    listed = contract.rsplit(": ", 1)[-1].rstrip(".").split(", ")
    assert all(p.endswith(".jsx") for p in listed), listed


def test_contract_survives_a_malformed_plan_and_stack():
    """Fail open: a broken plan must degrade the contract, never crash the coder."""
    assert build_ui_contract("not json", "not json")
    assert build_ui_contract("", "")


def test_contract_reaches_every_frontend_context():
    """The whole point — one identical string in front of every section."""
    state = {"tech_stack": TECH_STACK, "implementation_plan": json.dumps(GOOD_PLAN),
             "ui_contract": build_ui_contract(TECH_STACK, json.dumps(GOOD_PLAN)),
             "architecture_doc": "## API Endpoints\n| Method | Path |\n|---|---|\n| GET | /api/plans |",
             "file_list": [t["filepath"] for t in GOOD_PLAN], "generated_files": {}}
    for task in GOOD_PLAN:
        context = build_file_context(task, state, phase_prefix="frontend/src")
        assert "UI CONTRACT" in context, task["filepath"]
        assert "rounded-lg border border-gray-200" in context, task["filepath"]


def test_sections_appear_in_the_folder_map():
    """Sections are invented at PLANNING time, so they are absent from the
    architecture-derived file_list. A section missing from the folder map is a
    guaranteed wrong relative import in its sibling."""
    state = {"tech_stack": TECH_STACK, "implementation_plan": json.dumps(GOOD_PLAN),
             "architecture_doc": "## API Endpoints\n(none)",
             # file_list deliberately omits the sections, as the architecture would
             "file_list": ["frontend/src/lib/api.js", "frontend/src/pages/LandingPage.jsx"],
             "generated_files": {}}
    context = build_file_context(GOOD_PLAN[-1], state, phase_prefix="frontend/src")
    assert "Hero.jsx" in context and "PricingTable.jsx" in context, context[-1500:]


def _check(name, cond, detail=""):
    print(f"  {'ok  ' if cond else 'FAIL'} {name}{'' if cond else ' -> ' + detail}")
    return cond


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
        except Exception as e:
            failed += 1
            _check(name, False, f"{type(e).__name__}: {e}")
    print(f"\n{passed} passed, {failed} failed. (0 API calls)")
    return failed


if __name__ == "__main__":
    sys.exit(1 if _run_all() else 0)
