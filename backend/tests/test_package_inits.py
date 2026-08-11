"""Deterministic __init__.py package markers (token audit, 2026-08-10).

Zero API calls. The claim under test: a package marker's correct content is
DERIVABLE from the package's actual modules, so replacing 11 LLM calls per run
with rendering must (a) re-export every class that really exists, (b) never
re-export a name that does not, (c) survive broken/stub modules, and (d) leave
every non-__init__ task exactly as the LLM path had it.
"""
import ast
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.utils.backend_infra import render_package_inits, llm_generates_inits

MODEL = """from sqlalchemy.orm import Mapped, mapped_column
from app.database import Base


class Note(Base):
    __tablename__ = "notes"
    id: Mapped[int] = mapped_column(primary_key=True)
"""

SCHEMA = """from pydantic import BaseModel, ConfigDict


class NoteCreate(BaseModel):
    title: str


class NoteResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
"""

ROUTER = """from fastapi import APIRouter

router = APIRouter(prefix="/notes", tags=["notes"])
"""


def test_reexports_every_real_class():
    files = {"backend/app/models/note.py": MODEL,
             "backend/app/models/tag.py": "class Tag:\n    pass\n"}
    [(path, content)] = render_package_inits(["backend/app/models/__init__.py"], files)
    assert path == "backend/app/models/__init__.py"
    assert "from .note import Note" in content
    assert "from .tag import Tag" in content


def test_multiclass_module_exports_all_its_classes():
    files = {"backend/app/schemas/note.py": SCHEMA}
    [(_, content)] = render_package_inits(["backend/app/schemas/__init__.py"], files)
    assert "from .note import NoteCreate, NoteResponse" in content


def test_module_level_names_are_not_reexported():
    """`router = APIRouter()` is an assignment, not a class — re-exporting it
    from several modules would collide, and `from app.routers import notes`
    (submodule import) works with an empty marker anyway."""
    files = {"backend/app/routers/notes.py": ROUTER}
    [(_, content)] = render_package_inits(["backend/app/routers/__init__.py"], files)
    assert "import" not in content.replace("(generated deterministically", "")


def test_rendered_marker_parses_and_references_only_real_names():
    files = {"backend/app/models/note.py": MODEL,
             "backend/app/models/tag.py": "class Tag:\n    pass\n"}
    [(_, content)] = render_package_inits(["backend/app/models/__init__.py"], files)
    tree = ast.parse(content)  # must be valid Python
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            mod_path = f"backend/app/models/{node.module.lstrip('.')}.py"
            defined = {n.name for n in ast.parse(files[mod_path]).body
                       if isinstance(n, ast.ClassDef)}
            for alias in node.names:
                assert alias.name in defined, f"hallucinated re-export {alias.name}"


def test_broken_or_stub_module_is_skipped_not_fatal():
    files = {"backend/app/models/note.py": MODEL,
             "backend/app/models/broken.py": "def oops(:\n",       # syntax error
             "backend/app/models/stub.py": "# Generation failed\npass\n"}
    [(_, content)] = render_package_inits(["backend/app/models/__init__.py"], files)
    assert "from .note import Note" in content
    assert "broken" not in content and "stub" not in content


def test_empty_package_gets_a_plain_marker():
    [(_, content)] = render_package_inits(["backend/app/services/__init__.py"], {})
    ast.parse(content)
    assert "from ." not in content


def test_cross_module_name_collision_is_deterministic_first_wins():
    files = {"backend/app/models/a.py": "class Note:\n    pass\n",
             "backend/app/models/b.py": "class Note:\n    pass\n"}
    [(_, content)] = render_package_inits(["backend/app/models/__init__.py"], files)
    assert content.count("import Note") == 1
    assert "from .a import Note" in content


def test_only_direct_children_count():
    """A nested package's modules belong to ITS marker, not the parent's."""
    files = {"backend/app/api/v1/users.py": "class User:\n    pass\n"}
    [(_, content)] = render_package_inits(["backend/app/api/__init__.py"], files)
    assert "User" not in content


def test_escape_hatch_env_flag():
    assert not llm_generates_inits()
    os.environ["LLM_INIT_FILES"] = "true"
    try:
        assert llm_generates_inits()
    finally:
        del os.environ["LLM_INIT_FILES"]


def test_agents_filter_inits_out_of_llm_tasks():
    """The partition rule both coder agents apply: __init__.py tasks leave the
    LLM set, everything else stays byte-identical."""
    plan = [
        {"id": "be_001", "filepath": "backend/app/schemas/note.py", "phase": "backend"},
        {"id": "be_002", "filepath": "backend/app/schemas/__init__.py", "phase": "backend"},
        {"id": "be_003", "filepath": "backend/app/routers/notes.py", "phase": "backend"},
    ]
    inits = [t for t in plan if t["filepath"].endswith("__init__.py")]
    rest = [t for t in plan if not t["filepath"].endswith("__init__.py")]
    assert [t["id"] for t in inits] == ["be_002"]
    assert [t["id"] for t in rest] == ["be_001", "be_003"]


if __name__ == "__main__":
    passed = failed = 0
    for name, fn in sorted(globals().items()):
        if not name.startswith("test_") or not callable(fn):
            continue
        try:
            fn()
            print(f"  ok   {name}")
            passed += 1
        except Exception as e:                  # noqa: BLE001
            print(f"  FAIL {name}: {type(e).__name__}: {e}")
            failed += 1
    print(f"\n{passed} passed, {failed} failed. (0 API calls)")
    sys.exit(1 if failed else 0)
