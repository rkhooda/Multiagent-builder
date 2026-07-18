import sqlite3
import os
import traceback
from datetime import datetime, timezone
from typing import Dict
from langgraph.graph import StateGraph, START, END
from langgraph.checkpoint.sqlite import SqliteSaver
from app.exceptions import AgentError, LLMError
from app.graph.state import ProjectState
from app.agents.research_agent import research_agent
from app.agents.requirements_agent import requirements_agent
from app.agents.architecture_agent import architecture_agent
from app.agents.planning_agent import planning_agent
from app.agents.database_agent import database_agent
from app.agents.devops_agent import devops_agent
from app.agents.qa_agent import qa_agent
from app.agents.frontend_coder_agent import frontend_coder_agent
from app.agents.utils import regeneration_target

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

# Decision -> target node per gate.
GATE_ROUTES = {
    "human_gate_1": {
        "edit": "requirements",
        "back": "research",        # regenerate research, then fresh requirements, re-pause at gate 1
        "reject": "cancelled",
        "approve": "architecture",
    },
    "human_gate_2": {
        "edit": "architecture",
        "back": "requirements",    # regenerate requirements, then fresh architecture (skips gate 1)
        "reject": "cancelled",
        "approve": "planning",
    },
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

def route_after_requirements(state: ProjectState) -> str:
    # After a gate-2 'back' rerun the user wants to land back at gate 2 —
    # flow straight to architecture instead of re-pausing at gate 1.
    return "architecture" if state.get("skip_gate_1") else "human_gate_1"

def route_after_architecture(state: ProjectState) -> str:
    # After a gate-3 'back' rerun the old plan is stale — flow straight to
    # planning for a fresh plan instead of pausing at gate 2 again.
    return "planning" if state.get("replan_after_architecture") else "human_gate_2"

# ── Cascade invalidation & stage history ─────────────────────────────────────
# One stage-order map used by both invalidate_downstream (what becomes stale on
# a back/edit re-run) and the stage_node wrapper (which field to snapshot).
# All coder/QA/devops artifacts count as one "code" stage — they regenerate
# together and are never a feedback target themselves.
STAGE_ORDER = ["research", "requirements", "architecture", "planning", "code"]
STAGE_ARTIFACTS = {
    "research": {"research_report": ""},
    "requirements": {"requirements_doc": "", "tech_stack": ""},
    "architecture": {"architecture_doc": "", "file_list": []},
    "planning": {"implementation_plan": "", "excluded_tasks": []},
    "code": {"generated_files": {}, "qa_report": "", "qa_issues_count": 0,
             "devops_files": {}, "fix_counts": {}},
}
# Doc fields snapshotted into previous_versions before clearing (diff source).
SNAPSHOT_FIELDS = {"research_report", "requirements_doc", "architecture_doc", "implementation_plan"}

# ── Skip policy ──────────────────────────────────────────────────────────────
# Agents whose failure the pipeline can survive, and the explicit placeholder
# written to state when the user chooses to skip them. Everything else
# (requirements/architecture/planning/database) starves the stages downstream —
# /recover refuses to skip those. Skipping is always a USER decision: an
# unrecoverable failure pauses the project (error_paused) rather than
# auto-skipping, so a degraded run never happens silently.
SKIPPABLE_AGENTS = {
    "research": lambda reason: {
        "research_report": f"[SKIPPED — research agent failed: {reason}]",
        "current_stage": "requirements",
    },
    "qa": lambda reason: {
        "qa_report": (
            "# QA Report\n\n[SKIPPED — QA agent failed: "
            f"{reason}]\n\nGenerated code was NOT reviewed."
        ),
        "qa_issues_count": -1,  # -1 = "not reviewed", never rendered as "0 issues"
        "current_stage": "devops",
    },
    "devops": lambda reason: {
        "devops_files": {},
        "current_stage": "human_gate_4",
    },
}

# Stage -> the output field a feedback re-run regenerates (drives snapshotting
# and feedback-field clearing in stage_node).
STAGE_PRIMARY_OUTPUT = {
    "research": "research_report",
    "requirements": "requirements_doc",
    "architecture": "architecture_doc",
    "planning": "implementation_plan",
}

def invalidate_downstream(state: dict, from_stage: str) -> dict:
    """State update that marks everything produced AFTER from_stage stale:
    snapshots doc artifacts into previous_versions (last snapshot only) and
    clears them. The target stage's own output is left intact for feedback
    injection. Called once, from the resume endpoint, for every edit/back."""
    update = {}
    previous_versions = dict(state.get("previous_versions") or {})
    for stage in STAGE_ORDER[STAGE_ORDER.index(from_stage) + 1:]:
        for field, empty in STAGE_ARTIFACTS[stage].items():
            current = state.get(field)
            if not current:
                continue
            if field in SNAPSHOT_FIELDS:
                previous_versions[field] = current
            update[field] = empty
    update["previous_versions"] = previous_versions
    return update

def stage_node(stage: str, agent_fn):
    """Wraps an agent node with the cross-cutting per-stage bookkeeping:
    - universal error boundary: LLMError subclasses pass through already
      classified; anything else is a bug in our own code, logged with its
      traceback and wrapped as AgentError (never auto-retried). The exception
      propagates out of graph.stream() so the checkpoint keeps next=this node —
      run_graph_background records/broadcasts the failure and /recover can
      retry or skip from exactly here.
    - snapshot the previous output + clear feedback fields when this stage was
      the feedback target (single implementation instead of one per agent)
    - append a stage_history entry (attempt count, trigger, gate origin)"""
    output_field = STAGE_PRIMARY_OUTPUT.get(stage)
    def node(state: ProjectState) -> Dict:
        previous_output = regeneration_target(state, output_field) if output_field else None
        try:
            result = agent_fn(state)
        except LLMError as e:
            e.agent_type = e.agent_type or stage
            raise
        except Exception as e:
            print(f"[Boundary] bug in {stage} agent:\n{traceback.format_exc()}", flush=True)
            raise AgentError(f"{type(e).__name__}: {e}", agent_type=stage) from e

        # This stage just succeeded — clear any recorded failure for it so a
        # recovered retry doesn't keep showing a stale error card.
        if state.get("failed_agent") == stage:
            result.setdefault("failed_agent", "")
            result.setdefault("failure_context", None)

        if previous_output is not None:
            previous_versions = dict(state.get("previous_versions") or {})
            previous_versions[output_field] = previous_output
            result["previous_versions"] = previous_versions
            result["human_feedback"] = ""
            result["human_decision"] = ""

        # regen_cycle (set at resume, cleared on approve/reject) attributes
        # every stage in a cascade to the decision + gate that triggered it.
        cycle = state.get("regen_cycle") or {}
        history = list(state.get("stage_history") or [])
        entry = {
            "stage": stage,
            "attempt": 1 + sum(1 for e in history if e.get("stage") == stage),
            "trigger": cycle.get("trigger", "initial"),
            "gate_origin": cycle.get("gate"),
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }
        # A coder agent reports partial per-file failures via a transient key;
        # fold it into this stage's history entry and drop it so it never
        # leaks into the persisted ProjectState channels.
        partial_failures = result.pop("partial_failures", None)
        if partial_failures:
            entry["failed_files"] = partial_failures
            if entry["trigger"] == "initial":
                entry["trigger"] = "partial"
        history.append(entry)
        result["stage_history"] = history
        return result
    node.__name__ = f"{stage}_node"
    return node

def backend_code(state: ProjectState) -> Dict:
    current_log = state.get("log") or []
    return {
        "log": current_log + ["backend_code ran"],
        "current_stage": "qa"
    }

def human_gate_4(state: ProjectState) -> Dict:
    # Empty pass-through function. LangGraph pauses before this node.
    return {}

# 2. Build the state graph
workflow = StateGraph(ProjectState)

# Add all nodes (agent nodes wrapped for stage history + feedback bookkeeping)
workflow.add_node("research", stage_node("research", research_agent))
workflow.add_node("human_gate_1", human_gate_1)
workflow.add_node("requirements", stage_node("requirements", requirements_agent))
workflow.add_node("human_gate_2", human_gate_2)
workflow.add_node("architecture", stage_node("architecture", architecture_agent))
workflow.add_node("planning", stage_node("planning", planning_agent))
workflow.add_node("human_gate_3", human_gate_3)
workflow.add_node("frontend_code", stage_node("frontend_code", frontend_coder_agent))
workflow.add_node("backend_code", stage_node("backend_code", backend_code))
workflow.add_node("database", stage_node("database", database_agent))
workflow.add_node("qa", stage_node("qa", qa_agent))
workflow.add_node("devops", stage_node("devops", devops_agent))
workflow.add_node("human_gate_4", human_gate_4)
workflow.add_node("cancelled", cancelled)

# Wire all edges
# Gate 1 reviews research + requirements together; Gate 2 reviews architecture.
workflow.add_edge(START, "research")
workflow.add_edge("research", "requirements")
workflow.add_conditional_edges(
    "requirements",
    route_after_requirements,
    {"human_gate_1": "human_gate_1", "architecture": "architecture"},
)
workflow.add_conditional_edges(
    "human_gate_1",
    make_gate_router("human_gate_1"),
    {
        "architecture": "architecture",
        "requirements": "requirements",
        "research": "research",
        "cancelled": "cancelled",
    },
)
workflow.add_conditional_edges(
    "architecture",
    route_after_architecture,
    {"human_gate_2": "human_gate_2", "planning": "planning"},
)
workflow.add_conditional_edges(
    "human_gate_2",
    make_gate_router("human_gate_2"),
    {
        "planning": "planning",
        "architecture": "architecture",
        "requirements": "requirements",
        "cancelled": "cancelled",
    },
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
# database runs BEFORE backend_code so ORM model files exist on disk/state
# when the backend coder generates routers that import them (Day 19 reorder).
workflow.add_edge("frontend_code", "database")
workflow.add_edge("database", "backend_code")
workflow.add_edge("backend_code", "qa")
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
