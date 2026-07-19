# Prompt Changelog

Every change to a file in `prompts/` is recorded here. Prompts are the agents'
actual behaviour spec — a git diff shows *what* a line became but never *why* it
exists, so next week's "improvement" can silently undo this week's measured fix.

## Protocol

Follow it in order. The discipline is the deliverable as much as the fixes are.

1. **Attribute before editing.** Establish which layer actually caused the defect
   (`docs/QUALITY_BASELINE.md` §1). A prompt edit for a context-layer defect
   bloats every call and fixes nothing. Re-run `build_file_context` — it is pure —
   and check whether the truth was in the context.
2. **Write the hypothesis here first**, before touching the prompt:
   *defect X occurs because Y; changing Z should reduce it; measured by criterion
   C on fixtures F.*
3. **One change at a time.** Two simultaneous edits make the result unattributable.
4. **A/B it**: `backend/scripts/ab_prompt_test.py`, N=3 samples per variant per
   fixture, frozen fixtures.
5. **Decision rule**: keep only if the new variant is ≥ the old on *every*
   criterion **and** strictly better on the targeted one. A tie on the target is a
   **revert** — the change cost tokens and bought nothing measurable.
6. **Record the verdict either way.** A revert is a result, not a failure; it is
   the institutional knowledge this file exists to hold.
7. **Commit the change (or the revert + finding) on its own.**

Regression protection: winning variants' sampled outputs are committed as golden
files under `backend/tests/fixtures/prompt_tuning/golden/` and re-scored offline
with `--rescore` at zero API cost. Run it after any prompt edit.

### Measurement notes

- The harness calls `groq/llama-3.3-70b-versatile` **directly**, not through
  `call_llm`. That is the model which produced the entire Day 18–20 evidence base
  (`qwen3-coder:free` has returned 429 on every attempt for three sessions), so
  the A/B measures the baseline's own model while spending zero OpenRouter quota.
- Pass rate for a criterion = passing samples / (fixtures × samples).
- `guards_api_data` is an acknowledged **heuristic**: it detects wholesale
  omission of `?.`/`??`, not every unguarded access. Treat movement on it as a
  weak signal.

---

## 2026-07-19 (Day 21)

Budget plan set before the first call: A/B runs cost **0 OpenRouter calls** (groq
direct); the only OpenRouter spend today is Task 5's single architecture
regeneration. Guard warns at 25, stops at 30.

### Entry 1 — frontend coder: import-resolution rule

- **Prompt file**: `prompts/frontend_coder_agent.md`
- **Defect**: D5 — imports of things that do not exist. `NoteCard.jsx:6`
  `import './NoteCard.css'`; `formatDate.js:3-4` `from 'intl'` /
  `'intl-datetimeformat'`. Severity **breaks-startup** (Vite fails to resolve).
- **Attribution**: **PROMPT**. The folder map was in context and untrimmed, and
  the no-CSS rule already exists at line 8 — the model saw both and violated
  them. The `intl` case is a prompt *gap*: no rule covers importing a package
  that is not a dependency.
- **Hypothesis**: the existing rule fails because it is stated **positively and
  in passing** ("use Tailwind for ALL styling"), buried in a list of nine other
  hard rules, and the few-shot example demonstrates only correct behaviour — the
  model never sees the forbidden form written down. Stating the wrong form
  explicitly beside the right one, as a dedicated block, should reduce it.
  Adding a matching rule for JS globals should close the `intl` gap.
- **Measured by**: `imports_resolve` (target) and `no_css_import`, on fixtures
  `fe_notecard` and `fe_formatdate`. N=3.
- **Result** (`groq/llama-3.3-70b-versatile`, N=3, 12 samples):

  | Criterion | A | B | Δ |
  |---|---|---|---|
  | `imports_resolve` **(target)** | 3/6 (50%) | 5/6 (83%) | **+33 pts** |
  | `no_fences` | 5/6 (83%) | 5/6 (83%) | 0 |
  | `no_css_import` | 6/6 (100%) | 6/6 (100%) | 0 |
  | `min_lines` | 6/6 | 6/6 | 0 |
  | `has_default_export` | 3/3 | 3/3 | 0 |
  | `guards_api_data` | 3/3 | 3/3 | 0 |
  | `endpoints_subset` | 3/3 | 3/3 | 0 |

- **VERDICT: KEEP.** Target improved, nothing regressed.
- **Two honest caveats.**
  1. The **CSS import defect did not reproduce** — variant A passed
     `no_css_import` 3/3. So the measured value of this change is entirely on
     *phantom package imports*, not on the CSS rule. The recurring defect is
     broader than Day 18 recorded: A invented `luxon` (×2) and `date-fns` (×1)
     for `formatDate.js`, none of which appear in `failures.md`. `intl` was one
     instance of a general class: **the model reaches for a date library**.
  2. **Negative examples can be echoed.** B's single failure imported `intl`
     *and* `intl-datetimeformat` — the exact two strings written in the WRONG
     block. Naming a forbidden package teaches the model the package name. Net
     effect was still strongly positive, but the next iteration should test
     counter-examples that describe the wrong shape without naming a real
     package. Logged as a follow-up hypothesis, not folded into this entry —
     one variable at a time.
- **Shipped == measured**: `prompts/frontend_coder_agent.md` is byte-identical to
  the A/B'd variant B. (An earlier attempt to ship an improved-but-unmeasured
  wording was reverted; ship what you measured, or measure what you ship.)
- **Golden files**: 4 passing B samples under
  `backend/tests/fixtures/prompt_tuning/golden/`, all re-scoring clean.
- **Cost**: 12 groq calls, **0 OpenRouter**.
