import os
import sys

# Add backend directory to sys.path so we can import app
backend_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.append(backend_dir)

# Clean up old projects.db first so we test fresh creation - commented out to avoid I/O conflict with running uvicorn
backend_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
db_path = os.path.join(backend_dir, "projects.db")
# if os.path.exists(db_path):
#     print(f"Removing existing {db_path} for a clean test run...")
#     try:
#         os.remove(db_path)
#     except Exception as e:
#         print("Could not delete projects.db, skipping removal:", e)



# Import graph AFTER database is cleaned up so we don't hold a handle to a deleted file
from app.graph.pipeline import graph

def test_pipeline_flow():
    # 1. Thread config for checkpointer (thread_id matches project_id)
    config = {"configurable": {"thread_id": "test-001"}}

    # 2. Hardcoded test input
    initial_state = {
        "project_id": "test-001",
        "brief": "A todo app",
        "project_name": "TestApp",
        "research_report": "",
        "requirements_doc": "",
        "tech_stack": "",
        "architecture_doc": "",
        "implementation_plan": "",
        "file_list": [],
        "generated_files": {},
        "qa_report": "",
        "devops_files": {},
        "current_stage": "",
        "human_feedback": "",
        "human_decision": "",
        "log": [],
        "errors": []
    }

    print("--- Starting pipeline run ---")
    # First invocation runs through research and pauses at human_gate_1
    state_after_gate_1 = graph.invoke(initial_state, config)
    print("State after gate 1:")
    print("Current stage:", state_after_gate_1.get("current_stage"))
    print("Log:", state_after_gate_1.get("log"))
    
    # Confirm:
    # - current_stage should be "research"
    # - log should contain ["research ran"]
    assert state_after_gate_1.get("current_stage") == "research", f"Expected 'research', got {state_after_gate_1.get('current_stage')}"
    assert state_after_gate_1.get("log") == ["research ran"], f"Expected ['research ran'], got {state_after_gate_1.get('log')}"
    print("✓ Milestone 3 passes: Graph paused at human_gate_1, log is ['research ran']")

    # Confirm projects.db exists
    assert os.path.exists(db_path), "projects.db was not created on disk!"
    print("✓ Milestone 3 passes: projects.db was created on disk")

    print("\n--- Resuming past gate 1 ---")
    # To resume past an interrupt, we invoke the graph with None input and the same config
    state_after_gate_2 = graph.invoke(None, config)
    print("State after gate 2:")
    print("Current stage:", state_after_gate_2.get("current_stage"))
    print("Log:", state_after_gate_2.get("log"))

    # Confirm:
    # - current_stage should be "requirements"
    # - log should contain ["research ran", "requirements ran"]
    assert state_after_gate_2.get("current_stage") == "requirements", f"Expected 'requirements', got {state_after_gate_2.get('current_stage')}"
    assert state_after_gate_2.get("log") == ["research ran", "requirements ran"], f"Expected ['research ran', 'requirements ran'], got {state_after_gate_2.get('log')}"
    print("✓ Milestone 4 passes: Graph resumed, ran requirements, paused at human_gate_2")

if __name__ == "__main__":
    test_pipeline_flow()
