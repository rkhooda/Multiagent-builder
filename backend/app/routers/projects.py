import uuid
import asyncio
import json
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field
from typing import List, Dict, Optional
from concurrent.futures import ThreadPoolExecutor
from app.graph.pipeline import graph
from app.graph.state import ProjectState
from app.core.connection_manager import manager
from app.core.database import insert_project, update_project_status, get_all_projects

router = APIRouter(prefix="/api/projects", tags=["projects"])

class OptionalSections(BaseModel):
    existing_solutions: bool = False
    target_users: bool = False
    market_risks: bool = False

class ProjectCreateRequest(BaseModel):
    brief: str
    project_name: str
    optional_sections: Optional[OptionalSections] = None

class ProjectResumeRequest(BaseModel):
    decision: str = Field(..., description="Decision: approve, edit, back, or reject")
    feedback: str = Field("", description="Optional human feedback")

VALID_DECISIONS = {"approve", "edit", "back", "reject"}
# Gates that support 'back' navigation to an earlier stage (Day 16 extends this).
BACK_CAPABLE_GATES = {"human_gate_3"}

EDITABLE_STATE_FIELDS = {"requirements_doc", "architecture_doc", "implementation_plan"}

class ProjectStateEditRequest(BaseModel):
    field: str = Field(..., description="State field to overwrite")
    content: str = Field(..., description="New content for the field")
    excluded_tasks: Optional[List[Dict]] = Field(
        None,
        description="Tasks cut from the plan (only meaningful with field=implementation_plan); kept as an audit trail",
    )


def validate_plan_edit(content: str) -> list[str]:
    """Validate an edited implementation plan: task schema, unique ids, in-plan dependencies.

    Validates against TaskSchema but stores the submitted JSON verbatim, so extra
    keys like "custom": true on user-added tasks survive the round trip.
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
    for raw in tasks:
        if not isinstance(raw, dict):
            continue
        for dep in raw.get("requires", []) or []:
            if dep not in id_set:
                errors.append(
                    f"Task {raw.get('id', '?')} requires '{dep}' which is not in the submitted plan"
                )

    return errors

executor = ThreadPoolExecutor(max_workers=10)

def get_next_event(iterator):
    try:
        return next(iterator)
    except StopIteration:
        return None

async def run_graph_background(project_id: str, config: dict, initial_state=None):
    loop = asyncio.get_running_loop()
    current_agent = "unknown"
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
        error_message = str(e)
        save_error_to_state(config, error_message)
        update_project_status(project_id, "error", current_agent)
        await manager.broadcast(project_id, {
            "type": "error",
            "agent": current_agent,
            "message": error_message
        })

def serialize_project_state(state_snapshot, project_id: str) -> dict:
    """Centralized serialization of LangGraph state snapshot to API response."""
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

    
    return {
        "project_id": values.get("project_id", project_id),
        "brief": values.get("brief", ""),
        "project_name": values.get("project_name", ""),
        "research_report": values.get("research_report", ""),
        "requirements_doc": values.get("requirements_doc", ""),
        "tech_stack": values.get("tech_stack", ""),
        "architecture_doc": values.get("architecture_doc", ""),
        "implementation_plan": values.get("implementation_plan", ""),
        "excluded_tasks": values.get("excluded_tasks", []),
        "file_list": values.get("file_list", []),
        "generated_files": values.get("generated_files", {}),
        "qa_report": values.get("qa_report", ""),
        "qa_issues_count": values.get("qa_issues_count", 0),
        "devops_files": values.get("devops_files", {}),
        "previous_versions": values.get("previous_versions", {}),
        "current_stage": values.get("current_stage", ""),
        "human_feedback": values.get("human_feedback", ""),
        "human_decision": values.get("human_decision", ""),
        "log": values.get("log", []),
        "errors": values.get("errors", []),
        "status": status,
        "next_gate": next_gate
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
def list_projects():
    """Retrieve all projects from the SQLite database."""
    return get_all_projects()

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
        replan_after_architecture=False,
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
        validation_errors = validate_plan_edit(request.content)
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

    if request.decision not in VALID_DECISIONS:
        raise HTTPException(status_code=400, detail=f"decision must be one of {sorted(VALID_DECISIONS)}")

    if request.decision == "back" and state_snapshot.next[0] not in BACK_CAPABLE_GATES:
        raise HTTPException(
            status_code=400,
            detail=f"'back' is only supported at {sorted(BACK_CAPABLE_GATES)} for now"
        )

    try:
        # Update SQLite database project status to running
        current_stage = state_snapshot.values.get("current_stage", "resume")
        update_project_status(project_id, "running", current_stage)
        
        # Update state with human decision and feedback
        graph.update_state(
            config,
            {
                "human_decision": request.decision,
                "human_feedback": request.feedback
            }
        )

        # Resume the graph in a background task
        asyncio.create_task(run_graph_background(project_id, config))
        
        return {"status": "resumed"}
    except Exception as e:
        save_error_to_state(config, str(e))
        raise HTTPException(status_code=500, detail=str(e))
