"""Shared code-fence cleaning for LLM-generated files.

Single source of truth: write_project_file strips at write time, and
clean_project_files runs once before gate 4 so state, disk, and the ZIP
all hold the same fence-free content.
"""
import re

_FENCE_OPEN_RE = re.compile(r'^```[a-zA-Z0-9_.-]*[ \t]*\n?')
_FENCE_CLOSE_RE = re.compile(r'\n?```\s*$')


def strip_code_fences(code: str) -> str:
    """
    Remove markdown code fences that LLMs add despite instructions not to.
    Handles ```python, ```yaml, ```dockerfile, plain ```, and doubled fences.
    """
    code = code.strip()
    # Loop: some models wrap output in nested/doubled fences.
    for _ in range(3):
        stripped = _FENCE_CLOSE_RE.sub('', _FENCE_OPEN_RE.sub('', code)).strip()
        if stripped == code:
            break
        code = stripped
    return code


def clean_project_files(project_id: str, generated_files: dict) -> tuple:
    """
    Strip fences from every file in generated_files. Files whose content
    changed are rewritten to disk so state and disk stay identical.
    Returns (cleaned_files_dict, cleaned_count).
    """
    from .file_writer import write_project_file

    cleaned_files = {}
    cleaned_count = 0
    for filepath, content in generated_files.items():
        cleaned = strip_code_fences(content or "")
        if cleaned != (content or "").strip():
            # Post Day 18 every coder strips at write time via
            # process_and_write_generated_file, so anything caught here means a
            # coder bypassed that chain — a bug worth surfacing, not silent.
            print(f"[CodeCleaner] WARNING: {filepath} still had fences at the "
                  f"gate-4 safety net — a coder bypassed write-time stripping", flush=True)
            result = write_project_file(project_id, filepath, cleaned)
            if result["success"]:
                cleaned_count += 1
            else:
                cleaned = content  # keep original in state if rewrite failed
        cleaned_files[filepath] = cleaned
    return cleaned_files, cleaned_count
