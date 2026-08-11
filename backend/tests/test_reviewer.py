"""Improvement 01: the frontend reviewer + the bounded critic loop. Zero API calls.

Every LLM call is faked, so this runs in the offline gate. Three properties are
load-bearing and each has tests named for the failure it prevents:

  1. FAIL OPEN. A reviewer timeout, a malformed verdict that survives its repair,
     or an exhausted budget must never block a file from being written.
     Generation without review is a degraded outcome; generation blocked by a
     broken reviewer is a failure.

  2. ONE BUDGET ACCOUNT. Reviews/revisions draw from the SAME retry_counts
     ledger and the same per-run ceiling as Day 22's repairs. One file must not
     be able to spend 2 repairs plus 2 revisions.

  3. THE REVIEWER IS ALLOWED TO PASS. A critic that always finds something turns
     every file into two calls and drains the daily budget for cosmetic notes.
"""
import json
import os
import sys

BACKEND_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BACKEND_DIR)

import app.validation as validation  # noqa: E402
from app.agents import frontend_reviewer as reviewer  # noqa: E402
from app.validation import report as vreport  # noqa: E402

TASK = {
    "id": "fe_014",
    "phase": "frontend",
    "filename": "LandingPage.jsx",
    "filepath": "frontend/src/pages/LandingPage.jsx",
    "description": "Landing page shell composing Hero, FeatureGrid and Footer.",
    "requires": ["fe_010"],
    "estimated_complexity": "medium",
}
CONTENT = "import Hero from '../components/Landing/Hero';\nexport default function L(){return <Hero />}\n"

PASS_VERDICT = json.dumps({"verdict": "pass", "issues": [], "coherence_notes": "Consistent."})
REVISE_VERDICT = json.dumps({
    "verdict": "revise",
    "issues": [
        {"severity": "minor", "line": 2, "problem": "Unused import.", "fix_hint": "Remove it."},
        {"severity": "critical", "line": 1, "problem": "Calls GET /api/nope.",
         "fix_hint": "Call GET /api/plans."},
    ],
    "coherence_notes": "Introduces a second button style.",
})


class _FakeLLM:
    """Records calls and replays scripted responses. Raises when told to."""

    def __init__(self, *responses):
        self.responses = list(responses)
        self.calls = []

    def __call__(self, messages, agent_type, **kwargs):
        self.calls.append({"agent_type": agent_type, "label": kwargs.get("label"),
                           "messages": messages})
        nxt = self.responses.pop(0) if self.responses else PASS_VERDICT
        if isinstance(nxt, Exception):
            raise nxt
        return nxt


def _with_llm(fake):
    original = validation.call_llm
    validation.call_llm = fake
    return lambda: setattr(validation, "call_llm", original)


def _with_env(**env):
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


# ── Verdict parsing ──────────────────────────────────────────────────────────

def test_bare_object_parses():
    data, err = reviewer.extract_verdict_json(PASS_VERDICT)
    assert data and data["verdict"] == "pass", err


def test_fenced_and_prose_wrapped_verdicts_parse():
    """The two things models actually do to a 'return only JSON' instruction."""
    for wrapped in (f"```json\n{PASS_VERDICT}\n```",
                    f"Here is my review:\n\n{PASS_VERDICT}",
                    f"```\n{PASS_VERDICT}\n```\nHope that helps."):
        data, err = reviewer.extract_verdict_json(wrapped)
        assert data and data["verdict"] == "pass", (wrapped, err)


def test_nested_braces_do_not_end_the_object_early():
    verdict = json.dumps({"verdict": "pass", "issues": [],
                          "coherence_notes": "Uses {a: 1} style objects."})
    data, _ = reviewer.extract_verdict_json(verdict)
    assert data and data["coherence_notes"].endswith("objects.")


def test_truncated_and_empty_verdicts_report_a_reason():
    for bad in ("", "   ", "no json here", '{"verdict": "pass", "issues": ['):
        data, err = reviewer.extract_verdict_json(bad)
        assert data is None and err, bad


# ── The registry validator ───────────────────────────────────────────────────

def test_valid_verdicts_pass_the_validator():
    for good in (PASS_VERDICT, REVISE_VERDICT):
        assert validation.run_validators("frontend_review", good, {}) == []


def test_validator_rejects_bad_severity_and_verdict():
    bad = json.dumps({"verdict": "maybe", "issues": [{"severity": "blocker", "problem": "x"}],
                      "coherence_notes": ""})
    problems = validation.run_validators("frontend_review", bad, {})
    assert any("verdict" in p for p in problems), problems
    assert any("severity" in p for p in problems), problems


def test_validator_rejects_revise_with_nothing_blocking():
    """Prevents the most expensive drift: a revision call spent on no information."""
    bad = json.dumps({"verdict": "revise",
                      "issues": [{"severity": "minor", "problem": "Unused import."}],
                      "coherence_notes": ""})
    problems = validation.run_validators("frontend_review", bad, {})
    assert any("no issue is critical or major" in p for p in problems), problems


def test_validator_rejects_pass_hiding_a_critical():
    """Prevents the opposite drift: silently shipping the defect review was for."""
    bad = json.dumps({"verdict": "pass",
                      "issues": [{"severity": "critical", "problem": "Calls a fake endpoint."}],
                      "coherence_notes": ""})
    problems = validation.run_validators("frontend_review", bad, {})
    assert any("critical or major" in p for p in problems), problems


# ── review_file: routing, normalisation, and failing open ────────────────────

def test_review_uses_the_non_scarce_provider_slot():
    """ponytail #2: the reviewer must not be charged to the coders' primary."""
    from app.llm_router import MODELS
    primary, fallback = MODELS["frontend_review"]
    coder_primary = MODELS["frontend_code"][0]
    assert primary.startswith("gemini/"), primary
    assert primary != coder_primary, "review would contend with generation"
    assert fallback, "a single-tier reviewer has no recovery path"


def test_review_budget_clears_the_measured_truncation_point():
    """Regression guard for a defect that shipped and hid.

    At REVIEW_MAX_TOKENS=700, calibration on 14 real files had 10 reviews fail:
    every gemini attempt stopped at exactly 696 completion tokens with
    finish_reason=length, because gemini-2.5-flash reasons before answering and
    never reached the JSON. Fail-open turned all ten into "pass", so the reviewer
    was silently doing nothing — a broken feature that looked like a working one.

    Sizing a token budget from how long the ANSWER looks is wrong whenever the
    model thinks first. Anyone lowering this to "a verdict is short" reintroduces
    exactly that.
    """
    assert reviewer.REVIEW_MAX_TOKENS >= 1500, (
        "measured truncation at 696 completion tokens on gemini-2.5-flash; "
        "the budget must leave room for reasoning AND the verdict")


def test_review_call_is_labelled_and_typed():
    fake = _FakeLLM(PASS_VERDICT)
    restore = _with_llm(fake)
    try:
        result = reviewer.review_file(TASK, CONTENT, "spec", {})
    finally:
        restore()
    assert result.reviewed and result.verdict == "pass"
    assert fake.calls[0]["agent_type"] == "frontend_review"
    assert fake.calls[0]["label"] == TASK["filepath"], "per-file metrics attribution"


def test_issues_are_sorted_worst_first():
    fake = _FakeLLM(REVISE_VERDICT)
    restore = _with_llm(fake)
    try:
        result = reviewer.review_file(TASK, CONTENT, "spec", {})
    finally:
        restore()
    assert result.needs_revision
    assert [i["severity"] for i in result.issues] == ["critical", "minor"]
    assert len(result.blocking_issues) == 1


def test_reviewer_exception_fails_open():
    """A reviewer that is down must degrade to 'no review', never to 'no file'."""
    fake = _FakeLLM(RuntimeError("provider exploded"), RuntimeError("still down"))
    restore = _with_llm(fake)
    try:
        result = reviewer.review_file(TASK, CONTENT, "spec", {})
    finally:
        restore()
    assert not result.reviewed and not result.needs_revision
    assert result.verdict == "pass"
    assert "review_failed" in result.skipped_reason


def test_malformed_verdict_is_repaired_once_then_fails_open():
    """One repair (Day 10/22's shared path), then pass — never a third call."""
    fake = _FakeLLM("total garbage", "still not json")
    restore = _with_llm(fake)
    try:
        result = reviewer.review_file(TASK, CONTENT, "spec", {})
    finally:
        restore()
    assert len(fake.calls) == 2, "exactly one repair attempt"
    assert not result.reviewed and result.verdict == "pass"


def test_malformed_verdict_recovered_by_the_repair_is_used():
    fake = _FakeLLM("garbage", REVISE_VERDICT)
    restore = _with_llm(fake)
    try:
        result = reviewer.review_file(TASK, CONTENT, "spec", {})
    finally:
        restore()
    assert len(fake.calls) == 2
    assert result.reviewed and result.needs_revision


def test_parser_findings_are_handed_over_and_not_re_reported():
    """Day 22 already found these; spending reasoning on them again is waste."""
    context = reviewer.build_review_context(
        TASK, CONTENT, "spec", ["file.jsx at line 4: Missing semicolon."])
    assert "do NOT report these again" in context
    assert "Missing semicolon" in context
    clean = reviewer.build_review_context(TASK, CONTENT, "spec", [])
    assert "none" in clean.lower()


def test_reviewer_sees_the_coders_own_spec_verbatim():
    """Re-deriving a second view of the truth lets the two drift, at which point
    the reviewer files issues for endpoints the coder was never shown."""
    context = reviewer.build_review_context(TASK, CONTENT, "THE-EXACT-SPEC-BLOCK", [])
    assert "THE-EXACT-SPEC-BLOCK" in context


# ── Selective triggering ─────────────────────────────────────────────────────

def _fe(filepath, complexity="medium"):
    return {"filepath": filepath, "estimated_complexity": complexity}


def test_off_reviews_nothing():
    restore = _with_env(REVIEW_MODE="off")
    try:
        assert reviewer.should_review(_fe("frontend/src/pages/Home.jsx"), [], 5)[0] is False
    finally:
        restore()


def test_all_reviews_everything():
    restore = _with_env(REVIEW_MODE="all")
    try:
        ok, reason = reviewer.should_review(_fe("frontend/src/components/Tiny.jsx"), [], 0)
        assert ok and reason == "review_all"
    finally:
        restore()


def test_selective_targets_the_files_whose_defects_propagate():
    restore = _with_env(REVIEW_MODE="selective")
    try:
        cases = [
            (_fe("frontend/src/pages/LandingPage.jsx"), [], 0, "page_shell"),
            (_fe("frontend/src/App.jsx"), [], 0, "page_shell"),
            (_fe("frontend/src/components/ui/Button.jsx"), [], 0, "shared_primitive"),
            (_fe("frontend/src/lib/api.js"), [], 0, "shared_primitive"),
            (_fe("frontend/src/components/Card.jsx"), [], 3, "shared_primitive"),
            (_fe("frontend/src/components/X.jsx"), ["warn"], 0, "validation_warnings"),
            (_fe("frontend/src/components/Y.jsx", "high"), [], 0, "high_complexity"),
        ]
        for task, warnings, deps, expected in cases:
            ok, reason = reviewer.should_review(task, warnings, deps)
            assert ok and reason == expected, (task["filepath"], reason)
    finally:
        restore()


def test_selective_skips_ordinary_leaf_components():
    """Reviewing all 51 frontend files is the version that makes this unusable."""
    restore = _with_env(REVIEW_MODE="selective")
    try:
        ok, reason = reviewer.should_review(
            _fe("frontend/src/components/tasks/TaskRow.jsx", "low"), [], 1)
        assert not ok and reason == "not_selected"
    finally:
        restore()


def test_selective_fires_on_roughly_a_quarter_of_a_real_file_set():
    """Guards the economics, not the logic: if this ratio ever approaches 1.0 the
    predicate has stopped being selective and the budget math is void."""
    restore = _with_env(REVIEW_MODE="selective")
    try:
        files = ([_fe(f"frontend/src/components/tasks/Row{i}.jsx", "low") for i in range(30)]
                 + [_fe(f"frontend/src/pages/P{i}.jsx") for i in range(5)]
                 + [_fe("frontend/src/lib/api.js")]
                 + [_fe(f"frontend/src/hooks/use{i}.js", "low") for i in range(6)])
        fired = sum(1 for f in files if reviewer.should_review(f, [], 0)[0])
        assert fired / len(files) < 0.35, f"{fired}/{len(files)} is not selective"
    finally:
        restore()


# ── One budget account ───────────────────────────────────────────────────────

def test_reservation_is_atomic_check_and_charge():
    counts = {}
    ok, _ = vreport.try_reserve_repair(counts, "a.jsx")
    assert ok and vreport.repairs_spent_on(counts, "a.jsx") == 1


def test_revision_and_repair_share_one_per_file_cap():
    """The non-negotiable: one file cannot get 2 repairs AND 2 revisions."""
    counts = {}
    vreport.record_repair(counts, "a.jsx")          # a Day 22 write-time repair
    ok, _ = vreport.try_reserve_repair(counts, "a.jsx")   # an Improvement 01 revision
    assert ok
    blocked, reason = vreport.try_reserve_repair(counts, "a.jsx")
    assert not blocked and reason == "file_repair_cap_reached"
    assert vreport.repairs_spent_on(counts, "a.jsx") == vreport.REPAIR_CAP_PER_FILE


def test_run_ceiling_covers_revisions_too():
    counts = {}
    for i in range(vreport.REPAIR_CEILING_PER_RUN):
        assert vreport.try_reserve_repair(counts, f"f{i}.jsx")[0]
    blocked, reason = vreport.try_reserve_repair(counts, "one_more.jsx")
    assert not blocked and reason == "repair_budget_exhausted"


def test_failed_reservation_charges_nothing():
    counts = {}
    for i in range(vreport.REPAIR_CEILING_PER_RUN):
        vreport.try_reserve_repair(counts, f"f{i}.jsx")
    before = vreport.repairs_spent_total(counts)
    vreport.try_reserve_repair(counts, "denied.jsx")
    assert vreport.repairs_spent_total(counts) == before
    assert "repair:denied.jsx" not in counts


# ── The loop end to end, through the real coder + scheduler ─────────────────
#
# Drives frontend_coder_agent for real (real parallel_runner, real
# process_generated_file, real commit) with every LLM call faked, so the wiring
# that actually ships is what is under test — not a re-implementation of it.

import shutil  # noqa: E402

import app.agents.frontend_coder_agent as coder  # noqa: E402
import app.core.connection_manager as cm  # noqa: E402
from app.utils.file_writer import OUTPUTS_ROOT  # noqa: E402

LOOP_PROJECT = "__test_reviewer_loop__"

# Files the frontend phase renders DETERMINISTICALLY (2026-08-11), never sent to
# the LLM and never review candidates. Filtered out below so the reviewer
# assertions keep measuring the reviewer, rather than becoming a count of
# however many infra files the profile happens to render. App.jsx is here
# because this fixture's plan contains no App component, so it is written as the
# fallback placeholder — a plan that does include one keeps the coder's version.
_INFRA_PATHS = frozenset({
    "frontend/package.json", "frontend/vite.config.js", "frontend/index.html",
    "frontend/tailwind.config.js", "frontend/postcss.config.js",
    "frontend/src/index.css", "frontend/src/main.jsx", "frontend/src/App.jsx",
})


def _coder_files(out: dict) -> dict:
    """Only the files the CODER produced, keyed path -> content."""
    return {p: c for p, c in (out.get("generated_files") or {}).items()
            if p not in _INFRA_PATHS}
GOOD_FILE = (
    "import api from '../lib/api';\n"
    "export default function Panel() {\n"
    "  const [x, setX] = useState(null);\n"
    "  if (!x) return <div className=\"p-6\">Loading…</div>;\n"
    "  return <div className=\"p-6\">{x}</div>;\n"
    "}\n"
)
REVISED_FILE = GOOD_FILE.replace("Panel", "PanelFixed")

LOOP_TASKS = [
    {"id": "fe_001", "phase": "frontend", "filename": "api.js",
     "filepath": "frontend/src/lib/api.js", "description": "Shared axios client.",
     "requires": [], "estimated_complexity": "low"},
    {"id": "fe_002", "phase": "frontend", "filename": "HomePage.jsx",
     "filepath": "frontend/src/pages/HomePage.jsx", "description": "Home page shell.",
     "requires": ["fe_001"], "estimated_complexity": "medium"},
    {"id": "fe_003", "phase": "frontend", "filename": "Row.jsx",
     "filepath": "frontend/src/components/tasks/Row.jsx", "description": "One row.",
     "requires": ["fe_001"], "estimated_complexity": "low"},
]


def _loop_state():
    return {
        "project_id": LOOP_PROJECT, "project_name": "LoopTest",
        "implementation_plan": json.dumps(LOOP_TASKS),
        "file_list": [t["filepath"] for t in LOOP_TASKS],
        "architecture_doc": "## API Endpoints\n| Method | Path |\n|---|---|\n| GET | /api/x |",
        "tech_stack": json.dumps({"frontend": "React 19 + Vite + TailwindCSS + axios"}),
        "generated_files": {}, "log": [], "errors": [], "retry_counts": {},
    }


def _run_loop(review_responses, revision_response=REVISED_FILE, **env):
    """Run the real coder phase. Returns (out, review_calls, revision_calls)."""
    shutil.rmtree(os.path.join(OUTPUTS_ROOT, LOOP_PROJECT), ignore_errors=True)
    review_calls, revision_calls = [], []
    responses = list(review_responses)

    def fake_call_llm(messages, agent_type, **kwargs):
        if agent_type == "frontend_review":
            review_calls.append(kwargs.get("label"))
            nxt = responses.pop(0) if responses else PASS_VERDICT
            if isinstance(nxt, Exception):
                raise nxt          # a provider failure, not a string that looks like one
            return nxt
        return GOOD_FILE

    def fake_revise(messages, agent_type, **kwargs):
        revision_calls.append(kwargs.get("label"))
        return revision_response

    restore_env = _with_env(**env)
    restore_llm = _with_llm(fake_call_llm)
    original_revise_llm = coder.call_llm
    original_broadcast = cm.manager.broadcast_sync
    events = []
    cm.manager.broadcast_sync = lambda pid, event: events.append(event)
    coder.call_llm = fake_revise
    try:
        out = coder.frontend_coder_agent(_loop_state())
    finally:
        coder.call_llm = original_revise_llm
        cm.manager.broadcast_sync = original_broadcast
        restore_llm()
        restore_env()
        shutil.rmtree(os.path.join(OUTPUTS_ROOT, LOOP_PROJECT), ignore_errors=True)
    return out, review_calls, revision_calls, events


def test_loop_reviews_selectively_and_passes_without_revising():
    """The affordable default: only the page shell and the shared client are
    reviewed, and a passing verdict costs exactly zero revision calls."""
    out, reviews, revisions, _ = _run_loop([PASS_VERDICT, PASS_VERDICT],
                                           REVIEW_MODE="selective")
    assert sorted(reviews) == ["frontend/src/lib/api.js",
                               "frontend/src/pages/HomePage.jsx"], reviews
    assert revisions == [], "a pass must never spend a revision"
    assert out["review_results"]["frontend/src/pages/HomePage.jsx"]["verdict"] == "pass"
    assert "frontend/src/components/tasks/Row.jsx" not in out["review_results"]


def test_loop_revises_once_and_the_revision_reaches_state_and_disk():
    out, reviews, revisions, events = _run_loop([REVISE_VERDICT, PASS_VERDICT],
                                                REVIEW_MODE="selective")
    assert len(revisions) == 1, revisions
    revised_path = revisions[0]
    assert "PanelFixed" in out["generated_files"][revised_path], "state holds the revision"
    record = out["review_results"][revised_path]
    assert record["revised"] is True and record["verdict"] == "revise"
    assert {e["type"] for e in events} >= {"file_reviewed", "file_revised"}


def test_revision_charges_the_shared_repair_ledger_exactly_once():
    """The ceiling only means anything if the spend lands in the one account."""
    out, _, revisions, _ = _run_loop([REVISE_VERDICT, PASS_VERDICT],
                                     REVIEW_MODE="selective")
    counts = out["retry_counts"]
    assert vreport.repairs_spent_total(counts) == 1, counts
    assert vreport.repairs_spent_on(counts, revisions[0]) == 1, counts


def test_exhausted_budget_stops_revising_and_says_so():
    """Budget exhaustion degrades gracefully AND visibly — Day 22's position.
    The file still ships; the run states that the generator needs work."""
    restore = _with_env(REPAIR_CEILING_PER_RUN="0")
    original = vreport.REPAIR_CEILING_PER_RUN
    vreport.REPAIR_CEILING_PER_RUN = 0
    try:
        out, _, revisions, _ = _run_loop([REVISE_VERDICT, REVISE_VERDICT],
                                         REVIEW_MODE="selective")
    finally:
        vreport.REPAIR_CEILING_PER_RUN = original
        restore()
    assert revisions == [], "no revision may be issued past the ceiling"
    skipped = [r for r in out["review_results"].values()
               if "budget_exhausted" in (r.get("skipped_reason") or "")]
    assert skipped, out["review_results"]
    assert len(_coder_files(out)) == 3, "every file still ships"


def test_a_broken_reviewer_never_blocks_a_file():
    """The whole fail-open contract, exercised through the real phase."""
    out, _, revisions, _ = _run_loop([RuntimeError("down"), RuntimeError("down"),
                                      RuntimeError("down"), RuntimeError("down")],
                                     REVIEW_MODE="selective")
    assert len(_coder_files(out)) == 3, _coder_files(out).keys()
    assert revisions == []
    assert all(not r["reviewed"] for r in out["review_results"].values())


def test_a_rejected_revision_leaves_the_original_in_place():
    """A revision must never be able to make a file worse than no review at all."""
    out, _, revisions, _ = _run_loop([REVISE_VERDICT, PASS_VERDICT],
                                     revision_response="// nope",
                                     REVIEW_MODE="selective")
    assert len(revisions) == 1
    assert "PanelFixed" not in "".join(_coder_files(out).values())
    assert all("export default function Panel" in c or "api.js" in p
               for p, c in _coder_files(out).items())
    assert out["review_results"][revisions[0]]["revised"] is False


def test_review_off_restores_exact_v1_behaviour():
    """The instant rollback and the A/B control arm."""
    out, reviews, revisions, events = _run_loop([], REVIEW_MODE="off")
    assert reviews == [] and revisions == []
    assert out.get("review_results") == {}
    assert not any(e["type"].startswith("file_review") for e in events)
    assert len(_coder_files(out)) == 3


def test_fast_mode_withholds_review_like_it_withholds_repairs():
    shutil.rmtree(os.path.join(OUTPUTS_ROOT, LOOP_PROJECT), ignore_errors=True)
    restore_env = _with_env(REVIEW_MODE="all")
    reviews = []

    def fake(messages, agent_type, **kwargs):
        if agent_type == "frontend_review":
            reviews.append(kwargs.get("label"))
            return PASS_VERDICT
        return GOOD_FILE

    restore_llm = _with_llm(fake)
    original_broadcast = cm.manager.broadcast_sync
    cm.manager.broadcast_sync = lambda pid, event: None
    try:
        state = _loop_state()
        state["fast_mode"] = True
        out = coder.frontend_coder_agent(state)
    finally:
        cm.manager.broadcast_sync = original_broadcast
        restore_llm()
        restore_env()
        shutil.rmtree(os.path.join(OUTPUTS_ROOT, LOOP_PROJECT), ignore_errors=True)
    assert reviews == [], "fast mode must not spend review calls"
    assert len(_coder_files(out)) == 3


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
