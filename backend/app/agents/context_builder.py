"""Focused per-file context assembly for the coder agents (Day 18 + Day 19).

`build_file_context` dispatches on `phase`: the frontend path (Day 18, default)
and the backend path (Day 19). Both replace the Day 12 "dump the whole
architecture doc" approach that produced wrong imports and hallucinated
endpoints, and both share the section splitter, char budget, and degradation
logging.

Frontend (Day 18): API-endpoints section as the anti-hallucination anchor plus
the component section; `requires` deps as regex export summaries, tiny deps
whole.

Backend (Day 19): section selection driven by file kind (SQL schema for
models/schemas, THIS resource's API-table rows for routers), and — because
backend cross-file truth is unforgiving (a wrong import kills the app at
startup) — the FULL model/schema bodies for a router's resource, discovered from
the file tree even when the planner didn't list them in `requires`. Field
names/types are exactly what gets hallucinated when summarised, so they go in
whole and are truncated (never dropped) under budget pressure.
"""
import json
import re
from typing import Optional

from .utils import truncate_for_context

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


def extract_python_symbols(content: str) -> str:
    """Interface summary of a Python file: its top-level `class`/`def` lines,
    verbatim. Used for backend dependency summaries when full content won't fit
    the budget (the model still sees the names it can import). Deliberately dumb
    — no AST (that's Day 22)."""
    out = []
    for line in content.splitlines():
        if line and line[0] not in " \t" and (
            line.startswith(("class ", "def ", "async def "))
        ):
            out.append(line.rstrip(":").rstrip())
    if not out:
        return "(no top-level class/def found)"
    return "\n".join(out)


# Which architecture sections and dependency treatment each backend file kind
# needs. Drives section selection so a router isn't fed the component tree and a
# model isn't fed the API table.
def backend_file_kind(filepath: str, description: str = "") -> str:
    """Classify a backend file from its path (primary) then description.

    Returns one of: config, database, main, model, schema, router, service,
    other. Infra kinds (config/database/main) are generated deterministically
    elsewhere; the LLM-generated kinds are model/schema/router/service.
    """
    low = (filepath or "").lower()
    name = low.rsplit("/", 1)[-1]
    d = (description or "").lower()

    if name in ("config.py", "settings.py"):
        return "config"
    if name in ("database.py", "db.py", "session.py", "base.py"):
        return "database"
    if name == "main.py" or low.endswith("/main.py"):
        return "main"
    if "/models/" in low or name.endswith(("_model.py", "_models.py")) or name == "models.py":
        return "model"
    if "/schemas/" in low or name.endswith(("_schema.py", "_schemas.py")) or name == "schemas.py":
        return "schema"
    if "/routers/" in low or "/routes/" in low or "/api/" in low or name.endswith(("_router.py", "_routes.py")):
        return "router"
    if "/services/" in low or "/crud/" in low or "/repositories/" in low:
        return "service"
    # Description fallback for flat layouts.
    if "router" in d or "endpoint" in d or "route" in d:
        return "router"
    if "pydantic" in d or "schema" in d:
        return "schema"
    if "orm" in d or "sqlalchemy model" in d or "table model" in d:
        return "model"
    return "other"


def _same_resource(a: str, b: str) -> bool:
    """Whether two resource names refer to the same resource, tolerant of the
    common singular-schema / plural-router convention (schemas/note.py vs
    routers/notes.py). Without this a plural router never links to its singular
    schema — no full-content injection, no ordering edge."""
    a, b = a.lower(), b.lower()
    return a.rstrip("s") == b.rstrip("s")


def _resource_of(filepath: str) -> str:
    """The resource name a router/schema/model file is about — its filename stem
    (`routers/invoices.py` -> `invoices`). Used to pick the resource's API rows
    and its matching model/schema files."""
    stem = filepath.rsplit("/", 1)[-1]
    for suf in ("_router", "_routes", "_schema", "_schemas", "_model", "_models"):
        if stem.endswith(suf + ".py"):
            return stem[: -(len(suf) + 3)]
    return stem[:-3] if stem.endswith(".py") else stem


def parse_api_table(section: str) -> list:
    """Parse a markdown API-endpoints table into row dicts.

    Returns [{method, path, auth, description, response}, ...], mapping columns
    by their header names so column order/extra columns don't matter. Empty list
    if no table is found (caller falls back to the raw section)."""
    rows = []
    table_lines = [l.strip() for l in (section or "").splitlines() if l.strip().startswith("|")]
    if len(table_lines) < 2:
        return rows

    def cells(line):
        return [c.strip() for c in line.strip().strip("|").split("|")]

    headers = [h.lower() for h in cells(table_lines[0])]
    for line in table_lines[1:]:
        # Skip the header separator row (---|---).
        if set(line.replace("|", "").replace(":", "").strip()) <= {"-", " "}:
            continue
        values = cells(line)
        if len(values) < 2:
            continue
        row = {}
        for i, h in enumerate(headers):
            val = values[i] if i < len(values) else ""
            if "method" in h:
                row["method"] = val
            elif "path" in h or "endpoint" in h or "route" in h:
                row["path"] = val
            elif "auth" in h:
                row["auth"] = val
            elif "response" in h:
                row["response"] = val
            elif "desc" in h:
                row["description"] = val
        if row.get("path"):
            rows.append(row)
    return rows


def _rows_for_resource(rows: list, resource: str) -> list:
    """API rows whose path's first segment matches this resource, with simple
    singular/plural tolerance. Falls back to all rows if none match (better a
    full table than none)."""
    res = resource.rstrip("s").lower()
    matched = []
    for row in rows:
        seg = row.get("path", "").strip("/").split("/")[0].lower()
        if seg.rstrip("s") == res or res in seg or seg in res:
            matched.append(row)
    return matched or rows


def _format_api_rows(rows: list) -> str:
    header = "| Method | Path | Auth | Description |"
    sep = "|--------|------|------|-------------|"
    body = "\n".join(
        f"| {r.get('method','')} | {r.get('path','')} | {r.get('auth','')} | {r.get('description','')} |"
        for r in rows
    )
    return f"{header}\n{sep}\n{body}"


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


def build_file_context(task: dict, state: dict, phase_prefix: str = "frontend/src",
                       phase: str = "frontend") -> str:
    """Assemble a focused, budget-bounded context string for ONE coder task.

    Dispatches by `phase`: the frontend path (Day 18, default — unchanged) and
    the backend path (Day 19). Both share the section splitter, budget, and
    degradation-logging machinery.

    Returns the user-message body. Logs the final context size and any trims
    to state["log"] (Day 23 LangSmith / Day 26 optimisation depend on this
    data existing).
    """
    if phase == "backend":
        return _build_backend_context(task, state, phase_prefix)
    return _build_frontend_context(task, state, phase_prefix)


def _build_frontend_context(task: dict, state: dict, phase_prefix: str = "frontend/src") -> str:
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


SQL_SECTION_CHARS = 2200


def _build_backend_context(task: dict, state: dict, phase_prefix: str = "backend") -> str:
    """Backend per-file context (Day 19). Cross-file truth is the whole point:
    a router's imports must resolve against the ACTUAL generated model/schema,
    so their FULL bodies are injected (field names/types are exactly what gets
    hallucinated when summarised). Section selection is driven by file kind;
    routers get only THEIR resource's API-table rows, not the whole table."""
    log = state.get("log")
    filepath = task.get("filepath", "")
    description = task.get("description", "")
    is_custom = task.get("custom", False)

    architecture_doc = state.get("architecture_doc", "")
    tech_stack_str = state.get("tech_stack", "")
    implementation_plan = state.get("implementation_plan", "[]")
    generated_files = state.get("generated_files", {})
    file_list = state.get("file_list", [])

    kind = backend_file_kind(filepath, description)
    resource = _resource_of(filepath)
    sections = split_sections(architecture_doc)

    # ── SQL schema — the anti-hallucination anchor for models/schemas ───────
    sql_block = ""
    if kind in ("model", "schema") or is_custom:
        sql = _find_section(sections, ["database schema", "schema", "tables", "data model"])
        if not sql and architecture_doc and not sections:
            sql = architecture_doc[:SQL_SECTION_CHARS]
        sql_block = (sql or "")[:SQL_SECTION_CHARS]

    # ── API rows — only this resource's, for routers/services ───────────────
    api_block = ""
    if kind in ("router", "service") or is_custom:
        api_section = _find_section(sections, ["api endpoint", "api routes", "endpoints", "rest api"])
        rows = parse_api_table(api_section or "")
        if rows:
            api_block = _format_api_rows(_rows_for_resource(rows, resource))
        elif api_section:
            api_block = api_section[:API_SECTION_CHARS]

    # ── Dependency files — FULL for the resource's model/schema, summaries
    #    for everything else. Discover the resource's model/schema even when the
    #    planner didn't list them in `requires` (cross-file truth, not trust). ──
    by_id = _tasks_by_id(implementation_plan)
    dep_paths = {}  # path -> full? (True = inject whole)

    if kind in ("router", "service", "schema"):
        for p in file_list:
            if not _same_resource(_resource_of(p), resource):
                continue
            pk = backend_file_kind(p, "")
            if pk == "model" or (pk == "schema" and kind != "schema"):
                dep_paths[p] = True

    for dep_id in task.get("requires", []) or []:
        dep_task = by_id.get(dep_id)
        if not dep_task:
            continue
        dp = dep_task.get("filepath", dep_id)
        pk = backend_file_kind(dp, "")
        dep_paths.setdefault(dp, pk in ("model", "schema"))

    def dep_block(path, full):
        content = generated_files.get(path)
        if not content:
            return (f"--- {path} ---\n(planned but not generated yet — import from "
                    f"its expected interface)"), False
        if full:
            return f"--- {path} (full) ---\n{content.strip()}", True
        return f"--- {path} (symbols) ---\n{extract_python_symbols(content)}", False

    dep_render = []  # (path, text, is_full)
    for path, full in dep_paths.items():
        text, is_full = dep_block(path, full)
        dep_render.append([path, text, is_full])

    folder_map = _folder_map(file_list, phase_prefix)

    def assemble():
        parts = [
            "TASK",
            f"File to generate: {filepath}",
            f"Description: {description}",
            f"File kind: {kind}",
            "",
            "TECH STACK",
            _tech_stack_block(tech_stack_str),
        ]
        if sql_block:
            parts += ["", "ARCHITECTURE — DATABASE SCHEMA (use ONLY these tables/columns)", sql_block]
        if api_block:
            parts += ["", f"ARCHITECTURE — API ENDPOINTS for `{resource}` (implement ONLY these)", api_block]
        if dep_render:
            parts += ["", "DEPENDENCY FILES (already generated — import from these, reuse exact names)"]
            parts += [text for _, text, _ in dep_render]
        if folder_map:
            parts += ["", f"PROJECT STRUCTURE ({phase_prefix}) — import ONLY from files here, using the app. package root", folder_map]
        parts += [
            "",
            f"This file is at {filepath}. Use absolute `app.` imports "
            f"(e.g. from app.models.{resource} import ...). Output only the file's code.",
        ]
        return "\n".join(parts)

    context = assemble()
    trims = []

    # ── Degradation: downgrade non-full deps' verbosity, then truncate full
    #    bodies, then hard-truncate — the resource's model/schema stay full as
    #    long as the budget allows (their fields are what must not be guessed). ─
    if estimate_tokens(context) > MAX_CONTEXT_CHARS // 4:
        # 1. Truncate any full dep bodies to fit rather than dropping fields.
        for row in dep_render:
            path, _, is_full = row
            if is_full:
                content = generated_files.get(path, "")
                row[1] = f"--- {path} (full, truncated) ---\n{truncate_for_context(content.strip(), 3000)}"
                trims.append(f"truncated {path}")
        context = assemble()

    if estimate_tokens(context) > MAX_CONTEXT_CHARS // 4 and sql_block:
        sql_block = sql_block[:1000]
        trims.append("shrank SQL schema section")
        context = assemble()

    if estimate_tokens(context) > MAX_CONTEXT_CHARS // 4:
        context = context[:MAX_CONTEXT_CHARS]
        trims.append("hard-truncated to budget")

    if log is not None:
        size = f"{estimate_tokens(context)} tok / {len(context)} chars"
        note = f" (trimmed: {', '.join(trims)})" if trims else ""
        log.append(f"context_builder: {filepath} [{kind}] — {size}{note}")

    return context
