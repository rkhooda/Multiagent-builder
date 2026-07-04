"""
Test the planning agent in isolation.
Usage: cd backend && python tests/test_planning_agent.py
"""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dotenv import load_dotenv

load_dotenv()

MOCK_ARCHITECTURE_DOC = """
## Folder Structure
freelanceflow/
├── backend/
│   ├── requirements.txt
│   ├── main.py
│   └── app/
│       ├── models.py
│       ├── database.py
│       ├── auth.py
│       └── routers/
│           ├── users.py
│           ├── clients.py
│           ├── projects.py
│           ├── time_entries.py
│           └── invoices.py
└── frontend/
    ├── package.json
    └── src/
        ├── main.jsx
        ├── App.jsx
        ├── lib/
        │   └── api.js
        └── components/
            ├── Dashboard.jsx
            ├── ClientList.jsx
            ├── InvoiceCard.jsx
            └── TimeTracker.jsx

## Database Schema

```sql
CREATE TABLE users (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    email VARCHAR(255) UNIQUE NOT NULL,
    password_hash VARCHAR(255) NOT NULL,
    name VARCHAR(255) NOT NULL,
    created_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE clients (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    name VARCHAR(255) NOT NULL,
    email VARCHAR(255),
    hourly_rate DECIMAL(10,2) DEFAULT 0,
    created_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE invoices (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id UUID NOT NULL REFERENCES users(id),
    client_id UUID NOT NULL REFERENCES clients(id),
    amount DECIMAL(10,2) NOT NULL,
    status VARCHAR(50) DEFAULT 'draft',
    stripe_payment_link VARCHAR(500),
    created_at TIMESTAMPTZ DEFAULT NOW()
);
```

## API Endpoints

| Method | Path | Auth | Description |
|--------|------|------|-------------|
| POST | /api/auth/register | No | Register new user |
| POST | /api/auth/login | No | Login and get JWT |
| GET | /api/clients | Yes | List all clients |
| POST | /api/clients | Yes | Create client |
| GET | /api/invoices | Yes | List all invoices |
| POST | /api/invoices | Yes | Create invoice |
| GET | /api/invoices/{id} | Yes | Get single invoice |

## Component Hierarchy
- App
  - Dashboard (revenue metrics, outstanding invoices)
  - ClientList (list and manage clients)
  - InvoiceCard (single invoice display)
  - TimeTracker (active timer component)

## Security Approach
JWT tokens stored in httpOnly cookies. bcrypt for password hashing. All routes except /auth/* require valid JWT.
"""

MOCK_FILE_LIST = [
    "backend/requirements.txt",
    "backend/main.py",
    "backend/app/models.py",
    "backend/app/database.py",
    "backend/app/auth.py",
    "backend/app/routers/users.py",
    "backend/app/routers/clients.py",
    "backend/app/routers/invoices.py",
    "frontend/package.json",
    "frontend/src/main.jsx",
    "frontend/src/App.jsx",
    "frontend/src/lib/api.js",
    "frontend/src/components/Dashboard.jsx",
    "frontend/src/components/ClientList.jsx",
    "frontend/src/components/InvoiceCard.jsx",
]

MOCK_TECH_STACK = json.dumps(
    {
        "frontend": "React 19 + Vite + TailwindCSS",
        "backend": "FastAPI (Python 3.11)",
        "database": "PostgreSQL",
        "auth": "JWT + bcrypt",
        "hosting": "Docker + VPS",
        "key_libraries": ["SQLAlchemy", "Alembic", "Stripe", "WeasyPrint"],
    }
)

test_state = {
    "project_id": "test-planning-001",
    "project_name": "FreelanceFlow",
    "brief": "A web app for freelancers to track billable hours and generate Stripe invoices.",
    "architecture_doc": MOCK_ARCHITECTURE_DOC,
    "file_list": MOCK_FILE_LIST,
    "tech_stack": MOCK_TECH_STACK,
    "log": [
        "research_agent: completed - 2847 char report",
        "requirements_agent: completed - 3210 char doc",
        "architecture_agent: completed - 4500 char doc, 15 files",
    ],
    "errors": [],
}

print("=" * 60)
print("Testing Planning Agent")
print("=" * 60)

from app.agents.planning_agent import planning_agent
from app.models.task_schema import ImplementationPlan, TaskSchema
from app.agents.utils import parse_and_validate_plan

result = planning_agent(test_state)

print("\n" + "=" * 60)
print("RESULT SUMMARY")
print("=" * 60)

try:
    stored_tasks = json.loads(result["implementation_plan"])
    plan = ImplementationPlan(tasks=[TaskSchema(**t) for t in stored_tasks])
    summary = plan.summary()
    print(f"Total tasks: {summary['total']}")
    print(f"  Database:  {summary['database']} tasks")
    print(f"  Backend:   {summary['backend']} tasks")
    print(f"  Frontend:  {summary['frontend']} tasks")
    print(f"  DevOps:    {summary['devops']} tasks")
    print(f"  Low:       {summary['low_complexity']}")
    print(f"  Medium:    {summary['medium_complexity']}")
    print(f"  High:      {summary['high_complexity']}")

    deps_valid, dep_errors = plan.validate_dependencies()

    print("\n" + "=" * 60)
    print("EXECUTION ORDER (waves)")
    print("=" * 60)
    waves = plan.get_execution_order()
    for i, wave in enumerate(waves):
        print(f"Wave {i + 1}: {[t.id for t in wave]}")

    print("\n" + "=" * 60)
    print("ASSERTIONS")
    print("=" * 60)
    assertions = [
        (result["current_stage"] == "frontend_code", "Next stage is frontend_code"),
        (len(result["errors"]) == 0, "No errors in state"),
        (summary["total"] >= 8, f"At least 8 tasks (got {summary['total']})"),
        (summary["database"] >= 1, "At least 1 database task"),
        (summary["backend"] >= 1, "At least 1 backend task"),
        (summary["frontend"] >= 1, "At least 1 frontend task"),
        (deps_valid, "All dependencies reference valid task IDs"),
        (
            all(t.id.startswith("db_") for t in plan.get_phase_tasks("database")),
            "Database task IDs start with db_",
        ),
        (
            all(t.id.startswith("be_") for t in plan.get_phase_tasks("backend")),
            "Backend task IDs start with be_",
        ),
        (
            all(t.id.startswith("fe_") for t in plan.get_phase_tasks("frontend")),
            "Frontend task IDs start with fe_",
        ),
        (all(len(t.description) >= 50 for t in plan.tasks), "All task descriptions >= 50 chars"),
        (all("." in t.filepath.split("/")[-1] for t in plan.tasks), "All filepaths have extensions"),
    ]

    all_passed = True
    for condition, label in assertions:
        status = "✅ PASS" if condition else "❌ FAIL"
        if not condition:
            all_passed = False
        print(f"  {status}: {label}")

    if dep_errors:
        print(f"\n  Dependency errors: {dep_errors}")

    print("\n" + ("✅ ALL ASSERTIONS PASSED" if all_passed else "❌ SOME ASSERTIONS FAILED"))

    print("\n" + "=" * 60)
    print("SAMPLE TASKS")
    print("=" * 60)
    for task in plan.tasks[:5]:
        print(f"\n  [{task.id}] {task.filepath}")
        print(f"  Phase: {task.phase} | Complexity: {task.estimated_complexity}")
        print(f"  Requires: {task.requires}")
        print(f"  Description: {task.description[:100]}...")

except Exception as e:
    print(f"❌ Failed to parse result: {e}")
    print(f"Raw plan (first 500 chars): {result['implementation_plan'][:500]}")

print(f"\nLog entries: {len(result['log'])}")
print(f"Errors: {result['errors']}")
