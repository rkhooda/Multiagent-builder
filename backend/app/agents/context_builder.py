"""Focused per-file context assembly for the coder agents (Day 18).

Shared by the frontend coder (Day 18) and — unchanged — the backend coder
(Day 19). Replaces the Day 12 "dump the whole architecture doc" approach that
produced wrong imports and hallucinated endpoints.

The design (see the Day 18 ponytail conclusions in the commit body):
- Architecture is sliced by `## ` headers per call; only the sections a task
  actually touches are injected (always the API-endpoints section as the
  anti-hallucination anchor, plus the component/frontend section).
- Dependencies (`requires`) are injected as regex interface summaries — the
  file's export lines verbatim — not full bodies, so 12+ files stay in budget
  without dropping the one symbol a component needs. Tiny deps go in whole.
- A hard char budget (≤ ~4K tokens) forces real trimming with a defined
  degradation order, and every trim is logged to state["log"].
"""
import json
import re
from typing import Optional

# ≤ 4K tokens; chars/4 heuristic → ~16K chars. Kept well under the free-tier
# request caps that bit us on Days 12/14.
MAX_CONTEXT_CHARS = 16000
FULL_DEP_THRESHOLD = 800  # deps smaller than this are injected whole, not summarised
API_SECTION_CHARS = 1800
COMPONENT_SECTION_CHARS = 1400


def estimate_tokens(text: str) -> int:
    """Cheap token estimate — chars/4. Good enough for budgeting."""
    return len(text) // 4


def split_sections(doc: str) -> dict:
    """Split a markdown doc into {lowercased '## ' heading: body}.

    Robust to LLM formatting drift: matches any level-2 heading, case-
    insensitively; returns {} when nothing parses so callers fall back to a
    whole-doc slice rather than crashing.
    """
    sections = {}
    if not doc:
        return sections
    # Split on level-2 headings, keeping the heading text as the key.
    parts = re.split(r"(?m)^##\s+(.+?)\s*$", doc)
    # parts[0] is any preamble before the first heading; then (heading, body)...
    for i in range(1, len(parts) - 1, 2):
        heading = parts[i].strip().lower()
        body = parts[i + 1].strip()
        if heading:
            sections[heading] = body
    return sections


def _find_section(sections: dict, keywords: list) -> Optional[str]:
    """First section whose heading contains any keyword, else None."""
    for heading, body in sections.items():
        if any(kw in heading for kw in keywords):
            return body
    return None


def extract_exports(content: str) -> str:
    """Regex interface summary of a JS/JSX file: its export lines, verbatim.

    Deliberately dumb (no AST — that's Day 22). Captures `export default`,
    named `export const/function/class`, re-exports, and bare `export { ... }`
    lists so a dependent file sees exactly what it can import.
    """
    lines = content.splitlines()
    exports = []
    for line in lines:
        stripped = line.strip()
        if stripped.startswith("export "):
            # Keep the signature line, drop a trailing `{` body opener.
            sig = stripped.split("{")[0].strip() if "=>" not in stripped and "(" in stripped else stripped
            exports.append(sig.rstrip("{").strip() if sig.endswith("{") else stripped)
    if not exports:
        return "(no explicit exports found — inspect the dependency's usage in the architecture)"
    # De-dup while preserving order.
    seen, unique = set(), []
    for e in exports:
        if e not in seen:
            seen.add(e)
            unique.append(e)
    return "\n".join(unique)


def _tech_stack_block(tech_stack_str: str) -> str:
    if not tech_stack_str:
        return "Frontend: React 19 + Vite + TailwindCSS + axios"
    try:
        stack = json.loads(tech_stack_str)
        return (
            f"Frontend: {stack.get('frontend', 'React 19 + Vite + TailwindCSS + axios')}\n"
            f"Backend: {stack.get('backend', 'FastAPI')}\n"
            f"Database: {stack.get('database', 'PostgreSQL')}"
        )
    except (json.JSONDecodeError, TypeError):
        return "Frontend: React 19 + Vite + TailwindCSS + axios"


def _folder_map(file_list: list, phase_prefix: str = "frontend/src") -> str:
    """A compact list of the phase's files so the model computes relative
    import paths correctly. Only paths under phase_prefix are shown."""
    paths = sorted(p for p in (file_list or []) if p.startswith(phase_prefix))
    if not paths:
        return ""
    return "\n".join(f"  {p}" for p in paths)


def _tasks_by_id(implementation_plan_str: str) -> dict:
    try:
        tasks = json.loads(implementation_plan_str)
    except (json.JSONDecodeError, TypeError):
        return {}
    return {t.get("id"): t for t in tasks if isinstance(t, dict)}


def build_file_context(task: dict, state: dict, phase_prefix: str = "frontend/src") -> str:
    """Assemble a focused, budget-bounded context string for ONE coder task.

    Returns the user-message body. Logs the final context size and any trims
    to state["log"] (Day 23 LangSmith / Day 26 optimisation depend on this
    data existing).
    """
    log = state.get("log")
    filepath = task.get("filepath", "")
    description = task.get("description", "")
    is_custom = task.get("custom", False)

    architecture_doc = state.get("architecture_doc", "")
    tech_stack_str = state.get("tech_stack", "")
    implementation_plan = state.get("implementation_plan", "[]")
    generated_files = state.get("generated_files", {})
    file_list = state.get("file_list", [])

    sections = split_sections(architecture_doc)

    # ── API endpoints — the anti-hallucination anchor, always included ──────
    api_section = _find_section(sections, ["api endpoint", "api routes", "endpoints", "rest api"])
    if not api_section and architecture_doc and not sections:
        # Formatting drift: no parseable headings — fall back to a slice.
        api_section = architecture_doc[:API_SECTION_CHARS]
    api_block = (api_section or "(no API section found in architecture)")[:API_SECTION_CHARS]

    # ── Component / frontend layout section (skipped for custom tasks) ──────
    component_block = ""
    if not is_custom:
        comp = _find_section(sections, ["component", "frontend", "ui ", "pages", "routing"])
        if comp:
            component_block = comp[:COMPONENT_SECTION_CHARS]

    # ── Dependency exports (interface summaries; full for tiny deps) ────────
    by_id = _tasks_by_id(implementation_plan)
    dep_blocks = []
    dep_are_full = []  # parallel: True if this dep was injected whole (degradation target)
    for dep_id in task.get("requires", []) or []:
        dep_task = by_id.get(dep_id)
        if not dep_task:
            continue
        dep_path = dep_task.get("filepath", dep_id)
        content = generated_files.get(dep_path)
        if not content:
            dep_blocks.append(
                f"--- {dep_path} ---\n"
                f"(dependency failed to generate; code against its expected "
                f"interface from the architecture above)"
            )
            dep_are_full.append(False)
        elif len(content) <= FULL_DEP_THRESHOLD:
            dep_blocks.append(f"--- {dep_path} (full) ---\n{content.strip()}")
            dep_are_full.append(True)
        else:
            dep_blocks.append(f"--- {dep_path} (exports) ---\n{extract_exports(content)}")
            dep_are_full.append(False)

    folder_map = _folder_map(file_list, phase_prefix)

    def assemble() -> str:
        parts = [
            "TASK",
            f"File to generate: {filepath}",
            f"Description: {description}",
            "",
            "TECH STACK",
            _tech_stack_block(tech_stack_str),
            "",
            "ARCHITECTURE — API ENDPOINTS (use ONLY these; do not invent endpoints)",
            api_block,
        ]
        if component_block:
            parts += ["", "ARCHITECTURE — COMPONENTS / FRONTEND LAYOUT", component_block]
        if dep_blocks:
            parts += ["", "DEPENDENCY FILES (already generated — import from these)"] + dep_blocks
        if folder_map:
            parts += [
                "",
                f"FOLDER MAP ({phase_prefix}) — compute relative import paths from this",
                folder_map,
            ]
        parts += [
            "",
            f"This file is at {filepath}. Import the shared client and siblings "
            f"RELATIVE to this path. Output only the file's code.",
        ]
        return "\n".join(parts)

    context = assemble()
    trims = []

    # ── Degradation order: full deps → summaries → drop component section ───
    if estimate_tokens(context) > MAX_CONTEXT_CHARS // 4:
        # 1. Downgrade any full dependency bodies to export summaries.
        for i, dep_id in enumerate([d for d in (task.get("requires", []) or []) if by_id.get(d)]):
            if i < len(dep_are_full) and dep_are_full[i]:
                dep_task = by_id.get(dep_id)
                dep_path = dep_task.get("filepath", dep_id)
                content = generated_files.get(dep_path, "")
                dep_blocks[i] = f"--- {dep_path} (exports) ---\n{extract_exports(content)}"
                dep_are_full[i] = False
                trims.append(f"full->summary {dep_path}")
        context = assemble()

    if estimate_tokens(context) > MAX_CONTEXT_CHARS // 4 and component_block:
        # 2. Drop the least-critical section (component layout; API endpoints stay).
        component_block = ""
        trims.append("dropped component/frontend section")
        context = assemble()

    if estimate_tokens(context) > MAX_CONTEXT_CHARS // 4:
        # 3. Last resort — hard-truncate the whole context to budget.
        context = context[:MAX_CONTEXT_CHARS]
        trims.append("hard-truncated to budget")

    if log is not None:
        size = f"{estimate_tokens(context)} tok / {len(context)} chars"
        note = f" (trimmed: {', '.join(trims)})" if trims else ""
        log.append(f"context_builder: {filepath} — {size}{note}")

    return context
