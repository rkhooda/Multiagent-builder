import os
import sys

# Add backend directory to sys.path so we can import app and main
backend_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.append(backend_dir)

# Clean up old projects.db for a clean test run
db_path = os.path.join(backend_dir, "projects.db")
if os.path.exists(db_path):
    print(f"Removing existing {db_path} for a clean test run...")
    try:
        os.remove(db_path)
    except Exception as e:
        print("Could not delete projects.db:", e)

from fastapi.testclient import TestClient
from main import app

client = TestClient(app)

def test_full_api_flow():

    print("\n=== 1. Creating a new project ===")
    create_payload = {
        "brief": "A collaborative whiteboarding application",
        "project_name": "WhiteboardApp"
    }
    response = client.post("/api/projects", json=create_payload)
    assert response.status_code == 200, f"Failed to create project: {response.text}"
    data = response.json()
    project_id = data["project_id"]
    print("Project Created Successfully!")
    print(f"Project ID: {project_id}")
    print(f"Status: {data['status']}")
    print(f"Current Stage: {data['current_stage']}")
    print(f"Log: {data['log']}")
    
    assert data["status"] == "awaiting_approval"
    assert data["current_stage"] == "research"
    assert data["log"] == ["research ran"]

    print("\n=== 2. Getting project details ===")
    response = client.get(f"/api/projects/{project_id}")
    assert response.status_code == 200, f"Failed to get project: {response.text}"
    data = response.json()
    print("Project Details Loaded!")
    print(f"Project Name: {data['project_name']}")
    print(f"Status: {data['status']}")
    print(f"Current Stage: {data['current_stage']}")
    print(f"Next Gate: {data['next_gate']}")
    print(f"Log: {data['log']}")
    
    assert data["status"] == "awaiting_approval"
    assert data["next_gate"] == "human_gate_1"

    # Now let's resume the project through all the remaining gates
    expected_stages = [
        ("requirements", ["research ran", "requirements ran"], "human_gate_2"),
        ("planning", ["research ran", "requirements ran", "architecture ran", "planning ran"], "human_gate_3"),
        ("devops", ["research ran", "requirements ran", "architecture ran", "planning ran", "frontend_code ran", "backend_code ran", "database ran", "qa ran", "devops ran"], "human_gate_4")
    ]

    for step, (stage, log, next_gate) in enumerate(expected_stages, start=1):
        print(f"\n=== 3.{step}. Resuming at {data['next_gate']} ===")
        resume_payload = {
            "decision": "approve",
            "feedback": f"Approving gate {step}"
        }
        response = client.post(f"/api/projects/{project_id}/resume", json=resume_payload)
        assert response.status_code == 200, f"Failed to resume project: {response.text}"
        data = response.json()
        print(f"Resumed Successfully!")
        print(f"Status: {data['status']}")
        print(f"Current Stage: {data['current_stage']}")
        print(f"Next Gate: {data['next_gate']}")
        print(f"Log: {data['log']}")
        
        assert data["status"] == "awaiting_approval"
        assert data["current_stage"] == stage
        assert data["log"] == log
        assert data["next_gate"] == next_gate

    print("\n=== 4. Final resumption to complete the pipeline ===")
    resume_payload = {
        "decision": "approve",
        "feedback": "Final approval"
    }
    response = client.post(f"/api/projects/{project_id}/resume", json=resume_payload)
    assert response.status_code == 200, f"Failed to resume project: {response.text}"
    data = response.json()
    print("Resumed Successfully to Completion!")
    print(f"Status: {data['status']}")
    print(f"Current Stage: {data['current_stage']}")
    print(f"Next Gate: {data['next_gate']}")
    print(f"Log: {data['log']}")
    
    assert data["status"] == "completed"
    assert data["next_gate"] is None
    
    print("\n=== 5. Verifying GET status for completed project ===")
    response = client.get(f"/api/projects/{project_id}")
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "completed"
    assert data["next_gate"] is None
    print("✓ All checks pass! The entire pipeline flow works purely through API calls!")

if __name__ == "__main__":
    test_full_api_flow()
