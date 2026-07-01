from duckduckgo_search import DDGS
from typing import Dict, List


def web_search(query: str, max_results: int = 5) -> List[Dict]:
    """
    Search the web using DuckDuckGo. Returns list of dicts with:
    title, href, body (snippet)
    Returns empty list on any failure - web search is optional enrichment.
    """
    try:
        with DDGS() as ddgs:
            results = list(ddgs.text(query, max_results=max_results))
        return results
    except Exception as e:
        print(f"[WebSearch] Failed for query '{query}': {e}")
        return []


def format_search_results(results: List[Dict]) -> str:
    """Format search results into a clean string for injection into prompts."""
    if not results:
        return ""

    lines = ["Web search results for additional context:"]
    for i, result in enumerate(results, 1):
        title = result.get("title", "No title")
        snippet = result.get("body", result.get("snippet", "No description"))
        lines.append(f"{i}. {title}: {snippet}")
    return "\n".join(lines)
