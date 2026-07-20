"""Durable, vendor-independent per-attempt LLM metrics.

Deliberately separate from projects.db: Day 24 owns a project-delete path, and
analytics must not be cascaded away with the workflow rows. Tokens and latency
are the budget in a $0 free-tier system, so this data outlives the project that
produced it. This table is the ancestor of the vision doc's AgentRuns.

The grain is one row per ATTEMPT, not per call: a call that timed out on the
primary and succeeded on the fallback writes two rows, so the failed attempt's
latency survives and only the successful one carries tokens. Averaging over
outcome='ok' gives per-call cost; averaging over everything gives true spend.

Writes open their own connection (sqlite3 connections are not shareable across
threads, and the Day 20 coder workers run in a thread pool) and never raise —
instrumentation that breaks a run is worse than no instrumentation.
"""
import os
import sqlite3
from datetime import datetime, timezone

backend_dir = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
db_path = os.path.join(backend_dir, "metrics.db")

# Columns Day 26 groups and averages by. `label` carries the per-file identity
# for coder attempts (which file this call generated) and is NULL elsewhere.
_SCHEMA = """
CREATE TABLE IF NOT EXISTS agent_runs (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id      TEXT,
    agent           TEXT NOT NULL,
    model           TEXT,
    attempt         INTEGER,
    outcome         TEXT,
    prompt_tokens   INTEGER,
    completion_tokens INTEGER,
    total_tokens    INTEGER,
    latency_ms      INTEGER,
    context_chars   INTEGER,
    label           TEXT,
    created_at      TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_agent_runs_project ON agent_runs(project_id);
CREATE INDEX IF NOT EXISTS idx_agent_runs_agent   ON agent_runs(agent);
"""

# Columns added after the table shipped. CREATE TABLE IF NOT EXISTS silently
# skips an existing table, so a new column never reaches a Day 23 database
# without this. Each is applied once and ignored when already present.
_MIGRATIONS = [
    # Day 26: the provider said the completion stopped because it hit the token
    # ceiling (finish_reason='length'), i.e. the output was CUT OFF. Recorded per
    # attempt because a truncated file is a quality regression that otherwise
    # ships silently — it looks like a successful call.
    "ALTER TABLE agent_runs ADD COLUMN truncated INTEGER",
    # Day 26: 'hit' / 'miss' for the response cache; NULL for calls made before
    # caching existed, which is why hit-rate divides by non-NULL rows only.
    "ALTER TABLE agent_runs ADD COLUMN cache TEXT",
]


def _connect():
    conn = sqlite3.connect(db_path, timeout=5, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    # WAL lets the parallel coder workers write concurrently without lock errors.
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


def init_db():
    conn = _connect()
    with conn:
        conn.executescript(_SCHEMA)
        for stmt in _MIGRATIONS:
            try:
                conn.execute(stmt)
            except sqlite3.OperationalError:
                pass                            # already applied
    conn.close()


init_db()


def record_agent_run(agent: str, model: str = None, attempt: int = None,
                     outcome: str = None, project_id: str = None,
                     prompt_tokens: int = None, completion_tokens: int = None,
                     total_tokens: int = None, latency_ms: int = None,
                     context_chars: int = None, label: str = None,
                     truncated: bool = None, cache: str = None):
    """Persist one attempt. Never raises — a metrics failure must not fail a run."""
    if not is_enabled():
        return
    try:
        conn = _connect()
        with conn:
            conn.execute(
                "INSERT INTO agent_runs (project_id, agent, model, attempt, outcome,"
                " prompt_tokens, completion_tokens, total_tokens, latency_ms,"
                " context_chars, label, truncated, cache, created_at)"
                " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (project_id, agent, model, attempt, outcome, prompt_tokens,
                 completion_tokens, total_tokens, latency_ms, context_chars, label,
                 None if truncated is None else int(truncated), cache,
                 datetime.now(timezone.utc).isoformat()))
        conn.close()
    except Exception as e:                      # noqa: BLE001 — isolation is the point
        print(f"[Metrics] write failed (run unaffected): {e}", flush=True)


def is_enabled() -> bool:
    """OBSERVABILITY_ENABLED=false disables both backends for benchmarking."""
    return os.getenv("OBSERVABILITY_ENABLED", "true").lower() not in ("false", "0", "no")


# ── Query helpers (Day 26 imports these directly) ────────────────────────────
# Every helper returns [] / {} on an empty or absent table, so projects created
# before today — which have no metrics at all — degrade rather than error.

def _query(sql: str, params: tuple = ()) -> list:
    try:
        conn = _connect()
        rows = conn.execute(sql, params).fetchall()
        conn.close()
        return [dict(r) for r in rows]
    except Exception as e:                      # noqa: BLE001
        print(f"[Metrics] query failed: {e}", flush=True)
        return []


def avg_tokens_by_agent(project_id: str = None) -> list:
    """Day 26's headline question, in one query.

    Averages only successful attempts and only non-null usage, so providers that
    omit usage do not deflate the mean.
    """
    where = "WHERE outcome = 'ok'" + (" AND project_id = ?" if project_id else "")
    return _query(
        "SELECT agent,"
        " COUNT(*) AS calls,"
        " ROUND(AVG(prompt_tokens), 1)     AS avg_prompt_tokens,"
        " ROUND(AVG(completion_tokens), 1) AS avg_completion_tokens,"
        " SUM(COALESCE(prompt_tokens, 0))     AS total_prompt_tokens,"
        " SUM(COALESCE(completion_tokens, 0)) AS total_completion_tokens,"
        " SUM(CASE WHEN prompt_tokens IS NULL THEN 1 ELSE 0 END) AS missing_usage"
        f" FROM agent_runs {where}"
        " GROUP BY agent ORDER BY avg_prompt_tokens DESC",
        (project_id,) if project_id else ())


def latency_percentiles_by_agent(project_id: str = None) -> list:
    """p50/p95 per agent. Computed in Python — SQLite has no percentile function
    and the row counts here are in the hundreds, so sorting in memory is cheaper
    than a window-function query."""
    where = "WHERE latency_ms IS NOT NULL" + (" AND project_id = ?" if project_id else "")
    rows = _query(f"SELECT agent, latency_ms FROM agent_runs {where}",
                  (project_id,) if project_id else ())
    by_agent: dict = {}
    for r in rows:
        by_agent.setdefault(r["agent"], []).append(r["latency_ms"])
    out = []
    for agent, lats in by_agent.items():
        lats.sort()
        out.append({
            "agent": agent,
            "calls": len(lats),
            "p50_ms": lats[len(lats) // 2],
            "p95_ms": lats[min(int(len(lats) * 0.95), len(lats) - 1)],
            "max_ms": lats[-1],
        })
    return sorted(out, key=lambda r: r["p50_ms"], reverse=True)


def slowest_agents(n: int = 2, project_id: str = None) -> list:
    return latency_percentiles_by_agent(project_id)[:n]


def truncations(project_id: str = None) -> list:
    """Attempts the provider cut off at the token ceiling.

    A truncated completion is indistinguishable from a good one at the call site
    — it returns normally and gets written to disk — so it is only visible here.
    Day 26 measured architecture, requirements and two coder files all stopping
    within 5 tokens of their cap, which is why this exists.
    """
    where = "WHERE truncated = 1" + (" AND project_id = ?" if project_id else "")
    return _query(
        "SELECT agent, model, label, completion_tokens, created_at"
        f" FROM agent_runs {where} ORDER BY created_at DESC",
        (project_id,) if project_id else ())


def cache_stats(project_id: str = None) -> dict:
    """Hit rate over calls that could have been cached.

    Rows predating the cache carry NULL and are excluded, so an old project does
    not report a fake 0% hit rate.
    """
    where = "WHERE cache IS NOT NULL" + (" AND project_id = ?" if project_id else "")
    rows = _query(
        "SELECT SUM(CASE WHEN cache = 'hit' THEN 1 ELSE 0 END) AS hits,"
        f" COUNT(*) AS total FROM agent_runs {where}",
        (project_id,) if project_id else ())
    s = rows[0] if rows else {}
    hits, total = s.get("hits") or 0, s.get("total") or 0
    return {"hits": hits, "misses": total - hits, "total": total,
            "hit_rate": round(hits / total, 3) if total else None}


def tokens_used_today(provider: str = None) -> dict:
    """Tokens spent per provider since UTC midnight.

    The limit that actually stopped work on Day 26 was Groq's tokens-per-DAY
    allowance (95,966 of 100,000), not a per-minute rate. Providers report that
    only by refusing, so it is tracked here from what was actually consumed.

    Attributed by the model's provider prefix, and counting FAILED attempts too:
    a request that was rate-limited still had its prompt read by some providers,
    and under-counting the budget is the dangerous direction to be wrong in.
    """
    rows = _query(
        "SELECT model,"
        " SUM(COALESCE(prompt_tokens, 0) + COALESCE(completion_tokens, 0)) AS tokens,"
        " COUNT(*) AS calls"
        " FROM agent_runs WHERE created_at >= ? GROUP BY model",
        (datetime.now(timezone.utc).strftime("%Y-%m-%dT00:00:00+00:00"),))
    out: dict = {}
    for r in rows:
        p = (r["model"] or "").split("/", 1)[0].lower()
        entry = out.setdefault(p, {"tokens": 0, "calls": 0})
        entry["tokens"] += r["tokens"] or 0
        entry["calls"] += r["calls"] or 0
    return out.get(provider, {"tokens": 0, "calls": 0}) if provider else out


def run_summary(project_id: str) -> dict:
    """Per-run rollup for the UI panel. Cost is $0 on free tiers — tokens are
    the real budget line, so they are what this returns."""
    rows = _query(
        "SELECT COUNT(*) AS attempts,"
        " SUM(CASE WHEN outcome = 'ok' THEN 1 ELSE 0 END) AS ok_attempts,"
        " SUM(CASE WHEN outcome != 'ok' THEN 1 ELSE 0 END) AS failed_attempts,"
        " SUM(COALESCE(prompt_tokens, 0))     AS prompt_tokens,"
        " SUM(COALESCE(completion_tokens, 0)) AS completion_tokens,"
        " SUM(COALESCE(latency_ms, 0))        AS total_latency_ms"
        " FROM agent_runs WHERE project_id = ?", (project_id,))
    s = rows[0] if rows else {}
    attempts = s.get("attempts") or 0
    return {
        "attempts": attempts,
        "ok_attempts": s.get("ok_attempts") or 0,
        "failed_attempts": s.get("failed_attempts") or 0,
        "prompt_tokens": s.get("prompt_tokens") or 0,
        "completion_tokens": s.get("completion_tokens") or 0,
        "total_tokens": (s.get("prompt_tokens") or 0) + (s.get("completion_tokens") or 0),
        "total_latency_ms": s.get("total_latency_ms") or 0,
        "cost_usd": 0.0,                      # free tier; tokens are the budget
        "has_metrics": attempts > 0,          # False for pre-Day-23 projects
        "by_agent": avg_tokens_by_agent(project_id),
        "latency_by_agent": latency_percentiles_by_agent(project_id),
    }


def delete_project_metrics(project_id: str) -> int:
    """Hook for Day 24's delete endpoint. Metrics are NOT deleted automatically
    with a project — call this explicitly if the delete path should purge them."""
    try:
        conn = _connect()
        with conn:
            cur = conn.execute("DELETE FROM agent_runs WHERE project_id = ?", (project_id,))
        n = cur.rowcount
        conn.close()
        return n
    except Exception as e:                      # noqa: BLE001
        print(f"[Metrics] delete failed: {e}", flush=True)
        return 0
