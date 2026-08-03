"""Incremental QA stream (Improvement 02) — review files while generation runs.

QA's cost was pure serial tail: batched review started only after the last file
was written. This module re-times the SAME work: the coordinator (or the
database agent's loop) offers each committed file into a queue, one daemon
consumer thread forms batches by the exact `_chunk_files` rules and calls the
pure `review_batch`, and the QA node joins the stream and aggregates. Same
batches, same call count, same report — most of it just finishes earlier.

ponytail #2 (verified provider map, docs/PROVIDERS.md): QA (gemini) and the
coders (groq) draw on DIFFERENT primary pools, so there is no token contention
to schedule around — the whole concurrency story is this one consumer honouring
QA_CONCURRENCY (default 1). The stream lives here, not in parallel_runner,
because each coder phase's event loop dies at phase end (`asyncio.run` in
run_phase) while the producers span three graph nodes — a module-level registry
keyed by project_id (the _abandoned_projects pattern) plus a plain queue.Queue
is the minimum structure with the right lifetime, and call_llm is blocking so
a thread, not asyncio, is its natural home.

Purity discipline (Day 20, non-negotiable): producers enqueue pure
(filepath, content, warnings) snapshots; the consumer accumulates findings
under its OWN lock and broadcasts via the already-thread-marshalling
manager.broadcast_sync; ONLY the QA node writes LangGraph state, at join.

Every degradation is counted (Improvement 02 Task 1): a failed batch, a
stalled join, and a missing stream under QA_MODE=incremental each increment
degraded_events — never a log line only.
"""
import os
import queue
import threading
import time
from concurrent.futures import ThreadPoolExecutor, wait

from ..observability import degraded

_DONE = object()

_registry_lock = threading.Lock()
_streams: dict = {}


# ── Config (read at call time so tests can tune without re-imports) ──────────

def qa_mode() -> str:
    """batch | incremental. Shipped default: batch — the verdict rule fixed in
    advance requires a measured win before incremental becomes the default, and
    QA_MODE=batch must reproduce pre-Improvement-02 behaviour exactly (it is
    the rollback and the A/B control arm in one switch). Pinned by
    test_qa_stream.test_qa_mode_defaults_to_batch."""
    mode = (os.getenv("QA_MODE") or "batch").strip().lower()
    return mode if mode in ("batch", "incremental") else "batch"


def qa_concurrency() -> int:
    env = os.getenv("QA_CONCURRENCY") or ""
    return max(1, int(env)) if env.strip().isdigit() else 1


def rereview_changed() -> bool:
    """Off by default: staleness is realistically only a Day 22 repair whose
    delta is mechanical — exactly what QA is told not to re-litigate — and
    re-queuing by default would add calls in the common case, breaking the
    call-count-flat requirement. On, each changed file gets at most ONE
    re-review, reserved through the same repair budget as everything else."""
    return (os.getenv("QA_REREVIEW_CHANGED") or "").strip().lower() in ("1", "true", "yes")


def join_timeout() -> float:
    env = os.getenv("QA_STREAM_JOIN_TIMEOUT") or ""
    return float(env) if env.replace(".", "", 1).isdigit() else 900.0


class QAStream:
    """One project's in-flight review state. Internal state is guarded by
    self._lock; nothing here touches LangGraph state or the shared log."""

    def __init__(self, project_id: str, fast_mode: bool = False):
        self.project_id = project_id
        self.fast_mode = fast_mode
        self._q = queue.Queue()
        self._lock = threading.Lock()
        self.findings = []          # parsed issue dicts, with batch_id/reviewed_at
        self.errors = []            # per-batch failure strings (state parity at join)
        self.batch_records = []     # {batch_id, files, started, ended, issues}
        self.reviewed_content = {}  # filepath -> content snapshot as reviewed
        self.enqueued = set()
        self.failed_batches = 0
        self.next_batch_id = 1
        self._pool = ThreadPoolExecutor(
            max_workers=qa_concurrency(),
            thread_name_prefix=f"qa-review-{project_id[:8]}")
        self._futures = []
        self._dispatcher = threading.Thread(
            target=self._run, daemon=True, name=f"qa-stream-{project_id[:8]}")
        self._dispatcher.start()

    # ── producer side (coordinator / database agent loop) ────────────────────

    def offer(self, filepath: str, content: str, warnings: list) -> None:
        """Queue one committed file for review. A path is enqueued once —
        re-commits are detected at join by diffing reviewed_content against the
        final generated_files, which also catches mutations that never pass
        through a commit hook (Day 22 validation repairs edit state directly)."""
        if not filepath or filepath in self.enqueued:
            return
        self.enqueued.add(filepath)
        self._q.put((filepath, content or "", list(warnings or [])))

    # ── consumer side ────────────────────────────────────────────────────────

    def _run(self):
        from .qa_agent import MAX_BATCH_CHARS, qa_batch_size
        from ..llm_router import is_abandoned

        pending, pending_chars = [], 0
        while True:
            try:
                item = self._q.get(timeout=5.0)
            except queue.Empty:
                if is_abandoned(self.project_id):
                    return                      # project deleted mid-run
                continue
            if item is _DONE:
                if pending:
                    self._submit(pending)
                return
            content_len = len(item[1])
            # Mirrors _chunk_files exactly, so streamed batch composition (and
            # therefore call count) equals batch mode's chunking of the same
            # files in the same commit order.
            if content_len > MAX_BATCH_CHARS:
                if pending:
                    self._submit(pending)
                    pending, pending_chars = [], 0
                self._submit([item])
                continue
            if pending and pending_chars + content_len > MAX_BATCH_CHARS:
                self._submit(pending)
                pending, pending_chars = [], 0
            pending.append(item)
            pending_chars += content_len
            if len(pending) >= qa_batch_size():
                self._submit(pending)
                pending, pending_chars = [], 0

    def _submit(self, items):
        batch_id = self.next_batch_id
        self.next_batch_id += 1
        self._futures.append(self._pool.submit(self._review, list(items), batch_id))

    def _review(self, items, batch_id):
        from .qa_agent import parser_warnings_block, review_batch

        batch = [(fp, content) for fp, content, _ in items]
        block = parser_warnings_block(
            {fp: w for fp, _, w in items if w})
        started = time.time()
        try:
            issues = review_batch(batch, block, project_id=self.project_id,
                                  fast_mode=self.fast_mode, batch_id=batch_id)
        except Exception as e:                  # noqa: BLE001 — per-batch tolerance
            with self._lock:
                self.failed_batches += 1
                self.errors.append(
                    f"qa_agent: stream batch {batch_id} failed "
                    f"({[fp for fp, _ in batch]}): {e}")
            degraded.record(self.project_id, "qa_batch_failed")
            print(f"[QAStream] batch {batch_id} failed: {e}", flush=True)
            self._broadcast(batch_id)
            return
        with self._lock:
            self.findings.extend(issues)
            for fp, content in batch:
                self.reviewed_content[fp] = content
            self.batch_records.append({
                "batch_id": batch_id, "files": [fp for fp, _ in batch],
                "started": started, "ended": time.time(), "issues": len(issues)})
        self._broadcast(batch_id)

    def _broadcast(self, batch_id):
        from ..core.connection_manager import manager
        with self._lock:
            payload = {
                "type": "qa_batch_complete",
                "batch": batch_id,
                "streaming": True,
                "files_reviewed": len(self.reviewed_content),
                "files_enqueued": len(self.enqueued),
                "issues_found_so_far": len(self.findings),
            }
        try:
            manager.broadcast_sync(self.project_id, payload)
        except Exception as e:                  # noqa: BLE001 — UI only
            print(f"[QAStream] broadcast failed: {e}", flush=True)

    # ── join side (QA node only) ─────────────────────────────────────────────

    def finish(self, timeout: float = None) -> bool:
        """Flush the remainder and wait for all in-flight reviews. Returns
        False on a stall (the caller counts it and sweeps unreviewed files
        itself — coverage never depends on this returning True)."""
        timeout = join_timeout() if timeout is None else timeout
        deadline = time.monotonic() + timeout
        self._q.put(_DONE)
        self._dispatcher.join(timeout)
        if self._dispatcher.is_alive():
            return False
        done, not_done = wait(self._futures,
                              timeout=max(0.0, deadline - time.monotonic()))
        self._pool.shutdown(wait=False)
        return not not_done

    def snapshot(self) -> dict:
        """Consistent copy of everything the QA node folds into state."""
        with self._lock:
            return {
                "findings": list(self.findings),
                "errors": list(self.errors),
                "batch_records": list(self.batch_records),
                "reviewed_content": dict(self.reviewed_content),
                "failed_batches": self.failed_batches,
                "batches_submitted": self.next_batch_id - 1,
            }


# ── Registry ─────────────────────────────────────────────────────────────────

def offer(project_id: str, filepath: str, content: str, warnings: list = None,
          fast_mode: bool = False) -> None:
    """Producer hook. Creates the project's stream lazily on first offer so a
    crash-resumed run simply starts a fresh stream for what it re-commits. A
    no-op unless QA_MODE=incremental — batch mode has no stream at all."""
    if qa_mode() != "incremental":
        return
    with _registry_lock:
        stream = _streams.get(project_id)
        if stream is None:
            stream = _streams[project_id] = QAStream(project_id, fast_mode=fast_mode)
    stream.offer(filepath, content, warnings or [])


def take(project_id: str):
    """Pop the project's stream for joining (QA node). None when nothing
    streamed — the QA node then reviews everything itself."""
    with _registry_lock:
        return _streams.pop(project_id, None)


def discard(project_id: str) -> None:
    """Drop a stale stream (code-stage invalidation at a gate, project delete).
    The daemon consumer exits on its abandoned-check or at process end."""
    with _registry_lock:
        stream = _streams.pop(project_id, None)
    if stream is not None:
        stream._q.put(_DONE)
