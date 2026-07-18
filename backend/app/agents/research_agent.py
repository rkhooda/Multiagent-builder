import json
from pathlib import Path

from ..core.connection_manager import manager
from ..validation import call_validated
from .search import format_search_results, web_search
from .utils import build_feedback_prompt, regeneration_target, truncate_for_context


SYSTEM_PROMPT = (
    Path(__file__).resolve().parents[3] / "prompts" / "research_agent.md"
).read_text(encoding="utf-8")


def run_targeted_web_search(project_name: str, brief: str) -> str:
    """
    Runs multiple targeted search queries relevant to what a research analyst
    would want to know about this project space. Formats results as structured
    intelligence rather than raw snippets.

    Returns a formatted string ready for injection into the user message,
    or empty string if search fails entirely.
    """
    try:
        from .search import web_search
    except ImportError:
        return ""

    brief_seed = brief[:100].strip()

    queries = [
        f"{project_name} competitors pricing 2025",
        f"{brief_seed} market size tools",
        f"{project_name} alternatives open source",
        f"{brief_seed} industry trends 2025",
    ]

    all_results = []
    seen_urls = set()

    for query in queries:
        try:
            results = web_search(query, max_results=3)
            for r in results:
                url = r.get('href', r.get('url', ''))
                if url and url not in seen_urls:
                    seen_urls.add(url)
                    all_results.append({
                        'title': r.get('title', ''),
                        'snippet': r.get('body', r.get('snippet', '')),
                        'url': url,
                        'query': query,
                    })
        except Exception:
            continue

    if not all_results:
        return ""

    lines = [
        "WEB SEARCH INTELLIGENCE:",
        "The following is live market data gathered specifically for this report.",
        "Use specific details, product names, and pricing from these results as evidence in your analysis.",
        "Distinguish between information from search results vs your training knowledge when relevant.",
        "",
    ]

    for i, r in enumerate(all_results[:10], 1):
        title = r['title'].strip()
        snippet = r['snippet'].strip()
        if title or snippet:
            lines.append(f"[Source {i}] {title}")
            if snippet:
                lines.append(f"  -> {snippet[:300]}")
            lines.append("")

    return "\n".join(lines)


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

    section_specs = []

    # PERMANENT 1 — Problem Space
    section_specs.append((
        "## Problem Space",
        "Write exactly 3 paragraphs. Each paragraph must cover one of these angles:\n\n"
        "Paragraph 1 — THE PROBLEM IN HUMAN TERMS: Describe the core problem from the perspective of the person "
        "experiencing it daily. Be visceral and specific. What does their day actually look like? What goes wrong, "
        "how often, and what does it cost them in time, money, or stress? Avoid abstract framing — put the reader "
        "inside the problem.\n\n"
        "Paragraph 2 — WHY THIS PROBLEM PERSISTS: Explain the structural reasons this problem has not been solved "
        "satisfactorily. Is it a market failure (incumbents don't care about this segment)? A technical barrier "
        "(the tooling didn't exist until recently)? A behavioural barrier (users have workarounds they're attached to)? "
        "A distribution problem (good solutions exist but don't reach the right buyers)? Identify the real reason, "
        "not a surface reason.\n\n"
        "Paragraph 3 — THE OPPORTUNITY WINDOW: Explain why NOW is the right time to build this. What has changed "
        "recently — in technology, user behaviour, market conditions, or regulatory environment — that makes this "
        "problem more solvable or more urgent than it was 3-5 years ago?\n\n"
        "Minimum 300 words total. Every sentence must be specific to this project."
    ))

    # OPTIONAL — Existing Solutions & Competitors
    if include_existing:
        section_specs.append((
            "## Existing Solutions & Competitors",
            "Write a competitive analysis with two parts:\n\n"
            "PART 1 — COMPETITIVE MATRIX TABLE:\n"
            "Create a markdown table with these exact columns:\n"
            "| Product/Solution | Core Strength | Critical Weakness | Pricing Model | Market Position |\n\n"
            "Rules for this table:\n"
            "- Minimum 4 rows. Maximum 6 rows.\n"
            "- Every entry must be a real, named product or a clearly defined category of solution "
            "(e.g. 'Excel/Google Sheets manual tracking')\n"
            "- The Core Strength must be the single most compelling reason a user would choose this product\n"
            "- The Critical Weakness must be the most significant gap or pain point users report\n"
            "- Pricing Model must be specific: name the tier structure or price point if known "
            "(e.g. '$12/user/month', 'Free + $49/mo Pro', 'Enterprise only')\n"
            "- Market Position must classify: Solo/SMB/Mid-market/Enterprise and brand positioning\n\n"
            "PART 2 — COMPETITIVE GAP ANALYSIS:\n"
            "After the table, write 2 paragraphs:\n"
            "- Paragraph 1: What is the clearest unmet need across all these competitors? "
            "Where does every existing solution fall short for a specific type of user?\n"
            "- Paragraph 2: What is the specific positioning opportunity for this project? "
            "State it as a one-sentence positioning hypothesis: "
            "'Unlike [competitor category], [PROJECT_NAME] is built for [specific user] "
            "who needs [specific thing] without [specific compromise].'"
        ))

    # OPTIONAL — Target Users
    if include_users:
        section_specs.append((
            "## Target Users",
            "Define the primary user persona with clinical precision using this exact structure:\n\n"
            "**Primary User Persona Name**: [Give them a memorable archetype name, "
            "e.g. 'The Overloaded Solo Freelancer' or 'The Agency Operations Lead']\n\n"
            "**Who They Are**: 2-3 sentences. Job title, company size context, years of experience, "
            "and one specific detail about their daily work that makes them real.\n\n"
            "**Their Typical Day (Relevant to This Problem)**: Describe a specific scenario — walk through "
            "what their day looks like when they encounter this problem. What triggers it? What do they do next? "
            "How long does it take? What does the workaround cost them?\n\n"
            "**Pain Points** (numbered list of exactly 3):\n"
            "For each pain point:\n"
            "- State the pain in one bold sentence\n"
            "- Follow with 2-3 sentences explaining the downstream consequence\n\n"
            "**Current Workarounds**: Name the specific tools or methods they use today to cope. "
            "Be concrete — 'they use a Google Sheet with a custom formula column' not 'they use manual methods'. "
            "Explain why the workaround is tolerable but not good enough.\n\n"
            "**What Success Looks Like**: Write this as a specific outcome statement, not a feeling. "
            "Quantify where possible.\n\n"
            "**Willingness to Pay Signal**: Based on the problem severity and current workarounds, "
            "estimate what this user would reasonably pay monthly for a good solution and why."
        ))

    # PERMANENT 2 — Technical Landscape
    section_specs.append((
        "## Technical Landscape",
        "Write exactly 3 paragraphs covering these three angles:\n\n"
        "Paragraph 1 — STANDARD TECHNICAL STACK FOR THIS CATEGORY: What technologies, frameworks, databases, "
        "and architectural patterns are most commonly used to build products in this space? Name specific libraries, "
        "APIs, and tools (e.g. Stripe for payments, Plaid for banking, Twilio for SMS). Explain why these are the "
        "dominant choices — what properties make them right for this problem domain?\n\n"
        "Paragraph 2 — INTEGRATION COMPLEXITY AND KEY TECHNICAL DEPENDENCIES: What external systems, APIs, or data "
        "sources does a product in this space need to connect to? What are the technical risks or compliance "
        "requirements these integrations introduce? (e.g. PCI-DSS for payment processing, GDPR for European user data, "
        "OAuth complexity for social auth). Be specific about what makes integration in this domain hard vs straightforward.\n\n"
        "Paragraph 3 — BUILD VS BUY DECISIONS AND RECENT TOOLING SHIFTS: What components should be built from scratch "
        "vs assembled from existing services? What has changed in the last 2-3 years in the tooling available for this "
        "category that makes it easier or harder to build now? Are there new AI capabilities, open source libraries, "
        "or infrastructure services that change the calculus?\n\n"
        "Minimum 250 words total."
    ))

    # PERMANENT 3 — Key Risks (always contains Technical + Execution Risks)
    key_risks_format = (
        "Use the heading ## Key Risks with these sub-sections:\n\n"
        "### Technical Risks\n"
        "For each risk, use this format:\n"
        "**Risk Name**: [one bold sentence naming the risk]\n"
        "Impact: [What breaks or fails if this risk materialises? Be specific about consequence.]\n"
        "Mitigation: [What is the best known approach to reduce this risk? "
        "Name specific patterns, libraries, or architectural decisions.]\n\n"
        "Write exactly 2 technical risks using the format above.\n\n"
    )
    if include_market:
        key_risks_format += (
            "### Market Risks\n"
            "Use the same format as Technical Risks (Risk Name bold, Impact, Mitigation).\n"
            "Write exactly 2 market risks covering competition, adoption challenges, or pricing pressure.\n\n"
        )
    key_risks_format += (
        "### Execution Risks\n"
        "Use the same format as Technical Risks (Risk Name bold, Impact, Mitigation).\n"
        "Write exactly 2 execution risks covering team capability, timeline, scope creep, "
        "or dependency on third parties."
    )
    section_specs.append(("## Key Risks", key_risks_format))

    # PERMANENT 4 — Recommended Approach
    section_specs.append((
        "## Recommended Approach",
        "Write exactly 3 paragraphs structured as follows:\n\n"
        "Paragraph 1 — THE CORE STRATEGIC BET: State the fundamental product decision that will define success or "
        "failure for this project. This is not a feature list — it is the single most important strategic choice the "
        "team must get right. What does this product need to be uniquely good at, and what must it consciously "
        "deprioritise in v1? Be direct and opinionated.\n\n"
        "Paragraph 2 — MVP SCOPE AND SEQUENCING: Describe the minimal viable product that would be worth building "
        "and shipping. Name the 4-6 specific features or capabilities that constitute the MVP. Explain the sequencing "
        "logic — what gets built first and why. Identify the single metric that will tell the team whether the MVP "
        "succeeded.\n\n"
        "Paragraph 3 — DIFFERENTIATION AND MOAT: Explain how this product builds a defensible position over time. "
        "What is the mechanism that makes it hard for a user to leave once they've been using it for 6 months? "
        "Is it data accumulation, workflow integration, network effects, or switching costs? How does the v1 scope "
        "set up this long-term moat, even if the moat itself isn't visible yet?\n\n"
        "Minimum 300 words total. This section should read like advice from an experienced founder, "
        "not a strategy consultant's slide deck."
    ))

    # PERMANENT 5 — Research Confidence Score
    section_specs.append((
        "## Research Confidence Score",
        "Write this section using exactly this structure:\n\n"
        "**Score**: [Choose one: High / Medium / Low]\n\n"
        "**Justification**: Write a single paragraph (minimum 100 words) that justifies the score by addressing "
        "all four of these dimensions:\n"
        "1. Market clarity — how well-defined and measurable is the target market?\n"
        "2. Technical certainty — how well-understood are the technical requirements and risks?\n"
        "3. Competitive intelligence quality — how complete and reliable is the competitive picture?\n"
        "4. Brief specificity — how much detail did the project brief provide to ground this analysis?\n\n"
        "Be honest. If the brief was vague, say so. If the market is crowded and hard to read, say so. "
        "A Low confidence score with a strong justification is more useful than a High score with weak reasoning.\n\n"
        "Do not use bullet points in this section — write it as flowing prose."
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


def validate_report_quality(report: str, optional_sections: dict) -> tuple:
    """
    Quality checks beyond just length. Returns (passed, list_of_issues).
    """
    issues = []

    if len(report) < 800:
        issues.append(f"Report is only {len(report)} chars — too short for a premium report. Minimum 800 chars required.")

    permanent_sections = [
        ("## Problem Space", "Problem Space"),
        ("## Technical Landscape", "Technical Landscape"),
        ("## Recommended Approach", "Recommended Approach"),
        ("## Research Confidence Score", "Research Confidence Score"),
    ]
    for heading, name in permanent_sections:
        if heading.lower() not in report.lower():
            issues.append(f"Missing required section: {name}")

    if "### Technical Risks" not in report and "Technical Risks" not in report:
        issues.append("Missing Technical Risks sub-section under Key Risks")
    if "### Execution Risks" not in report and "Execution Risks" not in report:
        issues.append("Missing Execution Risks sub-section under Key Risks")

    forbidden = []
    if not optional_sections.get("existing_solutions", False):
        forbidden.append("## Existing Solutions")
    if not optional_sections.get("target_users", False):
        forbidden.append("## Target Users")

    for heading in forbidden:
        if heading.lower() in report.lower():
            issues.append(f"Forbidden section appeared: {heading} — this was not enabled by the user")

    generic_phrases = [
        "users struggle with",
        "this is a common problem",
        "many businesses face",
        "in today's fast-paced",
        "leveraging cutting-edge",
    ]
    generic_count = sum(1 for p in generic_phrases if p.lower() in report.lower())
    if generic_count >= 3:
        issues.append(f"Report contains {generic_count} generic filler phrases — needs more specific language")

    return len(issues) == 0, issues


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

    # Gate-1 back-navigation: regenerate the report with the previous version +
    # feedback injected (size-bounded — reports run ~16k chars).
    previous_report = regeneration_target(state, "research_report")
    human_feedback = state.get("human_feedback", "")
    if previous_report:
        log.append("research_agent: re-running with human feedback")

    # Targeted multi-query web search
    print(f"[ResearchAgent] Running targeted web search for: {project_name}")
    search_context = run_targeted_web_search(project_name, brief)
    if search_context:
        source_count = search_context.count('[Source')
        log.append(f"research_agent: web search enrichment gathered {source_count} sources")
        print(f"[ResearchAgent] Web search: {source_count} sources found")
    else:
        log.append("research_agent: web search returned no results, proceeding on training knowledge")
        print("[ResearchAgent] Web search: no results, proceeding without enrichment")

    # Build the runtime report structure — the only place optional sections exist
    report_structure_block = build_report_structure_block(optional_sections_str)

    user_content = f"""PROJECT NAME: {project_name}

PROJECT BRIEF:
{brief}

---

{report_structure_block}

---

{"" if not search_context else search_context}

---

GENERATION INSTRUCTIONS:
You are now writing the research report for {project_name}.

Before you begin, internalise this: the person reading this report is about to commit significant time and money to building this product. Every vague sentence you write wastes their time. Every specific insight you provide could save them months of going in the wrong direction.

Write the report now. Start with # Research Report: {project_name} and proceed through each section in the REPORT_STRUCTURE list above. Do not write any section not in that list. Do not add preamble before the title. Do not add a closing note after the final section.

Quality bar: if a sentence could appear in a report about any software product, rewrite it until it could only appear in a report about {project_name}."""

    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user",   "content": user_content},
    ]

    if previous_report:
        messages.append({
            "role": "user",
            "content": build_feedback_prompt(
                truncate_for_context(previous_report, max_chars=8000), human_feedback
            ),
        })

    print("[ResearchAgent] Calling LLM...")
    # Quality checks + one-shot repair live in the shared validation registry
    # (app/validation.py) — validate_report_quality below is its plug-in.
    report = call_validated(
        messages, "research", state, max_tokens=4500,
        original_instruction=(
            f"Every sentence must be specific to {project_name}, not generic advice. "
            f"Write the full corrected report now starting with # Research Report: {project_name}"
        ),
        log=log,
    )

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
