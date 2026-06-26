import os
import sys
import time
import requests

class LiveClient:
    def __init__(self, base_url="http://127.0.0.1:8000"):
        self.base_url = base_url
    def post(self, path, json=None):
        return requests.post(f"{self.base_url}{path}", json=json)
    def get(self, path):
        return requests.get(f"{self.base_url}{path}")

client = LiveClient()

def poll_project_until(project_id, target_status, max_wait=5.0):
    start_time = time.time()
    while time.time() - start_time < max_wait:
        res = client.get(f"/api/projects/{project_id}")
        if res.status_code == 200:
            data = res.json()
            if data["status"] == target_status:
                return data
        time.sleep(0.1)
    # Fetch one last time to print actual status on failure
    res = client.get(f"/api/projects/{project_id}")
    assert res.status_code == 200, f"Project not found (got {res.status_code}): {res.text}"
    raise AssertionError(f"Project did not reach {target_status}. Current status is: {res.json()['status']}")


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
    print(f"Initial POST response status: {data['status']}")
    assert data["status"] == "running"
    
    # Poll until research agent runs and we pause at human_gate_1
    data = poll_project_until(project_id, "awaiting_approval")
    print(f"After research runs - Status: {data['status']}")
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
        resume_data = response.json()
        assert resume_data["status"] == "resumed"
        
        # Poll until the next gate is reached
        data = poll_project_until(project_id, "awaiting_approval")
        print(f"Resumed Successfully and paused!")
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
    resume_data = response.json()
    assert resume_data["status"] == "resumed"
    
    # Poll until completed
    data = poll_project_until(project_id, "completed")
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

