import json
from pathlib import Path

from ..llm_router import call_llm
from .utils import build_feedback_prompt, truncate_for_context, parse_and_validate_plan


SYSTEM_PROMPT = (
    Path(__file__).resolve().parents[3] / "prompts" / "planning_agent.md"
).read_text(encoding="utf-8")

MIN_TASKS = 8


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
    previous_versions = dict(state.get("previous_versions", {}) or {})

    human_decision = state.get("human_decision", "")
    human_feedback = state.get("human_feedback", "")
    previous_plan = state.get("implementation_plan", "")
    is_edit_rerun = human_decision == "edit" and bool(human_feedback) and bool(previous_plan)

    print(f"[PlanningAgent] Starting for project: {project_name}")
    log.append(f"planning_agent: started - {len(file_list)} files in file_list")

    if is_edit_rerun:
        previous_versions["implementation_plan"] = previous_plan
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
    required_filepaths = set(file_list)
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

    if is_edit_rerun:
        messages.append({
            "role": "user",
            "content": build_feedback_prompt(previous_plan, human_feedback),
        })

    print("[PlanningAgent] Calling Gemini 2.5 Flash for task plan...")
    response = call_llm(messages, "planning", max_tokens=planning_max_tokens)

    plan, parse_errors = parse_and_validate_plan(response)

    if plan is not None:
        planned_filepaths = {task.filepath for task in plan.tasks}
        missing_files = sorted(required_filepaths - planned_filepaths)
        if missing_files:
            parse_errors.append(
                f"Plan is missing {len(missing_files)} required file tasks. Examples: {missing_files[:5]}"
            )

    repair_attempts = 0
    while (
        plan is None
        or len(plan.tasks) < MIN_TASKS
        or any("missing" in err.lower() and "file" in err.lower() for err in parse_errors)
    ) and repair_attempts < 2:
        repair_attempts += 1
        print(
            f"[PlanningAgent] Validation failed (attempt {repair_attempts}), sending repair prompt..."
        )
        log.append(
            f"planning_agent: validation failed, repair attempt {repair_attempts}: {parse_errors[:3]}"
        )

        error_summary = "\n".join(parse_errors[:5]) if parse_errors else (
            f"Only {len(plan.tasks) if plan else 0} tasks found, need at least {MIN_TASKS}"
        )

        messages.append({"role": "assistant", "content": response})
        messages.append(
            {
                "role": "user",
                "content": (
                    f"Your JSON output had these problems:\n{error_summary}\n\n"
                    "Please output the corrected JSON array only. "
                    "Rules: start with [, end with ], no markdown fences, no explanation. "
                    f"Must have at least {MIN_TASKS} tasks covering all files in the project. "
                    "If your previous answer was cut off, rewrite the ENTIRE array from the beginning and ensure the final ] is present. "
                    "Fix the specific errors listed above."
                ),
            }
        )
        response = call_llm(messages, "planning", max_tokens=planning_max_tokens)
        plan, parse_errors = parse_and_validate_plan(response)
        if plan is not None:
            planned_filepaths = {task.filepath for task in plan.tasks}
            missing_files = sorted(required_filepaths - planned_filepaths)
            if missing_files:
                parse_errors.append(
                    f"Plan is missing {len(missing_files)} required file tasks. Examples: {missing_files[:5]}"
                )

    if plan is None:
        error_msg = (
            f"planning_agent: could not produce valid plan after {repair_attempts} repair attempts"
        )
        log.append(error_msg)
        errors.append(error_msg)
        print(f"[PlanningAgent] ERROR: {error_msg}")
        return {
            "implementation_plan": response,
            "log": log,
            "errors": errors,
            "current_stage": "frontend_code",
        }

    plan_json = json.dumps([task.model_dump() for task in plan.tasks], indent=2)

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

    return {
        "implementation_plan": plan_json,
        # A fresh plan invalidates any exclusions made against the old one
        "excluded_tasks": [],
        "previous_versions": previous_versions,
        "log": log,
        "errors": errors,
        "current_stage": "frontend_code",
        "human_feedback": "",
        "human_decision": "",
        "replan_after_architecture": False,
        "_agent_event": True,
    }
