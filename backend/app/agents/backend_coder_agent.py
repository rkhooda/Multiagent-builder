"""Backend Coder Agent — Phase 6 (Day 19 rebuild, Day 20 parallelised).

Per-file FastAPI + SQLAlchemy + Pydantic generation. This agent supplies the
coder-specific callbacks (context, generate-one-file with the AST import fixer,
failure stub) and the backend dependency shape; the shared scheduler
(parallel_runner.run_phase) owns ordering, concurrency, per-file isolation,
transitive blocking, and live progress. Structural ordering (schema before
router) is enforced as real dependency edges (backend_implicit_deps), not a sort.

Ownership (Day 19 ponytail #1): the database agent already generated the ORM
models (it runs first now), so this agent generates schemas/routers/services and
CONSUMES the models as full-content context. It also owns the deterministic
Python infra (config.py, database.py, main.py, requirements.txt) — those are NOT
LLM-generated (a hallucinated version or engine setup is unacceptable), rendered
after the phase so main.py registers only the routers that actually delivered.
"""
from pathlib import Path

from app.exceptions import LLMError
from app.utils.file_writer import process_generated_file, write_project_file
from app.utils.backend_infra import (
    render_config,
    render_database,
    render_main,
    render_requirements,
)
from app.validation import call_validated, syntax_of
from .context_builder import build_file_context, backend_file_kind, _resource_of, _same_resource
from .parallel_runner import run_phase
from .utils import get_tasks_for_phase, assert_single_owner

SYSTEM_PROMPT = (Path(__file__).resolve().parents[3] / "prompts" / "backend_coder_agent.md").read_text(encoding="utf-8")

# Fraction of files that must fail before the whole stage is considered broken.
STAGE_FAIL_THRESHOLD = 0.5

# Infra files rendered deterministically (see backend_infra) — excluded from the
# per-task LLM loop even if the planner lists them, so they are never
# double-generated and never hallucinated.
INFRA_BASENAMES = {"config.py", "database.py", "main.py", "requirements.txt"}

# Backend structural ordering (config -> model -> schema -> router -> service ->
# main) is enforced by the scheduler as real dependency edges, not a sort key:
# every router/service implicitly depends on the schema (and model) tasks so it
# generates only after them. See backend_implicit_deps below.
STRUCTURAL_DEP_KINDS = {"model", "schema"}


def _failure_stub(filepath: str, error: str) -> str:
    """Placeholder written when a file's generation fails, so it is visible in
    the Gate 4 file browser and fixable there via Request AI Fix (reads disk).
    A bare module that imports cleanly — it defines no `router`, so main.py
    (which registers only successful routers) never imports a broken one."""
    return (f"# Generation failed for {filepath}: {error[:160]}\n"
            f"# Placeholder — regenerate with \"Request AI Fix\" at the review gate.\n\n"
            f"pass\n")


def _basename(filepath: str) -> str:
    return filepath.rsplit("/", 1)[-1]


def backend_implicit_deps(task: dict, by_id: dict) -> list:
    """Structural edges the planner may have omitted: a router/service is
    generated only after the SAME-resource model/schema tasks, so its FULL
    model/schema context is real before it imports them. Same-resource (not
    all-schemas) keeps blocking precise — one resource's schema failure blocks
    only that resource's router, not every router. (ORM models live in the
    database phase — already committed — so those edges fall outside this task
    set and impose no wait here.)"""
    kind = backend_file_kind(task.get("filepath", ""), task.get("description", ""))
    if kind not in ("router", "service"):
        return []
    resource = _resource_of(task.get("filepath", ""))
    return [tid for tid, t in by_id.items()
            if backend_file_kind(t.get("filepath", ""), t.get("description", "")) in STRUCTURAL_DEP_KINDS
            and _same_resource(_resource_of(t.get("filepath", "")), resource)]


def _infra_path(file_list: list, basename: str, default: str) -> str:
    """Resolve an infra file's path from the planned tree if present, else the
    conventional default, so infra lands where the architecture expects it."""
    for p in file_list or []:
        if _basename(p) == basename:
            return p
    return default


def _generate_infra(state: dict, generated_files: dict, ok_router_paths: list,
                    project_id: str, project_name: str, log: list, errors: list) -> int:
    """Render + write the deterministic Python infra files. Returns count written.
    main.py registers ONLY the routers that generated successfully."""
    file_list = state.get("file_list", [])
    tech_stack = state.get("tech_stack", "")

    req_content, req_warnings = render_requirements(tech_stack, generated_files)
    for w in req_warnings:
        errors.append(f"import_warning: {w}")

    infra = [
        (_infra_path(file_list, "config.py", "backend/app/config.py"), render_config()),
        (_infra_path(file_list, "database.py", "backend/app/database.py"), render_database()),
        (_infra_path(file_list, "main.py", "backend/app/main.py"),
         render_main(project_name, ok_router_paths)),
        (_infra_path(file_list, "requirements.txt", "backend/requirements.txt"), req_content),
    ]

    from ..core.connection_manager import manager
    written = 0
    for filepath, content in infra:
        result = write_project_file(project_id, filepath, content)
        if result["success"]:
            generated_files[filepath] = content
            written += 1
            log.append(f"backend_coder_agent: wrote infra {filepath} ({result['size_bytes']} bytes)")
            manager.broadcast_sync(project_id, {
                "type": "file_written", "filename": _basename(filepath),
                "filepath": filepath, "phase": "backend", "task_id": "infra",
            })
        else:
            errors.append(f"backend_coder_agent: failed to write infra {filepath}: {result['error']}")
    return written


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
    # Infra files are rendered deterministically — never sent to the LLM.
    llm_tasks = [t for t in be_tasks if _basename(t.get("filepath", "")) not in INFRA_BASENAMES]

    if not be_tasks:
        warning = "backend_coder_agent: no backend tasks in implementation plan"
        log.append(warning)
        print(f"[BackendCoder] WARNING: {warning}")
        return {"generated_files": generated_files, "log": log, "errors": errors,
                "current_stage": "qa"}

    file_tree = state.get("file_list") or list(generated_files.keys())

    def build_context(task, st):
        return build_file_context(task, st, phase_prefix="backend", phase="backend")

    def generate(task, context):
        # Pure worker (thread, one permit): primary + one repair LLM call
        # (call_validated, log=None), then the pure processor WITH the AST import
        # fixer. commit_generated_file surfaces any import warnings into state.
        messages = [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": context},
        ]
        tally = []  # worker-local: no shared-state mutation off the event loop
        raw = call_validated(
            messages, "backend_code", state, max_tokens=1500,
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
        implicit_deps=backend_implicit_deps)

    files_ok, files_failed, blocked, total = (
        len(result.ok), len(result.failed), len(result.blocked), result.total)

    # main.py registers only routers that actually delivered (never a failed or
    # blocked one) — the failed/blocked filepaths never reach ok_router_paths.
    ok_routers = [fp for fp in result.ok
                  if backend_file_kind(fp, "") == "router"]

    from ..core.connection_manager import manager

    # Deterministic infra — after the phase so main.py sees the real routers and
    # requirements.txt sees every generated import.
    infra_written = _generate_infra(state, generated_files, ok_routers,
                                    project_id, project_name, log, errors)

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
