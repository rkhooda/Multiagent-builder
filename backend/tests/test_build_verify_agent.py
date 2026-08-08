"""Offline unit tests for build_verify_agent's own logic — the enable flag,
skip-when-no-targets, report merging into validation_report/qa_report, and
the run_agent_safely guarantee that it never raises. No Docker, no LLM:
verify_target itself is monkeypatched, since Phase 2's own tests already
prove the ladder for real against a live daemon (test_build_verify.py).

    cd backend && python tests/test_build_verify_agent.py
"""
import os
import sys

BACKEND_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BACKEND_DIR)

from app.agents import build_verify_agent as bva  # noqa: E402
from app.profiles import VerifyTarget  # noqa: E402

passed = 0
failed = 0


def check(name, cond, detail=""):
    global passed, failed
    if cond:
        passed += 1
        print(f"  PASS  {name}")
    else:
        failed += 1
        print(f"  FAIL  {name}  {detail}")


FAKE_TARGETS = (VerifyTarget(name="backend", root="backend"),
                VerifyTarget(name="frontend", root="frontend"))


def _with_targets(monkeypatch_targets, fn):
    """Swap the react-fastapi profile's verify_targets for the duration of
    one check, restoring it afterward — no test framework, just save/restore."""
    from app.profiles import react_fastapi
    original = react_fastapi.PROFILE.verify_targets
    object.__setattr__(react_fastapi.PROFILE, "verify_targets", monkeypatch_targets)
    try:
        fn()
    finally:
        object.__setattr__(react_fastapi.PROFILE, "verify_targets", original)


def test_disabled_short_circuits():
    original = bva.BUILD_VERIFY_ENABLED
    bva.BUILD_VERIFY_ENABLED = False
    try:
        result = bva.build_verify_agent({"project_id": "test-build-verify-agent"})
    finally:
        bva.BUILD_VERIFY_ENABLED = original
    check("disabled: enabled=False in report", result["build_verification"] == {"enabled": False})
    check("disabled: qa_report/validation_report untouched", "qa_report" not in result)


def test_no_targets_is_not_a_failure():
    def run():
        result = bva.build_verify_agent({"project_id": "test-build-verify-agent", "stack_profile": "react-fastapi"})
        check("no targets: enabled=True, empty targets, no unverified_reason",
              result["build_verification"] == {"enabled": True, "targets": {}}, result)
    _with_targets((), run)


def test_all_pass_merges_into_reports():
    def fake_verify_target(project_id, target):
        return {"target": target.name, "tiers": {
            "install": {"verdict": "pass"}, "build": {"verdict": "pass"}, "boot": {"verdict": "pass"}}}

    original = bva.verify_target
    bva.verify_target = fake_verify_target
    try:
        def run():
            state = {
                "project_id": "test-build-verify-agent", "stack_profile": "react-fastapi",
                "qa_report": "# QA Report\n\n## Summary\nsome llm text\n\n## Findings\n",
                "validation_report": {"files_checked": 5},
            }
            result = bva.build_verify_agent(state)
            check("both targets recorded", set(result["build_verification"]["targets"]) == {"backend", "frontend"})
            check("validation_report keeps prior keys AND gains build_verification",
                  result["validation_report"]["files_checked"] == 5
                  and "build_verification" in result["validation_report"])
            check("qa_report gains the BUILD VERIFICATION prefix",
                  "**BUILD VERIFICATION**" in result["qa_report"] and "some llm text" in result["qa_report"],
                  result["qa_report"])
        _with_targets(FAKE_TARGETS, run)
    finally:
        bva.verify_target = original


def test_ladder_exception_never_escapes():
    """A genuine bug in verify_target (not the anticipated sandbox-unavailable
    case, which verify_target already handles internally) must still degrade
    to unverified, per the module's run_agent_safely guarantee."""
    def boom(project_id, target):
        raise RuntimeError("simulated bug")

    original = bva.verify_target
    bva.verify_target = boom
    try:
        def run():
            result = bva.build_verify_agent({"project_id": "test-build-verify-agent", "stack_profile": "react-fastapi"})
            targets = result["build_verification"]["targets"]
            check("both targets degrade to unverified_reason, not an exception",
                  all("unverified_reason" in t for t in targets.values()), targets)
        _with_targets(FAKE_TARGETS, run)
    finally:
        bva.verify_target = original


def test_whole_agent_never_raises_on_unexpected_bug():
    """Even a bug BEFORE the ladder even runs (e.g. active_profile itself
    breaking) must not propagate — the outer try/except's whole reason to
    exist, per the module docstring."""
    original = bva.active_profile
    bva.active_profile = lambda state: (_ for _ in ()).throw(RuntimeError("boom"))
    try:
        result = bva.build_verify_agent({"project_id": "test-build-verify-agent"})
        check("top-level bug still returns a report, not an exception",
              result["build_verification"]["enabled"] is True
              and "unverified_reason" in result["build_verification"], result)
    finally:
        bva.active_profile = original


def test_worst_case_wall_clock_stays_bounded():
    """No separate top-level stage timeout exists (ponytail decision, Phase
    3): the per-tier ceilings already compose into a hard bound. This pins
    that composed bound so a future recipe change can't silently blow past
    it — worst case per tier is timeout*(1+MAX_ENV_RETRIES) (one bounded
    retry on a suspected-transient failure), summed across a target's tiers,
    and targets run in parallel so the STAGE's worst case is the slowest
    single target, not the sum of all of them."""
    from app.build_verify.classify import MAX_ENV_RETRIES
    from app.profiles import get_profile

    CEILING_S = 1200  # 20 minutes — generous headroom over today's ~750s worst case
    profile = get_profile("react-fastapi")
    worst_per_target = []
    for target in profile.verify_targets:
        tiers = [t for t in (target.install, target.build, target.boot) if t is not None]
        total = sum(
            (t.ready_timeout_s if t is target.boot else t.timeout_s) * (1 + MAX_ENV_RETRIES)
            for t in tiers
        )
        worst_per_target.append((target.name, total))

    stage_worst_case = max((s for _, s in worst_per_target), default=0)
    check(f"react-fastapi's worst-case verify wall-clock ({stage_worst_case}s) stays under {CEILING_S}s",
          stage_worst_case < CEILING_S, worst_per_target)


def main() -> int:
    for fn in (
        test_disabled_short_circuits,
        test_no_targets_is_not_a_failure,
        test_all_pass_merges_into_reports,
        test_ladder_exception_never_escapes,
        test_whole_agent_never_raises_on_unexpected_bug,
        test_worst_case_wall_clock_stays_bounded,
    ):
        print(f"-- {fn.__name__}")
        fn()
    print(f"\n{passed} passed, {failed} failed")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
