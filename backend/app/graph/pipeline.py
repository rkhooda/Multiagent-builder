import json
import sqlite3
import os
import traceback
from datetime import datetime, timezone
from typing import Dict
from langgraph.graph import StateGraph, START, END
from langgraph.checkpoint.sqlite import SqliteSaver
from app.core.paths import data_path
from app.exceptions import AgentError, LLMError
from app.graph.state import ProjectState
from app.agents.research_agent import research_agent
from app.agents.requirements_agent import requirements_agent
from app.agents.architecture_agent import architecture_agent
from app.agents.planning_agent import planning_agent
from app.agents.database_agent import database_agent
from app.agents.devops_agent import devops_agent
from app.agents.build_verify_agent import build_verify_agent
from app.agents.qa_agent import qa_agent
from app.agents.validation_pass import validation_pass
from app.agents.frontend_coder_agent import frontend_coder_agent
from app.agents.backend_coder_agent import backend_coder_agent
from app.agents.utils import regeneration_target
from app.utils.file_writer import write_project_file

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
    # stack_profile is NOT listed here on purpose: re-running requirements must
    # not silently discard a profile the user chose at Gate 1. The agent itself
    # leaves an explicit choice alone and only recomputes an "auto" one.
    "requirements": {"requirements_doc": "", "tech_stack": "", "profile_mismatch": ""},
    "architecture": {"architecture_doc": "", "file_list": []},
    "planning": {"implementation_plan": "", "excluded_tasks": []},
    "code": {"generated_files": {}, "qa_report": "", "qa_issues_count": 0,
             "devops_files": {}, "fix_counts": {}, "validation_report": {},
             # Improvement 02: a regenerated code phase gets a fresh fail-open
             # account — stale "QA batch failed" counts from a discarded
             # generation would misreport the run the user actually downloads.
             "degraded_events": {}},
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

def _prose(value):
    """Agent output that is already markdown. Written through untouched."""
    return value if isinstance(value, str) and value.strip() else None


def _structured(title: str):
    """Renderer for a field the pipeline holds as JSON or as a Python
    dict/list — plan, validation report, build verdicts, tech stack.

    Fenced rather than flattened into prose: these are consumed by people
    checking exact values (which task, which file, which verdict), and a
    prettified narrative of a data structure invites the reader to trust a
    paraphrase. `default=str` because build verdicts carry non-JSON types and a
    docs file must never be the thing that fails a run.
    """
    def render(value):
        if not value:
            return None
        if isinstance(value, str):
            # The plan and tech stack arrive as JSON STRINGS, usually on one
            # line. Re-indent them so the document is readable; fall back to the
            # raw string if it does not parse, because an unreadable document
            # still beats no document.
            try:
                text = json.dumps(json.loads(value), indent=2, default=str)
            except (ValueError, TypeError):
                text = value
        else:
            text = json.dumps(value, indent=2, default=str)
        return f"# {title}\n\n```json\n{text}\n```\n"
    return render


# Stage -> the documents it contributes to outputs/{project_id}/docs/, as
# (state field, filename, renderer). Mirrored to disk as each stage completes so
# the existing ZIP/PDF export picks them up from gate 1 onward, not only after
# code generation has written files.
#
# WHY a renderer per document rather than the `if stage == "planning"` branch
# this replaces: the pipeline holds these in three different shapes — markdown
# prose, JSON strings, and live dicts — and a per-stage special case worked only
# while there were exactly two. A list per stage also lets one agent contribute
# more than one document, which planning and research both do.
#
# The zip already ships whatever is on disk (walk_generated_files), so nothing
# in the export path needs to know these exist.
STAGE_DOCS = {
    "research": [
        ("research_report", "research.md", _prose),
        ("tech_stack", "tech-stack.md", _structured("Recommended Tech Stack")),
    ],
    "requirements": [
        ("requirements_doc", "requirements.md", _prose),
    ],
    "architecture": [
        ("architecture_doc", "architecture.md", _prose),
    ],
    "planning": [
        ("implementation_plan", "implementation-plan.md",
         _structured("Implementation Plan")),
        ("ui_contract", "ui-contract.md", _prose),
        # The audit trail for what was CUT at gate 3. Worth shipping precisely
        # because it explains absences in the code — a reader who cannot find a
        # feature should be able to see it was excluded rather than lost.
        ("excluded_tasks", "excluded-tasks.md",
         _structured("Tasks Excluded at Gate 3")),
    ],
    "validation": [
        ("validation_report", "validation-report.md",
         _structured("Automated Validation Report")),
    ],
    "qa": [
        ("qa_report", "qa-report.md", _prose),
    ],
    "build_verify": [
        ("build_verification", "build-verification.md",
         _structured("Build Verification")),
    ],
}

_DOCS_README = """# Project documents

Written by the agents that designed this project, in pipeline order. They are
the reasoning behind the code in `frontend/` and `backend/` — read them to find
out WHY something is built the way it is.

| File | Produced by | What it is |
|---|---|---|
| `research.md` | research agent | Domain findings and prior art behind the brief |
| `tech-stack.md` | research agent | The recommended stack, and why |
| `requirements.md` | requirements agent | Functional and non-functional requirements |
| `architecture.md` | architecture agent | System design: components, data model, endpoints |
| `implementation-plan.md` | planning agent | Every file to build, with dependencies |
| `ui-contract.md` | planning agent | Shared UI tokens and conventions given to every frontend file |
| `excluded-tasks.md` | gate 3 | Tasks deliberately cut — absences here are decisions, not losses |
| `validation-report.md` | validation pass | Automated syntax/import checks over the generated code |
| `qa-report.md` | QA agent | Review findings across the generated files |
| `build-verification.md` | build verification | Whether install, build and boot actually succeeded |

A file missing from this folder means that stage did not run or produced
nothing — the pipeline never writes a placeholder, so absence is information.
"""


def _persist_stage_doc(stage: str, project_id: str, result: dict) -> None:
    """Mirror this stage's documents into outputs/{project_id}/docs/.

    Never raises: a documentation write must not be able to fail a run that has
    already produced its artifacts. A failed write is logged and the stage still
    returns — the state field remains the source of truth either way.
    """
    specs = STAGE_DOCS.get(stage)
    if not specs or not project_id:
        return
    wrote = False
    for field, filename, render in specs:
        try:
            content = render(result.get(field))
        except Exception as e:                   # noqa: BLE001
            print(f"[Docs] could not render {filename} for {stage}: {e}", flush=True)
            continue
        if not content:
            continue
        write_project_file(project_id, f"docs/{filename}", content,
                           add_header=False, strip_fences=False)
        wrote = True
    # Written alongside the first real document rather than at run start, so a
    # project that never reached an agent does not ship an index of nothing.
    if wrote:
        write_project_file(project_id, "docs/README.md", _DOCS_README,
                           add_header=False, strip_fences=False)

def invalidate_downstream(state: dict, from_stage: str) -> dict:
    """State update that marks everything produced AFTER from_stage stale:
    snapshots doc artifacts into previous_versions (last snapshot only) and
    clears them. The target stage's own output is left intact for feedback
    injection. Called once, from the resume endpoint, for every edit/back."""
    # Improvement 02: any invalidation that can clear generated_files makes a
    # live QA stream's snapshots stale — drop it here, the one choke point
    # every edit/back resume already routes through. Idempotent and safe when
    # no stream exists.
    from app.agents import qa_stream
    qa_stream.discard(state.get("project_id", ""))

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

        # Improvement 02: fold any fail-open degradations recorded during this
        # stage (by workers, the router, or the QA stream — none of which may
        # touch state) into the run's visible account. One drain point for all
        # agents; events recorded during a FAILED stage stay in the collector
        # and are drained by the next stage that completes.
        from app.observability import degraded
        pending = degraded.drain(state.get("project_id", ""))
        if pending:
            result["degraded_events"] = degraded.merge(
                degraded.merge(state.get("degraded_events"), pending),
                result.get("degraded_events"))

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

        _persist_stage_doc(stage, state.get("project_id", ""), result)

        return result
    node.__name__ = f"{stage}_node"
    return node

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
workflow.add_node("backend_code", stage_node("backend_code", backend_coder_agent))
workflow.add_node("database", stage_node("database", database_agent))
# Day 22: batch syntax/import/artifact checks between the last file-producing
# coder and QA, so no broken file reaches the QA agent or the download ZIP.
workflow.add_node("validation", stage_node("validation", validation_pass))
workflow.add_node("qa", stage_node("qa", qa_agent))
workflow.add_node("devops", stage_node("devops", devops_agent))
# Build Verification: runs generated code for real (install/build/boot) in a
# disposable sandbox after devops produces the deploy files. Warn, never
# block — build_verify_agent never raises, so this node cannot fail the run.
workflow.add_node("build_verify", stage_node("build_verify", build_verify_agent))
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
workflow.add_edge("backend_code", "validation")
workflow.add_edge("validation", "qa")
workflow.add_edge("qa", "devops")
workflow.add_edge("devops", "build_verify")
workflow.add_edge("build_verify", "human_gate_4")
workflow.add_conditional_edges(
    "human_gate_4",
    make_gate_router("human_gate_4"),
    {"end": END, "cancelled": "cancelled"},
)

# 3. Setup SQLite checkpointer
db_path = data_path("projects.db")

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
