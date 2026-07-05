import json
from pathlib import Path

from ..core.connection_manager import manager
from ..llm_router import call_llm
from .research_validation import validate_research_report
from .search import format_search_results, web_search


SYSTEM_PROMPT = (
    Path(__file__).resolve().parents[3] / "prompts" / "research_agent.md"
).read_text(encoding="utf-8")


def build_optional_sections_block(optional_sections_str: str) -> str:
    """
    Parse the optional_sections JSON from state and format it as a clear
    instruction block to inject into the user message.
    """
    try:
        sections = json.loads(optional_sections_str) if optional_sections_str else {}
    except json.JSONDecodeError:
        sections = {}

    include_existing = sections.get("existing_solutions", False)
    include_users = sections.get("target_users", False)
    include_market = sections.get("market_risks", False)

    block = "OPTIONAL SECTIONS:\n"
    block += f"- include_existing_solutions: {'true' if include_existing else 'false'}\n"
    block += f"- include_target_users: {'true' if include_users else 'false'}\n"
    block += f"- include_market_risks: {'true' if include_market else 'false'}\n"

    enabled = []
    if include_existing:
        enabled.append("Existing Solutions & Competitors")
    if include_users:
        enabled.append("Target Users")
    if include_market:
        enabled.append("Market Risks")

    if enabled:
        block += f"\nInclude these optional sections in your report: {', '.join(enabled)}."
    else:
        block += "\nNo optional sections requested. Include only the permanent default sections."

    return block


def research_agent(state: dict) -> dict:
    """
    Research Agent — Phase 2 Core Intelligence

    Reads: state['brief'], state['project_name'], state['optional_sections']
    Writes: state['research_report'], state['log'], state['current_stage']

    Calls the LLM with optional DuckDuckGo web search enrichment. Validates
    required sections and output length, and retries once if incomplete.
    """
    brief = state.get("brief", "")
    project_name = state.get("project_name", "Unknown Project")
    project_id = state.get("project_id", "")
    optional_sections_str = state.get("optional_sections", "{}")
    log = list(state.get("log", []))
    errors = list(state.get("errors", []))

    print(f"[ResearchAgent] Starting for project: {project_name}")
    print(f"[ResearchAgent] Optional sections: {optional_sections_str}")
    log.append(
        f"research_agent: started for project '{project_name}', optional_sections={optional_sections_str}"
    )

    search_context = ""
    search_queries = [
        f"{project_name} competitors alternatives",
        f"{project_name} market solutions open source",
    ]

    all_results = []
    for query in search_queries:
        results = web_search(query, max_results=4)
        all_results.extend(results)

    if all_results:
        search_context = "\n\n" + format_search_results(all_results[:8])
        log.append(f"research_agent: enriched with {len(all_results)} web search results")
        print(f"[ResearchAgent] Web search returned {len(all_results)} results")
    else:
        log.append("research_agent: web search returned no results, proceeding without enrichment")
        print("[ResearchAgent] No web search results, proceeding with brief only")

    optional_sections_block = build_optional_sections_block(optional_sections_str)

    user_content = f"""PROJECT NAME: {project_name}

PROJECT BRIEF:
{brief}

{optional_sections_block}
{search_context}

Generate the complete research report now following all format requirements and the optional section rules above. Be specific to this project — do not write generic content that could apply to any app."""

    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": user_content},
    ]

    print("[ResearchAgent] Calling LLM (primary attempt)...")
    report = call_llm(messages, "research", max_tokens=4000)

    min_length = 600
    is_valid, missing_sections = validate_research_report(report, optional_sections_str)
    if len(report) < min_length or not is_valid:
        print(
            f"[ResearchAgent] Output incomplete (length={len(report)}, missing={missing_sections}), retrying..."
        )
        log.append(
            f"research_agent: output incomplete (length={len(report)}, missing={missing_sections}), retrying"
        )

        messages.append({"role": "assistant", "content": report})
        messages.append(
            {
                "role": "user",
                "content": (
                    f"Your previous response was {len(report)} characters long and missed these sections: "
                    f"{missing_sections or 'none'}. "
                    "The research report must include ALL permanent sections: Problem Space, Technical Landscape, "
                    "Key Risks (Technical Risks + Execution Risks), Recommended Approach, and Research Confidence Score. "
                    f"Also follow the OPTIONAL SECTIONS block exactly as specified above. "
                    "Do not summarise — write the full content in the required markdown structure."
                ),
            }
        )
        report = call_llm(messages, "research", max_tokens=4000)
        is_valid, missing_sections = validate_research_report(report, optional_sections_str)

        if len(report) < min_length or not is_valid:
            error_msg = (
                "research_agent: output still incomplete after retry "
                f"(length={len(report)}, missing={missing_sections})"
            )
            log.append(error_msg)
            errors.append(error_msg)
            print(f"[ResearchAgent] WARNING: {error_msg}")

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

    log.append(f"research_agent: completed - generated {len(report)} char report")
    print(f"[ResearchAgent] Completed. Report length: {len(report)} chars")

    return {
        "research_report": report,
        "log": log,
        "errors": errors,
        "current_stage": "requirements",
        "_agent_event": event,
    }
