import json
from pathlib import Path

from ..core.connection_manager import manager
from ..llm_router import call_llm
from .research_validation import validate_research_report
from .search import format_search_results, web_search


SYSTEM_PROMPT = (
    Path(__file__).resolve().parents[3] / "prompts" / "research_agent.md"
).read_text(encoding="utf-8")


def build_report_structure_block(optional_sections_str: str) -> str:
    """
    Builds the REPORT_STRUCTURE block injected into the user message at runtime.

    The system prompt contains ZERO information about optional sections.
    This function is the single source of truth for what gets written.

    Permanent sections are always included.
    Optional sections are only included when their flag is True.
    """
    try:
        sections = json.loads(optional_sections_str) if optional_sections_str else {}
    except (json.JSONDecodeError, TypeError):
        sections = {}

    include_existing = sections.get("existing_solutions", False)
    include_users    = sections.get("target_users", False)
    include_market   = sections.get("market_risks", False)

    # Each entry: (heading, format_instructions)
    section_specs = []

    # PERMANENT 1 — Problem Space
    section_specs.append((
        "## Problem Space",
        "Write 2-3 detailed paragraphs (minimum 250 words) on the core problem being solved. "
        "Explain why it exists, why it remains unsolved, and what current pain points are. "
        "Be specific to this project — no generic filler."
    ))

    # OPTIONAL — Existing Solutions & Competitors
    if include_existing:
        section_specs.append((
            "## Existing Solutions & Competitors",
            "Write a markdown table with exactly these columns: "
            "Competitor/Solution | Strengths | Weaknesses | Market Position. "
            "Include a minimum of 4 real competitors or real solution categories. "
            "Every entry must be a real, named product or recognisable solution type. "
            "After the table, write one paragraph summarising the competitive gap this project can exploit."
        ))

    # OPTIONAL — Target Users
    if include_users:
        section_specs.append((
            "## Target Users",
            "Define the primary user persona using this exact structure:\n"
            "- **Primary User Persona**: [descriptive title]\n"
            "- **Job Title/Description**: [who they are and their daily responsibilities]\n"
            "- **Main Pain Points**: numbered list of at least 3 specific pain points\n"
            "- **Current Workarounds**: how they cope with the problem today\n"
            "- **What Success Looks Like**: the ideal outcome for them"
        ))

    # PERMANENT 2 — Technical Landscape
    section_specs.append((
        "## Technical Landscape",
        "Write 2-3 detailed paragraphs on relevant technologies, frameworks, libraries, APIs, "
        "and industry standards in this domain. Identify how similar products handle data flow, "
        "integrations, or compliance. Mention standard architectures common for this category."
    ))

    # PERMANENT 3 — Key Risks (always contains Technical + Execution Risks)
    key_risks_format = (
        "Use the heading ## Key Risks with these sub-sections:\n\n"
        "### Technical Risks\n"
        "Numbered list of exactly 2 technical risks. Each entry: risk name, detailed description, impact.\n\n"
    )
    if include_market:
        key_risks_format += (
            "### Market Risks\n"
            "Numbered list of exactly 2 market risks covering competition, adoption challenges, "
            "or pricing pressure. Each entry: risk name, detailed description, impact.\n\n"
        )
    key_risks_format += (
        "### Execution Risks\n"
        "Numbered list of exactly 2 execution risks covering team, timeline, or scope challenges. "
        "Each entry: risk name, detailed description, impact."
    )
    section_specs.append(("## Key Risks", key_risks_format))

    # PERMANENT 4 — Recommended Approach
    section_specs.append((
        "## Recommended Approach",
        "Write 2-3 detailed paragraphs on the recommended solution strategy. "
        "Describe specific features, architecture concepts, or UX patterns that differentiate "
        "a good solution from competitors. Explain why this approach minimises the risks above."
    ))

    # PERMANENT 5 — Research Confidence Score
    section_specs.append((
        "## Research Confidence Score",
        "Write exactly:\n"
        "**Score**: [High / Medium / Low]\n"
        "**Justification**: [One paragraph justifying the score, referencing market clarity, "
        "technical complexity, and depth of existing solutions]"
    ))

    # Assemble the REPORT_STRUCTURE block
    lines = ["REPORT_STRUCTURE:"]
    lines.append("Write the sections below in this exact order. Write ONLY these sections.")
    lines.append(f"Total sections to write: {len(section_specs)}")
    lines.append("")

    for i, (heading, instructions) in enumerate(section_specs, 1):
        lines.append(f"--- Section {i}: {heading} ---")
        lines.append(instructions)
        lines.append("")

    lines.append("END OF REPORT_STRUCTURE.")
    lines.append("")
    lines.append("Do not write any section not listed above. Do not add headings not listed above.")

    return "\n".join(lines)


def research_agent(state: dict) -> dict:
    """
    Research Agent — Phase 2 Core Intelligence

    Reads: state['brief'], state['project_name'], state['optional_sections']
    Writes: state['research_report'], state['log'], state['current_stage']

    The system prompt is completely blind to optional sections. All section
    control lives in the REPORT_STRUCTURE block injected into the user message.
    """
    brief = state.get("brief", "")
    project_name = state.get("project_name", "Unknown Project")
    project_id = state.get("project_id", "")
    optional_sections_str = state.get("optional_sections", "{}")
    log = list(state.get("log", []))
    errors = list(state.get("errors", []))

    print(f"[ResearchAgent] Starting: {project_name}")
    print(f"[ResearchAgent] Optional sections: {optional_sections_str}")
    log.append(f"research_agent: started, optional_sections={optional_sections_str}")

    # Web search enrichment
    search_context = ""
    try:
        results = web_search(f"{project_name} software market", max_results=5)
        if results:
            search_context = "\n\nWEB SEARCH CONTEXT (supplementary only — use to enrich your analysis):\n"
            search_context += format_search_results(results)
            log.append(f"research_agent: web search returned {len(results)} results")
    except Exception as e:
        print(f"[ResearchAgent] Web search skipped: {e}")

    # Build the runtime report structure — this is the only place optional sections exist
    report_structure_block = build_report_structure_block(optional_sections_str)

    user_content = f"""PROJECT NAME: {project_name}

PROJECT BRIEF:
{brief}

{report_structure_block}
{search_context}

Now write the research report. Start immediately with # Research Report: {project_name}
Write only the sections listed in REPORT_STRUCTURE above. Nothing else."""

    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": user_content},
    ]

    print("[ResearchAgent] Calling LLM...")
    report = call_llm(messages, "research", max_tokens=4000)

    # Post-generation forbidden section check
    try:
        sections = json.loads(optional_sections_str) if optional_sections_str else {}
    except Exception:
        sections = {}

    forbidden_checks = []
    if not sections.get("existing_solutions", False):
        forbidden_checks.append("## Existing Solutions")
    if not sections.get("target_users", False):
        forbidden_checks.append("## Target Users")
    if not sections.get("market_risks", False):
        forbidden_checks.append("### Market Risks")

    for heading in forbidden_checks:
        if heading.lower() in report.lower():
            print(f"[ResearchAgent] WARNING: forbidden section '{heading}' found — sending correction")
            log.append(f"research_agent: WARNING forbidden section '{heading}' appeared — correcting")
            messages.append({"role": "assistant", "content": report})
            messages.append({
                "role": "user",
                "content": (
                    f"Your report contains the section '{heading}' but it was NOT in the REPORT_STRUCTURE list. "
                    "Remove it completely and output the corrected full report. "
                    "Write only the sections that were listed in the REPORT_STRUCTURE block."
                ),
            })
            report = call_llm(messages, "research", max_tokens=4000)
            break

    # Length check
    MIN_LENGTH = 500
    if len(report) < MIN_LENGTH:
        log.append(f"research_agent: output too short ({len(report)} chars), retrying")
        messages.append({"role": "assistant", "content": report})
        messages.append({
            "role": "user",
            "content": "Your response is too short. Write each section thoroughly with at least 150 words per section.",
        })
        report = call_llm(messages, "research", max_tokens=4000)

    log.append(f"research_agent: completed — {len(report)} chars")
    print(f"[ResearchAgent] Done. Length: {len(report)} chars")

    preview = report[:200].replace("\n", " ").strip()
    event = {
        "type": "agent_complete",
        "agent": "research",
        "stage": "research",
        "preview": preview,
        "output_preview": preview,
        "content": report,
        "optional_sections": optional_sections_str,
        "report_length": len(report),
        "has_web_search": len(search_context) > 0,
    }
    manager.broadcast_sync(project_id, event)

    return {
        "research_report": report,
        "log": log,
        "errors": errors,
        "current_stage": "requirements",
        "_agent_event": event,
    }
