# Quality Baseline — Generated-Code Defect Taxonomy (Day 21)

Consolidates every recorded defect from `build-journal/failures.md` Days 12–20, the import-fixer
warnings and QA reports stored in checkpoint state, and a fresh full review of 27
generated files from the Day 18–20 output folders. Each defect class is attributed
to the **layer that actually caused it**, with the forensic evidence line recorded.

This document is written *before* any prompt is edited. Its job is to keep the day
honest: the ranked fix list at the bottom is the contract for what gets changed.

---

## 1. Attribution procedure (ponytail #1)

Fixing the wrong layer is the standard failure mode of prompt tuning: the prompt
accretes defensive rules, every call gets more expensive, and the defect survives.
So attribution runs *before* any edit.

The critical realisation is that **no new logging is needed**. `context_builder.
build_file_context(task, state)` is a pure function of `(task, state)` — the same
inputs always yield the same context string. The Day 18–20 logs record context
*size* and *trims* only, not content, but the builder can simply be re-run to
reproduce the exact string a file was given. Reuse the pure function; do not build
a forensics tool.

For a defect **D** in generated file **F**, with ground truth **T** (the symbol,
prop name, endpoint, or column type that F got wrong):

| Step | Check | Verdict |
|------|-------|---------|
| 1 | Re-run `build_file_context` for F's task against F's run state. Is **T** in the output string? | → step 2 if yes, step 3 if no |
| 2 | Does the reconstructed context's `trims` list show degradation? | Trimmed → **CONTEXT** (salience/position, not disobedience). Untrimmed → **PROMPT**: the model saw T and ignored it |
| 3 | Is **T** present in the `architecture_doc` itself? | Present → **CONTEXT BUILDER** (section selection or summarisation dropped it). Absent → **ARCHITECTURE** (upstream; queue for Task 5) |

A fourth layer emerged from the evidence and is worth naming separately, because it
changes the fix: **T was never extractable in principle** — the architecture defined
it, the builder selected the right section, but the extractor structurally cannot
carry it. That is still context-layer, but the fix belongs in *what gets extracted*,
not in section selection.

A fifth appeared during the run and is genuinely distinct: **PLANNER** — the truth
existed and was extractable, but the task's `requires` list never named the
dependency, so the builder was never asked for it.

Cost: ~5 minutes per class, zero API calls.

---

## 2. Consolidated defect table

Severity: **S**=breaks-startup, **F**=breaks-feature, **C**=cosmetic.
"Files" counts distinct generated files exhibiting the class.

| # | Defect class | Files | Phase | Sev | Layer | Evidence |
|---|---|---|---|---|---|---|
| D1 | Unused import / dead symbol | 8 | both | C | PROMPT (no rule exists) | `formatDate.js:4`, `NoteList.jsx:3-4`, `routers/tags.py:6-8`, `schemas/tag.py:7`, +4. Neither coder prompt mentions import hygiene |
| D2 | Prop / interface mismatch across a file seam | 5 | frontend | F | **PROMPT** (dual — see §3.1) | `NotesPage:23` passes `count=`, `Header:5` takes `noteCount`; `NotesPage:25` passes `onCreated=`, `NoteForm:6` takes `onCreate`; `NoteForm:18` calls `onCreate(a,b,c)` positionally vs `useNotes:30` `createNote(obj)` |
| D3 | Sibling-file inconsistency (same pattern, divergent shapes) | 5 | both | C→F | PROMPT (per-file isolation, no cross-file convention) | `routers/tags.py:32` uses `/{id}`, `routers/notes.py:45` uses `/{note_id}`; `notes.py` has no PUT while `pair-test/notes.py:39` does |
| D4 | Missing optional-chaining guard on API data | 3 | frontend | F | **PROMPT** (rule exists verbatim, ~50% adherence) | `NoteList.jsx:9` `notes.length`; `NoteCard.jsx:28,29,41`; `TagFilter.jsx:50`. Prompt l.13 states the rule; the few-shot demonstrates it |
| D5 | Import of a nonexistent package or file | 2 | frontend | **S** | **PROMPT** (one rule exists and is violated; one gap) | `NoteCard.jsx:6` `import './NoteCard.css'` — prompt l.8 says "no CSS files". `formatDate.js:3-4` `from 'intl'` / `'intl-datetimeformat'` — not dependencies; `Intl` is a global |
| D6 | Model/schema redefinition inside a router | 1 | backend | F | **PROMPT** (explicitly forbidden) | `routers/notes.py:14-34` redefines `NoteCreate/Update/Response`, shadowing the line-11 imports. Prompt l.16: "Do NOT redefine models in a router" |
| D7 | Response-shape contract mismatch across the stack | 2 | both | F | **ARCHITECTURE** (upstream) | `schemas/note.py:20` exposes scalar `tag_id`; `NoteCard.jsx:31` iterates `note.tags` as an array. `TagFilter.jsx:50` renders `/tags` rows as strings; `TagResponse` is an object → "Objects are not valid as a React child" |
| D8 | Broken ORM relationship wiring (missing FK) | 2 | database | F (whole app) | PROMPT (database agent) | `models/note.py:16` `tag_id` is plain `Integer` with no `ForeignKey`, but `:20` declares a `relationship()` → `NoForeignKeysError` at mapper config. Architecture SQL *does* declare the FK (§3.4) |
| D9 | Duplicate side effect (double network call) | 2 | frontend | F | PROMPT (no ownership rule) | `NoteCard.jsx:13-17` issues its own DELETE *and* calls `onDelete` → `useNotes.deleteNote` fires a second one → 404 → spurious error |
| D10 | Stale/uncleared React state | 2 | frontend | F | PROMPT | `useNotes.js:35,44` set `error`, nothing clears it on later success → error screen becomes permanent. `day18-3file/App.jsx:34` stale-closure `setNotes(notes.filter(...))` |
| D11 | Pydantic v1/v2 and PEP-604 idiom drift | 2 | backend | C | PROMPT (rule exists) | `models/note.py:16,20` use `Mapped[int \| None]`; prompt l.19 says prefer `Optional[X]`. (The v1 `class Config`/`from_orm` cases were fixed on Day 19 and have not recurred) |
| D12 | Duplicated source of truth / dead branch | 3 | both | C | PROMPT | `TagFilter.jsx:10` duplicates `useNotes.js:10` state; `day18-3file/App.jsx:49-86` renders `NoteForm` twice |
| D13 | Wrong/phantom relative import | 0 (Day 18+) | both | S | — resolved | Day 12: `from .invoice import Invoice` to a never-generated file; phantom `backend.app.database`. Structurally eliminated by the owned `database.py` + `app.` convention + `fix_imports`. **0 occurrences Days 18–20** |
| D14 | Markdown fences leaking into code files | 0 (Day 15+) | both | S | — resolved | Day 15 fence-cleanup pass; Day 18–20 checklists report 0 |
| D15 | Hallucinated endpoints | 0 (Day 18+) | frontend | F | — resolved | Day 18: 0 across 11 files; the API-section anchor works |
| D16 | Missing table creation / migration path | both trees | backend | F | TEMPLATE (not LLM) | No `Base.metadata.create_all`, no Alembic → fresh DB has zero tables. `backend_infra.render_*`, not model output |
| D17 | Junk files in the planned tree | 2 | frontend | C | **ARCHITECTURE** (upstream) | `frontend/src/pages/__init__.js`, `components/__init__.js` — a Python-ism leaked into a JS tree |

### Rate-limit / routing failure modes (infrastructure, not code quality)

Recorded for completeness; these are Day 12–20 environmental findings, already fixed
or documented, and are **not** in scope for prompt tuning.

| Class | Days | Status |
|---|---|---|
| OpenRouter free model slug retired without notice | 12, 18, 19 | Fixed each time; `MODELS` must be checked against the live `/api/v1/models` list |
| `qwen3-coder:free` per-model 429 on every attempt | 18, 19, 20 | Every coder call falls through to the groq fallback |
| Cohere free model returns empty responses | 17, 18 | Routed away from |
| Groq per-minute token cap mid-batch | 12 | Produced D13's phantom import as a knock-on |
| Gemini free tier is 20 requests/**day**/model | 25 | Inherent; documented in `docs/INTEGRATION_RESULTS.md`, not fixable |
| Groq 12k TPM cannot serve planning's ~26.9k-token call at all | 25 | Inherent per-provider; planning has a hard single-provider dependency |
| QA primary `nemotron:free` returns an upstream error | 25 | Same delisting mode as rows 1–3; QA cannot run when Gemini is also exhausted |

### D14 — tool-generated defects misattributed to the model (Day 25)

**New class, and the only one Day 25 added.** It belongs here rather than in the
table above because it is not environmental — it is our code manufacturing
defects and filing them under the coder's name.

The failure placeholder written for a failed file embedded a multi-line provider
error in a single-line comment, so every failed file was syntactically invalid.
On the Day 25 simple run that was **17 of 96 files** presenting as coder syntax
defects when the coder had never been reached. Fixed in `ac3ba89`.

**Why it matters to this document specifically:** the attribution procedure in
§1 starts from observed defects in generated files. This class corrupts that
input — it would have supported a confident, well-evidenced, entirely wrong
conclusion that the coder emits broken syntax under load, and the fix would have
gone into a prompt. Before attributing any syntax-class defect, confirm the file
was actually generated rather than stubbed.

No new *coder-level* defect classes emerged on Day 25. That is not evidence the
taxonomy is complete: provider exhaustion meant the coders produced only 26
files, far too few to surface new classes.

---

## 3. Forensic attribution — the top 5 classes

### 3.1 D2 — prop/interface mismatch — **overturns the Day 18 hypothesis**

`build-journal/failures.md` Day 18 recorded this root cause:

> the dependency summary injected into `NotesPage` shows `export default function NoteForm`
> but NOT its prop names (props are function parameters, not exports), so the consumer
> can't see the contract

**This is incorrect.** Re-running the real extractor against the real generated files:

```
components/NoteForm.jsx  len=2310  -> EXPORTS-SUMMARY
   what the consumer actually saw:
     | export default function NoteForm({ onCreate }) {
components/Header.jsx    len=448   -> FULL (below the 800-char threshold)
```

`extract_exports` keeps the whole signature line, destructured props included. And
`Header.jsx` at 448 chars fell under `FULL_DEP_THRESHOLD` and was injected **whole**.
So both `onCreate` and `noteCount` were verbatim in `NotesPage`'s context, and the
context was far under budget (no trims). Step 2 of the procedure → **PROMPT layer**.

Acting on the Day 18 note would have meant rewriting the extractor to capture
destructured props — work that is **already done**, for a defect it does not cause.
That is the whole day's budget spent on a no-op.

**But** the same class has a second cause in production. The planner's dependency
wiring is wildly variable run to run:

| Run | frontend tasks with non-empty `requires` |
|---|---|
| NotesTags (`113cf67c`) | 3 / 17 (**17%**) — `pages/Home.jsx` has `requires=[]` |
| FreelanceInvoicer (`f1a063f8`) | 21 / 24 (**87%**) |

When `requires` is empty the frontend builder injects **no dependency block at all**,
so the truth is absent and no prompt rule can recover it → **PLANNER layer**. The Day
18 hand-written fixture wired its deps properly and therefore *masked* this.

Both causes are real. The prompt cause is measurable on a fixture today; the planner
cause needs a context-builder change (see the ranked list).

### 3.2 D5 — nonexistent imports — PROMPT

`NoteCard.jsx:6` imports `./NoteCard.css`. The prompt's rule is unambiguous
(l.8: *"Use TailwindCSS utility classes for ALL styling — no CSS files"*), the folder
map was in context, and no trim occurred. The model saw the rule and the map, and
violated both → **PROMPT**. `formatDate.js` importing `'intl'` is the same layer via
a different route: no rule covers "don't import a package that isn't a dependency",
so it is a prompt *gap* rather than a prompt *violation*.

### 3.3 D6 — model redefinition in a router — PROMPT

The backend builder injects the resource's schema **full body** for routers
(`_build_backend_context`, `dep_paths[p] = True`), and `schemas/note.py` is small
enough that no truncation applied. The router had the real `NoteCreate` in front of
it and redefined it anyway, against an explicit prohibition → **PROMPT**.

### 3.4 D8 — missing ForeignKey — PROMPT (database agent), *not* upstream

The natural assumption is that the architecture never specified the FK. It did:

```
FK mentions in the architecture SQL section: 8
NOT NULL tokens: 17    DEFAULT tokens: 6
```

`CREATE TABLE note_tags (... FOREIGN KEY (note_id) REFERENCES notes (id), ...)` is
present verbatim. Step 3 → truth present in the architecture → not upstream. The SQL
section is injected for `kind in ("model","schema")`, so it reached the generator →
**PROMPT layer, database agent**. Out of scope for the two coder prompts, logged for
a later day.

### 3.5 D7 — response-shape mismatch — **ARCHITECTURE (upstream)**

The real API endpoints table is:

```
| Endpoint | Method | Description |
| /api/notes | GET | Get all notes for the current user |
| /api/tags  | GET | Get all tags |
```

Three columns. **No response shape, no auth column, no example payload.** So when the
frontend needs to know whether `/tags` returns `["work"]` or `[{id,name}]`, the truth
does not exist anywhere upstream — each coder invents a shape independently and they
disagree. Step 3 → absent from the architecture doc → **ARCHITECTURE**. This is the
Task 5 lever, and it is the only top-5 class that a coder-prompt edit cannot fix.

Note the architecture prompt's existing specificity rules **are** working: the doc
contains `0` occurrences of `...` and `0` of `[more`. The folder-tree rule needs no
strengthening — the *endpoints table* does. Evidence beat the assumption here.

---

## 4. Ranked fix list

Ranked by (severity × frequency × attribution confidence × measurability today).
Each names the layer it will be fixed at and the fixture that will prove it.

### Fix #1 — D5: unresolvable imports (breaks-startup)

- **Layer**: PROMPT (frontend coder). One violated rule, one gap.
- **Change**: a single import-resolution rule with a *counter-example* — the existing
  prompt states the CSS rule positively and it is still violated, so state the wrong
  form explicitly alongside the right one.
- **Criterion**: every non-`react` import either resolves to a path in the folder map
  or is on the tech-stack dependency allow-list. Fully automatable, binary.
- **Fixtures**: `NoteCard.jsx` (violates the CSS rule), `formatDate.js` (phantom package).
- **Why first**: breaks-startup, crisply measurable, and attribution is unambiguous.

### Fix #2 — D2: prop/interface mismatch (breaks-feature, highest impact)

- **Layer**: **CONTEXT BUILDER**, not prompt. The prompt cause is real but secondary;
  the planner cause is the one a prompt cannot reach, and it fires on 83% of tasks in
  the NotesTags run.
- **Change**: apply the pattern the **backend** builder already uses — discover sibling
  dependencies from `file_list` instead of trusting `requires`
  (`_build_backend_context` already does exactly this: *"cross-file truth, not trust"*).
  Reusing the existing in-repo pattern rather than inventing one.
- **Criterion**: every prop a consumer passes appears in the producer's destructured
  signature. Automatable by regex over the generated pair.
- **Fixtures**: a page + its child components, frozen from the **NotesTags** checkpoint
  (the 17%-wired run) so the planner gap is actually represented.
- **Why second**: highest impact, but the fix is a code change whose A/B needs a fixture
  built from real low-wiring state.

### Fix #3 — D6 + D11: backend rule-adherence drift (breaks-feature)

- **Layer**: PROMPT (backend coder). Both rules exist and are ignored.
- **Change**: one counter-example block showing the forbidden shapes (redefined schema
  class in a router; `Mapped[int | None]`) against the correct ones.
- **Criterion**: no `class .*BaseModel` definition in a router file; no `|`-union in an
  annotation; `py_compile` clean.
- **Fixtures**: `routers/notes.py` (redefinition), `models/note.py` (union syntax).

### Queued for Task 5 — upstream (architecture agent)

- **D7**: the API endpoints table carries no response shape → both stacks invent one.
  Fix: require a response-shape column with an example JSON body per row, backed by a
  mechanical validator.
- **D17**: `__init__.js` junk in a JS tree.
- Explicitly **not** changing: the folder-tree specificity rules. Measured `0` shortcuts
  in real output — they already work.

### Logged, not fixed today

- **D8** (missing FK) — database agent prompt, a different agent than today's scope.
- **D16** (no `create_all`/Alembic) — `backend_infra` template, not a prompt at all.
- **D1** (unused imports) — highest frequency but cosmetic; a linter is the right tool,
  not prompt tokens.

---

## 5. Fixture-staleness note

The fixtures frozen today embed **today's** architecture style — in particular the
three-column endpoints table that causes D7. When Task 5 sharpens the architecture
prompt, these fixtures will be measuring against a world that no longer exists.
Re-freeze from a fresh run before the next tuning session.

---

## 6. Outcomes (Day 21)

What the ranked fix list above actually produced. Full hypotheses, tables, and
verdicts are in `PROMPT_CHANGELOG.md`; this is the index.

| Fix | Layer | Change | Target criterion | Result | Verdict |
|---|---|---|---|---|---|
| #1 D5 | prompt | import-resolution rule + counter-examples | `imports_resolve` | 50% → **83%** | **KEEP** |
| #2 D2 | context builder | sibling component interfaces for consumer files | `props_complete` | 0% → **100%** | **KEEP** |
| #3 D6/D11 | prompt | router/schema separation block | `no_schema_redefinition` | 100% → 100% | **REVERT** |
| Task 5 D7 | architecture | response shapes + component props + validators | rows with `Response` | 0/21 → **16/16** | **KEEP** |

Cost: **0 OpenRouter calls** of a 30 budget. 33 calls to groq/gemini.

### Corrections this day made to the taxonomy above

- **D5 is broader than recorded.** The CSS-import violation did not reproduce in
  6 fresh samples; the recurring defect is *phantom date libraries* — variant A
  invented `luxon` twice and `date-fns` once. `intl` was one instance of a class.
- **D6 is rarer than its ranking implied.** It appeared in exactly one file and
  reproduced zero times in 6 samples. It was ranked #3 because it was an
  *unambiguous* rule violation, which made it feel like a clean win. **Rule-
  violation clarity is not defect frequency** — rank by measured frequency.
- **D2's Day 18 root cause was wrong** (§3.1) and its fix was a context change,
  not a prompt change.

### Method lessons worth more than the individual fixes

1. **A criterion that only detects a *wrong* value will reward an *absent* one.**
   `props_match` passed a page that rendered `<Header />` with no props at all.
   Pair every "not wrong" check with a "present and complete" check.
2. **A specificity rule and its token ceiling ship together.** The sharper
   architecture prompt truncated at the old `max_tokens` and produced *worse*
   output than the vague one until the ceiling was raised.
3. **Negative examples can be echoed.** Naming a forbidden package teaches its
   name; one sample imported the exact two package names from the WRONG block.
4. **Save raw outputs, not just pass/fail counts.** Fix #2's first verdict was
   wrong; correcting it cost zero API calls only because the samples were on disk.

### Carried forward

- **D8** (missing `ForeignKey`) — database-agent prompt; truth *is* in the
  architecture SQL, so it is a prompt fix, not an upstream one.
- **D16** (no `create_all`/Alembic) — `backend_infra` template; no generated
  backend has served a request against a real table.
- **D1** (unused imports, 8 files) — highest frequency, cosmetic; a linter, not
  prompt tokens.
- **Planner dependency wiring** (17% vs 87% between runs) — the context-builder
  fix routes around it for consumers, but the planner itself is still unreliable.
- **Re-freeze fixtures** from a run using the new architecture before the next
  tuning session (§5).
- **Groq daily token cap** (100k TPD, ~3.3k per sample → ~30 samples/day) is the
  real binding budget constraint, not OpenRouter.
