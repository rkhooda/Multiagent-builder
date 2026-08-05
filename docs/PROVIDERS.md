# Provider Map — Single Source of Truth

**Dated: 2026-08-05.** This table is transcribed from the live `MODELS` dict in
`backend/app/llm_router.py`, which is the only authority. **Update this file in
the same commit as any routing change.** Routing has drifted twice and silently
invalidated design premises both times (Day 23: qwen3-coder:free delisted, both
coders moved to Groq; Day 26: the nemotron reasoning model demoted off QA) —
a dated map is what stops a third silent drift.

| Agent type | Primary | Fallback | Primary's daily limit |
|---|---|---|---|
| research | gemini-2.5-flash | groq llama-3.3-70b | Gemini 1M tokens/day (tracked, rarely binding) |
| requirements | gemini-2.5-flash | openrouter north-mini-code:free | Gemini 1M tokens/day |
| architecture | groq llama-3.3-70b | gemini-2.5-flash | **Groq 100k tokens/day (scarce)** |
| planning | gemini-2.5-flash | groq llama-3.3-70b | Gemini 1M tokens/day |
| frontend_code | groq llama-3.3-70b | gemini-2.5-flash | **Groq 100k tokens/day (scarce)** |
| frontend_review (Improvement 01, default off) | gemini-2.5-flash | groq llama-3.3-70b | Gemini 1M tokens/day |
| backend_code | groq llama-3.3-70b | gemini-2.5-flash | **Groq 100k tokens/day (scarce)** |
| database | groq llama-3.3-70b | gemini-2.5-flash | **Groq 100k tokens/day (scarce)** |
| qa | gemini-2.5-flash | groq llama-3.3-70b | Gemini 1M tokens/day |
| devops | groq llama-3.3-70b | gemini-2.5-flash | **Groq 100k tokens/day (scarce)** |

## NVIDIA NIM — third fallback tier (Day 31)

Once primary and fallback are both spent, the chain now has a third *cloud*
tier before falling through to local Ollama, drawn from NVIDIA's
build.nvidia.com catalog behind a free-credit key (`NVIDIA_API_KEY` in
`backend/.env`, bridged to litellm's expected `NVIDIA_NIM_API_KEY` at load
time). Both models in each list below are spliced into the chain, in order —
not just the first — so a rate limit on the better NVIDIA model still falls
through to the second before reaching Ollama.

| Category | Agents | Chain (best → worst) |
|---|---|---|
| Code | architecture, frontend_code, backend_code, database, devops | `nvidia_nim/qwen/qwen2.5-coder-32b-instruct` → `nvidia_nim/deepseek-ai/deepseek-r1-distill-qwen-32b` |
| Prose/judgment | research, requirements, planning, qa, frontend_review | `nvidia_nim/deepseek-ai/deepseek-r1` → `nvidia_nim/nvidia/llama-3.1-nemotron-ultra-253b-v1` → `nvidia_nim/qwen/qwen2.5-72b-instruct` |

**DeepSeek R1 and Nemotron Ultra carry the same reasoning-model risk already
documented below for nemotron**: they can ignore `max_tokens` and spend the
whole budget on hidden reasoning before any answer text. That is why both sit
at tier 3 (last-resort), never primary/fallback, with Nemotron ordered after
R1 pending more trust — NVIDIA's own Nemotron family is specifically what got
demoted off QA on Day 26. DeepSeek's distilled sibling
(`deepseek-r1-distill-qwen-32b`, used for code) has shorter reasoning chains
than the flagship and is lower-risk, but not risk-free.

**The rate limit is a rolling per-account cooldown, not a daily allowance —
user-confirmed 2026-08-05** (tested directly against NVIDIA's models in
another tool): hitting the limit on any model clears itself in ~10-15
minutes, unlike Groq/Gemini's fixed UTC-midnight reset. `llm_router.py`
therefore tracks this with its own timestamp-based cooldown
(`_nvidia_cooldown_until`, ~12 min default, tunable via
`LLM_COOLDOWN_MINUTES_NVIDIA_NIM`) rather than the daily-budget mechanism
above — one 429 from any NVIDIA model excludes the *whole* tier from the
chain (the limit is account-wide, not per-slug) until the cooldown expires,
same shape as Ollama being silently absent when no daemon is detected. It is
NOT in `_DAILY_TOKEN_LIMITS`, deliberately — that tracker assumes a
midnight reset, which is the wrong shape here.

Model slugs above are best-effort from the public NIM catalog, not verified
live. If one is wrong or delisted, the existing "unclassified error → next
tier" handling already degrades gracefully — this is exactly how the delisted
`openrouter/qwen3-coder:free` was survived (Day 23, above).

A fourth, local tier (Ollama, unmetered) is appended to every chain when a
local daemon is detected; `LLM_MODE=prefer-local` puts it first, ahead of
every cloud tier including NVIDIA. `LLM_MODE=cloud-only` excludes only Ollama
— NVIDIA stays in, since it is cloud too.

## Which limit is currently scarce

**Groq's 100,000 tokens/day** — it is the primary for all four file-producing
agents plus architecture. Measured refusal at 95,966/100,000 (Day 26). Gemini's
1M/day has never been the binding constraint; OpenRouter free tier limits
*requests*, not tokens, and now serves only the requirements fallback.

## Consequences that designs must respect (as of the date above)

- **QA (gemini) and the coders (groq) draw on DIFFERENT primary pools.** There
  is no primary-pool token contention between reviewing and generating. They
  only contend when one has already failed over to the other's provider —
  i.e. when that provider is already degraded.
- **QA's primary is a THINKING model.** gemini-2.5-flash spends its output
  budget on reasoning before emitting a single answer token (measured 2026-08-03:
  2,427–2,740 reasoning tokens per QA batch; worst observed total completion
  4,949). Any max_tokens for a gemini-flash call must be sized for reasoning +
  answer, not answer alone — this is the Improvement-01 truncation trap, and at
  the QA ceiling it presents as an *error + silent groq failover*, not as a
  visible truncation.
- The stale premise "QA runs DeepSeek R1 via OpenRouter" is **wrong** — that
  routing never shipped; nemotron (also a reasoning model, via OpenRouter) held
  the slot until Day 26 demoted it for ignoring max_tokens.
