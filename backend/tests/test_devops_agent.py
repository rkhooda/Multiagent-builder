"""
Test the devops agent in isolation.
Usage: cd backend && python tests/test_devops_agent.py
"""
import sys, os, json
# Get the backend directory (parent of tests/)
BACKEND_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
# Project root is parent of backend/
PROJECT_ROOT = os.path.dirname(BACKEND_DIR)
sys.path.insert(0, BACKEND_DIR)
from dotenv import load_dotenv
load_dotenv()

MOCK_TECH_STACK = json.dumps({
    "frontend": "React 19 + Vite + TailwindCSS",
    "backend": "FastAPI (Python 3.11)",
    "database": "PostgreSQL",
    "auth": "JWT",
    "hosting": "Docker + VPS",
    "key_libraries": ["SQLAlchemy", "Alembic", "Stripe"]
})

test_state = {
    "project_id": "test-devops-001",
    "project_name": "FreelanceFlow",
    "tech_stack": MOCK_TECH_STACK,
    "architecture_doc": "## Environment Variables\n| Variable | Description |\n|---|---|\n| DATABASE_URL | PostgreSQL connection string |\n| JWT_SECRET | Secret for signing JWTs |\n| STRIPE_API_KEY | Stripe secret key |",
    "generated_files": {},
    "devops_files": {},
    "log": [],
    "errors": []
}

print("=" * 60)
print("Testing DevOps Agent")
print("=" * 60)

from app.agents.devops_agent import devops_agent

result = devops_agent(test_state)

print("\n" + "=" * 60)
print("RESULT SUMMARY")
print("=" * 60)
print(f"DevOps files generated: {len(result['devops_files'])}")
for filepath in result['devops_files']:
    print(f"  - {filepath}")
print(f"Next stage: {result['current_stage']}")
print(f"Errors: {result['errors']}")

print("\n" + "=" * 60)
print("ASSERTIONS")
print("=" * 60)
# Use the precomputed PROJECT_ROOT
outputs_dir = os.path.join(PROJECT_ROOT, "outputs", "test-devops-001")
expected_files = [
    "Dockerfile", "frontend/Dockerfile", "docker-compose.yml",
    "frontend/nginx.conf", ".github/workflows/ci.yml", ".env.example", "README.md"
]
assertions = [
    (len(result['devops_files']) == 7, f"All 7 devops files generated (got {len(result['devops_files'])})"),
    (result['current_stage'] == "qa", "Next stage is qa"),
    (len(result['errors']) == 0, "No errors"),
]
for f in expected_files:
    assertions.append((f in result['devops_files'], f"{f} was generated"))
    assertions.append((os.path.exists(os.path.join(outputs_dir, f)), f"{f} exists on disk"))

all_passed = True
for condition, label in assertions:
    status = "✅ PASS" if condition else "❌ FAIL"
    if not condition:
        all_passed = False
    print(f"  {status}: {label}")

print("\n" + ("✅ ALL PASSED" if all_passed else "❌ FAILURES"))

print("\n" + "=" * 60)
print("GENERATED docker-compose.yml PREVIEW")
print("=" * 60)
print(result['devops_files'].get('docker-compose.yml', 'NOT GENERATED')[:800])