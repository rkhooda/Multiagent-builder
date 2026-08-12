"""Post-coder validation pass (Day 22) — the batch half of the hybrid placement.

Runs after the last file-producing coder node and before QA, so no
syntactically-broken file reaches the QA agent or the download ZIP without being
either auto-repaired or loudly flagged.

Why here and not at write time (ponytail #1): Python and JSON/YAML checks are
stdlib and in-process, so they already run inside process_generated_file where
they catch a dead file before dependents build context on it. JS needs a node
subprocess, and spawning one per file across three parallel coder workers is
pure latency churn — so JS parsing, cross-file import resolution (which is
inherently whole-tree and cannot be done one file at a time anyway), and the
bounded repairs are batched into this single node.

Repairs run sequentially. ponytail: the run ceiling is 10 and realistic runs
spend 0-3, so routing them through parallel_runner would add a scheduler for
work that does not measurably benefit. Revisit if the ceiling ever rises.
"""
import json

from ..core.connection_manager import manager
from ..llm_router import call_llm
from ..observability import metrics_store
from ..utils.code_cleaner import strip_code_fences
from ..utils.file_writer import write_project_file
from ..validation import integration, report as vreport
from ..validation.syntax import (
    JsToolUnavailable, js_syntax_heuristic, validate_artifact, validate_content,
    validate_js_batch, validate_js_imports, validate_page_composition,
)

# Kinds worth spending an LLM call on. Import warnings are FLAG-only: a phantom
# import is a real generation defect (a hallucinated or planned-away module) and
# silently rewriting it would paper over the bug instead of surfacing it, while
# a missing package is fixed by editing package.json, not the importing file.
REPAIRABLE_KINDS = {"syntax", "artifact"}

REPAIR_PROMPT = (
    "This file has a {language} syntax error at line {line}: {message}\n\n"
    "FILE: {filepath}\n"
    "CURRENT CONTENT:\n{content}\n\n"
    "Output the complete corrected file only — no explanation, no markdown "
    "fences, no preamble. Change nothing except what is required to fix the error."
)

_LANGUAGE = {".py": "Python", ".js": "JavaScript", ".jsx": "JavaScript (JSX)",
             ".ts": "TypeScript", ".tsx": "TypeScript (TSX)", ".mjs": "JavaScript",
             ".json": "JSON", ".yml": "YAML", ".yaml": "YAML"}


def _ext(filepath):
    return filepath[filepath.rfind("."):].lower() if "." in filepath else ""


def _agent_for(filepath):
    """Which model repairs this file — the one that generated it."""
    ext = _ext(filepath)
    if ext == ".py":
        return "backend_code"
    if ext in (".yml", ".yaml"):
        return "devops"
    return "frontend_code"


def _revalidate(content, filepath, js_available):
    """Re-check ONE file after a repair, using the same parsers."""
    ext = _ext(filepath)
    if ext in (".js", ".jsx", ".ts", ".tsx", ".mjs"):
        if not js_available:
            return js_syntax_heuristic(content, filepath)
        try:
            return validate_js_batch({filepath: content}).get(filepath, [])
        except JsToolUnavailable:
            return []
    if ext in (".json", ".yml", ".yaml"):
        return validate_artifact(content, filepath)
    return validate_content(content, filepath)


def _repair_file(project_id, filepath, content, issue, js_available):
    """One bounded repair attempt. Returns (new_content, remaining_issues).

    new_content is None when the call failed outright. The caller charges the
    budget BEFORE calling, so a crashed call still costs its slot — otherwise a
    persistently failing file could retry forever.
    """
    prompt = REPAIR_PROMPT.format(
        language=_LANGUAGE.get(_ext(filepath), "source"),
        line=issue.line or "unknown",
        message=issue.message,
        filepath=filepath,
        content=content,
    )
    try:
        raw = call_llm([{"role": "user", "content": prompt}],
                       _agent_for(filepath), max_tokens=2000,
                       project_id=project_id, label=filepath)
    except Exception as e:
        print(f"[Validation] repair call failed for {filepath}: {e}", flush=True)
        return None, [issue]

    fixed = strip_code_fences(raw)
    if not fixed.strip():
        return None, [issue]
    remaining = _revalidate(fixed, filepath, js_available)
    if remaining:
        return None, remaining
    result = write_project_file(project_id, filepath, fixed)
    if not result["success"]:
        return None, [issue]
    return fixed, []


def validate_devops_artifacts(state: dict) -> dict:
    """Validate + repair the DevOps agent's JSON/YAML, folded into the report.

    DevOps runs AFTER qa in the graph, so the validation node cannot see its
    output — docker-compose.yml and CI configs would otherwise be the one
    category of generated file that ships completely unchecked. Rather than
    reorder the graph, the same parsers and the same budget run inline here.

    Returns a state fragment: {generated_files, devops_files, validation_report,
    retry_counts, log, errors} keys that actually changed.
    """
    project_id = state.get("project_id", "")
    generated_files = dict(state.get("generated_files") or {})
    devops_files = dict(state.get("devops_files") or {})
    retry_counts = dict(state.get("retry_counts") or {})
    report = dict(state.get("validation_report") or {})
    log = state.get("log", [])
    errors = state.get("errors", [])

    repaired, failed, issues = [], [], []
    exhausted = False

    for path in sorted(devops_files):
        found = validate_artifact(devops_files[path] or "", path)
        if not found:
            continue
        allowed, reason = vreport.may_repair(retry_counts, path)
        if not allowed:
            exhausted = exhausted or reason == "repair_budget_exhausted"
            failed.append(path)
            issues.extend(found)
            errors.append(f"validation: {path} left unrepaired ({reason})")
            continue

        vreport.record_repair(retry_counts, path)
        new_content, remaining = _repair_file(
            project_id, path, devops_files[path], found[0], js_available=False)
        if new_content is not None:
            devops_files[path] = generated_files[path] = new_content
            repaired.append(path)
            log.append(f"devops_agent: repaired invalid artifact {path}")
        else:
            failed.append(path)
            issues.extend(remaining)
            errors.append(f"validation: unresolved {found[0].describe()}")

    if not (issues or repaired or failed):
        return {}

    # Merge into the existing report rather than rebuilding it: the validation
    # node's own findings are still valid and must not be lost.
    report["artifact_errors"] = report.get("artifact_errors", 0) + len(
        [i for i in issues if i.kind == "artifact"])
    report["auto_repaired"] = report.get("auto_repaired", 0) + len(repaired)
    report["repair_failed"] = report.get("repair_failed", 0) + len(failed)
    report["repaired_files"] = sorted(set(report.get("repaired_files", []) + repaired))
    report["repair_failed_files"] = sorted(set(report.get("repair_failed_files", []) + failed))
    report["issues"] = list(report.get("issues", [])) + [i.as_dict() for i in issues]
    report["repair_calls_spent"] = vreport.repairs_spent_total(retry_counts)
    report["repair_budget_exhausted"] = report.get("repair_budget_exhausted", False) or exhausted

    unresolved = set(report.get("unresolved_files", [])) | {i.filepath for i in issues}
    report["unresolved_files"] = sorted(unresolved)
    checked = max(report.get("files_checked", 0), len(generated_files))
    report["files_checked"] = checked
    rate = (len(unresolved) / checked) if checked else 0.0
    report["failure_rate"] = round(rate, 4)
    report["below_threshold"] = rate > report.get("quality_threshold", vreport.QUALITY_THRESHOLD)

    log.append(f"devops_agent: artifact validation — {len(repaired)} repaired, "
               f"{len(failed)} unresolved")
    return {
        "generated_files": generated_files,
        "devops_files": devops_files,
        "validation_report": report,
        "retry_counts": retry_counts,
        "log": log,
        "errors": errors,
    }


def validation_pass(state: dict) -> dict:
    """Graph node: batch-validate every generated file, repair within budget,
    aggregate a structured report, and broadcast it as its own feed card."""
    project_id = state.get("project_id", "")
    generated_files = dict(state.get("generated_files") or {})
    log = state.get("log", [])
    errors = state.get("errors", [])
    retry_counts = dict(state.get("retry_counts") or {})

    log.append("validation_pass: started")
    print(f"[Validation] checking {len(generated_files)} files", flush=True)

    if not generated_files:
        log.append("validation_pass: no generated files to check")
        return {"validation_report": {}, "log": log, "errors": errors,
                "current_stage": "qa"}

    degraded_tools = []
    issues_by_file = {}

    # ── 1. JS/JSX syntax, one node process for the whole project ────────────
    js_available = True
    try:
        for path, issues in validate_js_batch(generated_files).items():
            issues_by_file.setdefault(path, []).extend(issues)
    except JsToolUnavailable as e:
        js_available = False
        note = f"JS deep validation unavailable — heuristic only ({e})"
        degraded_tools.append(note)
        print(f"[Validation] DEGRADED: {note}", flush=True)
        log.append(f"validation_pass: {note}")
        errors.append(f"validation: {note}")
        from ..observability import degraded
        degraded.record(project_id, "validation_js_unavailable")
        for path, content in generated_files.items():
            found = js_syntax_heuristic(content or "", path)
            if found:
                issues_by_file.setdefault(path, []).extend(found)

    # ── 2. Python + JSON/YAML (free, in-process; re-checked here so files from
    #      agents that bypass call_validated are covered too) ────────────────
    for path, content in generated_files.items():
        found = validate_content(content or "", path)
        if found:
            issues_by_file.setdefault(path, []).extend(found)

    # ── 3. Cross-file import resolution — whole-tree by nature ──────────────
    package_json = generated_files.get("frontend/package.json") or generated_files.get("package.json")
    for path, issues in validate_js_imports(generated_files, package_json).items():
        issues_by_file.setdefault(path, []).extend(issues)

    # ── 3b. Page composition coherence (Improvement 01) — deterministic, free ──
    # Right here beside the import check because it is the same KIND of question
    # (whole-tree, cross-file, cannot be answered one file at a time) and it
    # reuses the same resolver. Returns {} when nothing was decomposed, so an
    # undecomposed run pays nothing. coherence_warning is not in UNRESOLVED_KINDS
    # or REPAIRABLE_KINDS, so these never move the quality threshold and never
    # trigger a paid repair — they are information for the human at Gate 4.
    try:
        plan_tasks = json.loads(state.get("implementation_plan") or "[]")
    except (json.JSONDecodeError, TypeError):
        plan_tasks = []
    for path, issues in validate_page_composition(generated_files, plan_tasks).items():
        issues_by_file.setdefault(path, []).extend(issues)

    # ── 3c. The layer above parsing (docs/VERIFICATION_GAP_ANALYSIS.md) ─────
    # Every file that shipped broken in project e8935f86 PARSED. These four
    # checks ask the two questions parsing cannot: does every name resolve
    # within a file, and did the file finish being written at all.
    #
    # Truncation's authoritative signal is the provider's own finish_reason,
    # already recorded per call as `llm_truncated:{agent}`. The per-file paths
    # come from the metrics store for this project, so a cached or unrecorded
    # response falls back to the content heuristics inside detect_truncation.
    try:
        for path, issues in integration.lint_python(generated_files).items():
            issues_by_file.setdefault(path, []).extend(issues)
    except integration.RuffUnavailable as e:
        note = f"Python linting unavailable — undefined names NOT checked ({e})"
        degraded_tools.append(note)
        print(f"[Validation] DEGRADED: {note}", flush=True)
        log.append(f"validation_pass: {note}")
        errors.append(f"validation: {note}")
        from ..observability import degraded
        degraded.record(project_id, "validation_lint_unavailable")

    # metrics_store.truncations() already records the per-call flag with the
    # filepath in `label` — the store's own docstring notes this is the only
    # place a truncated completion is visible, since it returns normally at the
    # call site and gets written to disk like any other.
    truncated_paths = {row.get("label") for row in metrics_store.truncations(project_id)
                       if row.get("label")}
    for check in (lambda f: integration.detect_truncation(f, truncated_paths),
                  integration.detect_degeneracy,
                  integration.detect_import_time_io,
                  integration.check_python_manifest,
                  integration.check_route_registration,
                  integration.check_package_markers,
                  integration.check_config_keys,
                  integration.check_orm_symmetry):
        for path, issues in check(generated_files).items():
            issues_by_file.setdefault(path, []).extend(issues)

    # ── 4. Bounded repair of syntax/artifact defects ────────────────────────
    repaired, repair_failed = [], []
    budget_exhausted = False

    # Fast mode skips the REPAIRS, not the checks. The checks above are static
    # and free, so the report still says exactly what is broken; only the LLM
    # calls that would fix it are withheld. That is the honest shape of the
    # trade — a quick scaffold you can inspect, with the defects named rather
    # than silently unexamined.
    fast_mode = bool(state.get("fast_mode"))
    if fast_mode and issues_by_file:
        log.append(f"validation_pass: fast mode — {len(issues_by_file)} file(s) "
                   "reported but not repaired")
        print(f"[Validation] fast mode: skipping repair of {len(issues_by_file)} file(s)",
              flush=True)

    for path in sorted(issues_by_file):
        fixable = [i for i in issues_by_file[path] if i.kind in REPAIRABLE_KINDS]
        if not fixable:
            continue
        if fast_mode:
            repair_failed.append(path)
            continue
        allowed, reason = vreport.may_repair(retry_counts, path)
        if not allowed:
            if reason == "repair_budget_exhausted":
                budget_exhausted = True
            errors.append(f"validation: {path} left unrepaired ({reason})")
            log.append(f"validation_pass: {path} not repaired — {reason}")
            repair_failed.append(path)
            continue

        vreport.record_repair(retry_counts, path)  # charge before the call
        new_content, remaining = _repair_file(
            project_id, path, generated_files[path], fixable[0], js_available)

        if new_content is not None:
            generated_files[path] = new_content
            # Keep only the issues the repair did not address (import flags).
            issues_by_file[path] = [i for i in issues_by_file[path]
                                    if i.kind not in REPAIRABLE_KINDS]
            repaired.append(path)
            log.append(f"validation_pass: repaired {path}")
            print(f"[Validation] repaired {path}", flush=True)
        else:
            issues_by_file[path] = [
                i for i in issues_by_file[path] if i.kind not in REPAIRABLE_KINDS
            ] + list(remaining)
            repair_failed.append(path)
            errors.append(f"validation: unresolved {fixable[0].describe()}")
            log.append(f"validation_pass: repair failed for {path}")

    # Improvement 01: fold the reviewer's own tally into the SAME report rather
    # than shipping a second one. render_summary already prefixes the QA report,
    # and the Gate 4 panel already renders it, so counting here is the whole of
    # the UI work — no new panel, no new endpoint.
    reviews = (state.get("review_results") or {}).values()
    review_stats = {
        "files_reviewed": sum(1 for r in reviews if r.get("reviewed")),
        "files_revised": sum(1 for r in reviews if r.get("revised")),
        "review_skipped": sum(1 for r in reviews if r.get("skipped_reason")),
    }

    remaining_issues = [i for issues in issues_by_file.values() for i in issues]
    report = vreport.aggregate(
        remaining_issues,
        files_checked=len(generated_files),
        repaired=repaired,
        repair_failed=repair_failed,
        retry_counts=retry_counts,
        degraded_tools=degraded_tools,
        budget_exhausted=budget_exhausted,
        failed_files=vreport.failed_generation_files(state),
    )
    report.update(review_stats)

    log.append(
        f"validation_pass: completed — {report['files_checked']} files checked, "
        f"{report['syntax_errors_found']} unresolved syntax, "
        f"{report['auto_repaired']} repaired, "
        f"{report['phantom_imports'] + report['missing_packages']} import warnings, "
        f"{report.get('coherence_warnings', 0)} coherence warnings, "
        f"{report.get('lint_errors', 0)} undefined names, "
        f"{report.get('truncated_files', 0)} truncated, "
        f"{report.get('degenerate_files', 0)} degenerate, "
        f"{report['repair_calls_spent']}/{report['repair_ceiling']} repair calls spent")
    print(f"[Validation] Done. {vreport.render_summary(report)}", flush=True)

    manager.broadcast_sync(project_id, {
        "type": "validation_complete",
        "agent": "validation",
        "stage": "validation",
        "files_checked": report["files_checked"],
        "syntax_errors": report["syntax_errors_found"],
        "auto_repaired": report["auto_repaired"],
        "import_warnings": report["phantom_imports"] + report["missing_packages"],
        "artifact_errors": report["artifact_errors"],
        "coherence_warnings": report.get("coherence_warnings", 0),
        "repair_calls_spent": report["repair_calls_spent"],
        "repair_ceiling": report["repair_ceiling"],
        "below_threshold": report["below_threshold"],
    })

    return {
        "generated_files": generated_files,
        "validation_report": report,
        "retry_counts": retry_counts,
        "log": log,
        "errors": errors,
        "current_stage": "qa",
        "_agent_event": True,
    }
