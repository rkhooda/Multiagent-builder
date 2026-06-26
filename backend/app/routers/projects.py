import uuid
import asyncio
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field
from typing import List, Dict, Optional
from concurrent.futures import ThreadPoolExecutor
from app.graph.pipeline import graph
from app.graph.state import ProjectState
from app.core.connection_manager import manager
from app.core.database import insert_project, update_project_status, get_all_projects

router = APIRouter(prefix="/api/projects", tags=["projects"])

class ProjectCreateRequest(BaseModel):
    brief: str
    project_name: str

class ProjectResumeRequest(BaseModel):
    decision: str = Field(..., description="Decision: approve, edit, or reject")
    feedback: str = Field("", description="Optional human feedback")

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
                preview_fields = [
                    "research_report", "requirements_doc", "tech_stack",
                    "architecture_doc", "implementation_plan", "qa_report"
                ]
                for field in preview_fields:
                    if field in node_output and node_output[field]:
                        preview = str(node_output[field])[:200]
                        break
                
                if not preview and "log" in node_output and node_output["log"]:
                    preview = str(node_output["log"][-1])[:200]
                
                if not preview:
                    preview = f"Completed agent node {node_name}"
                
                await manager.broadcast(project_id, {
                    "type": "agent_complete",
                    "agent": node_name,
                    "stage": stage,
                    "preview": preview
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
        "file_list": values.get("file_list", []),
        "generated_files": values.get("generated_files", {}),
        "qa_report": values.get("qa_report", ""),
        "devops_files": values.get("devops_files", {}),
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
    
    # Build initial ProjectState
    initial_state = ProjectState(
        project_id=project_id,
        brief=request.brief,
        project_name=request.project_name,
        research_report="",
        requirements_doc="",
        tech_stack="",
        architecture_doc="",
        implementation_plan="",
        file_list=[],
        generated_files={},
        qa_report="",
        devops_files={},
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

@router.post("/{project_id}/resume")
async def resume_project(project_id: str, request: ProjectResumeRequest):
    config = {"configurable": {"thread_id": project_id}}
    state_snapshot = graph.get_state(config)
    
    if not state_snapshot.values:
        raise HTTPException(status_code=404, detail="Project not found")
        
    if not state_snapshot.next:
        raise HTTPException(status_code=400, detail="Project is already completed and cannot be resumed")
        
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
