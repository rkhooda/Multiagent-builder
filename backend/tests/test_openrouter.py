import sys
import os
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from app.llm_router import call_llm, MODELS

messages = [{"role": "user", "content": "Write a Python function that adds two numbers"}]
print("Running test_openrouter.py...")
primary, _ = MODELS.get("architecture")
print(f"Targeting model: {primary}")
try:
    response = call_llm(messages, "architecture")
    print("SUCCESS!")
    print(f"Response: {response}")
except Exception as e:
    print(f"FAILED: {e}")
    sys.exit(1)
