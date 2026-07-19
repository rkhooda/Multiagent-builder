"""Real parsers for generated files — Day 22.

Replaces the Day 18 brace-counting heuristics with actual syntax checks:
Python via stdlib `ast`/`compile` (in-process, free, exact line/col), JS/JSX via
a batched `@babel/parser` subprocess (see scripts/js_syntax_check.mjs).

The asymmetry that shapes this module: **parsing is free, repairing is not.**
Every check here is deterministic and costs nothing, so we run them on
everything, always. The expensive judgement — whether to spend an OpenRouter
call fixing what we found — lives in the caller under an explicit budget.

That asymmetry also picks the parser. A false positive does not merely produce a
bad report; it buys a paid repair of an already-correct file. `@babel/parser` is
the most tolerant of the JSX-capable parsers, and tolerance is the cheap
direction to be wrong in. (Plain `acorn` cannot parse JSX at all — every
generated React component would false-positive. That trap is what
`test_validation.py::valid JSX` exists to keep shut.)

Issues are small serialisable dataclasses so one shape crosses every boundary:
the node checker's JSON, the aggregated validation_report, the QA agent's
context, and the Gate 4 breakdown popover. No translation layers.
"""
import ast
import json
import os
import shutil
import subprocess
from dataclasses import dataclass, asdict, field
from pathlib import Path, PurePosixPath

SCRIPTS_DIR = Path(__file__).resolve().parents[2] / "scripts"
JS_CHECKER = SCRIPTS_DIR / "js_syntax_check.mjs"

JS_EXTS = {".js", ".jsx", ".mjs", ".ts", ".tsx"}
PY_EXTS = {".py"}

# How long the batched node process gets. It parses, it does not execute, so
# even a large project is sub-second; this only guards a hung/zombie process.
JS_TIMEOUT_SECONDS = 60


@dataclass
class SyntaxIssue:
    """One parser-reported defect in one file."""
    filepath: str
    line: int = 0
    col: int = 0
    message: str = ""
    kind: str = "syntax"  # syntax | phantom_import | missing_package | artifact

    def as_dict(self):
        return asdict(self)

    def describe(self):
        """One-line human form, reused in repair prompts and the QA report."""
        where = f" at line {self.line}" if self.line else ""
        return f"{self.filepath}{where}: {self.message}"


# ── Python ───────────────────────────────────────────────────────────────────

def validate_python(content: str, filepath: str) -> list:
    """Parse `content` as Python. Returns [] when it parses cleanly.

    Two passes because they catch different things: `ast.parse` covers grammar,
    while `compile(..., 'exec')` additionally runs the post-parse checks CPython
    defers (duplicate function parameters, `return` outside a function, some
    assignment-target errors). Both are in-process — no temp files, no
    subprocess, microseconds — which is why this can run at write time inside a
    parallel worker without adding latency.
    """
    if not content.strip():
        return [SyntaxIssue(filepath, message="file is empty")]

    for parse in (lambda: ast.parse(content, filename=filepath),
                  lambda: compile(content, filepath, "exec")):
        try:
            parse()
        except SyntaxError as e:
            return [SyntaxIssue(
                filepath=filepath,
                line=e.lineno or 0,
                col=e.offset or 0,
                message=(e.msg or "invalid syntax"),
            )]
        except ValueError as e:
            # e.g. source containing null bytes — compile raises ValueError.
            return [SyntaxIssue(filepath, message=f"cannot compile: {e}")]
    return []


# ── JS / JSX ─────────────────────────────────────────────────────────────────

class JsToolUnavailable(RuntimeError):
    """node or the parser package is missing — caller degrades, never crashes."""


def js_tool_status() -> str:
    """'' when the batch checker is usable, else a human reason string."""
    if not shutil.which("node"):
        return "node executable not found on PATH"
    if not JS_CHECKER.exists():
        return f"checker script missing at {JS_CHECKER}"
    if not (SCRIPTS_DIR / "node_modules" / "@babel" / "parser").exists():
        return "@babel/parser not installed (run: npm ci --prefix backend/scripts)"
    return ""


def validate_js_batch(files: dict) -> dict:
    """Parse many JS/JSX/TS files in ONE node process.

    `files` is {filepath: content}; returns {filepath: [SyntaxIssue]} for the
    JS-ish files only. Non-JS paths are ignored (not an error).

    ONE process per batch, never per file: spawning node ~20x inside three
    parallel workers is the latency trap this signature exists to prevent.
    Files go in as stdin JSON and come back as stdout JSON.

    Raises JsToolUnavailable if node/@babel/parser is absent so the caller can
    fall back loudly rather than silently reporting "no syntax errors" — a
    validator that quietly reports success when it did not run is worse than no
    validator at all.
    """
    targets = {p: c for p, c in (files or {}).items()
               if Path(p).suffix.lower() in JS_EXTS}
    if not targets:
        return {}

    reason = js_tool_status()
    if reason:
        raise JsToolUnavailable(reason)

    try:
        proc = subprocess.run(
            ["node", str(JS_CHECKER)],
            input=json.dumps({"files": targets}),
            capture_output=True, text=True, timeout=JS_TIMEOUT_SECONDS,
        )
    except (subprocess.TimeoutExpired, OSError) as e:
        raise JsToolUnavailable(f"js checker failed to run: {e}") from e

    if proc.returncode != 0:
        raise JsToolUnavailable(
            f"js checker exited {proc.returncode}: {(proc.stderr or '').strip()[:300]}")

    try:
        payload = json.loads(proc.stdout)
    except json.JSONDecodeError as e:
        raise JsToolUnavailable(f"js checker returned non-JSON: {e}") from e

    return {
        path: [SyntaxIssue(filepath=path, line=i.get("line", 0), col=i.get("col", 0),
                           message=i.get("message", "syntax error"))
               for i in issues]
        for path, issues in (payload.get("results") or {}).items() if issues
    }


def js_syntax_heuristic(content: str, filepath: str) -> list:
    """Degraded fallback when node/@babel is unavailable — the Day 18 brace
    count, kept ONLY for this path.

    ponytail: naive about braces inside strings/comments/regex, so it is a hint
    and never triggers a paid repair. The real parser is validate_js_batch.
    """
    if Path(filepath).suffix.lower() not in JS_EXTS or not content.strip():
        return []
    issues = []
    if content.count("{") != content.count("}"):
        issues.append("unbalanced braces")
    if content.count("(") != content.count(")"):
        issues.append("unbalanced parens")
    if "import " not in content and "export " not in content:
        issues.append("no import or export statement — likely truncated or prose")
    return [SyntaxIssue(filepath, message=m + " (heuristic — deep check unavailable)")
            for m in issues]


# ── JSON / YAML artifacts ────────────────────────────────────────────────────

def validate_artifact(content: str, filepath: str) -> list:
    """Parse .json / .yml / .yaml. Returns [] for anything else.

    These are small, high-value files (package.json, docker-compose.yml, CI
    configs) where a parse failure means the generated project does not even
    start — and where a repair is cheap because the file is tiny.
    """
    ext = Path(filepath).suffix.lower()
    if ext not in {".json", ".yml", ".yaml"} or not content.strip():
        return []

    if ext == ".json":
        try:
            json.loads(content)
        except json.JSONDecodeError as e:
            return [SyntaxIssue(filepath, line=e.lineno, col=e.colno,
                                message=f"invalid JSON: {e.msg}", kind="artifact")]
        return []

    import yaml
    try:
        yaml.safe_load(content)
    except yaml.YAMLError as e:
        mark = getattr(e, "problem_mark", None)
        return [SyntaxIssue(
            filepath,
            line=(mark.line + 1) if mark else 0,
            col=(mark.column + 1) if mark else 0,
            message=f"invalid YAML: {getattr(e, 'problem', None) or e}",
            kind="artifact")]
    return []


# ── Import / dependency resolution (FLAG-only) ───────────────────────────────

# Extension candidates a bundler tries for an extensionless relative specifier.
_JS_RESOLVE_ORDER = ("", ".js", ".jsx", ".ts", ".tsx",
                     "/index.js", "/index.jsx", "/index.ts", "/index.tsx")

_IMPORT_RE = None


def _import_specifiers(content: str):
    """Yield (specifier, line) for static imports/exports and bare requires.

    ponytail: regex, not an AST walk. The parse already happened in node and
    shipping its full AST back over stdout to re-walk in Python costs more than
    it buys for "list the module specifiers". Upgrade to AST output if we ever
    need scope-aware analysis.
    """
    global _IMPORT_RE
    if _IMPORT_RE is None:
        import re
        _IMPORT_RE = re.compile(
            r"""(?:\bfrom\s*|\bimport\s*\(?\s*|\brequire\s*\(\s*)['"]([^'"]+)['"]""")
    for m in _IMPORT_RE.finditer(content):
        yield m.group(1), content.count("\n", 0, m.start()) + 1


def validate_js_imports(files: dict, package_json: str = None) -> dict:
    """FLAG unresolvable JS imports. Never rewrites — mirrors the Day 19 policy.

    Two classes, both warnings:
      - relative `./x` / `../x` that resolves to no file in the generated tree
        -> phantom_import (a hallucinated module, or a file that was planned
        away). Auto-rewriting would paper over a real generation defect.
      - a bare specifier (`axios`) absent from package.json dependencies
        -> missing_package. This is the #1 cause of `npm install` succeeding and
        the app crashing on first import.

    Day 19's safe-fix analysis was Python-specific (dotted modules resolve
    unambiguously); JS specifiers are ambiguous across extensions and index
    files, so today JS gets flags only. Auto-fix stays a marked future
    extension of import_fixer.
    """
    tree = set(files or {})
    deps = set()
    if package_json:
        try:
            pkg = json.loads(package_json)
            for key in ("dependencies", "devDependencies", "peerDependencies"):
                deps.update(pkg.get(key) or {})
        except json.JSONDecodeError:
            deps = None  # unparseable package.json: reported separately, skip pkg checks
    else:
        deps = None

    results = {}
    for path, content in (files or {}).items():
        if Path(path).suffix.lower() not in JS_EXTS or not content:
            continue
        issues = []
        base = PurePosixPath(path).parent
        for spec, line in _import_specifiers(content):
            if spec.startswith("."):
                target = os.path.normpath(str(base / spec)).replace(os.sep, "/")
                if not any(f"{target}{ext}" in tree for ext in _JS_RESOLVE_ORDER):
                    issues.append(SyntaxIssue(
                        path, line=line, kind="phantom_import",
                        message=f"imports '{spec}' which resolves to no generated file"))
            elif deps is not None and not spec.startswith(("node:", "/")):
                # Scoped: @scope/name. Otherwise the package is the first segment
                # ('react-dom/client' -> 'react-dom').
                parts = spec.split("/")
                pkg_name = "/".join(parts[:2]) if spec.startswith("@") else parts[0]
                if pkg_name not in deps:
                    issues.append(SyntaxIssue(
                        path, line=line, kind="missing_package",
                        message=f"imports package '{pkg_name}' which is not in package.json dependencies"))
        if issues:
            results[path] = issues
    return results


def validate_content(content: str, filepath: str) -> list:
    """Language dispatch for the checks that are free and in-process.

    Python and JSON/YAML only — JS needs the batched subprocess and is handled
    by validate_js_batch post-phase.
    """
    ext = Path(filepath).suffix.lower()
    if ext in PY_EXTS:
        return validate_python(content, filepath)
    return validate_artifact(content, filepath)
