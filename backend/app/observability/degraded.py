"""Fail-open accounting (Improvement 02, ponytail #4).

The lesson this encodes: Improvement 01's reviewer truncated on every attempt,
fail-open turned each truncation into "pass", and the feature silently did
nothing for 10 of 14 files while every dashboard read healthy. Fail-open is
correct behaviour; fail-open UNCOUNTED is not.

So: one process-wide counter of degradation events, keyed by project, that any
code path can increment — including pure worker threads that must not touch
shared LangGraph state (Day 20 coordinator-only discipline). The stage_node
wrapper drains it into state["degraded_events"] after every agent, which is the
single choke point that already does per-stage bookkeeping, so no agent needs
wiring. From state it reaches the metrics panel and the Gate 4 summary.

ponytail: a dict and a lock, same shape as llm_router._abandoned_projects. No
event bus, no timestamps, no persistence of its own — counts land in the
checkpoint via state, and per-call detail already lives in metrics.db.
"""
import threading

_lock = threading.Lock()
_events: dict = {}          # project_id -> {event_name: count}


def record(project_id: str, event: str, count: int = 1) -> None:
    """Count one degradation. Safe from any thread; never raises.

    `count` exists for value-bearing entries (e.g. failover token totals,
    where the number IS the payload) — same dict, same drain path, no second
    collector.
    """
    if not project_id or not event or count <= 0:
        return
    with _lock:
        bucket = _events.setdefault(project_id, {})
        bucket[event] = bucket.get(event, 0) + count


def drain(project_id: str) -> dict:
    """Return and clear this project's pending counts (coordinator/node only)."""
    with _lock:
        return _events.pop(project_id, {})


def merge(into: dict, pending: dict) -> dict:
    """Fold drained counts into an existing state dict, returning a new dict."""
    merged = dict(into or {})
    for event, count in (pending or {}).items():
        merged[event] = merged.get(event, 0) + count
    return merged
