"""
Tests the research agent with different optional section combinations.
Usage: cd backend && python tests/test_research_agent_sections.py
"""
import sys
import os
import json

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from dotenv import load_dotenv
load_dotenv()

from app.agents.research_agent import research_agent

BASE_STATE = {
    "project_id": "test-sections",
    "project_name": "FreelanceFlow",
    "brief": "A web app for freelancers to track billable hours and generate Stripe invoices.",
    "log": [],
    "errors": [],
}

TEST_CASES = [
    {
        "label": "Defaults only (no optional sections)",
        "optional_sections": json.dumps(
            {"existing_solutions": False, "target_users": False, "market_risks": False}
        ),
        "must_contain": [
            "## Problem Space",
            "## Technical Landscape",
            "## Recommended Approach",
            "## Research Confidence Score",
        ],
        "must_not_contain": [
            "## Existing Solutions",
            "## Target Users",
            "Market Risks",
        ],
    },
    {
        "label": "Competitors + Market Risks only",
        "optional_sections": json.dumps(
            {"existing_solutions": True, "target_users": False, "market_risks": True}
        ),
        "must_contain": [
            "## Problem Space",
            "## Existing Solutions",
            "Market Risks",
            "## Recommended Approach",
        ],
        "must_not_contain": ["## Target Users"],
    },
    {
        "label": "All sections enabled",
        "optional_sections": json.dumps(
            {"existing_solutions": True, "target_users": True, "market_risks": True}
        ),
        "must_contain": [
            "## Problem Space",
            "## Existing Solutions",
            "## Target Users",
            "Market Risks",
            "## Recommended Approach",
        ],
        "must_not_contain": [],
    },
]

all_passed = True

for i, case in enumerate(TEST_CASES):
    print(f"\n{'='*60}")
    print(f"TEST {i+1}: {case['label']}")
    print("=" * 60)

    state = {**BASE_STATE, "optional_sections": case["optional_sections"]}
    result = research_agent(state)
    report = result["research_report"]

    print(f"Report length: {len(report)} chars")
    case_passed = True

    for must in case["must_contain"]:
        found = must.lower() in report.lower()
        status = "✅" if found else "❌"
        if not found:
            case_passed = False
            all_passed = False
        print(f"  {status} Contains '{must}'")

    for must_not in case["must_not_contain"]:
        found = must_not.lower() in report.lower()
        status = "❌ FOUND (should be absent)" if found else "✅ Correctly absent"
        if found:
            case_passed = False
            all_passed = False
        print(f"  {status}: '{must_not}'")

    print(f"\n  Result: {'✅ PASSED' if case_passed else '❌ FAILED'}")

print(f"\n{'='*60}")
print(f"OVERALL: {'✅ ALL TESTS PASSED' if all_passed else '❌ SOME TESTS FAILED'}")
print("=" * 60)

sys.exit(0 if all_passed else 1)
