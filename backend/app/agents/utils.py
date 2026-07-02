import re
import json
from typing import Optional


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
