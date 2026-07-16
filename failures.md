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
