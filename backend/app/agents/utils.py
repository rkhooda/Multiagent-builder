import re
import json
from typing import List, Optional


def extract_json_block(text: str) -> Optional[dict]:
    """
    Extract and parse a JSON code block from LLM response text.
    Handles both ```json ... ``` and ``` ... ``` formats.
    Returns parsed dict or None if no valid JSON block found.
    """
    # Try ```json ... ``` format first (most common)
    match = re.search(r'```json\s*([\s\S]*?)\s*```', text, re.IGNORECASE)
    if not match:
        # Try plain ``` ... ``` format
        match = re.search(r'```\s*(\{[\s\S]*?\})\s*```', text)
    if not match:
        # Try to find a raw JSON object anywhere in the text as last resort
        match = re.search(r'(\{[^{}]*"frontend"[^{}]*\})', text, re.DOTALL)

    if match:
        try:
            return json.loads(match.group(1).strip())
        except json.JSONDecodeError as e:
            print(f"[Utils] JSON parse error: {e}")
            return None

    return None


def extract_tech_stack(llm_response: str) -> dict:
    """
    Extract tech stack JSON from LLM response and validate against TechStackSchema.
    Returns validated dict. Falls back to sensible defaults if parsing fails.
    """
    from ..models.tech_stack import TechStackSchema, DEFAULT_TECH_STACK

    data = extract_json_block(llm_response)

    if data:
        try:
            stack = TechStackSchema(**data)
            print(f"[Utils] Tech stack parsed successfully: {stack.frontend} / {stack.backend}")
            return stack.model_dump()
        except Exception as e:
            print(f"[Utils] Tech stack validation error: {e}")

    print("[Utils] Could not parse tech stack JSON — using defaults")
    return DEFAULT_TECH_STACK.model_dump()


def strip_code_fences(code: str) -> str:
    """
    Remove markdown code fences from LLM-generated code.
    LLMs often wrap code in ```python or ```javascript despite instructions.
    """
    code = code.strip()
    # Remove opening fence with optional language tag
    code = re.sub(r'^```[a-zA-Z]*\n?', '', code)
    # Remove closing fence
    code = re.sub(r'\n?```$', '', code)
    return code.strip()


def truncate_for_context(text: str, max_chars: int = 6000) -> str:
    """
    Truncate long text to fit within LLM context limits.
    Cuts from the middle to preserve beginning and end.
    """
    if len(text) <= max_chars:
        return text
    half = max_chars // 2
    return (
        text[:half] +
        f"\n\n[... {len(text) - max_chars} characters truncated to fit context ...]\n\n" +
        text[-half:]
    )


def parse_folder_structure(architecture_doc: str) -> List[str]:
    """
    Extract all file paths from a folder structure tree in the architecture document.

    Looks for a code block containing the folder tree and returns a flat list of
    file paths such as:
    ['backend/app/models.py', 'backend/app/routers/users.py', 'frontend/src/App.jsx']

    Skips directory-only lines, comment lines, and tree heading noise.
    """
    folder_section_match = re.search(
        r"##\s*Folder Structure.*?```(?:text|bash)?\s*([\s\S]*?)(?:```|\n##\s|\Z)",
        architecture_doc,
        re.IGNORECASE | re.DOTALL,
    )

    if not folder_section_match:
        print("[Utils] Could not find folder structure code block in architecture doc")
        return []

    tree_text = folder_section_match.group(1).strip("\n")
    file_list: List[str] = []
    path_stack: List[str] = []

    for raw_line in tree_text.splitlines():
        if not raw_line.strip():
            continue

        line = raw_line.rstrip()
        stripped = line.strip()
        if not stripped:
            continue

        # Ignore comments and obvious tree headings.
        if stripped.startswith("#"):
            continue

        indent_prefix = re.match(r"^[\s│|]*", line)
        indent_segment = indent_prefix.group(0) if indent_prefix else ""
        depth = len(indent_segment.replace("\t", "    ")) // 4

        clean = re.sub(r"^[\s│|]*(?:├──|└──|\+--)?\s*", "", line).strip()
        clean = re.sub(r"\s+#.*$", "", clean).strip()

        if not clean:
            continue

        if clean in {".", "./"}:
            continue

        if clean.endswith(":"):
            continue

        is_directory = clean.endswith("/")
        name = clean.rstrip("/")

        if not name:
            continue

        path_stack = path_stack[:depth]

        if is_directory:
            path_stack.append(name)
            continue

        # Support trees where directories appear without a trailing slash.
        if "." not in name:
            path_stack.append(name)
            continue

        full_path = "/".join(path_stack + [name]) if path_stack else name
        file_list.append(full_path)

    print(f"[Utils] Parsed {len(file_list)} files from folder structure")
    return file_list


def extract_mermaid_diagrams(text: str) -> List[dict]:
    """
    Extract Mermaid diagram blocks from a document.
    Returns dicts shaped like: {"type": "erDiagram"|"flowchart", "code": "..."}
    """
    diagrams = []
    matches = re.finditer(r"```mermaid\s*([\s\S]*?)```", text, re.IGNORECASE)
    for match in matches:
        code = match.group(1).strip()
        diagram_type = "erDiagram" if "erDiagram" in code else "flowchart"
        diagrams.append({"type": diagram_type, "code": code})
    return diagrams
