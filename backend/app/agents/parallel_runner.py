"""Dependency-aware parallel file scheduler (Day 20).

Both coder agents fan their per-file generation out through this one runner:
independent files generate concurrently (default max 3), a dependent file waits
exactly until its dependencies have finished and committed, a file whose
dependency FAILED is marked `blocked` and never launched (saving an LLM call on
guaranteed-garbage output), and everything else proceeds. Failures are isolated.

Design (Day 20 ponytail conclusions):
  #1 scheduler shape — a dynamic ready-queue, but realised by letting asyncio's
     await-graph BE the queue: one coroutine per file `await`s its dependency
     futures, then takes a permit, then builds its context (now seeing freshly
     committed upstream files) and generates. No hand-rolled in-degree loop —
     asyncio already is one. A Kahn pass runs only to detect cycles and fix a
     deterministic creation order. (Wave-gather rejected: same correctness,
     strictly worse throughput, more code.)
  #2 purity — workers are pure (context in -> ProcessedFile out; they write only
     their own unique file). ONLY this coordinator, on the single event loop,
     mutates generated_files/errors/log and broadcasts, via commit_generated_file.
     The worker holds ONE permit for its whole lifetime, covering both the
     primary and the repair LLM call inside call_validated.
  #3 limits — one global semaphore, env-configurable; GENERATION_MODE=sequential
     forces 1 (the permanent debugging lever). Per-provider caps + adaptive
     throttling are deferred to Day 26's token bucket: the provider is only known
     inside call_llm's fallback chain, and Day 17's per-call backoff already
     absorbs a 429 even when several fire at once.

This is a file-scale miniature of the task-scale DAG scheduler in the AI Software
Factory vision (ready-queue, blocked-propagation, pure workers) — hence the
deliberately generic name, not a coder-specific one.
"""
import asyncio
import os
import time
from dataclasses import dataclass, field

from app.utils.file_writer import process_generated_file, commit_generated_file

DEFAULT_MAX_CONCURRENT = 3


def resolve_max_concurrent(requested: int = None) -> int:
    """Effective concurrency cap. GENERATION_MODE=sequential pins it to 1 (the
    debugging lever); otherwise GENERATION_MAX_CONCURRENT, else the caller's
    request, else the default. Always >= 1."""
    if (os.getenv("GENERATION_MODE") or "").strip().lower() == "sequential":
        return 1
    env = os.getenv("GENERATION_MAX_CONCURRENT")
    if env and env.strip().isdigit():
        return max(1, int(env.strip()))
    return max(1, requested or DEFAULT_MAX_CONCURRENT)


@dataclass
class PhaseResult:
    """Outcome of one parallel phase. ok/failed/blocked are filepaths; the coder
    maps them back to tasks for its own bookkeeping (which routers succeeded,
    partial_failures, the >50% rule)."""
    ok: list = field(default_factory=list)
    failed: list = field(default_factory=list)
    blocked: list = field(default_factory=list)
    total: int = 0

    @property
    def counts(self) -> dict:
        return {"done": len(self.ok), "failed": len(self.failed),
                "blocked": len(self.blocked), "total": self.total}


def _dep_edges(tasks: list, implicit_deps) -> tuple:
    """Build (by_id, edges) where edges[id] is the set of in-set dependency ids.

    Dependencies OUTSIDE this task set (e.g. ORM models the database agent
    already generated) are dropped: they are guaranteed committed before this
    phase runs, so they impose no wait here. `implicit_deps(task, by_id)` adds
    structural edges the planner may have omitted (Day 19's config->model->
    schema->router layering, encoded as real edges so it survives parallelism).
    """
    by_id = {}
    for i, t in enumerate(tasks):
        tid = t.get("id") or f"__task_{i}"
        t = {**t, "id": tid}
        by_id[tid] = t
    ids = set(by_id)
    edges = {}
    for tid, t in by_id.items():
        deps = set(t.get("requires") or [])
        if implicit_deps:
            deps |= set(implicit_deps(t, by_id) or [])
        edges[tid] = {d for d in deps if d in ids and d != tid}
    return by_id, edges


def _kahn_order(by_id: dict, edges: dict) -> list:
    """Topological order for deterministic task-creation order, and a cycle
    guard: a cycle leaves tasks unplaced -> raise (Gate 3 validation should have
    caught it; defend anyway rather than deadlock on the await-graph)."""
    indegree = {tid: len(edges[tid]) for tid in by_id}
    ready = sorted(tid for tid, d in indegree.items() if d == 0)
    order = []
    dependents = {tid: [] for tid in by_id}
    for tid, deps in edges.items():
        for d in deps:
            dependents[d].append(tid)
    while ready:
        tid = ready.pop(0)
        order.append(tid)
        for dep in dependents[tid]:
            indegree[dep] -= 1
            if indegree[dep] == 0:
                ready.append(dep)
        ready.sort()
    if len(order) < len(by_id):
        cyclic = sorted(set(by_id) - set(order))
        raise ValueError(
            f"Dependency cycle among tasks {cyclic} — cannot schedule. "
            f"(Gate 3 plan validation should reject cyclic `requires`.)")
    return order


async def run_tasks_parallel(tasks, state, *, generate, build_context, stub_for,
                             phase, project_id, file_tree=None, implicit_deps=None,
                             max_concurrent=None) -> PhaseResult:
    """Generate `tasks` concurrently, respecting dependencies. Coordinator owns
    all shared-state mutation; workers are pure.

    Callbacks (coder-supplied):
      build_context(task, state) -> str   run on the loop at LAUNCH, so it sees
                                          freshly committed dependency files.
      generate(task, context) -> ProcessedFile   run in a worker thread; does
                                          call_validated (primary + one repair,
                                          both under this worker's single permit)
                                          then process_generated_file. Raises on
                                          unrecoverable failure.
      stub_for(task, reason) -> str       placeholder body for a failed/blocked
                                          file (visible + fixable at Gate 4).
    """
    if not tasks:
        return PhaseResult(total=0)

    from ..core.connection_manager import manager

    by_id, edges = _dep_edges(tasks, implicit_deps)
    order = _kahn_order(by_id, edges)  # cycle guard + deterministic creation order

    cap = resolve_max_concurrent(max_concurrent)
    sem = asyncio.Semaphore(cap)
    result = PhaseResult(total=len(by_id))
    state.setdefault("log", []).append(
        f"{phase}_coder: scheduling {result.total} files, max_concurrent={cap}"
        + (" (sequential mode)" if cap == 1 else ""))

    def broadcast(event_type, task, extra=None):
        payload = {"type": event_type, "filename": task["filepath"].rsplit("/", 1)[-1],
                   "filepath": task["filepath"], "phase": phase, **result.counts}
        if extra:
            payload.update(extra)
        manager.broadcast_sync(project_id, payload)

    def write_stub(task, reason, event_type):
        """Commit a placeholder for a failed/blocked file (state + the SINGLE
        lifecycle broadcast). file_failed carries `error`; file_blocked carries
        `reason` — matching what the frontend feed reads for each."""
        processed = process_generated_file(project_id, task, stub_for(task, reason), file_tree)
        extra = {"error": reason[:300]} if event_type == "file_failed" else {"reason": reason}
        commit_generated_file(state, processed, project_id=project_id, phase=phase,
                              counts=result.counts, event_type=event_type, extra=extra)

    task_futures = {}

    async def run_one(tid):
        task = by_id[tid]
        fp = task["filepath"]
        dep_ids = sorted(edges[tid])
        dep_status = await asyncio.gather(*(task_futures[d] for d in dep_ids)) if dep_ids else []

        # Blocked: a dependency failed/was blocked -> never launch (quota saved,
        # and a file generated against a failed dependency is guaranteed garbage).
        bad = [d for d, s in zip(dep_ids, dep_status) if s != "ok"]
        if bad:
            result.blocked.append(fp)
            reason = f"blocked by failure of {by_id[bad[0]]['filepath']}"
            write_stub(task, reason, "file_blocked")  # single broadcast inside
            return "blocked"

        async with sem:  # one permit for the worker's whole lifetime (both LLM calls)
            broadcast("file_started", task)
            context = build_context(task, state)  # loop; sees committed deps
            try:
                processed = await asyncio.to_thread(generate, task, context)
            except Exception as e:  # per-file isolation — record, stub, keep going
                processed, err = None, str(e)

        if processed is None or processed.status != "ok":
            err = err if processed is None else (processed.error or "write failed")
            result.failed.append(fp)
            state.setdefault("errors", []).append(f"{phase}_coder: {fp} failed after repair: {err}")
            write_stub(task, err, "file_failed")  # single broadcast inside
            return "failed"

        result.ok.append(fp)
        commit_generated_file(state, processed, project_id=project_id, phase=phase,
                              counts=result.counts, event_type="file_written")
        return "ok"

    # Create every coroutine (topo order so a dependent's futures already exist),
    # then let asyncio's await-graph run them as a dynamic ready-queue.
    t0 = time.monotonic()
    for tid in order:
        task_futures[tid] = asyncio.create_task(run_one(tid))
    await asyncio.gather(*task_futures.values())
    elapsed = time.monotonic() - t0
    state.setdefault("log", []).append(
        f"{phase}_coder: phase wall-clock {elapsed:.1f}s for {result.total} files "
        f"at max_concurrent={cap} ({len(result.ok)} ok, {len(result.failed)} failed, "
        f"{len(result.blocked)} blocked)")
    return result


def run_phase(tasks, state, **kwargs) -> PhaseResult:
    """Synchronous entry point for the coder agents (graph nodes run in a thread
    pool with no event loop). Spins a fresh loop, runs the async coordinator to
    completion, returns the PhaseResult. Broadcasts still reach the app loop via
    manager.broadcast_sync (which marshals to the loop stored at WS-connect)."""
    return asyncio.run(run_tasks_parallel(tasks, state, **kwargs))
