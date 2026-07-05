"""
Final verification test for optional sections behaviour.
Usage: cd backend && python3 tests/test_research_agent_sections.py
"""
import sys
import os
import json

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from dotenv import load_dotenv
load_dotenv()

from app.agents.research_agent import research_agent

BASE = {
    "project_id": "test-001",
    "project_name": "FreelanceFlow",
    "brief": "A web app for freelancers to track billable hours and generate Stripe invoices.",
    "log": [],
    "errors": [],
}

CASES = [
    {
        "label": "NO checkboxes selected — only 5 permanent sections",
        "optional_sections": json.dumps({"existing_solutions": False, "target_users": False, "market_risks": False}),
        "must_contain": [
            "## Problem Space",
            "## Technical Landscape",
            "## Key Risks",
            "### Technical Risks",
            "### Execution Risks",
            "## Recommended Approach",
            "## Research Confidence Score",
        ],
        "must_not_contain": ["## Existing Solutions", "## Target Users", "### Market Risks"],
    },
    {
        "label": "ONLY Existing Solutions checked",
        "optional_sections": json.dumps({"existing_solutions": True, "target_users": False, "market_risks": False}),
        "must_contain": ["## Problem Space", "## Existing Solutions", "## Recommended Approach"],
        "must_not_contain": ["## Target Users", "### Market Risks"],
    },
    {
        "label": "ONLY Target Users checked",
        "optional_sections": json.dumps({"existing_solutions": False, "target_users": True, "market_risks": False}),
        "must_contain": ["## Problem Space", "## Target Users", "## Recommended Approach"],
        "must_not_contain": ["## Existing Solutions", "### Market Risks"],
    },
    {
        "label": "ONLY Market Risks checked",
        "optional_sections": json.dumps({"existing_solutions": False, "target_users": False, "market_risks": True}),
        "must_contain": ["## Problem Space", "### Market Risks", "## Recommended Approach"],
        "must_not_contain": ["## Existing Solutions", "## Target Users"],
    },
    {
        "label": "ALL checkboxes selected — all 8 sections",
        "optional_sections": json.dumps({"existing_solutions": True, "target_users": True, "market_risks": True}),
        "must_contain": [
            "## Problem Space",
            "## Existing Solutions",
            "## Target Users",
            "## Technical Landscape",
            "### Market Risks",
            "### Execution Risks",
            "## Recommended Approach",
            "## Research Confidence Score",
        ],
        "must_not_contain": [],
    },
]

overall = True

for i, case in enumerate(CASES):
    print(f"\n{'='*60}")
    print(f"CASE {i+1}: {case['label']}")
    print("=" * 60)

    state = {**BASE, "optional_sections": case["optional_sections"], "project_id": f"test-{i+1}"}
    result = research_agent(state)
    report = result["research_report"]
    print(f"Report length: {len(report)} chars")

    case_passed = True

    for heading in case["must_contain"]:
        found = heading.lower() in report.lower()
        print(f"  {'✅' if found else '❌'} Present:  '{heading}'")
        if not found:
            case_passed = False
            overall = False

    for heading in case["must_not_contain"]:
        found = heading.lower() in report.lower()
        print(f"  {'❌ FOUND — should be absent' if found else '✅'} Absent:   '{heading}'")
        if found:
            case_passed = False
            overall = False

    print(f"\n  → {'✅ PASSED' if case_passed else '❌ FAILED'}")

print(f"\n{'='*60}")
print(f"FINAL RESULT: {'✅ ALL 5 CASES PASSED' if overall else '❌ FAILURES — do not merge to main'}")
print("=" * 60)

sys.exit(0 if overall else 1)
