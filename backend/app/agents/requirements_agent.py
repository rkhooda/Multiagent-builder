import json
from pathlib import Path

from ..validation import call_validated
from .utils import build_feedback_prompt, extract_tech_stack, regeneration_target, truncate_for_context

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

    human_feedback = state.get("human_feedback", "")
    previous_doc = regeneration_target(state, "requirements_doc")
    # Gate-2 back-navigation: regenerate requirements, then flow straight to
    # architecture (skip re-pausing at gate 1). Mirrors replan_after_architecture.
    is_back_rerun = state.get("human_decision") == "back" and previous_doc is not None

    print(f"[RequirementsAgent] Starting for project: {project_name}")
    log.append(f"requirements_agent: started for project '{project_name}'")

    if previous_doc:
        log.append("requirements_agent: re-running with human feedback")

    # ── Skip tolerance: research may have been skipped after a failure ───
    # Proceed from the brief alone and note the degradation instead of dying.
    research_skipped = not research_report or research_report.startswith("[SKIPPED")
    if research_skipped:
        log.append("requirements_agent: research report missing/skipped — proceeding from the brief alone (degraded)")
        print("[RequirementsAgent] WARNING: no usable research report, working from brief only")
        research_block = (
            "RESEARCH REPORT: Not available (the research stage was skipped after a failure). "
            "Derive requirements from the project brief alone, and note in the document that "
            "no market/technical research informed them."
        )
    else:
        truncated_research = truncate_for_context(research_report, max_chars=5000)
        if len(truncated_research) < len(research_report):
            log.append(f"requirements_agent: research report truncated from {len(research_report)} to 5000 chars for context")
            print(f"[RequirementsAgent] Research report truncated to fit context window")
        research_block = f"RESEARCH REPORT (from Research Agent):\n{truncated_research}"

    # ── Build the messages array ──────────────────────────────────
    user_content = f"""PROJECT NAME: {project_name}

ORIGINAL PROJECT BRIEF:
{brief}

{research_block}

Based on the brief and research report above, generate the complete requirements document now.
Include all required sections. The tech stack must match the technical landscape identified in the research.
At the very end of your response, output the tech stack as a JSON code block."""

    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": user_content}
    ]

    if previous_doc:
        messages.append({
            "role": "user",
            "content": build_feedback_prompt(previous_doc, human_feedback),
        })

    # ── LLM call (length/section checks + one repair via the shared registry) ─
    print("[RequirementsAgent] Calling LLM...")
    response = call_validated(
        messages, "requirements", state, max_tokens=4500,
        original_instruction=(
            "Output the COMPLETE requirements document with every required section — "
            "Functional Requirements (12+ items), Non-Functional Requirements, User Stories (8+), "
            "Out of Scope, Tech Stack — and the Tech Stack JSON block at the end."
        ),
        log=log,
    )

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
        "current_stage": "architecture",
        "skip_gate_1": is_back_rerun,
        "_agent_event": True
    }
