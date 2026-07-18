"""Unified per-agent output validation + one-shot repair.

The ONLY place output-quality rules live (the Day 7 research retry, the
requirements length/section retries, the architecture repair loop, and the
Day 10 planning repair loop all dissolved into this registry).

Validators are fn(text, state) -> list[str] of problems. call_validated runs
call_llm, validates, sends ONE shared repair prompt on failure, re-validates,
and raises LLMOutputError on the second failure — the error boundary surfaces
that for user recovery. Repair needs the original message context, so agents
call this instead of call_llm; the boundary stays repair-free.
"""
import json

from app.exceptions import LLMOutputError
from app.llm_router import call_llm


def min_length(n: int):
    def check(text, state):
        return [] if len(text) >= n else [f"Output is only {len(text)} chars — minimum {n} required"]
    return check


def required_sections(sections: list):
    def check(text, state):
        low = text.lower()
        return [f"Missing required section: {s}" for s in sections if s.lower() not in low]
    return check


def min_code_lines(n: int = 10):
    """For coder outputs, after fence-stripping."""
    def check(text, state):
        from app.utils.code_cleaner import strip_code_fences
        lines = [l for l in strip_code_fences(text).splitlines() if l.strip()]
        return [] if len(lines) >= n else [f"Code output has only {len(lines)} non-empty lines — minimum {n}"]
    return check


def _research_quality(text, state):
    from app.agents.research_agent import validate_report_quality
    try:
        sections = json.loads(state.get("optional_sections") or "{}")
    except (json.JSONDecodeError, TypeError):
        sections = {}
    _, issues = validate_report_quality(text, sections)
    return issues


def _requirements_sections(text, state):
    from app.agents.requirements_agent import validate_requirements_doc
    _, missing = validate_requirements_doc(text)
    return [f"Missing required section: {s}" for s in missing]


def _architecture_quality(text, state):
    from app.agents.architecture_agent import validate_architecture_doc, MIN_LENGTH
    from app.agents.utils import parse_folder_structure, extract_mermaid_diagrams
    problems = []
    if len(text) < MIN_LENGTH:
        problems.append(f"Document is too short at {len(text)} characters; target at least {MIN_LENGTH}.")
    _, missing = validate_architecture_doc(text)
    problems.extend(f"Missing required section: {s}" for s in missing)
    if len(parse_folder_structure(text)) < 5:
        problems.append("Folder structure was not parseable — keep the full tree inside one closed ```text fence, every file explicitly named.")
    if len(extract_mermaid_diagrams(text)) < 1:
        problems.append("No Mermaid diagram blocks found — include one erDiagram and one flowchart.")
    return problems


def _valid_plan(text, state):
    from app.agents.utils import parse_and_validate_plan
    from app.agents.planning_agent import MIN_TASKS
    plan, errors = parse_and_validate_plan(text)
    if plan is None:
        return errors or ["No valid JSON task array found"]
    if len(plan.tasks) < MIN_TASKS:
        errors.append(f"Only {len(plan.tasks)} tasks found, need at least {MIN_TASKS}")
    required = set(state.get("file_list") or [])
    missing = sorted(required - {t.filepath for t in plan.tasks})
    if missing:
        errors.append(f"Plan is missing {len(missing)} required file tasks. Examples: {missing[:5]}")
    return errors


VALIDATORS = {
    "research": [min_length(500), _research_quality],
    "requirements": [min_length(500), _requirements_sections],
    "architecture": [min_length(800), _architecture_quality],
    "planning": [_valid_plan],
    "frontend_code": [min_code_lines(10)],
    "backend_code": [min_code_lines(10)],
}

REPAIR_PROMPT = (
    "Your previous response failed validation:\n{errors}\n\n"
    "Regenerate the complete output correcting these problems. {original_instruction}"
)


def run_validators(agent_name: str, text: str, state: dict) -> list:
    return [problem for validator in VALIDATORS.get(agent_name, []) for problem in validator(text, state)]


def call_validated(messages: list, agent_type: str, state: dict, max_tokens=4000,
                   original_instruction: str = "", log: list = None) -> str:
    """call_llm + registry validation + ONE repair attempt.

    Second validation failure raises LLMOutputError for the error boundary.
    """
    response = call_llm(messages, agent_type, max_tokens=max_tokens)
    problems = run_validators(agent_type, response, state)
    if not problems:
        return response

    if log is not None:
        log.append(f"{agent_type}_agent: validation failed ({len(problems)} problems), sending repair prompt")
    print(f"[Validation] {agent_type}: {problems[:5]} — repairing", flush=True)

    repair = REPAIR_PROMPT.format(
        errors="\n".join(f"- {p}" for p in problems[:8]),
        original_instruction=original_instruction,
    )
    repair_messages = messages + [
        {"role": "assistant", "content": response},
        {"role": "user", "content": repair},
    ]
    response = call_llm(repair_messages, agent_type, max_tokens=max_tokens)
    problems = run_validators(agent_type, response, state)
    if problems:
        raise LLMOutputError(
            f"Output failed validation after one repair attempt: {'; '.join(problems[:5])}",
            agent_type, "")
    if log is not None:
        log.append(f"{agent_type}_agent: repair succeeded")
    return response
