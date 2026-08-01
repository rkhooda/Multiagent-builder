"""Frontend reviewer — Improvement 01.

A second opinion on ONE generated frontend file, returned as a structured
verdict. Parsers (Day 22) catch code that does not parse; nothing until now
caught code that parses fine and is bad — a component that calls an endpoint
nobody implemented, re-invents a shared primitive, or quietly drops the error
state.

WHAT THIS MODULE IS NOT (ponytail #3). It is not a framework and it is not an
orchestration layer. It is a prompt file, a message builder, and a parse. The
LLM call is `call_validated` with a registry validator, exactly like every other
agent; the JSON repair is Day 10/22's one-shot repair, not new machinery; the
concurrency permit, the budget, and the commit all belong to the caller. This
module holds no state and starts no work of its own.

FAIL OPEN, ALWAYS. A reviewer timeout, a malformed verdict that survives its
repair, an exhausted budget, or an exception of any kind resolves to "pass".
Generation proceeding WITHOUT review is a degraded outcome; generation BLOCKED
by a broken reviewer is a failure. That asymmetry decides every error path here.
"""
import json
import os
import re
from dataclasses import dataclass, field
from pathlib import Path

from app.validation import call_validated

SYSTEM_PROMPT = (Path(__file__).resolve().parents[3] / "prompts"
                 / "frontend_reviewer_agent.md").read_text(encoding="utf-8")

# The verdict is a short JSON object. Small on purpose: this is the call that
# has to stay cheap for the whole feature to be affordable, and a reviewer given
# room to ramble writes an essay instead of a verdict.
REVIEW_MAX_TOKENS = 700

# Reviewing a file needs the file. This bounds what a single pathological
# generation can cost us in prompt tokens; real components are ~1-3k chars.
MAX_REVIEWED_FILE_CHARS = 9000

SEVERITY_ORDER = {"critical": 0, "major": 1, "minor": 2}
BLOCKING_SEVERITIES = {"critical", "major"}


@dataclass
class ReviewResult:
    """One file's review outcome. `reviewed=False` means no call was made."""
    reviewed: bool = False
    verdict: str = "pass"
    issues: list = field(default_factory=list)
    coherence_notes: str = ""
    skipped_reason: str = ""      # why no call was made, or why one failed open

    @property
    def needs_revision(self) -> bool:
        return self.reviewed and self.verdict == "revise" and bool(self.blocking_issues)

    @property
    def blocking_issues(self) -> list:
        return [i for i in self.issues if i.get("severity") in BLOCKING_SEVERITIES]

    def as_state(self) -> dict:
        """The compact record kept per file in state and surfaced at Gate 4."""
        record = {"reviewed": self.reviewed, "verdict": self.verdict,
                  "issues_found": len(self.issues)}
        if self.skipped_reason:
            record["skipped_reason"] = self.skipped_reason
        if self.coherence_notes:
            record["coherence_notes"] = self.coherence_notes[:400]
        return record


# ── Verdict parsing ──────────────────────────────────────────────────────────

def extract_verdict_json(text: str):
    """(data, error). Tolerant of a fenced or prose-wrapped object.

    ponytail: a brace-balanced scan, not a JSON grammar. The prompt demands a
    bare object and the validator + repair handle the case where the model
    ignored it; this only has to survive the two things models actually do —
    wrap it in ```json, or add a sentence before it.
    """
    if not (text or "").strip():
        return None, "Response was empty"
    body = text.strip()
    fenced = re.search(r"```(?:json)?\s*(.+?)\s*```", body, re.DOTALL)
    if fenced:
        body = fenced.group(1).strip()
    start = body.find("{")
    if start == -1:
        return None, "No JSON object found in the response"
    depth, end = 0, -1
    for i, ch in enumerate(body[start:], start):
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                end = i
                break
    if end == -1:
        return None, "JSON object is not closed — the response was cut off"
    try:
        data = json.loads(body[start:end + 1])
    except json.JSONDecodeError as e:
        return None, f"Response is not valid JSON: {e}"
    if not isinstance(data, dict):
        return None, "Top-level value must be a JSON object"
    return data, ""


def _normalise(data: dict) -> ReviewResult:
    issues = []
    for issue in (data.get("issues") or []):
        if not isinstance(issue, dict):
            continue
        try:
            line = int(issue.get("line") or 0)
        except (TypeError, ValueError):
            line = 0
        issues.append({
            "severity": issue.get("severity", "minor"),
            "line": line,
            "problem": str(issue.get("problem") or "").strip()[:300],
            "fix_hint": str(issue.get("fix_hint") or "").strip()[:300],
        })
    issues.sort(key=lambda i: SEVERITY_ORDER.get(i["severity"], 3))
    return ReviewResult(
        reviewed=True,
        verdict=data.get("verdict", "pass"),
        issues=issues,
        coherence_notes=str(data.get("coherence_notes") or "").strip(),
    )


# ── The review call ──────────────────────────────────────────────────────────

def build_review_context(task: dict, content: str, generation_context: str,
                         parser_findings: list = None) -> str:
    """The reviewer's user message.

    Reuses the coder's OWN context verbatim rather than re-deriving a second
    view of the truth (ponytail #3): the reviewer must judge the file against
    exactly what the generator was told, and rebuilding that from state would
    let the two drift — at which point the reviewer starts filing issues for
    endpoints the coder was never shown.
    """
    findings = parser_findings or []
    parts = [
        "THE SPEC THIS FILE WAS GENERATED FROM",
        generation_context,
        "",
        f"THE GENERATED FILE — {task.get('filepath', '')}",
        (content or "")[:MAX_REVIEWED_FILE_CHARS],
    ]
    if findings:
        parts += [
            "",
            "AUTOMATED PARSER FINDINGS (already recorded — do NOT report these again)",
            *[f"- {f}" for f in findings[:10]],
        ]
    else:
        parts += [
            "",
            "AUTOMATED PARSER FINDINGS: none. This file parses cleanly, so do not "
            "report syntax, formatting or import-resolution defects at all.",
        ]
    parts += ["", "Return only the JSON verdict object."]
    return "\n".join(parts)


def review_file(task: dict, content: str, generation_context: str, state: dict,
                parser_findings: list = None) -> ReviewResult:
    """Review ONE file. Never raises — every failure path returns a pass.

    Runs inside the coder's worker thread under the worker's existing permit, so
    it takes no new semaphore and adds no scheduling. `log=None` for the same
    reason `call_validated` is called that way by the coder: a worker thread must
    not append to the shared log list off the event loop.
    """
    filepath = task.get("filepath", "")
    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": build_review_context(
            task, content, generation_context, parser_findings)},
    ]
    try:
        raw = call_validated(
            messages, "frontend_review", state, max_tokens=REVIEW_MAX_TOKENS,
            original_instruction=(
                "Output ONLY the JSON verdict object — no fences, no prose. "
                "It must have exactly the keys verdict, issues and coherence_notes."),
            log=None,
            label=filepath,
        )
    except Exception as e:                        # noqa: BLE001 — fail open, always
        print(f"[Reviewer] {filepath}: review failed, treating as pass ({e})", flush=True)
        return ReviewResult(reviewed=False, skipped_reason=f"review_failed: {str(e)[:120]}")

    data, error = extract_verdict_json(raw)
    if data is None:
        # Belt and braces: the validator should have caught this and repaired it,
        # so reaching here means both attempts produced unparseable output.
        print(f"[Reviewer] {filepath}: unparseable verdict after repair ({error})", flush=True)
        return ReviewResult(reviewed=False, skipped_reason="unparseable_verdict")

    result = _normalise(data)
    print(f"[Reviewer] {filepath}: {result.verdict} "
          f"({len(result.blocking_issues)} blocking, {len(result.issues)} total)", flush=True)
    return result


# ── The revision prompt ──────────────────────────────────────────────────────

# Same shape as validation_pass.REPAIR_PROMPT deliberately: "here is the file,
# here is precisely what is wrong, return the whole corrected file and change
# nothing else". That framing is what keeps a repair from turning into a rewrite,
# and it is already the proven one in this codebase.
REVISION_PROMPT = (
    "A senior reviewer found these problems in {filepath}:\n{issues}\n\n"
    "CURRENT CONTENT:\n{content}\n\n"
    "Output the complete corrected file only — no explanation, no markdown "
    "fences, no preamble. Address every problem listed above and keep everything "
    "else exactly as it is."
)


def build_revision_message(filepath: str, content: str, issues: list) -> str:
    lines = []
    for issue in issues:
        where = f" (line {issue['line']})" if issue.get("line") else ""
        hint = f" Fix: {issue['fix_hint']}" if issue.get("fix_hint") else ""
        lines.append(f"- [{issue.get('severity', 'major')}]{where} {issue.get('problem', '')}{hint}")
    return REVISION_PROMPT.format(
        filepath=filepath, issues="\n".join(lines), content=content)


# ── Triggering ───────────────────────────────────────────────────────────────

def review_mode() -> str:
    """off | selective | all. Default selective."""
    mode = (os.getenv("REVIEW_MODE") or "selective").strip().lower()
    return mode if mode in ("off", "selective", "all") else "selective"


def _is_shared_primitive(filepath: str, dependent_count: int) -> bool:
    low = (filepath or "").lower()
    return ("/components/ui/" in low or "/components/common/" in low
            or "/lib/" in low or dependent_count >= 2)


def should_review(task: dict, processed_warnings: list, dependent_count: int,
                  mode: str = None) -> tuple:
    """(review?, reason). ONE readable predicate, env-tunable — ponytail #2.

    Reviewing every frontend file is the version of this feature that makes the
    system unusable on free tiers. Selective spends the calls where a defect
    propagates or is likeliest:

      - a page SHELL or a decomposed page: it composes other files, so a mistake
        here breaks a whole screen rather than one card;
      - a SHARED PRIMITIVE: many files import it, so its defects multiply;
      - a file whose own processing already produced warnings: the parsers found
        smoke, and a second opinion is cheapest exactly there;
      - a HIGH-complexity task: the biggest job given to one call.

    Everything else is skipped. On the Task 0 baseline's 51 frontend files this
    fires on roughly a dozen, not all of them.
    """
    mode = mode or review_mode()
    if mode == "off":
        return False, "review_off"
    filepath = task.get("filepath", "")
    if mode == "all":
        return True, "review_all"

    from .utils import is_page_task
    if is_page_task(filepath):
        return True, "page_shell"
    if _is_shared_primitive(filepath, dependent_count):
        return True, "shared_primitive"
    if processed_warnings:
        return True, "validation_warnings"
    if (task.get("estimated_complexity") or "").lower() == "high":
        return True, "high_complexity"
    return False, "not_selected"
