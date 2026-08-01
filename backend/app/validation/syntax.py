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


def _resolve_relative(spec: str, from_path: str, tree: set):
    """The generated file a relative specifier points at, or None.

    Factored out of validate_js_imports so the composition check below resolves
    imports the SAME way rather than growing a second, subtly different resolver.
    """
    base = PurePosixPath(from_path).parent
    target = os.path.normpath(str(base / spec)).replace(os.sep, "/")
    for ext in _JS_RESOLVE_ORDER:
        if f"{target}{ext}" in tree:
            return f"{target}{ext}"
    return None


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
        for spec, line in _import_specifiers(content):
            if spec.startswith("."):
                if _resolve_relative(spec, path, tree) is None:
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


# ── Page composition coherence (Improvement 01, FLAG-only) ───────────────────
#
# The cheapest quality win of the whole change: it catches the main risk that
# decomposition introduces, costs ZERO API calls, and reuses the resolver and the
# Issue shape that already exist.
#
# Decomposition's real risk is not correctness, it is DRIFT and DISCONNECTION —
# five sections that each parse, each import real files, and are collectively not
# a page because the shell renders three of them. Every existing check passes on
# that project. Only this one fails.
#
# FLAG-only, no auto-fix, stated plainly: `import_fixer` is Python-only by
# construction (it returns any non-.py file untouched) and Day 22 deliberately
# left JS import rewriting undone because JS specifiers are ambiguous across
# extensions and index files. There is therefore no "safe class" of JS auto-fix
# to reuse here, and inventing one would be exactly the duplication this change
# is supposed to avoid. A missing section import is a real generation defect and
# belongs in the report, not silently patched.

_IMPORT_BINDING_RE = None


def _import_bindings(content: str):
    """Yield (specifier, default_name, {named}, line) for static ES imports.

    ponytail: regex over the source, like _import_specifiers above and for the
    same reason — the parse already happened in node, and shipping its AST back
    to re-walk in Python costs more than "which names came from where" is worth.
    Namespace imports (`import * as ns`) are yielded with no bindings, because
    `ns.Anything` cannot be checked without scope analysis.
    """
    global _IMPORT_BINDING_RE
    if _IMPORT_BINDING_RE is None:
        import re
        _IMPORT_BINDING_RE = re.compile(
            r"""\bimport\s+(?!type\b)([^;'"]*?)\s*from\s*['"]([^'"]+)['"]""")
    for m in _IMPORT_BINDING_RE.finditer(content):
        clause, spec = m.group(1).strip(), m.group(2)
        line = content.count("\n", 0, m.start()) + 1
        if clause.startswith("*"):
            yield spec, None, set(), line
            continue
        named = set()
        default = None
        brace = clause.find("{")
        if brace != -1:
            inner = clause[brace + 1:clause.find("}") if "}" in clause else len(clause)]
            for part in inner.split(","):
                part = part.strip()
                if not part:
                    continue
                # `Foo as Bar` — the EXPORTED name is what must exist.
                named.add(part.split(" as ")[0].strip())
            head = clause[:brace].rstrip().rstrip(",").strip()
        else:
            head = clause
        if head and head.replace("$", "").replace("_", "").isalnum():
            default = head
        yield spec, default, named, line


_EXPORT_NAME_RE = None


def _exported_names(content: str):
    """(has_default, {named exports}) for a JS/JSX module.

    Deliberately dumb and TOLERANT: an unrecognised export form yields nothing,
    which makes this check silent rather than wrong. Same asymmetry that picked
    the tolerant parser on Day 22 — a false "missing export" warning sends the
    user hunting a defect that is not there, which is worse than a missed one.
    """
    global _EXPORT_NAME_RE
    if _EXPORT_NAME_RE is None:
        import re
        _EXPORT_NAME_RE = re.compile(
            r"^\s*export\s+(?:(default)\b|"
            r"(?:async\s+)?(?:function|class|const|let|var)\s+([A-Za-z_$][\w$]*)|"
            r"\{([^}]*)\})", re.MULTILINE)
    has_default, named = False, set()
    for m in _EXPORT_NAME_RE.finditer(content or ""):
        if m.group(1):
            has_default = True
        elif m.group(2):
            named.add(m.group(2))
        elif m.group(3):
            for part in m.group(3).split(","):
                part = part.strip()
                if not part:
                    continue
                # `foo as default` re-exports a default; `foo as bar` exports bar.
                alias = part.split(" as ")[-1].strip() if " as " in part else part
                if alias == "default":
                    has_default = True
                else:
                    named.add(alias)
    return has_default, named


def validate_page_composition(files: dict, plan_tasks: list) -> dict:
    """Deterministic composition checks over a decomposed frontend. Zero API cost.

    Returns {filepath: [SyntaxIssue(kind='coherence_warning')]}. Returns {} when
    nothing was decomposed, so an undecomposed run pays literally nothing.

    Four questions, in the order a broken page actually fails:
      1. does each page shell import every section it owns?
      2. do the names it imports match what those sections really export?
      3. is any generated section imported by nothing at all?
      4. do sections import primitives that exist?  <- NOT re-implemented here:
         validate_js_imports already answers it for every relative import in the
         project, and a second copy would drift from the first.
    """
    sections_of, by_id = {}, {}
    for task in plan_tasks or []:
        if not isinstance(task, dict) or not task.get("id"):
            continue
        by_id[task["id"]] = task
        if task.get("section_of"):
            sections_of.setdefault(task["section_of"], []).append(task)
    if not sections_of:
        return {}

    tree = set(files or {})
    results = {}

    def add(path, message, line=0):
        results.setdefault(path, []).append(
            SyntaxIssue(path, line=line, kind="coherence_warning", message=message))

    # Who imports what, resolved once for the whole tree.
    imported_by = {}
    bindings_of = {}
    for path, content in (files or {}).items():
        if Path(path).suffix.lower() not in JS_EXTS or not content:
            continue
        for spec, default, named, line in _import_bindings(content):
            if not spec.startswith("."):
                continue
            target = _resolve_relative(spec, path, tree)
            if target is None:
                continue        # already reported as phantom_import; not ours
            imported_by.setdefault(target, set()).add(path)
            bindings_of.setdefault((path, target), []).append((default, named, line))

    for shell_id, sections in sorted(sections_of.items()):
        shell = by_id.get(shell_id)
        if not shell:
            continue            # already reported by the plan validator
        shell_path = shell.get("filepath", "")
        shell_content = (files or {}).get(shell_path)
        if not shell_content:
            continue            # shell never generated; the stub already says so

        for section in sorted(sections, key=lambda s: s.get("filepath", "")):
            section_path = section.get("filepath", "")
            if section_path not in tree:
                continue        # never generated; counted as missing, not incoherent

            # 1. the shell must actually compose the section it owns
            binds = bindings_of.get((shell_path, section_path))
            if not binds:
                add(shell_path,
                    f"page shell does not import its section {section_path} "
                    f"({section.get('id')}) — the section is generated but never rendered")
                continue

            # 2. the names it imports must exist in the section
            has_default, named = _exported_names(files[section_path])
            for default_name, named_imports, line in binds:
                if default_name and not has_default:
                    add(shell_path,
                        f"imports {default_name} as the default export of {section_path}, "
                        f"which has no default export", line)
                missing = sorted(n for n in named_imports if n not in named)
                # Silent only when extraction recognised NOTHING in the module —
                # then the failure is ours, not the code's. A module with a
                # default export and no named ones is a real finding: importing
                # `{ Hero }` from it parses, resolves, and renders undefined.
                if missing and (named or has_default):
                    add(shell_path,
                        f"imports {missing} from {section_path}, which exports "
                        f"{sorted(named) if named else 'only a default'}", line)

    # 3. a generated section nothing imports
    for sections in sections_of.values():
        for section in sections:
            path = section.get("filepath", "")
            if path in tree and not imported_by.get(path):
                add(path, f"section {section.get('id')} is generated but imported by "
                          f"no file — nothing renders it")
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
