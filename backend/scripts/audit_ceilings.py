"""Ceiling starvation audit — read-only report over metrics.db.

For every agent type: configured output ceiling (imported live from the call
site's constant, so this script cannot drift from config) vs the measured
completion-token distribution. Three starvation signals, in order of authority:

  1. truncated=1        — the provider's own finish_reason='length'. Exact.
  2. near-ceiling ok    — completion within epsilon of the ceiling with no
                          length flag (some providers under-report).
  3. primary 'error'    — the gemini-thinking signature: a starved answer
                          surfaces as an error + silent failover, not as a
                          visible truncation (measured 2026-08-03, QA at 3000).

Usage:  venv/bin/python3 scripts/audit_ceilings.py
"""
import os
import sqlite3
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.agents.architecture_agent import ARCHITECTURE_MAX_TOKENS
from app.agents.backend_coder_agent import BACKEND_FILE_MAX_TOKENS
from app.agents.database_agent import DATABASE_MAX_TOKENS
from app.agents.devops_agent import DEVOPS_MAX_TOKENS
from app.agents.frontend_coder_agent import FRONTEND_FILE_MAX_TOKENS
from app.agents.frontend_reviewer import REVIEW_MAX_TOKENS
from app.agents.planning_agent import PLANNING_MIN_TOKENS, PLANNING_TOKENS_CAP
from app.agents.qa_agent import QA_MAX_TOKENS
from app.agents.requirements_agent import REQUIREMENTS_MAX_TOKENS
from app.agents.research_agent import RESEARCH_MAX_TOKENS
from app.llm_router import MODELS, resolve_max_tokens
from app.observability.metrics_store import db_path

# agent -> (default-profile ceiling, note). Planning is dynamic; audit its floor
# and report the cap alongside.
CEILINGS = {
    "research":        (RESEARCH_MAX_TOKENS, ""),
    "requirements":    (REQUIREMENTS_MAX_TOKENS, ""),
    "architecture":    (ARCHITECTURE_MAX_TOKENS, ""),
    "planning":        (PLANNING_MIN_TOKENS, "dynamic, cap %d" % PLANNING_TOKENS_CAP),
    "frontend_code":   (FRONTEND_FILE_MAX_TOKENS, "per file"),
    "frontend_review": (REVIEW_MAX_TOKENS, ""),
    "backend_code":    (BACKEND_FILE_MAX_TOKENS, "per file"),
    "database":        (DATABASE_MAX_TOKENS, "per file"),
    "qa":              (QA_MAX_TOKENS, "per batch"),
    "devops":          (DEVOPS_MAX_TOKENS, "per file"),
}

# gemini-2.5-flash spends its budget on reasoning before any answer token, so
# its starvation presents as signal 3, not signal 1.
REASONING_MODELS = ("gemini-2.5-flash",)

# The offline test suites and fault-injection smoke runs write into the SAME
# metrics.db as real runs (found during the 2026-08-03 audit — e.g. 'e2e' and
# 'restart' hold 300+ fake rows, 't-trunc' plants literal truncated fixtures).
# Excluded by prefix so the audit reads reality, not the test suite.
TEST_PROJECT_PREFIXES = ("test", "e2e", "restart", "cachetest", "smoke",
                         "stale", "t-trunc", "local-tier-check")


def _exclude_clause():
    conds = ["project_id IS NOT NULL"]
    conds += ["project_id NOT LIKE '%s%%'" % p for p in TEST_PROJECT_PREFIXES]
    return " AND ".join(conds)


def epsilon(ceiling):
    return max(16, ceiling // 50)               # 2%, floor of 16 tokens


def percentile(values, p):
    if not values:
        return None
    values = sorted(values)
    return values[min(int(len(values) * p), len(values) - 1)]


def audit(conn):
    rows = []
    for agent, (ceiling, note) in CEILINGS.items():
        primary, _ = MODELS[agent]
        reasoning = any(m in primary for m in REASONING_MODELS)
        real = _exclude_clause()
        ok = [r[0] for r in conn.execute(
            "SELECT completion_tokens FROM agent_runs WHERE agent=? AND outcome='ok'"
            " AND completion_tokens IS NOT NULL AND COALESCE(model,'') != 'cache'"
            " AND " + real, (agent,))]
        truncated = conn.execute(
            "SELECT COUNT(*) FROM agent_runs WHERE agent=? AND truncated=1 AND " + real,
            (agent,)).fetchone()[0]
        # planning's ceiling is dynamic per file count — comparing completions
        # against its floor would flag every healthy large plan.
        near = 0 if agent == "planning" else \
            sum(1 for v in ok if v >= ceiling - epsilon(ceiling))
        primary_errors = conn.execute(
            "SELECT COUNT(*) FROM agent_runs WHERE agent=? AND outcome='error'"
            " AND model=? AND " + real, (agent, primary)).fetchone()[0]
        fast = resolve_max_tokens(agent, ceiling, fast_mode=True)
        measured_max = max(ok) if ok else None
        if not ok:
            verdict = "NO_DATA"
        elif truncated or near:
            verdict = "STARVATION_SUSPECT"
        elif reasoning and primary_errors:
            verdict = "CHECK_ERRORS"           # signal 3: needs interpretation
        else:
            verdict = "CLEAN"
        rows.append({
            "agent": agent, "model": primary.split("/", 1)[-1],
            "reasoning": reasoning, "ceiling": ceiling, "fast": fast,
            "note": note, "calls": len(ok), "max": measured_max,
            "p95": percentile(ok, 0.95), "truncated": truncated,
            "near": near, "primary_errors": primary_errors, "verdict": verdict,
        })
    return rows


def main():
    conn = sqlite3.connect(db_path)
    rows = audit(conn)
    hdr = ("agent", "model", "ceiling", "fast", "ok calls", "max", "p95",
           "trunc", "near-ceil", "prim-err", "verdict")
    print("| " + " | ".join(hdr) + " |")
    print("|" + "---|" * len(hdr))
    for r in rows:
        ceiling = "%d%s" % (r["ceiling"], " (%s)" % r["note"] if r["note"] else "")
        print("| %s | %s%s | %s | %d | %d | %s | %s | %d | %d | %d | %s |" % (
            r["agent"], r["model"], " (thinking)" if r["reasoning"] else "",
            ceiling, r["fast"], r["calls"],
            r["max"] if r["max"] is not None else "—",
            r["p95"] if r["p95"] is not None else "—",
            r["truncated"], r["near"], r["primary_errors"], r["verdict"]))
    conn.close()


if __name__ == "__main__":
    main()
