"""
Test the architecture agent in isolation.
Usage: cd backend && python3 tests/test_architecture_agent.py
"""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dotenv import load_dotenv

load_dotenv()


MOCK_REQUIREMENTS_DOC = """
## Functional Requirements
1. The system shall allow users to register and log in with email and password
2. The system shall allow users to create clients with name, email, and hourly rate
3. The system shall allow users to create projects linked to clients
4. The system shall provide a timer to track billable hours per project
5. The system shall allow manual time entry with start time, end time, and description
6. The system shall generate PDF invoices from tracked time entries
7. The system shall send invoices to clients via email
8. The system shall create Stripe payment links on invoices
9. The system shall mark invoices as paid when Stripe webhook confirms payment
10. The system shall display a dashboard with revenue metrics
11. The system shall support multiple currency display
12. The system shall allow users to set custom invoice templates

## Non-Functional Requirements
- Page load under 2 seconds
- JWT authentication with 24h expiry and refresh tokens
- HTTPS only in production
- WCAG 2.1 AA accessibility

## User Stories
### Time Tracking
- As a freelancer, I want to start a timer for a project so that I can accurately track billable hours
- As a freelancer, I want to add time manually so that I can log hours I forgot to track

### Invoicing
- As a freelancer, I want to generate a PDF invoice from my time entries so that I can bill clients professionally
- As a freelancer, I want to send invoices by email directly from the app so that I don't need a separate email client

## Out of Scope
- Mobile app
- Team collaboration features
- Accounting/bookkeeping integration
- Recurring invoices

## Tech Stack Recommendation
React 19 frontend, FastAPI backend, PostgreSQL database, JWT auth, Stripe for payments.

```json
{"frontend": "React 19 + Vite + TailwindCSS", "backend": "FastAPI (Python 3.11)", "database": "PostgreSQL", "auth": "JWT + bcrypt", "hosting": "Docker + VPS", "key_libraries": ["SQLAlchemy", "Alembic", "Stripe", "SendGrid", "WeasyPrint"]}
```
"""

MOCK_TECH_STACK = json.dumps(
    {
        "frontend": "React 19 + Vite + TailwindCSS",
        "backend": "FastAPI (Python 3.11)",
        "database": "PostgreSQL",
        "auth": "JWT + bcrypt",
        "hosting": "Docker + VPS",
        "key_libraries": ["SQLAlchemy", "Alembic", "Stripe", "SendGrid", "WeasyPrint"],
    }
)

test_state = {
    "project_id": "test-architecture-001",
    "project_name": "FreelanceFlow",
    "brief": (
        "A web app for freelancers to track billable hours by project and client, "
        "generate professional PDF invoices, send them via email, and accept Stripe payments. "
        "Dashboard showing outstanding/paid invoices and monthly revenue."
    ),
    "requirements_doc": MOCK_REQUIREMENTS_DOC,
    "tech_stack": MOCK_TECH_STACK,
    "log": [
        "research_agent: completed - 2847 char report",
        "requirements_agent: completed - 3210 char doc",
    ],
    "errors": [],
}

print("=" * 60)
print("Testing Architecture Agent")
print("=" * 60)

from app.agents.architecture_agent import architecture_agent, validate_architecture_doc
from app.agents.utils import extract_mermaid_diagrams

result = architecture_agent(test_state)

print("\n" + "=" * 60)
print("RESULT SUMMARY")
print("=" * 60)
print(f"Architecture doc length: {len(result['architecture_doc'])} characters")
print(f"File list count: {len(result['file_list'])} files")
print(f"Next stage: {result['current_stage']}")
print(f"Errors: {result['errors']}")

print("\n" + "=" * 60)
print("SECTION VALIDATION")
print("=" * 60)
is_valid, missing = validate_architecture_doc(result["architecture_doc"])
if is_valid:
    print("PASS: All required sections present")
else:
    print(f"FAIL: Missing sections: {missing}")

print("\n" + "=" * 60)
print("MERMAID DIAGRAMS")
print("=" * 60)
diagrams = extract_mermaid_diagrams(result["architecture_doc"])
print(f"Found {len(diagrams)} Mermaid diagram(s):")
for diagram in diagrams:
    print(f"  - {diagram['type']}: {len(diagram['code'])} chars")

print("\n" + "=" * 60)
print("FILE LIST (first 20 files)")
print("=" * 60)
for file_path in result["file_list"][:20]:
    print(f"  {file_path}")
if len(result["file_list"]) > 20:
    print(f"  ... and {len(result['file_list']) - 20} more")

print("\n" + "=" * 60)
print("ASSERTIONS")
print("=" * 60)
assertions = [
    (len(result["architecture_doc"]) >= 1500, "Architecture doc >= 1500 chars"),
    (result["current_stage"] == "planning", "Next stage is planning"),
    (len(result["file_list"]) >= 5, "At least 5 files parsed from folder structure"),
    (len(result["errors"]) == 0, "No errors"),
    (is_valid, "All required sections present"),
    (len(diagrams) >= 1, "At least 1 Mermaid diagram found"),
    (
        any("backend" in file_path.lower() or "app" in file_path.lower() for file_path in result["file_list"]),
        "Backend files in file list",
    ),
    (
        any("frontend" in file_path.lower() or "src" in file_path.lower() for file_path in result["file_list"]),
        "Frontend files in file list",
    ),
    ("architecture_doc" in result, "architecture_doc key present in result"),
    ("file_list" in result, "file_list key present in result"),
]

all_passed = True
for condition, label in assertions:
    status = "PASS" if condition else "FAIL"
    if not condition:
        all_passed = False
    print(f"  {status}: {label}")

print("\n" + ("ALL ASSERTIONS PASSED" if all_passed else "SOME ASSERTIONS FAILED - fix before wiring into pipeline"))

print("\n" + "=" * 60)
print("ARCHITECTURE DOC PREVIEW (first 1200 chars)")
print("=" * 60)
print(result["architecture_doc"][:1200])
