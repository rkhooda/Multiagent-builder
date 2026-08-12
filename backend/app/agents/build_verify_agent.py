"""Build Verification Agent — ground truth for whether generated code
actually runs, not just parses. Runs the active profile's declared verify
targets (backend, frontend, ...) against the sandbox service in parallel.

run_agent_safely semantics, deliberately stronger than stage_node's default
"log a bug and stop the pipeline for human review": ladder.verify_target
already turns an unreachable sandbox into `unverified` internally, but this
function ALSO wraps its own top level, so even a genuine bug in this brand
new, less battle-tested machinery degrades to `unverified` + a
degraded_events increment rather than costing the human a whole pipeline run
over a build-verification defect. See docs/SANDBOX_THREAT_MODEL.md's Verdict
semantics: unverified must never read as pass, and never happen silently.
"""
import os
from concurrent.futures import ThreadPoolExecutor

from ..build_verify.classify import (NOT_APPLICABLE, UNVERIFIED_STATUS,
                                     render_qa_prefix, verification_status)
from ..build_verify.ladder import verify_target
from ..observability import degraded
from ..observability.metrics_store import record_agent_run
from ..profiles import active_profile

BUILD_VERIFY_ENABLED = os.environ.get("BUILD_VERIFY_ENABLED", "true").lower() != "false"


def _run_target(project_id: str, target) -> dict:
    try:
        return verify_target(project_id, target)
    except Exception as e:  # noqa: BLE001 — deliberate: see module docstring
        degraded.record(project_id, "build_verify_error")
        return {"target": target.name, "unverified_reason": f"{type(e).__name__}: {e}"}


def _merged_validation_report(state: dict, report: dict) -> dict:
    """Adds this stage's finding to the existing structured validation_report
    rather than a parallel report — the brief's own instruction, and it keeps
    score_project.py's future top tier reading from one place."""
    merged = dict(state.get("validation_report") or {})
    merged["build_verification"] = report
    return merged


def _prepend_qa_prefix(state: dict, report: dict) -> str:
    """Same mechanic validation's automated checks already use
    (qa_agent.py:494-498): insert right after '## Summary\\n'. A no-op if QA
    was skipped and qa_report has no such heading — str.replace(count=1) on a
    missing substring is a safe no-op, not an error."""
    qa_report = state.get("qa_report", "")
    prefix = render_qa_prefix(report)
    if prefix and "## Summary\n" in qa_report:
        return qa_report.replace("## Summary\n", f"## Summary\n{prefix}\n\n", 1)
    return qa_report


def _record_metrics(project_id: str, report: dict) -> None:
    """One row per (target, tier) in the existing agent_runs table — reuses
    its outcome/label/latency_ms columns rather than a new table, so build
    pass rate is queryable with the same tooling as token/latency metrics.
    `agent='build_verify'` groups these distinctly from LLM-call rows."""
    for name, r in report.get("targets", {}).items():
        tiers = r.get("tiers") or {}
        if not tiers:
            record_agent_run("build_verify", project_id=project_id,
                             outcome="unverified", label=name)
            continue
        for tier_name, detail in tiers.items():
            record_agent_run(
                "build_verify", project_id=project_id,
                outcome=detail.get("verdict"), label=f"{name}:{tier_name}",
                latency_ms=int(detail.get("duration_s", 0) * 1000) if "duration_s" in detail else None,
            )


def _summary(report: dict) -> str:
    parts = []
    for name, r in report.get("targets", {}).items():
        tiers = r.get("tiers")
        if tiers:
            parts.append(name + ": " + ", ".join(f"{t}={v.get('verdict')}" for t, v in tiers.items()))
        else:
            parts.append(f"{name}: unverified ({r.get('unverified_reason', 'unknown')})")
    return "; ".join(parts) if parts else "nothing to verify"


def build_verify_agent(state: dict) -> dict:
    """
    Reads: state['project_id'], state['stack_profile']
    Writes: state['build_verification'], state['log']

    Never raises. A problem anywhere in this stage becomes an `unverified`
    report plus a degraded_events increment — never a pipeline failure, and
    never a silent `pass`.
    """
    project_id = state.get("project_id", "")
    log = list(state.get("log", []))

    try:
        if not BUILD_VERIFY_ENABLED:
            log.append("build_verify_agent: disabled (BUILD_VERIFY_ENABLED=false)")
            return {"build_verification": {"enabled": False, "status": NOT_APPLICABLE}, "log": log}

        profile = active_profile(state)
        targets = profile.verify_targets
        if not targets:
            log.append(f"build_verify_agent: profile '{profile.name}' declares no verify targets")
            return {"build_verification": {"enabled": True, "targets": {},
                                       "status": NOT_APPLICABLE}, "log": log}

        log.append(f"build_verify_agent: verifying {len(targets)} target(s)")
        with ThreadPoolExecutor(max_workers=len(targets)) as pool:
            results = list(pool.map(lambda t: _run_target(project_id, t), targets))

        for r in results:
            if "unverified_reason" in r:
                degraded.record(project_id, "build_verify_unavailable")

        report = {"enabled": True, "targets": {r["target"]: r for r in results}}
        # Persisted so every downstream surface reads ONE computed status
        # instead of re-deriving it from the tier tree and disagreeing.
        report["status"] = verification_status(report)
        _record_metrics(project_id, report)
        # "done" was the wrong word for a run where nothing executed.
        verb = "NOT VERIFIED" if report["status"] == UNVERIFIED_STATUS else "done"
        log.append(f"build_verify_agent: {verb} - {_summary(report)}")
        return {
            "build_verification": report,
            "validation_report": _merged_validation_report(state, report),
            "qa_report": _prepend_qa_prefix(state, report),
            "log": log,
        }

    except Exception as e:  # noqa: BLE001 — deliberate: see module docstring
        degraded.record(project_id, "build_verify_error")
        log.append(f"build_verify_agent: unverified — {type(e).__name__}: {e}")
        return {
            "build_verification": {"enabled": True, "targets": {},
                                   "unverified_reason": str(e),
                                   "status": UNVERIFIED_STATUS},
            "log": log,
        }
