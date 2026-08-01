import json
from pathlib import Path

from ..validation import call_validated
from .context_builder import build_ui_contract
from .utils import (build_feedback_prompt, decomposition_enabled, parse_and_validate_plan,
                    regeneration_target, truncate_for_context)


SYSTEM_PROMPT = (
    Path(__file__).resolve().parents[3] / "prompts" / "planning_agent.md"
).read_text(encoding="utf-8")

MIN_TASKS = 8

# Section 4b of the prompt is the decomposition spec. It is ONE prompt file (the
# living spec, per the project's prompt rules) with the section excised at call
# time when the feature is off — two prompt files would drift apart, and a
# feature flag that still sends the instructions is not a rollback.
_DECOMPOSITION_HEADING = "## 4b. Frontend Page Decomposition"
_NEXT_HEADING = "## 5. Output Format"


def _system_prompt() -> str:
    """The planning prompt, with the decomposition section stripped when
    DECOMPOSE_FRONTEND is off. Resolved per call, not at import: the flag is a
    property of the run, and tests flip it between runs in one process."""
    if decomposition_enabled():
        return SYSTEM_PROMPT
    start = SYSTEM_PROMPT.find(_DECOMPOSITION_HEADING)
    end = SYSTEM_PROMPT.find(_NEXT_HEADING, start + 1)
    if start == -1 or end == -1:
        return SYSTEM_PROMPT
    return SYSTEM_PROMPT[:start] + SYSTEM_PROMPT[end:]


def _get_planning_max_tokens(file_count: int) -> int:
    """Scale output budget for large architectures that need one task per file."""
    return min(32000, max(4500, file_count * 300))


def planning_agent(state: dict) -> dict:
    """
    Implementation Planner Agent - Phase 2 Core Intelligence

    Reads: state['architecture_doc'], state['file_list'], state['tech_stack'],
           state['project_name'], state['brief']
    Writes: state['implementation_plan'], state['log'], state['current_stage']

    Calls Gemini 2.5 Flash - strong at structured JSON output and task decomposition.
    Outputs a validated JSON array of TaskSchema objects.
    Enforces correct dependency ordering: database -> backend -> frontend -> devops.
    """
    architecture_doc = state.get("architecture_doc", "")
    file_list = state.get("file_list", [])
    tech_stack_str = state.get("tech_stack", "")
    project_name = state.get("project_name", "Unknown Project")
    brief = state.get("brief", "")
    log = list(state.get("log", []))
    errors = list(state.get("errors", []))

    human_feedback = state.get("human_feedback", "")
    previous_plan = regeneration_target(state, "implementation_plan")

    print(f"[PlanningAgent] Starting for project: {project_name}")
    log.append(f"planning_agent: started - {len(file_list)} files in file_list")

    if previous_plan:
        log.append("planning_agent: re-planning with human feedback")

    if not architecture_doc:
        error_msg = "planning_agent: architecture_doc is empty - cannot create plan without architecture"
        log.append(error_msg)
        errors.append(error_msg)
        print(f"[PlanningAgent] ERROR: {error_msg}")
        return {
            "implementation_plan": "[]",
            "log": log,
            "errors": errors,
            "current_stage": "frontend_code",
        }

    tech_stack_formatted = ""
    if tech_stack_str:
        try:
            stack = json.loads(tech_stack_str)
            tech_stack_formatted = (
                f"Frontend: {stack.get('frontend', 'React')}\n"
                f"Backend: {stack.get('backend', 'FastAPI')}\n"
                f"Database: {stack.get('database', 'PostgreSQL')}\n"
                f"Key Libraries: {', '.join(stack.get('key_libraries', []))}"
            )
        except Exception:
            tech_stack_formatted = tech_stack_str

    file_list_formatted = (
        "\n".join(f"  - {file_path}" for file_path in file_list)
        if file_list
        else "No file list available"
    )
    planning_max_tokens = _get_planning_max_tokens(len(file_list))

    truncated_arch = truncate_for_context(architecture_doc, max_chars=12000)
    if len(truncated_arch) < len(architecture_doc):
        log.append("planning_agent: architecture doc truncated to fit context")

    user_content = f"""PROJECT NAME: {project_name}

ORIGINAL PROJECT BRIEF:
{brief}

TECH STACK:
{tech_stack_formatted}

FILES TO GENERATE (from architecture folder structure):
{file_list_formatted}

ARCHITECTURE DOCUMENT:
{truncated_arch}

Generate the complete implementation plan as a JSON array now.

CRITICAL RULES:
1. Output ONLY the raw JSON array. Start your response with [ and end with ]. No explanation, no markdown, no code fences, no text before or after the array.
2. Every file in the FILES TO GENERATE list above must have a corresponding task in your output.
3. Task ordering is MANDATORY: database tasks first (db_001, db_002...), then backend tasks (be_001...), then frontend tasks (fe_001...), then devops tasks (dv_001...).
4. Dependencies must reference real task IDs. Backend tasks that use database models must list those model task IDs in their requires field. Frontend tasks that call API endpoints must list the relevant backend task IDs in their requires field.
5. Every description must be specific to {project_name} - mention actual table names, actual endpoint paths, actual component names from the architecture. Never write generic descriptions.
6. Minimum 8 tasks. For a real application expect 15-30 tasks."""

    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": user_content},
    ]

    if previous_plan:
        # Compact + truncate the previous plan: a full indented 60-task plan is
        # ~25k+ tokens and exceeds free-tier per-request limits (Groq TPM 12k),
        # which fails permanently, not transiently. The model needs the plan's
        # shape as context, not every byte of it.
        try:
            compact_plan = json.dumps(json.loads(previous_plan), separators=(",", ":"))
        except (json.JSONDecodeError, TypeError):
            compact_plan = previous_plan
        messages.append({
            "role": "user",
            "content": build_feedback_prompt(
                truncate_for_context(compact_plan, max_chars=8000), human_feedback
            ),
        })

    print("[PlanningAgent] Calling Gemini 2.5 Flash for task plan...")
    # JSON/schema/dependency/coverage checks + one repair via the shared
    # registry (the Day 10 loop moved into validation.py's _valid_plan). A
    # second failure raises LLMOutputError — surfaced by the error boundary
    # instead of the old silent garbage-plan-in-state fallback.
    response = call_validated(
        messages, "planning", state, max_tokens=planning_max_tokens,
        original_instruction=(
            "Output the corrected JSON array only: start with [, end with ], no markdown fences, "
            f"no explanation. At least {MIN_TASKS} tasks covering every file in the project. "
            "If your previous answer was cut off, rewrite the ENTIRE array and ensure the final ] is present."
        ),
        log=log,
    )
    plan, _ = parse_and_validate_plan(response)

    # exclude_none keeps the plan JSON byte-identical to v1.0 when nothing is
    # decomposed: `section_of` is sparse by design, so emitting it as null on
    # every task would bloat every plan and break the "decomposition off ==
    # exact v1.0 behaviour" rollback guarantee.
    plan_json = json.dumps([task.model_dump(exclude_none=True) for task in plan.tasks],
                           indent=2)

    summary = plan.summary()
    log.append(
        f"planning_agent: completed - {summary['total']} tasks: "
        f"{summary['database']} db, {summary['backend']} backend, "
        f"{summary['frontend']} frontend, {summary['devops']} devops"
    )
    print(f"[PlanningAgent] Plan complete: {summary}")

    from ..core.connection_manager import manager

    manager.broadcast_sync(
        state.get("project_id", ""),
        {
            "type": "agent_complete",
            "agent": "planning",
            "stage": "planning",
            "preview": (
                f"Created {summary['total']} tasks: {summary['database']} database, "
                f"{summary['backend']} backend, {summary['frontend']} frontend, "
                f"{summary['devops']} devops"
            ),
            "output_preview": (
                f"Created {summary['total']} tasks: {summary['database']} database, "
                f"{summary['backend']} backend, {summary['frontend']} frontend, "
                f"{summary['devops']} devops"
            ),
            "content": plan_json,
            "plan_summary": summary,
        },
    )

    # Derived, not generated: no LLM call, no failure mode, and it is rebuilt
    # from the plan that was just validated so it can never describe primitives
    # that are not in it. Stored on state so every frontend context reads one
    # identical string and Gate 3 can show what the sections are held to.
    ui_contract = build_ui_contract(tech_stack_str, plan_json)
    log.append(f"planning_agent: ui contract derived ({len(ui_contract)} chars)")

    return {
        "implementation_plan": plan_json,
        "ui_contract": ui_contract,
        # A fresh plan invalidates any exclusions made against the old one
        "excluded_tasks": [],
        "log": log,
        "errors": errors,
        "current_stage": "frontend_code",
        "replan_after_architecture": False,
        "_agent_event": True,
    }
