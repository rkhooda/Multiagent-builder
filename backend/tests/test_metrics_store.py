"""Metrics store checks. Zero API cost — writes to a temp metrics.db.

Run: python3 tests/test_metrics_store.py
"""
import os
import sys
import tempfile
import threading

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Point the store at a throwaway DB before importing it.
_tmp = tempfile.mkdtemp()
import app.observability.metrics_store as ms  # noqa: E402

ms.db_path = os.path.join(_tmp, "metrics.db")
ms.init_db()

passed = failed = 0


def check(name, cond):
    global passed, failed
    if cond:
        passed += 1
    else:
        failed += 1
        print(f"  FAIL: {name}")


def rec(**kw):
    kw.setdefault("agent", "research")
    kw.setdefault("outcome", "ok")
    kw.setdefault("project_id", "p1")
    ms.record_agent_run(**kw)


# ── empty-table degradation (pre-Day-23 projects have no metrics) ────────────
check("empty avg_tokens_by_agent -> []", ms.avg_tokens_by_agent() == [])
check("empty latency percentiles -> []", ms.latency_percentiles_by_agent() == [])
s = ms.run_summary("nonexistent")
check("empty run_summary has_metrics False", s["has_metrics"] is False)
check("empty run_summary totals zero", s["total_tokens"] == 0 and s["attempts"] == 0)
check("empty run_summary still shaped", "by_agent" in s and s["cost_usd"] == 0.0)

# ── per-attempt grain: a failed attempt then a successful fallback ───────────
rec(agent="qa", model="nemotron", attempt=1, outcome="timeout", latency_ms=9000)
rec(agent="qa", model="gemini", attempt=1, outcome="ok", latency_ms=3000,
    prompt_tokens=20000, completion_tokens=1500, total_tokens=21500)
summ = ms.run_summary("p1")
check("both attempts recorded", summ["attempts"] == 2)
check("failed attempt counted", summ["failed_attempts"] == 1)
check("failed attempt latency retained", summ["total_latency_ms"] == 12000)
check("only successful attempt carries tokens", summ["prompt_tokens"] == 20000)

# ── null usage excluded from averages, not counted as zero ──────────────────
rec(agent="devops", attempt=1, outcome="ok", prompt_tokens=100, completion_tokens=10)
rec(agent="devops", attempt=1, outcome="ok", prompt_tokens=None, completion_tokens=None)
devops = [r for r in ms.avg_tokens_by_agent("p1") if r["agent"] == "devops"][0]
check("null usage excluded from mean (not averaged as 0)", devops["avg_prompt_tokens"] == 100.0)
check("null usage surfaced as missing_usage", devops["missing_usage"] == 1)
check("calls still counts the null-usage attempt", devops["calls"] == 2)

# ── failed attempts excluded from token averages ────────────────────────────
qa_rows = [r for r in ms.avg_tokens_by_agent("p1") if r["agent"] == "qa"]
check("avg_tokens_by_agent ignores failed attempts", qa_rows[0]["calls"] == 1)

# ── Day 26's headline query: ordering by input tokens ───────────────────────
check("avg_tokens_by_agent ranks token-hungriest first",
      ms.avg_tokens_by_agent("p1")[0]["agent"] == "qa")

# ── slowest agents ──────────────────────────────────────────────────────────
check("slowest_agents ranks by p50", ms.slowest_agents(1, "p1")[0]["agent"] == "qa")

# ── project isolation ───────────────────────────────────────────────────────
rec(agent="research", project_id="p2", latency_ms=10, prompt_tokens=5)
check("run_summary scoped to project", ms.run_summary("p2")["attempts"] == 1)

# ── concurrent writes (Day 20 coder workers share one store) ────────────────
def hammer(i):
    for j in range(20):
        ms.record_agent_run(agent="frontend_code", project_id="conc", outcome="ok",
                            label=f"file{i}.jsx", latency_ms=j, prompt_tokens=1)


threads = [threading.Thread(target=hammer, args=(i,)) for i in range(5)]
[t.start() for t in threads]
[t.join() for t in threads]
check("100 concurrent writes all landed (WAL, no lock loss)",
      ms.run_summary("conc")["attempts"] == 100)
labels = {r["label"] for r in ms._query(
    "SELECT DISTINCT label FROM agent_runs WHERE project_id='conc'")}
check("per-worker labels not cross-contaminated",
      labels == {f"file{i}.jsx" for i in range(5)})

# ── failure isolation: a broken DB path must not raise ──────────────────────
good, ms.db_path = ms.db_path, "/nonexistent-dir/metrics.db"
try:
    ms.record_agent_run(agent="research", project_id="p1")
    check("write failure swallowed, run unaffected", True)
except Exception:
    check("write failure swallowed, run unaffected", False)
check("query failure degrades to []", ms.avg_tokens_by_agent() == [])
ms.db_path = good

# ── OBSERVABILITY_ENABLED switch ────────────────────────────────────────────
before = ms.run_summary("p2")["attempts"]
os.environ["OBSERVABILITY_ENABLED"] = "false"
check("switch reports disabled", ms.is_enabled() is False)
ms.record_agent_run(agent="research", project_id="p2")
check("disabled store writes nothing", ms.run_summary("p2")["attempts"] == before)
os.environ["OBSERVABILITY_ENABLED"] = "true"
check("switch re-enables", ms.is_enabled() is True)

# ── local-tier attribution (Day 29) ─────────────────────────────────────────
# The denominator decides whether the honesty banner tells the truth: a failed
# cloud attempt that was retried locally must not inflate the total, and a cache
# hit did not run on any tier at all.
rec(agent="architecture", model="ollama/qwen3:4b", project_id="p3")
rec(agent="devops", model="ollama/qwen2.5-coder:7b", project_id="p3")
rec(agent="research", model="gemini/gemini-2.5-flash", project_id="p3")
rec(agent="research", model="groq/llama-3.3-70b-versatile", outcome="rate_limit", project_id="p3")
rec(agent="research", model="cache", project_id="p3")
u = ms.local_tier_usage("p3")
check("counts local calls", u["local_calls"] == 2)
check("failed attempts excluded from denominator", u["calls"] == 3)
check("cache hits excluded from denominator", u["calls"] == 3)
check("per-agent attribution names the agent", u["agents"] == {"architecture": 1, "devops": 1})
check("reports which local models ran", u["models"] == ["qwen2.5-coder:7b", "qwen3:4b"])
check("all-cloud run reports no local usage", ms.local_tier_usage("p1")["local_calls"] == 0)
check("local usage rides on run_summary", ms.run_summary("p3")["local"]["local_calls"] == 2)

# ── deletion hook (Day 24) ──────────────────────────────────────────────────
n = ms.delete_project_metrics("p2")
check("delete removes only that project's rows", n == before)
check("other projects untouched", ms.run_summary("p1")["attempts"] > 0)

print(f"\n{passed} passed, {failed} failed")
sys.exit(1 if failed else 0)
