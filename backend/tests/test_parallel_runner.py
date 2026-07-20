"""Concurrency-safety tests for the parallel file scheduler (Day 20).

Pure and fast (<2s): a FAKE generate_fn — no LLM, instant, deterministic — so
this is the regression harness Day 22/Day 26 lean on when they touch the
scheduler again. Runnable directly (`python3 tests/test_parallel_runner.py`) and
under pytest (the test_* functions assert).

Covers: diamond ordering + dependency-content injection, no lost updates at
concurrency, failure -> transitive blocking with honest counts, permit
discipline (never exceeds the cap), and sequential-mode determinism.
"""
import os
import sys
import shutil
import threading
import time

BACKEND_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BACKEND_DIR)

from app.agents.parallel_runner import run_phase
from app.utils.file_writer import ProcessedFile, OUTPUTS_ROOT
import app.core.connection_manager as cm

PROJECT_ID = "__test_parallel_runner__"

# Capture every broadcast so we can assert no duplicate lifecycle events.
_broadcasts = []
cm.manager.broadcast_sync = lambda pid, event: _broadcasts.append(event)


def _clean():
    shutil.rmtree(os.path.join(OUTPUTS_ROOT, PROJECT_ID), ignore_errors=True)


def _fresh_state(tasks):
    return {"project_id": PROJECT_ID, "generated_files": {}, "log": [], "errors": [],
            "file_list": [t["filepath"] for t in tasks]}


def make_generate(fail_ids=()):
    """Fake worker. Records concurrency + call order; simulates work with a small
    sleep so overlap is real; raises for fail_ids (the failure path)."""
    lock = threading.Lock()
    stats = {"inflight": 0, "max_inflight": 0, "order": []}

    def generate(task, context):
        with lock:
            stats["inflight"] += 1
            stats["max_inflight"] = max(stats["max_inflight"], stats["inflight"])
            stats["order"].append(task["id"])
        try:
            time.sleep(0.02)
            if task["id"] in fail_ids:
                raise RuntimeError(f"fake failure for {task['id']}")
            content = f"CONTENT::{task['id']}::ctx[{context}]"
            return ProcessedFile(filepath=task["filepath"], content=content,
                                 status="ok", size_bytes=len(content))
        finally:
            with lock:
                stats["inflight"] -= 1

    return generate, stats


def build_context(task, state):
    """Concatenate the committed content of this task's dependency files — proves
    a dependent sees its upstream files' FINISHED content at launch time."""
    gf = state["generated_files"]
    by_fp = {t: c for t, c in gf.items()}
    dep_fps = [f"{d}.py" for d in (task.get("requires") or [])]
    return " + ".join(by_fp.get(fp, f"<{fp} MISSING>") for fp in dep_fps)


def stub_for(task, reason):
    return f"# STUB {task['filepath']}: {reason}\npass\n"


def run(tasks, fail_ids=(), max_concurrent=3, implicit_deps=None):
    _clean()
    _broadcasts.clear()
    state = _fresh_state(tasks)
    gen, stats = make_generate(fail_ids)
    result = run_phase(
        tasks, state, generate=gen, build_context=build_context, stub_for=stub_for,
        phase="test", project_id=PROJECT_ID,
        file_tree=state["file_list"], implicit_deps=implicit_deps,
        max_concurrent=max_concurrent)
    return result, state, stats


def _task(tid, requires=None):
    return {"id": tid, "filepath": f"{tid}.py", "description": tid,
            "requires": requires or []}


# ── 1. Diamond ordering + dependency-content injection ──────────────────────
def test_diamond_ordering_and_content_injection():
    tasks = [_task("A"), _task("B", ["A"]), _task("C", ["A"]), _task("D", ["B", "C"])]
    result, state, stats = run(tasks)
    assert sorted(result.ok) == ["A.py", "B.py", "C.py", "D.py"], result.ok
    # D built its context from B's and C's FINISHED content.
    d_content = state["generated_files"]["D.py"]
    assert "CONTENT::B" in d_content and "CONTENT::C" in d_content, d_content
    # B and C each saw A's finished content (transitive freshness).
    assert "CONTENT::A" in state["generated_files"]["B.py"]
    assert "CONTENT::A" in state["generated_files"]["C.py"]
    _clean()


# ── 2. No lost updates: 20 tasks at concurrency 3 -> exactly 20, all correct ─
def test_no_lost_updates():
    tasks = [_task(f"n{i}") for i in range(20)]
    result, state, stats = run(tasks, max_concurrent=3)
    assert len(result.ok) == 20 and len(state["generated_files"]) == 20
    for i in range(20):
        assert state["generated_files"][f"n{i}.py"] == f"CONTENT::n{i}::ctx[]"
    assert stats["max_inflight"] >= 2  # genuinely ran in parallel
    _clean()


# ── 3. Failure -> transitive blocking, honest counts ────────────────────────
def test_failure_blocks_dependents():
    # A ok, C ok, B fails, D depends on B -> D blocked. counts {2,1,1}.
    tasks = [_task("A"), _task("B"), _task("C"), _task("D", ["B"])]
    result, state, stats = run(tasks, fail_ids={"B"}, max_concurrent=3)
    assert sorted(result.ok) == ["A.py", "C.py"], result.ok
    assert result.failed == ["B.py"], result.failed
    assert result.blocked == ["D.py"], result.blocked
    assert result.counts == {"done": 2, "failed": 1, "blocked": 1, "total": 4}
    # D was never generated (blocked, not launched) — its content is a stub.
    assert "STUB" in state["generated_files"]["D.py"]
    assert "D" not in stats["order"], "blocked file must never enter the worker"
    _clean()


# ── 3b. Transitive blocking propagates through a chain ──────────────────────
def test_transitive_blocking_chain():
    # B fails -> C (req B) blocked -> D (req C) blocked too.
    tasks = [_task("B"), _task("C", ["B"]), _task("D", ["C"])]
    result, state, stats = run(tasks, fail_ids={"B"})
    assert result.failed == ["B.py"]
    assert sorted(result.blocked) == ["C.py", "D.py"], result.blocked
    assert stats["order"] == ["B"], stats["order"]  # only B ever launched
    _clean()


# ── 4. Permit discipline: never exceeds the cap ─────────────────────────────
def test_permit_discipline():
    tasks = [_task(f"p{i}") for i in range(20)]
    for cap in (1, 2, 3, 5):
        _, _, stats = run(tasks, max_concurrent=cap)
        assert stats["max_inflight"] <= cap, (cap, stats["max_inflight"])
        if cap >= 2:
            assert stats["max_inflight"] >= 2, (cap, stats["max_inflight"])
    _clean()


# ── 5. Sequential mode: strictly serial, topological order ──────────────────
def test_sequential_mode():
    tasks = [_task("A"), _task("B", ["A"]), _task("C", ["A"]), _task("D", ["B", "C"])]
    os.environ["GENERATION_MODE"] = "sequential"
    try:
        result, state, stats = run(tasks, max_concurrent=3)  # env overrides the 3
    finally:
        del os.environ["GENERATION_MODE"]
    assert stats["max_inflight"] == 1, stats["max_inflight"]
    # Every task ran after all its dependencies (valid topological execution).
    pos = {tid: i for i, tid in enumerate(stats["order"])}
    reqs = {"A": [], "B": ["A"], "C": ["A"], "D": ["B", "C"]}
    for tid, deps in reqs.items():
        for d in deps:
            assert pos[d] < pos[tid], f"{d} ran after {tid}: {stats['order']}"
    _clean()


# ── 5b. No duplicate lifecycle broadcasts (each file terminal-events once) ──
def test_no_duplicate_broadcasts():
    tasks = [_task("A"), _task("B"), _task("C"), _task("D", ["B"])]
    run(tasks, fail_ids={"B"}, max_concurrent=3)  # A/C ok, B failed, D blocked
    terminal = {"file_written", "file_failed", "file_blocked"}
    seen = {}
    for e in _broadcasts:
        if e["type"] in terminal:
            key = e["filepath"]
            seen[key] = seen.get(key, 0) + 1
    # Exactly one terminal event per file, no duplicates.
    assert seen == {"A.py": 1, "B.py": 1, "C.py": 1, "D.py": 1}, seen
    # Blocked file never emitted file_started (never launched).
    started = [e["filepath"] for e in _broadcasts if e["type"] == "file_started"]
    assert "D.py" not in started, started
    # Failed event carries `error`; blocked carries `reason`.
    fail_ev = next(e for e in _broadcasts if e["type"] == "file_failed")
    blk_ev = next(e for e in _broadcasts if e["type"] == "file_blocked")
    assert "error" in fail_ev and "reason" in blk_ev
    _clean()


# ── 6. A context-builder raise is isolated to its own file ─────────────────
def test_context_builder_failure_is_isolated():
    """build_context parses LLM-authored architecture text, so it CAN raise on
    malformed input (Day 20: truncate_for_context NameError). Before Day 21 it
    ran outside the try, so one raise propagated through the dependent gather
    and killed the entire phase. It must fail exactly one file instead."""
    tasks = [_task("A"), _task("B"), _task("C"), _task("D", ["B"])]
    _clean()
    _broadcasts.clear()
    state = _fresh_state(tasks)
    gen, _ = make_generate()

    def exploding_context(task, st):
        if task["id"] == "B":
            raise NameError("name 'truncate_for_context' is not defined")
        return build_context(task, st)

    result = run_phase(
        tasks, state, generate=gen, build_context=exploding_context,
        stub_for=stub_for, phase="test", project_id=PROJECT_ID,
        file_tree=state["file_list"], max_concurrent=3)

    # The phase completed rather than raising; B failed, D blocked behind it.
    assert sorted(result.ok) == ["A.py", "C.py"], result.ok
    assert result.failed == ["B.py"], result.failed
    assert result.blocked == ["D.py"], result.blocked
    # B was stubbed on disk + in state (Gate-4 visible/fixable), not silently lost.
    assert "B.py" in state["generated_files"]
    assert "STUB" in state["generated_files"]["B.py"]
    # The real cause is recorded, not swallowed.
    assert any("truncate_for_context" in e for e in state["errors"]), state["errors"]
    _clean()


# ── 7. Cycle guard ──────────────────────────────────────────────────────────
def test_cycle_detection():
    tasks = [_task("X", ["Y"]), _task("Y", ["X"])]
    try:
        run(tasks)
    except ValueError as e:
        assert "cycle" in str(e).lower()
        _clean()
        return
    raise AssertionError("expected a ValueError on a dependency cycle")


def test_implicit_dep_never_contradicts_explicit():
    """An implicit layering edge must yield to an explicit one — Day 25.

    The real failure this reproduces: `utils/api.js` (a lib) explicitly requires
    `config.js`, while the frontend coder's layering makes every non-lib —
    including `config.js` — wait on the lib. That is a 2-cycle over a perfectly
    valid plan, and because every other file waits on the lib, the WHOLE phase
    was stranded (47/47 tasks unschedulable on the Day 25 simple run).

    Dropping the implicit edge is always safe: it encodes generation order, not
    correctness. Dropping the explicit one would defy the validated plan.
    """
    from app.agents.parallel_runner import _dep_edges, _kahn_order

    tasks = [
        {"id": "config", "filepath": "frontend/src/config.js", "requires": []},
        {"id": "api", "filepath": "frontend/src/utils/api.js", "requires": ["config"]},
        {"id": "page", "filepath": "frontend/src/pages/Home.jsx", "requires": []},
    ]
    libs = ["api"]

    def implicit(task, by_id):
        return [] if task["filepath"].endswith("api.js") else libs

    by_id, edges = _dep_edges(tasks, implicit)
    order = _kahn_order(by_id, edges)

    assert len(order) == 3, f"whole phase stranded: {order}"
    # The explicit edge survives; the contradicting implicit one was dropped.
    assert order.index("config") < order.index("api"), order
    # The non-contradicting implicit edge is kept: a page still waits on the client.
    assert "api" in edges["page"], edges
    assert order.index("api") < order.index("page"), order


def test_real_cycle_message_names_only_the_cycle():
    """A stranded fan-out must not bury the two ids that actually cycle."""
    from app.agents.parallel_runner import _dep_edges, _kahn_order

    tasks = [{"id": "a", "requires": ["b"]}, {"id": "b", "requires": ["a"]}] + [
        {"id": f"d{i}", "requires": ["a"]} for i in range(6)
    ]
    by_id, edges = _dep_edges(tasks, None)
    try:
        _kahn_order(by_id, edges)
    except ValueError as e:
        msg = str(e)
        assert "cycle: ['a', 'b']" in msg, msg
        assert "8 task(s) stranded" in msg, msg
        return
    raise AssertionError("expected a ValueError on a real cycle")


def test_failure_stub_is_syntactically_valid():
    """A failure placeholder must parse — Day 25.

    Provider errors are multi-line JSON. Embedding one in a comment without
    flattening prefixed only its first line, emitting the rest as bare code, so
    every failed file became a syntax error. On the Day 25 simple run that was
    17 of 96 files: defects the model never produced, charged to its score, and
    eligible to consume the Day 22 repair budget.
    """
    import ast
    from app.agents.backend_coder_agent import _failure_stub as backend_stub
    from app.agents.frontend_coder_agent import _failure_stub as frontend_stub

    multiline_error = 'rate-limited: {\n  "error": {\n    "code": 429\n  }\n}'

    py = backend_stub("backend/app/schemas/user.py", multiline_error)
    ast.parse(py)  # raises if the stub is malformed
    assert all(line.startswith("#") or not line.strip() or line == "pass"
               for line in py.splitlines()), py

    jsx = frontend_stub("frontend/src/x.jsx", multiline_error)
    for line in jsx.splitlines():
        if "429" in line or "error" in line:
            assert line.startswith("//"), f"error text escaped the comment: {line}"


TESTS = [
    ("diamond ordering + content injection", test_diamond_ordering_and_content_injection),
    ("failure stub is syntactically valid", test_failure_stub_is_syntactically_valid),
    ("implicit dep yields to explicit (no false cycle)", test_implicit_dep_never_contradicts_explicit),
    ("cycle message names only the cycle", test_real_cycle_message_names_only_the_cycle),
    ("no lost updates (20 @ 3)", test_no_lost_updates),
    ("failure blocks dependents {2,1,1}", test_failure_blocks_dependents),
    ("transitive blocking chain", test_transitive_blocking_chain),
    ("permit discipline (cap never exceeded)", test_permit_discipline),
    ("sequential mode determinism", test_sequential_mode),
    ("no duplicate lifecycle broadcasts", test_no_duplicate_broadcasts),
    ("context-builder raise isolated to one file", test_context_builder_failure_is_isolated),
    ("cycle detection", test_cycle_detection),
]

if __name__ == "__main__":
    passed = failed = 0
    for name, fn in TESTS:
        try:
            fn()
            print(f"  ok   {name}")
            passed += 1
        except Exception as e:
            print(f"  FAIL {name}: {type(e).__name__}: {e}")
            failed += 1
    _clean()
    print(f"\n{passed} passed, {failed} failed")
    sys.exit(1 if failed else 0)
