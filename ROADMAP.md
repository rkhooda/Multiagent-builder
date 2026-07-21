# Roadmap

Two sections, deliberately kept apart:

- **[Known limitations](#known-limitations-today)** — what is true *today*, so
  you can decide whether this tool fits your problem before you spend an
  afternoon on it.
- **[Planned improvements](#planned-improvements)** — what might change later.

Conflating those two is how a tool loses trust, so nothing below moves between
them without evidence. Every planned item cites a **measured** observation from
the build. Anything that sounded good but has no measurement behind it is in
[Explicitly not planned](#explicitly-not-planned) instead.

---

## Where this sits

The vision this was built toward describes five levels of autonomy, from
"structured assistant with human approval at every step" up to "autonomous
system that ships without supervision."

**This is Level 1, and Level 1 is what it was meant to be.** A brief goes in;
research, requirements, architecture, a task plan, generated code, automated
validation, an LLM QA pass and devops files come out; and a human approves at
four gates along the way. Nothing runs unsupervised, and nothing downstream is
built on an artifact a person has not seen.

That framing is the honest one, and it is also the useful one: the gates are
not a limitation on the way to autonomy, they are the reason the output is
trustworthy at all. Levels 2+ are about *removing* human checkpoints, which is
only safe once the layer beneath them is reliable. The measurements below show
that layer is not yet reliable enough to start removing gates.

---

## Known limitations (today)

### 1. Free-tier quota is the binding constraint, not model quality

This is the single most important thing to know, and it is measured, not
estimated:

| Provider | Limit | Measured behaviour |
|---|---|---|
| Gemini 2.5 Flash | ~20 requests/day/model | 15 ok / 77 rate-limited, then exhausted (Day 25) |
| Groq llama-3.3-70b | 12,000 tokens/min, 100,000/day | 32 ok / 109 rate-limited (Day 25) |
| OpenRouter free | ~50 requests/day | Slugs also delist without notice |

One **simple** project (a todo app) issued **233 LLM attempts** and consumed
**193,409 tokens** — roughly twice Groq's entire daily allowance, against a
Gemini allowance of 20 requests. **The pipeline cannot reliably complete one
project per day on free tiers.**

Practical consequences you should expect:

- A run will often pause partway through as `rate_limited`. This is normal.
  The checkpoint holds; wait for the daily reset and retry.
- **If no local tier is provisioned, quota exhaustion is a hard pause, not a
  graceful degradation.** Verified on Day 30: with Ollama absent, a run stopped
  at the research stage and could not proceed. "Degrades instead of failing"
  requires you to have actually pulled a local model.
- Use Gate 3 aggressively to cut the plan. It is the main cost lever you have.

### 2. Measured output quality is low, and partly for this reason

The one fully scored run reached **21.9% of planned files "usable"** — usable
meaning the file exists, parses, its imports resolve, and it is not a stub
(Day 25, `docs/INTEGRATION_RESULTS.md`).

**That number is not a clean measure of model capability.** Of 233 attempts,
188 failed and essentially all were 429s. Three of the four top defect classes
— never-generated, stub, and syntax — are the same underlying event: the
request never reached a model.

But it is the honest number this project can currently defend, and no run has
yet been measured under unconstrained quota. **Treat "60–80% complete" claims
about this class of tool — including any you may have read about this one — as
unproven.** What is verified is the *structure*: the plan, the architecture,
the folder layout and the boilerplate are consistently well-shaped.

### 3. Complexity degrades output, and the ceiling is real

Real-time collaboration, heavily stateful designs and anything requiring live
coordination between clients sit at or past the ceiling. The intended Day 25
degradation curve across three complexity tiers **was never measured** — quota
ran out after the first, simplest project — so the exact shape of the curve is
unknown rather than characterised.

Aim at conventional CRUD applications with a mainstream stack. That is where
this demonstrably works best.

### 4. Planning is the structural bottleneck

- It emits **~26,900 tokens in a single call**, which makes it unservable by
  any TPM-limited provider. With Gemini exhausted, nothing gets past planning.
- It **over-decomposes**: a todo app produced a **96-task, 95-file plan**.
- Splitting the plan across calls risks incoherence. That is a design change,
  not a tuning knob, and it has deliberately not been attempted under a quota
  ceiling where it could not be A/B tested.

### 5. Local models do not currently replace cloud

Ollama removes the quota ceiling and costs nothing, and it genuinely keeps a
run moving. But on an 8GB machine it has **never completed a full run** (Day
29, two attempts, two different failure modes):

- `phi4-mini` could not emit a valid plan — 718 completion tokens against a
  ~21,500 cloud average, rejected by the schema validator.
- `qwen3:4b` produces valid JSON but is far too slow: 403s for a research stage
  that Gemini does in ~8s. Local is **30–50× slower** across the board.
- Reasoning models charge thinking against `max_tokens`: asked for a small JSON
  array at 600 tokens, `qwen3:4b` returned an **empty string**. **Fast Mode
  plus a local reasoning model produces empty files.**

Local is a per-stage fallback on this hardware, not a way to run the pipeline.
16GB+ is where it plausibly becomes practical.

### 6. Free model slugs vanish without notice

This has broken the pipeline **twice** — `qwen/qwen3-coder:free` delisted
(Day 23), then the QA primary `nemotron` began returning upstream errors on
every call (Day 25). Check `MODELS` in `llm_router.py` against OpenRouter's
live `/api/v1/models` before assuming a logic bug.

### 7. Smaller known issues

- **Gate 3 plan validation accepts cyclic `requires`.** The scheduler is
  cycle-proof, so this is not currently harmful, but validation should reject
  cycles and does not.
- **Cross-file consistency is the weakest output dimension.** Imports across
  module boundaries are where generated projects most often break.
- **QA is an LLM reviewing code** — useful, and fallible. The deterministic
  `validation` stage is the one to trust on mechanical defects.
- **The download ZIP silently skips non-UTF-8 files.** Logged server-side only.
- **`LLM_MODE` is process-wide**, not per project. Concurrent runs cannot use
  different tiers.

---

## Planned improvements

Ordered by evidence strength, not by appeal.

### Near-term — remove known blockers

**Split the planning call.** *Evidence: ~26,900 tokens in one request makes
planning unservable by any TPM-limited provider; it is the single point where
runs die with Gemini exhausted (Day 25).* Decompose per phase, with a coherence
pass across the parts. Requires an A/B under real quota to confirm the parts
stay coherent — which is exactly what could not be done under the Day 25
ceiling.

**Constrain plan size at the source.** *Evidence: 96 tasks / 95 files for a
todo app (Day 25); plan size drives total token spend, which is the binding
constraint.* A brief-derived complexity budget the planner must fit, rather
than relying on the user to cut tasks manually at Gate 3.

**Reject cyclic dependencies in Gate 3 validation.** *Evidence: known gap
above; the old error message already claimed to do this.* Small and cheap.

**A live model-availability check at startup.** *Evidence: slug delisting broke
the pipeline twice (Days 23, 25).* Probe `MODELS` against the provider's live
list on boot and warn, instead of discovering it as a 404 mid-run.

### Medium-term — raise output quality

**Coder-critic review loops.** *Evidence: cross-file consistency is the top
residual defect class after the Day 21/22 work, and per-file AI fixes at Gate 4
demonstrably repair files — but only when a human notices and asks.* A critic
pass that reviews generated files against the plan and their siblings before
QA would apply that same capability automatically.

**Richer architecture reasoning for stateful designs.** *Evidence: real-time
and collaborative briefs sit at/past the ceiling, and architecture errors
cascade into every generated file.* Stateful designs need explicit reasoning
about data flow and synchronisation that the current single-pass architecture
agent does not do.

**Incremental / parallel QA.** *Evidence: QA is the slowest stage — one
observed success took 354s, batched over many files (Day 26).* Reviewing files
as they are produced, rather than in one batch at the end, would cut wall-clock
and remove a large single call from the critical path.

**Measure the degradation curve properly.** *Evidence: Day 25 set out to
measure quality across three complexity tiers and only ever completed the
simplest one.* Until this exists, every complexity claim here — including the
limitations above — is under-evidenced. This is the highest-value measurement
outstanding, and it needs either paid quota or a 16GB+ local machine.

### Longer-term — from the original Phase 2 ideas

Kept because they align with observed needs, not because they were on a list:

- **Push to GitHub** — aligns with the fact that the ZIP is currently a
  dead-end artifact you must manually turn into a repository.
- **Code-quality scoring surfaced in the UI** — `score_project.py` already
  computes the tier ladder offline; showing it at Gate 4 would give the user
  the same number this roadmap cites.
- **Alternative output templates** (stacks beyond React/FastAPI/Postgres) —
  worth doing only after the quality work above, since a second stack multiplies
  the surface every existing defect lives on.
- **Mobile / React Native output** — the furthest out, and the least supported
  by anything measured.

### Toward Level 2

Level 2 means removing a human gate. The honest prerequisite is that the layer
underneath it becomes reliable enough that a human reading it would almost
always approve. On the evidence, **Gate 1 (requirements) is the nearest
credible candidate** — requirements output has been the most consistently
sound artifact across the build — and only with an auto-approve that a user can
switch on deliberately, never as a default.

Gates 2 and 3 should stay. Architecture and plan errors cascade into every file
generated afterward, and both are still the stages where runs most visibly go
wrong.

---

## Explicitly not planned

- **Full autonomy.** Not a Level-5 system and not on a path to becoming one.
  The gates are the design.
- **Paid model tiers as the default.** The constraint this was built under —
  free tiers — is also what makes it usable by anyone who clones it. Paid keys
  work today by simply providing them; the defaults will not assume them.
- **Chasing the free-tier throughput ceiling.** Measured and documented as
  inherent (Day 25). No prompt, context or validation change moves it.

---

*Every number here traces to `docs/INTEGRATION_RESULTS.md`,
`docs/QUALITY_BASELINE.md`, or `docs/build-journal/failures.md`. If you find a
claim in this file without a measurement behind it, it is a bug in the file.*
