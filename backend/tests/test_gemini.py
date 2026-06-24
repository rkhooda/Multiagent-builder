import sys
import os
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from app.llm_router import call_llm, MODELS

messages = [{"role": "user", "content": "Say hello in exactly 10 words"}]
print("Running test_gemini.py...")
primary, _ = MODELS.get("research")
print(f"Targeting model: {primary}")
try:
    response = call_llm(messages, "research")
    print("SUCCESS!")
    print(f"Response: {response}")
except Exception as e:
    print(f"FAILED: {e}")
    sys.exit(1)
