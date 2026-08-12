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
    check_config_keys, check_config_references, check_frontend_entrypoint,
    check_orm_symmetry,
    check_package_markers, check_password_hashing,
    check_python_manifest, check_route_registration,
    detect_degeneracy,
    detect_import_time_io,
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


def test_real_world_file_endings_are_not_truncation():
    """Every one of these was an actual false positive produced by an earlier
    version of the tails table, found by running it against the WORKING copy
    and against ordinary web files. A truncation finding sends the file back to
    be REGENERATED, so a false positive is the expensive direction."""
    probes = {
        # `>` -- every html and svg file ends this way. crm-logo.svg is
        # byte-identical in the generated and repaired trees and was flagged.
        "frontend/src/assets/logo.svg": '<svg viewBox="0 0 24 24">\n  <path d="M0 0"/>\n</svg>',
        "frontend/index.html": '<!doctype html>\n<html>\n<body></body>\n</html>',
        # `*` and `/` -- JSDoc continuation and block-comment close.
        "frontend/src/f.js": '/**\n * Does a thing.\n *\n */\nexport const f = () => 1;\n',
        # a bare comment marker is a spacer, not a sentence cut in half
        "frontend/src/g.js": 'const a = 1;\n//\n',
        "backend/app/h.py": 'x = 1\n# TODO\n',
        "backend/app/i.py": 'import os\n# This module is finished.\n',
        # `:` -- a YAML mapping key
        "docker-compose.yml": 'services:\n  api:\n    build: .\nvolumes:\n  data:\n',
    }
    assert detect_truncation(probes) == {}


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


# ── Class C: route registration ──────────────────────────────────────────────

# The CRM's shape, reduced. main.py includes each endpoint router DIRECTLY and
# also includes api.py's router, which includes the same ones again -- and
# api.py repeats each router's own prefix, so /auth/register really lived at
# /api/v1/auth/auth/register.
CRM_ENDPOINT_AUTH = '''\
from fastapi import APIRouter

router = APIRouter(prefix="/auth", tags=["auth"])


@router.post("/register")
def register():
    return {}
'''

CRM_API_AGGREGATOR = '''\
from fastapi import APIRouter

from app.api.v1.endpoints import auth

router = APIRouter(prefix="/api/v1")

router.include_router(auth.router, prefix="/auth", tags=["auth"])
'''

CRM_MAIN = '''\
from fastapi import FastAPI

from app.api.v1.endpoints.auth import router as auth_router
from app.api.v1.api import router as api_router

app = FastAPI()

app.include_router(auth_router)
app.include_router(api_router)
'''

CRM_ROUTE_TREE = {
    "backend/app/api/v1/endpoints/auth.py": CRM_ENDPOINT_AUTH,
    "backend/app/api/v1/api.py": CRM_API_AGGREGATOR,
    "backend/app/main.py": CRM_MAIN,
}


def test_doubled_prefix_is_caught():
    messages = [i.message for v in check_route_registration(CRM_ROUTE_TREE).values()
                for i in v]
    assert any("already declares prefix='/auth'" in m for m in messages), messages


def test_router_registered_twice_is_caught():
    messages = [i.message for v in check_route_registration(CRM_ROUTE_TREE).values()
                for i in v]
    assert any("registered 2 times" in m for m in messages), messages


def test_the_duplicate_is_reported_at_both_registration_sites():
    """Both files need editing, so both must be marked -- the threshold counts
    unresolved FILES, and fixing only one leaves the routes still doubled."""
    found = check_route_registration(CRM_ROUTE_TREE)
    assert "backend/app/main.py" in found
    assert "backend/app/api/v1/api.py" in found


# contacts.py declared GET "/{contact_id}" at line 26 and GET "/search" at line
# 74. FastAPI matches in declaration order, so /contacts/search parsed "search"
# as a contact_id and returned 422 forever.
CRM_CONTACTS_ROUTES = '''\
from fastapi import APIRouter

router = APIRouter(prefix="/contacts", tags=["contacts"])


@router.get("/")
def list_contacts():
    return []


@router.get("/{contact_id}")
def get_contact(contact_id: int):
    return {}


@router.get("/search")
def search_contacts(q: str):
    return []
'''


def test_static_route_shadowed_by_a_dynamic_one_is_caught():
    found = check_route_registration(
        {"backend/app/api/v1/endpoints/contacts.py": CRM_CONTACTS_ROUTES})
    issues = found["backend/app/api/v1/endpoints/contacts.py"]
    assert any("'/search' is declared after '/{contact_id}'" in i.message
               for i in issues), [i.message for i in issues]
    assert all(i.kind == "route" for i in issues)


def test_correct_route_order_is_not_flagged():
    """The same two routes the right way round must be silent."""
    ok = CRM_CONTACTS_ROUTES.replace(
        '@router.get("/{contact_id}")\ndef get_contact(contact_id: int):\n    return {}\n\n\n', ""
    ).replace('@router.get("/search")\ndef search_contacts(q: str):\n    return []\n',
              '@router.get("/search")\ndef search_contacts(q: str):\n    return []\n\n\n'
              '@router.get("/{contact_id}")\ndef get_contact(contact_id: int):\n    return {}\n')
    assert check_route_registration({"backend/app/api/v1/endpoints/contacts.py": ok}) == {}


def test_a_different_method_does_not_shadow():
    """POST /search is not shadowed by GET /{id} -- method must match."""
    src = ('from fastapi import APIRouter\n\nrouter = APIRouter()\n\n\n'
           '@router.get("/{contact_id}")\ndef g(contact_id: int):\n    return {}\n\n\n'
           '@router.post("/search")\ndef s():\n    return []\n')
    assert check_route_registration({"backend/app/api/v1/endpoints/c.py": src}) == {}


def test_a_single_correct_registration_is_silent():
    tree = {
        "backend/app/api/v1/endpoints/auth.py": CRM_ENDPOINT_AUTH,
        "backend/app/main.py": ("from fastapi import FastAPI\n"
                                "from app.api.v1.endpoints.auth import router as auth_router\n\n"
                                "app = FastAPI()\n\napp.include_router(auth_router)\n"),
    }
    assert check_route_registration(tree) == {}


# ── Class C: packaging structure ─────────────────────────────────────────────


def test_missing_package_marker_is_caught():
    """The CRM shipped no __init__.py anywhere. Python 3 imports such a
    directory as a NAMESPACE package, so nothing raises -- `import app.models`
    succeeds and registers nothing, and create_all() created no tables. The
    hand-repaired copy adds nine markers; this reports exactly those nine."""
    files = {
        "backend/requirements.txt": "fastapi==0.115.6\nsqlalchemy==2.0.36\n",
        "backend/app/main.py": "from app.models.contact import Contact\n",
        "backend/app/models/contact.py": "class Contact:\n    pass\n",
    }
    found = check_package_markers(files)
    messages = [i.message for v in found.values() for i in v]
    assert any("backend/app/models/__init__.py" in m for m in messages), messages
    assert any("backend/app/__init__.py" in m for m in messages), messages
    assert all(i.kind == "packaging" for v in found.values() for i in v)


def test_present_markers_are_not_reported():
    files = {
        "backend/requirements.txt": "fastapi==0.115.6\n",
        "backend/app/__init__.py": "",
        "backend/app/models/__init__.py": "from app.models.contact import Contact\n",
        "backend/app/main.py": "from app.models.contact import Contact\n",
        "backend/app/models/contact.py": "class Contact:\n    pass\n",
    }
    assert check_package_markers(files) == {}


def test_a_directory_shadowed_by_a_dependency_is_not_a_package():
    """backend/alembic/ holds migrations and is deliberately NOT a package --
    `from alembic import context` resolves to the installed distribution. Found
    by running this against the WORKING copy, which has all nine real markers
    and still triggered on alembic."""
    files = {
        "backend/requirements.txt": "alembic==1.14.0\n",
        "backend/alembic/env.py": "from alembic import context\n",
        "backend/alembic/versions/0001_init.py": "revision = '0001'\n",
    }
    assert check_package_markers(files) == {}


# ── Class C: config keys and ORM symmetry ────────────────────────────────────

CRM_SETTINGS = '''\
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env")

    database_url: str = "sqlite:///./app.db"
    secret_key: str = "change-me"


settings = Settings()
'''


def test_config_key_case_mismatch_is_caught():
    """Settings defines `database_url`; db/session.py read
    `settings.DATABASE_URL`. pydantic-settings maps the ENV VAR name to a
    lowercase field, so the env var really is DATABASE_URL -- which is why this
    is easy to get wrong and invisible in either file alone."""
    files = {
        "backend/app/core/config.py": CRM_SETTINGS,
        "backend/app/db/session.py":
            "from app.core.config import settings\n\n"
            "engine = create_engine(settings.DATABASE_URL)\n",
    }
    issues = check_config_keys(files)["backend/app/db/session.py"]
    assert issues[0].kind == "config_key"
    assert "settings.DATABASE_URL" in issues[0].message
    assert "did you mean settings.database_url" in issues[0].message


def test_a_field_that_exists_is_not_reported():
    files = {
        "backend/app/core/config.py": CRM_SETTINGS,
        "backend/app/db/session.py":
            "from app.core.config import settings\n\n"
            "engine = create_engine(settings.database_url)\n",
    }
    assert check_config_keys(files) == {}


def test_config_check_is_silent_without_a_settings_class():
    """A project with no Settings must not produce a finding per read."""
    assert check_config_keys({"backend/app/x.py": "print(settings.anything)\n"}) == {}


# Contact.tags declared back_populates="contacts"; Tag had no `contacts`
# attribute. SQLAlchemy raises at MAPPER CONFIGURATION -- the first time any
# mapper is used -- so /health answered 200 and every request touching the
# database returned 500. The clearest example of "boot is not proof".
CRM_ORM_PAIR = {
    "backend/app/models/contact.py": (
        "from typing import List\n"
        "from sqlalchemy.orm import Mapped, relationship\n\n\n"
        "class Contact(Base):\n"
        "    __tablename__ = 'contacts'\n"
        "    tags: Mapped[List['Tag']] = relationship(back_populates='contacts')\n"),
    "backend/app/models/tag.py": (
        "from sqlalchemy.orm import Mapped, mapped_column\n\n\n"
        "class Tag(Base):\n"
        "    __tablename__ = 'tags'\n"
        "    name: Mapped[str] = mapped_column()\n"),
}


def test_back_populates_naming_a_missing_attribute_is_caught():
    issues = check_orm_symmetry(CRM_ORM_PAIR)["backend/app/models/contact.py"]
    assert issues[0].kind == "orm"
    assert "Tag has no 'contacts' attribute" in issues[0].message


def test_a_symmetric_relationship_is_not_reported():
    ok = dict(CRM_ORM_PAIR)
    ok["backend/app/models/tag.py"] = ok["backend/app/models/tag.py"] + (
        "    contacts: Mapped[list['Contact']] = relationship(back_populates='tags')\n")
    assert check_orm_symmetry(ok) == {}


def test_a_relationship_to_a_class_outside_the_tree_is_not_reported():
    """Nothing to check against — silence beats a guess."""
    files = {"backend/app/models/contact.py": (
        "from sqlalchemy.orm import Mapped, relationship\n\n\n"
        "class Contact(Base):\n"
        "    org: Mapped['Organisation'] = relationship(back_populates='contacts')\n")}
    assert check_orm_symmetry(files) == {}


# ── Class F: config files naming things that do not exist ────────────────────


def test_compose_building_a_missing_dockerfile_is_caught():
    """docker-compose built ./backend/Dockerfile; the file was at the repo root.
    Valid YAML, so validate_artifact reported it clean."""
    files = {
        "docker-compose.yml": ("services:\n  backend:\n    build:\n"
                               "      context: ./backend\n      dockerfile: Dockerfile\n"),
        "Dockerfile": "FROM python:3.11-slim\n",
    }
    issues = check_config_references(files)["docker-compose.yml"]
    assert issues[0].kind == "config_ref"
    assert "backend/Dockerfile" in issues[0].message
    assert "it is at 'Dockerfile'" in issues[0].message


def test_compose_pointing_at_a_real_dockerfile_is_silent():
    files = {
        "docker-compose.yml": ("services:\n  backend:\n    build:\n"
                               "      context: ./backend\n      dockerfile: Dockerfile\n"),
        "backend/Dockerfile": "FROM python:3.11-slim\n",
    }
    assert check_config_references(files) == {}


def test_ci_running_a_missing_npm_script_is_caught():
    """CI ran `npm run lint`; package.json had dev/build/preview only, so the
    workflow failed at step one."""
    files = {
        ".github/workflows/ci.yml": "jobs:\n  b:\n    steps:\n      - run: npm run lint\n",
        "frontend/package.json": '{"scripts": {"dev": "vite", "build": "vite build"}}',
    }
    issues = check_config_references(files)[".github/workflows/ci.yml"]
    assert "npm run lint" in issues[0].message
    assert "build, dev" in issues[0].message


def test_ci_running_pytest_without_tests_is_caught():
    files = {".github/workflows/ci.yml": "jobs:\n  b:\n    steps:\n      - run: pytest\n",
             "backend/app/main.py": "app = 1\n"}
    issues = check_config_references(files)[".github/workflows/ci.yml"]
    assert "no test files" in issues[0].message


def test_ci_running_pytest_with_tests_is_silent():
    files = {".github/workflows/ci.yml": "jobs:\n  b:\n    steps:\n      - run: pytest\n",
             "backend/tests/test_api.py": "def test_x():\n    pass\n"}
    assert check_config_references(files) == {}


def test_an_entrypoint_module_that_resolves_nowhere_is_caught():
    """Deliberately lenient: the image's WORKDIR is not knowable from the
    Dockerfile alone, so this only fires when the module resolves at NO level.
    The CRM's `uvicorn main:app` (app is at app.main:app) is therefore NOT
    caught here -- the compose finding already sends you to the same file, and
    the boot rung catches it exactly, by the container starting and exiting."""
    files = {"Dockerfile": 'FROM python:3.11\nCMD ["uvicorn", "server.wsgi:app"]\n',
             "backend/app/main.py": "app = 1\n"}
    issues = check_config_references(files)["Dockerfile"]
    assert "server/wsgi.py" in issues[0].message


# ── Class H: security ────────────────────────────────────────────────────────

# crud_user.py:18. get_password_hash() existed and was never called, so
# passwords were stored in plaintext AND login could never succeed. QA DID find
# this and the project shipped anyway.
CRM_CRUD_USER = '''\
from app.core.security import get_password_hash
from app.models.user import User


def create_user(db, user):
    db_user = User(
        email=user.email,
        hashed_password=user.password,
    )
    db.add(db_user)
    return db_user
'''


def test_plaintext_password_assignment_is_caught():
    issues = check_password_hashing({"backend/app/crud/crud_user.py": CRM_CRUD_USER})
    found = issues["backend/app/crud/crud_user.py"]
    assert found[0].kind == "security"
    assert "plaintext" in found[0].message


def test_a_hashed_assignment_is_not_reported():
    ok = CRM_CRUD_USER.replace("hashed_password=user.password,",
                               "hashed_password=get_password_hash(user.password),")
    assert check_password_hashing({"backend/app/crud/crud_user.py": ok}) == {}


def test_pwd_context_hashing_is_recognised():
    src = ("from app.core.security import pwd_context\n\n\n"
           "def f(u):\n    return User(hashed_password=pwd_context.hash(u.password))\n")
    assert check_password_hashing({"backend/app/crud/crud_user.py": src}) == {}


# ── Class D: the blank page ──────────────────────────────────────────────────

# All three causes lived in main.jsx. `npm run build` succeeded, the ZIP
# shipped, and the app rendered nothing.
CRM_MAIN_JSX = """import React from 'react';
import ReactDOM from 'react-dom';
import App from './App';

ReactDOM.render(
  <React.StrictMode>
    <App />
  </React.StrictMode>,
  document.getElementById('root')
);
"""

CRM_FRONTEND = {
    "frontend/package.json": '{"dependencies": {"react": "^19.2.8", "react-dom": "^19.2.8"}}',
    "frontend/src/main.jsx": CRM_MAIN_JSX,
    "frontend/src/index.css": "@tailwind base;\n@tailwind components;\n",
    "frontend/src/App.jsx": "export default function App() { return <div>hi</div>; }\n",
    "frontend/src/hooks/useAuth.js":
        "export const AuthProvider = ({ children }) => children;\n"
        "export const useAuth = () => useContext(AuthContext);\n",
}


def test_legacy_reactdom_render_under_react_19_is_caught():
    issues = check_frontend_entrypoint(CRM_FRONTEND)["frontend/src/main.jsx"]
    assert any("REMOVED in React 18" in i.message and "renders blank" in i.message
               for i in issues), [i.message for i in issues]
    assert all(i.kind == "entrypoint" for i in issues)


def test_reactdom_render_under_react_17_is_not_reported():
    """The call is correct on React 17 -- the defect is the PAIRING with a
    pinned major that removed it."""
    files = dict(CRM_FRONTEND)
    files["frontend/package.json"] = '{"dependencies": {"react": "^17.0.2"}}'
    issues = check_frontend_entrypoint(files).get("frontend/src/main.jsx", [])
    assert not any("REMOVED in React 18" in i.message for i in issues), issues


def test_an_unimported_stylesheet_is_caught():
    issues = check_frontend_entrypoint(CRM_FRONTEND)["frontend/src/main.jsx"]
    assert any("index.css" in i.message and "never imported" in i.message
               for i in issues), [i.message for i in issues]


def test_an_imported_stylesheet_is_not_reported():
    files = dict(CRM_FRONTEND)
    files["frontend/src/main.jsx"] = "import './index.css';\n" + CRM_MAIN_JSX
    issues = check_frontend_entrypoint(files).get("frontend/src/main.jsx", [])
    assert not any("never imported" in i.message for i in issues), issues


def test_a_provider_that_is_never_rendered_is_caught():
    issues = check_frontend_entrypoint(CRM_FRONTEND)["frontend/src/hooks/useAuth.js"]
    assert any("AuthProvider is defined but never rendered" in i.message
               for i in issues), [i.message for i in issues]


def test_a_mounted_provider_is_not_reported():
    files = dict(CRM_FRONTEND)
    files["frontend/src/App.jsx"] = (
        "import { AuthProvider } from './hooks/useAuth';\n"
        "export default function App() { return <AuthProvider><div/></AuthProvider>; }\n")
    issues = check_frontend_entrypoint(files).get("frontend/src/hooks/useAuth.js", [])
    assert not any("never rendered" in i.message for i in issues), issues


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
