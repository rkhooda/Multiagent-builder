"""
Test the database agent in isolation.
Usage: cd backend && python tests/test_database_agent.py
"""
import sys, os, json
# Get the backend directory (parent of tests/)
BACKEND_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
# Project root is parent of backend/
PROJECT_ROOT = os.path.dirname(BACKEND_DIR)
sys.path.insert(0, BACKEND_DIR)
from dotenv import load_dotenv
load_dotenv()

MOCK_PLAN = json.dumps([
    {
        "id": "db_001",
        "phase": "database",
        "filename": "models.py",
        "filepath": "backend/app/models.py",
        "description": "SQLAlchemy models for User, Client, Invoice, TimeEntry tables with UUID primary keys, timestamps, and foreign key relationships as defined in the architecture database schema.",
        "requires": [],
        "context_sections": ["Database Schema"],
        "estimated_complexity": "medium"
    },
    {
        "id": "db_002",
        "phase": "database",
        "filename": "database.py",
        "filepath": "backend/app/database.py",
        "description": "SQLAlchemy engine and session setup, connecting to PostgreSQL using the DATABASE_URL environment variable, with a get_db dependency function for FastAPI route injection.",
        "requires": [],
        "context_sections": ["Database Schema"],
        "estimated_complexity": "low"
    },
    {
        "id": "be_001",
        "phase": "backend",
        "filename": "main.py",
        "filepath": "backend/main.py",
        "description": "FastAPI app entrypoint.",
        "requires": ["db_001"],
        "context_sections": [],
        "estimated_complexity": "low"
    }
])

MOCK_ARCHITECTURE = """
## Database Schema

```sql
CREATE TABLE users (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    email VARCHAR(255) UNIQUE NOT NULL,
    password_hash VARCHAR(255) NOT NULL,
    created_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE clients (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id UUID NOT NULL REFERENCES users(id),
    name VARCHAR(255) NOT NULL,
    hourly_rate DECIMAL(10,2) DEFAULT 0
);
```

## API Endpoints
| Method | Path | Description |
"""

MOCK_TECH_STACK = json.dumps({
    "frontend": "React 19 + Vite",
    "backend": "FastAPI (Python 3.11)",
    "database": "PostgreSQL",
    "auth": "JWT",
    "hosting": "Docker",
    "key_libraries": ["SQLAlchemy", "Alembic"]
})

test_state = {
    "project_id": "test-database-001",
    "project_name": "FreelanceFlow",
    "implementation_plan": MOCK_PLAN,
    "architecture_doc": MOCK_ARCHITECTURE,
    "tech_stack": MOCK_TECH_STACK,
    "generated_files": {},
    "log": [],
    "errors": []
}

print("=" * 60)
print("Testing Database Agent")
print("=" * 60)

from app.agents.database_agent import database_agent

result = database_agent(test_state)

print("\n" + "=" * 60)
print("RESULT SUMMARY")
print("=" * 60)
print(f"Files generated: {len(result['generated_files'])}")
for filepath in result['generated_files']:
    print(f"  - {filepath}")
print(f"Next stage: {result['current_stage']}")
print(f"Errors: {result['errors']}")

print("\n" + "=" * 60)
print("ASSERTIONS")
print("=" * 60)
# Use the precomputed PROJECT_ROOT
outputs_dir = os.path.join(PROJECT_ROOT, "outputs", "test-database-001")
assertions = [
    (len(result['generated_files']) == 2, f"Exactly 2 database files generated (got {len(result['generated_files'])})"),
    ("backend/app/models.py" in result['generated_files'], "models.py was generated"),
    ("backend/app/database.py" in result['generated_files'], "database.py was generated"),
    (result['current_stage'] == "backend_code", "Next stage is backend_code"),
    (len(result['errors']) == 0, "No errors"),
    (os.path.exists(os.path.join(outputs_dir, "backend/app/models.py")), "models.py exists on disk"),
    (os.path.exists(os.path.join(outputs_dir, "backend/app/database.py")), "database.py exists on disk"),
]

all_passed = True
for condition, label in assertions:
    status = "✅ PASS" if condition else "❌ FAIL"
    if not condition:
        all_passed = False
    print(f"  {status}: {label}")

print("\n" + ("✅ ALL PASSED" if all_passed else "❌ FAILURES"))

print("\n" + "=" * 60)
print("GENERATED models.py PREVIEW")
print("=" * 60)
print(result['generated_files'].get('backend/app/models.py', 'NOT GENERATED')[:800])