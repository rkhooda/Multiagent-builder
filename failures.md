# Day 12 — Full Pipeline Test: Observed Failures

Two end-to-end runs were made against the brief: *"A web app that helps freelancers track
billable hours, generate invoices, and get paid via Stripe."*

- **Run 1** (`4835768d-a56f-43c1-a0ba-602e3d848586`) — abandoned mid-architecture-stage after
  observing the fallback model was also getting rate-limited. Backend was restarted with a
  fix before continuing.
- **Run 2** (`f1a063f8-f61f-4cbb-86c4-a825640ae552`) — ran to completion, all 4 gates approved.
  Started 23:24, gate 1 (research) 23:29, gate 2 (architecture) 23:34, gate 3 (planning) 23:44,
  pipeline_complete ~00:11. **Total wall-clock: ~47 minutes.**

No fixes were made to pipeline/agent logic during the run itself, per the day's instructions.
Two infra-level model routing fixes *were* applied (documented below) since they affect whether
today's QA agent functions at all, not pipeline logic.

## Critical

### 1. QA agent's configured primary model does not exist on OpenRouter's free tier
- **Where**: `backend/app/llm_router.py`, `MODELS["qa"]`
- **Error**: `litellm.NotFoundError: ... "This model is unavailable for free. The paid version
  is available now - use this slug instead: deepseek/deepseek-r1"` (HTTP 404), on every single
  QA batch call, all 3 retry attempts, both batches.
- **What happened**: The original mapping (`openrouter/deepseek/deepseek-r1:free`) was dead —
  OpenRouter has fully retired the free DeepSeek R1 tier. Queried the live OpenRouter models API
  directly (`GET /api/v1/models`) and confirmed there is no `:free` DeepSeek variant of any kind
  left, R1 or otherwise. Every QA batch silently fell through to the `gemini/gemini-2.5-flash`
  fallback — the QA agent never actually used a reasoning model in either run, despite the
  brief's premise.
- **Fix applied**: swapped the primary to `openrouter/nvidia/nemotron-3-nano-omni-30b-a3b-reasoning:free`,
  the only free model on OpenRouter currently tagged as a reasoning model. Verified with a
  standalone `call_llm` test call before finalizing — it responds correctly and quickly.
  Fallback stays `gemini/gemini-2.5-flash`.
- **Severity**: Critical — the intended "slow reasoning model for code review" design point
  was completely non-functional both runs. Not caught by any error path because the fallback
  succeeded silently.

### 2. `frontend_code` and `backend_code` pipeline nodes are stubs
- **Where**: `backend/app/graph/pipeline.py` (pre-existing, not touched today)
- **What happened**: These two nodes just append `"frontend_code ran"` / `"backend_code ran"`
  to the log and return — no LLM call, no files written. The planning agent produced 53 of the
  64 planned files under these two phases (29 backend, 24 frontend) and none of them were ever
  generated.
- **Impact**: Of 64 planned files, only 13 were actually written (6 database + 7 devops). This
  is the single biggest gap between "brief" and "generated project" today.
- **Severity**: Critical for project completeness, but expected — the brief explicitly scoped
  real implementations to research/requirements/architecture/planning/database/devops/qa only.
  Backend/frontend coder agents are Day 13+ work.

## Warnings

### 3. Database agent hit Groq's per-minute token rate limit mid-batch
- **Where**: `database_agent.py`, generating `backend/app/models/invoice.py`
- **Error**: `GroqException ... Rate limit reached for model llama-3.3-70b-versatile ...
  tokens per minute (TPM): Limit 12000` — hit on both the primary (Groq) and the fallback
  (`openrouter/qwen/qwen3-coder:free`, itself rate-limited) after 3 retries each.
- **Effect**: `invoice.py` was never generated. Because it wasn't generated, `project.py`
  (which does `from .invoice import Invoice`) now has a broken import — a real runtime bug
  caused directly by this failure. `agent.errors` correctly recorded the failure and the stage
  moved on rather than crashing (the resilience design worked as intended).
- **Severity**: Warning at the infra level, but produces a **Critical**, silently-passed-through
  runtime bug in the generated code (see Generation Metrics below).

### 4. Architecture agent's fallback model was itself rate-limited (Run 1 only, fixed for Run 2)
- **Where**: `backend/app/llm_router.py`, `MODELS["architecture"]`
- **What happened**: Original mapping was
  `("openrouter/qwen/qwen3-coder:free", "openrouter/nvidia/nemotron-3-super-120b-a12b:free")`.
  In Run 1, the primary hit 429s repeatedly, fell back to nemotron, which *also* hit 429s
  repeatedly — the architecture stage stalled for several minutes cycling through retries on
  both models with no forward progress possible until whichever provider's limit reset.
- **Fix applied**: swapped the fallback to `groq/llama-3.3-70b-versatile`, a model already
  proven reliable elsewhere in the pipeline (research/planning/devops). In Run 2, the primary
  still hit 3x 429s (expected — free tier is just congested) but the fallback then succeeded in
  one shot, no stall.
- **Severity**: Warning — was a genuine stall risk, now mitigated. Left the primary as-is since
  it does eventually work when OpenRouter isn't congested.

### 5. QA agent's issue-parsing regex misses issues when the model drops the `[SEVERITY]` bracket format
- **Where**: `backend/app/agents/qa_agent.py`, `_parse_issues` / `ISSUE_LINE_RE`
- **What happened**: Batch 1 of the QA review asked the model to emit
  `N. [SEVERITY][TRIVIAL] filepath:line - description`. The model (routed through the Gemini
  fallback, see #1) instead wrote plain `1. CRITICAL backend/app/utils/db_utils.py:8 - ...`
  (no brackets). The regex didn't match, so the fallback path fired: the entire batch's raw
  text — including two genuine CRITICAL findings about `db_utils.py` (`DATABASE_URL` not
  null-checked, no error handling around `create_engine`) — got downgraded to a single
  unclassified **Warning** with the raw text dumped as its description.
- **Effect**: `qa_issues_count` undercounts (2 instead of the true ≥4), and two real Critical
  issues never surfaced as Critical in the Gate 4 UI badge.
- **Severity**: Warning — the report still contains the information (nothing was silently
  dropped, per the fallback design), but severity classification degrades when the model
  doesn't follow formatting instructions exactly. Left unfixed today per the "don't fix
  pipeline bugs found during the run" instruction — this doesn't prevent the QA agent from
  functioning, it degrades report quality. Worth a stricter parser (e.g. tolerate missing
  brackets) or a structured-output/JSON mode on Day 13+.

### 6. QA agent runs before devops in the pipeline — devops-generated files are never reviewed
- **Where**: `pipeline.py` edge order: `database → qa → devops → human_gate_4`
- **What happened**: This ordering (pre-existing, not changed today) means the QA agent can
  only ever see files that exist *before* it runs. In this run that was the 6 database files —
  the 7 devops files (Dockerfile, docker-compose.yml, CI workflow, etc.) were generated
  afterward and are outside QA's reach entirely.
- **Severity**: Warning / design gap. Given devops output includes deploy-relevant files
  (Dockerfile, docker-compose.yml) this seems worth reordering on Day 13+, but wasn't in
  today's scope.

### 7. Planning agent needed a repair pass to produce valid JSON
- **Where**: `planning_agent.py`
- **Log**: `planning_agent: validation failed, repair attempt 1: ['No JSON array found in LLM
  response']` — recovered on the retry, plan came out correct (64 tasks).
- **Severity**: Warning — self-corrected, but worth tracking frequency over more runs since it
  indicates the planning LLM doesn't reliably follow the JSON-array output contract on the
  first attempt.

## Info

### 8. Research agent needed one quality-check retry
- **Log**: `research_agent: quality check failed — 1 issues, retrying` → `research_agent: retry
  complete — 16078 chars`. Self-corrected on the built-in retry path; no user-visible impact.
  Included for completeness since it's part of the day's full observation record.

### 9. Web search returned no results for the research phase
- **Log**: `research_agent: web search returned no results, proceeding on training knowledge`
  (both runs). Research agent fell back to model training knowledge rather than live search.
  Not necessarily wrong, but means "research" is really "the model's prior knowledge" here —
  worth checking whether the search integration itself is broken vs. just returning nothing for
  this particular brief.

### 10. `docker-compose.yml`'s Postgres env vars use invalid shell-style parameter expansion
- **Where**: generated `docker-compose.yml`, `database` service:
  ```yaml
  - POSTGRES_DB=${DATABASE_URL##*:}
  - POSTGRES_USER=${DATABASE_URL%%:*}
  - POSTGRES_PASSWORD=${DATABASE_URL#*://}
  ```
- **What happened**: docker-compose does not support bash parameter-expansion operators
  (`##`, `%%`, `#`) inside `environment:` values — these are passed through literally, not
  evaluated. All three of these values will be wrong at container start.
- **Severity**: Info-level for the backlog (devops agent output quality issue), but would break
  `docker compose up` if someone tried to actually run this project as generated. The QA agent
  never saw this file (see #6), so it's undocumented in the QA report itself.

## Generation Metrics

**Files generated**: 13 / 64 planned (20%)
- Database: 6/7 (1 failed — `invoice.py`, Groq TPM rate limit, see #3)
- Backend (excl. database): 0/29 (`backend_code` node is a stub, see #2)
- Frontend: 0/24 (`frontend_code` node is a stub, see #2)
- DevOps: 7/7

**Spot-check of 5 generated files**:
| File | Assessment |
|---|---|
| `backend/app/models/user.py` | Plausible SQLAlchemy 2.0 model, sane imports, clean. |
| `backend/app/models/project.py` | Imports `from .invoice import Invoice` — **broken**, `invoice.py` was never generated (cascading failure from #3). Otherwise well-formed. |
| `backend/app/utils/db_utils.py` | `from sqlalchemy.ext.declarative import DeclarativeBase` — **wrong import path** for SQLAlchemy 2.0 (should be `sqlalchemy.orm`), and `DeclarativeBase()` is instantiated directly rather than subclassed (`class Base(DeclarativeBase): pass`). Would raise on import. QA agent's own report on this exact file flagged only the missing-null-check issue, missing this bug entirely. |
| `Dockerfile` | Clean, sensible multi-stage-ish setup, non-root user, correct CMD. Plausible as-is. |
| `docker-compose.yml` | Structurally sound but the Postgres env-var expansion is broken (#10); also references a `frontend/nginx.conf`-served frontend on port 80 without matching the backend's actual generated routes (can't fully verify — routes/backend never generated). |

**Estimated % of files needing manual edits**: at minimum 2 of the 6 database files have
concrete bugs found in a 5-file spot-check (40% of database output), plus 1 devops file (out of
7) with a real config bug — call it **roughly 30-40% of what was actually generated** needs a
manual fix before it would run, before even counting the 51 files (80% of the total plan) that
were never generated at all and would need to be written from scratch.

**Wall-clock timing** (Run 2, from gate-approval timestamps):
| Stage | Time |
|---|---|
| Start → Gate 1 (research) | 23:24 → 23:29 (~5 min) |
| Gate 1 → Gate 2 (requirements + architecture) | 23:29 → 23:34 (~5 min) |
| Gate 2 → Gate 3 (planning) | 23:34 → 23:44 (~10 min) |
| Gate 3 → pipeline_complete (database + qa + devops) | 23:44 → ~00:11 (~27 min) |
| **Total** | **~47 minutes** |

The final stretch (code-gen + QA + devops) dominated the runtime despite generating the fewest
files of any stage that actually ran, almost entirely due to rate-limit retry backoff (Groq TPM
limit, OpenRouter 429s) rather than raw generation time. Free-tier rate limits are the dominant
cost in this pipeline's wall-clock time, not model latency itself.

## QA Agent (today's deliverable) — verification notes

- `qa_batch_complete` and `agent_complete` WebSocket events both fired correctly for each batch
  (verified via `manager.broadcast_sync` calls and the resulting `qa_report`/`qa_issues_count`
  fields on the project state after gate 4).
- Batching worked correctly: 6 reviewable files → 2 batches of 3, as designed.
- Per-batch resilience worked correctly: every single QA call in this run hit the dead
  DeepSeek R1 slug 3x before falling back (see Critical #1) — the batch never failed outright,
  it degraded gracefully to the fallback model on every call, exactly as designed.
- Trivial auto-fix path did not trigger in this run (0 issues were tagged `[TRIVIAL]` by the
  model) — untested in practice today; the code path itself was exercised in unit-style manual
  testing (see commit history) but not against a live trivial issue.
- Gate 4 UI renders `qa_report` as markdown with an issue-count badge — visually verified via
  `npx vite build` (clean) and by reading back the final project state's `qa_report` field.
- See Warning #5 for the one real quality gap found: bracket-format parsing is brittle against
  models that don't follow the exact requested format.

## Day 13 observations

Scope: replace the stub approval modal with rich Gate 1 (research + requirements) and Gate 2
(architecture) approval UIs, backed by real conditional routing in LangGraph (approve / edit /
reject). Task 0 triage found no Day 12 blockers — research/requirements/architecture never
crashed in either prior run, so no fixes were needed before starting.

### Pipeline restructure
The gate boundaries were moved to match the UI spec: previously `human_gate_1` sat between
`research` and `requirements` (reviewing research only), and `human_gate_2` sat between
`requirements` and `architecture` (reviewing requirements only). Today's brief wanted Gate 1 to
review research + requirements together, and Gate 2 to review architecture alone. Re-wired to
`research -> requirements -> human_gate_1 -> architecture -> human_gate_2 -> planning`. Verified
against a live run (project `0bef33f9-847b-4ec5-b025-295c89ec3f3d`) that both `research_report`
and `requirements_doc` are populated by the time gate 1 pauses.

### Conditional routing + feedback loop — verified live, all paths work
- **Reject**: gate 1 reject on project `bf4087c7-bbde-449a-a68a-1e5623666865` routed to a new
  `cancelled` node -> `END`; final project status correctly became `cancelled` (not
  `completed`), confirmed via `GET /api/projects/{id}`.
- **Edit**: submitting `{"decision":"edit","feedback":"Add a requirement for dark mode support"}`
  at gate 1 re-ran `requirements_agent` with the old doc + feedback injected, the graph looped
  back and re-paused at `human_gate_1` again, `previous_versions.requirements_doc` captured the
  exact old doc (5466 chars, byte-for-byte length match), and `human_feedback`/`human_decision`
  were both cleared to `""` afterward — confirmed the model addressed the feedback (mentions
  "dark"/"theme" in the regenerated doc, just not the literal phrase "dark mode").
- **Direct edit (PATCH)**: `PATCH /api/projects/{id}/state` with
  `{"field":"requirements_doc","content":"PATCHED TEST CONTENT"}` overwrote the field in the
  paused graph state instantly, no LLM call. Approving afterward correctly fed the patched
  (deliberately garbage) text into `architecture_agent` — no `requirements_doc is empty` warning
  appeared in the log, proving the edited value — not a stale cached one — is what flows
  downstream. (Architecture output was generic/low-quality as a direct result of the garbage
  test input; that's expected test fallout, not a bug.)

### Frontend
`Gate1Approval.jsx` / `Gate2Approval.jsx` render full-width (not the old cramped 35% sidebar
modal) when the project is awaiting approval at gate 1/2. Verified visually via a live browser
check (Playwright) against project `0bef33f9-847b-4ec5-b025-295c89ec3f3d` at gate 2: folder tree,
API endpoints markdown table, Mermaid ER diagram (rendered as an actual SVG showing
USER/TODO/NOTIFICATION with relationship labels), collapsible SQL block, and the Security Notes
markdown list all rendered correctly with zero console errors (one pre-existing benign
StrictMode double-mount WebSocket warning, unrelated to today's work).

### Known gaps / left for later
- The architecture markdown section parser (`splitArchitectureSections` in `Gate2Approval.jsx`)
  splits on `## ` headings and keyword-matches section names (`folder`, `api`, `database`,
  `security`). It has a whole-document Mermaid-aware fallback if parsing fails, but this hasn't
  been exercised against a real parse failure yet — only the happy path was observed live today.
- Gate 3 (planning) and Gate 4 (QA/devops) still use the older `ApprovalGate.jsx` component
  as-is; only gates 1 and 2 were in scope for today.
- Gate 1's inline-edit + diff-view interaction wasn't visually verified in the browser today
  (only backend PATCH + curl-level checks) — worth a follow-up visual pass before considering the
  editing UX fully done.

**Update**: Gate 1 was visually verified live (project `14e1209b-a8c1-48f9-a962-300534a8e093`,
Playwright browser check) — two-panel layout renders correctly, tech stack card shows as a clean
grid (not raw JSON), sticky action bar visible, and the inline Edit toggle works: clicking
"✏️ Edit" swaps the requirements panel to a pre-filled textarea with Save/Discard, and Discard
correctly reverts to the rendered markdown view with 0 console errors. The remaining unverified
item is the diff view specifically (red/green line rendering) — not exercised in a live browser
today, only unit-level confidence from the `diff` package's `diffLines` API and code review.

## Day 14 observations

Scope: Gate 3 turned into a full interactive plan editor (include/exclude, inline edit, add
custom task, hard remove, scope summary + time estimate, folder preview) with new backend plan
validation and gate 3 conditional routing (approve / edit-replan / back-to-architecture /
reject). Verified end-to-end against a live run (project `113cf67c-8c63-4713-bcc8-a5dd34e0b9d9`,
brief "A simple notes app with tags").

### Critical (found and fixed during the run)

#### 1. Feedback-injection replan blew past Groq's per-request token cap
- **Where**: `planning_agent.py` edit-rerun path (new Day 14 code)
- **Error**: `GroqException ... Request too large for model llama-3.3-70b-versatile ... (TPM):
  Limit 12000, Requested 28905` — all 3 retries on primary AND fallback failed; the replan
  errored out entirely.
- **Root cause**: the feedback prompt injected the full previous plan verbatim — a 61-task plan
  serialized with `indent=2` is ~28k chars. Unlike a congestion 429 this is a *permanent*
  request-size failure; retry/backoff can never succeed.
- **Fix applied**: compact the previous plan (`separators=(',',':')`) and pass it through
  `truncate_for_context(max_chars=8000)` before injection. Replan then succeeded first try.
- **Lesson**: "previous output" feedback injection must be size-bounded for any agent whose
  output scales with project size. The architecture agent gets away with full injection only
  because its docs are ~6k chars.

### Verification walkthroughs (all passed)

1. **Editor at gate 3**: 61 tasks grouped by phase with counts; summary bar live-updates
   (tasks/phase counts/excluded/complexity chips/est. minutes) on every check/uncheck.
2. **Dependency integrity**: unchecking `fe_004` surfaced "⚠️ 1 included task depends on this:
   fe_007", disabled Approve, showed the cannot-approve banner; "Exclude dependents too"
   cascaded transitively and re-enabled Approve. Same check fires on hard remove (`dv_005`
   remove was blocked until "Remove & exclude dependents" confirmed, which also excluded
   `dv_006`).
3. **Edit/add/remove**: inline description edit persisted through PATCH (marker text verified
   in checkpoint state); custom task got the next client-generated id (`db_006`), a `custom:
   true` flag, and a Custom badge; removed tasks disappear from the plan and land in
   `excluded_tasks` alongside unchecked ones (37 archived total).
4. **Replan with feedback**: planning re-ran with feedback, gate 3 re-fired with a fresh plan,
   old plan snapshotted into `previous_versions.implementation_plan` (28,161 chars),
   decision/feedback cleared, `excluded_tasks` reset.
5. **Back to architecture**: `back` decision re-ran architecture with feedback ("background job
   worker for email reminders"), then flowed straight to planning (gate 2 correctly skipped via
   `replan_after_architecture`), gate 3 re-fired with a 71-task plan containing worker/reminder
   tasks; the two-stage overlay showed "Regenerating architecture…" and the re-fired gate showed
   the regenerated-architecture banner with a working DiffView (`+ reminders.py` visible).
6. **Validation via curl**: broken dependency, duplicate ids, schema violation, and non-JSON
   plans all returned 422 with specific error messages; bogus resume decisions return 400.
7. **Generation respects the edited plan**: after approving the edited 36-task plan, the
   database agent generated exactly the 4 kept models + the custom `backend/seed_data.py`;
   excluded `backend/models/__init__.py` and removed `backend/docker-compose.yml` were never
   generated. Pipeline ran to `completed` through gate 4.

### Warnings / design gaps

#### 2. DevOps agent ignores the task plan entirely (pre-existing, by design)
- `devops_agent.py` generates a fixed `DEVOPS_FILES` set regardless of what the plan says, so
  excluding or removing devops-phase *plan tasks* has no effect on what devops generates.
  Plan-level devops edits are currently cosmetic. Worth either honoring the plan or hiding the
  devops phase's checkboxes at gate 3 once the coder agents are all real.

#### 3. Excluding a foundational task cascades very wide
- Excluding `backend/models/__init__.py` transitively excluded 35 of 72 tasks (correct
  behavior, since be/fe tasks chain off it). Fine for a power user, but the one-click cascade
  makes it easy to cut half the project without reading the list. A count is shown in the
  summary bar; an explicit confirm for cascades above ~10 tasks might be worth it later.

#### 4. Mid-flight overlay label transition not visually captured
- The two-stage back-navigation overlay was verified at stage 1 ("Regenerating architecture…")
  live in the browser; the flip to "Replanning…" is driven by the architecture
  `agent_complete` event and was verified by code path + the correct end state, but the exact
  moment of the label swap wasn't screenshotted. Cosmetic risk only.

## Day 15 observations

Full-pipeline test project: `341b1dc2-2ce7-4c79-a147-6ab45095e1fa` ("A simple notes app with
tags" → NotesTags Day15). Pipeline ran research → gate 4 cleanly in ~7.5 min elapsed;
13 files generated, 2 QA warnings.

### Issues found and fixed during the build

#### 1. State and disk already disagreed on file content (MEDIUM, pre-existing)
- Coder agents store the *raw* LLM output in `state["generated_files"]` while
  `write_project_file` strips fences before writing to disk — so the two sources of truth
  had diverged since Day 11 without anyone noticing (nothing read state content back until
  now). The new fence-cleanup pass at the end of the devops node normalizes state to the
  stripped content and rewrites any changed file; on the live run it cleaned 1 file that had
  slipped through with fences intact in state.

#### 2. Free-tier quota exhaustion blocks per-file fixes right after a pipeline run (LOW, environmental)
- A full pipeline run consumes enough Groq tokens that an immediate Request-AI-Fix on a
  database-phase file hit `RateLimitError` on primary AND fallback (`retry in ~31m`). The
  endpoint handles it correctly — 502 with the litellm message, file untouched, fix count not
  incremented, error shown inline in the modal — but expect fixes to need a cooldown after a
  full run on free tiers.

### Verification walkthroughs (all passed)

1. **File browser**: tree renders all 13 files grouped by folder with type-colored dots,
   QA warning-count dots on the 2 flagged files, click loads syntax-highlighted content
   (PrismLight, 9 registered languages), shimmer while loading, instant re-click from cache.
2. **QA panel**: header badges (2 total / 0 crit / 2 warn / 0 info) match the report;
   findings grouped by severity; clicking a finding's file reference switches to the Files
   tab and opens that file. The tolerant parser handled the QA model's malformed
   "Unparsed QA output" descriptions without breaking.
3. **Download**: `NotesTags-Day15.zip` unzips to the exact folder structure planned at gate 3,
   README present, all files valid UTF-8. Fence audit: no file starts/ends with a fence and no
   non-markdown file contains one. (Note: the literal `grep -r '```'` check from the task flags
   README code blocks — those are legitimate paired markdown fences in setup instructions, not
   stripping failures.)
4. **Request Fix (error path)**: modal pre-lists the file's QA findings as chips, chip click
   appends to the instruction, dependents warning shown ("5 files import this one"),
   rate-limit failure surfaced inline with the file untouched.
5. **Request Fix (success path)**: verified after the quota cooldown — see addendum below.
6. **Path traversal**: `path=../../.env` and URL-encoded variants → 400; missing file → 404.
7. **Gate 4 routing**: approve → status `completed`, graph reaches END; reject wired through
   the same GATE_ROUTES map as gates 1–3 (allowed decisions now derive from that map, so the
   API rejects edit/back at gate 4 with a 400).

### Warnings / notes

- **Pipeline time stat includes human review time.** `generation_seconds` is derived from
  first→last checkpoint timestamps (works retroactively for old projects, no log format
  change), so it measures wall-clock including gate waits. Labeled "incl. reviews" in the UI.
- **Tree-row kebab menu skipped.** The header Request-AI-Fix button covers the flow since a
  file must be open to describe a fix; add the kebab if fix-from-tree becomes a real need.
