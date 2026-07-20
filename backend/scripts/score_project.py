"""Objective, re-runnable per-file quality score for a generated project — Day 25.

WHY THIS EXISTS (ponytail #1). The day's deliverable is a comparison of output
quality across three complexity tiers. "% of files usable" judged by eye is both
subjective and un-rerunnable: it drifts between projects, so the comparison that
is the whole point of the measurement becomes invalid, and re-checking a fix
means re-reading everything by hand.

WHAT IT DOES NOT DO. It does not re-implement any checking. Day 22 already ships
the parsers (validate_content / validate_js_batch / validate_js_imports); this
module contributes only two things they lack — a monotonic TIER ladder per file,
and an offline entry point that reads persisted output instead of running inside
the graph. Everything else is delegation.

ZERO API COST. Reads outputs/{project_id}/ from disk plus the LangGraph
checkpoint. Never calls a model. That is the property that makes "fix, re-score"
cheap and "re-generate to check a fix" the anti-pattern it should be.

THE TIER LADDER (monotonic — a file's tier is the highest rung it reaches):

  0 missing      planned but absent from disk, or present but empty
  1 present      non-empty file on disk
  2 syntax       parses (Python via ast/compile, JS/JSX via @babel/parser)
  3 imports      no phantom relative imports, no packages absent from manifest
  4 substantive  non-stub AND in the plan's file_list

USABLE = tier 4. Fixed here, once, deliberately, and applied identically to all
three projects — a rubric that moves between runs cannot measure degradation.
Tier 4 is the honest automatable line for "usable without manual edits": the file
exists, parses, its imports resolve against what was actually generated, it has
real content, and it is a file the plan asked for.

The fifth rung people reach for — "would plausibly run" — is deliberately NOT
automated. Deciding it requires executing an arbitrary generated stack. It is
recorded as a separate, sampled, manual judgement (--manual) so the automated
number stays reproducible and the subjective one stays visibly subjective.
"""
import argparse
import ast
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.validation.syntax import (  # noqa: E402
    JS_EXTS,
    JsToolUnavailable,
    js_tool_status,
    validate_content,
    validate_js_batch,
    validate_js_imports,
)

OUTPUTS_DIR = Path(__file__).resolve().parents[2] / "outputs"
ARCHIVE_DIRNAME = ".archived"

TIER_MISSING, TIER_PRESENT, TIER_SYNTAX, TIER_IMPORTS, TIER_SUBSTANTIVE = range(5)
TIER_NAMES = {
    TIER_MISSING: "missing",
    TIER_PRESENT: "present",
    TIER_SYNTAX: "syntax",
    TIER_IMPORTS: "imports",
    TIER_SUBSTANTIVE: "substantive",
}
USABLE_TIER = TIER_SUBSTANTIVE

# Text/config files get no import analysis and no stub check beyond emptiness —
# a 3-line .env.example is correct at 3 lines. They stop at "imports" unless
# planned, which keeps them from inflating or deflating the code score.
NON_CODE_EXTS = {".md", ".txt", ".env", ".example", ".gitignore", ".conf", ".cfg", ".toml", ".ini"}


def load_state(project_id: str) -> dict:
    """Persisted ProjectState, or {} when the checkpoint has nothing."""
    from app.graph.pipeline import graph

    try:
        return graph.get_state({"configurable": {"thread_id": project_id}}).values or {}
    except Exception as exc:  # a missing/corrupt thread must not kill the score
        print(f"[score] warning: could not load checkpoint for {project_id}: {exc}")
        return {}


def read_disk_files(project_id: str) -> dict:
    """{relative_path: content} for everything on disk, archives excluded."""
    root = OUTPUTS_DIR / project_id
    files = {}
    if not root.is_dir():
        return files
    for path in sorted(root.rglob("*")):
        if not path.is_file() or ARCHIVE_DIRNAME in path.parts:
            continue
        rel = path.relative_to(root).as_posix()
        try:
            files[rel] = path.read_text(encoding="utf-8", errors="replace")
        except OSError as exc:
            print(f"[score] warning: unreadable {rel}: {exc}")
    return files


def planned_files(state: dict) -> set:
    """Paths the pipeline said it would produce.

    file_list is the planning agent's own output and is authoritative. The plan
    JSON is parsed as a fallback for older runs that predate file_list.

    devops_files are unioned in because the devops agent generates AFTER planning
    and its outputs (Dockerfile, CI workflow, compose) are legitimately absent
    from file_list. Without this, every project is penalised for correctly
    producing its own deployment scaffolding — a rubric bug that would have
    depressed all three scores identically and silently.
    """
    planned = set(state.get("file_list") or [])
    if not planned:
        try:
            for task in json.loads(state.get("implementation_plan") or "[]"):
                if isinstance(task, dict) and task.get("filepath"):
                    planned.add(task["filepath"])
        except (json.JSONDecodeError, TypeError):
            pass
    return planned | set(state.get("devops_files") or {})


def is_stub(content: str, filepath: str) -> bool:
    """True when a file is a placeholder rather than an implementation.

    ponytail: heuristic, not semantic analysis. Deciding "is this real code"
    properly needs a model, which would make the rubric cost money and stop being
    deterministic — the two properties it exists to have. Python gets a real AST
    check; everything else falls back to a size floor. Upgrade only if the
    manual tier keeps disagreeing with it.
    """
    stripped = content.strip()
    if not stripped:
        return True

    ext = Path(filepath).suffix.lower()
    if ext == ".py":
        try:
            tree = ast.parse(content)
        except SyntaxError:
            return False  # already caught one rung down; not a stub question
        has_def = any(isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))
                      for n in ast.walk(tree))
        # Imports + a bare pass/docstring and nothing else is scaffolding. An
        # __init__.py legitimately looks like this, so it is exempt.
        if not has_def and Path(filepath).name != "__init__.py":
            substantive = [n for n in tree.body
                           if not isinstance(n, (ast.Import, ast.ImportFrom, ast.Pass, ast.Expr))]
            return not substantive
        return False

    # Non-Python: a file too small to be an implementation. Config/text files are
    # excluded from this check by the caller.
    #
    # The floor is deliberately LOW. A false "stub" verdict deflates the quality
    # score for a file that is merely small but correct, and a wrong score is
    # worse than a missed stub — the number is the deliverable. Same asymmetry
    # that picked the tolerant parser in Day 22: be wrong in the tolerant
    # direction. Placeholders measure ~18 chars ("// TODO: implement"); the
    # shortest plausibly-real component measures ~39, so 30 sits between them
    # with margin on both sides.
    return len(stripped) < 30


def score_project(project_id: str, verbose: bool = False) -> dict:
    state = load_state(project_id)
    disk = read_disk_files(project_id)
    planned = planned_files(state)

    tiers = {}
    reasons = {}

    # Rung 2 — syntax. JS goes through one batched node process, never per file.
    js_files = {p: c for p, c in disk.items() if Path(p).suffix.lower() in JS_EXTS}
    js_syntax = {}
    js_available = not js_tool_status()
    if js_files and js_available:
        try:
            js_syntax = validate_js_batch(js_files)
        except JsToolUnavailable as exc:
            print(f"[score] warning: JS checking unavailable ({exc}); JS files capped at 'present'")
            js_available = False
    elif js_files:
        print(f"[score] warning: {js_tool_status()}; JS files capped at 'present'")

    # Rung 3 — imports. package.json drives the bare-specifier check.
    js_imports = validate_js_imports(js_files, disk.get("package.json")) if js_files else {}

    for path, content in disk.items():
        ext = Path(path).suffix.lower()
        is_js = ext in JS_EXTS

        if not content.strip():
            tiers[path] = TIER_MISSING
            reasons[path] = "file is empty"
            continue

        tier = TIER_PRESENT

        if is_js and not js_available:
            # Honest ceiling: without the parser we cannot claim syntax validity.
            tiers[path] = tier
            reasons[path] = "JS parser unavailable — not scored above 'present'"
            continue

        syntax_issues = js_syntax.get(path, []) if is_js else validate_content(content, path)
        if syntax_issues:
            tiers[path] = tier
            reasons[path] = syntax_issues[0].describe()
            continue
        tier = TIER_SYNTAX

        import_issues = js_imports.get(path, [])
        if import_issues:
            tiers[path] = tier
            reasons[path] = import_issues[0].describe()
            continue
        tier = TIER_IMPORTS

        if ext in NON_CODE_EXTS:
            # Config/docs cannot be judged "substantive" on size; being planned
            # is the only signal that means anything for them.
            tiers[path] = TIER_SUBSTANTIVE if path in planned else tier
            reasons[path] = "" if path in planned else "not in plan file_list"
            continue

        if is_stub(content, path):
            tiers[path] = tier
            reasons[path] = "stub — no implementation found"
            continue

        if planned and path not in planned:
            tiers[path] = tier
            reasons[path] = "not in plan file_list (unplanned file)"
            continue

        tiers[path] = TIER_SUBSTANTIVE
        reasons[path] = ""

    # Planned but never written. These are the most damaging defect class — a
    # missing file is worse than a broken one, and only the plan reveals them.
    missing = sorted(planned - set(disk))
    for path in missing:
        tiers[path] = TIER_MISSING
        reasons[path] = "planned but never generated"

    scored = len(tiers)
    usable = sum(1 for t in tiers.values() if t >= USABLE_TIER)
    histogram = {name: 0 for name in TIER_NAMES.values()}
    for t in tiers.values():
        histogram[TIER_NAMES[t]] += 1

    result = {
        "project_id": project_id,
        "project_name": state.get("project_name", ""),
        "planned": len(planned),
        "generated": len(disk),
        "missing": len(missing),
        "scored": scored,
        "usable": usable,
        "usable_pct": round(100.0 * usable / scored, 1) if scored else 0.0,
        "tiers": histogram,
        "qa_issues_count": state.get("qa_issues_count", 0),
        "files": [
            {"path": p, "tier": TIER_NAMES[t], "reason": reasons.get(p, "")}
            for p, t in sorted(tiers.items(), key=lambda kv: (kv[1], kv[0]))
        ],
    }

    if verbose:
        print(f"\n=== {project_id} {state.get('project_name', '')} ===")
        print(f"planned {result['planned']} | generated {result['generated']} | "
              f"missing {result['missing']}")
        print(f"USABLE (tier>={TIER_NAMES[USABLE_TIER]}): {usable}/{scored} = {result['usable_pct']}%")
        print("tiers: " + "  ".join(f"{k}={v}" for k, v in histogram.items()))
        print()
        for f in result["files"]:
            if f["tier"] != TIER_NAMES[TIER_SUBSTANTIVE]:
                print(f"  [{f['tier']:>11}] {f['path']}  — {f['reason']}")

    return result


def main():
    ap = argparse.ArgumentParser(description="Score a generated project's files into quality tiers (zero API cost).")
    ap.add_argument("project_id", nargs="+", help="one or more project ids")
    ap.add_argument("--json", metavar="PATH", help="write full results as JSON")
    ap.add_argument("--row", action="store_true", help="print a markdown ledger row per project")
    args = ap.parse_args()

    results = [score_project(pid, verbose=True) for pid in args.project_id]

    if args.row:
        print("\n| project | planned | generated | missing | % usable | QA issues |")
        print("|---|---|---|---|---|---|")
        for r in results:
            print(f"| {r['project_name'] or r['project_id'][:8]} | {r['planned']} | "
                  f"{r['generated']} | {r['missing']} | {r['usable_pct']}% | {r['qa_issues_count']} |")

    if args.json:
        Path(args.json).write_text(json.dumps(results if len(results) > 1 else results[0], indent=2))
        print(f"\n[score] wrote {args.json}")


if __name__ == "__main__":
    main()
