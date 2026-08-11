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

## Provider expansion (2026-08-11) — probed, and mostly refuted

Three free-tier providers were added. **Only Mistral survived contact.** Each
line below is a live call made on 2026-08-11, not a published claim:

| Provider | Live result | Routing |
|---|---|---|
| `cerebras` | Every model: *"Payment required to access this resource"*, on a valid `csk-` key. `/v1/models` also 403s | **none** |
| `deepseek` | Every model: *"Insufficient Balance"*. Live catalogue is `deepseek-v4-{pro,flash}` — litellm still maps the older `deepseek-chat`/`deepseek-reasoner` | **none** |
| `mistral` | 55 catalogue ids → 22 chat candidates → **13 admitted** | **3 per agent** |

The two failures are worth more than the success, because they were the two the
plan was justified by. Cerebras' 1M tokens/day *per model* is real and correctly
documented — it is simply not available on a free account any more, and no
amount of reading the rate-limit page would have shown that. DeepSeek's "5M
tokens on signup" was flagged UNVERIFIED in `docs/PROVIDER_EXPANSION.md` and is
now settled as **false**.

Both remain configured. Nothing needs writing if either is ever paid for or
re-granted: add credit, re-run `scripts/probe_models.py`, and the matrix admits
them.

### Configuration

| Provider | Pacing | Daily budget | Cooldown | Basis |
|---|---|---|---|---|
| `cerebras` | 12.0s | 1,000,000 | 1m (default) | Published 5 RPM / 1M TPD per model |
| `deepseek` | 2.0s | 0 (untracked) | 1m (default) | Concurrency limits only; no daily allowance |
| `mistral` | **5.0s** | 0 (untracked) | 1m (default) | **MEASURED** from response headers |

Mistral's pacing is measured from its own API, which is now the only primary
source — the published free-tier numbers were withdrawn:

```
x-ratelimit-limit-req-minute:    50
x-ratelimit-limit-tokens-minute: 50000
```

The widely reported **"2 RPM" is wrong by 25×**. The 5.0s value is TPM-derived
like Groq's, not RPM-derived: 50k tokens/min against ~4k-token coder calls binds
at ~12 req/min (4.8s) long before 50 RPM (1.2s) does. Note this is **4× Groq's
12k TPM**, which is what makes Mistral real capacity rather than a token pool.

Deliberate non-entries, so a later reader does not read them as drift:

- **No `_COOLDOWN_MINUTES` rows.** All three limit per *minute*, so the 1-minute
  default is already the right window; a row repeating it is a second place to
  drift.
- **DeepSeek and Mistral are budget-untracked for the reason NVIDIA is** — a
  UTC-midnight counter models the wrong *shape*. A credit balance never resets;
  Mistral's pool is monthly.
- **`SCARCE_PROVIDERS` unchanged** (`{"groq"}`): none of the three is scarce.

### The chain, and where the matrix sits

**primary → fallback → matrix-admitted expansion → NVIDIA NIM → OpenRouter free
→ Ollama.**

The expansion tier is ahead of NVIDIA/OpenRouter because its models were
admitted on **measured contract compliance against this pipeline's own output
shapes**, while those two lists were verified only for reachability and latency.
It sits behind primary/fallback because a probe is one call and the incumbents
have production history. The ordering is *how much evidence there is*.

**No expansion model routes without a row in
`backend/config/model_capabilities.json`.** The incumbents are exempt and stay
hardcoded: their evidence is daily production use in `metrics.db` plus this
file, which is stronger than one probe — and making the working chain contingent
on a JSON file would mean one bad file deletes the pipeline. The matrix gates
*adding* capacity, never removing it. A missing or unreadable matrix costs depth
and nothing else.

### The quality floor is a contract, not a score

`codestral-latest` and `mistral-code-latest` — both **code-specialist** models —
wrap output in markdown fences when explicitly told not to. They would emit
`.jsx` files that do not parse. Both are admitted for JSON/prose agents and
**barred from every code agent**. Five further models fail both probes and route
nowhere. This is what stops "never stalls" from becoming "never stops producing
garbage": depth is only admitted where it was measured to work.

Depth is capped at **3 models per provider**. Thirteen Mistral models is not
depth — they share one account and one rate limit, so a provider-wide 429 costs
thirteen round trips to discover (cooldown is per *model*) and adds no capacity.
Real depth comes from another provider.

### `ROUTING_MODE=pinned|auto`

`pinned` drops the expansion tier, restoring the exact pre-expansion chain. It
exists for **measurement**: an A/B arm whose chain varies between runs measures
whichever tier happened to answer. Improvements 01 and 02 both shipped UNPROVEN
for want of budget, and re-running them against a silently varying chain would
produce a number nobody should believe.

## Prompt caching (verified against provider docs, 2026-08-10)

Checked because the token audit found Groq spend is 90% prompt-side and the
repeated per-call preamble is the dominant payload — native prompt caching
would make that preamble nearly free **if** it applied. It does not, today:

- **Groq**: prompt caching exists and is automatic, and cached tokens **do
  not count toward rate limits** — but it is only available on the GPT-OSS
  models (`gpt-oss-20b`, `gpt-oss-120b`, `gpt-oss-safeguard-20b`). The
  coders' primary `llama-3.3-70b-versatile` is **not supported**. Caching
  requires exact prefix matches (static content first, variable content
  last).
- **Gemini 2.5 Flash**: implicit caching is on by default, minimum prefix
  **2048 tokens**. Today's coder messages put the per-file TASK block first,
  so the cross-call common prefix is only the system prompt (~940–1,700
  tokens) — below the threshold; implicit caching never triggers. The
  documented benefit is billing (moot at $0); rate-limit treatment is not
  documented.

**Consequence for designs:** the repeated coder preamble is not free on any
pool in the current routing. Moving the coders' primary to `groq/gpt-oss-20b`
(a slug already live-verified in the fallback tiers) would make cached prefix
tokens rate-limit-free on the scarce pool — but that is a routing change with
its own quality evaluation, not a config tweak. If it is ever made, reorder
the coder context to shared-prefix-first at the same time, or the cache never
matches.

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
