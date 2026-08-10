"""Content-addressed LLM response cache (Day 26).

WHY. The pipeline's scarcest resource is free-tier quota, and the way quota is
actually wasted is repetition: a restart-from-stage re-runs every upstream agent
whose inputs did not change, and a re-run of the same brief regenerates
identical work. Day 26 measured 75% of all attempts being rate-limited, so a
call not made is worth more than a call made faster.

KEY (ponytail pass). The key is agent_type + max_tokens + messages, hashed.

  - The MODEL is deliberately NOT in the key. Which model answers varies run to
    run — the fallback chain moves to gemini when groq is rate-limited — so
    keying on it would miss precisely when the restart is meant to hit. The
    cached value is the answer to the question, not one model's answer to it.
  - TEMPERATURE is not in the key either: it is hardcoded at the single call
    site and has never varied. Keying on a constant is config for a value that
    never changes.
  - max_tokens IS in the key, because fast mode halves it and must not be served
    a normal-mode response (or vice versa).

DETERMINISM. temperature is 0.3, so identical inputs do not produce identical
outputs. Caching therefore makes re-runs STABLE, which is the desired behaviour
for a restart (the stages you did not change should not silently change) and the
WRONG behaviour when the user explicitly asked for something different. That
distinction is the whole safety story, and it is handled by the bypass rules:

  - feedback regeneration (gate 'edit'/'back') injects the feedback text into
    the messages, so the key changes and the call MISSES naturally. Verified by
    test, not assumed.
  - the per-file "Request AI Fix" path passes use_cache=False, because repeating
    the same instruction there means "try again", not "tell me what you said".

SCOPE is global. The key captures everything that determines the output, so two
projects that ask an identical question can safely share an answer. project_id is
recorded only so a per-project clear is possible.
"""
import hashlib
import json
import os
from datetime import datetime, timezone

from .metrics_store import _connect, is_enabled

_SCHEMA = """
CREATE TABLE IF NOT EXISTS llm_cache (
    key         TEXT PRIMARY KEY,
    agent       TEXT,
    project_id  TEXT,
    response    TEXT NOT NULL,
    hits        INTEGER NOT NULL DEFAULT 0,
    created_at  TEXT NOT NULL
);
"""


def init_db():
    try:
        conn = _connect()
        with conn:
            conn.executescript(_SCHEMA)
        conn.close()
    except Exception as e:                      # noqa: BLE001
        print(f"[Cache] init failed (caching disabled): {e}", flush=True)


init_db()


def enabled() -> bool:
    """LLM_CACHE=false turns caching off — needed for honest A/B timing runs."""
    return (is_enabled()
            and os.getenv("LLM_CACHE", "true").lower() not in ("false", "0", "no"))


def make_key(agent_type: str, messages: list, max_tokens: int) -> str:
    payload = json.dumps(
        {"agent": agent_type, "max_tokens": max_tokens, "messages": messages},
        sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _count_fault(project_id, kind: str):
    """A cache fault degrades to a live call — correct, but it must be COUNTED
    (Improvement 02): a cache that silently stops working looks exactly like a
    healthy run with a 0% hit rate. Rides the existing degraded_events drain."""
    try:
        from . import degraded
        degraded.record(project_id, f"cache_fault:{kind}")
    except Exception:                           # noqa: BLE001 — accounting never fails a call
        pass


def get(key: str, project_id: str = None):
    """Cached response, or None. Never raises — a cache fault must degrade to a
    normal call, never fail one. `project_id` is attribution for the fault
    counter only; it plays no part in the lookup."""
    if not enabled():
        return None
    conn = None
    try:
        conn = _connect()
        with conn:
            row = conn.execute(
                "SELECT response FROM llm_cache WHERE key = ?", (key,)).fetchone()
            if row is not None:
                conn.execute("UPDATE llm_cache SET hits = hits + 1 WHERE key = ?",
                             (key,))
        return row["response"] if row is not None else None
    except Exception as e:                      # noqa: BLE001
        print(f"[Cache] read failed (falling back to a live call): {e}", flush=True)
        _count_fault(project_id, "read")
        return None
    finally:
        if conn is not None:
            conn.close()


def put(key: str, agent: str, response: str, project_id: str = None):
    # An empty response is a provider failure mode here (Day 18 saw a model
    # return ""), not an answer. Caching it would make one bad call permanent.
    if not enabled() or not (response or "").strip():
        return
    try:
        conn = _connect()
        with conn:
            conn.execute(
                "INSERT OR IGNORE INTO llm_cache"
                " (key, agent, project_id, response, created_at) VALUES (?,?,?,?,?)",
                (key, agent, project_id, response,
                 datetime.now(timezone.utc).isoformat()))
        conn.close()
    except Exception as e:                      # noqa: BLE001
        print(f"[Cache] write failed (run unaffected): {e}", flush=True)
        _count_fault(project_id, "write")


def clear(project_id: str = None) -> int:
    """Drop cached responses — all of them, or one project's.

    A per-project clear can remove a row another project would also have hit,
    since identical inputs share one row. That costs a regeneration, never
    correctness.
    """
    try:
        conn = _connect()
        with conn:
            if project_id:
                cur = conn.execute("DELETE FROM llm_cache WHERE project_id = ?",
                                   (project_id,))
            else:
                cur = conn.execute("DELETE FROM llm_cache")
        n = cur.rowcount
        conn.close()
        return n
    except Exception as e:                      # noqa: BLE001
        print(f"[Cache] clear failed: {e}", flush=True)
        return 0


def stats() -> dict:
    """Stored entries and the reuse they have earned."""
    try:
        conn = _connect()
        row = conn.execute(
            "SELECT COUNT(*) AS entries, COALESCE(SUM(hits), 0) AS hits FROM llm_cache"
        ).fetchone()
        conn.close()
        return {"entries": row["entries"], "hits": row["hits"], "enabled": enabled()}
    except Exception:                           # noqa: BLE001
        return {"entries": 0, "hits": 0, "enabled": enabled()}
