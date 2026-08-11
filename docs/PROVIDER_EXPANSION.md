# Provider Expansion — Verification Record

**Dated: 2026-08-11.** Written before any routing change. Everything here is
split into **VERIFIED** (checked today against a primary source, with the
source named) and **UNVERIFIED** (with the specific thing that would settle
it). Nothing in the unverified section may be built on — that rule exists
because this project's routing premises have been silently wrong three times
(`docs/PROVIDERS.md`: the delisted `qwen3-coder:free`, the demoted nemotron,
the whole-namespace-dead NVIDIA `qwen/*` slugs), and each cost a session.

## Why this is not an OmniRoute integration

The task began as "integrate OmniRoute, a gateway exposing 90+ providers and
500+ models behind a single API key, with a large monthly free token
allowance." That premise did not survive verification.

| Premise | Verified finding (2026-08-11) |
|---|---|
| Hosted gateway; paste key + base URL | **Self-hosted local proxy.** Default `http://localhost:20128/v1`. README: *"OmniRoute is a local proxy that never phones home."* There is no vendor endpoint. |
| A single vendor-issued API key | The key is **generated in its own local dashboard**. Self-issued. |
| Large monthly free token allowance | **OmniRoute grants no tokens.** Free capacity comes from upstream provider accounts the operator signs up for and configures. The widely-quoted "1.6B tokens/month" is the arithmetic sum of ~90 providers' free tiers, realisable only by registering with all of them. |
| 90+ providers / 500+ models available | Only those personally configured. With this repo's four existing keys, the pool would be **four providers**. |

Source: [github.com/diegosouzapw/OmniRoute](https://github.com/diegosouzapw/OmniRoute)
(MIT, 45,509 stars, last push 2026-08-11 — the project is real and active;
it is the *description of what it does for us* that was wrong).

**The decisive point is architectural, not factual.** The binding constraint
is Groq's 100k tokens/day. OmniRoute is a router; pointing it at the existing
keys yields the same 100k/day. This repository already owns a router —
`build_chain()` (`backend/app/llm_router.py:875`) — with per-provider RPM/RPD
tracking, per-model cooldowns, typed exceptions and failover accounting behind
it.

Routing through the gateway would have **actively degraded** that machinery.
`_provider_of(model)` (`llm_router.py:224`) derives the provider from the model
string; every gateway call becomes provider `omniroute`, collapsing
`_DAILY_TOKEN_LIMITS`, the per-provider cooldown table and `SCARCE_PROVIDERS`
failover accounting into one opaque bucket — destroying exactly the
per-provider visibility that three drift incidents paid for.

Secondary consideration, recorded but not decisive: OmniRoute v3.8.5 was
flagged by Socket.dev for suspected MITM/root-CA behaviour. No malware was
confirmed and the maintainer's response was rated responsible, but they
acknowledged **2 of 6 flags as genuine vulnerabilities**, patched in 3.8.6 —
a silent credential-overwrite path in Cloud Sync and a credential-exposure
flaw in Keychain Import ([issue #2863](https://github.com/diegosouzapw/OmniRoute/issues/2863)).
It is a single-maintainer project that would custody every key we hold.

**Decision:** add free-tier providers *directly* to the existing chain. Capacity
comes from the provider accounts either way; direct signup also preserves
per-provider budget tracking instead of blinding it.

## VERIFIED — 2026-08-11

### Transport

`litellm 1.83.9` is already installed (`backend/requirements.txt`) and
**natively supports** the provider prefixes we need. Checked by enumerating
`litellm.provider_list` in the project venv:

| Provider | Native litellm support | Model-string form |
|---|---|---|
| `cerebras` | yes | `cerebras/<model>` |
| `deepseek` | yes | `deepseek/<model>` |
| `mistral` | yes | `mistral/<model>` |
| `inception` | **no** | — |
| `longcat` | **no** | — |

Consequence: for Cerebras/DeepSeek/Mistral **no adapter code is needed at
all** — they are model strings in the existing lists, and `_provider_of()`
already derives the provider from the prefix. Inception and LongCat would each
need custom OpenAI-compatible plumbing (`openai/` prefix plus `api_base`), so
they are excluded for now — see "What I chose not to build".

### Context windows

`litellm.get_model_info()` already returns context windows, so no
context-window database is needed for mapped slugs. Measured today:

| Model | max_input_tokens | max_output_tokens |
|---|---|---|
| `cerebras/llama-3.3-70b` | 128,000 | 128,000 |
| `deepseek/deepseek-chat` | 131,072 | 8,192 |
| `deepseek/deepseek-reasoner` | 131,072 | 65,536 |
| `mistral/mistral-large-latest` | 262,144 | 262,144 |
| `mistral/codestral-latest` | 32,000 | 8,191 |
| `groq/llama-3.3-70b-versatile` (existing) | 128,000 | 32,768 |
| `gemini/gemini-2.5-flash` (existing) | 1,048,576 | 65,535 |

**Unmapped slugs miss**: `cerebras/qwen-3-coder-480b` raises *"This model isn't
mapped yet"*. So the Phase 3 context-window filter needs a fallback for
unmapped models — the probe records the window into the capability matrix, and
matrix value wins over `get_model_info()`.

### Cerebras free tier

Primary source: [inference-docs.cerebras.ai/support/rate-limits](https://inference-docs.cerebras.ai/support/rate-limits).
Free Trial tier, **per model**:

| Model | RPM | TPM | TPH | TPD |
|---|---|---|---|---|
| `gpt-oss-120b` | 5 | 30K | 1M | 1M |
| `zai-glm-4.7` | 5 | 30K | 1M | 1M |
| `gemma-4-31b` | 5 | 30K | 1M | 1M |

Quoted verbatim: *"Every model on the public Model Catalog is available on the
Free Trial tier, subject to the per-model Free Trial rate limits listed
above."*

**This is the headline capacity finding.** 1M tokens/day **per model** against
Groq's 100k/day total — a 10× increase on a single model, and the allowance is
per-model across the catalogue.

**The catch is RPM 5, not the token ceiling.** One call per 12 seconds. The
coders run 3 parallel workers (`GENERATION_MODE=parallel`), so Cerebras cannot
absorb a parallel coder phase at full speed. This is already modelled by the
existing `LLM_MIN_INTERVAL_{PROVIDER}` key — no new mechanism, but the pacing
value must be set deliberately (`LLM_MIN_INTERVAL_CEREBRAS=12`), and Cerebras
should sit as depth behind Groq rather than replacing it for parallel work.

### DeepSeek

Primary source: [api-docs.deepseek.com/quick_start/rate_limit](https://api-docs.deepseek.com/quick_start/rate_limit).
Documents **concurrency** limits only — `deepseek-v4-pro` 500,
`deepseek-v4-flash` 2500 concurrent connections; HTTP 429 when exceeded.
**No free tier is documented anywhere in the official docs.**

**Slug drift, live:** DeepSeek's current documented models are
`deepseek-v4-pro` and `deepseek-v4-flash`. litellm's `get_model_info()` maps
the *older* `deepseek-chat` / `deepseek-reasoner`. This is the same failure
shape as the dead NVIDIA `qwen/*` namespace — a mapped slug is not a live
slug. Any DeepSeek admission must use catalogue-confirmed slugs.

## SETTLED BY LIVE PROBE — 2026-08-11

Every row below was open when this document was first written and is now closed
by an actual call, made by `backend/scripts/probe_models.py`. **Two of the three
providers the plan was built on do not work.**

| Claim | Verdict | Evidence |
|---|---|---|
| Cerebras free tier is usable (1M tokens/day per model) | **REFUTED for this account** | Every model returns *"Payment required to access this resource"* on a valid `csk-` key. `/v1/models` returns HTTP 403. The published allowance is real; free access to it is not. |
| Cerebras caps free-tier context at 8,192 tokens | **UNRESOLVABLE** — cannot be tested without inference access | Moot: no traffic routes there |
| DeepSeek "5M free tokens on signup" | **REFUTED** | Every model returns *"Insufficient Balance"*. As suspected, the claim appears nowhere in DeepSeek's own docs. |
| Mistral "Experiment" tier ≈2 RPM | **REFUTED — wrong by 25×** | Mistral's own response headers: `x-ratelimit-limit-req-minute: 50`, `x-ratelimit-limit-tokens-minute: 50000`. TPM is the binding shape, and 50k/min is **4× Groq's 12k**. |
| Whether each provider returns `usage` token counts | **Mistral: yes** | `usage_reported: true` on every admitted row. Untestable for the other two. |
| Live model catalogue per provider | **Fetched** | Mistral 55 ids, DeepSeek 2 (`deepseek-v4-pro`/`-flash`), Cerebras 403. |
| DeepSeek slug drift | **CONFIRMED** | litellm maps `deepseek-chat`/`deepseek-reasoner`; neither exists in the live catalogue. Same shape as the dead NVIDIA `qwen/*` namespace — a mapped slug is not a live slug. |

The lesson generalises past this expansion: **a valid key is not access.** Both
failures returned billing errors, not auth errors, so nothing short of an
inference call would have exposed them — not the docs, not the key format, not
`/v1/models` (DeepSeek happily listed models it refuses to serve).

### Mistral, measured

13 of 22 chat candidates admitted. The quality floor did real work:

| Model | Verdict |
|---|---|
| `codestral-latest`, `mistral-code-latest` | **Barred from code agents.** Both *code-specialist* models wrap output in markdown fences when told not to — they would emit `.jsx` that does not parse. Admitted for JSON/prose only. |
| `devstral-latest`, `devstral-medium-latest`, `ministral-{3b,8b,14b}-latest`, `mistral-large-latest`, `mistral-code-agent-latest` | Failed both contract probes. Route nowhere. |
| `labs-leanstral-1-5{,-1}` | Listed in the catalogue, refused on call |
| `mistral-medium*`, `mistral-small-latest`, `mistral-vibe-cli-*`, `glm-5-2`, `zai-glm-5-2`, `magistral-small-latest` | Admitted for all agents |

That a *code* model fails the *code* contract while a general model passes is
the case for probing rather than reasoning from a model's name.

## Measured: what an 8,192-token cap would actually cost

The 8k cap above is unverified, but its *consequence* is measurable today from
`metrics.db` (`agent_runs`, 753 calls with recorded prompt tokens). This is
banked data, not a projection — it makes the worst case cheap to reason about
before spending anything on signup.

| Agent | n | avg prompt | max prompt | calls over 8,192 |
|---|---|---|---|---|
| planning | 92 | 1,324 | **22,695** | 2 |
| architecture | 136 | 240 | 7,448 | 0 |
| backend_code | 40 | 2,638 | 4,897 | 0 |
| frontend_review | 48 | 3,452 | 4,431 | 0 |
| research | 71 | 463 | 4,409 | 0 |
| qa | 52 | 1,209 | 4,097 | 0 |
| frontend_code | 240 | 1,302 | 3,973 | 0 |
| requirements | 67 | 332 | 2,803 | 0 |
| database | 6 | 1,683 | 2,581 | 0 |
| devops | 1 | 24 | 24 | 0 |

**Overall: 2 of 753 calls (0.3%) exceed 8,192 prompt tokens, both `planning`.**

This corrects an inference made earlier in this document's first revision, that
the coders' prompts exceed 8k — that came from reading "11–15k characters" in
`backend/.env.example` as tokens. At ~3.5 chars/token those prompts are 3–4k
tokens, which is what the table measures.

Two consequences that matter:

- **The worst case is survivable.** Even if the cap is real, all four
  file-producing agents — the ones on the scarce Groq pool — fit under it with
  room to spare. Cerebras remains usable for the constraint that actually
  binds. Only `planning` (already on Gemini's non-scarce 1M/day) would be
  excluded, and `architecture` at 7,448 max has thin headroom.
- **Context-window filtering is load-bearing, not theoretical.** Architecture
  sits within 10% of an 8k ceiling and prompt size grows with project
  complexity, so the Phase 3 filter must skip a too-small model *before*
  attempting it rather than discovering the overflow as an error.

## What I chose NOT to build, and why

1. **The OmniRoute gateway adapter.** It adds routing, not capacity, and would
   blind the per-provider budget tracking. The capacity comes from provider
   accounts either way.
2. **A catalogue fetcher with a dated snapshot file and a refresh script.**
   That machinery is sized for a 500-model gateway. Three providers is on the
   order of ~20 candidate models, and the probe run must contact each model
   anyway — so the **capability matrix is the catalogue**. One committed
   artefact, not two. Add a separate fetcher when a provider's model list is
   observed to churn faster than the probe cadence.
3. **A ranking config separate from the matrix.** The matrix's `tier` column
   *is* the ranking. A second file to keep in sync is a drift source.
4. **A runtime capability service.** Committed JSON read at import. No process,
   no cache invalidation, no network at boot.
5. **A parallel budget ledger.** `_DAILY_TOKEN_LIMITS` + `_spend()` +
   `budget_exhausted()` already do exactly this. New providers are dict
   entries.
6. **Lazy mid-run probing of unknown models.** "No model routes without a
   matrix row" makes the admitted set closed. Lazy probing is a runtime
   service wearing a different hat, and it would let an unmeasured model serve
   production traffic — the precise thing the quality floor exists to prevent.
7. **Inception and LongCat.** Not litellm-native, so each needs custom
   OpenAI-compatible plumbing; both are one-time grants rather than renewing
   allowances; neither has known model quality for these agents. Cost is real
   and the capacity is not renewable. Revisit only if the renewing tiers prove
   insufficient.
8. **A context-window database.** `litellm.get_model_info()` covers mapped
   slugs; the probe fills gaps into the matrix for the rest.

## Resolved: duplicate keys in `.env` shadowed the working ones

The first probe run reported all four incumbent keys as invalid. The keys were
never lost. Pasting the `.env.example` block into `.env` to add the three new
keys **re-declared the four existing names further down the file** as
`your_key_here`, and **python-dotenv keeps the LAST occurrence of a name**, so
the placeholders silently won:

```
line  1   GEMINI_API_KEY=<real>          <- ignored
line 23   GEMINI_API_KEY=your_key_here   <- what the process actually loaded
```

Reading the resolved value shows only the placeholder, which is why the first
diagnosis was wrong; counting occurrences per name is what found it. The four
duplicate lines were removed and all five providers now authenticate live.

Worth generalising: **`.env` has no duplicate-key warning.** A repeated name is
not an error, not a warning, and invisible to anything that reads the resolved
value — including every diagnostic in this repo. When a key that should work
does not, count its occurrences before assuming it is wrong.

`.gitignore` also gained `.env.*` with `!.env.example`: the bare `.env` rule
matched neither `.env.local` nor the `.env.backup-*` written during this fix, so
a side file holding the same real keys sat in `git status` as an ordinary
untracked file waiting to be added.

## Measured: the verbosity ratio

With all keys working, the re-probe produced the baseline the ceiling pin needs.
`groq/llama-3.3-70b-versatile` = **230 tokens** on the fixed sizing task.

| Model | Output tokens | Ratio |
|---|---|---|
| `mistral/mistral-small-latest` | 210 | 0.91 |
| `mistral/mistral-medium-latest`, `-3.5` | 249 | 1.08 |
| `mistral/mistral-medium`, `glm-5-2`, `zai-glm-5-2` | 254 | 1.10 |
| `mistral/magistral-small-latest` | 268 | **1.17** |
| `gemini/gemini-2.5-flash` *(incumbent)* | **1,726** | **7.5** |

Two findings:

- **Mistral is at parity with Groq** (0.91–1.17×), so it fits every agent
  ceiling with one exception below.
- **Gemini costs 7.5× the tokens for the same file.** This is the reasoning tax
  `docs/PROVIDERS.md` describes qualitatively, now a number. It is measurement,
  not inference, and it is why any ceiling for a gemini call must be sized for
  reasoning plus answer.

### The one agent that cannot take the depth

`architecture` has a ceiling of 12,000 against a measured requirement of
**11,996 — 99.97%, already at the wall on Groq**. At 1.17× it would need ~14,035
and truncate by ~2,000 tokens. It is therefore excluded from the expansion tier
(`model_matrix.CEILING_SATURATED_AGENTS`) and keeps its incumbent chain.

That truncation is the expensive kind: the architecture document is the input to
planning, and at a reasoning-shaped ceiling it presents as an error plus a
silent failover rather than as visible truncation — the defect that starved QA
for weeks. Depth for architecture must come from raising the ceiling on
evidence, not from quietly routing it somewhere it does not fit.

The exclusion is pinned in **both directions**: if headroom ever appears, the
test fails and says to delete the entry, so capacity is not left switched off
for a reason that stopped being true.

## Status

Phases 1–3 shipped and verified live: providers registered, probe and committed
capability matrix, matrix-driven routing with the context pre-flight filter, the
contract-based quality floor, the ceiling-saturation guard, and
`ROUTING_MODE=pinned|auto`.

End-to-end proof, not just unit tests: with all 12 incumbent models marked
budget-exhausted, a real `frontend_code` call fell through to
`mistral/mistral-medium-3.5` and returned a clean fence-free component on the
first attempt — logged and attributed in `metrics.db`.

Offline regression gate: **25/25 green** (`test_build_verify` and
`test_sandbox_hostile` SKIPPED — no docker daemon on this host, so the
build-verification ladder and sandbox isolation are not covered by this run).

Still unmeasured: the payoff number — whether a two-arm A/B now fits in a day's
budget, and which UNPROVEN verdicts (Improvements 01 and 02) can be settled.
That needs a full pipeline run.
