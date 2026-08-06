# Provider Map — Single Source of Truth

**Dated: 2026-08-06.** This table is transcribed from the live `MODELS` dict in
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

## Deep fallback tiers (2026-08-06)

The chain is **primary → fallback → NVIDIA NIM → OpenRouter free → Ollama**,
built by `build_chain()`. A provider with no key configured contributes
nothing, so a checkout with only `GEMINI_API_KEY` behaves as it always did.
Every model in each list below is spliced in, in order — a rate limit on one
falls through to the next rather than ending the tier.

Both lists were **verified live on 2026-08-06** against each provider's
catalog; every slug returned a real completion, and the latency shown is the
measured round trip for a trivial prompt. Ordering is fastest-verified-first,
non-reasoning models ahead of reasoning ones (a reasoning model can spend the
whole `max_tokens` budget thinking before emitting an answer — the Day 26
nemotron demotion — so they are runway, not first choice).

### NVIDIA NIM (`NVIDIA_API_KEY`, bridged to `NVIDIA_NIM_API_KEY` at load time)

| Category | Agents | Chain (best → worst) |
|---|---|---|
| Code | architecture, frontend_code, backend_code, database, devops | `poolside/laguna-xs-2.1` (4.4s, coder, non-reasoning) → `minimaxai/minimax-m3` (2.1s, non-reasoning) → `meta/llama-3.1-70b-instruct` (1.3s, non-reasoning) → `openai/gpt-oss-20b` → `thinkingmachines/inkling` → `nvidia/nemotron-3-super-120b-a12b` |
| Prose/judgment | research, requirements, planning, qa, frontend_review | `minimaxai/minimax-m3` → `meta/llama-3.1-70b-instruct` → `mistralai/mistral-medium-3.5-128b` → `openai/gpt-oss-20b` → `thinkingmachines/inkling` → `nvidia/nemotron-3-super-120b-a12b` → `nvidia/nemotron-3-nano-30b-a3b` |

**The previous NVIDIA slugs were all dead** and had been since they shipped:
`qwen/qwen2.5-coder-32b-instruct` and `qwen/qwen2.5-72b-instruct` return
**410 Gone** (the whole `qwen/*` namespace is out of NVIDIA's catalog), and
both `deepseek-ai/deepseek-r1*` slugs **404**. They were documented as
"best-effort … not verified live" — so the third tier was never a tier, just
two guaranteed-dead round trips before Ollama. Re-probe the live catalog
(`GET https://integrate.api.nvidia.com/v1/models`) before trusting a slug.

Also confirmed dead or unusable on 2026-08-06 and therefore *not* in the
lists: `mistralai/codestral-22b-instruct-v0.1`, `moonshotai/kimi-k2.6`,
`ibm/granite-8b-code-instruct`, `writer/palmyra-creative-122b`,
`nvidia/llama-3.1-nemotron-70b-instruct`, `mistralai/mistral-large-2-instruct`
(all 404); `z-ai/glm-5.2`, `deepseek-ai/deepseek-v4-flash`,
`deepseek-ai/deepseek-v4-pro`, `meta/llama-3.3-70b-instruct`,
`google/gemma-4-31b-it` (listed in the catalog but never answer — >180s on a
three-word prompt).

### OpenRouter free pool (`OPENROUTER_API_KEY`)

A fourth cloud tier on a *different* account and different upstreams, so it
survives an NVIDIA account-wide limit. It was previously reachable from
exactly one slot in `MODELS` (requirements' fallback) despite the key being
configured — capacity already paid for and not wired in.

| Category | Chain (best → worst) |
|---|---|
| Code | `poolside/laguna-s-2.1:free` (2.3s, coder) → `poolside/laguna-xs-2.1:free` → `openai/gpt-oss-20b:free` → `nvidia/nemotron-3-super-120b-a12b:free` |
| Prose/judgment | `google/gemma-4-31b-it:free` (1.2s, non-reasoning) → `google/gemma-4-26b-a4b-it:free` → `inclusionai/ling-3.0-flash:free` → `openai/gpt-oss-20b:free` → `nvidia/nemotron-3-super-120b-a12b:free` |

`nvidia/nemotron-3-ultra-550b-a55b:free` is excluded — its upstream returned
`ResourceExhausted` on every probe.

### Per-model cooldown replaces the same-model retry

A 429 (or a 503 / `ResourceExhausted`, or a timeout) takes **that model** out
of the chain for a provider-shaped window and the call moves straight to the
next model. There is no same-model retry any more: the chain *is* the retry,
and asking the provider that just refused is near-certain to fail again.

| Provider | Cooldown | Why |
|---|---|---|
| gemini | 1 min | free-tier RPM window |
| groq | 2 min | TPM window (the daily TPD ceiling is tracked separately, below) |
| openrouter | 5 min | free pool, partly rolling |
| nvidia_nim | 12 min | user-confirmed 10–15 min rolling window (2026-08-05) |

Tunable per provider with `LLM_COOLDOWN_MINUTES_<PROVIDER>`. Cooldown is per
**model**, not per provider: if a provider's limit is really account-wide,
each of its models pays one 429 before being skipped — bounded and visible in
the logs. If the *whole* chain is cooling down, the call waits for the soonest
model to come back (up to `MAX_COOLDOWN_WAIT_SECONDS`, 120s) rather than
failing — a pause, not a lost file.

This is distinct from `_DAILY_TOKEN_LIMITS`, which models the Groq/Gemini
allowances that reset at UTC midnight and cannot be waited out.

A fifth, local tier (Ollama, unmetered) is appended to every chain when a
local daemon is detected; `LLM_MODE=prefer-local` puts it first, ahead of
every cloud tier. `LLM_MODE=cloud-only` excludes only Ollama — NVIDIA and
OpenRouter stay in, since they are cloud too.

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
