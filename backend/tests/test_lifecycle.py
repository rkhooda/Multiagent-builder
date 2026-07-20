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
)
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

# idempotent retry: a half-failed cascade must be finishable by deleting again
try:
    asyncio.get_event_loop().run_until_complete(delete_project(PID))
    check("second delete 404s rather than crashing", False, "no HTTPException")
except HTTPException as e:
    check("second delete 404s rather than crashing", e.status_code == 404)

print(f"\n{passed} passed, {failed} failed")
sys.exit(1 if failed else 0)
