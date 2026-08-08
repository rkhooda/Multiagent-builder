import uuid
import asyncio
import io
import json
import re
import shutil
import zipfile
from pathlib import Path
from fastapi import APIRouter, HTTPException
from fastapi.responses import PlainTextResponse, StreamingResponse
from pydantic import BaseModel, Field
from typing import List, Dict, Optional
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from app.graph.pipeline import (
    graph, GATE_ROUTES, STAGE_ORDER, STAGE_ARTIFACTS, SNAPSHOT_FIELDS,
    SKIPPABLE_AGENTS, invalidate_downstream,
)
from app.graph.state import ProjectState
from app.core.connection_manager import manager
from app.core.database import (
    insert_project, update_project_status, get_all_projects,
    update_project_rollups, delete_project_row,
    record_failure, clear_failure, get_failure,
)
from app.models import status as status_vocab
from app.profiles import get_profile
from app.utils.file_writer import OUTPUTS_ROOT
from app.observability import llm_cache, metrics_store

router = APIRouter(prefix="/api/projects", tags=["projects"])

class OptionalSections(BaseModel):
    existing_solutions: bool = False
    target_users: bool = False
    market_risks: bool = False

class ProjectCreateRequest(BaseModel):
    brief: str
    project_name: str
    optional_sections: Optional[OptionalSections] = None
    fast_mode: bool = False
    # "auto" defers to the requirements agent's recommendation via the
    # deterministic mapping; any registered profile name pins it explicitly.
    stack_profile: str = "auto"

class ProjectResumeRequest(BaseModel):
    decision: str = Field(..., description="Decision: approve, edit, back, or reject")
    feedback: str = Field("", description="Optional human feedback")


EDITABLE_STATE_FIELDS = {"requirements_doc", "architecture_doc", "implementation_plan"}

class ProjectStateEditRequest(BaseModel):
    field: str = Field(..., description="State field to overwrite")
    content: str = Field(..., description="New content for the field")
    excluded_tasks: Optional[List[Dict]] = Field(
        None,
        description="Tasks cut from the plan (only meaningful with field=implementation_plan); kept as an audit trail",
    )


def validate_plan_edit(content: str, excluded: list = None) -> list[str]:
    """Validate an edited implementation plan: task schema, unique ids, in-plan dependencies.

    Validates against TaskSchema but stores the submitted JSON verbatim, so extra
    keys like "custom": true on user-added tasks survive the round trip.

    `excluded` is the tasks the user unchecked at Gate 3. It is used only to
    SHARPEN the dependency message: cutting a section leaves its page shell still
    requiring it, which the generic in-plan check already rejects — but "requires
    'fe_011' which is not in the submitted plan" does not tell the user that they
    cut a section out from under a page shell, which is the fix they need to make.
    """
    from app.models.task_schema import TaskSchema

    try:
        tasks = json.loads(content)
    except json.JSONDecodeError as e:
        return [f"Plan is not valid JSON: {e}"]

    if not isinstance(tasks, list) or len(tasks) == 0:
        return ["Plan must be a non-empty JSON array of tasks"]

    errors: list[str] = []
    ids: list[str] = []
    for i, raw in enumerate(tasks):
        if not isinstance(raw, dict):
            errors.append(f"Task at index {i} is not an object")
            continue
        try:
            TaskSchema(**raw)
        except Exception as e:
            errors.append(f"Task {raw.get('id', f'index {i}')}: {e}")
        if raw.get("id"):
            ids.append(raw["id"])

    duplicates = {tid for tid in ids if ids.count(tid) > 1}
    if duplicates:
        errors.append(f"Duplicate task ids: {sorted(duplicates)}")

    id_set = set(ids)
    cut = {t.get("id"): t for t in (excluded or []) if isinstance(t, dict) and t.get("id")}
    for raw in tasks:
        if not isinstance(raw, dict):
            continue
        for dep in raw.get("requires", []) or []:
            if dep in id_set:
                continue
            cut_task = cut.get(dep)
            if cut_task and cut_task.get("section_of") == raw.get("id"):
                errors.append(
                    f"Page shell {raw.get('id', '?')} ({raw.get('filepath', '?')}) still "
                    f"composes section '{dep}' ({cut_task.get('filepath', '?')}), which you "
                    f"removed. Remove it from this shell's `requires` too, or keep the section."
                )
            else:
                errors.append(
                    f"Task {raw.get('id', '?')} requires '{dep}' which is not in the submitted plan"
                )

    # The other direction: a section kept while its shell was cut. Nothing would
    # ever render it, so it is a guaranteed-orphan generation call.
    for raw in tasks:
        if not isinstance(raw, dict):
            continue
        shell_id = raw.get("section_of")
        if shell_id and shell_id not in id_set:
            where = f" ({cut[shell_id].get('filepath', '?')})" if shell_id in cut else ""
            errors.append(
                f"Section {raw.get('id', '?')} ({raw.get('filepath', '?')}) belongs to page "
                f"shell '{shell_id}'{where}, which is not in the submitted plan. Nothing would "
                f"import this section — remove it too, or keep the shell."
            )

    return errors

executor = ThreadPoolExecutor(max_workers=10)

def get_next_event(iterator):
    try:
        return next(iterator)
    except StopIteration:
        return None

# ── Live-run registry (the liveness half of the position model) ──────────────
# Ponytail #1: the checkpointer's `next` is the authoritative answer to WHERE a
# project is, but it cannot answer WHETHER anything is still driving it — a
# process killed mid-node leaves next=<that node> and SQLite status=running
# forever. That is the zombie. Liveness is only ever true inside the process
# that owns the task, so it is deliberately in-memory: a restart empties this
# set, which is exactly the correct answer after a restart.
# Maps project_id -> the running Task, so a delete can tear the run down before
# removing its data rather than deleting out from under a live run.
_live_runs: Dict[str, asyncio.Task] = {}


async def run_graph_background(project_id: str, config: dict, initial_state=None):
    loop = asyncio.get_running_loop()
    current_agent = "unknown"
    nodes_ran = 0
    _live_runs[project_id] = asyncio.current_task()
    try:
        stream_iterator = await loop.run_in_executor(
            executor, 
            lambda: graph.stream(initial_state, config)
        )
        
        while True:
            event = await loop.run_in_executor(executor, get_next_event, stream_iterator)
            if event is None:
                break
                
            for node_name, node_output in event.items():
                if node_name == '__interrupt__':
                    continue
                
                current_agent = node_name
                nodes_ran += 1
                # A node completed, so whatever failure this project was
                # carrying is resolved. Clearing here (rather than in the graph
                # state) keeps the failure record and the checkpoint decoupled.
                if nodes_ran == 1:
                    clear_failure(project_id)
                if "gate" in node_name:
                    continue
                
                stage = node_output.get("current_stage", node_name)
                preview = ""
                content = ""
                preview_fields = [
                    "research_report", "requirements_doc", "tech_stack",
                    "architecture_doc", "implementation_plan", "qa_report"
                ]
                for field in preview_fields:
                    if field in node_output and node_output[field]:
                        content = str(node_output[field])
                        preview = content[:200]
                        break
                
                if not preview and "log" in node_output and node_output["log"]:
                    preview = str(node_output["log"][-1])[:200]
                    content = str(node_output["log"][-1])
                
                if not preview:
                    preview = f"Completed agent node {node_name}"
                    content = preview

                if node_output.get("_agent_event"):
                    continue
                 
                await manager.broadcast(project_id, {
                    "type": "agent_complete",
                    "agent": node_name,
                    "stage": stage,
                    "preview": preview,
                    "output_preview": preview,
                    "content": content
                })
                
                # Update SQLite database project status and current stage
                update_project_status(project_id, "running", stage)

                # Denormalised list rollups, stamped as the values become known
                # rather than recomputed per row on every list request.
                if "generated_files" in node_output or "qa_issues_count" in node_output:
                    update_project_rollups(
                        project_id,
                        files_generated=(len(node_output["generated_files"])
                                         if "generated_files" in node_output else None),
                        qa_issues_count=node_output.get("qa_issues_count"),
                    )
                
        state_snapshot = graph.get_state(config)
        if state_snapshot.next:
            gate_name = state_snapshot.next[0]
            # Update SQLite database project status
            update_project_status(project_id, "awaiting_approval", gate_name)
            await manager.broadcast(project_id, {
                "type": "gate_reached",
                "gate": gate_name,
                "status": "awaiting_approval"
            })
        elif nodes_ran == 0 and (get_failure(project_id)[0]
                                 or state_snapshot.values.get("failed_agent")):
            # The checkpoint had nothing left to run AND the project is carrying a
            # recorded failure: this resume could not re-enter the graph, so the
            # run did NOT complete. Claiming completion here would report a failed
            # project — no architecture, no plan, no code — as finished, which is
            # the worst possible lie this function can tell. Leave it error_paused.
            failed = get_failure(project_id)[0] or state_snapshot.values["failed_agent"]
            print(f"[Resume] {project_id}: nothing to re-enter (failed at {failed}); "
                  f"staying error_paused rather than reporting completion", flush=True)
            update_project_status(project_id, "error_paused", failed)
            await manager.broadcast(project_id, {
                "type": "agent_error",
                "agent": failed,
                "error_type": "unresumable",
                "message": ("This run could not be resumed from its last checkpoint. "
                            "Restart from an earlier stage to continue."),
                "recoverable": False,
                "skippable": False,
            })
        else:
            final_decision = state_snapshot.values.get("human_decision", "")
            if final_decision == "reject":
                update_project_status(project_id, "cancelled", "cancelled")
                await manager.broadcast(project_id, {
                    "type": "project_cancelled",
                    "project_id": project_id
                })
            else:
                # Update SQLite database project status to completed
                update_project_status(project_id, "completed", "completed")
                await manager.broadcast(project_id, {
                    "type": "pipeline_complete",
                    "project_id": project_id
                })
            
    except Exception as e:
        # The invariant: the graph task NEVER dies without leaving the project
        # in a recoverable status and broadcasting why. Even the handler itself
        # is guarded — worst case we still stamp error_paused into SQLite.
        try:
            await handle_pipeline_failure(project_id, config, current_agent, e)
        except Exception as handler_err:
            print(f"[Failure] handler itself failed for {project_id}: {handler_err}", flush=True)
            update_project_status(project_id, "error_paused", current_agent)
    finally:
        _live_runs.pop(project_id, None)


# ── Failure handling & recovery ──────────────────────────────────────────────
# Statuses: running -> awaiting_approval | rate_limited | error_paused |
# completed | cancelled. rate_limited auto-retries (60s, max 3 cycles) then
# converts to error_paused for manual recovery via POST /recover.

AUTO_RETRY_DELAY_SECONDS = 60
MAX_AUTO_RETRY_CYCLES = 3
_auto_retry_tasks: Dict[str, asyncio.Task] = {}


def cancel_auto_retry(project_id: str):
    task = _auto_retry_tasks.pop(project_id, None)
    if task:
        task.cancel()


async def handle_pipeline_failure(project_id: str, config: dict, current_agent: str, exc: Exception):
    """Classify a dead graph task's exception, persist it to the checkpoint,
    transition the project status, and broadcast a recoverable error event."""
    error_type = getattr(exc, "error_type", "unknown")
    recoverable = getattr(exc, "recoverable", True)
    message = str(exc)

    state_snapshot = graph.get_state(config)
    # The failed node is what the checkpoint will re-run; prefer the typed
    # exception's agent, then the interrupted node, then the last-seen agent.
    agent = getattr(exc, "agent_type", "") or (
        state_snapshot.next[0] if state_snapshot.next else current_agent)

    _, previous_failure = get_failure(project_id)
    cycles = (previous_failure or {}).get("auto_retry_cycles", 0)
    rate_limited = error_type == "rate_limit" and cycles < MAX_AUTO_RETRY_CYCLES
    status = "rate_limited" if rate_limited else "error_paused"

    failure_context = {
        "error_type": error_type,
        "message": message[:2000],
        "model": getattr(exc, "model", ""),
        "auto_retry_cycles": cycles + 1 if rate_limited else cycles,
        "recoverable": recoverable,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "attempt": cycles + 1,
    }

    # CRITICAL: this handler must NOT call graph.update_state(). After a node
    # raises, the checkpoint's `next` is (failed_node,) and its task carries the
    # error — exactly what a retry needs. A write here re-derives `next` to ()
    # and the run becomes permanently unresumable (proven in test_lifecycle.py).
    # The failure is recorded on the project row instead.
    record_failure(project_id, agent, failure_context)
    update_project_status(project_id, status, agent)
    print(f"[Failure] {project_id} agent={agent} type={error_type} -> {status}: {message[:300]}", flush=True)

    if rate_limited:
        await manager.broadcast(project_id, {
            "type": "rate_limited",
            "agent": agent,
            "message": message[:500],
            "retry_in": AUTO_RETRY_DELAY_SECONDS,
            "cycle": cycles + 1,
            "max_cycles": MAX_AUTO_RETRY_CYCLES,
        })
        cancel_auto_retry(project_id)
        _auto_retry_tasks[project_id] = asyncio.create_task(_auto_retry(project_id, config, agent))
    else:
        await manager.broadcast(project_id, {
            "type": "agent_error",
            "agent": agent,
            "error_type": error_type,
            "message": message[:500],
            "recoverable": recoverable,
            "skippable": agent in SKIPPABLE_AGENTS,
        })


async def _auto_retry(project_id: str, config: dict, agent: str):
    """Countdown then re-stream from the checkpoint (the failed node re-runs)."""
    try:
        await asyncio.sleep(AUTO_RETRY_DELAY_SECONDS)
        _auto_retry_tasks.pop(project_id, None)
        update_project_status(project_id, "running", agent)
        await manager.broadcast(project_id, {"type": "auto_retry", "agent": agent})
        await run_graph_background(project_id, config)
    except asyncio.CancelledError:
        pass

def compute_generation_seconds(project_id: str):
    """Elapsed seconds from first to latest checkpoint (includes gate review time).

    Checkpoint timestamps come free from the SqliteSaver, so historical projects
    get a real value without retrofitting timestamps into every log line.
    """
    from datetime import datetime
    try:
        config = {"configurable": {"thread_id": project_id}}
        timestamps = [s.created_at for s in graph.get_state_history(config) if s.created_at]
        if len(timestamps) < 2:
            return None
        newest = datetime.fromisoformat(timestamps[0].replace("Z", "+00:00"))
        oldest = datetime.fromisoformat(timestamps[-1].replace("Z", "+00:00"))
        return max(0, int((newest - oldest).total_seconds()))
    except Exception as e:
        print(f"[Stats] Failed to compute generation time for {project_id}: {e}")
        return None


def derive_position(state_snapshot, project_id: str, db_status: str) -> dict:
    """The single source of truth for "where is this project RIGHT NOW".

    Ponytail #1: three candidate signals, ranked by how much they can lie.
      - checkpointer `next`  — authoritative for POSITION. It is literally what a
        resume will execute next. Never stale.
      - projects.status      — authoritative for INTENT, stale-prone for liveness
        (a killed process leaves `running` behind forever).
      - state.current_stage  — a naming convention ("names the NEXT stage"),
        written by whichever node ran last. Useful as a label, never as truth.

    So: position comes from `next`, liveness from `next` + the in-process live-run
    registry. `phase` is what the UI switches on:
      gate        -> render that gate's approval card (cold or live, identically)
      running     -> a task really is streaming; render the live feed
      interrupted -> ZOMBIE: next is an agent node but nothing is driving it.
                     Offer Resume instead of a spinner that will never resolve.
      error       -> error_paused / rate_limited; render the recovery card
      terminal    -> completed / cancelled
    """
    next_node = state_snapshot.next[0] if state_snapshot.next else None
    is_live = project_id in _live_runs

    if db_status in (status_vocab.ERROR_PAUSED, status_vocab.RATE_LIMITED):
        phase = "error"
    elif next_node is None:
        phase = "terminal"
    elif next_node.startswith("human_gate_"):
        phase = "gate"
    elif is_live:
        phase = "running"
    else:
        phase = "interrupted"

    return {
        "phase": phase,
        "next_node": next_node,
        "gate": next_node if phase == "gate" else None,
        "is_live": is_live,
        # Everything except a finished graph can be re-entered from the checkpoint.
        "resumable": phase in ("gate", "interrupted", "error"),
    }


def serialize_project_state(state_snapshot, project_id: str) -> dict:
    """Centralized serialization of LangGraph state snapshot to API response."""
    from app.llm_router import MODELS
    values = state_snapshot.values
    
    # Query database for the real status and stage
    from app.core.database import get_db_connection
    status = "running"
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT status FROM projects WHERE id = ?", (project_id,))
        row = cursor.fetchone()
        conn.close()
        if row:
            status = row["status"]
    except Exception as db_err:
        print(f"[DB ERROR] Failed to fetch project status: {db_err}")

    next_gate = state_snapshot.next[0] if (state_snapshot.next and state_snapshot.next[0].startswith("human_gate_")) else None
    failed_agent, failure_context = get_failure(project_id)


    return {
        "project_id": values.get("project_id", project_id),
        "brief": values.get("brief", ""),
        "project_name": values.get("project_name", ""),
        "research_report": values.get("research_report", ""),
        "requirements_doc": values.get("requirements_doc", ""),
        "tech_stack": values.get("tech_stack", ""),
        # Improvement 03. stack_profile is the target being built;
        # profile_mismatch is non-empty only when the recommendation matched
        # nothing we build, and Gate 1 renders it as an unresolved choice.
        "stack_profile": values.get("stack_profile", ""),
        "stack_profile_label": get_profile(values.get("stack_profile", "")).label,
        "profile_mismatch": values.get("profile_mismatch", ""),
        "architecture_doc": values.get("architecture_doc", ""),
        "implementation_plan": values.get("implementation_plan", ""),
        "excluded_tasks": values.get("excluded_tasks", []),
        "file_list": values.get("file_list", []),
        "generated_files": values.get("generated_files", {}),
        "qa_report": values.get("qa_report", ""),
        "qa_issues_count": values.get("qa_issues_count", 0),
        "devops_files": values.get("devops_files", {}),
        "previous_versions": values.get("previous_versions", {}),
        "fix_counts": values.get("fix_counts", {}),
        "retry_counts": values.get("retry_counts", {}),
        "validation_report": values.get("validation_report", {}),
        "build_verification": values.get("build_verification", {}),
        "degraded_events": values.get("degraded_events", {}),
        "stage_history": values.get("stage_history", []),
        "current_stage": values.get("current_stage", ""),
        "human_feedback": values.get("human_feedback", ""),
        "human_decision": values.get("human_decision", ""),
        "log": values.get("log", []),
        "errors": values.get("errors", []),
        # Failure info comes from the project row. Projects that failed before
        # Day 24 still carry it in their checkpoint, so fall back to state for
        # them rather than losing their error card.
        "failed_agent": failed_agent or values.get("failed_agent", ""),
        "failure_context": failure_context or values.get("failure_context"),
        "status": status,
        "next_gate": next_gate,
        "position": derive_position(state_snapshot, project_id, status),
        "generation_seconds": compute_generation_seconds(project_id),
        "agent_models": {agent: primary for agent, (primary, _) in MODELS.items()},
    }

def save_error_to_state(config, error_message: str):
    """Saves a pipeline execution error message directly to the checkpoint state."""
    try:
        state_snapshot = graph.get_state(config)
        if state_snapshot.values:
            current_errors = state_snapshot.values.get("errors", []) or []
            graph.update_state(config, {"errors": current_errors + [error_message]})
    except Exception as db_err:
        print(f"[DB ERROR] Failed to save execution error to state: {db_err}", flush=True)

@router.get("")
def list_projects(status: Optional[str] = None, sort: str = "created_at"):
    """Enriched project list, newest-first, with server-side filter and sort.

    `interrupted` is derived per row from the live-run registry rather than
    persisted: a `running` row that no task in this process is driving is a
    zombie by definition, and that check is an in-memory set lookup — no
    checkpoint load per row. This is what stops the list showing a dead
    "running" spinner for a project the server was killed under.
    """
    rows = get_all_projects(status_filter=status, sort=sort)
    for row in rows:
        row["interrupted"] = (
            row["status"] == status_vocab.RUNNING and row["id"] not in _live_runs
        )
    return {"projects": rows, "total": len(rows)}

def _validated_profile(name: str) -> str:
    """A registered profile name, or "auto". Anything else is a 400 rather than
    a silent fallback — a typo'd profile must not quietly build react-fastapi,
    which is exactly the failure the mismatch path exists to prevent."""
    from app.profiles import AUTO, PROFILES

    value = (name or AUTO).strip().lower()
    if value == AUTO or value in PROFILES:
        return value
    raise HTTPException(
        status_code=400,
        detail=f"Unknown stack profile '{name}'. Supported: "
               f"{AUTO}, {', '.join(sorted(PROFILES))}",
    )


@router.get("/stack-profiles")
async def list_stack_profiles():
    """The targets this system can actually build — the honest supported list.
    Drives the New Project selector and the Gate 1 override."""
    from app.profiles import AUTO, DEFAULT_PROFILE, PROFILES

    return {
        "default": DEFAULT_PROFILE,
        "auto": AUTO,
        "profiles": [
            {
                "name": p.name,
                "label": p.label,
                "summary": p.summary,
                "phases": [{"name": s.name, "label": s.label} for s in p.phases],
            }
            for p in PROFILES.values()
        ],
    }


class ProfileChangeRequest(BaseModel):
    stack_profile: str = Field(..., description="A registered profile name")


@router.post("/{project_id}/stack-profile")
async def set_stack_profile(project_id: str, request: ProfileChangeRequest):
    """Change the active profile while paused at a gate.

    Everything downstream of requirements is stack-shaped — the architecture
    names a folder tree and an API surface, and the plan and code follow from
    it — so a profile change invalidates from requirements onward through the
    EXISTING invalidate_downstream, rather than any new invalidation logic.
    """
    from app.graph.pipeline import graph, invalidate_downstream
    from app.profiles import AUTO, PROFILES

    name = (request.stack_profile or "").strip().lower()
    if name == AUTO or name not in PROFILES:
        raise HTTPException(
            status_code=400,
            detail=f"Choose a specific profile to build. Supported: "
                   f"{', '.join(sorted(PROFILES))}",
        )

    config = {"configurable": {"thread_id": project_id}}
    snapshot = graph.get_state(config)
    state = snapshot.values or {}
    if not state:
        raise HTTPException(status_code=404, detail="Project not found")
    if state.get("stack_profile") == name:
        return {"stack_profile": name, "invalidated": [], "unchanged": True}

    update = invalidate_downstream(state, "requirements")
    update["stack_profile"] = name
    update["profile_mismatch"] = ""     # the human just resolved it
    graph.update_state(config, update)
    return {
        "stack_profile": name,
        "invalidated": sorted(k for k in update
                              if k not in ("previous_versions", "stack_profile",
                                           "profile_mismatch")),
    }


@router.post("")
async def create_project(request: ProjectCreateRequest):
    project_id = str(uuid.uuid4())
    config = {"configurable": {"thread_id": project_id}}

    optional_sections_dict = (
        request.optional_sections.model_dump()
        if request.optional_sections
        else {"existing_solutions": False, "target_users": False, "market_risks": False}
    )

    # Build initial ProjectState
    initial_state = ProjectState(
        project_id=project_id,
        brief=request.brief,
        project_name=request.project_name,
        optional_sections=json.dumps(optional_sections_dict),
        fast_mode=request.fast_mode,
        stack_profile=_validated_profile(request.stack_profile),
        profile_mismatch="",
        research_report="",
        requirements_doc="",
        tech_stack="",
        architecture_doc="",
        implementation_plan="",
        excluded_tasks=[],
        file_list=[],
        generated_files={},
        qa_report="",
        qa_issues_count=0,
        devops_files={},
        fix_counts={},
        retry_counts={},
        stage_history=[],
        regen_cycle=None,
        replan_after_architecture=False,
        skip_gate_1=False,
        failed_agent="",
        failure_context=None,
        current_stage="",
        human_feedback="",
        human_decision="",
        log=[],
        errors=[]
    )
    
    # Insert initial project record into SQLite
    insert_project(project_id, request.project_name, request.brief, "running", "research")
    
    # Run graph in background
    asyncio.create_task(run_graph_background(project_id, config, initial_state))
    
    return {
        "project_id": project_id,
        "status": "running",
        "current_stage": "research",
        "log": []
    }

@router.get("/{project_id}")
def get_project(project_id: str):
    config = {"configurable": {"thread_id": project_id}}
    state_snapshot = graph.get_state(config)
    
    if not state_snapshot.values:
        raise HTTPException(status_code=404, detail="Project not found")
        
    return serialize_project_state(state_snapshot, project_id)

# ── Cascade delete ───────────────────────────────────────────────────────────
# Ponytail #3: four stores, and the order is chosen by which half-failure is
# survivable. "Row gone, files left" is unreachable garbage nobody can find or
# reclaim; "files gone, row left" is a visible broken project the user can simply
# delete again. So the DISCOVERABLE POINTER — the projects row — goes LAST, and
# every step is idempotent so a retry finishes the job. That makes mark-then-
# sweep unnecessary: a retry already is the sweep.
#
# Hard delete, not soft: a soft delete needs an archived flag, a filter on every
# query, a "show archived" toggle AND a purge action that performs this same hard
# delete anyway. The irreplaceability concern is real but already answered by the
# Day 15 ZIP, so the confirm dialog nudges the download instead. Deleted means
# deleted, which is also the honest UX.

PROJECT_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,127}$")


def project_output_path(project_id: str) -> Path:
    """Resolve outputs/{project_id}/ with two independent traversal guards.

    project_id reaches the filesystem as a path segment, so this is the worst
    possible bug in the codebase: a crafted id like '../..' would hand rmtree a
    directory outside outputs/. Belt and braces — the id must match a strict
    charset (no dots, no separators), AND the resolved path's parent must be
    exactly OUTPUTS_ROOT.
    """
    if not PROJECT_ID_RE.match(project_id):
        raise HTTPException(status_code=400, detail="Invalid project id")
    root = Path(OUTPUTS_ROOT).resolve()
    target = (root / project_id).resolve()
    if target.parent != root or target == root:
        raise HTTPException(status_code=400, detail="Invalid project id")
    return target


@router.delete("/{project_id}")
async def delete_project(project_id: str):
    """Delete a project from all four stores. Idempotent; safe to retry."""
    target_dir = project_output_path(project_id)   # validates before anything is touched

    # Never delete out from under a live run: cancel the task and wait for it to
    # unwind, so no node writes a checkpoint back after we have deleted it.
    cancelled_run = False
    cancel_auto_retry(project_id)
    # Before cancelling: coder workers run in threads that task.cancel() cannot
    # reach, so they must be told to stop at their next LLM call or they keep
    # spending quota on a project that is about to stop existing.
    from app.llm_router import abandon_project
    abandon_project(project_id)
    # Drop any live incremental QA stream with it — its consumer exits on the
    # abandoned check rather than reviewing files nobody can download.
    from app.agents import qa_stream
    qa_stream.discard(project_id)
    task = _live_runs.get(project_id)
    if task:
        cancelled_run = True
        task.cancel()
        try:
            await task
        except (asyncio.CancelledError, Exception):
            pass
        _live_runs.pop(project_id, None)

    result = {"project_id": project_id, "cancelled_run": cancelled_run}

    # 1. metrics (independent store, no references into it)
    result["metrics_rows_deleted"] = metrics_store.delete_project_metrics(project_id)

    # 2. checkpointer thread — the SDK owns its schema, so use its own deletion
    #    rather than hand-rolled DELETEs against checkpoints/writes.
    try:
        graph.checkpointer.delete_thread(project_id)
        result["checkpoint_deleted"] = True
    except Exception as e:                                    # noqa: BLE001
        print(f"[Delete] checkpoint delete failed for {project_id}: {e}", flush=True)
        result["checkpoint_deleted"] = False

    # 3. generated files (including any restart archive under .archived/)
    if target_dir.is_dir():
        shutil.rmtree(target_dir)
        result["files_deleted"] = True
    else:
        result["files_deleted"] = False

    # 4. the pointer, last — until this row is gone the project stays visible
    #    and a partial failure above can be finished by deleting again.
    result["row_deleted"] = delete_project_row(project_id) > 0

    if not result["row_deleted"] and not result["files_deleted"]:
        raise HTTPException(status_code=404, detail="Project not found")

    await manager.broadcast(project_id, {"type": "project_deleted", "project_id": project_id})
    print(f"[Delete] {project_id}: {result}", flush=True)
    return result


@router.patch("/{project_id}/state")
def edit_project_state(project_id: str, request: ProjectStateEditRequest):
    if request.field not in EDITABLE_STATE_FIELDS:
        raise HTTPException(
            status_code=400,
            detail=f"field must be one of {sorted(EDITABLE_STATE_FIELDS)}"
        )

    config = {"configurable": {"thread_id": project_id}}
    state_snapshot = graph.get_state(config)

    if not state_snapshot.values:
        raise HTTPException(status_code=404, detail="Project not found")

    if not state_snapshot.next:
        raise HTTPException(status_code=400, detail="Project is not awaiting approval and cannot be edited")

    update = {request.field: request.content}

    if request.field == "implementation_plan":
        validation_errors = validate_plan_edit(request.content, request.excluded_tasks)
        if validation_errors:
            raise HTTPException(status_code=422, detail={"errors": validation_errors})
        if request.excluded_tasks is not None:
            update["excluded_tasks"] = request.excluded_tasks

    # Single atomic update_state: the plan and its excluded-tasks audit trail land
    # in one checkpoint, so a concurrent resume can never see a half-applied edit.
    graph.update_state(config, update)

    return {"status": "updated", "field": request.field}

@router.post("/{project_id}/resume")
async def resume_project(project_id: str, request: ProjectResumeRequest):
    config = {"configurable": {"thread_id": project_id}}
    state_snapshot = graph.get_state(config)
    
    if not state_snapshot.values:
        raise HTTPException(status_code=404, detail="Project not found")
        
    if not state_snapshot.next:
        raise HTTPException(status_code=400, detail="Project is already completed and cannot be resumed")

    # Crash/error recovery: if the graph stopped mid-run (next is an agent
    # node, not a gate — backend killed or agent errored), restart streaming
    # from the checkpoint. Any decision/feedback already in state drives the
    # interrupted cycle to completion; the request's decision is ignored.
    gate = state_snapshot.next[0]
    if not gate.startswith("human_gate_"):
        update_project_status(project_id, "running", state_snapshot.values.get("current_stage", gate))
        asyncio.create_task(run_graph_background(project_id, config))
        return {"status": "resumed_from_checkpoint", "resuming_at": gate}

    # The routing map is the single source of truth for what each gate supports.
    allowed_decisions = set(GATE_ROUTES.get(gate, {"approve", "reject"}))
    if request.decision not in allowed_decisions:
        raise HTTPException(
            status_code=400,
            detail=f"decision at {gate} must be one of {sorted(allowed_decisions)}"
        )

    try:
        # Update SQLite database project status to running
        current_stage = state_snapshot.values.get("current_stage", "resume")
        update_project_status(project_id, "running", current_stage)
        
        # Update state with human decision and feedback. For feedback-driven
        # re-runs (edit/back), the same atomic update also invalidates every
        # artifact downstream of the target stage (snapshot + clear) and bumps
        # the target's retry count.
        update = {
            "human_decision": request.decision,
            "human_feedback": request.feedback,
            "regen_cycle": None,
        }
        if request.decision in ("edit", "back"):
            target = GATE_ROUTES[gate][request.decision]
            update["regen_cycle"] = {"trigger": request.decision, "gate": gate}
            if target in STAGE_ORDER:
                update.update(invalidate_downstream(state_snapshot.values, target))
            retry_counts = dict(state_snapshot.values.get("retry_counts") or {})
            retry_counts[target] = retry_counts.get(target, 0) + 1
            update["retry_counts"] = retry_counts
        graph.update_state(config, update)

        # Resume the graph in a background task
        asyncio.create_task(run_graph_background(project_id, config))

        return {"status": "resumed"}
    except Exception as e:
        save_error_to_state(config, str(e))
        raise HTTPException(status_code=500, detail=str(e))


# ── Restart from a chosen stage ──────────────────────────────────────────────
# Ponytail #2: restart and gate 'back'/'edit' are the SAME operation with
# different triggers, so this reuses both proven mechanisms instead of inventing
# state surgery. invalidate_downstream() snapshots and clears everything after
# the target; re-entry is done by writing the checkpoint as though the user had
# pressed a specific decision at a specific gate (as_node), which lets the
# existing GATE_ROUTES conditional edges do all the routing. No new graph wiring.
#
# thread_id is REUSED, not forked: forking would mean a second project row, a
# second outputs dir and split metrics for what the user thinks of as one
# project. Continuity is preserved instead by previous_versions (docs) and the
# on-disk archive (files), and the restart is recorded honestly in stage_history.

# Public stage name -> (stage key in STAGE_ORDER, gate to re-enter through, decision).
RESTART_ENTRY = {
    "research":        ("research",     "human_gate_1", "back"),
    "requirements":    ("requirements", "human_gate_1", "edit"),
    "architecture":    ("architecture", "human_gate_2", "edit"),
    "planning":        ("planning",     "human_gate_3", "edit"),
    "code_generation": ("code",         "human_gate_3", "approve"),
}

# The stage whose output must already exist for a restart target to be runnable.
RESTART_REQUIRES = {
    "requirements": "research_report",
    "architecture": "requirements_doc",
    "planning": "architecture_doc",
    "code_generation": "implementation_plan",
}

# Agents that re-run for each restart target, for the cost estimate.
AGENTS_FROM_STAGE = {
    "research": ["research", "requirements", "architecture", "planning",
                 "frontend_code", "backend_code", "database", "qa", "devops"],
    "requirements": ["requirements", "architecture", "planning",
                     "frontend_code", "backend_code", "database", "qa", "devops"],
    "architecture": ["architecture", "planning",
                     "frontend_code", "backend_code", "database", "qa", "devops"],
    "planning": ["planning", "frontend_code", "backend_code", "database", "qa", "devops"],
    "code_generation": ["frontend_code", "backend_code", "database", "qa", "devops"],
}

ARCHIVE_DIRNAME = ".archived"


class RestartRequest(BaseModel):
    from_stage: str = Field(..., description=f"One of {sorted(RESTART_ENTRY)}")


def restart_cost_estimate(project_id: str, from_stage: str) -> dict:
    """Rough re-run cost from this project's own metrics history.

    The honest estimate for "what will re-running these agents cost" is what
    those same agents already spent on this project. Agents with no history
    (never reached, or pre-Day-23) are reported as unknown rather than zero.
    """
    agents = set(AGENTS_FROM_STAGE.get(from_stage, []))
    rows = [r for r in metrics_store.avg_tokens_by_agent(project_id) if r.get("agent") in agents]
    known = {r["agent"] for r in rows}
    return {
        "agents": sorted(agents),
        "agents_with_history": sorted(known),
        "agents_without_history": sorted(agents - known),
        "estimated_tokens": sum(
            (r.get("total_prompt_tokens") or 0) + (r.get("total_completion_tokens") or 0)
            for r in rows
        ),
        "estimated_calls": sum((r.get("calls") or 0) for r in rows),
    }


def archive_generated_files(project_id: str) -> dict:
    """Move the current generated tree aside instead of deleting it.

    Ponytail #2: one run's worth of disk is cheap insurance against a restart the
    user regrets, and archiving inside outputs/{project_id}/ means the delete
    cascade reclaims it for free with the same rmtree. The cost is that every
    walker of that directory must skip ARCHIVE_DIRNAME — see list_project_files
    and download_project_zip.
    """
    directory = (Path(OUTPUTS_ROOT) / project_id)
    if not directory.is_dir():
        return {"archived": 0, "path": None}

    movable = [p for p in directory.iterdir() if p.name != ARCHIVE_DIRNAME]
    if not movable:
        return {"archived": 0, "path": None}

    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    target = directory / ARCHIVE_DIRNAME / stamp
    target.mkdir(parents=True, exist_ok=True)
    for path in movable:
        shutil.move(str(path), str(target / path.name))
    print(f"[Restart] {project_id}: archived {len(movable)} entries to {target}", flush=True)
    return {"archived": len(movable), "path": f"{ARCHIVE_DIRNAME}/{stamp}"}


@router.get("/{project_id}/restart-preview")
def restart_preview(project_id: str, from_stage: str):
    """What a restart would discard and roughly cost — drives the confirm dialog."""
    if from_stage not in RESTART_ENTRY:
        raise HTTPException(status_code=400, detail=f"from_stage must be one of {sorted(RESTART_ENTRY)}")
    state_snapshot = graph.get_state({"configurable": {"thread_id": project_id}})
    if not state_snapshot.values:
        raise HTTPException(status_code=404, detail="Project not found")

    stage_key = RESTART_ENTRY[from_stage][0]
    discarded = STAGE_ORDER[STAGE_ORDER.index(stage_key):]
    kept = STAGE_ORDER[:STAGE_ORDER.index(stage_key)]
    return {
        "from_stage": from_stage,
        "discards": discarded,
        "keeps": kept,
        "files_to_archive": len(state_snapshot.values.get("generated_files") or {}) if "code" in discarded else 0,
        "cost_estimate": restart_cost_estimate(project_id, from_stage),
    }


@router.post("/{project_id}/restart")
async def restart_project(project_id: str, request: RestartRequest):
    if request.from_stage not in RESTART_ENTRY:
        raise HTTPException(status_code=400, detail=f"from_stage must be one of {sorted(RESTART_ENTRY)}")

    config = {"configurable": {"thread_id": project_id}}
    state_snapshot = graph.get_state(config)
    values = state_snapshot.values
    if not values:
        raise HTTPException(status_code=404, detail="Project not found")

    if project_id in _live_runs:
        raise HTTPException(status_code=409, detail="Project is currently running — cancel or wait before restarting")

    # Guard: never restart into a stage whose inputs were never produced.
    required_field = RESTART_REQUIRES.get(request.from_stage)
    if required_field and not values.get(required_field):
        raise HTTPException(
            status_code=400,
            detail=f"Cannot restart from '{request.from_stage}': its input ({required_field}) has not been generated yet.",
        )

    stage_key, gate, decision = RESTART_ENTRY[request.from_stage]

    # Same invalidation the Day 16 back-navigation uses — snapshot docs into
    # previous_versions and clear everything downstream of the target.
    update = dict(invalidate_downstream(values, stage_key))
    # The target stage's own artifacts are stale too on a restart (unlike an
    # 'edit', which feeds the old output back in as context), so clear them.
    for field, empty in STAGE_ARTIFACTS[stage_key].items():
        current = values.get(field)
        if current and field in SNAPSHOT_FIELDS:
            update.setdefault("previous_versions", {})[field] = current
        update[field] = empty

    archive = archive_generated_files(project_id) if "code" in STAGE_ORDER[STAGE_ORDER.index(stage_key):] else {"archived": 0, "path": None}

    history = list(values.get("stage_history") or [])
    history.append({
        "stage": stage_key,
        "attempt": 1 + sum(1 for e in history if e.get("stage") == stage_key),
        "trigger": "restart",
        "from_stage": request.from_stage,
        "gate_origin": gate,
        "archived_to": archive["path"],
        "timestamp": datetime.now(timezone.utc).isoformat(),
    })
    retry_counts = dict(values.get("retry_counts") or {})
    retry_counts[stage_key] = retry_counts.get(stage_key, 0) + 1

    update.update({
        "human_decision": decision,
        "human_feedback": "",
        "regen_cycle": {"trigger": "restart", "gate": gate},
        "stage_history": history,
        "retry_counts": retry_counts,
        "failed_agent": "",
        "failure_context": None,
        "log": list(values.get("log") or []) + [
            f"restart: re-running from {request.from_stage}"
            + (f" (archived {archive['archived']} files to {archive['path']})" if archive["archived"] else "")
        ],
    })

    # as_node makes the checkpoint read as though the user had just pressed
    # `decision` at `gate` — the existing conditional edge then routes execution
    # to the target stage. Restart adds no graph wiring of its own.
    graph.update_state(config, update, as_node=gate)
    update_project_status(project_id, "running", stage_key)
    asyncio.create_task(run_graph_background(project_id, config))

    return {
        "status": "restarted",
        "from_stage": request.from_stage,
        "discarded": STAGE_ORDER[STAGE_ORDER.index(stage_key):],
        "archived": archive,
    }


class RecoverRequest(BaseModel):
    action: str = Field(..., description="retry, skip, or cancel")


def _db_status(project_id: str) -> str:
    from app.core.database import get_db_connection
    conn = get_db_connection()
    try:
        row = conn.execute("SELECT status FROM projects WHERE id = ?", (project_id,)).fetchone()
        return row["status"] if row else ""
    finally:
        conn.close()


@router.post("/{project_id}/recover")
async def recover_project(project_id: str, request: RecoverRequest):
    """Recover a failed project: retry the failed agent, skip it (skippable
    agents only), or cancel. Valid only while error_paused / rate_limited."""
    config = {"configurable": {"thread_id": project_id}}
    state_snapshot = graph.get_state(config)
    if not state_snapshot.values:
        raise HTTPException(status_code=404, detail="Project not found")

    status = _db_status(project_id)
    if status not in ("error_paused", "rate_limited"):
        raise HTTPException(status_code=409, detail=f"Project is '{status}', not in a recoverable error state")

    values = state_snapshot.values
    row_failed_agent, row_failure = get_failure(project_id)
    failed_agent = (row_failed_agent or values.get("failed_agent")
                    or (state_snapshot.next[0] if state_snapshot.next else ""))
    cancel_auto_retry(project_id)

    if request.action == "cancel":
        clear_failure(project_id)
        update_project_status(project_id, "cancelled", "cancelled")
        await manager.broadcast(project_id, {"type": "project_cancelled", "project_id": project_id})
        return {"status": "cancelled"}

    if request.action == "retry":
        # No graph write here either — the pending task IS the retry. Counting
        # the attempt on the project row instead of in retry_counts keeps the
        # checkpoint untouched, which is the whole reason a retry can re-enter
        # at all. The row's counter is cleared once a node actually completes.
        context = dict(row_failure or {})
        context["manual_retries"] = context.get("manual_retries", 0) + 1
        record_failure(project_id, failed_agent, context)
        update_project_status(project_id, "running", failed_agent)
        asyncio.create_task(run_graph_background(project_id, config))
        return {
            "status": "retrying",
            "agent": failed_agent,
            "retry_count": context["manual_retries"],
            "retry_cap_warning": context["manual_retries"] >= 3,
        }

    if request.action == "skip":
        if failed_agent not in SKIPPABLE_AGENTS:
            raise HTTPException(
                status_code=400,
                detail=f"'{failed_agent}' cannot be skipped — every downstream stage needs its output. Retry or cancel instead.",
            )
        reason = (row_failure or values.get("failure_context") or {}).get("message", "unknown failure")[:200]
        update = SKIPPABLE_AGENTS[failed_agent](reason)
        history = list(values.get("stage_history") or [])
        history.append({
            "stage": failed_agent,
            "attempt": 1 + sum(1 for entry in history if entry.get("stage") == failed_agent),
            "trigger": "skipped",
            "gate_origin": None,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        })
        update["stage_history"] = history
        update["log"] = list(values.get("log") or []) + [
            f"{failed_agent}_agent: SKIPPED after failure — {reason}"
        ]
        # Improvement 02: a skipped stage is the run's loudest reduced mode —
        # counted in the same account as every other fail-open path.
        from app.observability import degraded as _degraded
        update["degraded_events"] = _degraded.merge(
            values.get("degraded_events"), {f"agent_skipped:{failed_agent}": 1})
        update["failed_agent"] = ""
        update["failure_context"] = None
        clear_failure(project_id)
        # as_node: the checkpoint records this as the failed node's own output,
        # so `next` advances past it and streaming resumes with the next stage.
        node = state_snapshot.next[0] if state_snapshot.next else failed_agent
        graph.update_state(config, update, as_node=node)
        update_project_status(project_id, "running", failed_agent)
        await manager.broadcast(project_id, {"type": "agent_skipped", "agent": failed_agent})
        asyncio.create_task(run_graph_background(project_id, config))
        return {"status": "skipped", "agent": failed_agent}

    raise HTTPException(status_code=400, detail="action must be retry, skip, or cancel")


# ── Per-file AI fix (Gate 4) ─────────────────────────────────────────────────
# Ponytail: fixes run as a standalone LLM call + graph.update_state() while the
# graph stays paused at gate 4 — resuming the graph would re-run coder nodes and
# re-fire the interrupt cycle for a single-file change. Focused context only:
# the file's plan task, its QA findings, its current content, and the files it
# requires. Dependents are warned about in the UI, never auto-regenerated.

MAX_FIXES_PER_FILE = 3

PROMPTS_DIR = Path(__file__).resolve().parents[3] / "prompts"
FIX_AGENT_BY_PHASE = {
    "database": ("database", "database_agent.md"),
    "backend": ("backend_code", "backend_coder_agent.md"),
    "frontend": ("frontend_code", "frontend_coder_agent.md"),
    "devops": ("devops", "devops_agent.md"),
}

QA_FINDING_RE = re.compile(r"- \*\*File\*\*: `([^`]+)`\n\s*- \*Issue\*: (.+)")


class FileFixRequest(BaseModel):
    filepath: str = Field(..., description="Generated file to regenerate")
    instruction: str = Field(..., min_length=5, description="What needs to be fixed")


def _phase_for_file(filepath: str, tasks: list) -> tuple:
    """Return (task, phase) for a filepath; falls back to a path heuristic for custom/devops files."""
    task = next((t for t in tasks if isinstance(t, dict) and t.get("filepath") == filepath), None)
    if task and task.get("phase") in FIX_AGENT_BY_PHASE:
        return task, task["phase"]
    if filepath.startswith("frontend/") and not filepath.lower().endswith(("dockerfile", ".conf")):
        return task, "frontend"
    if filepath.startswith("backend/"):
        return task, "backend"
    return task, "devops"


@router.post("/{project_id}/files/fix")
def fix_project_file(project_id: str, request: FileFixRequest):
    config = {"configurable": {"thread_id": project_id}}
    state_snapshot = graph.get_state(config)
    if not state_snapshot.values:
        raise HTTPException(status_code=404, detail="Project not found")
    if not state_snapshot.next or state_snapshot.next[0] != "human_gate_4":
        raise HTTPException(status_code=400, detail="Per-file fixes are only available at gate 4")

    values = state_snapshot.values
    generated_files = dict(values.get("generated_files", {}))
    filepath = request.filepath
    if filepath not in generated_files:
        raise HTTPException(status_code=404, detail=f"No generated file at {filepath}")

    fix_counts = dict(values.get("fix_counts", {}) or {})
    if fix_counts.get(filepath, 0) >= MAX_FIXES_PER_FILE:
        raise HTTPException(
            status_code=400,
            detail=f"Fix limit reached for {filepath} ({MAX_FIXES_PER_FILE} per file). Edit it manually after download.",
        )

    try:
        tasks = json.loads(values.get("implementation_plan", "[]"))
        if not isinstance(tasks, list):
            tasks = []
    except (json.JSONDecodeError, TypeError):
        tasks = []

    task, phase = _phase_for_file(filepath, tasks)
    agent_type, prompt_file = FIX_AGENT_BY_PHASE[phase]
    system_prompt = (PROMPTS_DIR / prompt_file).read_text(encoding="utf-8")

    qa_findings = [
        f"- {desc}" for path, desc in QA_FINDING_RE.findall(values.get("qa_report", ""))
        if path.split(":")[0] == filepath
    ]

    from app.agents.utils import get_completed_file_content, truncate_for_context
    requires_context = ""
    if task and task.get("requires"):
        requires_context = get_completed_file_content(generated_files, task["requires"], tasks)

    user_content = f"""You are fixing ONE existing file in the project "{values.get('project_name', '')}".

FILE: {filepath}
ORIGINAL TASK: {task.get('description', 'No plan task recorded for this file.') if task else 'No plan task recorded for this file.'}

QA FINDINGS FOR THIS FILE:
{chr(10).join(qa_findings) if qa_findings else 'None recorded.'}

FIX REQUESTED BY THE USER:
{request.instruction}

{truncate_for_context(requires_context, max_chars=6000)}

CURRENT FILE CONTENT:
{truncate_for_context(generated_files[filepath], max_chars=12000)}

Apply the requested fix. Output ONLY the complete corrected file content — no explanation, no markdown fences, no preamble. Keep everything that is not related to the fix unchanged."""

    from app.utils.code_cleaner import strip_code_fences
    from app.llm_router import call_llm
    from app.utils.file_writer import write_project_file

    try:
        fixed = strip_code_fences(call_llm(
            [{"role": "system", "content": system_prompt}, {"role": "user", "content": user_content}],
            agent_type,
            max_tokens=4000,
            project_id=project_id,
            label=filepath,
            # Never cached. Re-requesting a fix with the SAME instruction means
            # "that did not work, try again" — replaying the previous answer is
            # the one thing the user is certain not to want.
            use_cache=False,
        ))
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Fix generation failed: {e}")

    if len(fixed.strip()) < 20:
        raise HTTPException(status_code=502, detail="Model returned empty or near-empty content; file left unchanged")

    result = write_project_file(project_id, filepath, fixed)
    if not result["success"]:
        raise HTTPException(status_code=500, detail=f"Failed to write fixed file: {result['error']}")

    previous_content = generated_files[filepath]
    previous_versions = dict(values.get("previous_versions", {}) or {})
    previous_versions[filepath] = previous_content
    generated_files[filepath] = fixed
    fix_counts[filepath] = fix_counts.get(filepath, 0) + 1
    # Mirror into retry_counts so per-file fixes and stage regenerations share
    # one counter system (fix_counts stays the cap authority for this endpoint).
    retry_counts = dict(values.get("retry_counts", {}) or {})
    retry_counts[f"file_fix:{filepath}"] = fix_counts[filepath]
    log = list(values.get("log", [])) + [
        f"file_fix: regenerated {filepath} via {agent_type} agent (fix {fix_counts[filepath]}/{MAX_FIXES_PER_FILE})"
    ]

    graph.update_state(config, {
        "generated_files": generated_files,
        "previous_versions": previous_versions,
        "fix_counts": fix_counts,
        "retry_counts": retry_counts,
        "log": log,
    })

    manager.broadcast_sync(project_id, {
        "type": "file_fixed",
        "filepath": filepath,
        "fix_count": fix_counts[filepath],
        "fixes_remaining": MAX_FIXES_PER_FILE - fix_counts[filepath],
    })

    return {
        "status": "fixed",
        "filepath": filepath,
        "fix_count": fix_counts[filepath],
        "fixes_remaining": MAX_FIXES_PER_FILE - fix_counts[filepath],
        "previous_content": previous_content,
        "content": fixed,
    }


# ── Generated-file browsing & download (Gate 4) ─────────────────────────────
# Ponytail: file contents are lazy-fetched per file from disk on click rather
# than shipped in one payload — GET /projects/{id} already carries the full
# generated_files dict and must not balloon further; per-click latency on a
# local disk read is negligible and the frontend caches viewed files.

LANGUAGE_BY_EXT = {
    ".py": "python", ".js": "javascript", ".jsx": "jsx", ".ts": "typescript",
    ".tsx": "tsx", ".sql": "sql", ".md": "markdown", ".json": "json",
    ".yml": "yaml", ".yaml": "yaml", ".sh": "bash", ".html": "html",
    ".css": "css", ".conf": "nginx", ".toml": "toml", ".txt": "text",
}


def infer_language(filepath: str) -> str:
    name = Path(filepath).name.lower()
    if name == "dockerfile":
        return "docker"
    if name.startswith(".env"):
        return "bash"
    return LANGUAGE_BY_EXT.get(Path(filepath).suffix.lower(), "text")


def _project_output_dir(project_id: str) -> Path:
    directory = (Path(OUTPUTS_ROOT) / project_id).resolve()
    if not directory.is_dir():
        raise HTTPException(status_code=404, detail="No generated files for this project")
    return directory


def walk_generated_files(directory: Path):
    """Every current generated file, skipping the restart archive.

    Single walker shared by the file list, the ZIP and the PDF export — archived
    files live under outputs/{id}/.archived/ so the delete cascade reclaims them
    for free, which means every reader of this directory must skip them or a
    restart would silently re-introduce discarded files into the download.
    """
    for path in sorted(directory.rglob("*")):
        if path.is_file() and ARCHIVE_DIRNAME not in path.relative_to(directory).parts:
            yield path


@router.get("/{project_id}/metrics")
def get_project_metrics(project_id: str):
    """Per-run token/latency rollup for the metrics panel.

    Projects created before Day 23 have no rows; run_summary returns
    has_metrics=False with zeroed totals so the UI renders an explanation
    instead of an error.

    Provider budgets are process-wide rather than per-project: quota is shared
    across every run, so "how much is left today" is the same question whichever
    project asks it.
    """
    from app.llm_router import daily_budget_report

    summary = metrics_store.run_summary(project_id)
    summary["providers"] = daily_budget_report()
    try:
        values = graph.get_state({"configurable": {"thread_id": project_id}}).values
        summary["fast_mode"] = bool((values or {}).get("fast_mode"))
        summary["degraded_events"] = (values or {}).get("degraded_events") or {}
        summary["qa_overlap_ratio"] = (values or {}).get("qa_overlap_ratio")
    except Exception:                           # noqa: BLE001 — a panel detail
        summary["fast_mode"] = False
        summary["degraded_events"] = {}
        summary["qa_overlap_ratio"] = None
    return summary


@router.delete("/{project_id}/cache")
def clear_project_cache(project_id: str):
    """Drop this project's cached responses so the next run regenerates.

    The escape hatch for when a user genuinely wants fresh output rather than a
    faithful replay — the cache is otherwise deliberately sticky.
    """
    return {"cleared": llm_cache.clear(project_id), "scope": project_id}


@router.delete("/cache/all")
def clear_all_cache():
    return {"cleared": llm_cache.clear(), "scope": "global"}


@router.get("/{project_id}/files")
def list_project_files(project_id: str):
    """Flat list of generated files with per-file metadata (tree is built client-side)."""
    directory = _project_output_dir(project_id)
    files = []
    for path in walk_generated_files(directory):
        rel = path.relative_to(directory).as_posix()
        try:
            line_count = len(path.read_text(encoding="utf-8").splitlines())
        except UnicodeDecodeError:
            line_count = None
        files.append({
            "path": rel,
            "size_bytes": path.stat().st_size,
            "line_count": line_count,
            "language": infer_language(rel),
        })
    return {
        "files": files,
        "total_files": len(files),
        "total_lines": sum(f["line_count"] or 0 for f in files),
        "total_bytes": sum(f["size_bytes"] for f in files),
    }


@router.get("/{project_id}/files/content")
def get_project_file_content(project_id: str, path: str):
    """Raw content of one generated file. Rejects path traversal outside the project dir."""
    directory = _project_output_dir(project_id)
    target = (directory / path).resolve()
    if directory not in target.parents:
        raise HTTPException(status_code=400, detail="Invalid file path")
    if not target.is_file():
        raise HTTPException(status_code=404, detail=f"File not found: {path}")
    try:
        return PlainTextResponse(target.read_text(encoding="utf-8"))
    except UnicodeDecodeError:
        raise HTTPException(status_code=400, detail=f"File is not valid UTF-8: {path}")


@router.get("/{project_id}/summary.pdf")
def export_summary_pdf(project_id: str):
    """One-to-two page handover summary. Works on partial projects: a cancelled
    run with no files, no QA and no metrics still exports a sensible page."""
    state_snapshot = graph.get_state({"configurable": {"thread_id": project_id}})
    if not state_snapshot.values:
        raise HTTPException(status_code=404, detail="Project not found")

    state = serialize_project_state(state_snapshot, project_id)

    # Files are optional — a project that never generated any has no directory.
    files = []
    directory = Path(OUTPUTS_ROOT) / project_id
    if directory.is_dir():
        for path in walk_generated_files(directory.resolve()):
            rel = path.relative_to(directory.resolve()).as_posix()
            try:
                line_count = len(path.read_text(encoding="utf-8").splitlines())
            except (UnicodeDecodeError, OSError):
                line_count = None
            files.append({"path": rel, "size_bytes": path.stat().st_size, "line_count": line_count})

    try:
        metrics = metrics_store.run_summary(project_id)
    except Exception as e:                                    # noqa: BLE001
        print(f"[PDF] metrics unavailable for {project_id}: {e}", flush=True)
        metrics = {}

    from app.utils.summary_pdf import build_summary_pdf
    buffer = build_summary_pdf(state, files, metrics, project_id)

    raw_name = state.get("project_name") or project_id
    safe_name = re.sub(r"[^A-Za-z0-9._-]+", "-", raw_name).strip("-.") or project_id
    return StreamingResponse(
        buffer,
        media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="{safe_name}-summary.pdf"'},
    )


@router.get("/{project_id}/download")
def download_project_zip(project_id: str):
    """ZIP of everything under outputs/{project_id}/, skipping non-UTF-8 files with a warning."""
    directory = _project_output_dir(project_id)

    state_values = graph.get_state({"configurable": {"thread_id": project_id}}).values or {}
    raw_name = state_values.get("project_name") or project_id
    safe_name = re.sub(r"[^A-Za-z0-9._-]+", "-", raw_name).strip("-.") or project_id

    buffer = io.BytesIO()
    file_count = 0
    total_bytes = 0
    skipped = []
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as zf:
        for path in walk_generated_files(directory):
            rel = path.relative_to(directory).as_posix()
            data = path.read_bytes()
            try:
                data.decode("utf-8")
            except UnicodeDecodeError:
                skipped.append(rel)
                continue
            zf.writestr(f"{safe_name}/{rel}", data)
            file_count += 1
            total_bytes += len(data)

    if skipped:
        print(f"[Download] {project_id}: skipped {len(skipped)} non-UTF-8 files: {skipped}", flush=True)
    print(f"[Download] {project_id}: zipped {file_count} files, {total_bytes} bytes", flush=True)

    buffer.seek(0)
    return StreamingResponse(
        buffer,
        media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="{safe_name}.zip"'},
    )
