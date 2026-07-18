"""Deterministic, AST-based Python import repair for generated backend files.

Day 19 ponytail #2: a coder LLM reliably gets import *shape* wrong in two safe,
mechanical ways — the wrong package prefix (`from backend.app.database import
Base` when the tree roots at `app`) and the wrong relative depth (`from
.database` from a file two packages deep). Both are safely auto-fixable because
the intended target exists in the project tree; we only need to rewrite the
path. Everything else is FLAGGED, never rewritten:

- A local import whose module exists NOWHERE in the tree is a real generation
  defect (a hallucinated module, or a symbol from a file that was never
  planned). Silently "fixing" it to the nearest name would paper over a bug that
  must surface in the Gate 4 QA report instead.
- Third-party imports (`fastapi`, `sqlalchemy`, `pydantic`, stdlib, ...) are
  never candidates — we only touch imports rooted at `app`/`backend` or written
  relative.

Why AST over regex: `ast` distinguishes `import`/`from ... import`, the relative
`level`, and multi-name imports with zero brittleness, and it is stdlib. Why AST
over an LLM: this is a pure, deterministic mechanical rewrite — an LLM call would
add latency, cost free-tier quota, and could hallucinate.

The rewrite itself is textual (targeted replacement on the import's own line) so
the rest of the file — comments, formatting, blank lines — is preserved exactly;
`ast.unparse` would reformat the whole file.

Language-aware hook: `fix_imports` handles Python (`.py`) today and returns the
input untouched for any other extension, so the write pipeline can opt JS in
later behind the same call.
"""
import ast
from pathlib import PurePosixPath


def _module_of(filepath: str):
    """Map a project filepath to its dotted `app....` module, or None if it is
    not an importable module under the `app` package.

    `backend/app/models/user.py` -> `app.models.user`
    `backend/app/database.py`     -> `app.database`
    `backend/app/models/__init__.py` -> `app.models`
    """
    parts = PurePosixPath(filepath.replace("\\", "/")).parts
    if "app" not in parts:
        return None
    mod = list(parts[parts.index("app"):])
    if not mod or not mod[-1].endswith(".py"):
        return None
    mod[-1] = mod[-1][:-3]
    if mod[-1] == "__init__":
        mod = mod[:-1]
    return ".".join(mod) if mod else None


def _build_index(project_file_tree):
    """Return (known_modules, known_packages) derived from the tree.

    known_modules: every importable `.py` module (`app.models.user`, ...).
    known_packages: every dotted prefix of those (`app`, `app.models`, ...) so
    package imports like `from app.routers import users` resolve too.
    """
    known_modules = set()
    known_packages = set()
    for path in project_file_tree or []:
        mod = _module_of(path)
        if not mod:
            continue
        known_modules.add(mod)
        segs = mod.split(".")
        for i in range(1, len(segs)):
            known_packages.add(".".join(segs[:i]))
    return known_modules, known_packages


def _resolve(module: str, level: int, current_module, known_modules, known_packages):
    """Resolve one ImportFrom target to a known `app....` module/package.

    Returns the corrected dotted path, or None if it can't be safely resolved
    (→ flag, don't rewrite). `module` is node.module (may be None for
    `from . import x`); `level` is node.level (0 = absolute).
    """
    known = known_modules | known_packages

    # ── Relative import: resolve against the current file's package ─────────
    if level > 0:
        if not current_module:
            return None
        base = current_module.split(".")[:-1]  # drop the module's own name
        up = level - 1
        base = base[: len(base) - up] if up <= len(base) else []
        naive = ".".join(base + ([module] if module else []))
        if naive in known:
            return naive
        # Wrong depth: fall back to a unique match on the written suffix.
        return _unique_by_suffix(module, known_modules, known_packages)

    # ── Absolute import rooted at app/backend ──────────────────────────────
    if module in known:
        return module  # already correct — untouched
    # Strip a stray `backend.` prefix (`backend.app.x` -> `app.x`).
    stripped = module
    while stripped.split(".")[0] == "backend":
        stripped = ".".join(stripped.split(".")[1:])
    if stripped in known:
        return stripped
    # Last resort: a unique tail match (handles `app.database` typo'd depth).
    return _unique_by_suffix(module, known_modules, known_packages)


def _unique_by_suffix(module, known_modules, known_packages):
    """A known module/package is a safe target only if the written import's
    final name matches exactly ONE known module. Ambiguity → no rewrite."""
    if not module:
        return None
    final = module.split(".")[-1]
    hits = {m for m in known_modules if m.split(".")[-1] == final}
    if len(hits) == 1:
        return next(iter(hits))
    return None


def _is_local_candidate(node) -> bool:
    """Only relative imports, or absolutes rooted at app/backend, are ours to
    touch. fastapi/sqlalchemy/pydantic/stdlib are left alone."""
    if node.level and node.level > 0:
        return True
    if node.module and node.module.split(".")[0] in ("app", "backend"):
        return True
    return False


def fix_imports(code: str, filepath: str, project_file_tree):
    """Rewrite the safely-fixable local imports in `code`; flag the rest.

    Returns (new_code, warnings). Warnings are human-readable strings describing
    imports that could not be safely resolved — the caller records them as
    `import_warning` entries for the Gate 4 QA panel. Non-Python files and
    unparseable files are returned untouched (the latter with one warning).
    """
    if not filepath.endswith(".py"):
        return code, []

    try:
        tree = ast.parse(code)
    except SyntaxError as e:
        return code, [f"{filepath}: skipped import repair — file does not parse ({e.msg})"]

    known_modules, known_packages = _build_index(project_file_tree)
    current_module = _module_of(filepath)

    lines = code.splitlines(keepends=True)
    warnings = []
    # (lineno, old_module_str, new_module_str) rewrites, applied after the walk.
    edits = []

    for node in ast.walk(tree):
        if not isinstance(node, ast.ImportFrom) or not _is_local_candidate(node):
            continue
        old = ("." * node.level) + (node.module or "")
        target = _resolve(node.module, node.level, current_module,
                           known_modules, known_packages)
        if target is None:
            names = ", ".join(a.name for a in node.names)
            warnings.append(
                f"{filepath}: unresolved local import `from {old} import {names}` "
                f"— no matching module in the project tree (generation defect, not auto-fixed)"
            )
            continue
        if target != old:
            edits.append((node.lineno, old, target))

    for lineno, old, new in edits:
        idx = lineno - 1
        if 0 <= idx < len(lines):
            # Replace only the `from <old> import` head on this line.
            lines[idx] = lines[idx].replace(f"from {old} import", f"from {new} import", 1)

    return "".join(lines), warnings
