"""Frontend Coder Agent — Phase 5 (Day 18 rebuild, Day 20 parallelised).

Per-task React file generation for a Vite + React + TailwindCSS + axios project.
Replaces the Day 12 minimal stub (single generic prompt, whole architecture
dumped as context, no ordering, no per-file isolation).

Day 20: the sequential loop is gone — file generation fans out through the
shared parallel scheduler (parallel_runner.run_phase). This agent only supplies
the coder-specific callbacks (build context, generate one file, stub a failure)
and the frontend dependency shape (every non-lib file implicitly depends on the
shared API client). Ordering, concurrency limits, per-file failure isolation,
transitive blocking, and live progress broadcasts all live in the scheduler.
The stage still fails as a whole only when > 50% of its files don't deliver.
"""
from pathlib import Path

from app.exceptions import LLMError
from app.utils.file_writer import process_generated_file
from app.validation import call_validated, syntax_of
from .context_builder import build_file_context
from .parallel_runner import comment_safe, run_phase
from .utils import get_tasks_for_phase

SYSTEM_PROMPT = (Path(__file__).resolve().parents[3] / "prompts" / "frontend_coder_agent.md").read_text(encoding="utf-8")

# Fraction of files that must not-deliver (failed + blocked) before the whole
# stage is considered broken.
STAGE_FAIL_THRESHOLD = 0.5


def _failure_stub(filepath: str, error: str) -> str:
    """Placeholder written when a file's generation fails or is blocked, so the
    failure is visible in the Gate 4 file browser and fixable there via Request
    AI Fix (which reads from disk). Keeps dependent imports from breaking
    outright: JSX files export a null component, others an empty object."""
    note = (f"// Generation failed for {filepath}: {comment_safe(error)}\n"
            f"// Placeholder — regenerate with \"Request AI Fix\" at the review gate.\n\n")
    if filepath.lower().endswith((".jsx", ".tsx")):
        return note + "export default function GenerationFailedPlaceholder() {\n  return null;\n}\n"
    return note + "export default {};\n"


def _is_lib_file(filepath: str) -> bool:
    """The API client / shared lib everything imports — generate these first."""
    low = filepath.lower()
    return "/lib/" in low or low.endswith("api.js") or low.endswith("api.jsx")


def frontend_coder_agent(state: dict) -> dict:
    """Reads: implementation_plan, architecture_doc, tech_stack, file_list,
    generated_files. Writes: generated_files (merged), log, errors,
    current_stage. Raises LLMError only when > 50% of files fail to deliver
    (recoverable stage halt); anything under that is a recorded partial."""
    project_id = state.get("project_id", "")
    project_name = state.get("project_name", "Unknown Project")
    implementation_plan = state.get("implementation_plan", "[]")

    # Point state at a live working dict so the coordinator's commits and the
    # launch-time context builder share one view (single-threaded on the loop).
    generated_files = dict(state.get("generated_files", {}))
    state["generated_files"] = generated_files
    log = state.setdefault("log", [])
    errors = state.setdefault("errors", [])

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

    file_tree = state.get("file_list") or list(generated_files.keys())
    lib_ids = {t["id"] for t in fe_tasks if t.get("id") and _is_lib_file(t.get("filepath", ""))}

    def implicit_deps(task, by_id):
        # Every non-lib file waits on the shared API client(s): a component that
        # imports the client must not generate before it exists.
        return [] if _is_lib_file(task.get("filepath", "")) else list(lib_ids)

    def build_context(task, st):
        return build_file_context(task, st, phase_prefix="frontend/src")

    def generate(task, context):
        # Pure worker (runs in a thread under one permit): primary + one repair
        # LLM call (call_validated, log=None so no shared-list mutation off-loop),
        # then the pure processor. No generated_files/errors/log touch here.
        messages = [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": context},
        ]
        tally = []  # worker-local: no shared-state mutation off the event loop
        raw = call_validated(
            messages, "frontend_code", state, max_tokens=1500,
            original_instruction="Output ONLY the file's code — no fences, no prose.",
            log=None,
            # Day 22: covers .json artifacts written by this phase. .jsx needs a
            # node subprocess, so it is checked in the batched validation_pass —
            # spawning node per file across parallel workers is the trap.
            extra_validators=[syntax_of(task.get("filepath", ""))],
            # Explicit per-file attribution: these workers run in a thread pool,
            # so ambient/thread-local context cannot identify which file a call
            # belongs to. Proven by the parallel-attribution test.
            label=task.get("filepath", ""),
            repair_tally=tally,
        )
        processed = process_generated_file(project_id, task, raw, file_tree=file_tree)
        processed.repairs_spent = len(tally)
        return processed

    result = run_phase(
        fe_tasks, state, generate=generate, build_context=build_context,
        stub_for=lambda t, r: _failure_stub(t.get("filepath", ""), r),
        phase="frontend", project_id=project_id, file_tree=file_tree,
        implicit_deps=implicit_deps)

    files_ok, files_failed, blocked, total = (
        len(result.ok), len(result.failed), len(result.blocked), result.total)
    log.append(f"frontend_coder_agent: completed — {files_ok} ok, "
               f"{files_failed} failed, {blocked} blocked")
    print(f"[FrontendCoder] Done. {files_ok} ok, {files_failed} failed, {blocked} blocked")

    from ..core.connection_manager import manager

    # >50% rule counts blocked as not-delivered — the stage genuinely didn't
    # produce them, and a router blocked by a failed model is a real gap.
    not_delivered = files_failed + blocked
    if total and not_delivered / total > STAGE_FAIL_THRESHOLD:
        raise LLMError(
            f"frontend stage halted: {not_delivered}/{total} files failed to "
            f"deliver (>{int(STAGE_FAIL_THRESHOLD * 100)}%; {files_failed} failed, "
            f"{blocked} blocked)",
            "frontend_code",
        )

    manager.broadcast_sync(project_id, {
        "type": "agent_complete",
        "agent": "frontend_code",
        "stage": "frontend_code",
        "output_preview": f"Generated {files_ok}/{total} frontend files"
                          + (f" ({files_failed} failed, {blocked} blocked)" if not_delivered else ""),
        "files_ok": files_ok,
        "files_failed": files_failed,
        "files_blocked": blocked,
    })

    out = {
        "generated_files": generated_files,
        "log": log,
        "errors": errors,
        "current_stage": "database",
        "_agent_event": True,
    }
    # Honest partial-stage record: stage_node folds this into stage_history.
    failed_files = result.failed + result.blocked
    if failed_files:
        out["partial_failures"] = failed_files
    return out
