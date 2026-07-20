"""Day 24 lifecycle: status vocabulary, cold-start position, delete cascade.

Zero API cost — every check runs against real SQLite/checkpointer/disk stores
with throwaway ids. The delete test is the proof obligation from ponytail #3:
after a delete, all FOUR stores must be clean for that id.
"""
import asyncio
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.models import status as status_vocab
from app.core.database import (
    get_db_connection, insert_project, get_all_projects, update_project_status,
)
from app.graph.pipeline import graph
from app.observability import metrics_store
from app.routers.projects import (
    delete_project, derive_position, project_output_path, _live_runs,
    RESTART_ENTRY, restart_cost_estimate, walk_generated_files, ARCHIVE_DIRNAME,
    handle_pipeline_failure, cancel_auto_retry, _db_status as _db_status_for,
)
from app.core.database import record_failure, clear_failure, get_failure
from app.utils.file_writer import OUTPUTS_ROOT
from fastapi import HTTPException
from pathlib import Path

passed, failed = 0, 0


def check(name, condition, detail=""):
    global passed, failed
    if condition:
        passed += 1
        print(f"  ok   {name}")
    else:
        failed += 1
        print(f"  FAIL {name} {detail}")


# ── status vocabulary ────────────────────────────────────────────────────────
check("legacy error maps to error_paused", status_vocab.canonical("error") == status_vocab.ERROR_PAUSED)
check("legacy complete maps to completed", status_vocab.canonical("complete") == status_vocab.COMPLETED)
check("canonical values pass through", status_vocab.canonical("running") == "running")
check("unknown status falls back to error_paused",
      status_vocab.canonical("wat") == status_vocab.ERROR_PAUSED)
check("no legacy rows survive in the db",
      not [r for r in get_all_projects() if r["status"] not in status_vocab.ALL])


# ── path traversal safety (ponytail #3) ──────────────────────────────────────
for evil in ["../..", "..", "a/../../etc", "../outputs", "", ".", "foo/bar"]:
    try:
        project_output_path(evil)
        check(f"traversal rejected: {evil!r}", False, "was accepted")
    except HTTPException:
        check(f"traversal rejected: {evil!r}", True)

check("clean id resolves under outputs",
      project_output_path("abc-123").parent == Path(OUTPUTS_ROOT).resolve())


# ── cold-start position model (ponytail #1) ──────────────────────────────────
class FakeSnapshot:
    def __init__(self, nxt):
        self.next = nxt
        self.values = {"x": 1}


check("paused at a gate reads as gate even when the row still says running",
      derive_position(FakeSnapshot(("human_gate_2",)), "nope", "running")["phase"] == "gate")
check("gate identity is surfaced",
      derive_position(FakeSnapshot(("human_gate_2",)), "nope", "running")["gate"] == "human_gate_2")

zombie = derive_position(FakeSnapshot(("architecture",)), "not-live", "running")
check("agent node with no live task is interrupted, not running", zombie["phase"] == "interrupted")
check("interrupted project is offered as resumable", zombie["resumable"] is True)

_live_runs["live-one"] = "task-handle"
try:
    live = derive_position(FakeSnapshot(("architecture",)), "live-one", "running")
    check("agent node with a live task reads as running", live["phase"] == "running")
finally:
    _live_runs.pop("live-one", None)

check("finished graph is terminal",
      derive_position(FakeSnapshot(()), "x", "completed")["phase"] == "terminal")
check("error status outranks position",
      derive_position(FakeSnapshot(("qa",)), "x", "error_paused")["phase"] == "error")


# ── restart wiring (ponytail #2) ─────────────────────────────────────────────
from app.graph.pipeline import GATE_ROUTES, STAGE_ORDER

for public, (stage_key, gate, decision) in RESTART_ENTRY.items():
    check(f"restart '{public}' routes through an existing gate edge",
          GATE_ROUTES[gate].get(decision) is not None)
    check(f"restart '{public}' targets a known stage", stage_key in STAGE_ORDER)

# The routing claim is the whole basis of ponytail #2 ("restart is a gate press"),
# so assert it against the real compiled graph rather than trusting the map: after
# writing the checkpoint as_node=<gate> with the decision, `next` must be the
# node the user asked to restart from.
EXPECTED_NEXT = {
    "research": "research",
    "requirements": "requirements",
    "architecture": "architecture",
    "planning": "planning",
    "code_generation": "frontend_code",
}
RPID = "test-day24-restart"
rconf = {"configurable": {"thread_id": RPID}}
for public, (stage_key, gate, decision) in RESTART_ENTRY.items():
    graph.update_state(rconf, {
        "project_id": RPID,
        "research_report": "r", "requirements_doc": "q", "architecture_doc": "a",
        "implementation_plan": "[]",
        "human_decision": decision,
        "skip_gate_1": False, "replan_after_architecture": False,
    }, as_node=gate)
    actual = graph.get_state(rconf).next
    check(f"restart '{public}' re-enters the graph at {EXPECTED_NEXT[public]}",
          actual and actual[0] == EXPECTED_NEXT[public], f"got {actual}")

graph.checkpointer.delete_thread(RPID)


# ── recovery position must survive a failure ─────────────────────────────────
# The regression this guards: writing failure info into the LangGraph checkpoint
# re-derives `next` from (failed_node,) to (), after which the run streams zero
# events forever and can never be retried. Asserted against the REAL compiled
# graph, driven through the same restart entry a user would use.
FPID = "test-day24-failure"
fconf = {"configurable": {"thread_id": FPID}}
insert_project(FPID, "Failure", "brief", status_vocab.RUNNING, "architecture")
graph.update_state(fconf, {
    "project_id": FPID, "research_report": "r", "requirements_doc": "q",
    "human_decision": "edit", "skip_gate_1": False, "replan_after_architecture": False,
}, as_node="human_gate_2")

pending_before = graph.get_state(fconf).next
check("failure fixture is pending at architecture",
      pending_before == ("architecture",), f"got {pending_before}")


class FakeLLMError(Exception):
    error_type = "rate_limit"
    recoverable = True
    agent_type = "architecture"
    model = "groq/llama"


asyncio.get_event_loop().run_until_complete(
    handle_pipeline_failure(FPID, fconf, "architecture", FakeLLMError("provider exploded")))

check("POSITION SURVIVES: pending task intact after a failure",
      graph.get_state(fconf).next == ("architecture",),
      f"got {graph.get_state(fconf).next} — the run is now unresumable")

agent, ctx = get_failure(FPID)
check("failure recorded on the project row", agent == "architecture")
check("failure context carries the error type", (ctx or {}).get("error_type") == "rate_limit")
check("failure is NOT written into the checkpoint",
      not graph.get_state(fconf).values.get("failed_agent"))
check("status reflects the rate limit", _db_status_for(FPID) == status_vocab.RATE_LIMITED)

# a second failure must keep counting cycles without touching position
asyncio.get_event_loop().run_until_complete(
    handle_pipeline_failure(FPID, fconf, "architecture", FakeLLMError("again")))
check("auto-retry cycles accumulate across failures",
      (get_failure(FPID)[1] or {}).get("auto_retry_cycles") == 2)
check("POSITION SURVIVES a second failure",
      graph.get_state(fconf).next == ("architecture",))

clear_failure(FPID)
check("clearing the failure leaves the position alone",
      graph.get_state(fconf).next == ("architecture",))
check("cleared failure reads as healthy", get_failure(FPID) == ("", None))

cancel_auto_retry(FPID)
graph.checkpointer.delete_thread(FPID)
_c = get_db_connection()
with _c:
    _c.execute("DELETE FROM projects WHERE id = ?", (FPID,))
_c.close()


# ── END-TO-END: a failed run must actually be retryable ──────────────────────
# The assertions above prove the position survives. This proves the whole loop
# works on the REAL pipeline: stream -> node raises -> failure handler ->
# /recover retry -> the node re-runs and the pipeline advances to its gate.
# No API calls: the architecture agent's one LLM seam is patched to fail once.
import app.agents.architecture_agent as arch_module
from app.routers.projects import run_graph_background, recover_project, RecoverRequest

E2E = "test-day24-retry-e2e"
econf = {"configurable": {"thread_id": E2E}}
ARCH_DOC = "# Architecture\n\n## Components\nA real enough document for the test.\n"
calls = {"n": 0}
real_call_validated = arch_module.call_validated


def flaky_call_validated(messages, agent_type, state, **kwargs):
    calls["n"] += 1
    if calls["n"] == 1:
        from app.exceptions import LLMError
        raise LLMError("provider exploded", agent_type="architecture")
    return ARCH_DOC


arch_module.call_validated = flaky_call_validated
insert_project(E2E, "Retry E2E", "brief", status_vocab.RUNNING, "architecture")
graph.update_state(econf, {
    "project_id": E2E, "project_name": "Retry E2E", "brief": "a brief",
    "research_report": "# Research\nfindings", "requirements_doc": "# Requirements\nreqs",
    "tech_stack": '{"frontend": ["React"], "backend": "FastAPI"}',
    "architecture_doc": "", "implementation_plan": "", "log": [], "errors": [],
    "retry_counts": {}, "stage_history": [], "previous_versions": {},
    "human_decision": "edit", "skip_gate_1": False, "replan_after_architecture": False,
}, as_node="human_gate_2")

try:
    loop = asyncio.get_event_loop()
    loop.run_until_complete(run_graph_background(E2E, econf))

    check("E2E: first run fails at architecture", calls["n"] == 1)
    check("E2E: project is error_paused after the failure",
          _db_status_for(E2E) == status_vocab.ERROR_PAUSED, _db_status_for(E2E))
    check("E2E: architecture is still the pending task",
          graph.get_state(econf).next == ("architecture",),
          f"got {graph.get_state(econf).next}")
    check("E2E: failure recorded on the row", get_failure(E2E)[0] == "architecture")

    loop.run_until_complete(recover_project(E2E, RecoverRequest(action="retry")))
    # recover_project schedules the run as a task; drain it.
    pending = [t for t in asyncio.all_tasks(loop) if not t.done()]
    if pending:
        loop.run_until_complete(asyncio.gather(*pending, return_exceptions=True))

    check("E2E: RETRY ACTUALLY RE-RAN the failed node", calls["n"] == 2, f"calls={calls['n']}")
    values = graph.get_state(econf).values
    check("E2E: the retry produced the document", values.get("architecture_doc") == ARCH_DOC)
    check("E2E: pipeline advanced to gate 2",
          graph.get_state(econf).next == ("human_gate_2",),
          f"got {graph.get_state(econf).next}")
    check("E2E: project is awaiting approval again",
          _db_status_for(E2E) == status_vocab.AWAITING_APPROVAL, _db_status_for(E2E))
    check("E2E: the failure record was cleared", get_failure(E2E) == ("", None))
finally:
    arch_module.call_validated = real_call_validated
    cancel_auto_retry(E2E)
    graph.checkpointer.delete_thread(E2E)
    _ec = get_db_connection()
    with _ec:
        _ec.execute("DELETE FROM projects WHERE id = ?", (E2E,))
    _ec.close()


# ── delete cascade: all four stores clean (ponytail #3 proof) ────────────────
PID = "test-day24-delete"
conf = {"configurable": {"thread_id": PID}}

insert_project(PID, "Throwaway", "a brief", status_vocab.RUNNING, "research")
graph.update_state(conf, {"project_id": PID, "brief": "a brief", "log": ["seeded"]})
metrics_store.record_agent_run(agent="research", project_id=PID, attempt=1,
                               outcome="ok", prompt_tokens=10, completion_tokens=5)
out_dir = Path(OUTPUTS_ROOT) / PID
(out_dir / "sub").mkdir(parents=True, exist_ok=True)
(out_dir / "sub" / "f.py").write_text("print(1)\n")
(out_dir / ARCHIVE_DIRNAME / "20260101T000000Z").mkdir(parents=True, exist_ok=True)
(out_dir / ARCHIVE_DIRNAME / "20260101T000000Z" / "old.py").write_text("print(0)\n")


def store_state():
    conn = get_db_connection()
    row = conn.execute("SELECT COUNT(*) n FROM projects WHERE id = ?", (PID,)).fetchone()["n"]
    ckpt = conn.execute("SELECT COUNT(*) n FROM checkpoints WHERE thread_id = ?", (PID,)).fetchone()["n"]
    writes = conn.execute("SELECT COUNT(*) n FROM writes WHERE thread_id = ?", (PID,)).fetchone()["n"]
    conn.close()
    mconn = metrics_store._connect()
    metrics = mconn.execute("SELECT COUNT(*) n FROM agent_runs WHERE project_id = ?", (PID,)).fetchone()["n"]
    mconn.close()
    return {"row": row, "checkpoints": ckpt, "writes": writes,
            "metrics": metrics, "files": (Path(OUTPUTS_ROOT) / PID).exists()}


before = store_state()
check("seeded project exists in all four stores",
      before["row"] and before["checkpoints"] and before["metrics"] and before["files"],
      str(before))

# the restart archive must be invisible to the file walkers
walked = [p.name for p in walk_generated_files(out_dir)]
check("walker sees current files", "f.py" in walked)
check("walker skips the restart archive", "old.py" not in walked, str(walked))

asyncio.get_event_loop().run_until_complete(delete_project(PID))
after = store_state()

check("ORPHAN CHECK: projects row gone", after["row"] == 0, str(after))
check("ORPHAN CHECK: checkpoint rows gone", after["checkpoints"] == 0, str(after))
check("ORPHAN CHECK: checkpoint writes gone", after["writes"] == 0, str(after))
check("ORPHAN CHECK: metrics rows gone", after["metrics"] == 0, str(after))
check("ORPHAN CHECK: output dir gone (archive included)", after["files"] is False, str(after))

# deleting a RUNNING project must tear the task down first, so no node can write
# a checkpoint back after its data was removed
LIVE_PID = "test-day24-live"
insert_project(LIVE_PID, "Live", "brief", status_vocab.RUNNING, "research")
graph.update_state({"configurable": {"thread_id": LIVE_PID}}, {"project_id": LIVE_PID})


async def _live_delete():
    async def never_finishes():
        await asyncio.sleep(3600)

    task = asyncio.ensure_future(never_finishes())
    _live_runs[LIVE_PID] = task
    await asyncio.sleep(0)                      # let it start
    result = await delete_project(LIVE_PID)
    return result, task


live_result, live_task = asyncio.get_event_loop().run_until_complete(_live_delete())
check("deleting a running project reports the run was cancelled", live_result["cancelled_run"] is True)
check("the run's task is actually cancelled", live_task.cancelled() or live_task.done())
check("the live-run registry no longer holds it", LIVE_PID not in _live_runs)
_conn = get_db_connection()
_remaining = _conn.execute("SELECT COUNT(*) n FROM projects WHERE id = ?", (LIVE_PID,)).fetchone()["n"]
_conn.close()
check("running project's row is gone", _remaining == 0)

# idempotent retry: a half-failed cascade must be finishable by deleting again
try:
    asyncio.get_event_loop().run_until_complete(delete_project(PID))
    check("second delete 404s rather than crashing", False, "no HTTPException")
except HTTPException as e:
    check("second delete 404s rather than crashing", e.status_code == 404)

print(f"\n{passed} passed, {failed} failed")
sys.exit(1 if failed else 0)
