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
from app.llm_router import call_llm
from app.utils.code_cleaner import strip_code_fences
from app.utils.file_writer import process_generated_file
from app.validation import call_validated, syntax_of
from app.validation import report as vreport
from .context_builder import build_file_context
from .frontend_reviewer import (
    build_revision_message, review_file, review_mode, should_review,
)
from .parallel_runner import comment_safe, run_phase
from .utils import get_tasks_for_phase

SYSTEM_PROMPT = (Path(__file__).resolve().parents[3] / "prompts" / "frontend_coder_agent.md").read_text(encoding="utf-8")

# Fraction of files that must not-deliver (failed + blocked) before the whole
# stage is considered broken.
STAGE_FAIL_THRESHOLD = 0.5

# A revision that comes back this thin is a refusal or a truncation, not a fix.
# Mirrors the min_code_lines(5) floor the generator itself is held to.
MIN_REVISED_LINES = 5


def _revise(task, content, issues, project_id, file_tree):
    """One targeted revision. Returns a ProcessedFile, or None to keep the original.

    Plain call_llm, not call_validated, on purpose: call_validated would spend a
    SECOND repair call inside a step that is already the file's last chance, and
    a revision that comes back broken is caught for free by the Day 22 validation
    pass under the same budget. One call in, one file out.

    Returning None is the fail-open path and it is the common one: the original
    was already written to disk by the first process_generated_file, so declining
    the revision leaves a good file in place rather than a worse one. A revision
    must never be able to make a file worse than not reviewing it at all.
    """
    filepath = task.get("filepath", "")
    try:
        raw = call_llm(
            [{"role": "user", "content": build_revision_message(filepath, content, issues)}],
            "frontend_code", max_tokens=1500, project_id=project_id, label=filepath)
    except Exception as e:                        # noqa: BLE001 — fail open
        print(f"[Reviewer] {filepath}: revision call failed, keeping original ({e})", flush=True)
        return None

    clean = strip_code_fences(raw)
    if len([l for l in clean.splitlines() if l.strip()]) < MIN_REVISED_LINES:
        print(f"[Reviewer] {filepath}: revision too short, keeping original", flush=True)
        return None

    processed = process_generated_file(project_id, task, clean, file_tree=file_tree)
    if processed.status != "ok":
        print(f"[Reviewer] {filepath}: revision failed to write, keeping original", flush=True)
        return None
    return processed


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
    state["review_results"] = dict(state.get("review_results") or {})
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

    # Sections are invented at planning time and are absent from the
    # architecture-derived file_list, so the phase's own task filepaths are
    # unioned in — otherwise import_fixer and the folder map cannot see them.
    file_tree = sorted(set(state.get("file_list") or list(generated_files.keys()))
                       | {t.get("filepath") for t in fe_tasks if t.get("filepath")})
    lib_ids = {t["id"] for t in fe_tasks if t.get("id") and _is_lib_file(t.get("filepath", ""))}

    # ── Improvement 01: reviewer wiring, resolved once per phase ─────────────
    # Live dict on state so the worker's atomic reservation and the coordinator's
    # commit charge ONE ledger. Same trick generated_files already uses above.
    retry_counts = dict(state.get("retry_counts") or {})
    state["retry_counts"] = retry_counts
    # How many other frontend tasks import this one — the "shared primitive"
    # half of the selective predicate, read off the plan's own requires edges.
    dependents = {}
    for t in fe_tasks:
        for dep in (t.get("requires") or []):
            dependents[dep] = dependents.get(dep, 0) + 1
    # Fast mode already skips Day 22's repairs; a second opinion is the same kind
    # of spend, so it is withheld for the same reason and stated the same way.
    fast_mode = bool(state.get("fast_mode"))
    mode = "off" if fast_mode else review_mode()
    if mode != "off":
        log.append(f"frontend_coder_agent: review mode {mode}, "
                   f"budget {vreport.repairs_spent_total(retry_counts)}/"
                   f"{vreport.REPAIR_CEILING_PER_RUN}")
    elif fast_mode:
        log.append("frontend_coder_agent: fast mode — files generated without review")

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

        # ── generate -> process -> [selective] review -> ONE revision ────────
        # All of it inside this worker, under the permit it already holds for
        # its whole lifetime (Day 20 ponytail #2 / Improvement 01 ponytail #2).
        # No second wave, no new semaphore, no new scheduler: a dependent file
        # awaiting this one's future therefore sees the REVISED content, which a
        # post-hoc review pass could not offer.
        filepath = task.get("filepath", "")
        findings = list(processed.warnings) + list(processed.import_warnings)
        wanted, reason = should_review(task, findings, dependents.get(task.get("id"), 0), mode)
        if not wanted:
            return processed

        result = review_file(task, processed.content, context, state,
                             parser_findings=processed.warnings)
        processed.review = result.as_state()
        if not result.needs_revision:
            return processed

        # ONE account (ponytail #2). A revision is an LLM call that fixes a
        # generated file, exactly like a Day 22 repair, so it charges the same
        # repair:{path} key under the same per-file cap and per-run ceiling.
        # Reserved atomically because these workers are a thread pool: check-then
        # -charge as two statements lets three workers each claim the last slot.
        allowed, why = vreport.try_reserve_repair(retry_counts, filepath)
        if not allowed:
            # Budget exhaustion is a SIGNAL, not an error — Day 22's position.
            # The file ships as generated, the reason is recorded, and the run
            # says out loud that the coder prompt or architecture needs work.
            processed.review["skipped_reason"] = f"revision_{why}"
            print(f"[Reviewer] {filepath}: revision withheld ({why})", flush=True)
            return processed

        revised = _revise(task, processed.content, result.blocking_issues,
                          project_id, file_tree)
        if revised is None:
            return processed          # original stands; the slot is still spent
        revised.repairs_spent = processed.repairs_spent
        revised.review = processed.review
        revised.revised = True
        return revised

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
        # Both were mutated in place by the coordinator/worker during the phase;
        # they must be returned or LangGraph merges nothing and the budget spent
        # here is invisible to the Day 22 validation pass that runs next.
        "retry_counts": retry_counts,
        "review_results": state.get("review_results", {}),
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
