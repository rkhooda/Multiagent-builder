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


def min_code_lines(n: int = 5):
    """For coder outputs, after fence-stripping. The floor distinguishes real
    code from empty/prose responses (e.g. the empty replies free-tier models
    sometimes return) while permitting legitimately small files — a minimal
    axios client (src/lib/api.js) is ~7 lines, so 10 was too strict."""
    def check(text, state):
        from app.utils.code_cleaner import strip_code_fences
        lines = [l for l in strip_code_fences(text).splitlines() if l.strip()]
        return [] if len(lines) >= n else [f"Code output has only {len(lines)} non-empty lines — minimum {n}"]
    return check


def syntax_of(filepath: str):
    """Day 22: a real parser check for ONE file, as a registry validator.

    Deliberately a closure over the filepath rather than reading it from
    `state`: coder workers run in parallel threads against one shared state
    dict, so a "current file" key there would race. The closure is per-call and
    thread-confined.

    Returns problems as plain strings carrying the exact parser message and
    line, which REPAIR_PROMPT interpolates verbatim — so the precision repair
    prompt ("syntax error at line 42: ...") needs no separate machinery, it is
    just what this validator returned.

    Python/JSON/YAML only. JS/JSX needs a node subprocess and is batched
    post-phase in the validation_pass node — spawning node per file inside
    parallel workers is the latency trap we avoid by construction.
    """
    def check(text, state):
        from app.utils.code_cleaner import strip_code_fences
        from app.validation.syntax import validate_content
        # Line numbers refer to the fence-stripped code, which is what the model
        # is asked to re-emit, so they line up with what it sees.
        return [i.describe() for i in validate_content(strip_code_fences(text), filepath)]
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


def _architecture_specificity(text, state):
    """Day 21: mechanically enforce the specificity rules the coders depend on.

    A prompt rule alone does not survive model drift — the model quietly stops
    following it and nothing notices until generated code is wrong. These are the
    cheap-to-check half of the Day 21 architecture rules, so a regression fails
    validation and triggers the repair pass instead of shipping a vague blueprint.

    Only rules with a real, measured defect behind them are enforced here. The
    folder-tree shortcut rules are deliberately NOT checked: real output measured
    0 occurrences of `...` and `[more`, so a validator would be dead weight.
    """
    import re as _re
    from app.agents.context_builder import split_sections, _find_section, parse_api_table

    problems = []
    sections = split_sections(text)

    # D7 — every endpoint row needs a concrete example response body. Without it
    # the frontend and backend invent two different shapes for one resource.
    api = _find_section(sections, ["api endpoint", "api routes", "endpoints", "rest api"])
    if api:
        rows = parse_api_table(api)
        if rows:
            missing = [r.get("path", "?") for r in rows if not (r.get("response") or "").strip()]
            if missing:
                problems.append(
                    f"{len(missing)} API endpoint rows have an empty or missing Response "
                    f"column (e.g. {missing[:3]}). Every row needs a concrete one-line "
                    f"example JSON body — add a `Response` column if the table lacks one.")
            vague = [r.get("path", "?") for r in rows
                     if (r.get("response") or "").strip()
                     and not _re.search(r"[{\[]", r.get("response", ""))]
            if vague:
                problems.append(
                    f"{len(vague)} Response cells contain no JSON (e.g. {vague[:3]}). "
                    f"Give a literal example body like "
                    f'`{{"id": 1, "title": "string"}}`, not a type name or prose.')

    # D2 — the component tree must name props, or every consumer guesses.
    comp = _find_section(sections, ["component hierarchy", "component"])
    if comp and "props:" not in comp.lower():
        problems.append(
            "The component hierarchy names no props. Annotate every component as "
            "`- NoteList (props: notes, onDelete)`, or `(props: none)` — prop names "
            "are the contract between a page and its children.")

    # D17 — Python-only files inside a JS tree (and the reverse).
    from app.agents.utils import parse_folder_structure
    files = parse_folder_structure(text)
    foreign = []
    for p in files:
        base = p.rsplit("/", 1)[-1]
        in_frontend = _re.search(r"(^|/)frontend(/|$)", p) is not None
        # `__init__.js` is the Python package convention leaking into a JS tree —
        # the real NotesTags run planned four of them. Any .py under frontend/ is
        # the same mistake in the other direction.
        if base.startswith("__init__") and not base.endswith(".py"):
            foreign.append(p)
        elif in_frontend and p.endswith(".py"):
            foreign.append(p)
    if foreign:
        problems.append(
            f"Language-foreign files in the tree: {foreign[:4]}. JavaScript has no "
            f"__init__ module convention — a JS/TS directory needs no index marker "
            f"file, and a frontend tree never contains .py files.")
    return problems


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
    "architecture": [min_length(800), _architecture_quality, _architecture_specificity],
    "planning": [_valid_plan],
    "frontend_code": [min_code_lines(5)],
    "backend_code": [min_code_lines(5)],
}

REPAIR_PROMPT = (
    "Your previous response failed validation:\n{errors}\n\n"
    "Regenerate the complete output correcting these problems. {original_instruction}"
)


def run_validators(agent_name: str, text: str, state: dict, extra: list = None) -> list:
    validators = list(VALIDATORS.get(agent_name, [])) + list(extra or [])
    return [problem for validator in validators for problem in validator(text, state)]


def call_validated(messages: list, agent_type: str, state: dict, max_tokens=4000,
                   original_instruction: str = "", log: list = None,
                   extra_validators: list = None, repair_tally: list = None) -> str:
    """call_llm + registry validation + ONE repair attempt.

    Second validation failure raises LLMOutputError for the error boundary.

    `extra_validators` carries per-call checks the static registry cannot express
    because they depend on which FILE is being generated (Day 22 syntax_of).
    They participate in the same single repair attempt — a syntax error and a
    length problem are fixed by one call, not two, which is what keeps the
    write-time repair spend at exactly one per file.

    `repair_tally` is a caller-owned list appended to when a repair call is
    spent. It exists so write-time repairs charge the SAME per-file budget as
    the Day 22 validation pass without this function touching shared state:
    coder workers run in parallel threads, so incrementing state["retry_counts"]
    here would race. The worker owns the list; the coordinator folds the count
    in via commit_generated_file.
    """
    response = call_llm(messages, agent_type, max_tokens=max_tokens)
    problems = run_validators(agent_type, response, state, extra_validators)
    if not problems:
        return response

    if log is not None:
        log.append(f"{agent_type}_agent: validation failed ({len(problems)} problems), sending repair prompt")
    print(f"[Validation] {agent_type}: {problems[:5]} — repairing", flush=True)

    repair = REPAIR_PROMPT.format(
        errors="\n".join(f"- {p}" for p in problems[:8]),
        original_instruction=original_instruction,
    )
    # Providers reject empty assistant messages (Cohere 400s on them), and an
    # empty response has nothing worth echoing back anyway.
    repair_messages = messages + (
        [{"role": "assistant", "content": response}] if response.strip() else []
    ) + [{"role": "user", "content": repair}]
    if repair_tally is not None:
        repair_tally.append(agent_type)
    response = call_llm(repair_messages, agent_type, max_tokens=max_tokens)
    problems = run_validators(agent_type, response, state, extra_validators)
    if problems:
        raise LLMOutputError(
            f"Output failed validation after one repair attempt: {'; '.join(problems[:5])}",
            agent_type, "")
    if log is not None:
        log.append(f"{agent_type}_agent: repair succeeded")
    return response
