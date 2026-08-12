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
    check("disabled: enabled=False in report",
          result["build_verification"] == {"enabled": False, "status": "not_applicable"})
    check("disabled: qa_report/validation_report untouched", "qa_report" not in result)


def test_no_targets_is_not_a_failure():
    def run():
        result = bva.build_verify_agent({"project_id": "test-build-verify-agent", "stack_profile": "react-fastapi"})
        check("no targets: enabled=True, empty targets, no unverified_reason",
              result["build_verification"] == {"enabled": True, "targets": {},
                                               "status": "not_applicable"}, result)
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


# ── The status vocabulary (verification gap, 2026-08-13) ─────────────────────
#
# Project e8935f86 shipped with BOTH targets unverified -- nothing executed --
# while Gate 4 said "the generated code did not fully install/build/boot".
# "We checked and it failed" and "we never checked" were the same branch.

def test_unverified_is_not_failed():
    from app.build_verify.classify import (FAILED, UNVERIFIED_STATUS,
                                           verification_status)
    # The CRM's exact report shape: both targets never ran.
    crm = {"enabled": True, "targets": {
        "backend": {"target": "backend",
                    "unverified_reason": "<urlopen error [Errno 8] nodename nor servname provided>"},
        "frontend": {"target": "frontend",
                     "unverified_reason": "<urlopen error [Errno 8] nodename nor servname provided>"},
    }}
    status = verification_status(crm)
    check("the CRM report reads unverified, not failed",
          status == UNVERIFIED_STATUS and status != FAILED, status)


def test_a_real_failure_is_failed_not_unverified():
    from app.build_verify.classify import FAILED, verification_status
    report = {"enabled": True, "targets": {"backend": {"target": "backend", "tiers": {
        "install": {"verdict": "pass"}, "build": {"verdict": "fail_code"},
        "boot": {"verdict": "skipped"}}}}}
    check("a tier that ran and failed reads failed",
          verification_status(report) == FAILED, verification_status(report))


def test_all_tiers_pass_is_verified():
    from app.build_verify.classify import VERIFIED, verification_status
    report = {"enabled": True, "targets": {"backend": {"target": "backend", "tiers": {
        "install": {"verdict": "pass"}, "build": {"verdict": "pass"},
        "boot": {"verdict": "pass"}}}}}
    check("every tier passing reads verified",
          verification_status(report) == VERIFIED, verification_status(report))


def test_one_unverified_tier_outranks_other_passes():
    """`unverified` beats both `failed` and `verified`: if any part never ran,
    the truthful statement is "we do not know", and anything else overstates
    what was established."""
    from app.build_verify.classify import UNVERIFIED_STATUS, verification_status
    report = {"enabled": True, "targets": {
        "backend": {"target": "backend", "tiers": {"install": {"verdict": "pass"}}},
        "frontend": {"target": "frontend", "tiers": {"install": {"verdict": "unverified"}}},
    }}
    check("one unverified tier outranks another target's passes",
          verification_status(report) == UNVERIFIED_STATUS, verification_status(report))


def test_tier_level_unverified_is_surfaced_to_the_target():
    """The same abstention bug one level down: a tier-level UNVERIFIED lived
    only inside `tiers`, while build_verify_agent tested for a target-level
    `unverified_reason` -- so it was never counted and never surfaced."""
    import app.build_verify.ladder as ladder

    calls = {"cleanup": False}

    def fake_call(endpoint, payload, timeout_s):
        if endpoint == "/workspace/start":
            return {"workspace": "/tmp/ws"}
        if endpoint == "/workspace/cleanup":
            calls["cleanup"] = True
            return {}
        raise OSError("sandbox went away mid-ladder")

    class _Spec:
        command, image, timeout_s, workdir, env = ("true",), "x", 5, "", None

    class _Target:
        name, root = "backend", "backend"
        install, build, boot = _Spec(), None, None

    original = ladder._call
    ladder._call = fake_call
    try:
        result = ladder.verify_target("p1", _Target())
    finally:
        ladder._call = original

    check("a tier-level unverified surfaces as target-level unverified_reason",
          result.get("tiers", {}).get("install", {}).get("verdict") == "unverified"
          and "unverified_reason" in result, result)
    check("the workspace is still cleaned up", calls["cleanup"])


def test_the_headline_never_calls_an_unrun_check_a_failure():
    from app.build_verify.classify import (UNVERIFIED_STATUS, render_qa_prefix,
                                           status_headline)
    text = status_headline(UNVERIFIED_STATUS)
    check("the unverified headline says never executed, not 'did not build'",
          "NOT VERIFIED" in text and "never been executed" in text
          and "did not" not in text.lower(), text)

    prefix = render_qa_prefix({"enabled": True, "targets": {
        "backend": {"target": "backend", "unverified_reason": "sandbox unreachable"}}})
    check("the QA prefix leads with the honest headline",
          "NOT VERIFIED" in prefix and "NOT CHECKED" in prefix, prefix)


def main() -> int:
    for fn in (
        test_disabled_short_circuits,
        test_no_targets_is_not_a_failure,
        test_all_pass_merges_into_reports,
        test_ladder_exception_never_escapes,
        test_whole_agent_never_raises_on_unexpected_bug,
        test_worst_case_wall_clock_stays_bounded,
        test_unverified_is_not_failed,
        test_a_real_failure_is_failed_not_unverified,
        test_all_tiers_pass_is_verified,
        test_one_unverified_tier_outranks_other_passes,
        test_tier_level_unverified_is_surfaced_to_the_target,
        test_the_headline_never_calls_an_unrun_check_a_failure,
    ):
        print(f"-- {fn.__name__}")
        fn()
    print(f"\n{passed} passed, {failed} failed")
    return 1 if failed else 0




if __name__ == "__main__":
    sys.exit(main())
