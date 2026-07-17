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

# Decision -> target node per gate. Adding a back edge to another gate later
# (Day 16) is one new dict entry, not a new routing function.
GATE_ROUTES = {
    "human_gate_1": {"edit": "requirements", "reject": "cancelled", "approve": "architecture"},
    "human_gate_2": {"edit": "architecture", "reject": "cancelled", "approve": "planning"},
    "human_gate_3": {
        "edit": "planning",        # replan with feedback
        "back": "architecture",    # regenerate architecture, then auto-replan (skips gate 2)
        "reject": "cancelled",
        "approve": "frontend_code",
    },
    # Final review: per-file fixes happen OUTSIDE the graph (files/fix endpoint
    # with update_state while the gate stays paused), so no edit/back here.
    "human_gate_4": {"reject": "cancelled", "approve": "end"},
}

def make_gate_router(gate_name: str):
    routes = GATE_ROUTES[gate_name]
    def route(state: ProjectState) -> str:
        return routes.get(state.get("human_decision", ""), routes["approve"])
    return route

def route_after_architecture(state: ProjectState) -> str:
    # After a gate-3 'back' rerun the old plan is stale — flow straight to
    # planning for a fresh plan instead of pausing at gate 2 again.
    return "planning" if state.get("replan_after_architecture") else "human_gate_2"

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
    make_gate_router("human_gate_1"),
    {"architecture": "architecture", "requirements": "requirements", "cancelled": "cancelled"},
)
workflow.add_conditional_edges(
    "architecture",
    route_after_architecture,
    {"human_gate_2": "human_gate_2", "planning": "planning"},
)
workflow.add_conditional_edges(
    "human_gate_2",
    make_gate_router("human_gate_2"),
    {"planning": "planning", "architecture": "architecture", "cancelled": "cancelled"},
)
workflow.add_edge("cancelled", END)
workflow.add_edge("planning", "human_gate_3")
workflow.add_conditional_edges(
    "human_gate_3",
    make_gate_router("human_gate_3"),
    {
        "frontend_code": "frontend_code",
        "planning": "planning",
        "architecture": "architecture",
        "cancelled": "cancelled",
    },
)
workflow.add_edge("frontend_code", "backend_code")
workflow.add_edge("backend_code", "database")
workflow.add_edge("database", "qa")
workflow.add_edge("qa", "devops")
workflow.add_edge("devops", "human_gate_4")
workflow.add_conditional_edges(
    "human_gate_4",
    make_gate_router("human_gate_4"),
    {"end": END, "cancelled": "cancelled"},
)

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
