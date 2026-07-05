"""
Verification test for optional sections behaviour + report quality.
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


def check_quality(report: str, case_label: str) -> bool:
    print(f"\n  Quality checks for: {case_label}")
    quality_checks = [
        (len(report) >= 800, f"Length >= 800 chars (got {len(report)})"),
        ("## Problem Space" in report, "Problem Space present"),
        ("## Technical Landscape" in report, "Technical Landscape present"),
        ("## Recommended Approach" in report, "Recommended Approach present"),
        ("## Research Confidence Score" in report, "Research Confidence Score present"),
        ("**Score**" in report, "Confidence Score has Score field"),
        (report.startswith("# Research Report:"), "Report starts with correct heading"),
        (not report.strip().startswith("Here is"), "No preamble before title"),
        (len([line for line in report.split('\n') if line.strip().startswith('##')]) >= 4,
         "At least 4 section headings present"),
    ]
    all_ok = True
    for condition, label in quality_checks:
        status = "✅" if condition else "❌"
        if not condition:
            all_ok = False
        print(f"    {status} {label}")
    return all_ok


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

    quality_ok = check_quality(report, case["label"])
    if not quality_ok:
        case_passed = False
        overall = False

    print(f"\n  -> {'✅ PASSED' if case_passed else '❌ FAILED'}")

print(f"\n{'='*60}")
print(f"FINAL RESULT: {'✅ ALL 5 CASES PASSED' if overall else '❌ FAILURES — do not merge to main'}")
print("=" * 60)

sys.exit(0 if overall else 1)
