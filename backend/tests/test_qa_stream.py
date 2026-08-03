"""Improvement 02 — incremental QA stream correctness. Zero API calls.

Every test monkeypatches qa_agent.review_batch (the one seam between batching
and the LLM), so the full stream → join → sweep → aggregate path runs for real
while no provider is touched. The eight properties here are the deliverable
that does not depend on quota:

  1. every file reviewed exactly once (none missed, none double-reviewed)
  2. partial batches flush at generation end; the report covers 100% of files
  3. out-of-order arrival produces byte-identical report text
  4. a failed batch logs, counts a degraded event, and the stage completes
  5. a changed file follows the Task 4 policy and never exceeds its budget
  6. QA_MODE=batch reproduces end-of-run behaviour (no stream, same chunks)
  7. QA_CONCURRENCY is a hard ceiling and producers are never blocked
  8. every fail-open path increments degraded_events
"""
import os
import sys
import threading
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.agents import qa_agent as qa_mod
from app.agents import qa_stream
from app.observability import degraded

_ENV = ("QA_MODE", "QA_BATCH_SIZE", "QA_CONCURRENCY", "QA_REREVIEW_CHANGED",
        "QA_STREAM_JOIN_TIMEOUT")
_REAL_REVIEW = qa_mod.review_batch


def _set_env(**kwargs):
    for key in _ENV:
        os.environ.pop(key, None)
    for key, value in kwargs.items():
        os.environ[key] = value


def _state(pid, files, **extra):
    base = {"project_id": pid, "project_name": "T", "generated_files": dict(files),
            "log": [], "errors": [], "retry_counts": {}, "validation_report": {}}
    base.update(extra)
    return base


class FakeReview:
    """Records every review_batch call; returns one INFO finding per file."""

    def __init__(self, fail_batch_ids=(), delay=0.0):
        self.lock = threading.Lock()
        self.calls = []            # list of (batch_id, [filepaths])
        self.inflight = 0
        self.max_inflight = 0
        self.fail_batch_ids = set(fail_batch_ids)
        self.delay = delay

    def __call__(self, batch, automated_block, *, project_id="", fast_mode=False,
                 batch_id=0):
        files = [fp for fp, _ in batch]
        with self.lock:
            self.inflight += 1
            self.max_inflight = max(self.max_inflight, self.inflight)
            self.calls.append((batch_id, files))
        try:
            if self.delay:
                time.sleep(self.delay)
            if batch_id in self.fail_batch_ids:
                raise RuntimeError(f"injected failure for batch {batch_id}")
            now = time.time()
            return [{"severity": "INFO", "trivial": False, "file": fp, "line": None,
                     "description": f"finding for {fp}", "batch_id": batch_id,
                     "reviewed_at": now} for fp in files]
        finally:
            with self.lock:
                self.inflight -= 1

    @property
    def reviewed_files(self):
        return [fp for _, files in self.calls for fp in files]


def _run(pid, files, fake, offered=None, wait=1.5, **state_extra):
    """Offer `offered` (or all) files to the stream, then run the QA node."""
    qa_mod.review_batch = fake
    try:
        for fp in (offered if offered is not None else files):
            qa_stream.offer(pid, fp, files[fp], [])
        deadline = time.monotonic() + wait
        # Wait for the stream to drain full batches so tests are not timing-flaky.
        while time.monotonic() < deadline:
            with qa_stream._registry_lock:
                stream = qa_stream._streams.get(pid)
            if stream is None or stream._q.empty():
                break
            time.sleep(0.02)
        time.sleep(0.1)
        return qa_mod.qa_agent(_state(pid, files, **state_extra))
    finally:
        qa_mod.review_batch = _REAL_REVIEW
        qa_stream.discard(pid)
        degraded.drain(pid)


# ── 0. shipped defaults are pinned (spirit of test_both_features_default_to_off) ──

def test_qa_mode_defaults_to_batch():
    _set_env()
    assert qa_stream.qa_mode() == "batch"
    assert qa_stream.qa_concurrency() == 1
    assert qa_stream.rereview_changed() is False
    assert qa_mod.qa_batch_size() == 3


def test_junk_config_falls_back_to_defaults():
    _set_env(QA_MODE="turbo", QA_BATCH_SIZE="lots", QA_CONCURRENCY="-3")
    assert qa_stream.qa_mode() == "batch"
    assert qa_mod.qa_batch_size() == 3
    assert qa_stream.qa_concurrency() == 1


# ── 1 + 2. exact coverage, partial-batch flush ───────────────────────────────

def test_every_file_reviewed_exactly_once_incremental():
    _set_env(QA_MODE="incremental")
    files = {f"src/a{i}.js": f"x={i}" for i in range(7)}
    fake = FakeReview()
    out = _run("t-cover", files, fake)
    assert sorted(fake.reviewed_files) == sorted(files), fake.calls
    assert len(fake.reviewed_files) == len(files)          # none double-reviewed
    assert "**Files Reviewed**: 7" in out["qa_report"]
    assert out["qa_issues_count"] == 7


def test_partial_batch_flushes_at_generation_end():
    _set_env(QA_MODE="incremental")
    files = {f"src/b{i}.js": "x" for i in range(4)}         # 3 + 1 pending
    fake = FakeReview()
    out = _run("t-flush", files, fake)
    assert sorted(len(f) for _, f in fake.calls) == [1, 3], fake.calls
    assert "**Files Reviewed**: 4" in out["qa_report"]


def test_unoffered_files_are_swept_at_join():
    """A file that never reached the stream (crash-resume, database path bug)
    is still reviewed — coverage never depends on the producers."""
    _set_env(QA_MODE="incremental")
    files = {f"src/c{i}.js": "x" for i in range(5)}
    fake = FakeReview()
    offered = list(files)[:3]
    out = _run("t-sweep", files, fake, offered=offered)
    assert sorted(fake.reviewed_files) == sorted(files)
    assert len(fake.reviewed_files) == len(files)
    assert "**Files Reviewed**: 5" in out["qa_report"]


def test_no_stream_in_incremental_mode_falls_back_and_counts():
    _set_env(QA_MODE="incremental")
    files = {"src/d.js": "x"}
    fake = FakeReview()
    qa_mod.review_batch = fake
    try:
        out = qa_mod.qa_agent(_state("t-fallback", files))
    finally:
        qa_mod.review_batch = _REAL_REVIEW
    assert degraded.drain("t-fallback").get("qa_stream_fallback") == 1
    assert "**Files Reviewed**: 1" in out["qa_report"]


# ── 3. deterministic aggregation ─────────────────────────────────────────────

def test_out_of_order_arrival_gives_byte_identical_report():
    findings = [
        {"severity": "WARNING", "trivial": False, "file": "b.js", "line": "4",
         "description": "w1", "batch_id": 2, "reviewed_at": 2.0},
        {"severity": "CRITICAL", "trivial": False, "file": "a.js", "line": None,
         "description": "c1", "batch_id": 1, "reviewed_at": 1.0},
        {"severity": "INFO", "trivial": False, "file": "a.js", "line": "9",
         "description": "i1", "batch_id": 3, "reviewed_at": 3.0},
    ]
    import itertools
    reports = set()
    for perm in itertools.permutations(findings):
        issues = qa_mod.sort_findings(list(perm))
        reports.add(qa_mod._build_report("P", 3, issues, []))
    assert len(reports) == 1, "report text depends on arrival order"


# ── 4. failed batch tolerance ────────────────────────────────────────────────

def test_failed_batch_counts_logs_and_stage_completes():
    """A failed stream batch is logged and counted — and its files get exactly
    ONE second chance via the join's coverage sweep (they were never marked
    reviewed). Batch mode simply loses them; the retry is deliberate, bounded
    to one, and only spends a call on the failure path."""
    _set_env(QA_MODE="incremental")
    files = {f"src/e{i}.js": "x" for i in range(6)}         # batches 1 and 2
    fake = FakeReview(fail_batch_ids={1})
    out = _run("t-fail", files, fake)
    assert degraded.drain("t-fail") == {}                   # drained inside _run
    assert out["qa_issues_count"] == 6                      # sweep re-covered batch 1
    assert any("failed" in e for e in out["errors"])
    assert "**Files Reviewed**: 6" in out["qa_report"]      # stage completed
    assert len(fake.calls) == 3                             # 2 stream + 1 sweep retry


def test_failed_batch_increments_degraded_events():
    _set_env(QA_MODE="incremental")
    files = {f"src/f{i}.js": "x" for i in range(3)}
    fake = FakeReview(fail_batch_ids={1})
    qa_mod.review_batch = fake
    try:
        for fp in files:
            qa_stream.offer("t-deg", fp, files[fp], [])
        time.sleep(0.5)
        qa_mod.qa_agent(_state("t-deg", files))
        counts = degraded.drain("t-deg")
        assert counts.get("qa_batch_failed") == 1, counts
    finally:
        qa_mod.review_batch = _REAL_REVIEW
        qa_stream.discard("t-deg")


def test_total_wipeout_raises_instead_of_empty_report():
    _set_env(QA_MODE="batch")
    files = {"src/g.js": "x"}
    fake = FakeReview(fail_batch_ids={1})
    qa_mod.review_batch = fake
    try:
        qa_mod.qa_agent(_state("t-wipe", files))
        raise AssertionError("expected the wipeout to raise")
    except RuntimeError:
        pass
    finally:
        qa_mod.review_batch = _REAL_REVIEW
        degraded.drain("t-wipe")


# ── 5. changed files: policy + budget cap ────────────────────────────────────

def test_changed_file_flagged_stale_by_default():
    _set_env(QA_MODE="incremental")
    files = {f"src/h{i}.js": "old" for i in range(3)}
    fake = FakeReview()
    qa_mod.review_batch = fake
    try:
        for fp in files:
            qa_stream.offer("t-stale", fp, files[fp], [])
        time.sleep(0.5)
        files["src/h1.js"] = "changed after review"
        out = qa_mod.qa_agent(_state("t-stale", files))
    finally:
        qa_mod.review_batch = _REAL_REVIEW
        qa_stream.discard("t-stale")
        degraded.drain("t-stale")
    assert "Possibly Stale Reviews" in out["qa_report"]
    assert "src/h1.js" in out["qa_report"]
    assert out["retry_counts"] == {}                        # free path spends nothing
    assert len(fake.calls) == 1                             # no extra call


def test_changed_file_rereviewed_once_within_budget():
    _set_env(QA_MODE="incremental", QA_REREVIEW_CHANGED="true")
    files = {f"src/i{i}.js": "old" for i in range(3)}
    fake = FakeReview()
    qa_mod.review_batch = fake
    try:
        for fp in files:
            qa_stream.offer("t-rr", fp, files[fp], [])
        time.sleep(0.5)
        files["src/i2.js"] = "changed"
        out = qa_mod.qa_agent(_state("t-rr", files))
    finally:
        qa_mod.review_batch = _REAL_REVIEW
        qa_stream.discard("t-rr")
        degraded.drain("t-rr")
    assert out["retry_counts"].get("repair:src/i2.js") == 1
    assert "Possibly Stale Reviews" not in out["qa_report"]
    assert fake.reviewed_files.count("src/i2.js") == 2      # review + ONE re-review


def test_changed_file_rereview_respects_budget_cap():
    from app.validation.report import REPAIR_CAP_PER_FILE
    _set_env(QA_MODE="incremental", QA_REREVIEW_CHANGED="true")
    files = {f"src/j{i}.js": "old" for i in range(3)}
    fake = FakeReview()
    qa_mod.review_batch = fake
    try:
        for fp in files:
            qa_stream.offer("t-cap", fp, files[fp], [])
        time.sleep(0.5)
        files["src/j0.js"] = "changed"
        out = qa_mod.qa_agent(_state(
            "t-cap", files,
            retry_counts={"repair:src/j0.js": REPAIR_CAP_PER_FILE}))
        counts = degraded.drain("t-cap")
    finally:
        qa_mod.review_batch = _REAL_REVIEW
        qa_stream.discard("t-cap")
    assert counts.get("qa_rereview_capped") == 1, counts
    assert "Possibly Stale Reviews" in out["qa_report"]
    assert out["retry_counts"]["repair:src/j0.js"] == REPAIR_CAP_PER_FILE
    assert fake.reviewed_files.count("src/j0.js") == 1      # never exceeds cap


# ── 6. QA_MODE=batch is the exact pre-change behaviour ───────────────────────

def test_batch_mode_reviews_end_of_run_with_identical_chunks():
    _set_env(QA_MODE="batch")
    files = {f"src/k{i}.js": "x" * (i + 1) for i in range(7)}
    # offer() must be a no-op in batch mode — no stream may exist.
    qa_stream.offer("t-batch", "src/k0.js", "x", [])
    with qa_stream._registry_lock:
        assert "t-batch" not in qa_stream._streams
    fake = FakeReview()
    qa_mod.review_batch = fake
    try:
        out = qa_mod.qa_agent(_state("t-batch", files))
    finally:
        qa_mod.review_batch = _REAL_REVIEW
        degraded.drain("t-batch")
    expected = [[fp for fp, _ in b] for b in qa_mod._chunk_files(files)]
    assert [f for _, f in fake.calls] == expected           # same batches, same order
    assert out["qa_overlap_ratio"] == 0.0                   # serial tail by definition
    assert degraded.drain("t-batch") == {}                  # no degradation counted


# ── 7. permit discipline ─────────────────────────────────────────────────────

def test_qa_never_exceeds_concurrency_and_producers_never_block():
    _set_env(QA_MODE="incremental", QA_CONCURRENCY="1")
    files = {f"src/l{i}.js": "x" for i in range(9)}
    fake = FakeReview(delay=0.15)
    qa_mod.review_batch = fake
    try:
        t0 = time.monotonic()
        for fp in files:
            qa_stream.offer("t-permit", fp, files[fp], [])
        produce_elapsed = time.monotonic() - t0
        out = _run("t-permit", files, fake, offered=[], wait=3.0)
    finally:
        qa_mod.review_batch = _REAL_REVIEW
        qa_stream.discard("t-permit")
        degraded.drain("t-permit")
    assert fake.max_inflight <= 1, fake.max_inflight        # the permit is hard
    assert produce_elapsed < 0.5                            # generation never starved
    assert "**Files Reviewed**: 9" in out["qa_report"]


def test_concurrency_two_is_honoured_as_a_ceiling():
    _set_env(QA_MODE="incremental", QA_CONCURRENCY="2")
    files = {f"src/m{i}.js": "x" for i in range(12)}
    fake = FakeReview(delay=0.1)
    out = _run("t-permit2", files, fake, wait=3.0)
    assert fake.max_inflight <= 2, fake.max_inflight
    assert "**Files Reviewed**: 12" in out["qa_report"]


# ── 8. every fail-open path is counted ───────────────────────────────────────

def test_truncated_response_is_counted():
    from app.llm_router import _log_attempt
    _log_attempt("qa", "gemini/gemini-2.5-flash", 1, "ok", time.monotonic(),
                 usage={"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
                 project_id="t-trunc", truncated=True)
    assert degraded.drain("t-trunc").get("llm_truncated:qa") == 1


def test_stalled_stream_is_counted_and_swept():
    _set_env(QA_MODE="incremental", QA_STREAM_JOIN_TIMEOUT="0.05")
    files = {f"src/n{i}.js": "x" for i in range(3)}
    slow = FakeReview(delay=1.0)
    qa_mod.review_batch = slow
    try:
        for fp in files:
            qa_stream.offer("t-stall", fp, files[fp], [])
        out = qa_mod.qa_agent(_state("t-stall", files))
        counts = degraded.drain("t-stall")
    finally:
        qa_mod.review_batch = _REAL_REVIEW
        qa_stream.discard("t-stall")
    assert counts.get("qa_stream_stalled") == 1, counts
    # The join swept whatever the stalled stream had not delivered.
    assert "**Files Reviewed**: 3" in out["qa_report"]


if __name__ == "__main__":
    passed = failed = 0
    for name, fn in sorted(globals().items()):
        if not name.startswith("test_") or not callable(fn):
            continue
        _set_env()
        try:
            fn()
            print(f"  ok   {name}")
            passed += 1
        except Exception as e:                  # noqa: BLE001
            import traceback
            print(f"  FAIL {name}: {type(e).__name__}: {e}")
            traceback.print_exc()
            failed += 1
    _set_env()
    print(f"\n{passed} passed, {failed} failed. (0 API calls)")
    sys.exit(1 if failed else 0)
