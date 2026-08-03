"""Day 26: output budget resolution + truncation detection. Zero API calls."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.llm_router import FAST_MODE_FLOOR, _finish_reason, resolve_max_tokens


class _Choice:
    def __init__(self, reason):
        self.finish_reason = reason


class _Resp:
    def __init__(self, reason):
        self.choices = [_Choice(reason)]


def test_call_site_value_is_the_default():
    """The measured budgets live at the call sites and must survive untouched —
    architecture already runs at 11,996/12,000, so any silent lowering truncates."""
    assert resolve_max_tokens("architecture", 12000) == 12000
    assert resolve_max_tokens("frontend_code", 1500) == 1500


def test_env_override_wins():
    os.environ["LLM_MAX_TOKENS_RESEARCH"] = "2000"
    try:
        assert resolve_max_tokens("research", 4500) == 2000
    finally:
        del os.environ["LLM_MAX_TOKENS_RESEARCH"]


def test_env_override_beats_fast_mode():
    """An operator setting an explicit number means it, in either mode."""
    os.environ["LLM_MAX_TOKENS_RESEARCH"] = "3000"
    try:
        assert resolve_max_tokens("research", 4500, fast_mode=True) == 3000
    finally:
        del os.environ["LLM_MAX_TOKENS_RESEARCH"]


def test_junk_env_is_ignored_not_fatal():
    os.environ["LLM_MAX_TOKENS_RESEARCH"] = "lots"
    try:
        assert resolve_max_tokens("research", 4500) == 4500
    finally:
        del os.environ["LLM_MAX_TOKENS_RESEARCH"]


def test_fast_mode_scales_agents_with_measured_headroom():
    assert resolve_max_tokens("database", 2500, fast_mode=True) == 1250


def test_fast_mode_never_scales_an_agent_measured_at_its_ceiling():
    """architecture ran 11,996/12,000 and requirements 4,496/4,500 on both
    calls. Halving those does not shorten the document, it cuts it off. qa
    joined this set 2026-08-03: its gemini primary thinks for 2.4-2.7k tokens
    before answering, so halving starves the answer, not the reasoning."""
    for agent, budget in (("architecture", 12000), ("requirements", 4500),
                          ("research", 4500), ("planning", 9000), ("qa", 6000)):
        assert resolve_max_tokens(agent, budget, fast_mode=True) == budget, agent


def test_ceilings_cover_measured_requirements():
    """Ceiling audit 2026-08-03 (docs/CEILING_AUDIT.md): an output requirement
    and its token ceiling ship together, in EVERY profile. This table is the
    measured worst complete response per agent (reasoning + answer for
    thinking models); update it when routing or requirements change — the same
    discipline as docs/PROVIDERS.md. Twice now a requirement was raised
    without its ceiling (reviewer at 700 vs ~2,000; QA at 3,000 vs 4,949) and
    both shipped as silent failover, not visible truncation.

    Measured bases: qa 4,949 (worst complete gemini batch, 2026-08-03);
    frontend_review 3,075 derived (same-model reasoning 2,740 + worst groq
    answer 335 — no gemini review has ever completed to measure directly);
    frontend_code 829 (TaskForm.jsx, groq); planning 26,894 (worst real plan
    vs the dynamic cap); doc-writers pin at their saturated measured max.
    Thin-history agents (database, devops, backend_code) pin their best
    available dev-script measurements."""
    from app.agents.architecture_agent import ARCHITECTURE_MAX_TOKENS
    from app.agents.backend_coder_agent import BACKEND_FILE_MAX_TOKENS
    from app.agents.database_agent import DATABASE_MAX_TOKENS
    from app.agents.devops_agent import DEVOPS_MAX_TOKENS
    from app.agents.frontend_coder_agent import FRONTEND_FILE_MAX_TOKENS
    from app.agents.frontend_reviewer import REVIEW_MAX_TOKENS
    from app.agents.planning_agent import PLANNING_TOKENS_CAP
    from app.agents.qa_agent import QA_MAX_TOKENS
    from app.agents.requirements_agent import REQUIREMENTS_MAX_TOKENS
    from app.agents.research_agent import RESEARCH_MAX_TOKENS

    measured = [
        ("research",        RESEARCH_MAX_TOKENS,       4368),
        ("requirements",    REQUIREMENTS_MAX_TOKENS,   4496),
        ("architecture",    ARCHITECTURE_MAX_TOKENS,  11996),
        ("planning",        PLANNING_TOKENS_CAP,      26894),
        ("frontend_code",   FRONTEND_FILE_MAX_TOKENS,   829),
        ("frontend_review", REVIEW_MAX_TOKENS,         3075),
        ("backend_code",    BACKEND_FILE_MAX_TOKENS,     96),
        ("database",        DATABASE_MAX_TOKENS,        722),
        ("devops",          DEVOPS_MAX_TOKENS,           64),
        ("qa",              QA_MAX_TOKENS,             4949),
    ]
    for agent, ceiling, requirement in measured:
        assert ceiling >= requirement, (
            f"{agent}: configured ceiling {ceiling} < measured requirement "
            f"{requirement} (default profile)")
        effective_fast = resolve_max_tokens(agent, ceiling, fast_mode=True)
        assert effective_fast >= requirement, (
            f"{agent}: fast-mode effective ceiling {effective_fast} < measured "
            f"requirement {requirement} — exclude it from FAST_MODE_SCALABLE "
            f"or raise FAST_MODE_FLOOR")


def test_fast_mode_respects_the_floor():
    """Halving a coder file's 1,500 would stop it mid-function, so the floor
    binds instead — fast mode is meant to be lighter, not to emit broken files."""
    assert resolve_max_tokens("frontend_code", 1500, fast_mode=True) == FAST_MODE_FLOOR


def test_truncation_is_read_from_finish_reason():
    assert _finish_reason(_Resp("length")) == "length"
    assert _finish_reason(_Resp("stop")) == "stop"


def test_finish_reason_never_raises_on_a_malformed_response():
    """Detection is instrumentation — it must not be able to fail a real call."""
    class Broken:
        choices = []
    assert _finish_reason(Broken()) == ""
    assert _finish_reason(None) == ""


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
