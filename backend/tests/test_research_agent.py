"""
Run this directly to test the research agent without the full pipeline.
Usage: cd backend && python tests/test_research_agent.py
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dotenv import load_dotenv

load_dotenv()

from app.agents.research_agent import research_agent
from app.agents.research_validation import REQUIRED_SECTIONS, validate_research_report


test_state = {
    "project_id": "test-research-001",
    "project_name": "FreelanceFlow",
    "brief": (
        "A web app that helps freelancers track billable hours by project and client, "
        "generate professional PDF invoices, send them via email, and accept payment "
        "through Stripe. Target users are solo freelancers and small agencies. "
        "Must include a dashboard showing outstanding invoices, paid invoices, and monthly revenue."
    ),
    "log": [],
    "errors": [],
}


def print_assertion(label: str, passed: bool, detail: str):
    status = "PASS" if passed else "FAIL"
    print(f"[{status}] {label}: {detail}")


print("=" * 60)
print("Testing Research Agent")
print("=" * 60)

result = research_agent(test_state)

print("\n" + "=" * 60)
print("RESULT SUMMARY")
print("=" * 60)
print(f"Report length: {len(result['research_report'])} characters")
print(f"Log entries: {result['log']}")
print(f"Errors: {result['errors']}")
print(f"Next stage: {result['current_stage']}")

is_valid, missing = validate_research_report(result["research_report"])
if is_valid:
    print("\nOK All required sections present")
else:
    print(f"\nMissing sections: {missing}")
    print("Go back and fix prompts/research_agent.md before continuing")

print("\n" + "=" * 60)
print("REPORT PREVIEW (first 1000 chars)")
print("=" * 60)
print(result["research_report"][:1000])
print("\n...")
print("\n" + "=" * 60)
print("REPORT END (last 500 chars)")
print("=" * 60)
print(result["research_report"][-500:])

print("\n" + "=" * 60)
print("FINAL ASSERTIONS")
print("=" * 60)

print_assertion(
    "Minimum length",
    len(result["research_report"]) >= 800,
    f"len={len(result['research_report'])}",
)
print_assertion(
    "Problem Space present",
    any("## Problem Space".lower() in line.lower() for line in result["research_report"].split("\n")),
    "expects Problem Space heading",
)
print_assertion(
    "Existing Solutions or Competitors present",
    (
        "## Existing Solutions".lower() in result["research_report"].lower()
        or "## Competitors".lower() in result["research_report"].lower()
    ),
    "expects Existing Solutions or Competitors heading",
)
print_assertion(
    "Target Users present",
    "## Target Users".lower() in result["research_report"].lower(),
    "expects Target Users heading",
)
print_assertion(
    "Key Risks present",
    "## Key Risks".lower() in result["research_report"].lower(),
    "expects Key Risks heading",
)
print_assertion(
    "Errors empty",
    result["errors"] == [],
    f"errors={result['errors']}",
)
print_assertion(
    "Current stage advanced",
    result["current_stage"] == "requirements",
    f"current_stage={result['current_stage']}",
)
print_assertion(
    "Log has at least 2 entries",
    len(result["log"]) >= 2,
    f"log_count={len(result['log'])}",
)
print_assertion(
    "Validator sections all present",
    is_valid,
    f"missing={missing}",
)
print_assertion(
    "Required section count",
    len(REQUIRED_SECTIONS) == 7,
    f"count={len(REQUIRED_SECTIONS)}",
)
