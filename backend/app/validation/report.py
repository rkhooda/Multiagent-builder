"""Repair budget, report aggregation, and the Gate 4 quality threshold (Day 22).

The economics this module encodes: **parsing is free, repairing is not.** Every
check runs on every file, always. Repair calls are real OpenRouter spend, so
they are capped twice — per file and per run — and the spend is always visible.

The run ceiling is deliberately low. A project needing 10+ repairs does not have
a repair problem; it has a PROMPT or ARCHITECTURE problem, and the fix lives in
the Day 21 A/B harness with a PROMPT_CHANGELOG entry. Budget exhaustion is a
diagnostic signal that the generator is misaimed — raising the ceiling to make
the symptom go away is the exact anti-pattern Day 21 exists to prevent.
"""
import os
import threading

# Env-configurable with sane defaults. Day 25 (three full integration projects)
# is the real evidence base for tuning these — resist retuning on one run.
QUALITY_THRESHOLD = float(os.getenv("QUALITY_THRESHOLD", "0.2"))
REPAIR_CAP_PER_FILE = int(os.getenv("REPAIR_CAP_PER_FILE", "2"))

# Raised 10 -> 14 for Improvement 01, deliberately and once.
#
# The ceiling now covers THREE kinds of LLM spend, not two: Day 22's write-time
# repairs, Day 22's validation-pass repairs, and Improvement 01's reviewer
# revisions. Those revisions land EARLY (inside the coder phase), so leaving the
# ceiling at 10 would let a review-heavy frontend phase exhaust the budget before
# the validation pass runs at all — converting "some files got a second opinion"
# into "no syntax error anywhere in the project got repaired". That is a strictly
# worse trade, and it would have been silent.
#
# +4 is sized against the selective trigger, not picked round: it fires on ~12-18
# of a ~50-file frontend phase, and measured runs spend 0-3 repairs. It is NOT
# licence for more repairing — the Day 22 reasoning stands unchanged: a project
# needing this many repairs has a PROMPT or ARCHITECTURE problem, and exhaustion
# is the diagnostic signal that says so. Raising it again to silence a symptom is
# the exact anti-pattern Day 21 exists to prevent.
REPAIR_CEILING_PER_RUN = int(os.getenv("REPAIR_CEILING_PER_RUN", "14"))

REPAIR_PREFIX = "repair:"

# Issue kinds that count as an UNRESOLVED mechanical defect for the threshold.
# `lint` and `truncated`/`degenerate` ARE unresolved defects: each is a
# deterministic statement that the artifact does not run, not an opinion about
# style. `lint_info` (unused imports) is deliberately absent — 66 of them
# against 4 real defects on the CRM tree.
#
# `import_time_io` is absent too: a module that opens a connection at import is
# a real defect, but it is a design finding for the human rather than a
# mechanical failure of the file, and it has no unambiguous automatic fix.
UNRESOLVED_KINDS = {"syntax", "phantom_import", "missing_package", "artifact",
                    "lint", "truncated", "degenerate", "manifest", "route"}


def repair_key(filepath: str) -> str:
    return f"{REPAIR_PREFIX}{filepath}"


def repairs_spent_on(retry_counts: dict, filepath: str) -> int:
    return int((retry_counts or {}).get(repair_key(filepath), 0))


def repairs_spent_total(retry_counts: dict) -> int:
    """Total automatic repair calls this run, derived by summing the repair:*
    keys — no separate counter to drift out of sync with the per-file ones."""
    return sum(v for k, v in (retry_counts or {}).items()
               if isinstance(k, str) and k.startswith(REPAIR_PREFIX))


def may_repair(retry_counts: dict, filepath: str):
    """(allowed, reason). Enforces per-file cap then run ceiling.

    Gate 4's human-initiated fixes (fix_counts, cap 3) are tracked SEPARATELY on
    purpose. They are a person deciding to spend, not the machine retrying
    itself; folding them into one pool would let failed auto-repairs exhaust the
    budget a user needs to click "fix this file". Both are surfaced together in
    the Gate 4 breakdown, so the account is one VIEW over two ledgers.
    """
    if repairs_spent_total(retry_counts) >= REPAIR_CEILING_PER_RUN:
        return False, "repair_budget_exhausted"
    if repairs_spent_on(retry_counts, filepath) >= REPAIR_CAP_PER_FILE:
        return False, "file_repair_cap_reached"
    return True, ""


def record_repair(retry_counts: dict, filepath: str) -> dict:
    """Charge one repair call against `filepath`. Mutates and returns the dict."""
    key = repair_key(filepath)
    retry_counts[key] = int(retry_counts.get(key, 0)) + 1
    return retry_counts


# Guards try_reserve_repair only. The sequential callers (validation_pass,
# validate_devops_artifacts) do not need it; Improvement 01's reviewer decides
# mid-worker, and the coder workers are a THREAD POOL, so check-then-charge as
# two statements would let three concurrent workers all read "9 spent" and each
# charge a tenth — three revisions issued against a ceiling of one.
_reserve_lock = threading.Lock()


def try_reserve_repair(retry_counts: dict, filepath: str) -> tuple:
    """Atomically may_repair + record_repair. Returns (allowed, reason).

    This is the ONE account (Improvement 01, ponytail #2). A reviewer revision is
    an LLM call that fixes a generated file, which is exactly what a Day 22
    repair is, so it charges the identical `repair:{path}` key and obeys the
    identical per-file cap and per-run ceiling. There is deliberately no separate
    review ledger and no REVIEW_CEILING: two counters over one budget is how a
    file quietly ends up spending 2 repairs plus 2 revisions.

    Charges only on success, so a denied request costs nothing — unlike the
    sequential callers, which charge before calling because there a crashed call
    must still consume its slot.
    """
    with _reserve_lock:
        allowed, reason = may_repair(retry_counts, filepath)
        if allowed:
            record_repair(retry_counts, filepath)
        return allowed, reason


def failed_generation_files(state: dict) -> set:
    """Files a coder failed or was blocked from generating (Day 20 stubs).

    These matter for the threshold and cannot be found by parsing: the stub
    written to disk is syntactically VALID, so a parser reports it clean while
    the file is in fact missing its content. stage_node folds each phase's
    partial_failures into stage_history as `failed_files`.
    """
    failed = set()
    for entry in state.get("stage_history") or []:
        failed.update(entry.get("failed_files") or [])
    return failed


def aggregate(issues: list, *, files_checked: int, repaired: list, repair_failed: list,
              retry_counts: dict, degraded_tools: list, budget_exhausted: bool,
              failed_files=None) -> dict:
    """Build the structured validation_report.

    `issues` are the issues REMAINING after repair (a repaired file contributes
    none), so the counts describe the state the user actually downloads.
    """
    failed_files = set(failed_files or ())
    by_kind = {}
    for issue in issues:
        by_kind.setdefault(issue.kind, []).append(issue)

    # Threshold numerator: the UNION of file paths with an unresolved mechanical
    # defect, so a file with three problems counts once, not three times.
    unresolved = {i.filepath for i in issues if i.kind in UNRESOLVED_KINDS} | failed_files
    # Denominator: files ATTEMPTED, not planned. Day 20 writes a stub for every
    # failed/blocked file, so those are present and land in the numerator above.
    denominator = max(files_checked, len(unresolved))
    rate = (len(unresolved) / denominator) if denominator else 0.0

    return {
        "files_checked": files_checked,
        "syntax_errors_found": len(by_kind.get("syntax", [])),
        "auto_repaired": len(repaired),
        "repair_failed": len(repair_failed),
        "phantom_imports": len(by_kind.get("phantom_import", [])),
        "missing_packages": len(by_kind.get("missing_package", [])),
        "artifact_errors": len(by_kind.get("artifact", [])),
        # Improvement 01. Reported but NOT in UNRESOLVED_KINDS: a shell that
        # forgets a section is a real defect, but it is not a mechanical failure
        # of the file, and letting it move the quality threshold would make the
        # A/B's own instrument respond to the change under test.
        "coherence_warnings": len(by_kind.get("coherence_warning", [])),
        # The layer above parsing (docs/VERIFICATION_GAP_ANALYSIS.md). Counted
        # separately because the REMEDIES differ: a lint finding is repaired in
        # place, a truncated or degenerate file is REGENERATED (the cause is a
        # ceiling or a generation loop, upstream of the file), and import-time
        # I/O is a design finding for the human.
        "manifest_errors": len(by_kind.get("manifest", [])),
        "route_errors": len(by_kind.get("route", [])),
        "lint_errors": len(by_kind.get("lint", [])),
        "lint_info": len(by_kind.get("lint_info", [])),
        "truncated_files": len(by_kind.get("truncated", [])),
        "degenerate_files": len(by_kind.get("degenerate", [])),
        "import_time_io": len(by_kind.get("import_time_io", [])),
        "generation_failed": len(failed_files),
        "repair_calls_spent": repairs_spent_total(retry_counts),
        "repair_ceiling": REPAIR_CEILING_PER_RUN,
        "repair_budget_exhausted": budget_exhausted,
        "degraded_tools": list(degraded_tools),
        "repaired_files": sorted(repaired),
        "repair_failed_files": sorted(repair_failed),
        "unresolved_files": sorted(unresolved),
        "failure_rate": round(rate, 4),
        "quality_threshold": QUALITY_THRESHOLD,
        "below_threshold": rate > QUALITY_THRESHOLD,
        "issues": [i.as_dict() for i in issues],
    }


def render_summary(report: dict) -> str:
    """One-paragraph preamble prepended to the QA report."""
    if not report:
        return ""
    syntax_total = report["syntax_errors_found"] + report["auto_repaired"]
    parts = [
        f"{syntax_total} syntax error{'' if syntax_total == 1 else 's'} found "
        f"({report['auto_repaired']} auto-repaired, {report['syntax_errors_found']} unresolved)",
        f"{report['phantom_imports'] + report['missing_packages']} import warnings",
    ]
    if report["artifact_errors"]:
        parts.append(f"{report['artifact_errors']} invalid JSON/YAML artifacts")
    if report.get("coherence_warnings"):
        parts.append(f"{report['coherence_warnings']} page-composition warnings")
    if report.get("manifest_errors"):
        parts.append(f"{report['manifest_errors']} dependency manifest errors")
    if report.get("route_errors"):
        parts.append(f"{report['route_errors']} route registration errors")
    if report.get("lint_errors"):
        parts.append(f"{report['lint_errors']} undefined/redefined names")
    if report.get("truncated_files"):
        parts.append(f"{report['truncated_files']} truncated files")
    if report.get("degenerate_files"):
        parts.append(f"{report['degenerate_files']} files the generator looped on")
    if report.get("import_time_io"):
        parts.append(f"{report['import_time_io']} modules doing I/O at import time")
    if report["generation_failed"]:
        parts.append(f"{report['generation_failed']} files failed generation")

    lines = [
        f"**AUTOMATED CHECKS**: {', '.join(parts)}. "
        f"Repair budget used: {report['repair_calls_spent']}/{report['repair_ceiling']}."
    ]
    if report.get("files_reviewed"):
        lines.append(
            f"**REVIEW**: {report['files_reviewed']} frontend file(s) reviewed by a second "
            f"model, {report.get('files_revised', 0)} revised. Revisions draw from the same "
            f"repair budget shown above."
            + (f" {report['review_skipped']} not reviewed (budget or reviewer unavailable)."
               if report.get("review_skipped") else ""))
    if report["repair_budget_exhausted"]:
        lines.append(
            "> **Repair budget exhausted.** Remaining failures were left unrepaired. "
            "A run needing this many repairs usually indicates a prompt or architecture "
            "problem rather than isolated bad luck — re-check the generator prompts "
            "before raising the ceiling.")
    for tool in report.get("degraded_tools") or []:
        lines.append(f"> **{tool}** — reduced checking on affected files.")
    if report["below_threshold"]:
        lines.append(
            f"> **Quality below threshold**: {report['failure_rate'] * 100:.0f}% of files "
            f"have unresolved mechanical issues (threshold "
            f"{report['quality_threshold'] * 100:.0f}%).")
    return "\n\n".join(lines)


def qa_context_block(report: dict) -> str:
    """Structured findings handed to the QA agent, with an explicit instruction
    not to re-litigate them.

    The point of the day: DeepSeek R1's reasoning tokens should be spent on
    logic and security, not on missing colons a free parser already found.
    """
    if not report or not report.get("issues"):
        return ""
    lines = [
        "MECHANICAL ISSUES ALREADY DETECTED BY AUTOMATED PARSERS:",
        "These were found deterministically and are already recorded in the report.",
        "Do NOT list them again, and do not spend analysis on syntax, missing",
        "imports, or unparseable config. Focus entirely on logic errors, security",
        "problems, and missing error handling that a parser cannot detect.",
        "",
    ]
    for issue in report["issues"][:40]:
        where = f":{issue['line']}" if issue.get("line") else ""
        lines.append(f"- [{issue['kind']}] {issue['filepath']}{where} — {issue['message']}")
    if len(report["issues"]) > 40:
        lines.append(f"- ...and {len(report['issues']) - 40} more")
    return "\n".join(lines)
