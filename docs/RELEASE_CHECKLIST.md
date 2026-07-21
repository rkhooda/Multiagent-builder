# v1.0 Release Health Check

"v1.0 shipped" is a claim, so it gets evidence. This is the recorded pass/fail
across every major subsystem built over the 30 days, run against the
**containerised stack** (`docker compose`, nginx on `:3001`) — the artifact a
user actually gets, not the dev server.

**Date:** Day 30
**Stack:** `docker compose` — backend + frontend, host Ollama at
`host.docker.internal:11434`
**Provider state during the check:** Gemini daily quota exhausted; Groq at
100.8% of its 100k daily token budget. This is unhelpful for the live run and
*ideal* for testing degradation paths, which is most of what a release check
should be exercising.

---

## Method (ponytail #2)

Three rules, chosen so the check is meaningful rather than theatre:

1. **Automated suites first, as the cheap regression gate.** They run in 45
   seconds and cover far more than any manual pass.
2. **The manual checklist covers only what no suite covers.** Re-testing what
   `test_validation.py` already asserts is theatre. What the suites cannot
   reach is the live HTTP surface, the gate state machine, and the
   container/persistence boundary — so that is all the manual list contains.
3. **Maximise $0 checks against persisted projects.** Free-tier quota is the
   scarcest resource in this project; anything verifiable against already-
   generated output must not spend a token.

**Pass bar:** every automated suite green, and every manual item either PASS
with recorded evidence, or FAIL and carried into
[ROADMAP.md](../ROADMAP.md) as a stated known limitation. No silent gaps.

---

## 1. Automated regression gate

`cd backend && ./venv/bin/python tests/run_all.py` — **15/15 green** (14 at the start of the day, plus test_abandoned_projects.py added by finding 4 below).

| Suite | Result |
|---|---|
| `test_folder_map.py` | PASS — 5 passed |
| `test_import_fixer.py` | PASS — 14 passed |
| `test_lifecycle.py` | PASS |
| `test_llm_cache.py` | PASS — 13 passed |
| `test_metrics_attribution.py` | PASS — 16 passed |
| `test_metrics_store.py` | PASS — 35 passed |
| `test_ollama_routing.py` | PASS — 15 passed |
| `test_parallel_runner.py` (Day 20 fake-generator) | PASS — 12 passed |
| `test_prompt_regression.py` (Day 21 golden) | PASS — 7/7 golden outputs |
| `test_rate_limits.py` | PASS — 9 passed |
| `test_score_project.py` | PASS — 18 passed |
| `test_token_budgets.py` | PASS — 9 passed |
| `test_validation.py` (Day 22 crafted-breakage) | PASS — 20 passed |
| `test_validation_pass.py` | PASS — 14 passed |
| `test_abandoned_projects.py` (added Day 30) | PASS — 7 passed |

The 12 **live** suites (`--live`) are excluded from the gate by design: they
issue real provider requests, so under exhausted quota they fail on 429s rather
than on any code defect. Verified as such — `test_pipeline.py` and
`test_research_agent_sections.py` both fail with
`Rate limit reached … tokens per day (TPD): Limit 100000, Used 99077`, which is
a quota statement, not a regression.

## 2. Containerised stack

| # | Check | Result | Evidence |
|---|---|---|---|
| 2.1 | Stack builds and boots | **PASS** | backend `Up (healthy)`, frontend `Up` |
| 2.2 | Health through nginx | **PASS** | `GET :3001/api/health` → `{"status":"ok","version":"0.1","js_validation":true,...}` |
| 2.3 | JS validation tooling present in image | **PASS** | `js_validation: true` (Day 28 node-in-backend-image) |
| 2.4 | WebSocket through nginx | **PASS** | `ws://localhost:3001/ws/projects/{id}` connects, first frame `{"type":"heartbeat"}` |
| 2.5 | Frontend served and SPA routes resolve | **PASS** | `GET :3001/new` → 200, page renders |
| 2.6 | Ollama tier detected by the container | **PASS** | `/api/health` → `local_models: ["phi4-mini","qwen3:4b"]` after pointing `OLLAMA_BASE_URL` at `host.docker.internal` |
| 2.7 | Port collision documented | **PASS** | `:3000` occupied by an unrelated process; `FRONTEND_PORT=3001` works and is now in the README |

## 3. REST surface ($0, against persisted projects)

| # | Check | Result | Evidence |
|---|---|---|---|
| 3.1 | List projects | **PASS** | `GET /api/projects` → 200, array |
| 3.2 | Get project | **PASS** | 200 with full serialized state |
| 3.3 | Unknown project | **PASS** | → 404 |
| 3.4 | File listing | **PASS** | 56 files / 1,514 lines |
| 3.5 | File content | **PASS** | → 200 |
| 3.6 | **Path traversal rejected** | **PASS** | `?path=../../../etc/passwd` → **400** |
| 3.7 | ZIP download | **PASS** | → 200, 29,058 bytes, 56 files in archive |
| 3.8 | PDF export | **PASS** | → 200, 7,165 bytes, valid `PDF document, version 1.4, 3 pages` |
| 3.9 | Metrics endpoint | **PASS** | 94 attempts, 57 ok, 37 failed, 193,409 tokens |
| 3.10 | Restart preview | **PASS** | `?from_stage=planning` → discards `[planning, code]`, keeps `[research, requirements, architecture]`, 41 files to archive, cost estimate present |
| 3.11 | Recover guard on a running project | **PASS** | → **409** `"Project is 'running', not in a recoverable error state"` |
| 3.12 | Delete cascade | **PASS** | → `cancelled_run: true, metrics_rows_deleted: 106, checkpoint_deleted: true, files_deleted: true, row_deleted: true`; output dir gone from disk; subsequent `GET` → 404 |
| 3.13 | Delete is idempotent | **PASS** | re-DELETE → 404, no error |
| 3.14 | Invalid gate decision rejected | **PASS** | `{"decision":"bogus"}` at `human_gate_1` → **400** `"must be one of ['approve','back','edit','reject']"` |
| 3.15 | Non-editable field rejected | **PASS** | `PATCH /state {"field":"research_report"}` → **400**, allowed list returned |
| 3.16 | Gate reject cancels the run | **PASS** | `{"decision":"reject"}` at gate 1 → project `cancelled`; all prior artifacts retained (research 9,741 chars, requirements 7,117 chars) and restartable |

## 3b. Container restart / persistence

`docker compose down` → `up --build`, with two projects in flight.

| # | Check | Result | Evidence |
|---|---|---|---|
| 3b.1 | Projects survive a full stack restart | **PASS** | both projects present after `down`/`up`; bind mounts under `./data` intact |
| 3b.2 | An interrupted run is detected as such | **PASS** | the running project returned `status: running, interrupted: true` — the derived field correctly distinguishes it from a live run |
| 3b.3 | Resume from checkpoint after restart | **PASS** | `POST /resume` → `{"status":"resumed_from_checkpoint","resuming_at":"research"}` |
| 3b.4 | Deletion is durable across restart | **PASS** | deleted project still 404 after rebuild |
| 3b.5 | Gate-paused project holds its gate | **PASS** | second project still at `human_gate_1` after restart |

## 4. Pipeline behaviour (live)

Exercised via the Day 30 capstone run — see
[INTEGRATION_RESULTS.md](INTEGRATION_RESULTS.md) for the full record.

| # | Check | Result | Evidence |
|---|---|---|---|
| 4.1 | Project creation from a brief | **PASS** | `POST /api/projects` → `project_id`, status `running`, stage `research` |
| 4.2 | Provider chain fails over | **PASS** | research: gemini ×2 → groq ×2, each classified `rate_limit` in the per-attempt log |
| 4.3 | **Quota exhaustion pauses cleanly** | **PASS** | run halted as `rate_limited` with `failed_agent: research`; no crash, no partial corruption, checkpoint intact |
| 4.4 | Error recovery — retry | **PASS** | `POST /recover {"action":"retry"}` → `{"status":"retrying","agent":"research","retry_count":1}` |
| 4.5 | Rate-limit → local reroute | **PASS** | after provisioning Ollama, the chain resolved `ollama/qwen3:4b` for every agent type and the retried run proceeded on local |
| 4.6 | Per-attempt observability | **PASS** | every attempt logs model, outcome, latency, `context_chars`, tokens, cache state |
| 4.7 | Daily budget tracking | **PASS** | `groq used 100799 / limit 100000 (100.8%)`, `gemini 99308 / 1000000`; local reported `tracked: false` |

## 5. Findings raised by this check

Three things this check surfaced that were **not** visible to any test suite:

1. **Degradation requires provisioning.** With no local model pulled, cloud
   exhaustion is a hard pause, not a graceful degradation. The claim "the
   pipeline degrades instead of failing" is only true once Ollama is actually
   running with a model. Now stated plainly in the README and ROADMAP.
2. **The local context window silently truncates.** `qwen3:4b` runs a 4k
   context; the research prompt is ~12,800 characters, and the Ollama server
   logs `slot context shift … n_discard = 2045` — it is *discarding earlier
   prompt content* to fit. Output is produced, so nothing errors, but the model
   never saw the whole prompt. This is a quality trap distinct from the Day 29
   thinking-token finding and is carried into the roadmap.
3. **`.env.example` documented the wrong path.** It told the user to copy to a
   root `.env`; compose reads `backend/.env`. Fixed in `71c4911` — a first-run
   blocker that no test could have caught.
4. **Deleting a running project did not stop its workers.** `task.cancel()`
   cannot reach a thread dispatched by `asyncio.to_thread`, so a deleted
   project kept issuing provider calls for minutes and regrew its own metrics
   rows from 0 to 5 while `GET` returned 404 — spending the scarcest resource
   in the system on files nobody could download. **Fixed** at the `call_llm`
   choke point (`a5a8ce7`) with 7 new assertions; verified after redeploy as
   **zero** orphan calls following a restart.

### A tester error worth recording

The check for "invalid gate decision" was run twice. The second attempt used
`reject`, which **is** valid at gate 1, and therefore cancelled a persisted
test project rather than being refused. The system behaved correctly; the test
was wrong. Recorded because the release log should show what actually happened,
and because it independently confirmed 3.16 — cancel retains every prior
artifact and leaves the project restartable.

## 6. Not verified

Stated rather than quietly omitted:

- **Gate edit / back-navigation / restart-from-stage on a live run.** The
  capstone run did not reach a gate within the release window under exhausted
  cloud quota. The underlying routing is covered by `test_pipeline.py` and the
  gate state machine by `test_lifecycle.py`, and all three paths were exercised
  on Days 13–16 and 24, but they are **not** re-verified live today.
- **LangSmith trace visibility.** Tracing is off in this deployment
  (`LANGCHAIN_TRACING_V2=false`, placeholder key). Local metrics — the always-on
  path — are verified above.
- **Cache hit path end-to-end.** `test_llm_cache.py` (13 assertions) covers it
  offline; a live hit needs a repeated call the quota did not allow.

*(Docker down/up persistence moved to §3b — it was verified after all, when
redeploying the orphan-worker fix made a restart necessary anyway.)*
