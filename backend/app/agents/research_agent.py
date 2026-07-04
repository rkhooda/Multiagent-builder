from pathlib import Path

from ..core.connection_manager import manager
from ..llm_router import call_llm
from .research_validation import validate_research_report
from .search import format_search_results, web_search


SYSTEM_PROMPT = (
    Path(__file__).resolve().parents[3] / "prompts" / "research_agent.md"
).read_text(encoding="utf-8")


def research_agent(state: dict) -> dict:
    """
    Research Agent - Phase 2 Core Intelligence

    Reads: state['brief'], state['project_name']
    Writes: state['research_report'], state['log'], state['current_stage']

    Calls Gemini 2.5 Flash via LiteLLM with optional DuckDuckGo web search
    enrichment. Validates required sections and output length, and retries once
    if the result is incomplete.
    """
    brief = state.get("brief", "")
    project_name = state.get("project_name", "Unknown Project")
    project_id = state.get("project_id", "")
    log = list(state.get("log", []))
    errors = list(state.get("errors", []))

    print(f"[ResearchAgent] Starting for project: {project_name}")
    log.append(f"research_agent: started for project '{project_name}'")

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

    user_content = f"""PROJECT NAME: {project_name}

PROJECT BRIEF:
{brief}
{search_context}

Generate the complete research report now. Cover every required section thoroughly. Be specific to this project - do not write generic content that could apply to any app."""

    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": user_content},
    ]

    print("[ResearchAgent] Calling LLM (primary attempt)...")
    report = call_llm(messages, "research", max_tokens=4000)

    min_length = 800
    is_valid, missing_sections = validate_research_report(report)
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
                    f"{missing_sections or 'none'}. The research report must be at minimum 800 words and must "
                    "include ALL required sections: Problem Space, Existing Solutions & Competitors "
                    "(table with 4+ entries), Target Users, Technical Landscape, Key Risks, "
                    "Recommended Approach, and Research Confidence Score. Do not summarise - "
                    "write the full content in the required markdown structure."
                ),
            }
        )
        report = call_llm(messages, "research", max_tokens=4000)
        is_valid, missing_sections = validate_research_report(report)

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
