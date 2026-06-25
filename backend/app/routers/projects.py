import uuid
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field
from typing import List, Dict, Optional
from app.graph.pipeline import graph
from app.graph.state import ProjectState

router = APIRouter(prefix="/api/projects", tags=["projects"])

class ProjectCreateRequest(BaseModel):
    brief: str
    project_name: str

class ProjectResumeRequest(BaseModel):
    decision: str = Field(..., description="Decision: approve, edit, or reject")
    feedback: str = Field("", description="Optional human feedback")

@router.post("")
def create_project(request: ProjectCreateRequest):
    try:
        project_id = str(uuid.uuid4())
        
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
        
        config = {"configurable": {"thread_id": project_id}}
        
        # Run the graph using graph.stream() until the first interrupt
        for event in graph.stream(initial_state, config):
            pass
            
        # Get the updated state from checkpointer
        state_snapshot = graph.get_state(config)
        if not state_snapshot.values:
            raise HTTPException(status_code=500, detail="Failed to initialize project state")
            
        values = state_snapshot.values
        status = "awaiting_approval" if state_snapshot.next else "completed"
        
        return {
            "project_id": project_id,
            "status": status,
            "current_stage": values.get("current_stage", ""),
            "log": values.get("log", [])
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.get("/{project_id}")
def get_project(project_id: str):
    config = {"configurable": {"thread_id": project_id}}
    state_snapshot = graph.get_state(config)
    
    if not state_snapshot.values:
        raise HTTPException(status_code=404, detail="Project not found")
        
    values = state_snapshot.values
    status = "awaiting_approval" if state_snapshot.next else "completed"
    next_gate = state_snapshot.next[0] if state_snapshot.next else None
    
    return {
        "project_id": values.get("project_id"),
        "brief": values.get("brief"),
        "project_name": values.get("project_name"),
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

@router.post("/{project_id}/resume")
def resume_project(project_id: str, request: ProjectResumeRequest):
    config = {"configurable": {"thread_id": project_id}}
    state_snapshot = graph.get_state(config)
    
    if not state_snapshot.values:
        raise HTTPException(status_code=404, detail="Project not found")
        
    if not state_snapshot.next:
        raise HTTPException(status_code=400, detail="Project is already completed and cannot be resumed")
        
    # Update state with human decision and feedback
    graph.update_state(
        config,
        {
            "human_decision": request.decision,
            "human_feedback": request.feedback
        }
    )
    
    # Resume the graph using graph.stream()
    for event in graph.stream(None, config):
        pass
        
    # Fetch the updated state
    updated_snapshot = graph.get_state(config)
    values = updated_snapshot.values
    status = "awaiting_approval" if updated_snapshot.next else "completed"
    next_gate = updated_snapshot.next[0] if updated_snapshot.next else None
    
    return {
        "project_id": project_id,
        "status": status,
        "current_stage": values.get("current_stage", ""),
        "log": values.get("log", []),
        "next_gate": next_gate
    }
