"""Unit tests for the AST-based import fixer (Day 19, ponytail #2).

Pure deterministic code — tested directly, no LLM. Run:
    cd backend && python tests/test_import_fixer.py
"""
import os
import sys

BACKEND_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BACKEND_DIR)

from app.utils.import_fixer import fix_imports

TREE = [
    "backend/app/database.py",
    "backend/app/config.py",
    "backend/app/models/user.py",
    "backend/app/models/task.py",
    "backend/app/schemas/task.py",
    "backend/app/routers/tasks.py",
    "backend/app/main.py",
]

passed = 0
failed = 0


def check(name, cond):
    global passed, failed
    if cond:
        passed += 1
        print(f"  PASS  {name}")
    else:
        failed += 1
        print(f"  FAIL  {name}")


# 1. A correct file is left byte-for-byte untouched, no warnings.
correct = (
    "from typing import List\n"
    "from fastapi import APIRouter, Depends\n"
    "from sqlalchemy.orm import Session\n"
    "from app.database import get_db\n"
    "from app.models.task import Task\n"
)
out, warns = fix_imports(correct, "backend/app/routers/tasks.py", TREE)
check("correct file unchanged", out == correct)
check("correct file no warnings", warns == [])

# 2. Fixable relative-depth error: `.database` from two packages deep -> app.database
rel = "from .database import Base\n\nclass User(Base):\n    pass\n"
out, warns = fix_imports(rel, "backend/app/models/user.py", TREE)
check("relative-depth rewritten", "from app.database import Base" in out)
check("relative-depth no warning", warns == [])
check("relative-depth body preserved", "class User(Base):" in out)

# 2b. Wrong relative depth spelled with too many dots resolves by naive path.
rel2 = "from ..models.task import Task\n"
out, warns = fix_imports(rel2, "backend/app/routers/tasks.py", TREE)
check("relative ..models.task -> app.models.task", "from app.models.task import Task" in out and warns == [])

# 3. Fixable package-prefix mismatch: backend.app.database -> app.database
prefix = "from backend.app.database import Base\n"
out, warns = fix_imports(prefix, "backend/app/models/user.py", TREE)
check("backend. prefix stripped", out == "from app.database import Base\n")
check("prefix fix no warning", warns == [])

# 4. Unfixable phantom module: flagged, NOT rewritten.
phantom = "from app.services.payment_gateway import charge\n"
out, warns = fix_imports(phantom, "backend/app/routers/tasks.py", TREE)
check("phantom import not rewritten", out == phantom)
check("phantom import flagged", len(warns) == 1 and "payment_gateway" in warns[0])

# 5. Syntax-broken file passes through untouched, with one warning.
broken = "def oops(:\n    pass\n"
out, warns = fix_imports(broken, "backend/app/routers/tasks.py", TREE)
check("broken file unchanged", out == broken)
check("broken file warned", len(warns) == 1 and "does not parse" in warns[0])

# 6. Pure third-party imports are never candidates.
thirdparty = "from fastapi import FastAPI\nfrom sqlalchemy import create_engine\nimport os\n"
out, warns = fix_imports(thirdparty, "backend/app/main.py", TREE)
check("third-party untouched", out == thirdparty and warns == [])

# 7. Non-Python file returned untouched.
out, warns = fix_imports("import Foo from './bar'", "frontend/src/App.jsx", TREE)
check("non-python untouched", out == "import Foo from './bar'" and warns == [])

print(f"\n{passed} passed, {failed} failed")
sys.exit(1 if failed else 0)
