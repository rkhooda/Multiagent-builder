"""Crafted-breakage tests for the Day 22 validation layer.

ZERO LLM calls: repairs are driven by a fake repair function, so this runs in
the fast suite alongside test_parallel_runner.py. Runnable directly
(`python3 tests/test_validation.py`) and under pytest.

The load-bearing test here is `test_valid_jsx_passes`. Plain acorn cannot parse
JSX; if it ever sneaks back in as the parser, EVERY generated React component
reports a syntax error, the repair loop burns OpenRouter budget regenerating
already-correct files, and every metric in the validation report lies. That one
test keeps the trap shut.
"""
import os
import sys

BACKEND_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BACKEND_DIR)

from app.validation.syntax import (  # noqa: E402
    validate_python, validate_artifact, validate_js_batch, validate_js_imports,
    js_syntax_heuristic, js_tool_status, JsToolUnavailable,
)

# ── Fixtures: realistic generated output, valid and broken ───────────────────

VALID_JSX = """import React, { useState } from 'react';
import NoteCard from './NoteCard';

export default function NotesPage({ notes, onDelete }) {
  const [query, setQuery] = useState('');
  const visible = notes?.filter((n) => n.title.includes(query)) ?? [];
  return (
    <div className="p-4">
      <input value={query} onChange={(e) => setQuery(e.target.value)} />
      {visible.map((note) => (
        <NoteCard key={note.id} note={{ ...note }} onDelete={() => onDelete(note.id)} />
      ))}
      {visible.length === 0 && <p>No notes yet</p>}
    </div>
  );
}
"""

BROKEN_JSX = """import React from 'react';

export default function Broken() {
  return (
    <div>
      <span>hello</div>
    </div>
  );
}
"""

BROKEN_DESTRUCTURE = """export function useThing({ a, b ) {
  return a + b;
}
"""

PROSE_LEAK = """Here is the component you asked for:

export default function App() { return <div />; }
"""


def _check(name, cond, detail=""):
    print(f"  {'ok  ' if cond else 'FAIL'} {name}{'' if cond else ' -> ' + detail}")
    return cond


# ── JS / JSX syntax ──────────────────────────────────────────────────────────

def test_valid_jsx_passes():
    """THE regression: valid JSX must NOT be reported as a syntax error."""
    out = validate_js_batch({"frontend/src/pages/NotesPage.jsx": VALID_JSX})
    assert out == {}, f"valid JSX false-positived: {out}"


def test_modern_syntax_passes():
    """Optional chaining, nullish coalescing, spread — all already in VALID_JSX,
    plus a plain .js module using them."""
    src = "export const f = (o) => o?.a ?? { ...o, b: 1 };\n"
    out = validate_js_batch({"frontend/src/lib/util.js": src})
    assert out == {}, f"modern syntax false-positived: {out}"


def test_broken_jsx_fails_with_line():
    out = validate_js_batch({"a.jsx": BROKEN_JSX})
    assert "a.jsx" in out, "unbalanced JSX tag not caught"
    issue = out["a.jsx"][0]
    assert issue.line == 6, f"expected line 6, got {issue.line}"
    assert issue.kind == "syntax"


def test_broken_destructuring_fails():
    out = validate_js_batch({"b.js": BROKEN_DESTRUCTURE})
    assert "b.js" in out and out["b.js"][0].line >= 1


def test_prose_leak_fails():
    """Stray prose the fence-stripper missed must not parse as a module."""
    out = validate_js_batch({"c.jsx": PROSE_LEAK})
    assert "c.jsx" in out, "prose leak parsed as valid JS"


def test_batch_is_one_process_and_isolates_files():
    """Many files, one call: a broken file must not mask its valid neighbours."""
    out = validate_js_batch({
        "ok1.jsx": VALID_JSX, "bad.jsx": BROKEN_JSX, "ok2.js": "export const a = 1;\n",
    })
    assert set(out) == {"bad.jsx"}, f"expected only bad.jsx to fail, got {set(out)}"


def test_non_js_ignored():
    assert validate_js_batch({"a.py": "def f(): pass", "b.md": "# hi"}) == {}


# ── Python syntax ────────────────────────────────────────────────────────────

def test_valid_python_passes():
    assert validate_python("from app.db import Base\n\n\ndef f(x):\n    return x\n", "m.py") == []


def test_missing_colon_fails_at_right_line():
    src = "def ok():\n    return 1\n\n\ndef bad()\n    return 2\n"
    out = validate_python(src, "m.py")
    assert out and out[0].line == 5, f"expected line 5, got {out and out[0].line}"


def test_compile_only_error_caught():
    """ast.parse accepts this; compile() is what rejects it."""
    out = validate_python("def f(a, a):\n    return a\n", "m.py")
    assert out and "duplicate argument" in out[0].message


# ── JSON / YAML artifacts ────────────────────────────────────────────────────

def test_valid_artifacts_pass():
    assert validate_artifact('{"name": "app", "dependencies": {"react": "^19"}}', "package.json") == []
    assert validate_artifact("services:\n  web:\n    image: node:20\n", "docker-compose.yml") == []


def test_broken_json_flagged():
    out = validate_artifact('{"name": "app",}', "package.json")
    assert out and out[0].kind == "artifact"


def test_broken_yaml_flagged():
    out = validate_artifact("services:\n  web:\n   image: x\n  bad\n", "docker-compose.yml")
    assert out and out[0].kind == "artifact" and out[0].line >= 1


# ── Imports & packages (FLAG-only) ───────────────────────────────────────────

PKG = '{"dependencies": {"react": "^19.0.0", "axios": "^1.7.0"}}'


def test_phantom_relative_import_flagged():
    files = {
        "frontend/src/pages/NotesPage.jsx": "import NoteCard from './NoteCard';\nexport default 1;\n",
        "package.json": PKG,
    }
    out = validate_js_imports(files, PKG)
    assert "frontend/src/pages/NotesPage.jsx" in out
    assert out["frontend/src/pages/NotesPage.jsx"][0].kind == "phantom_import"


def test_resolvable_relative_import_not_flagged():
    """Extension candidates must resolve: './NoteCard' -> NoteCard.jsx."""
    files = {
        "frontend/src/pages/NotesPage.jsx": "import NoteCard from '../components/NoteCard';\n",
        "frontend/src/components/NoteCard.jsx": "export default function NoteCard() {}\n",
    }
    assert validate_js_imports(files, PKG) == {}


def test_index_file_resolution():
    files = {
        "frontend/src/App.jsx": "import x from './components';\n",
        "frontend/src/components/index.js": "export default 1;\n",
    }
    assert validate_js_imports(files, PKG) == {}


def test_missing_package_flagged():
    files = {"frontend/src/App.jsx": "import dayjs from 'dayjs';\n"}
    out = validate_js_imports(files, PKG)
    assert out and out["frontend/src/App.jsx"][0].kind == "missing_package"


def test_declared_and_subpath_packages_not_flagged():
    files = {"frontend/src/main.jsx": (
        "import React from 'react';\n"
        "import { createRoot } from 'react-dom/client';\n"
        "import axios from 'axios';\n"
        "import fs from 'node:fs';\n")}
    pkg = '{"dependencies": {"react": "^19", "react-dom": "^19", "axios": "^1"}}'
    assert validate_js_imports(files, pkg) == {}


# ── Degradation ──────────────────────────────────────────────────────────────

def test_node_missing_degrades_loudly(monkeypatch=None):
    """With node absent, validate_js_batch must RAISE (never silently return
    clean) so the caller can fall back and say so in the report."""
    import app.validation.syntax as syn
    real = syn.shutil.which
    syn.shutil.which = lambda name: None
    try:
        raised = False
        try:
            syn.validate_js_batch({"a.jsx": VALID_JSX})
        except JsToolUnavailable:
            raised = True
        assert raised, "missing node did not raise JsToolUnavailable"
    finally:
        syn.shutil.which = real


def test_heuristic_fallback_still_detects():
    assert js_syntax_heuristic("function f() { return 1;\n", "a.js")
    assert js_syntax_heuristic("export const a = 1;\n", "a.js") == []


def _run_all():
    tests = [(n, f) for n, f in sorted(globals().items())
             if n.startswith("test_") and callable(f)]
    passed = failed = 0
    status = js_tool_status()
    print(f"js tooling: {'available' if not status else 'UNAVAILABLE — ' + status}")
    for name, fn in tests:
        try:
            fn()
            passed += _check(name, True)
        except AssertionError as e:
            failed += 1
            _check(name, False, str(e))
        except Exception as e:
            failed += 1
            _check(name, False, f"{type(e).__name__}: {e}")
    print(f"\n{passed} passed, {failed} failed")
    return failed


if __name__ == "__main__":
    sys.exit(1 if _run_all() else 0)
