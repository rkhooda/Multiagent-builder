"""Frontend Coder Agent — Phase 5 (Day 18 rebuild).

Per-task React file generation for a Vite + React + TailwindCSS + axios
project. Replaces the Day 12 minimal stub (single generic prompt, whole
architecture dumped as context, no ordering, no per-file isolation).

Pipeline per file: focused context (context_builder) -> validated LLM call
with one-shot repair (call_validated) -> shared write chain (strip + syntax
sanity + write) -> generated_files update -> file_written broadcast. One
file's unrecoverable failure is isolated and the loop continues; the stage
only fails as a whole when > 50% of its files fail. Sequential by design —
asyncio parallelism is Day 20, and this chain must prove correct here first.
"""
import json
from pathlib import Path

from app.exceptions import LLMError
from app.utils.file_writer import process_and_write_generated_file
from app.validation import call_validated
from .context_builder import build_file_context
from .utils import get_tasks_for_phase

SYSTEM_PROMPT = (Path(__file__).resolve().parents[3] / "prompts" / "frontend_coder_agent.md").read_text(encoding="utf-8")

# Fraction of files that must fail before the whole stage is considered broken.
STAGE_FAIL_THRESHOLD = 0.5


def _failure_stub(filepath: str, error: str) -> str:
    """Placeholder written when a file's generation fails, so the failure is
    visible in the Gate 4 file browser and fixable there via Request AI Fix
    (which reads from disk). Keeps dependent imports from breaking outright:
    JSX files export a null component, others an empty object."""
    note = (f"// Generation failed for {filepath}: {error[:160]}\n"
            f"// Placeholder — regenerate with \"Request AI Fix\" at the review gate.\n\n")
    if filepath.lower().endswith((".jsx", ".tsx")):
        return note + "export default function GenerationFailedPlaceholder() {\n  return null;\n}\n"
    return note + "export default {};\n"


def _is_lib_file(filepath: str) -> bool:
    """The API client / shared lib everything imports — generate these first."""
    low = filepath.lower()
    return "/lib/" in low or low.endswith("api.js") or low.endswith("api.jsx")


def topological_order(tasks: list) -> list:
    """Kahn's algorithm over intra-phase `requires` edges so a file is
    generated after everything it imports. Lib/api files break ties first
    (everything imports them). Cycles were blocked at Gate 3, but if one slips
    through we append the leftovers in a stable order rather than dropping them.
    """
    by_id = {t["id"]: t for t in tasks if t.get("id")}
    ids = set(by_id)
    # in-degree counts only dependencies that are themselves in this phase.
    indegree = {tid: 0 for tid in ids}
    dependents = {tid: [] for tid in ids}
    for tid, task in by_id.items():
        for req in task.get("requires", []) or []:
            if req in ids and req != tid:
                indegree[tid] += 1
                dependents[req].append(tid)

    def ready_key(tid):
        t = by_id[tid]
        return (not _is_lib_file(t.get("filepath", "")), t.get("filepath", ""))

    ready = sorted([tid for tid in ids if indegree[tid] == 0], key=ready_key)
    order = []
    while ready:
        tid = ready.pop(0)
        order.append(by_id[tid])
        for dep in dependents[tid]:
            indegree[dep] -= 1
            if indegree[dep] == 0:
                ready.append(dep)
        ready.sort(key=ready_key)

    if len(order) < len(ids):
        # Cycle or unmet edge — include the rest so nothing is silently skipped.
        placed = {t["id"] for t in order}
        leftovers = sorted((by_id[tid] for tid in ids if tid not in placed),
                           key=lambda t: t.get("filepath", ""))
        order.extend(leftovers)
    return order


def frontend_coder_agent(state: dict) -> dict:
    """Reads: implementation_plan, architecture_doc, tech_stack, file_list,
    generated_files. Writes: generated_files (merged), log, errors,
    current_stage. Raises LLMError only when > 50% of files fail (recoverable
    stage halt); anything under that is a recorded partial completion."""
    project_id = state.get("project_id", "")
    project_name = state.get("project_name", "Unknown Project")
    implementation_plan = state.get("implementation_plan", "[]")
    generated_files = dict(state.get("generated_files", {}))
    log = state.get("log", [])
    errors = state.get("errors", [])

    log.append("frontend_coder_agent: started")
    print(f"[FrontendCoder] Starting for project: {project_name}")

    # Parse the plan fresh every run — never a cached copy (Gate 3 may have edited it).
    fe_tasks = get_tasks_for_phase(implementation_plan, "frontend")
    if not fe_tasks:
        warning = "frontend_coder_agent: no frontend tasks in implementation plan"
        log.append(warning)
        print(f"[FrontendCoder] WARNING: {warning}")
        # Nothing to do — do NOT set _agent_event (no broadcast), let the
        # generic completion event fire so the UI still advances.
        return {"generated_files": generated_files, "log": log, "errors": errors,
                "current_stage": "backend_code"}

    ordered = topological_order(fe_tasks)
    total = len(ordered)
    log.append(f"frontend_coder_agent: {total} frontend tasks, generating in dependency order")
    print(f"[FrontendCoder] {total} tasks; first: {ordered[0].get('filepath')}")

    from ..core.connection_manager import manager

    files_ok = 0
    files_failed = 0
    failed_files = []

    for i, task in enumerate(ordered):
        task_id = task.get("id", f"fe_{i}")
        filepath = task.get("filepath", "")
        print(f"[FrontendCoder] ({i+1}/{total}) {filepath} [{task_id}]")

        try:
            context = build_file_context(task, state, phase_prefix="frontend/src")
            messages = [
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": context},
            ]
            raw = call_validated(
                messages, "frontend_code", state, max_tokens=1500,
                original_instruction="Output ONLY the file's code — no fences, no prose.",
                log=log,
            )
            result = process_and_write_generated_file(project_id, task, raw, state)
            if not result["success"]:
                raise RuntimeError(result["error"] or "write failed")

            # Keep state == disk: store the cleaned, header-free content.
            generated_files[filepath] = result["content"]
            files_ok += 1
            log.append(f"frontend_coder_agent: wrote {filepath} ({result['size_bytes']} bytes)")
            manager.broadcast_sync(project_id, {
                "type": "file_written",
                "filename": Path(filepath).name,
                "filepath": filepath,
                "phase": "frontend",
                "task_id": task_id,
                "index": i + 1,
                "total": total,
            })

        except Exception as e:
            # Per-file isolation: log, broadcast, and keep going. This survives
            # the Day 17 boundary's own repair attempt (call_validated already
            # tried once) — a genuine per-file failure, not a transient blip.
            files_failed += 1
            failed_files.append(filepath)
            msg = f"frontend_coder_agent: {filepath} failed after repair: {e}"
            errors.append(msg)
            print(f"[FrontendCoder] FAILED {filepath}: {e}")
            # Write a placeholder so the failed file is visible AND fixable at
            # Gate 4 (the file browser reads from disk), and dependent imports
            # still resolve. Kept in generated_files so state == disk.
            stub = _failure_stub(filepath, str(e))
            stub_result = process_and_write_generated_file(project_id, task, stub, state)
            if stub_result["success"]:
                generated_files[filepath] = stub_result["content"]
            manager.broadcast_sync(project_id, {
                "type": "file_error",
                "filename": Path(filepath).name,
                "filepath": filepath,
                "phase": "frontend",
                "task_id": task_id,
                "error": str(e)[:300],
                "index": i + 1,
                "total": total,
            })

    log.append(f"frontend_coder_agent: completed — {files_ok} ok, {files_failed} failed")
    print(f"[FrontendCoder] Done. {files_ok} ok, {files_failed} failed")

    # Stage-level failure only when the majority failed — a headless project.
    if total and files_failed / total > STAGE_FAIL_THRESHOLD:
        raise LLMError(
            f"frontend stage halted: {files_failed}/{total} files failed to "
            f"generate (>{int(STAGE_FAIL_THRESHOLD * 100)}%)",
            "frontend_code",
        )

    manager.broadcast_sync(project_id, {
        "type": "agent_complete",
        "agent": "frontend_code",
        "stage": "frontend_code",
        "output_preview": f"Generated {files_ok}/{total} frontend files",
        "files_ok": files_ok,
        "files_failed": files_failed,
    })

    result = {
        "generated_files": generated_files,
        "log": log,
        "errors": errors,
        "current_stage": "backend_code",
        "_agent_event": True,
    }
    # Honest partial-stage record: stage_node folds this into stage_history.
    if failed_files:
        result["partial_failures"] = failed_files
    return result
