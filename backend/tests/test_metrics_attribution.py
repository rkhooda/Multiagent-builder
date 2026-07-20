"""Parallel-attribution proof: metrics rows from concurrent coder workers must
name the file that actually produced them.

This is the subtle correctness bug the explicit-identity design exists to
prevent — thread-locals and ambient context do not map back across the thread
pool the Day 20 runner uses, so a wrong design silently attributes every row to
whichever file finished last.

Zero API cost: FAULT_INJECTION makes call_llm return canned content without
touching a provider, while still writing a metrics row per attempt.

Run: python3 tests/test_metrics_attribution.py
"""
import json
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

os.environ["FAULT_INJECTION"] = "syntaxerr:frontend_code:99"
os.environ.setdefault("LANGCHAIN_TRACING_V2", "false")

import app.observability.metrics_store as ms  # noqa: E402

ms.db_path = os.path.join(tempfile.mkdtemp(), "metrics.db")
ms.init_db()

from app.agents.frontend_coder_agent import frontend_coder_agent  # noqa: E402

passed = failed = 0


def check(name, cond):
    global passed, failed
    if cond:
        passed += 1
    else:
        failed += 1
        print(f"  FAIL: {name}")


FILES = [f"frontend/src/components/Comp{i}.jsx" for i in range(6)]
TASKS = [{"id": f"fe_{i}", "filepath": p, "phase": "frontend",
          "description": f"Component {i}", "requires": []}
         for i, p in enumerate(FILES)]

state = {
    "project_id": "attr-test",
    "project_name": "AttrTest",
    "architecture_doc": "React SPA.",
    "tech_stack": "React",
    "implementation_plan": json.dumps(TASKS),
    "file_list": FILES,
    "generated_files": {}, "devops_files": {},
    "log": [], "errors": [], "retry_counts": {}, "stage_history": [],
}

frontend_coder_agent(state)

rows = ms._query("SELECT agent, label, project_id, outcome, latency_ms"
                 " FROM agent_runs WHERE project_id = 'attr-test'")

check("metrics rows were written by the parallel phase", len(rows) >= len(FILES))
check("every row attributed to this project",
      all(r["project_id"] == "attr-test" for r in rows))
check("every row attributed to the frontend_code agent",
      all(r["agent"] == "frontend_code" for r in rows))

labels = {r["label"] for r in rows}
check("no row lost its file label", all(r["label"] for r in rows))
check("every generated file appears exactly once as a label",
      labels == set(FILES))
check("no cross-contamination: label count == file count",
      len(labels) == len(FILES))

# The real failure mode this guards: all rows collapsing onto one file.
check("labels are distinct, not all the last-finished file", len(labels) > 1)
check("latency recorded per attempt",
      all(r["latency_ms"] is not None for r in rows))

per_file = ms._query(
    "SELECT label, COUNT(*) AS n FROM agent_runs"
    " WHERE project_id='attr-test' GROUP BY label")
check("each file has at least one attempt row", all(r["n"] >= 1 for r in per_file))

# ── per-agent timeouts (Day 25) ──────────────────────────────────────────────
# Regression guard, not a style check. Planning's only recorded success took
# 84.5s; at the old uniform 90s ceiling it timed out on nearly every attempt and
# the pipeline could not get past planning at all. If these shrink back toward
# the default, the large-output agents silently become unrunnable again.
from app.llm_router import AGENT_TIMEOUT_SECONDS, DEFAULT_TIMEOUT_SECONDS  # noqa: E402

check("planning clears its observed 84.5s runtime with headroom",
      AGENT_TIMEOUT_SECONDS.get("planning", DEFAULT_TIMEOUT_SECONDS) >= 180)
check("qa clears its observed 354s runtime",
      AGENT_TIMEOUT_SECONDS.get("qa", DEFAULT_TIMEOUT_SECONDS) >= 360)
check("small-output agents still use the default",
      AGENT_TIMEOUT_SECONDS.get("research", DEFAULT_TIMEOUT_SECONDS) == DEFAULT_TIMEOUT_SECONDS)

print(f"\n{passed} passed, {failed} failed")
sys.exit(1 if failed else 0)
