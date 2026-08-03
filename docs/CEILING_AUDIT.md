# Output Ceiling Starvation Audit

**Dated: 2026-08-03.** Computed by `backend/scripts/audit_ceilings.py` over
`backend/metrics.db` rows from 2026-07-19 → 2026-08-03 (the store's full
history). Test-suite and fault-injection rows are excluded by project-id prefix
(`test*`, `e2e`, `restart`, `smoke*`, `stale*`, `t-trunc`, `cachetest`,
`local-tier-check`, NULL) — the offline suites write into the same metrics.db
as real runs and had planted, among other things, literal truncation fixtures
under `t-trunc` and 300+ fault-injected rows under `e2e`/`restart`.

Context: Improvement 02's Task 0 found QA's 3,000-token ceiling starving its
gemini-2.5-flash answers (2,400–2,740 reasoning tokens spent before any answer
text), surfacing not as truncation but as an **error + silent failover to
groq** — the scarce pool. This audit asks whether any other agent has the same
defect.

## Detection rule (ponytail #1)

Three signals, in order of authority:

1. **`truncated` column** — the provider's own `finish_reason='length'`,
   recorded per attempt since Day 26. Exact; authoritative when present.
2. **Near-ceiling ok calls** — completion within 2 % (floor 16 tokens) of the
   configured ceiling without a length flag. Suspect only: doc-writing agents
   legitimately fill any budget.
3. **Primary-error-then-failover** — the gemini thinking signature; cause is
   not recorded in history, so it needs interpretation (or, going forward, the
   failover accounting added in this same change).

Headroom rule: reasoning-model agents get ~1.2 × the worst observed *complete*
total (reasoning + answer) — the rule that set QA at 6,000. Direct-model agents
keep measured headroom; saturated doc-writers keep their Day 26 length
controls. Agents with no usable history keep conservative defaults and rely on
the truncation flag + failover accounting rather than spending live calls —
they all run direct (non-thinking) primaries, the class this defect does not
target first.

## Audit table (real-run rows only)

| agent | primary | ceiling | measured max | p95 | trunc | near-ceil | prim-err | verdict |
|---|---|---|---|---|---|---|---|---|
| research | gemini-flash (thinking) | 4500 | 4,368 | 4,368 | 0 | 0 | 0 | SATURATED (by design) |
| requirements | gemini-flash (thinking) | 4500 | 4,496 | 4,496 | 0 | 1 | 0 | SATURATED (by design) |
| architecture | groq llama-3.3 | 12000 | 11,996 ¹ | 11,996 | 0 | 2 | 0 | SATURATED (by design) |
| planning | gemini-flash (thinking) | 4500–32000 dynamic | 26,894 | 26,894 | 0 | — | 0 | CLEAN (1.19× cap headroom) |
| frontend_code | groq llama-3.3 | 1500/file | 829 (groq) · 1,496 (gemini ²) | 911 | 0 | 2 ² | 0 | CLEAN on primary; fallback-tier suspect ² |
| frontend_review | gemini-flash (thinking) | 2000 → **4000** | 1,996 (all truncated) | 696 | **22** | 0 | 0 | **STARVED — fixed** |
| backend_code | groq llama-3.3 | 1500/file | — | — | 0 | 0 | 0 | NO_DATA ³ |
| database | groq llama-3.3 | 2500/file | — | — | 0 | 0 | 0 | NO_DATA ³ |
| qa | gemini-flash (thinking) | 3000 → 6000 (Impr. 02) | 4,949 (gemini) · 32,768 (nemotron era) | — | 0 | 1 | 2 | STARVED at 3000 — **fixed 2026-08-03**, 1.21× headroom now |
| devops | groq llama-3.3 | 2000/file | — | — | 0 | 0 | 0 | NO_DATA ³ |

¹ Gemini-era rows (Jul 19–20); the current groq routing has no real
architecture history yet.
² The two near-ceiling frontend_code rows (1,496/1,495) are on the **gemini
fallback tier** — see "Fallback-tier interaction" below.
³ Every real-pipeline attempt for these agents was rate-limited (TodoSimple
quota starvation); the only completions come from standalone dev scripts
(database ≤ 722, devops ≤ 64, backend ≤ 96 tokens).

## Findings and actions

1. **frontend_review was starving RIGHT NOW — the predicted second instance.**
   Every gemini review ever recorded (22/22) hit the ceiling: 21 at the
   Improvement-01-era 700, and one at 1,996 against the *raised* 2000 ceiling
   on 2026-08-01 — i.e. the Improvement-01 fix (700 → 2000) was sized for a
   direct model and no gemini review has **ever** completed. Every complete
   verdict in history came from the groq fallback (≤ 335 tokens) — reviewer
   traffic silently landing on the scarce pool, exactly the QA failure shape.
   **Action: `REVIEW_MAX_TOKENS` 2000 → 4000** (basis: reasoning ≥ 1,996
   observed cut, same-model QA reasoning 2,427–2,740, complete answer ≤ 335;
   1.2 × (2,740 + 335) ≈ 3,690 → 4000). Pinned by test. The reviewer is
   default-off, so this changes no default-run cost.
2. **frontend_code starves under fast mode.** Largest measured complete file:
   829 tokens (TaskForm.jsx, groq). The fast-mode floor was 800. **Action:
   `FAST_MODE_FLOOR` 800 → 1000** (1.2 × 829 ≈ 995), pinned in both profiles
   by the parameterised test.
3. **Saturated ≠ starved.** research/requirements/architecture run at their
   ceilings *by design* (Day 26 length controls: a doc-writer fills any
   budget). Left unchanged deliberately; raising them buys longer documents
   for more tokens, which is a quality/cost trade, not a defect fix.
4. **Ceilings are single-valued but chains are not.** `max_tokens` applies to
   every tier of the fallback chain, and four agents (the coders, devops,
   architecture) have a *thinking* model as their fallback behind a ceiling
   sized for their direct primary. The two 1,49x gemini frontend_code rows are
   this exact interaction (predating the truncation column, so unflagged).
   NOT fixed today: raising coder ceilings to ~4k for the fallback's benefit
   would also raise the groq admission cost of every primary call (groq counts
   `max_tokens` against TPM at admission), risking more 429s in the phase that
   is already quota-starved. Recorded as a known limitation; the truncation
   flag + failover accounting now make it visible when it bites. Fixing it
   properly (per-tier budgets) is its own scoped change.
5. **The test suites write into the real metrics.db.** Discovered during this
   audit; excluded by prefix in the script. Candidate cleanup for a future
   change: point the suites at a temp metrics path so audit queries never need
   a denylist.

## Pinning

`backend/tests/test_token_budgets.py::test_ceilings_cover_measured_requirements`
holds the measured-requirement table and asserts every agent's effective
ceiling covers it in the **default and fast-mode profiles**. When routing or an
output requirement changes, that table is what gets updated — same discipline
as [PROVIDERS.md](PROVIDERS.md).
