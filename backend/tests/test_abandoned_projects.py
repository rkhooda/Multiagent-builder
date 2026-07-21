#!/usr/bin/env python3
"""Deleting a running project must stop its in-flight workers (Day 30).

WHY THIS EXISTS. task.cancel() cannot reach a thread dispatched by
asyncio.to_thread, so the coder phase of a deleted project kept calling
providers after deletion — measured on Day 30 as metrics rows regrowing from 0
to 5 for a project that returned 404. Quota is the scarcest resource in this
project, so spending it on a deleted project is the worst possible waste.

The guard lives at the call_llm choke point. These assertions pin the two
properties that matter: an abandoned project raises BEFORE any provider or
cache work happens, and an unrelated project is completely unaffected.

Zero API calls — the guard is checked ahead of every network path.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.exceptions import LLMError
from app.llm_router import (abandon_project, call_llm, is_abandoned,
                            _abandoned_projects)

MESSAGES = [{"role": "user", "content": "hello"}]


def test_unknown_project_is_not_abandoned():
    assert not is_abandoned("never-registered")


def test_empty_project_id_is_never_abandoned():
    # call_llm's project_id is optional; None must not match the guard, or
    # every attribution-free call site would start failing.
    assert not is_abandoned("")
    assert not is_abandoned(None)


def test_abandon_marks_only_that_project():
    abandon_project("doomed-1")
    assert is_abandoned("doomed-1")
    assert not is_abandoned("doomed-2")


def test_abandon_ignores_empty_id():
    before = len(_abandoned_projects)
    abandon_project("")
    abandon_project(None)
    assert len(_abandoned_projects) == before


def test_abandon_is_idempotent():
    abandon_project("doomed-3")
    abandon_project("doomed-3")
    assert is_abandoned("doomed-3")


def test_call_llm_refuses_an_abandoned_project():
    """The point of the whole change: no provider call, no cache lookup."""
    abandon_project("doomed-4")
    try:
        call_llm(MESSAGES, "research", project_id="doomed-4")
    except LLMError as exc:
        assert "cancelled or deleted" in str(exc)
        # model="none" records that no tier was ever attempted.
        assert exc.model == "none"
        return
    raise AssertionError("call_llm should have refused an abandoned project")


def test_guard_runs_before_the_cache():
    """A cached answer must not resurrect a deleted project's work."""
    abandon_project("doomed-5")
    try:
        # use_cache=True is the default; if the guard ran after the cache
        # lookup this could return a hit instead of raising.
        call_llm(MESSAGES, "research", project_id="doomed-5", use_cache=True)
    except LLMError:
        return
    raise AssertionError("guard must precede the cache lookup")


def _check(name, ok, detail=""):
    print(f"  {'PASS' if ok else 'FAIL'}  {name}{(' — ' + detail) if detail else ''}")
    return 1 if ok else 0


def _run_all():
    tests = [(n, f) for n, f in sorted(globals().items())
             if n.startswith("test_") and callable(f)]
    passed = failed = 0
    for name, fn in tests:
        try:
            fn()
            passed += _check(name, True)
        except AssertionError as e:
            failed += 1
            _check(name, False, str(e))
        except Exception as e:                      # noqa: BLE001
            failed += 1
            _check(name, False, f"{type(e).__name__}: {e}")
    print(f"\n{passed} passed, {failed} failed. (0 API calls)")
    return failed


if __name__ == "__main__":
    sys.exit(1 if _run_all() else 0)
