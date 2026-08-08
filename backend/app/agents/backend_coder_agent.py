"""Backend Coder Agent — Phase 6 (Day 19 rebuild, Day 20 parallelised).

Per-file FastAPI + SQLAlchemy + Pydantic generation. This agent supplies the
coder-specific callbacks (context, generate-one-file with the AST import fixer,
failure stub) and the backend dependency shape; the shared scheduler
(parallel_runner.run_phase) owns ordering, concurrency, per-file isolation,
transitive blocking, and live progress. Structural ordering (schema before
router) is enforced as real dependency edges (backend_implicit_deps), not a sort.

Ownership (Day 19 ponytail #1): the database agent already generated the ORM
models (it runs first now), so this agent generates schemas/routers/services and
CONSUMES the models as full-content context. Deterministic infra (config.py,
database.py, main.py, requirements.txt for react-fastapi) is NOT LLM-generated —
the active Stack Profile renders it after the phase so main.py registers only
the routers that actually delivered. Stack conventions (prompt, context recipe,
implicit dependency edges, infra set) all come from the profile since
Improvement 03.
"""
from app.exceptions import LLMError
from app.profiles import active_profile
from app.utils.file_writer import process_generated_file
from app.validation import call_validated, syntax_of
from .context_builder import build_file_context
from .parallel_runner import comment_safe, run_phase
from .utils import get_tasks_for_phase, assert_single_owner

# Audited 2026-08-03: NO real pipeline history (every TodoSimple attempt was
# rate-limited). Direct model; frontend files (same shape) measured <= 829 —
# truncation flag + failover accounting are the alarm.
BACKEND_FILE_MAX_TOKENS = 1500

# Fraction of files that must fail before the whole stage is considered broken.
STAGE_FAIL_THRESHOLD = 0.5

def _failure_stub(filepath: str, error: str) -> str:
    """Placeholder written when a file's generation fails, so it is visible in
    the Gate 4 file browser and fixable there via Request AI Fix (reads disk).
    A bare module that imports cleanly — it defines no `router`, so main.py
    (which registers only successful routers) never imports a broken one."""
    return (f"# Generation failed for {filepath}: {comment_safe(error)}\n"
            f"# Placeholder — regenerate with \"Request AI Fix\" at the review gate.\n\n"
            f"pass\n")


def _basename(filepath: str) -> str:
    return filepath.rsplit("/", 1)[-1]


def backend_coder_agent(state: dict) -> dict:
    """Reads: implementation_plan, architecture_doc, tech_stack, file_list,
    generated_files (incl. the models the database agent already wrote). Writes:
    generated_files (merged), log, errors, current_stage. Raises LLMError only
    when > 50% of files fail (recoverable stage halt)."""
    project_id = state.get("project_id", "")
    project_name = state.get("project_name", "Unknown Project")
    implementation_plan = state.get("implementation_plan", "[]")

    # Point state at a live working dict so the coordinator's commits and the
    # launch-time context builder share one view (single-threaded on the loop).
    generated_files = dict(state.get("generated_files", {}))
    state["generated_files"] = generated_files
    log = state.setdefault("log", [])
    errors = state.setdefault("errors", [])

    log.append("backend_coder_agent: started")
    print(f"[BackendCoder] Starting for project: {project_name}")

    # Ownership guard — fail loudly if a filepath was planned under both phases.
    assert_single_owner(implementation_plan)

    be_tasks = get_tasks_for_phase(implementation_plan, "backend")

    if not be_tasks:
        warning = "backend_coder_agent: no backend tasks in implementation plan"
        log.append(warning)
        print(f"[BackendCoder] WARNING: {warning}")
        return {"generated_files": generated_files, "log": log, "errors": errors,
                "current_stage": "qa"}

    # Stack conventions come from the active profile (Improvement 03); the
    # react-fastapi profile reproduces the pre-profile behaviour exactly.
    # Resolved after the empty-phase early return so a profile that declares no
    # backend phase never needs one.
    profile = active_profile(state)
    spec = profile.phase("backend")
    system_prompt = profile.prompt_for("backend")

    # Infra files are rendered deterministically — never sent to the LLM.
    llm_tasks = [t for t in be_tasks
                 if _basename(t.get("filepath", "")) not in profile.infra_basenames]

    file_tree = state.get("file_list") or list(generated_files.keys())

    def build_context(task, st):
        return build_file_context(task, st, phase_prefix=spec.context_prefix,
                                  phase=spec.context_recipe,
                                  file_kind=profile.file_kind,
                                  import_note=spec.import_note,
                                  structure_note=spec.structure_note)

    def generate(task, context):
        # Pure worker (thread, one permit): primary + one repair LLM call
        # (call_validated, log=None), then the pure processor WITH the AST import
        # fixer. commit_generated_file surfaces any import warnings into state.
        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": context},
        ]
        tally = []  # worker-local: no shared-state mutation off the event loop
        raw = call_validated(
            messages, spec.agent_type, state, max_tokens=BACKEND_FILE_MAX_TOKENS,
            original_instruction="Output ONLY the file's code — no fences, no prose.",
            log=None,
            # Day 22: real ast/compile parse of THIS file, inside the existing
            # one-repair budget. A syntactically dead model is not worth the
            # routers generated against it, so this catches it at write time.
            extra_validators=[syntax_of(task.get("filepath", ""))],
            # Explicit per-file attribution — see frontend_coder_agent.
            label=task.get("filepath", ""),
            repair_tally=tally,
        )
        processed = process_generated_file(
            project_id, task, raw, file_tree=file_tree, apply_import_fixer=True)
        processed.repairs_spent = len(tally)
        return processed

    result = run_phase(
        llm_tasks, state, generate=generate, build_context=build_context,
        stub_for=lambda t, r: _failure_stub(t.get("filepath", ""), r),
        phase="backend", project_id=project_id, file_tree=file_tree,
        implicit_deps=profile.implicit_deps.get("backend"))

    files_ok, files_failed, blocked, total = (
        len(result.ok), len(result.failed), len(result.blocked), result.total)

    # main.py registers only routers that actually delivered (never a failed or
    # blocked one) — the failed/blocked filepaths never reach ok_router_paths.
    ok_routers = [fp for fp in result.ok
                  if profile.file_kind and profile.file_kind(fp, "") == "router"]

    from ..core.connection_manager import manager

    # Deterministic infra — after the phase so main.py sees the real routers and
    # requirements.txt sees every generated import. Profile-owned: a stack with
    # no deterministic infra declares none and writes nothing.
    infra_written = profile.infra(state, generated_files, ok_routers,
                                  project_id, project_name, log, errors) if profile.infra else 0

    log.append(f"backend_coder_agent: completed — {files_ok} ok, {files_failed} failed, "
               f"{blocked} blocked, {infra_written} infra files")
    print(f"[BackendCoder] Done. {files_ok} ok, {files_failed} failed, "
          f"{blocked} blocked, {infra_written} infra")

    # >50% rule counts blocked as not-delivered (a router blocked by a failed
    # schema is a real gap the stage didn't fill).
    not_delivered = files_failed + blocked
    if total and not_delivered / total > STAGE_FAIL_THRESHOLD:
        raise LLMError(
            f"backend stage halted: {not_delivered}/{total} files failed to deliver "
            f"(>{int(STAGE_FAIL_THRESHOLD * 100)}%; {files_failed} failed, "
            f"{blocked} blocked)",
            "backend_code",
        )

    manager.broadcast_sync(project_id, {
        "type": "agent_complete", "agent": "backend_code", "stage": "backend_code",
        "output_preview": f"Generated {files_ok}/{total} backend files + {infra_written} infra"
                          + (f" ({files_failed} failed, {blocked} blocked)" if not_delivered else ""),
        "files_ok": files_ok, "files_failed": files_failed, "files_blocked": blocked,
    })

    out = {
        "generated_files": generated_files,
        "log": log,
        "errors": errors,
        "current_stage": "qa",
        "_agent_event": True,
    }
    failed_files = result.failed + result.blocked
    if failed_files:
        out["partial_failures"] = failed_files
    return out
