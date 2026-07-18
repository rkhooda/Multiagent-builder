"""Backend Coder Agent — Phase 6 (Day 19 rebuild).

Per-file FastAPI + SQLAlchemy + Pydantic generation, built on the Day 18 coder
infrastructure (mirrors frontend_coder_agent.py): focused per-file context
(build_file_context, phase="backend") -> validated LLM call with one-shot repair
(call_validated) -> shared write chain with deterministic import repair
(process_and_write_generated_file, apply_import_fixer=True) -> generated_files
update -> broadcast. Per-file failures are isolated; the stage only fails when
> 50% of files fail.

Ownership (Day 19 ponytail #1): the database agent already generated the ORM
models (it runs first now), so this agent generates schemas/routers/services and
CONSUMES the models as full-content context. It also owns the deterministic
Python infra (config.py, database.py, main.py, requirements.txt) — those are NOT
LLM-generated (a hallucinated version or engine setup is unacceptable) and main.py
registers only the routers that actually generated.

Generation order is structural (config -> database -> model -> schema -> router ->
service -> main) layered UNDER the topological `requires` sort, so a router is
always generated after the model/schema it imports.
"""
import json
from pathlib import Path

from app.exceptions import LLMError
from app.utils.file_writer import process_and_write_generated_file, write_project_file
from app.utils.backend_infra import (
    render_config,
    render_database,
    render_main,
    render_requirements,
)
from app.validation import call_validated
from .context_builder import build_file_context, backend_file_kind
from .utils import get_tasks_for_phase, assert_single_owner

SYSTEM_PROMPT = (Path(__file__).resolve().parents[3] / "prompts" / "backend_coder_agent.md").read_text(encoding="utf-8")

# Fraction of files that must fail before the whole stage is considered broken.
STAGE_FAIL_THRESHOLD = 0.5

# Infra files rendered deterministically (see backend_infra) — excluded from the
# per-task LLM loop even if the planner lists them, so they are never
# double-generated and never hallucinated.
INFRA_BASENAMES = {"config.py", "database.py", "main.py", "requirements.txt"}

# Structural priority within the backend phase, layered UNDER the topological
# sort so the order holds even when the planner's `requires` edges are sparse.
KIND_PRIORITY = {
    "config": 0, "database": 1, "model": 2, "schema": 3,
    "router": 4, "service": 5, "main": 6, "other": 7,
}


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


def order_backend_tasks(tasks: list) -> list:
    """Kahn's topological sort over intra-phase `requires`, with the structural
    KIND_PRIORITY as the ready-set tie-break. Guarantees config/db/schema come
    before routers even without explicit edges. Leftovers from a cycle are
    appended in a stable order rather than dropped."""
    by_id = {t["id"]: t for t in tasks if t.get("id")}
    ids = set(by_id)
    indegree = {tid: 0 for tid in ids}
    dependents = {tid: [] for tid in ids}
    for tid, task in by_id.items():
        for req in task.get("requires", []) or []:
            if req in ids and req != tid:
                indegree[tid] += 1
                dependents[req].append(tid)

    def key(tid):
        t = by_id[tid]
        kind = backend_file_kind(t.get("filepath", ""), t.get("description", ""))
        return (KIND_PRIORITY.get(kind, 7), t.get("filepath", ""))

    ready = sorted([tid for tid in ids if indegree[tid] == 0], key=key)
    order = []
    while ready:
        tid = ready.pop(0)
        order.append(by_id[tid])
        for dep in dependents[tid]:
            indegree[dep] -= 1
            if indegree[dep] == 0:
                ready.append(dep)
        ready.sort(key=key)

    if len(order) < len(ids):
        placed = {t["id"] for t in order}
        order.extend(sorted((by_id[tid] for tid in ids if tid not in placed),
                            key=lambda t: t.get("filepath", "")))
    return order


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
    generated_files = dict(state.get("generated_files", {}))
    log = state.get("log", [])
    errors = state.get("errors", [])

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

    ordered = order_backend_tasks(llm_tasks)
    total = len(ordered)
    log.append(f"backend_coder_agent: {total} backend tasks, generating in structural + dependency order")
    print(f"[BackendCoder] {total} LLM tasks; first: {ordered[0].get('filepath') if ordered else '(none)'}")

    from ..core.connection_manager import manager

    files_ok = 0
    files_failed = 0
    failed_files = []
    ok_router_paths = []

    for i, task in enumerate(ordered):
        task_id = task.get("id", f"be_{i}")
        filepath = task.get("filepath", "")
        kind = backend_file_kind(filepath, task.get("description", ""))
        print(f"[BackendCoder] ({i+1}/{total}) {filepath} [{task_id}] kind={kind}")

        try:
            context = build_file_context(task, state, phase_prefix="backend", phase="backend")
            messages = [
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": context},
            ]
            raw = call_validated(
                messages, "backend_code", state, max_tokens=1500,
                original_instruction="Output ONLY the file's code — no fences, no prose.",
                log=log,
            )
            result = process_and_write_generated_file(
                project_id, task, raw, state, apply_import_fixer=True)
            if not result["success"]:
                raise RuntimeError(result["error"] or "write failed")

            generated_files[filepath] = result["content"]
            files_ok += 1
            if kind == "router":
                ok_router_paths.append(filepath)
            log.append(f"backend_coder_agent: wrote {filepath} ({result['size_bytes']} bytes)")
            manager.broadcast_sync(project_id, {
                "type": "file_written", "filename": Path(filepath).name,
                "filepath": filepath, "phase": "backend", "task_id": task_id,
                "index": i + 1, "total": total,
            })

        except Exception as e:
            files_failed += 1
            failed_files.append(filepath)
            errors.append(f"backend_coder_agent: {filepath} failed after repair: {e}")
            print(f"[BackendCoder] FAILED {filepath}: {e}")
            stub_result = process_and_write_generated_file(
                project_id, task, _failure_stub(filepath, str(e)), state)
            if stub_result["success"]:
                generated_files[filepath] = stub_result["content"]
            manager.broadcast_sync(project_id, {
                "type": "file_error", "filename": Path(filepath).name,
                "filepath": filepath, "phase": "backend", "task_id": task_id,
                "error": str(e)[:300], "index": i + 1, "total": total,
            })

    # Deterministic infra — after the loop so main.py sees the real routers and
    # requirements.txt sees every generated import.
    infra_written = _generate_infra(state, generated_files, ok_router_paths,
                                    project_id, project_name, log, errors)

    log.append(f"backend_coder_agent: completed — {files_ok} ok, {files_failed} failed, "
               f"{infra_written} infra files")
    print(f"[BackendCoder] Done. {files_ok} ok, {files_failed} failed, {infra_written} infra")

    # Stage-level failure only when the majority of LLM files failed.
    if total and files_failed / total > STAGE_FAIL_THRESHOLD:
        raise LLMError(
            f"backend stage halted: {files_failed}/{total} files failed to generate "
            f"(>{int(STAGE_FAIL_THRESHOLD * 100)}%)",
            "backend_code",
        )

    manager.broadcast_sync(project_id, {
        "type": "agent_complete", "agent": "backend_code", "stage": "backend_code",
        "output_preview": f"Generated {files_ok}/{total} backend files + {infra_written} infra",
        "files_ok": files_ok, "files_failed": files_failed,
    })

    result = {
        "generated_files": generated_files,
        "log": log,
        "errors": errors,
        "current_stage": "qa",
        "_agent_event": True,
    }
    if failed_files:
        result["partial_failures"] = failed_files
    return result
