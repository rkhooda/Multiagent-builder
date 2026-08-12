"""Day 26: output budget resolution + truncation detection. Zero API calls."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.llm_router import (FAST_MODE_FLOOR, REASONING_ANSWER_FLOOR,
                            _finish_reason, is_reasoning_model,
                            model_max_tokens, resolve_max_tokens)


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
        # Improvement 03, measured 2026-08-08 from real generations of the two
        # new profiles (backend/metrics.db, projects gen-static-site-v3 and
        # gen-express-v2; zero truncation flags on any call). A new prompt does
        # NOT inherit a ceiling from a differently-shaped one, so each is
        # measured on its own output even where it reuses an existing routing
        # key. Both sit under the ceiling that key already carries, which is
        # the finding — no ceiling needed raising, and now that is pinned
        # rather than assumed.
        ("frontend_code[static-site]",  FRONTEND_FILE_MAX_TOKENS, 562),
        ("backend_code[express]",       BACKEND_FILE_MAX_TOKENS,  464),
        ("database[express-prisma]",    DATABASE_MAX_TOKENS,      154),
    ]
    for agent, ceiling, requirement in measured:
        assert ceiling >= requirement, (
            f"{agent}: configured ceiling {ceiling} < measured requirement "
            f"{requirement} (default profile)")
        # The routing key is what resolve_max_tokens scales; the bracketed
        # suffix only records which profile produced the measurement.
        effective_fast = resolve_max_tokens(agent.split("[")[0], ceiling, fast_mode=True)
        assert effective_fast >= requirement, (
            f"{agent}: fast-mode effective ceiling {effective_fast} < measured "
            f"requirement {requirement} — exclude it from FAST_MODE_SCALABLE "
            f"or raise FAST_MODE_FLOOR")


def test_reasoning_tier_gets_room_to_think_behind_a_direct_primary():
    """The defect that shipped project e8935f86 (2026-08-12): four agents run a
    direct primary and a THINKING fallback behind ONE ceiling sized for the
    primary. When groq rate-limited, gemini spent the whole budget reasoning and
    the answer was truncated — 7 of that run's 9 truncations, zero on the
    primary. See docs/VERIFICATION_GAP_ANALYSIS.md and CEILING_AUDIT finding #4,
    which predicted this exact interaction and deferred it.

    Basis: 1.2 x (worst gemini reasoning 2,740 + largest complete coder answer
    943) ~= 4,420."""
    from app.agents.backend_coder_agent import BACKEND_FILE_MAX_TOKENS
    from app.agents.database_agent import DATABASE_MAX_TOKENS
    from app.agents.devops_agent import DEVOPS_MAX_TOKENS
    from app.agents.frontend_coder_agent import FRONTEND_FILE_MAX_TOKENS

    direct, thinking = "groq/llama-3.3-70b-versatile", "gemini/gemini-2.5-flash"
    assert not is_reasoning_model(direct)
    assert is_reasoning_model(thinking)

    # The four agents whose fallback is a thinking model. Each must give that
    # tier room for reasoning AND the largest complete coder answer (943).
    for name, ceiling in (("frontend_code", FRONTEND_FILE_MAX_TOKENS),
                          ("backend_code", BACKEND_FILE_MAX_TOKENS),
                          ("database", DATABASE_MAX_TOKENS),
                          ("devops", DEVOPS_MAX_TOKENS)):
        assert model_max_tokens(thinking, ceiling) >= 2740 + 943, name
        # The primary tier is untouched: raising it would raise groq's TPM
        # admission cost on every call, which is why the flat raise was rejected.
        assert model_max_tokens(direct, ceiling) == ceiling, name

    # Ceilings already MEASURED with reasoning included must not be inflated.
    from app.agents.architecture_agent import ARCHITECTURE_MAX_TOKENS
    from app.agents.qa_agent import QA_MAX_TOKENS
    from app.agents.requirements_agent import REQUIREMENTS_MAX_TOKENS
    for name, ceiling in (("qa", QA_MAX_TOKENS),
                          ("requirements", REQUIREMENTS_MAX_TOKENS),
                          ("architecture", ARCHITECTURE_MAX_TOKENS)):
        assert model_max_tokens(thinking, ceiling) == ceiling, (
            f"{name}: floor {REASONING_ANSWER_FLOOR} raised a ceiling that was "
            f"already measured with reasoning included ({ceiling}) — double-counted")

    # Every reasoning slug in the chains is recognised, under either provider
    # prefix. An unrecognised one silently keeps the direct-tier ceiling.
    for slug in ("nvidia_nim/openai/gpt-oss-20b", "openrouter/openai/gpt-oss-20b:free",
                 "nvidia_nim/nvidia/nemotron-3-super-120b-a12b",
                 "openrouter/nvidia/nemotron-3-super-120b-a12b:free",
                 "nvidia_nim/thinkingmachines/inkling", "ollama/qwen3:4b"):
        assert is_reasoning_model(slug), slug
    for slug in ("groq/llama-3.3-70b-versatile", "mistral/mistral-medium-3.5",
                 "nvidia_nim/minimaxai/minimax-m3", "ollama/phi4-mini"):
        assert not is_reasoning_model(slug), slug


def test_a_truncated_call_is_a_lower_bound_on_requirement():
    """The flaw in how `measured` above is built, found 2026-08-13.

    Its numbers are maxima over completions that did NOT truncate. But a
    truncated call is precisely the evidence that requirement EXCEEDS the
    ceiling — and excluding it means the table can only ever record numbers
    below the ceiling, so `ceiling >= requirement` passes by construction while
    every call truncates. That is how frontend_code stayed pinned at a measured
    829 through five truncations at 1,496 in the same run.

    The rule: a truncation flag sets requirement > ceiling for that tier, and
    the pin must be updated from the truncation, not from the survivors."""
    ceiling = 1500
    complete_runs = [829, 943, 612]
    truncated_at = [1496, 1496, 1500]

    naive = max(complete_runs)
    assert ceiling >= naive          # passes, and means nothing
    assert truncated_at, "a truncated call proves requirement > ceiling"
    assert max(truncated_at) >= ceiling - 5
    # Honest requirement: unknown, but strictly greater than what was cut off.
    assert ceiling < max(truncated_at) + 1


def test_expansion_models_fit_every_agent_ceiling():
    """Provider expansion (2026-08-11): an admitted model's output requirement
    is pinned in EVERY profile, fast mode included.

    A second model on an agent's chain is a second output requirement, and the
    probe measures each one against a fixed task so the numbers are comparable.
    Groq needs 230 tokens for it; every admitted Mistral model needs 0.91-1.17x
    that. Scaling each agent's measured requirement by the WORST admitted ratio
    is what turns "this model looks fine" into a bound.

    The check runs in both directions, so this cannot go stale silently:
    an agent that fits must fit, and an agent listed as saturated must genuinely
    NOT fit — otherwise the exclusion is stale and should be deleted.

    Skips when the matrix is absent or has no baseline (no probe has run here),
    because an unmeasured ratio is not evidence of anything.
    """
    from app import model_matrix as M

    admitted = [r for r in M._ROWS if r.get("verbosity_ratio")]
    if not admitted or not M._MATRIX.get("baseline_output_tokens"):
        print("    (skipped: no probed matrix with a baseline on this checkout)")
        return
    worst = max(r["verbosity_ratio"] for r in admitted)

    from app.agents.architecture_agent import ARCHITECTURE_MAX_TOKENS
    from app.agents.backend_coder_agent import BACKEND_FILE_MAX_TOKENS
    from app.agents.database_agent import DATABASE_MAX_TOKENS
    from app.agents.devops_agent import DEVOPS_MAX_TOKENS
    from app.agents.frontend_coder_agent import FRONTEND_FILE_MAX_TOKENS

    # Same measured requirements as the table above; only the agents the
    # expansion tier can actually serve.
    served = [
        ("frontend_code", FRONTEND_FILE_MAX_TOKENS, 829),
        ("backend_code",  BACKEND_FILE_MAX_TOKENS,   96),
        ("database",      DATABASE_MAX_TOKENS,      722),
        ("devops",        DEVOPS_MAX_TOKENS,         64),
        ("architecture",  ARCHITECTURE_MAX_TOKENS, 11996),
    ]
    for agent, ceiling, requirement in served:
        needed = requirement * worst
        effective_fast = resolve_max_tokens(agent, ceiling, fast_mode=True)
        excluded = agent in M.CEILING_SATURATED_AGENTS
        if excluded:
            # The exclusion must be EARNED. If headroom appears — the ceiling
            # was raised, or a verbose model left the matrix — delete the entry
            # rather than leaving capacity switched off for a reason that
            # stopped being true.
            assert effective_fast < needed, (
                f"{agent} is in CEILING_SATURATED_AGENTS but now fits "
                f"({effective_fast} >= {needed:.0f}) — the exclusion is stale, "
                f"remove it and let the expansion tier serve this agent")
            assert M.models_for(agent) == [], (
                f"{agent} is excluded but models_for still returns models")
            continue
        assert effective_fast >= needed, (
            f"{agent}: fast-mode ceiling {effective_fast} < {requirement} x "
            f"{worst} = {needed:.0f} tokens needed by the most verbose admitted "
            f"model — raise the ceiling or add {agent} to "
            f"CEILING_SATURATED_AGENTS")


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
