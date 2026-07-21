"""Day 29: run the whole pipeline on local models, with cloud disabled.

Drives the graph directly and auto-approves every gate, because the claim under
test is "the pipeline completes when no cloud provider will answer" — a claim
about the router and the graph, not about the approval UI. Cloud is disabled by
marking the daily budgets spent, so no cloud request is ever sent and the run
costs nothing.

Run:  python3 backend/scripts/local_pipeline_run.py "a short brief"
"""
import os
import sys
import time

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(_HERE))
sys.path.insert(0, _HERE)

os.environ["LLM_CACHE"] = "false"          # a cache hit would prove nothing
os.environ.setdefault("GENERATION_MODE", "parallel")   # exercise the local cap

from app import llm_router as R                                  # noqa: E402
from app.graph.pipeline import graph                             # noqa: E402
from app.graph.state import ProjectState                         # noqa: E402
# Same disabling method as the per-agent check, imported rather than repeated so
# the two proofs cannot drift into testing different things.
from local_tier_check import exhaust_cloud_budgets               # noqa: E402

BRIEF = (sys.argv[1] if len(sys.argv) > 1 else
         "A minimal personal bookmark manager: save a URL with a title and tags, "
         "list saved bookmarks, and filter them by tag.")
PROJECT_ID = f"local-run-{int(time.time())}"


def initial_state():
    import json
    return ProjectState(
        project_id=PROJECT_ID, brief=BRIEF, project_name="Local Model Run",
        optional_sections=json.dumps({"existing_solutions": False,
                                      "target_users": False, "market_risks": False}),
        fast_mode=False, research_report="", requirements_doc="", tech_stack="",
        architecture_doc="", implementation_plan="", excluded_tasks=[], file_list=[],
        generated_files={}, qa_report="", qa_issues_count=0, devops_files={},
        fix_counts={}, retry_counts={}, stage_history=[], regen_cycle=None,
        replan_after_architecture=False, skip_gate_1=False, failed_agent="",
        failure_context=None, current_stage="", human_feedback="",
        human_decision="", log=[], errors=[])


def main():
    if not R.ollama_models():
        print("No local models pulled — nothing to prove. Pull one first.")
        return 1
    exhaust_cloud_budgets()

    config = {"configurable": {"thread_id": PROJECT_ID}}
    started = time.monotonic()
    print(f"project {PROJECT_ID}\nlocal models: {', '.join(R.ollama_models())}\n")

    payload, gates = initial_state(), 0
    while True:
        for event in graph.stream(payload, config):
            for node, out in event.items():
                if node == "__interrupt__":
                    continue
                mark = time.monotonic() - started
                print(f"  [{mark:6.0f}s] {node}"
                      + (f" -> {out.get('current_stage')}" if isinstance(out, dict)
                         and out.get("current_stage") else ""))
        snapshot = graph.get_state(config)
        if not snapshot.next:
            break
        gate = snapshot.next[0]
        if not gate.startswith("human_gate_"):
            print(f"  stopped at non-gate node {gate} — treating as a failure")
            return 1
        gates += 1
        print(f"  [{time.monotonic() - started:6.0f}s] {gate}: auto-approve")
        graph.update_state(config, {"human_decision": "approve",
                                    "human_feedback": "", "regen_cycle": None})
        payload = None                      # resume from the checkpoint

    elapsed = time.monotonic() - started
    values = graph.get_state(config).values or {}
    usage = R.metrics_store.local_tier_usage(PROJECT_ID)
    files = values.get("generated_files") or {}
    errors = values.get("errors") or []

    print(f"\ncompleted in {elapsed / 60:.1f} min through {gates} gates")
    print(f"files generated: {len(files)}")
    print(f"tier attribution: {usage['local_calls']}/{usage['calls']} calls local "
          f"via {', '.join(usage['models']) or 'none'}")
    print(f"per-agent local calls: {usage['agents']}")
    if errors:
        print(f"errors ({len(errors)}):")
        for e in errors[:15]:
            print(f"  - {e}")
    # The run is only the proof it claims to be if NOTHING came from the cloud.
    if usage["calls"] and usage["local_calls"] != usage["calls"]:
        print("WARNING: a cloud tier served part of this run")
        return 1
    return 0 if files else 1


if __name__ == "__main__":
    sys.exit(main())
