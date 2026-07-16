import sqlite3
import os
from typing import Dict
from langgraph.graph import StateGraph, START, END
from langgraph.checkpoint.sqlite import SqliteSaver
from app.graph.state import ProjectState
from app.agents.research_agent import research_agent
from app.agents.requirements_agent import requirements_agent
from app.agents.architecture_agent import architecture_agent
from app.agents.planning_agent import planning_agent
from app.agents.database_agent import database_agent
from app.agents.devops_agent import devops_agent
from app.agents.qa_agent import qa_agent

def human_gate_1(state: ProjectState) -> Dict:
    # Empty pass-through function. LangGraph pauses before this node.
    return {}

def human_gate_2(state: ProjectState) -> Dict:
    # Empty pass-through function. LangGraph pauses before this node.
    return {}

def human_gate_3(state: ProjectState) -> Dict:
    # Empty pass-through function. LangGraph pauses before this node.
    return {}

def cancelled(state: ProjectState) -> Dict:
    log = list(state.get("log") or [])
    log.append("project cancelled by user at approval gate")
    return {"log": log, "current_stage": "cancelled"}

def route_gate_1(state: ProjectState) -> str:
    decision = state.get("human_decision", "")
    if decision == "edit":
        return "requirements"
    if decision == "reject":
        return "cancelled"
    return "architecture"

def route_gate_2(state: ProjectState) -> str:
    decision = state.get("human_decision", "")
    if decision == "edit":
        return "architecture"
    if decision == "reject":
        return "cancelled"
    return "planning"

def frontend_code(state: ProjectState) -> Dict:
    current_log = state.get("log") or []
    return {
        "log": current_log + ["frontend_code ran"],
        "current_stage": "frontend_code"
    }

def backend_code(state: ProjectState) -> Dict:
    current_log = state.get("log") or []
    return {
        "log": current_log + ["backend_code ran"],
        "current_stage": "backend_code"
    }

def human_gate_4(state: ProjectState) -> Dict:
    # Empty pass-through function. LangGraph pauses before this node.
    return {}

# 2. Build the state graph
workflow = StateGraph(ProjectState)

# Add all nodes
workflow.add_node("research", research_agent)
workflow.add_node("human_gate_1", human_gate_1)
workflow.add_node("requirements", requirements_agent)
workflow.add_node("human_gate_2", human_gate_2)
workflow.add_node("architecture", architecture_agent)
workflow.add_node("planning", planning_agent)
workflow.add_node("human_gate_3", human_gate_3)
workflow.add_node("frontend_code", frontend_code)
workflow.add_node("backend_code", backend_code)
workflow.add_node("database", database_agent)
workflow.add_node("qa", qa_agent)
workflow.add_node("devops", devops_agent)
workflow.add_node("human_gate_4", human_gate_4)
workflow.add_node("cancelled", cancelled)

# Wire all edges
# Gate 1 reviews research + requirements together; Gate 2 reviews architecture.
workflow.add_edge(START, "research")
workflow.add_edge("research", "requirements")
workflow.add_edge("requirements", "human_gate_1")
workflow.add_conditional_edges(
    "human_gate_1",
    route_gate_1,
    {"architecture": "architecture", "requirements": "requirements", "cancelled": "cancelled"},
)
workflow.add_edge("architecture", "human_gate_2")
workflow.add_conditional_edges(
    "human_gate_2",
    route_gate_2,
    {"planning": "planning", "architecture": "architecture", "cancelled": "cancelled"},
)
workflow.add_edge("cancelled", END)
workflow.add_edge("planning", "human_gate_3")
workflow.add_edge("human_gate_3", "frontend_code")
workflow.add_edge("frontend_code", "backend_code")
workflow.add_edge("backend_code", "database")
workflow.add_edge("database", "qa")
workflow.add_edge("qa", "devops")
workflow.add_edge("devops", "human_gate_4")
workflow.add_edge("human_gate_4", END)

# 3. Setup SQLite checkpointer
# Determine path to projects.db in the backend root folder
backend_dir = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
db_path = os.path.join(backend_dir, "projects.db")

conn = sqlite3.connect(db_path, check_same_thread=False)
# Enable WAL (Write-Ahead Logging) mode and optimize concurrency pragmas
conn.execute("PRAGMA journal_mode=WAL;")
conn.execute("PRAGMA busy_timeout=5000;")
memory = SqliteSaver(conn)

# Compile the graph with interrupt_before gates
graph = workflow.compile(
    checkpointer=memory,
    interrupt_before=["human_gate_1", "human_gate_2", "human_gate_3", "human_gate_4"]
)
