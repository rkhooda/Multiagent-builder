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

def human_gate_1(state: ProjectState) -> Dict:
    # Empty pass-through function. LangGraph pauses before this node.
    return {}

def human_gate_2(state: ProjectState) -> Dict:
    # Empty pass-through function. LangGraph pauses before this node.
    return {}

def human_gate_3(state: ProjectState) -> Dict:
    # Empty pass-through function. LangGraph pauses before this node.
    return {}

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

def qa(state: ProjectState) -> Dict:
    current_log = state.get("log") or []
    return {
        "log": current_log + ["qa ran"],
        "current_stage": "qa"
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
workflow.add_node("qa", qa)
workflow.add_node("devops", devops_agent)
workflow.add_node("human_gate_4", human_gate_4)

# Wire all edges
# research pauses at Gate 1, requirements pauses at Gate 2, then architecture continues
workflow.add_edge(START, "research")
workflow.add_edge("research", "human_gate_1")
workflow.add_edge("human_gate_1", "requirements")
workflow.add_edge("requirements", "human_gate_2")
workflow.add_edge("human_gate_2", "architecture")
workflow.add_edge("architecture", "planning")
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
