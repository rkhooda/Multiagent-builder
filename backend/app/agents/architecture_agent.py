import json
from pathlib import Path

from ..llm_router import call_llm
from .utils import build_feedback_prompt, truncate_for_context, parse_folder_structure, extract_mermaid_diagrams


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


def _call_with_repairs(messages: list[dict]) -> str:
    """Call the architecture model with up to three increasingly strict attempts."""
    architecture_doc = call_llm(messages, "architecture", max_tokens=5000)

    for _ in range(3):
        is_valid, missing_sections = validate_architecture_doc(architecture_doc)
        file_list = parse_folder_structure(architecture_doc)
        diagrams = extract_mermaid_diagrams(architecture_doc)

        if (
            len(architecture_doc) >= MIN_LENGTH
            and is_valid
            and len(file_list) >= 5
            and len(diagrams) >= 1
        ):
            return architecture_doc

        repair_notes = []
        if len(architecture_doc) < MIN_LENGTH:
            repair_notes.append(
                f"- Document is too short at {len(architecture_doc)} characters; target at least {MIN_LENGTH}."
            )
        if missing_sections:
            repair_notes.append(
                "- Missing required sections:\n" + "\n".join(f"  - {section}" for section in missing_sections)
            )
        if len(file_list) < 5:
            repair_notes.append(
                f"- Folder structure was not parseable enough; only {len(file_list)} files were extracted."
            )
        if len(diagrams) < 1:
            repair_notes.append("- No Mermaid diagrams were extracted.")
        if "```text" in architecture_doc and "```" not in architecture_doc.split("```text", 1)[1]:
            repair_notes.append("- The folder structure code block was left unclosed.")

        messages.append({"role": "assistant", "content": architecture_doc})
        messages.append(
            {
                "role": "user",
                "content": (
                    "Your previous response failed validation. Rewrite the FULL architecture document from scratch.\n\n"
                    "Fix these issues exactly:\n"
                    + "\n".join(repair_notes)
                    + "\n\nNon-negotiable formatting rules:\n"
                    "1. Use all required markdown headings exactly.\n"
                    "2. Close every code fence.\n"
                    "3. Keep the folder tree inside a single ```text fenced block.\n"
                    "4. After the folder tree, continue with the remaining sections outside that code fence.\n"
                    "5. Include one ```mermaid erDiagram``` block and one ```mermaid flowchart TD``` block.\n"
                    "6. Output the entire document again, not a patch or addendum."
                ),
            }
        )
        architecture_doc = call_llm(messages, "architecture", max_tokens=5000)

    return architecture_doc


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
    previous_versions = dict(state.get("previous_versions", {}) or {})

    human_decision = state.get("human_decision", "")
    human_feedback = state.get("human_feedback", "")
    previous_doc = state.get("architecture_doc", "")
    is_edit_rerun = human_decision == "edit" and bool(human_feedback) and bool(previous_doc)

    print(f"[ArchitectureAgent] Starting for project: {project_name}")
    log.append(f"architecture_agent: started for project '{project_name}'")

    if is_edit_rerun:
        previous_versions["architecture_doc"] = previous_doc
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
7. Make the architecture specific to this project domain, with concrete tables, endpoints, and component names."""

    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": user_content},
    ]

    if is_edit_rerun:
        messages.append({
            "role": "user",
            "content": build_feedback_prompt(previous_doc, human_feedback),
        })

    print("[ArchitectureAgent] Calling DeepSeek V3 via OpenRouter...")
    architecture_doc = _call_with_repairs(messages)

    is_valid, missing_sections = validate_architecture_doc(architecture_doc)
    if len(architecture_doc) < MIN_LENGTH:
        log.append(f"architecture_agent: WARNING output length below target ({len(architecture_doc)} chars)")
    if not is_valid:
        log.append(f"architecture_agent: WARNING missing sections after retries {missing_sections}")

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
        "previous_versions": previous_versions,
        "log": log,
        "errors": errors,
        "current_stage": "planning",
        "human_feedback": "",
        "human_decision": "",
    }
