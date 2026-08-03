import os
import re
import time
from pathlib import Path
from ..llm_router import call_llm
from ..utils.file_writer import write_project_file

SYSTEM_PROMPT = (Path(__file__).resolve().parents[3] / "prompts" / "qa_agent.md").read_text(encoding="utf-8")

MAX_BATCH_CHARS = 60000  # ~15K tokens at ~4 chars/token
MAX_AUTO_FIXES_PER_FILE = 1


def qa_batch_size() -> int:
    """Batch size 3 is load-bearing for cost: per-file review would triple the
    call count. Read at call time so tests and operators can tune it."""
    env = os.getenv("QA_BATCH_SIZE") or ""
    return max(1, int(env)) if env.strip().isdigit() else 3

# MEASURED, not guessed (2026-08-03, Improvement 02 Task 0). QA's primary,
# gemini-2.5-flash, is a THINKING model: it spent 2,427-2,740 completion tokens
# on reasoning per 3-file batch before emitting any answer text, and the worst
# observed complete response was 4,949 tokens. At the old ceiling of 3,000 the
# answer intermittently had no room left — surfacing not as a truncation but as
# an ERROR and a silent groq failover (the Improvement-01 trap one layer down).
# Locked by test_token_budgets.test_qa_ceiling_covers_measured_requirement.
QA_MAX_TOKENS = 6000

ISSUE_LINE_RE = re.compile(
    r'^\s*\d+[\.\)]\s*\[(CRITICAL|WARNING|INFO)\](\[TRIVIAL\])?\s*([^\s:]+)(?::(\d+))?\s*[-–]\s*(.+)$',
    re.IGNORECASE | re.MULTILINE
)

REVIEW_INSTRUCTION = """Review this code for bugs, security issues, and missing error handling.
{automated_block}

Output ONLY a numbered list of specific issues, one per line, in exactly this format:
N. [SEVERITY][TRIVIAL] filepath:line - description

- SEVERITY must be one of CRITICAL, WARNING, or INFO.
- Include the [TRIVIAL] tag ONLY if the issue is nothing more than a missing import statement or an obvious typo (misspelled identifier) that can be mechanically fixed without changing logic. Omit the tag for everything else.
- filepath must be one of the file paths shown in the "=== FILE: ... ===" headers below.
- If there are no issues in a file, do not invent one.
- Do not include any prose, headers, or explanation outside the numbered list.
- If you find no issues at all in this batch, output exactly: No issues found.

FILES TO REVIEW:
{files_block}"""

AUTO_FIX_INSTRUCTION = """The following file has a trivial issue flagged during QA review:

ISSUE: {issue}

CURRENT FILE CONTENT ({filepath}):
{content}

Fix ONLY this trivial issue (missing import or typo). Do not change any other logic, formatting, or structure. Output the complete corrected file content — no explanation, no markdown fences, no preamble."""


def _chunk_files(generated_files: dict) -> list:
    """Split files into batches of up to qa_batch_size(); oversized files get
    their own batch. The identical rule drives the incremental stream's
    should_flush, so batch composition — and therefore call count — is the same
    whether files arrive all at once or one commit at a time."""
    batches = []
    current = []
    current_chars = 0

    for filepath, content in generated_files.items():
        content = content or ""
        content_len = len(content)

        if content_len > MAX_BATCH_CHARS:
            if current:
                batches.append(current)
                current = []
                current_chars = 0
            batches.append([(filepath, content)])
            continue

        if len(current) >= qa_batch_size() or (current and current_chars + content_len > MAX_BATCH_CHARS):
            batches.append(current)
            current = []
            current_chars = 0

        current.append((filepath, content))
        current_chars += content_len

    if current:
        batches.append(current)

    return batches


def _format_files_block(batch: list) -> str:
    parts = []
    for filepath, content in batch:
        parts.append(f"=== FILE: {filepath} ===\n{content}")
    return "\n\n".join(parts)


def _parse_issues(raw_output: str, batch_files: list) -> list:
    """Parse the model's numbered-list output into structured issue dicts."""
    issues = []
    matches = list(ISSUE_LINE_RE.finditer(raw_output))

    if not matches:
        stripped = raw_output.strip()
        if stripped and "no issues found" not in stripped.lower():
            issues.append({
                "severity": "WARNING",
                "trivial": False,
                "file": batch_files[0] if batch_files else None,
                "description": f"Unparsed QA output for this batch: {stripped[:500]}"
            })
        return issues

    for match in matches:
        severity = match.group(1).upper()
        trivial = bool(match.group(2))
        raw_filepath = match.group(3)
        line_no = match.group(4)
        description = match.group(5).strip()

        filepath = raw_filepath if raw_filepath in batch_files else None
        if filepath is None:
            candidates = [f for f in batch_files if f.endswith(raw_filepath) or raw_filepath.endswith(Path(f).name)]
            filepath = candidates[0] if candidates else raw_filepath

        issues.append({
            "severity": severity if severity in ("CRITICAL", "WARNING", "INFO") else "INFO",
            "trivial": trivial,
            "file": filepath,
            "line": line_no,
            "description": description
        })

    return issues


def review_batch(batch: list, automated_block: str, *, project_id: str = "",
                 fast_mode: bool = False, batch_id: int = 0) -> list:
    """Review ONE batch of (filepath, content) pairs. PURE with respect to
    shared state: no state mutation, no broadcasts — callable from the QA node
    or from the incremental stream's consumer thread, at any time. Raises on
    LLM failure; the caller owns the per-batch tolerance (count the degraded
    event, log, continue).

    Findings carry identity for out-of-order aggregation: batch_id and
    reviewed_at (epoch seconds — reviewed_at vs generation end is what the
    qa_overlap_ratio is computed from). sort_findings() makes the final report
    independent of completion order.
    """
    batch_files = [f for f, _ in batch]
    user_content = REVIEW_INSTRUCTION.format(
        files_block=_format_files_block(batch),
        automated_block=automated_block or "")
    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": user_content},
    ]
    raw_output = call_llm(messages, "qa", max_tokens=QA_MAX_TOKENS,
                          project_id=project_id, fast_mode=fast_mode,
                          label=f"qa_batch:{batch_id}")
    issues = _parse_issues(raw_output, batch_files)
    reviewed_at = time.time()
    for issue in issues:
        issue["batch_id"] = batch_id
        issue["reviewed_at"] = reviewed_at
    return issues


def sort_findings(issues: list) -> list:
    """Deterministic order regardless of batch completion order: same findings
    in, byte-identical report text out."""
    def key(issue):
        try:
            line = int(issue.get("line") or 0)
        except (TypeError, ValueError):
            line = 0
        return (issue.get("file") or "", line,
                issue.get("severity") or "", issue.get("description") or "")
    return sorted(issues, key=key)


def _build_report(project_name: str, files_reviewed: int, issues: list, auto_fixed_files: list) -> str:
    critical = [i for i in issues if i["severity"] == "CRITICAL"]
    warnings = [i for i in issues if i["severity"] == "WARNING"]
    info = [i for i in issues if i["severity"] == "INFO"]

    def format_group(group):
        if not group:
            return "No issues identified."
        lines = []
        for issue in group:
            loc = issue["file"] or "unknown file"
            if issue.get("line"):
                loc = f"{loc}:{issue['line']}"
            lines.append(f"- **File**: `{loc}`\n  - *Issue*: {issue['description']}")
        return "\n".join(lines)

    summary = (
        f"**Files Reviewed**: {files_reviewed}  \n"
        f"**Critical**: {len(critical)} | **Warnings**: {len(warnings)} | **Info**: {len(info)}  \n"
        f"**Auto-Fixes Applied**: {len(auto_fixed_files)}"
        + (f" ({', '.join(auto_fixed_files)})" if auto_fixed_files else "")
    )

    return f"""# QA Report: {project_name}

## Summary
{summary}

## Critical
{format_group(critical)}

## Warnings
{format_group(warnings)}

## Info
{format_group(info)}
"""


def qa_agent(state: dict) -> dict:
    """
    QA Agent — Phase 4 Code Generation (batched review)

    Reads: state['generated_files'], state['project_name']
    Writes: state['qa_report'], state['qa_issues_count'], state['generated_files']
            (trivial auto-fixes applied), state['log'], state['errors']

    Routing lives in docs/PROVIDERS.md (gemini-2.5-flash primary as of
    2026-08-03). Reviews files in batches (qa_batch_size, default 3),
    tolerating individual batch failures without failing the whole stage.
    """
    generated_files = state.get("generated_files", {})
    project_name = state.get("project_name", "Unknown Project")
    project_id = state.get("project_id", "")
    fast_mode = bool(state.get("fast_mode"))
    log = state.get("log", [])
    errors = state.get("errors", [])

    from ..core.connection_manager import manager

    print(f"[QAAgent] Starting for project: {project_name}")
    log.append("qa_agent: started")

    if not generated_files:
        warning = "qa_agent: no generated files to review"
        log.append(warning)
        print(f"[QAAgent] WARNING: {warning}")
        return {
            "qa_report": f"# QA Report: {project_name}\n\nNo generated files were available to review.",
            "qa_issues_count": 0,
            "log": log,
            "errors": errors,
            "current_stage": "devops"
        }

    # Day 22: hand the reasoning model what the parsers already found, and tell
    # it not to re-litigate. R1's tokens should go to logic and security, not to
    # missing colons a free parser caught before it ever ran.
    from ..validation.report import qa_context_block, render_summary
    validation_report = state.get("validation_report") or {}
    automated_block = qa_context_block(validation_report)
    if automated_block:
        automated_block = "\n" + automated_block + "\n"

    batches = _chunk_files(generated_files)
    all_issues = []
    failed_batches = 0
    last_batch_error = None

    for i, batch in enumerate(batches):
        batch_files = [f for f, _ in batch]
        print(f"[QAAgent] Reviewing batch {i + 1}/{len(batches)}: {batch_files}")

        try:
            batch_issues = review_batch(batch, automated_block,
                                        project_id=project_id,
                                        fast_mode=fast_mode, batch_id=i + 1)
            all_issues.extend(batch_issues)

            log.append(f"qa_agent: batch {i + 1}/{len(batches)} reviewed ({len(batch_issues)} issues found)")

        except Exception as e:
            # Inner tolerance layer: one dead batch degrades the report, it
            # doesn't fail the stage. A TOTAL wipeout is different — re-raised
            # below so the error boundary pauses instead of shipping a
            # "0 issues" report that nothing was actually reviewed for.
            failed_batches += 1
            last_batch_error = e
            error_msg = f"qa_agent: batch {i + 1}/{len(batches)} failed ({batch_files}): {e}"
            errors.append(error_msg)
            print(f"[QAAgent] ERROR: {error_msg}")
            from ..observability import degraded
            degraded.record(project_id, "qa_batch_failed")

        manager.broadcast_sync(project_id, {
            "type": "qa_batch_complete",
            "batch": i + 1,
            "total_batches": len(batches),
            "issues_found_so_far": len(all_issues)
        })

    if batches and failed_batches == len(batches):
        raise last_batch_error

    # ── Trivial auto-fix (missing imports / typos only, 1 attempt per file) ──
    auto_fixed_files = []
    fix_attempted_files = set()

    for issue in all_issues:
        if not issue.get("trivial"):
            continue
        filepath = issue.get("file")
        if not filepath or filepath not in generated_files or filepath in fix_attempted_files:
            continue

        fix_attempted_files.add(filepath)

        try:
            fix_messages = [
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": AUTO_FIX_INSTRUCTION.format(
                    issue=issue["description"],
                    filepath=filepath,
                    content=generated_files[filepath]
                )}
            ]
            fixed_content = call_llm(fix_messages, "qa", max_tokens=QA_MAX_TOKENS,
                                      project_id=project_id, label=filepath,
                                      fast_mode=fast_mode)

            if len(fixed_content.strip()) < 20:
                continue

            result = write_project_file(project_id, filepath, fixed_content)
            if result["success"]:
                generated_files[filepath] = fixed_content
                auto_fixed_files.append(filepath)
                log.append(f"qa_agent: auto-fixed trivial issue in {filepath}")
            else:
                errors.append(f"qa_agent: auto-fix write failed for {filepath}: {result['error']}")

        except Exception as e:
            error_msg = f"qa_agent: auto-fix failed for {filepath}: {e}"
            errors.append(error_msg)
            print(f"[QAAgent] ERROR: {error_msg}")

    # Fold the backend coder's write-time import warnings (Day 19 import fixer)
    # into the report so unresolved imports surface in the Gate 4 QA panel with
    # their file reference — the fixer flags what it cannot safely rewrite.
    for entry in errors:
        if not isinstance(entry, str) or not entry.startswith("import_warning:"):
            continue
        body = entry[len("import_warning:"):].strip()
        fpath, _, desc = body.partition(": ")
        all_issues.append({
            "severity": "WARNING",
            "trivial": False,
            "file": fpath.strip() or None,
            "line": None,
            "description": (desc or body).strip(),
        })

    all_issues = sort_findings(all_issues)
    qa_report = _build_report(project_name, len(generated_files), all_issues, auto_fixed_files)
    # Prepend the automated-checks summary so the Gate 4 QA panel leads with what
    # the machine established deterministically, before the model's opinions.
    summary = render_summary(validation_report)
    if summary:
        qa_report = qa_report.replace("## Summary\n", f"## Summary\n{summary}\n\n", 1)
    qa_issues_count = len(all_issues)

    log.append(
        f"qa_agent: completed — {len(generated_files)} files reviewed, "
        f"{qa_issues_count} issues found, {len(auto_fixed_files)} auto-fixed"
    )
    print(f"[QAAgent] Done. {qa_issues_count} issues found, {len(auto_fixed_files)} auto-fixed")

    manager.broadcast_sync(project_id, {
        "type": "agent_complete",
        "agent": "qa",
        "stage": "qa",
        "output_preview": qa_report[:200],
        "qa_issues_count": qa_issues_count
    })

    return {
        "generated_files": generated_files,
        "qa_report": qa_report,
        "qa_issues_count": qa_issues_count,
        "log": log,
        "errors": errors,
        "current_stage": "devops",
        "_agent_event": True
    }
