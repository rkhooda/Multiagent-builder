"""Day 26: the coder folder map must shrink WITHOUT losing a path.

Wrong relative imports are the defect context_builder exists to prevent, so the
grouped folder map is only acceptable while it is provably lossless.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.agents.context_builder import _folder_map

FILES = [
    "frontend/src/App.jsx",
    "frontend/src/main.jsx",
    "frontend/src/components/Header.jsx",
    "frontend/src/components/NoteCard.jsx",
    "frontend/src/components/NoteList.jsx",
    "frontend/src/components/auth/LoginForm.jsx",
    "frontend/src/hooks/useNotes.js",
    "frontend/src/lib/api.js",
    "frontend/src/pages/NotesPage.jsx",
    "backend/app/main.py",                      # other phase — must be excluded
]


def _paths_in(rendered: str) -> set:
    """Reconstruct full paths from the grouped rendering."""
    out, folder = set(), None
    for line in rendered.split("\n"):
        if line.strip().endswith("/"):
            folder = line.strip().rstrip("/")
        elif line.strip():
            out |= {f"{folder}/{n.strip()}" for n in line.split(",")}
    return out


def test_every_path_survives_the_grouping():
    expected = {p for p in FILES if p.startswith("frontend/src")}
    assert _paths_in(_folder_map(FILES, "frontend/src")) == expected


def test_other_phases_are_still_excluded():
    assert "backend/app/main.py" not in _folder_map(FILES, "frontend/src")


def test_grouping_is_actually_smaller():
    flat = "\n".join(f"  {p}" for p in sorted(
        p for p in FILES if p.startswith("frontend/src")))
    assert len(_folder_map(FILES, "frontend/src")) < len(flat)


def test_root_level_files_are_not_lost():
    """A file directly under the prefix has no subdirectory to group beneath."""
    assert _paths_in(_folder_map(FILES, "frontend/src")) >= {
        "frontend/src/App.jsx", "frontend/src/main.jsx"}


def test_empty_input_renders_nothing():
    assert _folder_map([], "frontend/src") == ""
    assert _folder_map(["backend/x.py"], "frontend/src") == ""


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
