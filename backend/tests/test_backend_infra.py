"""Deterministic backend infra: the rendered app must actually SERVE.

Zero API calls. The end-to-end case runs the rendered project in a SUBPROCESS
rather than importing it here, because the rendered project's package is also
called `app` — importing it in this process would collide with the backend's own
`app` package and test the wrong code.

The defect this suite exists for: main.py registered routers and answered
/health, but nothing ever created the database schema. With the shipped
`sqlite:///./app.db` default that produced the worst shape of broken — the
service starts cleanly, /health returns 200, and every real request fails with
"no such table".
"""
import os
import subprocess
import sys
import tempfile
import textwrap

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.utils.backend_infra import (                              # noqa: E402
    render_config, render_database, render_main, render_package_inits)

MODEL = '''from sqlalchemy import String
from sqlalchemy.orm import Mapped, mapped_column
from app.database import Base


class Contact(Base):
    __tablename__ = "contacts"
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(120))
'''

ROUTER = '''from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session
from app.database import get_db
from app.models.contact import Contact

router = APIRouter(prefix="/api/contacts", tags=["contacts"])


@router.get("/")
def list_contacts(db: Session = Depends(get_db)):
    return [{"id": c.id, "name": c.name} for c in db.query(Contact).all()]


@router.post("/")
def create_contact(name: str, db: Session = Depends(get_db)):
    c = Contact(name=name)
    db.add(c)
    db.commit()
    db.refresh(c)
    return {"id": c.id, "name": c.name}
'''


def _render_project(root: str, with_models: bool = True):
    os.makedirs(os.path.join(root, "app", "routers"), exist_ok=True)
    os.makedirs(os.path.join(root, "app", "models"), exist_ok=True)

    def write(rel, content):
        with open(os.path.join(root, rel), "w") as fh:
            fh.write(content)

    write("app/__init__.py", "")
    write("app/routers/__init__.py", "")
    write("app/config.py", render_config())
    write("app/database.py", render_database())
    write("app/routers/contacts.py", ROUTER)
    if with_models:
        write("app/models/contact.py", MODEL)
        generated = {"backend/app/models/contact.py": MODEL}
        for _, content in render_package_inits(
                ["backend/app/models/__init__.py"], generated):
            write("app/models/__init__.py", content)
    write("app/main.py", render_main("Acme CRM", ["backend/app/routers/contacts.py"]))


# ── the rendered source ──────────────────────────────────────────────────────

def test_main_creates_the_schema_at_startup():
    main = render_main("X", ["backend/app/routers/contacts.py"])
    assert "create_all" in main, (
        "nothing else creates the schema; without this every request fails "
        "with 'no such table' while /health still returns 200")
    assert "import app.models" in main, (
        "create_all against empty metadata silently creates nothing — the "
        "models import is what registers them")


def test_schema_creation_is_sqlite_only():
    """create_all cannot ALTER an existing table, so on a real database it would
    quietly diverge from the models the moment a column changes. Postgres uses
    the generated Alembic migrations."""
    main = render_main("X", [])
    assert 'startswith("sqlite")' in main
    assert "return" in main.split('startswith("sqlite")')[1][:120]


def test_health_route_survives_a_project_with_no_routers():
    """Build verification probes /health; a project whose routers all failed
    must still boot so the failure is visible as an empty API, not a crash."""
    main = render_main("X", [])
    assert "/health" in main
    assert "no routers were generated" in main


# ── the rendered project, actually running ───────────────────────────────────

_EXERCISE = textwrap.dedent('''
    from fastapi.testclient import TestClient
    from app.main import app

    with TestClient(app) as client:
        assert client.get("/health").status_code == 200, "health failed"
        created = client.post("/api/contacts/", params={"name": "Acme Ltd"})
        assert created.status_code == 200, f"create failed: {created.text}"
        listed = client.get("/api/contacts/")
        assert listed.status_code == 200, f"list failed: {listed.text}"
        assert listed.json() == [{"id": 1, "name": "Acme Ltd"}], listed.json()
    print("SERVED")
''')


def _run_in(root: str) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, "-c", _EXERCISE], cwd=root,
                          capture_output=True, text=True, timeout=120)


def test_the_rendered_project_serves_a_real_database_request():
    """The whole point: a downloaded project must answer a request that touches
    the database, with no migration step and no manual setup."""
    with tempfile.TemporaryDirectory() as root:
        _render_project(root)
        result = _run_in(root)
        assert "SERVED" in result.stdout, (
            f"rendered project did not serve:\n{result.stdout}\n{result.stderr}")
        assert os.path.exists(os.path.join(root, "app.db")), (
            "no sqlite file was created — the schema step did not run")


def test_a_project_with_no_models_still_boots():
    """A run whose model phase produced nothing must not crash on startup; the
    models import is optional by design."""
    with tempfile.TemporaryDirectory() as root:
        _render_project(root, with_models=False)
        # Without models the contacts router cannot import, so only assert the
        # app itself starts — rendered separately with no routers.
        with open(os.path.join(root, "app", "main.py"), "w") as fh:
            fh.write(render_main("X", []))
        result = subprocess.run(
            [sys.executable, "-c",
             "from fastapi.testclient import TestClient\n"
             "from app.main import app\n"
             "with TestClient(app) as c:\n"
             "    assert c.get('/health').status_code == 200\n"
             "print('BOOTED')"],
            cwd=root, capture_output=True, text=True, timeout=120)
        assert "BOOTED" in result.stdout, f"{result.stdout}\n{result.stderr}"


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
