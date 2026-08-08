#!/usr/bin/env python3
"""Generate one profile's coding phases for real, then leave it scorable.

Improvement 03, Phase 3. Drives plan → coder phases for a single brief under a
chosen Stack Profile, writing files to outputs/{project_id}/ and persisting
state to the LangGraph checkpoint so scripts/score_project.py can read it
afterwards with zero further API cost.

Deliberately NOT a full pipeline run: research, requirements, architecture, QA
and devops are skipped. The claim under test is "this profile's coders produce
usable files for their stack", and the scarce Groq pool is what the coders
spend. Everything skipped is either non-scarce (planning is measured separately
by plan_shape_test.py) or irrelevant to that claim.

  python3 scripts/generate_profile.py static-site
  python3 scripts/generate_profile.py node-express-api --id my-run
  python3 scripts/score_project.py <printed project id>
"""
import argparse
import json
import os
import sys

BACKEND_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BACKEND_DIR)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

os.environ.setdefault("LLM_CACHE", "false")     # a cache hit would prove nothing

from app.agents.planning_agent import planning_agent           # noqa: E402
from app.profiles import get_profile                           # noqa: E402
from plan_shape_test import BRIEFS, stub_architecture          # noqa: E402

# Which brief drives each profile, and which coder agents run for it.
TARGETS = {
    "static-site": {
        "brief_key": "static_site_profile",
        "agents": ["frontend"],
    },
    "node-express-api": {
        "brief_key": "express_profile",
        "agents": ["database", "backend"],
    },
    "react-fastapi": {
        "brief_key": "full_stack",
        "agents": ["frontend", "database", "backend"],
    },
}

AGENT_FNS = {
    "frontend": "app.agents.frontend_coder_agent:frontend_coder_agent",
    "backend": "app.agents.backend_coder_agent:backend_coder_agent",
    "database": "app.agents.database_agent:database_agent",
}


def _load(dotted: str):
    module, _, name = dotted.partition(":")
    return getattr(__import__(module, fromlist=[name]), name)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("profile", choices=sorted(TARGETS))
    ap.add_argument("--id", help="project id (default: gen-<profile>)")
    args = ap.parse_args()

    target = TARGETS[args.profile]
    spec = BRIEFS[target["brief_key"]]
    profile = get_profile(args.profile)
    project_id = args.id or f"gen-{args.profile}"

    state = {
        "project_id": project_id,
        "project_name": spec["name"],
        "brief": spec["brief"],
        "architecture_doc": stub_architecture(spec),
        "file_list": spec["files"],
        "tech_stack": json.dumps({"frontend": "", "backend": "", "database": ""}),
        "stack_profile": args.profile,
        "generated_files": {},
        "log": [], "errors": [], "retry_counts": {}, "review_results": {},
    }

    print(f"=== planning ({profile.label}) ===", flush=True)
    state.update(planning_agent(state))
    tasks = json.loads(state["implementation_plan"])
    print(f"    {len(tasks)} tasks: "
          + ", ".join(f"{p.name}={sum(1 for t in tasks if t['phase'] == p.name)}"
                      for p in profile.phases), flush=True)

    for phase in target["agents"]:
        if not any(t["phase"] == phase for t in tasks):
            print(f"=== {phase}: no tasks, skipping (absent phase) ===", flush=True)
            continue
        print(f"=== {phase} coder ===", flush=True)
        try:
            state.update(_load(AGENT_FNS[phase])(state))
        except Exception as e:                              # noqa: BLE001
            print(f"    STAGE FAILED: {type(e).__name__}: {e}", flush=True)
            state.setdefault("errors", []).append(f"{phase}: {e}")

    # Persist so score_project.py can read the plan from the checkpoint.
    from app.graph.pipeline import graph
    config = {"configurable": {"thread_id": project_id}}
    graph.update_state(config, {
        "project_id": project_id,
        "project_name": spec["name"],
        "implementation_plan": state["implementation_plan"],
        "file_list": spec["files"],
        "generated_files": state.get("generated_files", {}),
        "stack_profile": args.profile,
    })

    files = state.get("generated_files", {})
    print(f"\n{len(files)} files written to outputs/{project_id}/")
    for path in sorted(files):
        print(f"  {path}")
    if state.get("errors"):
        print(f"\n{len(state['errors'])} errors:")
        for e in state["errors"][:10]:
            print(f"  {e}")
    print(f"\nScore it:  python3 scripts/score_project.py {project_id}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
