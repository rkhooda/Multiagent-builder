"""Whole-project integration checks — the layer above parsing.

Written from docs/VERIFICATION_GAP_ANALYSIS.md, whose one-sentence finding is
the reason this module exists: **every file that shipped broken in project
e8935f86 parses.** A bare `notes` is a valid expression statement; a file cut
off mid-comment is valid in both Python and JS. `validation/syntax.py` asks
"does this parse", every defect answered yes, and the ZIP went out unable to
install, import, build or boot.

So this module never asks whether a file parses. It asks the two questions
above that:

  1. within one file, does every name resolve? (a linter — `ruff`)
  2. between files, do any two disagree? (imports, config keys, routes,
     manifests, config-file cross-references)

Everything here is deterministic and free: no LLM call, no network, no
container. It emits the same `SyntaxIssue` shape the rest of the pipeline
already carries, so findings reach `validation_report`, the QA context and the
Gate 4 panel with `file:line` and no translation layer.

ponytail: this is a set of functions over the already-in-memory `generated_files`
dict, called from the existing `validation_pass` node. No registry, no plugin
system, no new report, no new budget.
"""
import ast
import json
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path, PurePosixPath

from .syntax import PY_EXTS, SyntaxIssue

# ── 1. Linting: name resolution within one file ──────────────────────────────
#
# WHY an external linter and not our own ast walk. Answering "is this name
# defined here" properly means implementing Python's scope rules: builtins,
# comprehension scopes, walrus, global/nonlocal, star-imports, conditional
# definition, `del`. ruff already does it, in milliseconds, and is the
# ecosystem standard the generated project's own CI should be running anyway.
# Writing a scope analyser instead would be the static-analysis platform this
# work is explicitly not building.
#
# Linting is STATIC — it parses, it never executes — so it runs here in the
# builder rather than in the sandbox, and needs no egress. JS linting is the
# opposite: eslint needs the project's own config and node_modules, so it
# belongs to the sandbox lint rung after `npm install`.

# Deliberately narrow. These are the rules that mean "this file cannot run",
# not "this file could be tidier". A noisy panel gets ignored, and every rule
# here has to survive being shown to a human at Gate 4 next to a real defect.
#
#   F821 undefined name          <- schemas/tag.py used `datetime`, never
#                                   imported it; /openapi.json returned 500 and
#                                   every typed response broke.
#   F811 redefinition            <- the second definition silently wins
#   F822 undefined name in __all__
#   E902 IO/syntax error ruff could not even read
BLOCKING_LINT_RULES = ("F821", "F811", "F822", "E902")

# Reported, but as `lint_info` — never counted toward the quality threshold and
# never worth a paid repair. Measured on the CRM tree: 66 F401 findings against
# 4 real defects. An unused import is a true observation that is almost never
# the reason a project does not run, and a panel where the signal is 6% noise
# is a panel nobody reads.
INFO_LINT_RULES = ("F401",)

RUFF_TIMEOUT_SECONDS = 60


class RuffUnavailable(RuntimeError):
    """ruff is not installed or not runnable. Caller degrades loudly."""


def _ruff_binary() -> str:
    """The ruff beside the running interpreter, so a venv install is found
    without depending on PATH (the sandbox and the compose image differ)."""
    candidate = Path(sys.executable).parent / "ruff"
    return str(candidate) if candidate.exists() else "ruff"


def lint_python(files: dict) -> dict:
    """Run ruff over every generated Python file. Returns {path: [SyntaxIssue]}.

    Raises RuffUnavailable so the caller can record a degraded event rather
    than silently reporting a clean project — the failure mode this whole
    change exists to remove.
    """
    py = {p: c for p, c in (files or {}).items()
          if PurePosixPath(p).suffix.lower() in PY_EXTS and c}
    if not py:
        return {}

    rules = ",".join(BLOCKING_LINT_RULES + INFO_LINT_RULES)
    with tempfile.TemporaryDirectory(prefix="lint-") as tmp:
        root = Path(tmp)
        for path, content in py.items():
            dest = root / path
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_text(content, encoding="utf-8")
        try:
            proc = subprocess.run(
                [_ruff_binary(), "check", "--no-cache", "--isolated",
                 "--select", rules, "--output-format", "json", "."],
                capture_output=True, text=True, cwd=tmp,
                timeout=RUFF_TIMEOUT_SECONDS,
            )
        except (FileNotFoundError, OSError) as exc:
            raise RuffUnavailable(str(exc)) from exc
        except subprocess.TimeoutExpired as exc:
            raise RuffUnavailable(f"ruff timed out after {RUFF_TIMEOUT_SECONDS}s") from exc

        # ruff exits 1 when it finds violations — that is success, not failure.
        # A missing/!=0,1 exit with no parseable JSON is a tool problem.
        try:
            found = json.loads(proc.stdout or "[]")
        except json.JSONDecodeError as exc:
            raise RuffUnavailable(
                f"ruff returned unparseable output (exit {proc.returncode}): "
                f"{(proc.stderr or proc.stdout or '')[:200]}") from exc

    results = {}
    for item in found:
        rel = (item.get("filename") or "").replace(os.sep, "/")
        # ruff reports absolute paths; map back to the project-relative key.
        path = next((p for p in py if rel.endswith(p)), None)
        if path is None:
            continue
        code = item.get("code") or ""
        line = (item.get("location") or {}).get("row", 0) or 0
        message = item.get("message", "")
        if code == "F821" and _is_forward_reference(py[path], line, message):
            continue
        results.setdefault(path, []).append(SyntaxIssue(
            path, line=line,
            col=(item.get("location") or {}).get("column", 0) or 0,
            kind="lint_info" if code in INFO_LINT_RULES else "lint",
            message=f"{code}: {message}".strip(": "),
        ))
    return results


_F821_NAME = re.compile(r"Undefined name `([^`]+)`")


def _is_forward_reference(content: str, line: int, message: str) -> bool:
    """Is this F821 a QUOTED forward reference rather than a real undefined name?

    Settled against ground truth, not taste: the hand-repaired, verified-working
    CRM also writes `Mapped[List["Tag"]]` without importing Tag, because
    SQLAlchemy resolves string annotations through its registry at mapper
    configuration. Reporting those would have produced 16 findings on a file
    that works, and buried the 2 that matter.

    The distinction is exactly quoting:
        notes                     -> real, reported (the truncation defect)
        created_at: datetime      -> real, reported (the /openapi.json 500)
        Mapped[List["Tag"]]       -> quoted, skipped
    """
    match = _F821_NAME.search(message or "")
    if not match:
        return False
    name = match.group(1)
    lines = (content or "").splitlines()
    if not 1 <= line <= len(lines):
        return False
    return f'"{name}"' in lines[line - 1] or f"'{name}'" in lines[line - 1]


# ── 2. Truncation and degeneracy ─────────────────────────────────────────────
#
# Reported as their own kinds, distinct from `syntax`, because the REMEDY is
# different: a syntax error is repaired in place; a file the ceiling cut off is
# regenerated, and a file the model looped on is a generation failure. Paying
# repair tokens to complete a truncated file treats the symptom.
#
# The PRIMARY signal is not in this module at all — the provider already told
# us, via finish_reason == "length", and llm_router records it per call as
# `llm_truncated:{agent}`. What is here is the fallback for when that flag is
# unavailable (a cached response, a provider that under-reports, a file that
# arrived through a path that did not record it), plus the shape the flag
# cannot express: a file that stopped at a statement boundary and one that
# repeated itself until the ceiling.

# A file ending in an unterminated construct. Python and JS both allow a
# trailing comment, which is exactly how three of the CRM's truncated files
# passed the parser, so the comment case is judged by what precedes it.
_TRUNCATION_TAILS = (
    (re.compile(r",\s*$"), "ends on a trailing comma"),
    (re.compile(r"[({\[]\s*$"), "ends on an unclosed bracket"),
    # No `:` here. A trailing colon is how a YAML mapping key, a JSON-ish block
    # and a Python block header all legitimately end -- the CRM's own
    # docker-compose.yml ends on `postgres_data:` and is complete. Including it
    # cost one false positive on a correct file, which is the expensive
    # direction: a truncation finding sends the file back to be REGENERATED.
    (re.compile(r"(?:=|\+|\*|/|<|>|&|\|)\s*$"), "ends on a dangling operator"),
    (re.compile(r"\b(?:def|class|if|for|while|return|import|from|const|let|var|function)\s*$"),
     "ends on an incomplete statement"),
)


def _last_code_line(content: str) -> str:
    for line in reversed((content or "").splitlines()):
        if line.strip():
            return line.rstrip()
    return ""


def detect_truncation(files: dict, truncated_paths=None) -> dict:
    """Files that stop mid-thought. `truncated_paths` is the authoritative set
    from the provider's own finish_reason, passed in by the caller; the content
    heuristics below only ADD to it."""
    results = {}
    flagged = set(truncated_paths or ())

    for path, content in (files or {}).items():
        if not content:
            continue
        lines = content.splitlines()
        last = _last_code_line(content)
        reason = ""

        if path in flagged:
            reason = ("generation stopped at the model's output ceiling "
                      "(provider reported finish_reason=length)")
        else:
            for pattern, why in _TRUNCATION_TAILS:
                if pattern.search(last):
                    reason = why
                    break
            # A file whose final line is an unterminated comment AND which is
            # implausibly short for its type. ReminderForm.jsx was 7 lines
            # ending mid-word in a comment; a real 7-line file does not import
            # four modules and then stop.
            if not reason and _ends_mid_comment(last) and len(lines) < 15:
                reason = "ends inside a comment after only %d lines" % len(lines)

        if reason:
            results.setdefault(path, []).append(SyntaxIssue(
                path, line=len(lines), kind="truncated",
                message=f"file appears truncated — {reason}. Regenerate rather "
                        f"than repair: the cause is upstream of this file."))
    return results


def _ends_mid_comment(last: str) -> bool:
    stripped = last.strip()
    if not (stripped.startswith("#") or stripped.startswith("//")):
        return False
    # A finished comment sentence ends in punctuation or a closing bracket.
    return not stripped.rstrip().endswith((".", ":", ")", "]", "}", "!", "?"))


# A repetition loop is not a code smell, it is the generator failing. alembic.ini
# shipped 207 non-blank lines of which 23 were unique -- the same comment block
# nine times -- and then truncated before the [loggers] sections, so `alembic
# upgrade` died on KeyError.
DEGENERACY_MIN_LINES = 40
DEGENERACY_UNIQUE_RATIO = 0.35     # unique/total below this is a loop
DEGENERACY_MIN_BLOCK = 3           # consecutive lines that count as a "block"


def detect_degeneracy(files: dict) -> dict:
    """Files the model looped on. Distinct from truncation: the remedy is the
    same (regenerate) but the cause is not a ceiling, so raising one would only
    buy more repetition."""
    results = {}
    for path, content in (files or {}).items():
        lines = [ln.strip() for ln in (content or "").splitlines() if ln.strip()]
        if len(lines) < DEGENERACY_MIN_LINES:
            continue
        ratio = len(set(lines)) / len(lines)
        if ratio >= DEGENERACY_UNIQUE_RATIO:
            continue
        repeats = _worst_repeated_block(lines)
        if repeats < 2:
            continue
        results.setdefault(path, []).append(SyntaxIssue(
            path, line=1, kind="degenerate",
            message=(f"generation looped — {len(lines)} non-blank lines, only "
                     f"{len(set(lines))} unique, with a block repeated {repeats} "
                     f"times. This is a generation failure, not a style issue; "
                     f"regenerate the file.")))
    return results


def _worst_repeated_block(lines: list) -> int:
    """How many times the most-repeated N-line block occurs. Cheap proxy for
    'the model looped': counting single lines alone would flag legitimate
    repetition like a long list of similar imports."""
    counts = {}
    for i in range(len(lines) - DEGENERACY_MIN_BLOCK + 1):
        block = "\n".join(lines[i:i + DEGENERACY_MIN_BLOCK])
        counts[block] = counts.get(block, 0) + 1
    return max(counts.values(), default=0)


# ── 3. Import-time side effects ──────────────────────────────────────────────
#
# db/base_class.py opened a Postgres engine at import time. A dead module
# nothing used, which nonetheless made `import app.models` fail wherever the
# database was not already up.

_IO_AT_IMPORT = {
    "create_engine": "opens a database connection",
    "connect": "opens a connection",
    "urlopen": "makes a network request",
    "get": "makes a network request",
    "post": "makes a network request",
    "run": "runs a subprocess",
    "check_output": "runs a subprocess",
}


def detect_import_time_io(files: dict) -> dict:
    """Module-level calls that touch the world. Only module scope: the same
    call inside a function is how every one of these is meant to be used."""
    results = {}
    for path, content in (files or {}).items():
        if PurePosixPath(path).suffix.lower() not in PY_EXTS or not content:
            continue
        try:
            tree = ast.parse(content)
        except SyntaxError:
            continue                      # syntax.py already reports it
        for node in tree.body:            # module scope ONLY
            for call in _module_level_calls(node):
                name = _called_name(call)
                why = _IO_AT_IMPORT.get(name)
                if not why:
                    continue
                results.setdefault(path, []).append(SyntaxIssue(
                    path, line=getattr(call, "lineno", 0), kind="import_time_io",
                    message=(f"module-level {name}() {why} when this file is "
                             f"imported. Move it into a function or a lifespan "
                             f"handler — importing a module must not touch the "
                             f"network or the database.")))
    return results


def _module_level_calls(node):
    """Calls in a module-level statement, not descending into def/class."""
    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
        return
    for child in ast.walk(node):
        if isinstance(child, ast.Call):
            yield child


def _called_name(call) -> str:
    func = call.func
    if isinstance(func, ast.Name):
        return func.id
    if isinstance(func, ast.Attribute):
        return func.attr
    return ""
