import json
from pathlib import Path

from ..validation import call_validated
from .utils import build_feedback_prompt, regeneration_target, truncate_for_context, parse_folder_structure, extract_mermaid_diagrams


SYSTEM_PROMPT = (
    Path(__file__).resolve().parents[3] / "prompts" / "architecture_agent.md"
).read_text(encoding="utf-8")

REQUIRED_SECTIONS = [
    "## Folder Structure",
    "## Database Schema",
    "## API Endpoints",
    "## Component",
    "## Security",
]

MIN_LENGTH = 1500


def validate_architecture_doc(doc: str) -> tuple[bool, list[str]]:
    """Check all required sections are present in the architecture document."""
    missing = []
    for section in REQUIRED_SECTIONS:
        found = any(section.lower() in line.lower() for line in doc.split("\n"))
        if not found:
            missing.append(section)
    return len(missing) == 0, missing


def architecture_agent(state: dict) -> dict:
    """
    Architecture Agent - Phase 2 Core Intelligence

    Reads: state['brief'], state['project_name'], state['requirements_doc'], state['tech_stack']
    Writes: state['architecture_doc'], state['file_list'], state['log'], state['current_stage']
    """
    brief = state.get("brief", "")
    project_name = state.get("project_name", "Unknown Project")
    project_id = state.get("project_id", "")
    requirements_doc = state.get("requirements_doc", "")
    tech_stack_str = state.get("tech_stack", "")
    log = list(state.get("log", []))
    errors = list(state.get("errors", []))

    human_feedback = state.get("human_feedback", "")
    # 'edit' = gate 2 feedback loop; 'back' = gate 3 back-navigation — both regenerate with feedback
    previous_doc = regeneration_target(state, "architecture_doc")
    is_back_rerun = state.get("human_decision") == "back" and previous_doc is not None

    print(f"[ArchitectureAgent] Starting for project: {project_name}")
    log.append(f"architecture_agent: started for project '{project_name}'")

    if previous_doc:
        log.append("architecture_agent: re-running with human feedback")

    if not requirements_doc:
        error_msg = "architecture_agent: requirements_doc is empty - architecture quality will be degraded"
        log.append(error_msg)
        errors.append(error_msg)
        print(f"[ArchitectureAgent] WARNING: {error_msg}")

    tech_stack_formatted = ""
    if tech_stack_str:
        try:
            stack = json.loads(tech_stack_str)
            tech_stack_formatted = (
                f"Frontend: {stack.get('frontend', 'Not specified')}\n"
                f"Backend: {stack.get('backend', 'Not specified')}\n"
                f"Database: {stack.get('database', 'Not specified')}\n"
                f"Auth: {stack.get('auth', 'Not specified')}\n"
                f"Hosting: {stack.get('hosting', 'Not specified')}\n"
                f"Key Libraries: {', '.join(stack.get('key_libraries', []))}"
            )
        except json.JSONDecodeError:
            tech_stack_formatted = tech_stack_str
            print("[ArchitectureAgent] Could not parse tech stack JSON, using raw string")

    truncated_requirements = truncate_for_context(requirements_doc, max_chars=4000)
    if len(truncated_requirements) < len(requirements_doc):
        log.append("architecture_agent: requirements doc truncated to fit context")

    user_content = f"""PROJECT NAME: {project_name}

ORIGINAL BRIEF:
{brief}

CONFIRMED TECH STACK:
{tech_stack_formatted}

REQUIREMENTS DOCUMENT:
{truncated_requirements}

Generate the complete system architecture document now.

CRITICAL REQUIREMENTS:
1. The folder structure must list EVERY SINGLE FILE that will be created. No "..." shortcuts. No "[more files here]" placeholders. Every file explicitly named with its real filename.
2. The database schema must include both SQL CREATE TABLE statements AND a Mermaid ER diagram.
3. The API endpoints table must cover every endpoint needed - minimum 15 endpoints.
4. Include a Mermaid flowchart for the main data flow.
5. Every filename in the folder structure must be the actual name the file will have - not generic names like 'component.jsx' or 'utils.py'. Use names based on the project's specific features.
6. Include at least 2 Mermaid diagrams total: one ER diagram and one flowchart.
7. Make the architecture specific to this project domain, with concrete tables, endpoints, and component names.
8. The API endpoints table must have the columns | Method | Path | Auth | Description | Response |, and EVERY row's Response cell must hold a concrete one-line example JSON body (an array for list endpoints). Not a type name, not "see schema". The coders read this cell to agree on one response shape — without it the frontend and backend invent two different ones.
9. Every component in the hierarchy must name its props, e.g. `- NoteList (props: notes, onDelete)`, or `(props: none)`. Prop names are the contract between a page and its children.
10. Never put `__init__.py` or any Python-only file in the frontend tree, and never put `__init__.js` anywhere — JavaScript has no such convention."""

    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": user_content},
    ]

    if previous_doc:
        messages.append({
            "role": "user",
            "content": build_feedback_prompt(previous_doc, human_feedback),
        })

    print("[ArchitectureAgent] Calling architecture model...")
    # Length/section/tree/diagram checks + one repair via the shared registry.
    architecture_doc = call_validated(
        # Day 21: raised from 5000. The specificity rules (an example response
        # JSON on every endpoint row, props on every component) roughly 2.5x the
        # document — a verified regeneration grew 7287 -> 18497 chars. At 5000 the
        # doc truncated mid-way and failed validation on missing trailing
        # sections, i.e. the sharper prompt produced WORSE output than the vague
        # one. A specificity rule and its token ceiling ship together.
        messages, "architecture", state, max_tokens=12000,
        original_instruction=(
            "Rewrite the FULL architecture document from scratch. Non-negotiable formatting rules: "
            "use all required markdown headings exactly; close every code fence; keep the folder tree "
            "inside a single ```text fenced block with every file explicitly named; include one "
            "```mermaid erDiagram``` block and one ```mermaid flowchart TD``` block; output the entire "
            "document again, not a patch."
        ),
        log=log,
    )

    print("[ArchitectureAgent] Parsing folder structure to extract file list...")
    file_list = parse_folder_structure(architecture_doc)

    if len(file_list) < 5:
        warning = f"architecture_agent: folder structure parser found only {len(file_list)} files - may need manual review"
        log.append(f"architecture_agent: WARNING only {len(file_list)} files parsed from folder structure")
        errors.append(warning)
        print(f"[ArchitectureAgent] WARNING: Only {len(file_list)} files found. Check folder structure format.")
    else:
        log.append(f"architecture_agent: parsed {len(file_list)} files from folder structure")
        print(f"[ArchitectureAgent] File list: {len(file_list)} files extracted")

    diagrams = extract_mermaid_diagrams(architecture_doc)
    log.append(f"architecture_agent: found {len(diagrams)} Mermaid diagram(s) in output")
    print(f"[ArchitectureAgent] Mermaid diagrams found: {len(diagrams)}")

    log.append(
        f"architecture_agent: completed - {len(architecture_doc)} char doc, {len(file_list)} files"
    )
    print(f"[ArchitectureAgent] Completed. Doc: {len(architecture_doc)} chars, Files: {len(file_list)}")

    from ..core.connection_manager import manager

    preview = architecture_doc[:200].replace("\n", " ").strip()
    manager.broadcast_sync(
        project_id,
        {
            "type": "agent_complete",
            "agent": "architecture",
            "stage": "architecture",
            "preview": preview,
            "output_preview": preview,
            "content": architecture_doc,
            "file_count": len(file_list),
            "diagram_count": len(diagrams),
            "doc_length": len(architecture_doc),
        },
    )

    return {
        "architecture_doc": architecture_doc,
        "file_list": file_list,
        "log": log,
        "errors": errors,
        "current_stage": "planning",
        # On a gate-3 'back' rerun the old plan is stale: skip gate 2 and replan immediately
        "replan_after_architecture": is_back_rerun,
        # End of a gate-2 back-cycle: re-arm gate 1 for future requirements re-runs
        "skip_gate_1": False,
        "_agent_event": True,
    }
