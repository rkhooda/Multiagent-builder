# Provider Map — Single Source of Truth

**Dated: 2026-08-03.** This table is transcribed from the live `MODELS` dict in
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

A third local tier (Ollama, unmetered) is appended to every chain when a local
daemon is detected; `LLM_MODE=prefer-local` puts it first.

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
