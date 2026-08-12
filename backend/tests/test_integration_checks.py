"""Integration checks — the layer above parsing. Zero API calls.

Every fixture here is the EXACT defect from project e8935f86 ("CRM system II"),
transcribed from the as-generated ZIP. The point of the suite is stated once:
**every one of these files parses.** Each test asserts that first, so a future
change that "fixes" this by improving the parser is caught immediately — the
parser was never the problem.

See docs/VERIFICATION_GAP_ANALYSIS.md for the full defect table.
"""
import ast
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.validation.integration import (  # noqa: E402
    check_python_manifest, detect_degeneracy, detect_import_time_io,
    detect_truncation, lint_python,
)


def _parses(src: str) -> bool:
    try:
        ast.parse(src)
        return True
    except SyntaxError:
        return False


# ── Class A: undefined name (the linter layer, entirely absent) ──────────────

# backend/app/schemas/tag.py as generated, trimmed to the defect: `datetime` is
# annotated but never imported. TypeAdapter was then not fully defined, so
# /openapi.json returned 500, /docs was an error page, and every typed response
# broke. ruff/pyflakes catches this in milliseconds.
CRM_TAG_SCHEMA = '''\
from typing import Optional
from pydantic import BaseModel, ConfigDict, Field

class TagCreate(BaseModel):
    name: str = Field(..., max_length=255)

class TagRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    name: str
    created_at: datetime
'''


def test_undefined_name_is_caught_though_the_file_parses():
    assert _parses(CRM_TAG_SCHEMA), "precondition: the defect is not a syntax error"
    found = lint_python({"backend/app/schemas/tag.py": CRM_TAG_SCHEMA})
    issues = found.get("backend/app/schemas/tag.py", [])
    blocking = [i for i in issues if i.kind == "lint"]
    assert any("F821" in i.message and "datetime" in i.message for i in blocking), issues
    assert any(i.line == 11 for i in blocking), [(i.line, i.message) for i in blocking]


def test_a_clean_file_produces_no_lint_findings():
    """A false positive here buys a paid repair of correct code."""
    clean = ("from datetime import datetime\n\n\n"
             "def now() -> datetime:\n    return datetime.utcnow()\n")
    assert lint_python({"backend/app/util.py": clean}) == {}


def test_lint_ignores_non_python_files():
    assert lint_python({"frontend/src/App.jsx": "const x = <div/>;"}) == {}


# The hand-repaired, VERIFIED-WORKING contact.py also writes Mapped[List["Tag"]]
# without importing Tag: SQLAlchemy resolves quoted annotations through its
# registry at mapper-configuration time. Reporting those produced 16 findings on
# a file that works and buried the 2 that mattered.
WORKING_FORWARD_REFS = '''\
from typing import List
from sqlalchemy.orm import Mapped, relationship

from app.database import Base


class Contact(Base):
    __tablename__ = "contacts"

    tags: Mapped[List["Tag"]] = relationship(secondary="contact_tags")
    owner: Mapped["User"] = relationship(back_populates="contacts")
'''


def test_quoted_forward_references_are_not_undefined_names():
    """Settled against ground truth, not taste — this is the working copy."""
    assert lint_python({"backend/app/models/contact.py": WORKING_FORWARD_REFS}) == {}


def test_an_unquoted_annotation_is_still_reported():
    """The distinction is exactly quoting: suppressing the quoted case must not
    also suppress `created_at: datetime`, which is the /openapi.json 500."""
    src = "from pydantic import BaseModel\n\n\nclass T(BaseModel):\n    at: datetime\n"
    issues = lint_python({"s.py": src})["s.py"]
    assert any("F821" in i.message and "datetime" in i.message for i in issues), issues


def test_unused_imports_are_informational_not_blocking():
    """66 F401 against 4 real defects on the CRM tree. True observations that
    are almost never why a project does not run; a panel that is 94% noise is a
    panel nobody reads."""
    src = "import os\nimport sys\n\n\ndef f():\n    return 1\n"
    issues = lint_python({"s.py": src})["s.py"]
    assert issues and all(i.kind == "lint_info" for i in issues), issues
    assert all("F401" in i.message for i in issues)


# ── Class B: truncation ──────────────────────────────────────────────────────

# backend/app/models/contact.py, verbatim tail. Ends on a bare `notes`, which
# is a valid EXPRESSION STATEMENT -- ast.parse accepts it -- and a NameError at
# import time.
CRM_CONTACT_MODEL = '''\
from typing import List
from sqlalchemy.orm import Mapped, relationship


class Contact(Base):
    __tablename__ = "contacts"

    tags: Mapped[List["Tag"]] = relationship(secondary="contact_tags")

    notes
'''

# backend/alembic/env.py, verbatim. 13 lines, stops mid-comment. A trailing
# comment is valid Python, so this parses; alembic could not run at all.
CRM_ALEMBIC_ENV = '''\
import os
import sys
from logging.config import fileConfig

from sqlalchemy import engine_from_config
from sqlalchemy import pool

from alembic import context

# Add the 'backend' directory to sys.path so that 'app' can be imported.
# This assumes env.py is located in 'backend/alembic'.
# os.path.dirname(__file__) will be the directory of this file (e.g., /path/to/backend
'''

# frontend/src/components/forms/ReminderForm.jsx, verbatim. 7 lines, cut off
# mid-word inside a comment.
CRM_REMINDER_FORM = '''\
import { useState, useEffect } from 'react';
import api from '../../lib/api';
import Input from '../common/Input';
import Button from '../common/Button';

// Helper to format ISO string to datetime-local input format (YYYY-MM-DDTH
'''


def test_bare_name_tail_is_reported_as_truncation_not_syntax():
    """The remedy differs: a truncated file is REGENERATED, not repaired."""
    assert _parses(CRM_CONTACT_MODEL), "precondition: `notes` is a valid expression"
    found = detect_truncation({"backend/app/models/contact.py": CRM_CONTACT_MODEL},
                              truncated_paths={"backend/app/models/contact.py"})
    issues = found["backend/app/models/contact.py"]
    assert issues[0].kind == "truncated"
    assert "Regenerate" in issues[0].message


def test_file_cut_mid_comment_is_caught_by_the_provider_flag():
    """env.py's tail is a comment, so no content heuristic can be sure. The
    provider already said finish_reason=length -- that is the primary signal,
    and it was recorded and ignored on the real run."""
    assert _parses(CRM_ALEMBIC_ENV)
    found = detect_truncation({"backend/alembic/env.py": CRM_ALEMBIC_ENV},
                              truncated_paths={"backend/alembic/env.py"})
    assert found["backend/alembic/env.py"][0].kind == "truncated"
    assert "ceiling" in found["backend/alembic/env.py"][0].message


def test_short_file_ending_mid_comment_is_caught_without_the_flag():
    """The fallback for when the flag is unavailable -- a cached response, or a
    provider that under-reports."""
    found = detect_truncation({"frontend/src/components/forms/ReminderForm.jsx":
                               CRM_REMINDER_FORM})
    issues = found.get("frontend/src/components/forms/ReminderForm.jsx", [])
    assert issues and issues[0].kind == "truncated", issues


def test_dangling_syntax_tails_are_caught_without_the_flag():
    for tail, name in (("const x = {\n  a: 1,", "trailing comma"),
                       ("export default function App(", "unclosed bracket"),
                       ("const total = a +", "dangling operator")):
        found = detect_truncation({"f.js": "// header\n" + tail})
        assert found.get("f.js"), (name, tail)


def test_a_complete_file_is_not_called_truncated():
    """A finished file whose last line is a normal statement or a finished
    comment must not be flagged -- regenerating a correct file costs tokens."""
    for content in ("def f():\n    return 1\n",
                    "const a = 1;\n// done.\n",
                    "import os\n\n\nclass A:\n    pass\n"):
        assert detect_truncation({"f.py": content}) == {}, content


def test_a_trailing_colon_is_not_truncation():
    """The CRM's own docker-compose.yml ends on `postgres_data:` and is
    complete. A trailing colon is how a YAML key and a Python block header both
    legitimately end, and this check sends files back to be REGENERATED -- so a
    false positive here is the expensive kind."""
    compose = ("services:\n  api:\n    build: .\n\n"
               "volumes:\n  postgres_data:\n")
    assert detect_truncation({"docker-compose.yml": compose}) == {}


# ── Class B: degeneracy (a distinct failure mode from truncation) ────────────

# backend/alembic.ini as generated: 207 non-blank lines, 23 unique -- the same
# comment block nine times, then truncated before the [loggers] sections, so
# `alembic upgrade` died on KeyError.
CRM_ALEMBIC_INI = ("# A generic, single database configuration.\n"
                   "# The path to the migration scripts.\n"
                   "script_location = alembic\n") * 9


def test_repetition_loop_is_reported_as_degeneracy():
    found = detect_degeneracy({"backend/alembic.ini": CRM_ALEMBIC_INI * 2})
    issues = found.get("backend/alembic.ini", [])
    assert issues and issues[0].kind == "degenerate", issues
    assert "looped" in issues[0].message
    assert "regenerate" in issues[0].message.lower()


def test_degeneracy_is_distinct_from_truncation():
    """Same file, two different findings with two different remedies. Raising
    the ceiling buys more repetition, so they must not be conflated."""
    files = {"backend/alembic.ini": CRM_ALEMBIC_INI * 2}
    assert "degenerate" in {i.kind for v in detect_degeneracy(files).values() for i in v}
    assert detect_degeneracy(files) != detect_truncation(files)


def test_legitimately_repetitive_files_are_not_degenerate():
    """A long import block or a list of similar routes repeats by nature."""
    imports = "".join(f"from app.models.m{i} import M{i}\n" for i in range(60))
    assert detect_degeneracy({"backend/app/models/__init__.py": imports}) == {}


def test_short_files_are_never_degenerate():
    assert detect_degeneracy({"a.py": "x = 1\ny = 2\nx = 1\ny = 2\n"}) == {}


# ── Class C9: import-time I/O ────────────────────────────────────────────────

# backend/app/db/base_class.py -- a dead module nothing imported, which opened a
# Postgres engine at import time.
CRM_BASE_CLASS = '''\
import os
from sqlalchemy import create_engine
from sqlalchemy.orm import declarative_base

SQLALCHEMY_DATABASE_URL = os.getenv("DATABASE_URL")
engine = create_engine(SQLALCHEMY_DATABASE_URL)
Base = declarative_base()
'''


def test_module_level_engine_is_flagged():
    assert _parses(CRM_BASE_CLASS)
    found = detect_import_time_io({"backend/app/db/base_class.py": CRM_BASE_CLASS})
    issues = found["backend/app/db/base_class.py"]
    assert issues[0].kind == "import_time_io"
    assert "create_engine" in issues[0].message
    assert issues[0].line == 6


def test_the_same_call_inside_a_function_is_fine():
    """This is how create_engine is MEANT to be used -- flagging it would make
    the check useless noise."""
    ok = ("from sqlalchemy import create_engine\n\n\n"
          "def get_engine(url):\n    return create_engine(url)\n")
    assert detect_import_time_io({"backend/app/db/session.py": ok} ) == {}


# ── Class E: dependency manifest ─────────────────────────────────────────────

# backend/requirements.txt as generated. `csv` is stdlib, so `pip install -r`
# fails on that line and NOTHING else installs -- the most blocking defect in
# the whole set. The generator even flagged its own uncertainty and shipped it.
CRM_REQUIREMENTS = '''\
alembic==1.14.0
fastapi==0.115.6
passlib[bcrypt]==1.7.4
pydantic==2.10.4
python-jose[cryptography]==3.3.0
sqlalchemy==2.0.36
uvicorn[standard]==0.34.0
csv  # unpinned: not in known-good map, verify version
'''


def test_stdlib_module_in_requirements_is_rejected():
    found = check_python_manifest({"backend/requirements.txt": CRM_REQUIREMENTS})
    issues = found["backend/requirements.txt"]
    assert any("csv" in i.message and "standard-library" in i.message for i in issues)
    assert all(i.kind == "manifest" for i in issues)
    assert issues[0].line == 8


def test_a_real_package_is_not_mistaken_for_stdlib():
    """`email`, `typing` and `io` are stdlib names that also prefix real
    distributions -- email-validator and typing-extensions must survive."""
    manifest = "email-validator==2.2.0\ntyping-extensions==4.12.2\nfastapi==0.115.6\n"
    assert check_python_manifest({"backend/requirements.txt": manifest}) == {}


def test_usage_triggered_dependencies_are_caught():
    """Nothing imports these by name, so no import scan finds them. All three
    were missing from the CRM manifest and all three are in the repaired one."""
    files = {
        "backend/requirements.txt": "fastapi==0.115.6\npydantic==2.10.4\npasslib==1.7.4\n",
        "backend/app/schemas/user.py":
            "from pydantic import BaseModel, EmailStr\n\n\n"
            "class U(BaseModel):\n    email: EmailStr\n",
        "backend/app/api/auth.py":
            "from fastapi.security import OAuth2PasswordRequestForm\n",
        "backend/app/core/security.py":
            "from passlib.context import CryptContext\n\nctx = CryptContext()\n",
    }
    messages = [i.message for v in check_python_manifest(files).values() for i in v]
    assert any("email-validator" in m for m in messages), messages
    assert any("python-multipart" in m for m in messages), messages
    assert any("bcrypt" in m for m in messages), messages


def test_declared_usage_dependencies_are_not_reported():
    files = {
        "backend/requirements.txt": "fastapi==0.115.6\npydantic==2.10.4\nemail-validator==2.2.0\n",
        "backend/app/schemas/user.py":
            "from pydantic import BaseModel, EmailStr\n\n\n"
            "class U(BaseModel):\n    email: EmailStr\n",
    }
    assert check_python_manifest(files) == {}


def test_local_packages_are_not_reported_as_missing_dependencies():
    """`app.core` is this project's own code, not a distribution."""
    files = {
        "backend/requirements.txt": "fastapi==0.115.6\n",
        "backend/app/main.py": "from fastapi import FastAPI\nfrom app.core import config\n",
        "backend/app/core/config.py": "x = 1\n",
    }
    assert check_python_manifest(files) == {}


def test_an_undeclared_third_party_import_is_reported():
    files = {
        "backend/requirements.txt": "fastapi==0.115.6\n",
        "backend/app/main.py": "import redis\n",
    }
    messages = [i.message for v in check_python_manifest(files).values() for i in v]
    assert any("redis" in m for m in messages), messages


def test_a_distribution_named_differently_from_its_import_is_accepted():
    """python-jose is imported as `jose`; reporting it would be a false
    positive on a correct manifest."""
    files = {
        "backend/requirements.txt": "python-jose[cryptography]==3.3.0\npython-dotenv==1.0.1\n",
        "backend/app/core/security.py": "from jose import jwt\nimport dotenv\n",
    }
    assert check_python_manifest(files) == {}


def _run_all():
    tests = [(n, f) for n, f in sorted(globals().items())
             if n.startswith("test_") and callable(f)]
    passed = failed = 0
    for name, fn in tests:
        try:
            fn()
            print(f"  ok   {name}")
            passed += 1
        except Exception as exc:  # noqa: BLE001 — a suite reports, never raises
            print(f"  FAIL {name}: {type(exc).__name__}: {exc}")
            failed += 1
    print(f"\n{passed} passed, {failed} failed. (0 API calls)")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(_run_all())
