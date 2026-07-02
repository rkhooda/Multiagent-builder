"""
Test the requirements agent in isolation without running the full pipeline.
Usage: cd backend && python3 tests/test_requirements_agent.py
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dotenv import load_dotenv
load_dotenv()

MOCK_RESEARCH_REPORT = """
## Problem Space
Freelancers consistently struggle with the administrative burden of tracking billable hours
and generating invoices. Most use spreadsheets or paper which leads to lost hours, delayed
payments, and unprofessional-looking invoices. The invoicing market is fragmented between
overly complex accounting tools and too-simple solutions.

## Existing Solutions & Competitors
| Competitor | Strengths | Weaknesses | Market Position |
|---|---|---|---|
| FreshBooks | Full-featured, polished UI | Expensive ($15+/month), overkill for solos | SMB accounting leader |
| Wave | Free tier, decent invoicing | Limited time tracking, no Stripe native | Small business free tool |
| Toggl Track | Best-in-class time tracking | No invoicing built in | Time tracking specialist |
| Harvest | Good invoicing + time tracking combo | $12/month, dated UI | Mid-market time+billing |

## Target Users
Primary persona: Solo freelance developer or designer, 2-8 years experience,
working with 3-10 active clients simultaneously. Pain points: forgetting to log hours,
spending hours formatting invoices in Word, chasing late payments manually.

## Technical Landscape
React for frontend (widely adopted in freelance community), FastAPI or Node.js for
backend, Stripe for payments (developer-friendly API), PostgreSQL for relational data,
SendGrid or Resend for transactional email, PDF generation via WeasyPrint or Puppeteer.

## Key Risks
Technical: PDF generation can be complex cross-platform. Stripe webhook reliability.
Market: High competition from established free tools. Execution: Scope creep beyond MVP.

## Recommended Approach
Build a focused MVP targeting solo freelancers. Prioritise: time tracking with timer,
clean invoice generation, Stripe payment links. Keep accounting features out of v1.

## Research Confidence Score
High — this is a well-understood market with clear user pain points and established
technical solutions.
"""

test_state = {
    "project_id": "test-requirements-001",
    "project_name": "FreelanceFlow",
    "brief": (
        "A web app that helps freelancers track billable hours by project and client, "
        "generate professional PDF invoices, send them via email, and accept payment "
        "through Stripe. Target users are solo freelancers and small agencies. "
        "Must include a dashboard showing outstanding invoices, paid invoices, and monthly revenue."
    ),
    "research_report": MOCK_RESEARCH_REPORT,
    "log": ["research_agent: completed — 2847 char report"],
    "errors": []
}

print("=" * 60)
print("Testing Requirements Agent")
print("=" * 60)

from app.agents.requirements_agent import requirements_agent, validate_requirements_doc

result = requirements_agent(test_state)

print("\n" + "=" * 60)
print("RESULT SUMMARY")
print("=" * 60)
print(f"Requirements doc length: {len(result['requirements_doc'])} characters")
print(f"Tech stack stored: {result['tech_stack'][:200] if result['tech_stack'] else 'MISSING'}")
print(f"Next stage: {result['current_stage']}")
print(f"Log entries: {len(result['log'])}")
print(f"Errors: {result['errors']}")

print("\n" + "=" * 60)
print("SECTION VALIDATION")
print("=" * 60)
is_valid, missing = validate_requirements_doc(result['requirements_doc'])
if is_valid:
    print("✅ All required sections present")
else:
    print(f"❌ Missing sections: {missing}")

print("\n" + "=" * 60)
print("TECH STACK EXTRACTED")
print("=" * 60)
import json
try:
    stack = json.loads(result['tech_stack'])
    for key, val in stack.items():
        print(f"  {key}: {val}")
    print("✅ Tech stack JSON is valid")
except Exception as e:
    print(f"❌ Tech stack JSON parse failed: {e}")

print("\n" + "=" * 60)
print("ASSERTIONS")
print("=" * 60)
assertions = [
    (len(result['requirements_doc']) >= 800, "Requirements doc >= 800 chars"),
    (result['current_stage'] == "architecture", "Next stage is architecture"),
    (result['tech_stack'] != "", "Tech stack is not empty"),
    (len(result['errors']) == 0, "No errors"),
    (is_valid, "All required sections present"),
    (result['tech_stack'] and json.loads(result['tech_stack']).get('frontend'), "Tech stack has frontend field"),
    (result['tech_stack'] and json.loads(result['tech_stack']).get('backend'), "Tech stack has backend field"),
    (result['tech_stack'] and json.loads(result['tech_stack']).get('database'), "Tech stack has database field"),
]

all_passed = True
for condition, label in assertions:
    status = "✅ PASS" if condition else "❌ FAIL"
    if not condition:
        all_passed = False
    print(f"  {status}: {label}")

print("\n" + ("✅ ALL ASSERTIONS PASSED" if all_passed else "❌ SOME ASSERTIONS FAILED"))

print("\n" + "=" * 60)
print("REQUIREMENTS DOC PREVIEW (first 800 chars)")
print("=" * 60)
print(result['requirements_doc'][:800])
