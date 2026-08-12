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


# ── 4. Dependency manifest sanity ────────────────────────────────────────────
#
# requirements.txt listed `csv` — a stdlib module, not a PyPI package — so
# `pip install -r requirements.txt` failed outright. Nothing downstream could
# run, which makes this the single most blocking defect in the whole set. The
# generator even knew something was odd and shipped it anyway, annotated
# "csv  # unpinned: not in known-good map, verify version".
#
# The stdlib direction is exact and free: sys.stdlib_module_names is the
# running interpreter's own list. No curated list to drift.

_REQUIREMENT_NAME = re.compile(r"^\s*([A-Za-z0-9._-]+)")


def _requirement_names(manifest: str) -> list:
    """(name, line) for each real requirement line."""
    out = []
    for n, raw in enumerate((manifest or "").splitlines(), start=1):
        line = raw.split("#", 1)[0].strip()
        if not line or line.startswith("-"):      # -r, -e, --index-url
            continue
        match = _REQUIREMENT_NAME.match(line)
        if match:
            out.append((match.group(1), n))
    return out


def _normalise(name: str) -> str:
    """PEP 503-ish: case- and separator-insensitive."""
    return re.sub(r"[-_.]+", "_", (name or "").strip().lower())


# Distributions whose IMPORT name differs from their PyPI name. Needed only for
# the "imported but not declared" direction; the stdlib direction needs none.
#
# ponytail: deliberately short and one-directional. The complete mapping only
# exists in each distribution's installed metadata, which we do not have at
# check time, so this is scored to avoid FALSE POSITIVES — an unrecognised
# import is reported, and every entry here is a case observed in a generated
# manifest. A missed one is a false negative the install rung still catches.
IMPORT_TO_DISTRIBUTION = {
    "jose": "python-jose", "jwt": "pyjwt", "multipart": "python-multipart",
    "dotenv": "python-dotenv", "psycopg2": "psycopg2-binary",
    "email_validator": "email-validator", "yaml": "pyyaml",
    "sqlalchemy": "sqlalchemy", "dateutil": "python-dateutil",
    "bs4": "beautifulsoup4", "PIL": "pillow", "cv2": "opencv-python",
    "sklearn": "scikit-learn", "google": "google-api-python-client",
    "attr": "attrs", "pkg_resources": "setuptools", "magic": "python-filetype",
}


def check_python_manifest(files: dict, manifest_path="backend/requirements.txt") -> dict:
    """Two directions over requirements.txt: entries that are not installable,
    and third-party imports that are not declared."""
    manifest = (files or {}).get(manifest_path)
    if manifest is None:
        return {}

    results = {}
    declared = {}
    for name, line in _requirement_names(manifest):
        base = _normalise(name.split("[", 1)[0])
        declared[base] = line
        if base in _STDLIB:
            results.setdefault(manifest_path, []).append(SyntaxIssue(
                manifest_path, line=line, kind="manifest",
                message=(f"'{name}' is a Python standard-library module, not a "
                         f"PyPI package. `pip install -r {PurePosixPath(manifest_path).name}` "
                         f"fails on this line, so nothing else installs. "
                         f"Remove it — `import {name}` needs no dependency.")))

    # Direction two: imported, never declared.
    root = str(PurePosixPath(manifest_path).parent)
    local = _local_python_roots(files, root)
    seen = {}
    for path, content in (files or {}).items():
        if PurePosixPath(path).suffix.lower() not in PY_EXTS or not content:
            continue
        if root and not path.startswith(root + "/"):
            continue
        for module, line in _python_imports(content):
            top = module.split(".", 1)[0]
            if not top or top in _STDLIB or top in local:
                continue
            candidate = _normalise(IMPORT_TO_DISTRIBUTION.get(top, top))
            if candidate in declared:
                continue
            seen.setdefault(top, (path, line))

    for top, (path, line) in sorted(seen.items()):
        results.setdefault(path, []).append(SyntaxIssue(
            path, line=line, kind="manifest",
            message=(f"imports '{top}', which is not declared in "
                     f"{manifest_path}. It installs on a developer machine that "
                     f"happens to have it and fails everywhere else.")))

    _check_usage_dependencies(files, root, declared, manifest_path, results)
    return results


# Dependencies pulled in by USAGE, not by an import statement — so no amount of
# import scanning finds them. All three were missing from the CRM manifest and
# all three are in the hand-repaired one.
#
#   EmailStr                   pydantic imports email-validator to build it
#   OAuth2PasswordRequestForm  fastapi needs python-multipart to parse the form
#   passlib bcrypt handler     passlib[bcrypt] does not itself install bcrypt
#
# ponytail: three rules, not a framework model. Each is a symbol that appears
# verbatim in the source and a distribution that must appear in the manifest.
# The boot rung would also catch these, but only once egress works, and this
# names the file, the line and the fix for free.
USAGE_REQUIRES = (
    ("EmailStr", "email-validator",
     "pydantic validates EmailStr through email-validator"),
    ("OAuth2PasswordRequestForm", "python-multipart",
     "FastAPI parses form bodies through python-multipart"),
    ("CryptContext", "bcrypt",
     "passlib's bcrypt handler needs the bcrypt package at runtime"),
)


def _check_usage_dependencies(files, root, declared, manifest_path, results):
    for symbol, distribution, why in USAGE_REQUIRES:
        if _normalise(distribution) in declared:
            continue
        where = _first_use(files, root, symbol)
        if where is None:
            continue
        path, line = where
        results.setdefault(path, []).append(SyntaxIssue(
            path, line=line, kind="manifest",
            message=(f"uses {symbol}, so '{distribution}' must be in "
                     f"{manifest_path} — {why}. Nothing imports it by name, so "
                     f"this fails at runtime, not at install.")))


def _first_use(files, root, symbol):
    for path in sorted(files or {}):
        if PurePosixPath(path).suffix.lower() not in PY_EXTS:
            continue
        if root and not path.startswith(root + "/"):
            continue
        for n, line in enumerate((files[path] or "").splitlines(), start=1):
            if re.search(rf"\b{re.escape(symbol)}\b", line):
                return path, n
    return None


# ── 5. Route registration: uniqueness, prefixes, ordering ────────────────────
#
# Three defects the CRM shipped, none of them visible in any single file:
#
#   1. main.py included all nine endpoint routers DIRECTLY and also included
#      api.py's router, which includes the same nine. Every route existed twice.
#   2. auth.py declares APIRouter(prefix="/auth") and api.py includes it with
#      prefix="/auth" again, so the real path was /api/v1/auth/auth/register.
#   3. contacts.py declares GET "/{contact_id}" at line 26 and GET "/search" at
#      line 74. FastAPI matches in declaration order, so /contacts/search parsed
#      "search" as a contact_id and returned 422 forever.
#
# This is also the static route table the frontend-contract check needs, and
# when the boot rung succeeds it is cross-checked against the real
# /openapi.json — one mechanism, three uses.

_HTTP_METHODS = ("get", "post", "put", "patch", "delete", "head", "options")


def _router_prefix(tree) -> str:
    """The prefix of the module-level `router = APIRouter(prefix=...)`."""
    for node in ast.walk(tree):
        if not isinstance(node, ast.Assign):
            continue
        call = node.value
        if not (isinstance(call, ast.Call) and _called_name(call) == "APIRouter"):
            continue
        for kw in call.keywords:
            if kw.arg == "prefix" and isinstance(kw.value, ast.Constant):
                return str(kw.value.value or "")
    return ""


def _module_of(path: str, root: str) -> str:
    """backend/app/api/v1/endpoints/auth.py -> app.api.v1.endpoints.auth"""
    rel = path[len(root) + 1:] if root and path.startswith(root + "/") else path
    return rel[:-3].replace("/", ".") if rel.endswith(".py") else rel.replace("/", ".")


def _include_router_calls(tree, imports):
    """(target module, prefix, line) for each include_router call."""
    for node in ast.walk(tree):
        if not (isinstance(node, ast.Call) and _called_name(node) == "include_router"):
            continue
        if not node.args:
            continue
        target = node.args[0]
        # `auth.router` -> the module `auth`; `some_router` -> an alias.
        if isinstance(target, ast.Attribute) and isinstance(target.value, ast.Name):
            name = target.value.id
        elif isinstance(target, ast.Name):
            name = target.id
        else:
            continue
        prefix = ""
        for kw in node.keywords:
            if kw.arg == "prefix" and isinstance(kw.value, ast.Constant):
                prefix = str(kw.value.value or "")
        yield imports.get(name, name), prefix, node.lineno


def _imported_modules(tree) -> dict:
    """local name -> dotted module it refers to, for both import styles:
        from app.api.v1.endpoints import auth        -> auth  -> ...endpoints.auth
        from app.api.v1.endpoints.auth import router as a -> a -> ...endpoints.auth
    """
    out = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module:
            for alias in node.names:
                local = alias.asname or alias.name
                if alias.name == "router" or alias.asname:
                    out[local] = node.module
                else:
                    out[local] = f"{node.module}.{alias.name}"
        elif isinstance(node, ast.Import):
            for alias in node.names:
                out[alias.asname or alias.name.split(".")[0]] = alias.name
    return out


def check_route_registration(files: dict, root="backend") -> dict:
    """Duplicate registrations, doubled prefixes, and shadowed static routes."""
    results = {}
    modules = {}          # dotted module -> (path, tree, own prefix)
    for path, content in (files or {}).items():
        if PurePosixPath(path).suffix.lower() not in PY_EXTS or not content:
            continue
        if root and not path.startswith(root + "/"):
            continue
        try:
            tree = ast.parse(content)
        except SyntaxError:
            continue
        modules[_module_of(path, root)] = (path, tree, _router_prefix(tree))

    includers = {}        # included module -> [(includer path, prefix, line)]
    for dotted, (path, tree, _) in modules.items():
        for target, prefix, line in _include_router_calls(tree, _imported_modules(tree)):
            includers.setdefault(target, []).append((path, prefix, line))

    for target, sites in sorted(includers.items()):
        entry = modules.get(target)
        # Doubled prefix: the module's own APIRouter prefix repeated by the
        # includer. Real path becomes /auth/auth/... .
        if entry:
            own = entry[2]
            for path, prefix, line in sites:
                if own and prefix and prefix.rstrip("/") == own.rstrip("/"):
                    results.setdefault(path, []).append(SyntaxIssue(
                        path, line=line, kind="route",
                        message=(f"includes {target} with prefix='{prefix}', but that "
                                 f"router already declares prefix='{own}'. Every path "
                                 f"under it becomes '{prefix}{own}/...'. Remove one.")))
        if len(sites) > 1:
            where = ", ".join(f"{p}:{ln}" for p, _, ln in sites)
            for path, _, line in sites:
                results.setdefault(path, []).append(SyntaxIssue(
                    path, line=line, kind="route",
                    message=(f"router '{target}' is registered {len(sites)} times "
                             f"({where}). Each registration mounts the same routes at "
                             f"a different path — include it once.")))

    for path, (_, tree, _prefix) in ((p, m) for p, m in
                                     ((v[0], (dotted, v[1], v[2]))
                                      for dotted, v in modules.items())):
        for issue in _shadowed_routes(path, tree):
            results.setdefault(path, []).append(issue)
    return results


def _shadowed_routes(path: str, tree) -> list:
    """A static segment declared AFTER a dynamic one that swallows it.

    FastAPI (and Flask, and Express) match in declaration order, so
    GET /{contact_id} declared first means GET /search is never reached and
    "search" is parsed as an id.
    """
    routes = []          # (method, template, line)
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        for dec in node.decorator_list:
            if not (isinstance(dec, ast.Call) and isinstance(dec.func, ast.Attribute)):
                continue
            method = dec.func.attr.lower()
            if method not in _HTTP_METHODS or not dec.args:
                continue
            arg = dec.args[0]
            if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
                routes.append((method, arg.value, dec.lineno))
    routes.sort(key=lambda r: r[2])

    issues = []
    for i, (method, template, line) in enumerate(routes):
        if "{" in template:
            continue                            # this one IS the dynamic route
        for earlier_method, earlier, earlier_line in routes[:i]:
            if earlier_method == method and _shadows(earlier, template):
                issues.append(SyntaxIssue(
                    path, line=line, kind="route",
                    message=(f"{method.upper()} '{template}' is declared after "
                             f"'{earlier}' (line {earlier_line}), which matches it "
                             f"first — routes are matched in declaration order, so "
                             f"'{template.strip('/')}' is parsed as a path parameter "
                             f"and this handler never runs. Move it above.")))
                break
    return issues


def _shadows(dynamic: str, static: str) -> bool:
    """Would `dynamic` (e.g. /{contact_id}) match `static` (e.g. /search)?"""
    d = [s for s in dynamic.strip("/").split("/") if s]
    s = [s for s in static.strip("/").split("/") if s]
    if len(d) != len(s) or not d:
        return False
    return all(a == b or (a.startswith("{") and a.endswith("}")) for a, b in zip(d, s)) \
        and any(a.startswith("{") for a in d)


_STDLIB = frozenset(getattr(sys, "stdlib_module_names", ())) | {
    # sys.stdlib_module_names exists from 3.10. On an older interpreter the
    # frozenset is empty, which would make the stdlib check silently do nothing
    # — so the entries actually observed in generated manifests are named here
    # as a floor, not as the list.
    "csv", "json", "os", "sys", "datetime", "typing", "logging", "pathlib",
    "sqlite3", "asyncio", "uuid", "hashlib", "secrets", "io", "re", "time",
    "enum", "abc", "functools", "itertools", "collections", "dataclasses",
    "subprocess", "tempfile", "shutil", "base64", "math", "random", "string",
    "smtplib", "email", "http", "urllib", "socket", "threading", "decimal",
}


def _python_imports(content: str):
    """(dotted module, line) for every import in a Python file."""
    try:
        tree = ast.parse(content)
    except SyntaxError:
        return
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                yield alias.name, node.lineno
        elif isinstance(node, ast.ImportFrom):
            # `from . import x` / `from .y import z` are relative — local by
            # construction, never a distribution.
            if node.level == 0 and node.module:
                yield node.module, node.lineno


def _local_python_roots(files: dict, root: str) -> set:
    """Top-level package/module names that live in this project, so `app.core`
    is never mistaken for a missing dependency."""
    local = set()
    prefix = (root + "/") if root else ""
    for path in files or {}:
        if not path.startswith(prefix):
            continue
        rest = path[len(prefix):]
        head = rest.split("/", 1)[0]
        if head.endswith(".py"):
            local.add(head[:-3])
        elif head and "." not in head:
            local.add(head)
    return local
