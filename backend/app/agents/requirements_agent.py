import json
from pathlib import Path

from ..llm_router import call_llm
from .utils import extract_tech_stack, truncate_for_context

SYSTEM_PROMPT = (
    Path(__file__).resolve().parents[3] / "prompts" / "requirements_agent.md"
).read_text(encoding="utf-8")

REQUIRED_SECTIONS = [
    "## Functional Requirements",
    "## Non-Functional Requirements",
    "## User Stories",
    "## Out of Scope",
    "## Tech Stack",
]

MIN_LENGTH = 800


def validate_requirements_doc(doc: str) -> tuple[bool, list[str]]:
    """
    Check that all required sections are present in the requirements document.
    Returns (is_valid, list_of_missing_sections).
    """
    missing = []
    for section in REQUIRED_SECTIONS:
        section_found = any(
            section.lower() in line.lower()
            for line in doc.split('\n')
        )
        if not section_found:
            missing.append(section)
    return len(missing) == 0, missing


def requirements_agent(state: dict) -> dict:
    """
    Requirements Agent — Phase 2 Core Intelligence

    Reads: state['brief'], state['project_name'], state['research_report']
    Writes: state['requirements_doc'], state['tech_stack'], state['log'], state['current_stage']

    Calls Gemini 2.5 Flash with brief + research report as context.
    Extracts and validates the tech stack JSON from the response.
    Stores requirements doc (human-readable) and tech stack (machine-readable) separately.
    """
    brief = state.get("brief", "")
    project_name = state.get("project_name", "Unknown Project")
    project_id = state.get("project_id", "")
    research_report = state.get("research_report", "")
    log = list(state.get("log", []))
    errors = list(state.get("errors", []))

    print(f"[RequirementsAgent] Starting for project: {project_name}")
    log.append(f"requirements_agent: started for project '{project_name}'")

    # ── Validate we have the research report ──────────────────────
    if not research_report:
        error_msg = "requirements_agent: no research_report in state — cannot proceed without research"
        log.append(error_msg)
        errors.append(error_msg)
        print(f"[RequirementsAgent] ERROR: {error_msg}")
        return {
            "requirements_doc": "ERROR: Research report was missing. Requirements could not be generated.",
            "tech_stack": "",
            "log": log,
            "errors": errors,
            "current_stage": "architecture"
        }

    # ── Truncate research report if too long for context ─────────
    truncated_research = truncate_for_context(research_report, max_chars=5000)
    if len(truncated_research) < len(research_report):
        log.append(f"requirements_agent: research report truncated from {len(research_report)} to 5000 chars for context")
        print(f"[RequirementsAgent] Research report truncated to fit context window")

    # ── Build the messages array ──────────────────────────────────
    user_content = f"""PROJECT NAME: {project_name}

ORIGINAL PROJECT BRIEF:
{brief}

RESEARCH REPORT (from Research Agent):
{truncated_research}

Based on the brief and research report above, generate the complete requirements document now.
Include all required sections. The tech stack must match the technical landscape identified in the research.
At the very end of your response, output the tech stack as a JSON code block."""

    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": user_content}
    ]

    # ── First LLM call ────────────────────────────────────────────
    print("[RequirementsAgent] Calling LLM (primary attempt)...")
    response = call_llm(messages, "requirements", max_tokens=4500)

    # ── Validate length ───────────────────────────────────────────
    if len(response) < MIN_LENGTH:
        print(f"[RequirementsAgent] Output too short ({len(response)} chars), retrying...")
        log.append(f"requirements_agent: output too short ({len(response)} chars), retrying")

        messages.append({"role": "assistant", "content": response})
        messages.append({
            "role": "user",
            "content": (
                f"Your response was only {len(response)} characters — far too short. "
                "The requirements document must be at minimum 800 words and include ALL sections: "
                "Functional Requirements (12+ items), Non-Functional Requirements, User Stories (8+ stories), "
                "Out of Scope, Tech Stack Recommendation with justification, and the Tech Stack JSON block at the end. "
                "Please write the complete document now."
            )
        })
        response = call_llm(messages, "requirements", max_tokens=4500)

    # ── Validate sections ─────────────────────────────────────────
    is_valid, missing_sections = validate_requirements_doc(response)
    if not is_valid:
        print(f"[RequirementsAgent] Missing sections: {missing_sections}, retrying with repair prompt...")
        log.append(f"requirements_agent: missing sections {missing_sections}, sending repair prompt")

        messages.append({"role": "assistant", "content": response})
        missing_list = "\n".join(f"- {s}" for s in missing_sections)
        messages.append({
            "role": "user",
            "content": (
                f"Your requirements document is missing these required sections:\n{missing_list}\n\n"
                "Please output the COMPLETE requirements document again, ensuring every section above is present. "
                "Do not truncate or summarise — write the full content for each section."
            )
        })
        response = call_llm(messages, "requirements", max_tokens=4500)

    # ── Extract the tech stack JSON ───────────────────────────────
    print("[RequirementsAgent] Extracting tech stack JSON...")
    tech_stack_dict = extract_tech_stack(response)
    tech_stack_json_str = json.dumps(tech_stack_dict, indent=2)

    print(f"[RequirementsAgent] Tech stack: {tech_stack_dict.get('frontend')} / {tech_stack_dict.get('backend')} / {tech_stack_dict.get('database')}")

    # ── Final logging ─────────────────────────────────────────────
    log.append(f"requirements_agent: completed — {len(response)} char doc, tech stack extracted")
    print(f"[RequirementsAgent] Completed. Doc length: {len(response)} chars")

    # ── Broadcast via WebSocket ───────────────────────────────────
    from ..core.connection_manager import manager
    preview = response[:200].replace('\n', ' ').strip()
    manager.broadcast_sync(project_id, {
        "type": "agent_complete",
        "agent": "requirements",
        "stage": "requirements",
        "preview": preview,
        "output_preview": preview,
        "content": response,
        "tech_stack": tech_stack_dict,
        "doc_length": len(response)
    })

    return {
        "requirements_doc": response,
        "tech_stack": tech_stack_json_str,
        "log": log,
        "errors": errors,
        "current_stage": "architecture"
    }
