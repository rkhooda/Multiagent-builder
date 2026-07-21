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
5. **Request Fix (success path)**: verified after the quota cooldown — see addendum at the
   end of this section.
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

### Addendum (post-cooldown): Request Fix success path — PASS

Run against `14e1209b` (HabitTest, paused at gate 4), `backend/app/models/habit.py`,
database-agent route. Full chain verified: 200 with `fix_count 1/3`, requested change present
in the returned content, old content snapshotted into `previous_versions` (both in the
response for the immediate diff and persisted in state for the reload-seeded diff),
`fix_counts` persisted, `file_fix:` log line appended, gate still paused. The content
endpoint (disk) and a re-downloaded ZIP both serve the fixed version — state, disk, and ZIP
stayed identical. (The NotesTags Day15 project had been approved to `completed` before the
cooldown elapsed, which also confirmed walkthrough 7's approve→END→completed path on a live
click; the fix test moved to HabitTest since fixes are gate-4-only by design.)

## Day 16 observations

Scope: complete the back-navigation matrix (gate 1 → research, gate 2 → requirements) with a
single `invalidate_downstream` cascade helper, centralise feedback injection/snapshot/clearing
into `regeneration_target` + the `stage_node` wrapper, add `retry_counts` (soft cap 3 with
amber button warnings), `stage_history` attempt tracking, and the StageTimeline breadcrumb.
Test project: `af6eaa1f-fa3c-4f83-9d36-6b99c9edf7d7` ("A tiny bookmark manager with folders").

### Matrix results (curl + Playwright)

1. **Gate 1 edit** — PASS. Requirements regenerated (feedback about browser-import honoured),
   gate 1 re-fired, `previous_versions.requirements_doc` snapshotted, decision/feedback
   cleared, `retry_counts={'requirements': 1}`, history shows `(requirements, 2, edit, gate_1)`.
2. **Gate 1 back** — PASS. Research + requirements both regenerated (research now Indian-SME
   focused per feedback), gate 1 re-fired, BOTH snapshots present, history shows
   `(research, 2, back, gate_1)` and `(requirements, 3, back, gate_1)`.
3. **Gate 2 back** — PASS. Requirements + architecture regenerated, pipeline landed back at
   **gate 2, not gate 1** (skip_gate_1 conditional edge worked), all three doc snapshots
   present, requirements mention offline-first. Note: the fresh architecture doc did not
   literally use the word "offline" — downstream freshness is real (it was rebuilt from the
   new requirements) but the architecture model under-emphasised the new requirement.
4. **Gate 3 back (regression)** — DEFERRED to post-quota-cooldown (see below). The retrofit
   path is the same `invalidate_downstream` + `regeneration_target` code proven by tests 1–3,
   and the helper's stage maths are unit-checked, but the live run is still owed.
5. **Retry cap** — PASS (UI verified on project `0637908c` at gate 1 with injected counts,
   then reset). Both regenerate buttons render amber with ⚠️ at count ≥ 3; clicking shows the
   inline notice BEFORE any regeneration; Continue Anyway opens the feedback input; Edit
   Directly Instead jumps into the pre-filled inline requirements editor. 0 console errors.
6. **Cross-cycle integrity** — PARTIAL. Approve-through after test 3 ran architecture fresh
   from the offline-first requirements (84-file plan input), but planning died on free-tier
   quota (below), so "plan reflects offline-first" is still owed.
7. **Restart resume** — PASS (via a real failure rather than a staged kill). Planning errored
   mid-run leaving `next=('planning',)`; after a backend restart all state (docs, history,
   retry counts) survived from the checkpointer. This exposed a real gap — the resume endpoint
   only understood gate interruptions — fixed by the checkpoint-recovery branch
   (`resumed_from_checkpoint`), verified to restart streaming at the correct node.

### Environmental

- **Free-tier quota exhaustion mid-matrix (expected failure mode, now with a recovery path).**
  The regeneration-heavy matrix burned Gemini's free-tier request quota and ~86k of Groq's
  100k tokens/day; planning (a ~39k-token request for the 84-file architecture) failed on
  primary AND fallback (Groq retry window: ~6h). Exactly the failure documented on Days 12/15.
  Unlike before, the project is no longer stranded: `POST /resume` now restarts from the
  checkpoint once quota recovers.

### UI verification (Playwright, 0 console errors throughout)

- StageTimeline: correct done/active states mid-run (Research ✓ 2×, Requirements ✓ 4×,
  Architecture ✓ 2×, Planning pulsing), attempt badges, hover popover with per-attempt
  trigger + gate + time, click-to-diff opening the requirements diff, old projects without
  `stage_history` fall back to doc-presence detection with a "history unavailable" popover.
- Gate 1: Go Back to Research button, back-mode feedback label, required non-empty feedback.

### Owed after quota cooldown

- Live gate-3 back regression run, cross-cycle plan freshness check (test 6), and one full
  back-cycle driven from the browser (multi-stage overlay label flips + auto-opened dual
  diffs at the re-fired gate — code-path verified only).

## Day 17 sabotage results

Scope: error taxonomy + hardened `call_llm` (90s timeouts, auth fast-failover,
classified errors, FAULT_INJECTION hook), universal error boundary in
`stage_node`, skip policy (research/qa/devops), rate-limit chain with optional
Ollama tier-3 and 60s auto-retry (max 3 cycles), unified validation registry
with one-shot repair, `POST /recover` (retry/skip/cancel), and error-card UI.
All six sabotage scenarios were run against live backends with fault injection
(`FAULT_INJECTION=kind:target:count` in `llm_router.py`) on a throwaway
"one-page hello world" brief. **Every scenario ended in clean, recoverable
behavior; zero projects were left in a zombie `running` state.**

### 1. Auth failure — PASS (and found a real bug)
- `auth:gemini` injection: research failed over gemini→groq in ONE attempt
  (structured log shows a single `outcome:auth` then `outcome:ok`) — no retry
  burning on the bad key. Report generated normally.
- `auth:*` (all providers): chain exhausted instantly →
  `LLMAuthError("All providers rejected their API keys…")` → `error_paused`,
  `failure_context.error_type=auth`, red card data persisted.
- Keys "restored" (clean restart) → `POST /recover {retry}` → pipeline
  resumed **from the failed agent, not from scratch**: on the S1a project the
  9,287-char research report survived byte-identical while only requirements
  re-ran to gate 1.
- **Bonus real bug found and fixed**: Cohere (requirements fallback) returned
  an *empty* response; validation caught it, but the repair prompt echoed the
  empty string as an assistant message, which Cohere's API rejects with a 400
  ("must have non-empty content"). Fixed: empty responses are no longer echoed
  into repair messages (`fix(validation)` commit).

### 2. Forced 429 — PASS
- `429:*:4`: gemini 2 attempts (2s wait) → groq 2 attempts (10s wait) → no
  Ollama (correctly silent, probe returns None) → status `rate_limited`,
  `rate_limited` event broadcast with `retry_in: 60`, `cycle: 1/3`.
- Auto-retry fired at 60s, injection count was exhausted ("quota lifted"),
  research succeeded on the first clean call and the pipeline ran to gate 1.
  `failed_agent` cleared by the boundary on success.

### 3. Garbage output — PASS
- `garbage:research`: `call_llm` returned "ok" → validation failed (min length
  + missing sections) → ONE repair prompt → still "ok" → `LLMOutputError` →
  `error_paused` with `error_type=bad_output`.
- **Skip research (allowed)**: `[SKIPPED — …]` placeholder written via
  `update_state(as_node=research)`, `stage_history` got `trigger:'skipped'`,
  requirements proceeded **from the brief alone** and its output explicitly
  notes no research informed it. Gate 1 reached.
- **Skip architecture (refused)**: after approving gate 1 with
  `garbage:architecture` active, architecture paused the same way; skip
  returned HTTP 400 "'architecture' cannot be skipped — every downstream
  stage needs its output." Cancel then worked.
- UI pass (Playwright): red card rendered (agent name, friendly copy,
  collapsible Details, Retry/Skip/Cancel), Skip click drove the whole
  recovery; timeline showed Research ⊘ (grey slash) + Requirements ✓.

### 4. Timeout — PASS
- `timeout:research`: exactly 4 attempts logged (same-model retry, then
  fallback ×2) → `LLMTimeoutError` → `error_paused`, `error_type=timeout`,
  message "groq/… timed out after 90s".
- Also verified the 409 guard: `POST /recover` against a healthy
  `awaiting_approval` project → HTTP 409.

### 5. Our-code bug — PASS
- Temporary `raise KeyError("sabotage-s5")` in the research agent body →
  boundary classified it `agent_bug` with `recoverable: false` (no auto-retry
  attempted), full traceback in server logs via `[Boundary]`, red card data in
  state. Cancel worked; sabotage line reverted.

### 6. Restart resilience — PASS
- Backend killed while S1b sat in `error_paused` → after restart the project
  still reported `error_paused` + `failed_agent` + `failure_context` (SQLite
  status row + checkpointed state) → `POST /recover {retry}` resumed from the
  checkpoint and ran to gate 1.
- Caveat (accepted): a pending `rate_limited` auto-retry timer does NOT
  survive a restart — the countdown card still renders from persisted state
  and manual "Retry Now" works, which is the designed fallback.

### Notes / follow-ups
- Ollama tier-3 was verified only in its ABSENT path (probe silent, chain ends
  at tier 2) — the live path is Day 29's job.
- The two WebSocket "handshake timed out" console errors seen during UI
  testing were caused by hitting the page mid-backend-restart; the hook's
  2s auto-reconnect recovered. Pre-existing behavior, not Day 17 fallout.
- database/devops per-file inline retries were deliberately left as inner
  tolerance layers (per-file failures already degrade gracefully); the
  validation registry ships `min_code_lines` ready for the Day 18+ coder
  agents.

## Day 18 observations

Scope: rebuild the frontend coder from a stub into a real per-task generator —
hardened prompt with a few-shot example, focused per-file context builder
(context_builder.py), dependency-ordered sequential generation, shared write
pipeline (process_and_write_generated_file), and per-file failure isolation
integrated with the Day 17 error boundary. Tested directly against the frontend
phase (not the full pipeline — see Environmental note) on a "TagNotes" tag-notes
brief.

### Task 0 triage — no blockers
Day 17 sabotage passed all six scenarios with zero zombie `running` states, and
the one real bug (Cohere empty-echo in repair) was already fixed in 707d692.
Nothing blocked agent execution or recovery, so no triage fix was needed.

### Issues found and fixed during the build

#### 1. The whole frontend_code model chain was dead (CRITICAL, fixed)
- Primary `openrouter/qwen/qwen3-coder:free` is per-model rate-limited (free
  daily cap exhausted from testing) — every call in every test 429'd on both
  attempts. Fallback `openrouter/cohere/north-mini-code:free` returned EMPTY
  responses (the Day-17 Cohere empty-reply failure mode), so the first 3-file
  test came out 0/3 with "0 non-empty lines" validation failures.
- Verified against the live OpenRouter `/models` list: `deepseek/deepseek-chat:free`
  (the stale "intended" fallback) is retired; qwen3-coder and north-mini-code
  are present. Fixed by routing the fallback to `groq/llama-3.3-70b-versatile`
  — reliable, code-capable, on a different provider (no shared OpenRouter free
  limit), mirroring the architecture agent's Day-12 fix. Every generation in
  every subsequent test succeeded via this fallback.

#### 2. min_code_lines(10) too strict for minimal files (fixed)
- A correct `src/lib/api.js` is ~7 lines and failed the 10-line floor, forcing
  a needless repair. Lowered the coder floor to 5 — still catches empty/prose
  responses, permits legitimately small files.

### Generation metrics — before/after

**Before (Day 12 / Day 15):** `frontend_code` was a stub — **0 frontend files
generated** on either run (Day 12: 0/24 planned frontend files; Day 15 notes
app: frontend still stub). No imports, no components, nothing.

**After (Day 18), 11-file frontend phase (TagNotes):**
- **11/11 files generated**, 0 failed. Per-file context 418–633 tokens each
  (budget is ~4000) — the whole-architecture dump is gone.
- Topological order correct: `api, formatDate, EmptyState, Header` (no deps)
  first, then `NoteCard, NoteForm, NoteList, TagFilter, useNotes`, then
  `NotesPage` (10th, depends on 5), `App` (11th, depends on page) last.
- **Static checklist across all 11:** 0 markdown fences; **0 broken relative
  imports** (every `./`/`../` import resolves to a real generated file);
  **0 hallucinated endpoints** — only the `useNotes` hook calls the API
  (`/notes`, `/notes?tag=`, POST `/notes`, DELETE `/notes/{id}` — all from the
  architecture), every component correctly delegates; all 11 have a default
  export; loading/error states and optional chaining present throughout.

**Per-file pass/fail (would-run-without-manual-edit):** **9/11 fully correct.**
The 2 failures are prop-name mismatches at the page↔child seam:
- `NotesPage` passes `onCreated={createNote}` but `NoteForm` accepts `onCreate`.
- `NotesPage` passes `count={...}` but `Header` accepts `noteCount`.
Each component is individually correct and the app still *renders* (mismatched
props are just `undefined` → the create button silently no-ops and the header
count is blank); two one-line renames make it fully functional.

**Honest estimate: ~82% (9/11) would run without any manual edit; the app
renders end-to-end, with 2 features needing a one-line prop-rename to wire up.**
Versus the Day 12/15 baseline of **0 frontend files**, this is the day's proof
of value — and the remaining defect is a specific, fixable seam problem, not the
wrong-imports / hallucinated-endpoints / oversized-context mess of the minimal
implementation.

#### Root cause of the seam mismatches (input for Day 21 prompt tuning)
This is exactly the failure mode ponytail #2 flagged for the interface-summary
strategy: the dependency summary injected into `NotesPage` shows
`export default function NoteForm` but NOT its prop names (props are function
parameters, not exports), so the consumer can't see the contract and each model
picks its own prop names. Options for Day 21: have the summary extract the
destructured-prop signature of the default-export component (still regex, no
AST), or add an explicit "match the prop names used by the components you
import" instruction with an example. The regex export extractor stays dumb;
the fix belongs in what we extract, not in an AST parser.

### Fault-injection sanity (per-file isolation) — PASS
`FAULT_INJECTION=garbage:frontend_code:2` on the 3-file fixture forced `api.js`
to fail (garbage output failed validation on the initial call AND the one
repair). Result: `LLMOutputError` caught, a placeholder stub written to disk
(`export default {}` with a failure comment), the file kept in
`generated_files` (state == disk), and **the loop continued** — `NoteForm` and
`App` generated normally. Final: 2 ok / 1 failed (33% < 50% → stage completed,
did not halt), `partial_failures=['frontend/src/lib/api.js']`, honest
`errors` entry. `stage_node` folded the partial report into `stage_history`
(`trigger:'partial'`, `failed_files:[...]`, verified via unit call) without
leaking the transient key into persisted state. The stub is on disk so the
Gate-4 file browser shows it and Request-AI-Fix (which reads from disk) can
regenerate it — mechanism verified; a live Gate-4 fix in a running server was
not exercised today (quota).

### Environmental / notes
- **Ran the frontend phase directly, not through the full pipeline.**
  qwen3-coder:free (also the architecture agent's primary) is fully rate-limited,
  and a full research→planning run would burn Gemini/Groq quota that Day 19's
  backend coder needs. The direct-phase run exercises the real agent code
  (topological ordering, context builder, write pipeline, isolation) on a
  realistic 11-file plan; only the upstream doc-generation stages were stubbed
  with a hand-written architecture + plan fixture.
- **All 14 generation calls this session went to the groq fallback** — qwen3-
  coder:free returned 429 on every attempt. OpenRouter free coder capacity is
  effectively unavailable right now; Day 19 should expect the same and lean on
  groq/gemini.
- `>50%` stage-halt path raises a recoverable `LLMError` (not the
  non-recoverable `AgentError`), so a mass failure — usually transient rate
  limits — is retryable via `/recover` rather than dead-ended.

## Day 19 observations

Scope: rebuild the backend coder from the Day 12 stub into a real per-file
FastAPI generator on the Day 18 infrastructure — FastAPI/SQLAlchemy/Pydantic-v2
prompt with few-shot router + schema examples, structural + topological
generation order, cross-file context injecting FULL model/schema bodies, an
AST-based `fix_imports`, and deterministic infra files (config/database/main/
requirements). Resolved the database/backend ownership boundary and reordered
the graph. Tested directly against the backend phase (not the full pipeline —
same quota reason as Day 18), on a notes / notes+tags fixture.

### Task 0 triage — one shared-infra blocker fixed
`backend_code` still routed to `(cohere/north-mini-code:free, qwen3-coder:free)`
— the exact pair Day 18 proved dead (cohere returns empty, qwen3-coder per-model
rate-limited). Repointed to `(qwen3-coder:free, groq/llama-3.3-70b-versatile)`
mirroring frontend. As Day 18 predicted, every generation this session fell
through to the groq fallback (qwen3-coder 429'd on every attempt).

### Ownership & ordering (ponytail #1)
The old graph ran `frontend -> backend_code -> database`, so routers would have
been generated before any model file existed. Reordered to
`frontend -> database -> backend_code`. Database agent owns models/migrations;
backend coder consumes them (full-content context) and owns schemas/routers/
services + the Python infra. `assert_single_owner` raises if a filepath is
planned under both phases. Critically, NOTHING defined the declarative `Base`
before (every model and db_utils imported a phantom `backend.app.database`) — the
backend coder's `database.py` now owns `Base` + `get_db`, and the database prompt
imports `from app.database import Base` with an `app.` package root.

### Pair test (single resource) — fully runnable, CRUD verified end-to-end
Generated database.py -> model -> schema -> router -> main (+ config,
requirements) for a notes app. After the prompt fixes below:
- **7/7 files generated, 0 failures, 0 import warnings.**
- `python -m py_compile` passes on all 6 .py files (the Day-22-preview check).
- **The app starts and a full CRUD smoke test passes on SQLite**: POST 201,
  GET 200, LIST (pagination), PUT 200 (partial update via exclude_unset),
  DELETE 204, GET-after-delete 404, /health ok. Router imports resolve
  symbol-by-symbol against the REAL generated model (`Note`) and schema
  (`NoteCreate/NoteUpdate/NoteResponse`); `note_id: int` matches the model's
  integer pk; endpoints exactly match the API table (no invented PUT when the
  table omitted it); the session dependency is used correctly; main.py registers
  exactly the one generated router.

### Three prompt-driven defects found by the pair test, fixed at the prompt
Per "fix the prompt, not the output," each was fixed in the prompt and
regenerated (never hand-edited):
1. **Database model unstartable** — the prompt forced a UUID pk regardless of the
   SQL schema (which said INTEGER), and the LLM put `mapped_column(...)` in the
   annotation slot (`id: mapped_column(...) = ...`), which SQLAlchemy rejects at
   import. This also mis-led the router into `note_id: UUID`. Fixed: schema-driven
   pk type + a hard `Mapped[...] = mapped_column(...)` rule + a correct few-shot
   model example. Router then correctly used `int`.
2. **Pydantic v1/v2 mixing** — schema emitted a nested `class Config:` and a
   `from_orm` classmethod. Fixed: hard v2 rule (top-level `model_config`, no
   `class Config`/`from_orm`) + a schema few-shot.
3. **datetime typed as str** — `NoteResponse.created_at: str` while the column is
   `datetime` → ResponseValidationError 500 on every timestamped response. Fixed:
   rule that schema field types mirror the model's column types.
LLM variance is real: one intermediate regeneration invented a `class NoteBase:`
WITHOUT `BaseModel` (breaking `List[NoteResponse]` as a response field); the
schema few-shot with direct `BaseModel` inheritance stabilised it. Free-tier
quality is not guaranteed per-call — the few-shots reduce, not eliminate, drift.

### Full backend phase (two resources, direct run)
notes+tags with a FK relationship: **10/10 planned files generated (2 models +
2 schemas + 2 routers + 4 infra), 0 failures, 0 import warnings**, all 6 .py
py_compile-clean. Static cross-import check: every router->model/schema/database
import resolves symbol-by-symbol; `note.py` correctly imports `Tag` for its FK
relationship; main.py registers BOTH routers; each router's endpoints match its
API-table rows exactly. Runtime import could NOT be exercised here because the
generated model uses `Mapped[int | None]` (PEP 604, valid for the target Python
3.11+) and the only interpreter on this box is 3.9, which can't evaluate that
union at runtime — a test-environment limit, not a generation defect (py_compile
parses it fine, and the single-resource pair — which had no unions — ran fully).

### import fixer (ponytail #2)
AST-based, safe-fix-only (wrong prefix / wrong relative depth), flags phantom
modules. 14 crafted unit tests pass. In these runs it made **0 auto-fixes and
raised 0 flags** — the prompt's `app.` convention held on every file — but it is
the deterministic safety net for when it doesn't, and warnings route into the
Gate 4 QA panel as `import_warning` findings with file references.

### requirements.txt (ponytail #3)
Rendered from the curated `KNOWN_GOOD_VERSIONS` map: core stack always pinned,
detected third-party imports pinned from the map, unknown imports left UNPINNED
with a QA warning — the LLM never invents a version. The notes app produced a
clean 5-line pinned requirements.txt (SQLite -> no psycopg2). NOTE: a scratch-venv
`pip install` on this box failed to BUILD `pydantic-core` from source (no matching
prebuilt wheel for the local interpreter + no Rust toolchain) — an environmental
toolchain limit; the pins themselves are real, installable versions.

### Fault path — PASS
`FAULT_INJECTION=garbage:backend_code:2` on a 2-router fixture failed the first
router (initial + repair both garbage). Result: a failure stub written to disk
(visible/fixable at Gate 4), the failure recorded in `errors` + `partial_failures`
(folded into `stage_history` by `stage_node`), the phase CONTINUED (1/2 = 50%,
not >50%), and **main.py registered only the successful router** — the failed one
excluded, exactly as designed. Per-file Gate-4 fix reads from disk (Day 15
mechanism) so the stub is regenerable; a live-server Gate-4 fix was not exercised
today (quota).

### vs. Day 12 baseline
Day 12: `backend_code` was a stub — **0 / 29 planned backend files generated**.
The only Python that existed (database-agent models) had broken imports
(`from .invoice import Invoice` to a never-generated file; `from
backend.app.database import Base` to a module nothing defined; a wrong
`DeclarativeBase` import path that raised on import). ~30-40% of what little was
generated needed manual fixes; the app could not start.
Day 19: the backend phase produces a **runnable FastAPI app** — model+router+
schema+infra whose cross-imports resolve, whose fields/endpoints match the
architecture, that starts and serves CRUD (single-resource verified end-to-end;
two-resource verified static + py_compile, runtime-blocked only by the local
3.9 interpreter). The phantom-`Base` and wrong-prefix import failures of Day 12
are structurally eliminated (owned `database.py` + `app.` convention + fix_imports
safety net).

### Environmental / notes
- Ran the backend phase directly (real database + backend agents on a
  hand-written architecture+plan fixture), not the full research->planning
  pipeline, to conserve shared Gemini/Groq free-tier quota — same rationale and
  precedent as Day 18.
- Every backend generation went to the groq fallback; qwen3-coder:free 429'd on
  every attempt (OpenRouter free coder capacity still effectively unavailable).
- Installed `sqlalchemy` + `pydantic-settings` into the builder's own venv to run
  the generated app in isolation (the builder itself didn't depend on them).

## Day 20 observations

Scope: replace both coders' sequential loops with a shared, dependency-aware
parallel scheduler (`parallel_runner.py`) — independent files generate
concurrently (default max 3), dependents wait exactly as long as they must, a
file whose dependency failed is `blocked` and never launched, workers stay pure
while a single event-loop coordinator owns all state mutation and broadcasts, and
the UI shows a live count-based progress bar. Correctness first: a race that
corrupts `generated_files` is strictly worse than slow generation.

### Task 0 triage — one shared-infra crash bug fixed
`context_builder._build_backend_context` called `truncate_for_context` without
importing it — a latent `NameError` that fires whenever a full model+schema
dependency pushes a router's context over the 4K-token budget (plausible with
2+ large models). Fixed the import; also deleted a dead no-op degradation loop.
This is exactly the class of bug Task 0 targets: harmless-looking until file
generation is interleaved, then 3× harder to see.

### The purity refactor (the load-bearing change)
`process_and_write_generated_file` interleaved pure processing with shared-state
mutation. Split into `process_generated_file` (PURE — strip/sanity/import-fix/
write its own unique file; no `generated_files`/`errors`/`log` touch, no
broadcast) and `commit_generated_file` (coordinator-only — state + broadcast on
the event loop). This eliminates the whole parallel-bug class (lost updates,
interleaved logs, double broadcasts) by construction: every worker is pure, and
asyncio's single loop serialises all commits without a lock.

### Ponytail conclusions (recorded in the relevant commit bodies)
1. **Scheduler shape** — dynamic ready-queue, but realised by letting asyncio's
   await-graph BE the queue: one coroutine per file awaits its dependency
   futures, then takes a permit, then builds its context (seeing freshly
   committed deps). No hand-rolled in-degree loop. Wave-gather rejected (same
   correctness, worse throughput, more code). A Kahn pass runs only for cycle
   detection + deterministic creation order.
2. **Purity boundary** — the worker holds ONE permit for its whole lifetime, so
   both the primary and the repair LLM call (`call_validated`, kept one layer up
   where the message context lives) are covered; the pure unit is strip/sanity/
   fix/write. Disk writes are safe in the worker because filepaths are unique
   (Day 19 single-owner assertion).
3. **Limits** — one global semaphore, env-configurable (`GENERATION_MAX_CONCURRENT`,
   `GENERATION_MODE=sequential` forces 1). Per-provider caps + adaptive throttle
   deferred to Day 26's token bucket: the provider is only known inside
   `call_llm`'s fallback chain, and Day 17's per-call backoff already absorbs a
   429 even when several fire at once. Marked with a `ponytail:` ceiling comment.

### Structural ordering as real edges (+ a latent Day 19 bug fixed)
Day 19's backend `KIND_PRIORITY` sort is gone; structural ordering is now real
dependency edges (`backend_implicit_deps`: a router/service depends on its
SAME-resource schema/model tasks) so it survives parallel scheduling. Making
same-resource matching plural-tolerant (`_same_resource`) fixed a latent Day 19
blind spot: a plural router (`routers/notes.py`) never linked to its singular
schema (`schemas/note.py`), so under Day 19 it silently got neither full-content
injection nor an ordering guarantee. Now `notes` ↔ `note` link.

### Verification
- **Scheduler unit tests** (`test_parallel_runner.py`, fake generator, <2s):
  7/7 — diamond ordering + dependency-content injection, no-lost-updates
  (20 files @ 3 → exactly 20, unique, correct), failure→transitive blocking with
  honest `{done:2, failed:1, blocked:1}` counts, permit discipline (in-flight
  never exceeds the cap at 1/2/3/5), sequential-mode determinism (strictly serial
  + valid topological order), and the cycle guard.
- **Offline end-to-end wiring** (real `backend_coder_agent`, `call_llm` stubbed,
  no quota): 13/13 — parallelism (max in-flight ≥ 2), schema-before-router
  ordering, launch-time dependency-content injection (routers saw their schema's
  FINISHED content), infra rendered after the phase, `main.py` registering both
  routers, state == disk. Fault path: a schema fails → its router is BLOCKED
  (never launched — quota saved), the other resource is unaffected, `main.py`
  registers ONLY the delivered router, and the failed/blocked files are stubbed
  on disk (Gate-4 visible + fixable).
- **Regression**: `test_import_fixer.py` 14/14; frontend `vite build` clean.

### Timing — sequential vs parallel (simulated latency)
Measured the scheduler's wall-clock win in isolation from LLM/network noise: a
fake worker sleeps 1.0s per file (standing in for one free-tier LLM call) on a
realistic 12-file fixture (6 resources × {schema, router}, each router depending
on its schema). Same scheduler, only `max_concurrent` changes:

| Mode | max_concurrent | max in-flight | wall-clock | files |
|------|----------------|---------------|-----------|-------|
| sequential | 1 | 1 | 12.3s | 12/12 |
| parallel   | 3 | 3 |  4.0s | 12/12 |

**Speedup: 3.05×** (12.3s → 4.0s) — essentially the theoretical 3× ceiling; the
schema→router edges pack cleanly at concurrency 3. This measures the scheduler's
overlap precisely; it does NOT include real free-tier variance.

**Real-quota run deliberately deferred** (same precedent as Days 18–19):
OpenRouter's free coder capacity is ~50 req/day and qwen3-coder:free has 429'd on
every attempt for three sessions running, so every real call falls to the groq
fallback — a real seq+parallel pair (24+ coder calls) would exhaust shared quota
that later days need, and would add rate-limit/latency variance that obscures the
scheduler measurement rather than sharpening it. The PDF's ~20min→~8min
expectation assumes real LLM latency dominates; on this project the dominant cost
is rate-limit backoff, not generation time (Day 12), so the *realised* speedup is
capped by whatever effective concurrency the free tier tolerates, not by the
scheduler. Rate-limit-under-parallelism note: 3 concurrent calls that all 429
would trigger 3 simultaneous Day-17 fallbacks (a mild thundering herd onto groq);
the global cap of 3 keeps that modest, and the per-provider cap / adaptive
throttle that would tame it fully is Day 26's token bucket. Not observed live
this session (no quota run).

### vs. Day 19 baseline
Day 19: both coders generated files one at a time in a structural+topological
sort; a 12-file backend phase was ~12 serial LLM calls. Day 20: the same phase
fans out through one shared scheduler at up to 3 concurrent with identical
correctness guarantees (state == disk, ordering now enforced by real dependency
edges, per-file isolation) PLUS an honest `blocked` status and transitive
blocking that saves LLM calls on files doomed by a failed dependency. The
sequential path is not a separate code path — it is the same scheduler at
`max_concurrent=1` (`GENERATION_MODE=sequential`), the permanent debugging lever.

## Day 22 observations

Scope: replace the placeholder structural heuristics with real parsers, wire
genuine syntax errors into the existing repair machinery under a bounded budget,
extend checking to JS imports and JSON/YAML artifacts, aggregate everything into
an automated-checks report prepended to the QA report, and warn at Gate 4 when
too many files still fail after repair.

### Placement (ponytail #1) — hybrid, and Python needed no new machinery
The laziest correct answer was rung 2, not rung 7. `call_validated` already does
validate -> one repair -> re-validate with the message context, inside the
worker's permit, and `ast.parse`/`compile` are stdlib and in-process. So the
Python check is just another registry validator, and because `REPAIR_PROMPT`
interpolates `{errors}`, the "syntax error at line N" precision prompt IS the
validator's returned string — no separate repair path was written.

The one real obstacle: validators are `fn(text, state)` and cannot see the
filepath, and putting it in `state` would race across parallel coder workers.
Solved with a per-call closure (`syntax_of`) threaded through a new
`extra_validators` kwarg, which shares the SINGLE existing repair attempt — so a
syntax error and a length problem are still fixed by one call, not two.

JS went post-phase because it needs a node subprocess and spawning one per file
across three parallel workers is pure churn. Cross-file import resolution is
whole-tree by nature and could not have been done at write time anyway.

**Gap in the day's plan, found while reading the graph:** the real order is
`frontend_code -> database -> backend_code -> validation -> qa -> devops`. DevOps
— the YAML/JSON producer — runs AFTER qa, so a validation node before qa can
never see `docker-compose.yml` or the CI configs. Rather than reorder the graph,
the same parsers and the same budget run inline at the end of the devops node and
merge into the existing report. Without that, the DevOps agent's output would
have remained the one file category shipping completely unparsed.

### JS tooling (ponytail #2) — @babel/parser, and the JSX trap
Nothing was installed: node 22 is present but `node --check` has no JSX, and the
frontend's rolldown lives in a gitignored `node_modules` belonging to the
builder's own UI (wrong coupling for a backend check). So one dependency was
unavoidable.

Chose `@babel/parser` over acorn+acorn-jsx (two packages, still no TS) and
esbuild (one package but a per-arch native binary, and a bundler cosplaying as a
linter). Installs as 4 pure-JS `@babel/*` packages, ~5MB, no binary.
**Correction to the initial assessment:** it is not zero-dependency as first
assumed — it pulls `@babel/types` and two helpers. Verified and the code comment
was corrected rather than left overstating.

The tiebreaker was economic: Babel is the most tolerant parser, and a false
positive here does not merely skew a report — it buys a paid OpenRouter repair of
an already-correct file. `test_valid_jsx_passes` was written BEFORE any repair
wiring, exactly as the day's constraint demanded; plain acorn would fail every
generated React component and every metric downstream would lie.

### Repair economics (ponytail #3)
Per-file cap 2, run ceiling 10, both through `retry_counts` under `repair:{path}`.
Write-time repairs charge the same account via a worker-local tally folded in by
`commit_generated_file` (coordinator-only) — incrementing `retry_counts` inside a
parallel worker would race, so Day 20's purity boundary stayed intact.

**Deliberate deviation from the brief:** Gate 4's `fix_counts` was NOT merged into
the automatic budget. Those fixes are human-initiated; pooling them means a
machine's failed auto-repairs could exhaust the budget a user needs to click
"fix this file". Both are surfaced together in the Gate 4 breakdown, so it is one
VIEW over two ledgers rather than one ledger.

Threshold is defined precisely as
`|union of files with unresolved mechanical issues| / |files attempted|`.
Union of paths, so a file with three problems counts once. Denominator is
attempted rather than planned. Failed/blocked files come from `stage_history`,
not from parsing — Day 20 writes a syntactically VALID stub for them, so parsers
alone would report those files healthy.

A crashed repair call still charges its slot; otherwise a persistently failing
file would retry forever.

### Verification
- **Crafted-breakage suites, zero LLM calls** (`test_validation.py` 20/20,
  `test_validation_pass.py` 14/14, ~4s combined): valid JSX + modern syntax pass;
  unbalanced JSX, broken destructuring and prose leak fail with line numbers;
  Python missing-colon reports the right line; the `compile`-only class
  (duplicate argument) is caught where `ast.parse` alone passes; phantom relative
  imports, missing packages, broken JSON/YAML flagged; per-file cap stops at 2;
  run ceiling stops at 10 and sets `repair_budget_exhausted`; import warnings
  never buy a repair call; node-missing degrades loudly instead of reporting
  clean.
- **Live repair (real models)**: a broken router + broken JSX component both
  repaired successfully (qwen3-coder:free 429'd, groq fallback served both — the
  documented pattern), repaired Python verified to parse, and a genuine phantom
  import caught in the fixture.
- **Scoped live run** (real frontend coder, `FAULT_INJECTION=syntaxerr:frontend_code:1`,
  upstream docs from the Day 21 fixture — same quota precedent as Days 19/21):
  3 files generated, the injected broken file caught by the batch parser,
  repaired with one real call, **all 3 files parse post-repair**, budget 1/10,
  `retry_counts` correct, and the QA report carried the prepended
  `**AUTOMATED CHECKS**` summary.
- **Regression**: Day 20 fake-generator suite 9/9, Day 19 import fixer 14/14,
  Day 21 golden `--rescore` 7/7, frontend `vite build` clean.

Added a `syntaxerr` fault kind: `garbage` returns 2 chars and trips the LENGTH
validator (the Day 17 path), so it never reached the syntax validator. `syntaxerr`
returns a plausible-looking file that only a real parser rejects.

### Honest limits on today's evidence
- **The QA-focus comparison is NOT established.** The live run produced a single
  3-file batch with 1 QA issue — far too small to claim R1's reasoning
  "noticeably shifted" from syntax to logic. The mechanism is verified (the
  structured findings and the do-not-re-litigate instruction demonstrably reach
  the QA prompt, asserted in-test), but the qualitative payoff is unmeasured. A
  real before/after against a Day 19-era report needs a full-size run — Day 25.
- **The Gate 4 banner was not exercised in a browser.** Its trigger flag
  (`below_threshold`) was verified live (25% > 20% -> True) and the component
  compiles under `vite build`, but the rendered DOM and the breakdown popover
  were not clicked through in a running server. `QUALITY_THRESHOLD=0.01` was
  confirmed to load and override.
- **No full research->devops pipeline run.** Same quota reasoning as Days 18-21:
  qwen3-coder:free 429'd on every attempt again this session, and a full run
  would burn shared free-tier capacity for weaker signal than the scoped run gave.
- Thresholds and ceilings are set at their defaults on ONE run's evidence. Day 25
  (three full integration projects) is the intended tuning point — resist moving
  them before then.

## Day 23 observations

### Task 0 — LangSmith reality check (done BEFORE any tracing design)
The PDF's "tracing works automatically, no code changes" claim is **false for
this repo**, for a more basic reason than the LiteLLM-vs-LangChain one:

- **`LANGCHAIN_API_KEY` is the literal placeholder `your_key_here`, and
  `LANGCHAIN_TRACING_V2=false`.** Both have sat unchanged since Day 1. No trace
  has ever been emitted, so **the dashboard could not be inspected** — there is
  no account credential to inspect it with. The Day 1 "install + env placeholder"
  step was never finished, and nothing since then depended on it, so it went
  unnoticed for 22 days.
- `langsmith 0.4.37` **is** installed in `backend/venv` (it is in
  `requirements.txt`), so the SDK half of the setup is real. Only the key is missing.
- Consequence for today: the "explore a working dashboard" branch of Task 4 is
  **blocked on a user-supplied key**, not on code. Everything else — usage
  capture, the local metrics store, the UI panel, Day 26's evidence table —
  is independent of LangSmith and was built first, deliberately.

### Task 0 — degradation behaviour, measured not assumed
Probed `@traceable` with the placeholder key and tracing forced on:
- The traced function **returned its result normally**; the `403 Forbidden` was
  swallowed by langsmith's background uploader and only logged. **The SDK already
  provides the Task 5 isolation contract** — a bad key cannot fail a run. This
  removed the need for a hand-written try/except around every trace emit.
- Overhead, 60 calls, steady state (SDK init excluded):
  `tracing=true` median **1.49ms** / `tracing=false` median **1.28ms`
  -> **~0.21ms added per call**, against LLM calls of 2-30s. Negligible; no
  batching or async queue needed for the LangSmith side.

### Task 0 — blocking triage fix: another delisted OpenRouter slug
`openrouter/qwen/qwen3-coder:free` now returns `NotFoundError: This model is
unavailable`. It was the **primary** for `architecture`, `frontend_code`, and
`backend_code`, and the fallback for `database` — so every coder call was
spending one guaranteed-failing round trip before falling through to Groq.
This is the *second* time this exact slug has died (see Day 18) and it would
have silently inflated every latency number this day exists to measure.

Live `/api/v1/models` free tier is down to **14 models**, of which the only
remaining coder-oriented one is `cohere/north-mini-code:free` — the model Day 18
recorded as returning EMPTY responses. So there is no drop-in replacement.
Fix applied: promote the already-proven `groq/llama-3.3-70b-versatile` to primary
for those agents, with `gemini/gemini-2.5-flash` as a cross-provider fallback.
Deliberately did **not** adopt an untested slug (`poolside/laguna-m.1:free`,
`openai/gpt-oss-20b:free`) — today's purpose is a clean baseline, and a new model
would confound it. Worth revisiting once metrics exist to compare against.
Caveat: four agents now share Groq as primary, concentrating load on one
provider's rate limit; the Gemini fallback is what makes that recoverable.

### Provider token-usage reliability (Task 1 verification)
| provider | `resp.usage` populated? | notes |
|---|---|---|
| `gemini/gemini-2.5-flash` | yes | also reports `reasoning_tokens` under `completion_tokens_details` |
| `groq/llama-3.3-70b-versatile` | yes | also reports server-side `queue_time`/`prompt_time` |
| `openrouter/*` | **unverified** | could not test: the coder slug is delisted; QA's nemotron slug is alive but untested for usage |

### Day 26 evidence table — measured baseline (project `day23-baseline`)

Live run, real providers. Upstream research/requirements from the Day 21
fixture; the coder phase ran a fixed 4-task plan (Day 22 precedent). Queryable
again any time via `metrics_store` — this table is a snapshot, not the source.

**Totals:** 20 attempts (9 ok, 11 failed), 20,983 input + 79,271 output =
**100,254 tokens**, 722s summed LLM wall-clock.

| agent | ok calls | avg in | avg out | total in | total out |
|---|---|---|---|---|---|
| planning | 1 | 5,631 | 21,472 | 5,631 | 21,472 |
| qa | 2 | 1,840 | **17,110** | 3,680 | 34,220 |
| architecture | 2 | 2,147 | 10,969 | 4,294 | 21,937 |
| frontend_code | 4 | 1,845 | 411 | 7,378 | 1,642 |

| agent | attempts | p50 | p95 | max |
|---|---|---|---|---|
| planning | 5 | **84.5s** | 90.2s | 90.2s |
| qa | 3 | 7.9s | 354.4s | **354.4s** |
| frontend_code | 6 | 0.8s | 5.2s | 5.2s |
| architecture | 6 | 0.2s | 46.5s | 46.5s |

**Slowest 2 agents: `planning` (p50 84.5s) and `qa` (max 354s).**
**Top 2 by token spend: `planning` (27k) and `qa` (38k).**

#### Where the measurements contradicted the expectations

The brief predicted "architecture — large output; QA ~20k INPUT tokens". Both
were wrong on this run, and the direction of the error matters for Day 26:

- **QA is output-heavy, not input-heavy** — 1.8k in vs **17.1k out** per call.
  The reasoning model (`nemotron-3-nano-omni-...-reasoning`) emits its reasoning
  as completion tokens. The predicted ~20k input assumed a large codebase under
  review; this run reviewed only 4 small files, so the input figure will grow on
  a full project while the output figure is a property of the MODEL. Optimising
  QA's *input* (context trimming) would have been aimed at the wrong half.
- **`planning`, not `architecture`, is the most expensive agent** on both axes —
  21.5k output tokens and an 84.5s p50. It emits the entire task plan as JSON in
  one call, which is inherently the largest single output in the pipeline.
- **QA's 354s max** is a single call against the OpenRouter reasoning model,
  roughly 45x its own p50. Any per-run timeout budget must account for it.

Caveat: 1-2 ok calls per agent. These are order-of-magnitude signals, not stable
means. Day 25's three full integration projects are what turn them into a real
baseline — do not tune thresholds on this table alone.

#### Provider reliability, measured

| model | ok | failed | failure mode |
|---|---|---|---|
| `groq/llama-3.3-70b-versatile` | 3 | **8** | rate_limit |
| `gemini/gemini-2.5-flash` | 5 | 2 | timeout |
| `openrouter/nvidia/nemotron-...-reasoning:free` | 1 | 1 | error |

**8 of 11 failures were Groq rate limits — a direct consequence of today's
triage fix** concentrating architecture/frontend_code/backend_code/database onto
Groq as primary after `qwen3-coder:free` was delisted. The pipeline still
completed (Gemini fallback absorbed it), but this is now the top routing risk
and the instrumentation caught it on its first real run. Rebalancing primaries
across providers is a Day 26 candidate, and there is now data to justify it.

#### Parallel attribution proof (Task 6)

Four coder files generated concurrently, real providers:

| file | attempts | in | out |
|---|---|---|---|
| `NoteCard.jsx` | 1 | 1,678 | 383 |
| `NoteList.jsx` | 1 | 1,712 | 148 |
| `lib/api.js` | 1 | 1,673 | 200 |
| `pages/NotesPage.jsx` | **3** | 2,315 | 911 |

Every row carries its own filepath, and NotesPage's three attempts (rate-limit
fallover) stayed attributed to NotesPage rather than smearing across the batch
or collapsing onto the last-finished task. This is the explicit-identity design
working — the failure mode it prevents would have made every per-file number
above meaningless. Also asserted at zero cost in `test_metrics_attribution.py`.

### Day 23 blocking bug found BY the instrumentation

The first baseline run showed `planning` spending 24k input tokens and 66s to
produce a plan that was then discarded. Root cause: `TaskSchema` rejected any
filepath without a dot, so the moment the planner proposed `backend/Dockerfile`
the entire plan failed validation — and the coder, validation and QA stages
silently received an empty task list and generated nothing. Fixed by allowing
conventional extensionless filenames. This class of failure (expensive work
silently discarded) is exactly what per-attempt metrics make visible.

### Honest limits on today's evidence
- **The LangSmith dashboard has never been opened.** No API key was ever
  configured, so the trace-emit path is implemented and its failure behaviour is
  probed, but nothing about the rendered dashboard, trace nesting under phases,
  or metadata filtering has been confirmed. Everything claimed about LangSmith
  in `docs/OBSERVABILITY.md` beyond the degradation contract is untested.
- **No full research->devops pipeline run.** Same quota reasoning as Days 18-22,
  compounded by the Groq rate limiting above. `research`, `requirements`,
  `backend_code`, `database` and `devops` have NO rows in the baseline table.
- **The metrics UI was not exercised in a browser.** The endpoint was verified
  live with curl (including the has_metrics=false path) and the components
  compile under `vite build`, but the panel was not rendered in a running app.
- Per-agent sample sizes are 1-2 successful calls. Treat every number above as
  a direction, not a value.

### Day 23 UI verification — what was real and what was staged
The metrics panel and the Day 22 quality banner WERE rendered and clicked
through in a browser (both servers running, `/projects/day23-baseline`):
- Token/latency stats and the per-agent table showed the **real** measured
  numbers above, fetched live from the endpoint.
- The Day 22 "Show breakdown" popover opened and listed its rows — closing the
  Day 22 open item "the banner was not exercised in a browser".

Two defects were found this way that `vite build` could not catch: two identical
"Run Metrics" cards at gate 4, and an attempt count overwriting the call count so
planning read "5 calls" beside averages from its 1 successful call. Both fixed.

**Caveat on the `day23-baseline` project row:** its LangGraph checkpoint was
seeded by hand to park it at gate 4, and its `validation_report` values
(25% failure rate, 1 phantom import) are **synthetic** — chosen to force the
banner into its below-threshold state. The `agent_runs` metrics for that project
are entirely real. Do not read that checkpoint as a genuine end-to-end run.

## Day 24 observations

Lifecycle day: projects list, cold resume, restart-from-stage, cascade delete,
summary PDF. Almost entirely plumbing over existing persisted data — one real
agent call was spent, on a deliberate restart test.

### Two real bugs, both found by running the thing rather than building it

**1. An unresumable run was reported as `completed`.** Found while verifying
restart-from-stage. After a node raises, `handle_pipeline_failure` calls
`graph.update_state(...)` without `as_node`, which makes LangGraph re-derive the
pending position — and it can come back empty. Streaming that checkpoint then
yields **zero** events (confirmed directly: `list(graph.stream(None, config))`
returned `[]`), after which `run_graph_background` fell through to its completion
branch and stamped `completed`. A project with no architecture, no plan and no
generated code would appear in the list as finished.

Fixed the misreport: if no node ran and the project carries a `failed_agent`,
stay `error_paused` and tell the user to restart from an earlier stage.

**CLOSED — the underlying position loss is fixed too.** Isolating the
mechanics against a compiled graph showed the diagnosis above was incomplete:
`update_state` does not always empty `next`. When a node raises, LangGraph
leaves `next=(failed_node,)` with the error on the pending task — precisely what
a retry needs — and *any* write re-derives that pending task from current state.
Where it lands depends on graph shape: `()` for a node reached through a gate's
conditional edge, `('research',)` for another path. Measured:

| in the failure handler | `next` | retry re-runs? | failure recorded? |
|---|---|---|---|
| no `update_state` | preserved | yes | no |
| plain `update_state` | destroyed | **no** | yes |
| `update_state(as_node=predecessor)` | preserved | yes | yes |

The `as_node=predecessor` form works but needs a hand-maintained map of graph
structure that would drift silently, and it is ambiguous for nodes with several
predecessors (`architecture` has three gates). So failure info moved out of the
checkpoint entirely and onto the projects row — it describes the RUN, not the
project, and recording it there cannot corrupt graph position. The `/recover
retry` path stopped writing to the graph for the same reason and counts attempts
on the row. Failures recorded before the move are still read from their
checkpoint as a fallback, so their error cards still render.

`test_lifecycle.py` now drives the whole loop on the real pipeline with zero API
calls (the architecture agent's one LLM seam is patched to fail once): node
raises -> error_paused -> retry -> node re-runs -> document produced -> pipeline
advances to gate 2 -> failure cleared. Reintroducing the write fails 8 of those
assertions, so the test genuinely guards the regression.

Projects already corrupted by the old behaviour (`972e7066`) stay unresumable by
retry — their pending task is long gone — but Restart recovers them, since it
re-enters via `as_node=<gate>` rather than relying on `next`.

**2. The interrupted banner never rendered.** `setStatus('interrupted')` was
writing into the variable `useProjectStream` owns for its connection lifecycle;
the socket's `connected` event fired afterwards and overwrote it, so the zombie
showed a badge reading "Unknown" and a right-hand panel claiming "Agents
working…" — the exact dead spinner the feature exists to prevent. `vite build`
passes either way; only loading a seeded zombie in a browser exposed it.
Interruption is now derived from `position.phase` at render time.

### Cold-resume finding: the gates were already cold-safe

The day's stated expectation was that at least one gate would turn out to depend
on a live WebSocket event. It did not happen. Gates 1–4 read everything from
`projectState`; the single live-only prop (`lastAgentComplete`) merely picks the
wording of the in-flight regeneration overlay, which cannot occur on a cold load.
Verified in a browser against a cold server: gate 1 rendered research +
requirements + tech stack, gate 4 rendered its stats, metrics and quality banner.

### Restart-from-stage

`invalidate_downstream` was reused unchanged; restart re-enters by writing the
checkpoint as `as_node=<gate>` with a decision, so the existing `GATE_ROUTES`
conditional edges do the routing and restart adds no graph wiring. All five
targets are asserted to land on the right node against the real compiled graph
(`test_lifecycle.py`, zero API cost).

The one live restart (a *completed* project, from architecture) performed its
state surgery exactly as designed — research and requirements preserved,
architecture and plan snapshotted into `previous_versions` and cleared,
`stage_history` recording `trigger: restart` — and then failed on the LLM call:
Groq rate-limited on both attempts, Gemini 2.5 Flash returned 503 "high demand".
An external provider failure, not a pipeline defect, but it means **the restart
has never been observed producing a fresh document end to end.**

### Delete cascade

Order is metrics → checkpoint → files → projects row, i.e. the discoverable
pointer last, so any half-failure leaves a visible project that can simply be
deleted again. Hard delete, with the ZIP download offered inline in the confirm
dialog. `test_lifecycle.py` asserts all four stores are clean afterwards, that a
running project's task is cancelled and awaited first, and that seven traversal
payloads are rejected. Exercised through the UI as well as over HTTP.

### Caveats
- Restarting from `code_generation` has not been run; only its routing is proven.
- The PDF was verified against a real project, a fully empty state, a hostile
  input state and a real cancelled project. The task referenced
  `/mnt/skills/public/pdf/SKILL.md`, which does not exist in this environment, so
  plain reportlab platypus was used instead of that skill's guidance.
- 51 legacy projects are still in the list; 3 rows with status `error` were
  migrated to `error_paused` on init.

## Day 25 observations

Full write-up: **`docs/INTEGRATION_RESULTS.md`** (rubric, provider ceiling,
before/after per fix, known limits). Pointer only — not duplicated here.

The day set out to produce a three-tier degradation curve and did not. One
simple project ran, partially, halting at `backend_code`; medium and complex
never started. **That curve does not exist in this data** and was not inferred.

What ran into instead, in order, each blocking the pipeline outright:

1. **Planning could not complete at all.** A uniform 90s timeout against
   planning's ~21.5k-token output; its only success ever recorded took 84.5s.
   After the fix it completed at 109.2s — past the old ceiling by 21%. `877407d`
2. **All 47 frontend tasks were unschedulable** on a correct, acyclic plan. Our
   own implicit layering closed a 2-cycle against an explicit dependency
   (`api.js` requires `config.js`; layering made `config.js` wait on
   `api.js`). `7081d39`
3. **34 of 47 files lost to 429s** — 59 rate-limits against 13 successes.
   Pacing per model doubled delivery to 26 ok. `26a0e1f`
4. **17 of 96 files were syntax errors we wrote ourselves** — the failure
   placeholder embedded multi-line JSON in a single-line comment. `ac3ba89`

Measured provider ceiling, the day's most consequential number: **Gemini free
tier is 20 requests/day/model** (exhausted after 15 successes), and **Groq's 12k
TPM cannot serve planning's ~26.9k-token call at any pacing**. Consequence: the
pipeline cannot complete one simple project per day on free tiers, and with
Gemini exhausted nothing gets past planning. Inherent — documented, not chased.
Day 29's Ollama moves from convenience to prerequisite.

Two corrections worth carrying forward:

- **My first diagnosis of the halt was wrong.** The status said `rate_limited`;
  the operative failure was a Gemini *timeout*, and the status reported the last
  error rather than the one that mattered.
- **The rubric flattered the system.** Once stubs parsed, JSX placeholders
  cleared the size floor and scored as real files — 43.8% usable where the honest
  figure is 21.9%. Corrected in `1e2df35`.

Caveats: LangSmith attribution was unavailable (`LANGCHAIN_TRACING_V2=false`, key
is a placeholder), so Task 4 used local metrics and offline state instead of
traces. Runs were driven over the REST API the UI calls, not through the browser.
QA never ran, so QA-issue and repair-spend columns are empty rather than zero.

## Day 26 observations

Optimisation day. Full ledger in `docs/OPTIMISATION_TARGETS.md`; this is the
summary and the corrections.

**The day's plan was wrong on three counts, and the data said so before any
code changed.** Recording this because the pattern — plan assumes a cause,
metrics name a different one — has now repeated across several days.

- **QA was not slow from a bloated input.** The plan assumed a ~20k-token
  context needing batch summarisation. Measured QA input is **1,840 tokens**,
  among the leanest in the pipeline. QA was slow because its primary model, an
  OpenRouter reasoning model, **ignored `max_tokens=3000` and returned 32,768
  tokens in 354 seconds**. The Gemini fallback reviewed the same batch in **7.9
  seconds with 1,452 tokens**. A 45× latency tax on output that was discarded.
  Trimming QA's context would have removed real signal to fix a non-problem.
- **Token budgets already existed and were binding, not absent.** Every call
  site passes a tuned `max_tokens`. Measured: architecture stopped at
  11,996/12,000 twice, requirements at 4,496/4,500 on *both* recorded calls,
  research at 97%, two coder files at 1,49x/1,500. The PDF's suggested defaults
  (research 3000, requirements 2500, architecture 4000) are *below* current
  output — adopting them would have truncated every document the pipeline
  produces. The deliverable became truncation *detection*, not budget reduction.
- **RPM was never the limit that bit.** The refusal that halted work was Groq's
  **tokens-per-day: 95,966 of 100,000**, retry in 45 minutes. Provider errors
  also name a *model*, not a provider, so the planned per-provider bucket would
  have been a less accurate grain than the per-model pacer Day 25 already
  shipped. Built daily-budget tracking instead of a second rate limiter.

**Severity: high, and pre-existing.** Truncation is silent — a cut-off
architecture document returns exactly like a complete one and gets written to
disk. It had been happening since at least Day 23 with nothing to detect it.
Now read from `finish_reason` and surfaced in the metrics panel.

**Aggregate view changed the optimisation target.** Per-call latency says QA is
slowest; *total* time says `frontend_code` is **53.6% of the run** (43 calls ×
16s) against planning's 17.6%. Ranking by p50 points at the wrong agent. The
metrics panel now ranks by total time for this reason. Likewise by total tokens:
`frontend_code` consumes **110,619 input tokens per run**, alone exceeding Groq's
entire daily allowance — which explains the exhaustion completely.

**One real bug found and fixed while building:** the cache read closed its
connection inside the `with conn:` block, so every miss threw and took the
exception path. Tests passed anyway because the handler returned `None`, which
happened to be the correct value — a reminder that a green test can hide a
broken path when the failure mode and the success value coincide.

**Not validated, and honestly so.** Fast mode's "~50% faster / ~80% quality"
claim could not be tested: Groq's daily allowance was exhausted *before* the
day's work began, so no live A/B was possible. What is established is that the
mechanism the claim assumes cannot deliver it — `max_tokens` is a guillotine,
not an instruction to be brief, so halving it either changes nothing (coder
files use 356 of 1,500) or truncates (the four agents already at their ceiling).
Fast mode therefore skips the LLM repair pass, keeping the free static checks,
and scales budgets only where headroom was measured.

Quality baseline held: offline re-score of `2901fb46` returns **21.9% usable**,
identical to Day 25. All 12 offline suites green. Live-API agent tests could not
run — exhausted quota, not regression.

## Day 27 observations

Pure frontend day: design system, dark mode, motion, gate layout, mobile.
Zero LLM spend — every visual state was designed against Day 25's persisted
projects loaded cold, and no pipeline was run. The one moment that would have
cost quota (clicking Approve on a paused gate to prove the button works) was
deliberately not taken; interactivity was verified through Request changes
instead, which opens the feedback input without calling a model.

**What got the token treatment.** Everything the eye actually lands on: the
shell, projects list, project detail, live feed, all four gates, file browser,
metrics panel, timeline, error cards, lifecycle dialogs and both forms. The
audit for hardcoded palette classes (`bg-gray-*`, `text-blue-*`, …) went from
several hundred to **zero**. Dark and light are two variable blocks in
`index.css` and there is not a single `dark:` variant in the codebase.

**The design rule.** Filled accent means one thing: the line has stopped and
needs a human. Outline accent means an action is available. Everything the
pipeline does unattended is neutral. This was not decoration — the first pass
put gold on the New project button, the active filter chip and every gate
action at once, and a waiting gate stopped being findable in a 52-row list.
Rationing it fixed that. `rate_limited` deliberately takes the muted warn tone:
waiting on a provider is not waiting on you.

**Three planned approaches were rejected on inspection**, each recorded in its
commit:
- framer-motion was not installed. Its distinctive value is exit animation and
  nothing here exits. Decisively, the global `prefers-reduced-motion` block
  covers CSS but would NOT have reached a JS library's inline styles — staying
  in CSS made accessibility automatic. Verified empirically: animations drop
  0.18s → 0.00001s and transitions 0.15s → 0.00001s under emulated reduce.
- The split-pane was achieved by giving each gate's existing content and action
  blocks explicit grid placement, rather than restructuring 2,770 lines of gate
  JSX for the same visual result.
- "Make it all responsive" was scoped down to the one journey that matters.

**Responsive scope decision.** The approve-from-phone path is fully mobile:
drawer nav, progressively hidden table columns, single-column gates, and a
pinned 2×2 action grid. Four surfaces deliberately refuse to render below `lg`
and say so — the file browser, the per-agent metrics table, architecture
diagrams and diffs. Shrinking those to 375px looks broken and invites decisions
on evidence you cannot read. That boundary is stated in the UI, not hidden.

**Real bugs found while restyling**, all pre-existing:
- `--accent-ink` was written as an 8-digit hex, i.e. 33% alpha, washing the
  approve label out against its own fill.
- The `prose prose-sm` classes on every markdown block were **inert** — the
  Tailwind typography plugin was never installed, so agent output had been
  rendering unstyled since the markdown was added. Replaced with real
  theme-aware styling.
- `Layout.jsx` carried its own status-dot colour map that had already drifted
  from `lib/status.js`, the file written specifically to stop that drift.
- The code viewer was hardcoded to a light Prism theme.

**No fixture was missing.** Every visual state had a persisted project to design
against: all four gates paused, completed, error_paused, cancelled and
interrupted. Day 24's cold-resume made this the cheapest day of the build.

**Left deliberately.** Rarely-seen surfaces keep their own local palette objects
(ErrorCard's severity map, Gate3's complexity badges) — they now resolve to
tokens but were not restructured. `ProjectDetailPage` still holds a
`react-hooks/exhaustive-deps` warning that predates today. No behavioural
change: all nine offline backend suites still pass, and restart, delete,
feedback and resume were each exercised in the browser.

## Day 28 observations

Docker packaging. Two images, nginx in front, one command to start. The only
LLM spend was a single end-to-end run through the containerised stack.

**The WebSocket through nginx — the thing the day was actually about — worked.**
`101 Switching Protocols` on the handshake, then real `agent_complete` frames
arriving over the proxied socket. The three lines that matter are
`proxy_http_version 1.1` plus re-setting `Upgrade` and `Connection`: nginx talks
HTTP/1.0 upstream by default and strips both headers as hop-by-hop, so omitting
any one yields a page that loads, an API that works, and a feed that is silently
dead. Worth testing the handshake with `curl` BEFORE spending any quota — it
costs nothing and would have caught a misconfiguration for free.

**Mounting the database directory rather than the file was not theoretical.**
Mid-run the host showed `projects.db-wal` at 144KB beside `projects.db`. A
single-file bind mount leaves those siblings in the container's writable layer,
so every transaction still in that WAL at shutdown would have vanished — no
crash, no error, just a project that was there yesterday. Mount directories.

**WAL over a macOS bind mount works.** This was the one design decision taken on
faith. VirtioFS handled it: no `database is locked`, no `disk I/O error`, and
full state (94 log lines, 41 files) survived `down`/`up` intact. The fallback if
it had misbehaved was cheap — the backend is the only writer, so `journal_mode=
DELETE` would have cost almost nothing.

**One real bug, found only because the smoke test ran.** The backend crash-looped
on first start: `FileNotFoundError: /prompts/research_agent.md`. Nine agent
modules resolve their system prompt via `parents[3]/"prompts"`, which lands at
`/prompts` — a SIBLING of `/app`, outside a `./backend` build context. Prompts
are source, not data, so the fix was widening the build context to the repo root
rather than mounting them. Any fresh clone would have hit this.

**Node survived into the image.** `COPY --from=node:20-slim /usr/local/bin/node`
works because both bases are bookworm; `RUN node --version` in the Dockerfile
turns a broken copy into a build failure instead of a silent downgrade to
brace-counting. Container reports v20.20.2 and logs `JS deep validation ready`.

**The run halted at `backend_code` — quota, not packaging.** Groq's daily budget
was already at 104.8% before the day started; every `backend_code` attempt
returned `rate_limit` and the pipeline paused itself at 10/12 files failed.
Correct behaviour, and orthogonal to Docker. Not retried, per the one-spend rule.
Gates 1-3 all approved through nginx, 41 files written, ZIP verified (56 files,
valid content) both before and after a restart.

**Pre-existing quirks surfaced, not fixed today:**
- `status`/`current_stage` lag the checkpoint — the API read `backend_code` while
  the log showed `database_agent` already finished. The two-persistence-layer
  split in CLAUDE.md, visible in practice. It made a naive wait-loop give up.
- The project list returns `project_name: None` while the detail endpoint returns
  it correctly.
- Naming the project "Day28 Smoke" sent the research agent off writing about
  smoking cessation instead of the habit tracker in the brief. The project name
  steers generation more than expected — relevant to prompt work, not to Docker.

**Image sizes:** backend 1.24GB (litellm + langchain + google deps dominate),
frontend 97.4MB. The backend is worth revisiting if it ever ships anywhere.

**Port 3000 was already held** by an unrelated Next.js dev server, so the host
port is now `${FRONTEND_PORT:-3000}` and the test ran on 3001. Host-side only —
nginx still listens on 80 inside, so the proxy path tested is the real one.

## Day 29 observations

Ollama activated as a genuine tier-3 fallback. Cloud was disabled for every test
by marking the daily budgets spent, so the day's testing cost **zero cloud
calls** — no keys were broken and no request left the machine.

**Hardware forced the model set, and the prompt's recommended one did not fit.**
This is an 8GB M1. `qwen3:14b` needs ~16GB and was never attempted. `phi4-mini`
(3.8B, Q4_K_M, 2.5GB) pulled and served fine. `qwen2.5-coder:7b` (4.7GB) was
abandoned mid-download — see the thrashing note below. The system adapted
correctly without being told: the probe enumerated one model and every agent
degraded to it through the preference lists, which is exactly the
ideal-absent-so-take-next-best path working.

**Pulling a model while generating makes an 8GB machine unusable.** Mid-run:
swap at 10.85GB of 12GB, 1.3M pageouts, 15% memory free, and `llama-server`
sitting at **6.8% CPU** — blocked on paging, not computing. Killing the download
recovered it. Two consequences: the 7B coder model is not viable on this machine
alongside anything else, and `OLLAMA_MAX_CONCURRENT=1` is not conservatism, it
is the difference between working and swapping.

**The probe's 1s timeout made the local tier vanish under its own load.** Found
by the harness, not by reading. A re-probe (60s TTL) landed during a 101s local
completion, `/api/tags` did not answer within 1s, the model list came back
empty, and the next agent skipped a *working* Ollama and raised instead of
falling back. Fixed: 3s timeout, and a failed re-probe now retains the last
known models. Once the daemon has been seen serving, a failure means "busy" far
more often than "gone". An absent daemon is unaffected — nothing listening is a
connection refusal, not a timeout.

**The rate limiter already did the proactive routing.** Day 26 checks
`budget_exhausted` at the top of each tier and `continue`s before building a
request, so an exhausted allowance already fell through to whatever tier came
next. Ollama just became that tier. Confirmed in the logs: both cloud tiers log
`budget_exhausted` at `latency_ms: 0` and no request is sent. Nothing needed
building here — it needed verifying.

**Every agent has a cross-provider fallback pair, so one exhausted provider
never reaches local.** `research` is gemini+groq, `architecture` is groq+gemini,
and so on — no agent has both tiers on one provider. Exhausting a single
provider therefore just moves work to the other cloud provider; local is only
reached on near-total cloud exhaustion. That is the correct ordering (local IS a
last resort), but it means the "provider X is spent so X's agents go local"
scenario does not exist in this codebase. The only true mixed case is
gemini+groq spent with openrouter alive, which keeps `requirements` on cloud
(its fallback is openrouter) and sends the other eight local.

**Per-agent routing, cloud fully disabled — 9/9 agents served locally**, 304s
total, 34s average on short prompts:

| agent | s | agent | s | agent | s |
|---|---|---|---|---|---|
| research | 37.5 | requirements | 13.1 | architecture | 12.9 |
| planning | 16.0 | frontend_code | 74.4 | backend_code | 43.8 |
| database | 60.9 | qa | ~24 | devops | 44.3 |

**LiteLLM does report usage for Ollama.** `prompt_tokens` and
`completion_tokens` both populated on every local call, so Day 23's null-safe
handling needed no special case for the local tier.

**Local output is far shorter than cloud output, and that is the quality story.**
Completion tokens on the full pipeline run against the cloud averages recorded
on Day 25:

| agent | cloud avg | local | ratio |
|---|---|---|---|
| research | 4.1k | 1,145 | 28% |
| requirements | 4.5k | 1,556 | 35% |
| architecture | 10.6k | 1,250 | 12% |
| planning | 21.5k | 718 | **3%** |

**Planning is the agent that degrades badly, and the validators caught it.**
phi4-mini emitted a structurally invalid plan: 6 tasks against a minimum of 8,
two tasks with empty `filepath`, and 27 required files missing. At 718 completion
tokens against a cloud average of 21.5k there was not enough output for a valid
plan to exist. The Day 22 validation and repair layer rejected it and re-ran —
which is the argument against a pause-for-cloud whitelist: the schema validator
already refuses to build on a too-weak result, without a policy table to keep in
sync. Planning is the one agent where local is more expensive than it looks,
because each repair is another multi-minute local call.

**Stage timings, full pipeline, cloud disabled** (phi4-mini, 8GB M1):
research 201.7s, requirements 208.7s, architecture 160.1s, planning 324.3s.
Roughly 30-50x the cloud equivalents — gemini does research in ~8s. Local is not
a performance option, it is an availability option.

**Design decisions taken and why** (recorded at the ponytail passes):
- Two local preference lists (code vs prose), not nine per-agent entries. On one
  machine the binding cost is the ~5GB model swap, not model capability, so
  alternating models per stage costs more than capability-matching gains.
- No pause-for-cloud agent whitelist. Every high-stakes artifact already sits
  behind a human gate, and the plan additionally behind a schema validator. A
  refusal table would guard a failure those already catch. `LLM_MODE=cloud-only`
  gives the same protection as a per-run choice with nothing to keep in sync.
- The local concurrency cap lives in `call_llm`, not the Day 20 parallel runner.
  The runner cannot know which tier a call will land on; the tier is only known
  at the call. Three workers still get admitted and only those reaching local
  serialise.
- No `tier` column in metrics. `ollama/qwen3:4b` already encodes it and every
  attempt passes one logging choke point.
- Per-project `LLM_MODE` not plumbed through state. Single-user tool, sequential
  runs, and the `mode` parameter on `call_llm` already exists — so per-project
  becomes additive the day two concurrent projects need different tiers.

**Full end-to-end local run: reached gate 3, not yet completed.** Research →
requirements → architecture → planning all succeeded locally through gates 1 and
2, with every cloud tier skipped at `latency_ms: 0`. The run then sat in the
planning repair loop: each repair is another ~5 minute local call, and the
machine was in sustained swap (9.5GB of 10GB, 14% memory free, `llama-server`
at 2.3% CPU — stalled on paging rather than computing). The routing claim is
proven — 100% of calls served locally, zero cloud requests, attribution correct
in metrics — but "completes end-to-end" is **not** demonstrated on this
hardware, and should not be claimed until it is. On 8GB the honest summary is
that local models keep the pipeline *alive*, not that they get it *finished*.
