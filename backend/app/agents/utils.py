import os
import re
import json
from typing import List, Optional


# ── Improvement 01: frontend page decomposition ──────────────────────────────
# Both switches live here rather than in a new config module because the two
# places that need them — the planning agent (which gates the prompt section)
# and the plan validator (which enforces the rules) — already import from this
# module. DECOMPOSE_FRONTEND=false must restore exact v1.0 behaviour: the prompt
# section is stripped AND the validator stands down, so a plan is neither asked
# for sections nor judged for lacking them.
_COMPLEXITY_RANK = {"low": 0, "medium": 1, "high": 2}

# Bounds on the fan-out, duplicated in the prompt so the model is told and in the
# validator so it is checked. One section is not a decomposition; more than five
# is atomisation, and each extra section is a whole generation call.
MIN_SECTIONS_PER_PAGE = 2
MAX_SECTIONS_PER_PAGE = 5


def decomposition_enabled() -> bool:
    return (os.getenv("DECOMPOSE_FRONTEND") or "true").strip().lower() not in (
        "false", "0", "no", "off")


def decompose_threshold() -> str:
    """Minimum estimated_complexity at which a page is worth decomposing."""
    value = (os.getenv("DECOMPOSE_COMPLEXITY_THRESHOLD") or "high").strip().lower()
    return value if value in _COMPLEXITY_RANK else "high"


def meets_complexity(complexity: str, threshold: str = None) -> bool:
    threshold = threshold or decompose_threshold()
    return _COMPLEXITY_RANK.get((complexity or "").lower(), -1) >= _COMPLEXITY_RANK[threshold]


def is_page_task(filepath: str) -> bool:
    """A page/screen — the only thing decomposition applies to. Mirrors
    context_builder._is_consumer: these are the files that COMPOSE others."""
    low = (filepath or "").lower()
    return "/pages/" in low or low.rsplit("/", 1)[-1] in ("app.jsx", "app.tsx")


def extract_json_block(text: str) -> Optional[dict]:
    """
    Extract and parse a JSON code block from LLM response text.
    Handles both ```json ... ``` and ``` ... ``` formats.
    Returns parsed dict or None if no valid JSON block found.
    """
    # Try ```json ... ``` format first (most common)
    match = re.search(r'```json\s*([\s\S]*?)\s*```', text, re.IGNORECASE)
    if not match:
        # Try plain ``` ... ``` format
        match = re.search(r'```\s*(\{[\s\S]*?\})\s*```', text)
    if not match:
        # Try to find a raw JSON object anywhere in the text as last resort
        match = re.search(r'(\{[^{}]*"frontend"[^{}]*\})', text, re.DOTALL)

    if match:
        try:
            return json.loads(match.group(1).strip())
        except json.JSONDecodeError as e:
            print(f"[Utils] JSON parse error: {e}")
            return None

    return None


def extract_tech_stack(llm_response: str) -> dict:
    """
    Extract tech stack JSON from LLM response and validate against TechStackSchema.
    Returns validated dict. Falls back to sensible defaults if parsing fails.
    """
    from ..models.tech_stack import TechStackSchema, DEFAULT_TECH_STACK

    data = extract_json_block(llm_response)

    if data:
        try:
            stack = TechStackSchema(**data)
            print(f"[Utils] Tech stack parsed successfully: {stack.frontend} / {stack.backend}")
            return stack.model_dump()
        except Exception as e:
            print(f"[Utils] Tech stack validation error: {e}")

    print("[Utils] Could not parse tech stack JSON — using defaults")
    return DEFAULT_TECH_STACK.model_dump()


def truncate_for_context(text: str, max_chars: int = 6000) -> str:
    """
    Truncate long text to fit within LLM context limits.
    Cuts from the middle to preserve beginning and end.
    """
    if len(text) <= max_chars:
        return text
    half = max_chars // 2
    return (
        text[:half] +
        f"\n\n[... {len(text) - max_chars} characters truncated to fit context ...]\n\n" +
        text[-half:]
    )


def parse_folder_structure(architecture_doc: str) -> List[str]:
    """
    Extract all file paths from a folder structure tree in the architecture document.

    Looks for a code block containing the folder tree and returns a flat list of
    file paths such as:
    ['backend/app/models.py', 'backend/app/routers/users.py', 'frontend/src/App.jsx']

    Skips directory-only lines, comment lines, and tree heading noise.
    """
    folder_section_match = re.search(
        r"##\s*Folder Structure.*?```(?:text|bash)?\s*([\s\S]*?)(?:```|\n##\s|\Z)",
        architecture_doc,
        re.IGNORECASE | re.DOTALL,
    )

    if not folder_section_match:
        print("[Utils] Could not find folder structure code block in architecture doc")
        return []

    tree_text = folder_section_match.group(1).strip("\n")
    file_list: List[str] = []
    path_stack: List[str] = []

    for raw_line in tree_text.splitlines():
        if not raw_line.strip():
            continue

        line = raw_line.rstrip()
        stripped = line.strip()
        if not stripped:
            continue

        # Ignore comments and obvious tree headings.
        if stripped.startswith("#"):
            continue

        indent_prefix = re.match(r"^[\s│|]*", line)
        indent_segment = indent_prefix.group(0) if indent_prefix else ""
        depth = len(indent_segment.replace("\t", "    ")) // 4

        clean = re.sub(r"^[\s│|]*(?:├──|└──|\+--)?\s*", "", line).strip()
        clean = re.sub(r"\s+#.*$", "", clean).strip()

        if not clean:
            continue

        if clean in {".", "./"}:
            continue

        if clean.endswith(":"):
            continue

        is_directory = clean.endswith("/")
        name = clean.rstrip("/")

        if not name:
            continue

        path_stack = path_stack[:depth]

        if is_directory:
            path_stack.append(name)
            continue

        # Support trees where directories appear without a trailing slash.
        if "." not in name:
            path_stack.append(name)
            continue

        full_path = "/".join(path_stack + [name]) if path_stack else name
        file_list.append(full_path)

    print(f"[Utils] Parsed {len(file_list)} files from folder structure")
    return file_list


def extract_mermaid_diagrams(text: str) -> List[dict]:
    """
    Extract Mermaid diagram blocks from a document.
    Returns dicts shaped like: {"type": "erDiagram"|"flowchart", "code": "..."}
    """
    diagrams = []
    matches = re.finditer(r"```mermaid\s*([\s\S]*?)```", text, re.IGNORECASE)
    for match in matches:
        code = match.group(1).strip()
        diagram_type = "erDiagram" if "erDiagram" in code else "flowchart"
        diagrams.append({"type": diagram_type, "code": code})
    return diagrams


def parse_and_validate_plan(llm_response: str) -> tuple[object, list[str]]:
    """
    Extract, parse, and validate the implementation plan JSON from LLM response.

    Returns (ImplementationPlan, errors_list).
    If errors_list is empty, the plan is valid.
    If errors is non-empty, ImplementationPlan may be None or partially valid.
    """
    from ..models.task_schema import TaskSchema, ImplementationPlan

    errors = []

    array_match = re.search(r'\[\s*\{[\s\S]*\}\s*\]', llm_response)
    if not array_match:
        errors.append("No JSON array found in LLM response")
        return None, errors

    raw_json = array_match.group(0)

    try:
        task_list = json.loads(raw_json)
    except json.JSONDecodeError as e:
        errors.append(f"JSON parse error: {e}")
        fixed = re.sub(r',\s*([}\]])', r'\1', raw_json)
        try:
            task_list = json.loads(fixed)
            errors = []
        except json.JSONDecodeError:
            return None, errors

    if not isinstance(task_list, list):
        errors.append("Expected a JSON array at the top level")
        return None, errors

    if len(task_list) == 0:
        errors.append("Task list is empty")
        return None, errors

    validated_tasks = []
    task_errors = []

    for i, raw_task in enumerate(task_list):
        try:
            task = TaskSchema(**raw_task)
            validated_tasks.append(task)
        except Exception as e:
            task_id = raw_task.get("id", "unknown") if isinstance(raw_task, dict) else "unknown"
            task_errors.append(f"Task {i} ({task_id}): {e}")

    if task_errors:
        errors.extend(task_errors[:5])

    if not validated_tasks:
        errors.append("No tasks passed validation")
        return None, errors

    plan = ImplementationPlan(tasks=validated_tasks)
    deps_valid, dep_errors = plan.validate_dependencies()
    if not deps_valid:
        errors.extend(dep_errors[:5])

    print(
        f"[Utils] Plan parsed: {len(validated_tasks)}/{len(task_list)} tasks valid, {len(errors)} errors"
    )
    return plan, errors


def get_tasks_for_phase(implementation_plan_str: str, phase: str) -> list[dict]:
    """
    Parses the implementation_plan JSON string and returns only tasks
    matching the given phase ('database', 'backend', 'frontend', 'devops').
    Returns empty list if plan is invalid or no matching tasks found.
    """
    import json
    try:
        all_tasks = json.loads(implementation_plan_str)
    except (json.JSONDecodeError, TypeError):
        print(f"[Utils] Could not parse implementation_plan for phase filtering")
        return []

    if not isinstance(all_tasks, list):
        return []

    matching = [t for t in all_tasks if t.get("phase") == phase]
    print(f"[Utils] Found {len(matching)} tasks for phase '{phase}'")
    return matching


def assert_single_owner(implementation_plan_str: str) -> None:
    """Guard the database/backend ownership boundary (Day 19 ponytail #1).

    The database agent owns `phase == 'database'` files (ORM models +
    migrations); the backend coder owns `phase == 'backend'` files. Exactly one
    agent may own any filepath — a filepath planned under BOTH phases would be
    generated twice on different graph passes, each "working" in isolation and
    silently clobbering the other. Cheap insurance: raise loudly instead.
    """
    db_paths = {t.get("filepath") for t in get_tasks_for_phase(implementation_plan_str, "database")}
    be_paths = {t.get("filepath") for t in get_tasks_for_phase(implementation_plan_str, "backend")}
    overlap = sorted(p for p in (db_paths & be_paths) if p)
    if overlap:
        raise ValueError(
            "Ownership conflict: these filepaths are planned under both the "
            f"'database' and 'backend' phases and would be double-generated: {overlap}. "
            "Fix the plan so each file belongs to exactly one phase."
        )


def build_feedback_prompt(previous_output: str, feedback: str) -> str:
    """
    Builds the standard feedback-regeneration prompt appended to an agent's
    normal user message when re-running a stage after a human 'edit'/'back'
    decision. The ONLY copy of the regeneration framing string.
    """
    return (
        f"PREVIOUS OUTPUT:\n{previous_output}\n\n"
        f"HUMAN FEEDBACK:\n{feedback}\n\n"
        "Regenerate the document addressing the feedback. Keep what was good, fix what was wrong."
    )


def regeneration_target(state: dict, output_field: str) -> Optional[str]:
    """
    Returns this agent's previous output when it is the target of a
    feedback-driven re-run ('edit' or 'back' decision), else None.

    Cascade stages downstream of the target never match: invalidate_downstream
    cleared their output field before the cycle started, so the non-empty
    check fails even while the decision fields are still set.
    """
    if (
        state.get("human_decision") in ("edit", "back")
        and state.get("human_feedback")
        and state.get(output_field)
    ):
        return state.get(output_field)
    return None


def get_completed_file_content(generated_files: dict, task_ids: list[str], all_tasks: list[dict]) -> str:
    """
    Given a list of task IDs this task depends on, look up their filepaths
    and return the already-generated content for context injection.
    Used so dependent files (e.g. a router that imports from models.py)
    have access to what was already generated.
    """
    if not task_ids:
        return ""

    task_by_id = {t["id"]: t for t in all_tasks}
    context_parts = []

    for tid in task_ids:
        task = task_by_id.get(tid)
        if not task:
            continue
        filepath = task.get("filepath")
        content = generated_files.get(filepath)
        if content:
            context_parts.append(f"--- Already generated: {filepath} ---\n{content}\n")

    return "\n".join(context_parts)
