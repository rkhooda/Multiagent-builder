# Improvement 01 — Frontend section decomposition + reviewer-critic loop

Measured record for the first post-v1.0 capability change. Dated, append-only,
same discipline as `INTEGRATION_RESULTS.md`: hypothesis first, one change,
measure, keep or revert, record either way.

**Hypothesis.** Complex frontend files score lowest because one call writes a
whole page one-shot with no second opinion. Two bounded changes should raise
frontend quality: (a) decompose large pages into section-level tasks so each
call has a small, precise job, (b) a reviewer agent that judges a generated file
against its spec, with exactly one targeted revision when it fails.

**Keep/revert rule, fixed BEFORE any treatment number was seen.** Keep only if
all three hold:

1. frontend tier quality improves measurably (score_project.py, tier ≥ 4), AND
2. the extra LLM calls per run stay within the ceiling agreed in ponytail #2
   (≤ +35% total calls, ≤ +0% on the scarcest pool), AND
3. coherence warnings (Task 4, deterministic) do not rise.

If quality rises but coherence degrades, the UI contract is too thin — fix that
before accepting. If it costs more than it gains, revert and record why.

---

## Task 0 — Pre-flight and baseline (2026-08-01)

### Suites green before any generation behaviour changed

| Suite | Result |
|---|---|
| `backend/tests/run_all.py` (16 offline suites, Day 20/21/22 included) | **16/16 green** |
| `test_parallel_runner.py` — Day 20 fake-generator scheduler suite | 12 passed, 0 failed |
| `test_validation.py` + `test_validation_pass.py` — Day 22 crafted breakage | 20 + 14 passed, 0 failed |
| `test_prompt_regression.py` — Day 21 golden outputs | 7/7 pass, 0 API calls |
| `ab_prompt_test.py --rescore tests/fixtures/prompt_tuning/golden` | 7/7 still pass, 0 API calls |

### Baseline: `score_project.py`, offline, zero API cost

Scored from persisted output on disk plus the LangGraph checkpoint. This is the
number the change must beat.

**`2901fb46` TodoSimple** — the Day 25 simple run, the only persisted project
whose frontend phase actually produced files.

| Scope | Scored | Usable (tier 4) | % usable |
|---|---|---|---|
| Whole project | 96 | 21 | **21.6%** |
| **Frontend only** | **51** | **18** | **35.3%** |

> The whole-project figure was first recorded as 21.9% (21/95). Task 1 changed
> `planned_files()` to union the plan's filepaths rather than use them only as a
> fallback — required so decomposition's section components are not scored as
> "unplanned files", which would have made the treatment look worse by
> construction. That admits `backend/Dockerfile` to the planned set, so the
> denominator is 96 and the same 21 usable files read as 21.6%. **The frontend
> column — the number under test — is byte-identical before and after:** 51
> scored, 18 usable, 35.3%, same histogram.

Frontend tier histogram (the number under test):

| Tier | Count | Share |
|---|---|---|
| 0 missing | 4 | 7.8% |
| 1 present (parses? no) | 8 | 15.7% |
| 2 syntax ok, imports broken | 8 | 15.7% |
| 3 imports ok, stub/unplanned | 13 | 25.5% |
| 4 substantive | 18 | 35.3% |

Defect composition of the 33 non-usable frontend files:

- **13 tier-3 "stub"** — Day 20 failure placeholders. These are quota
  starvation, *not* code quality: the file was never generated at all.
- **8 tier-1** — all one defect, `Missing semicolon` at line 4, i.e. a fence or
  header artifact at the top of the file.
- **8 tier-2** — unresolved relative imports (`./api`, `./api.js`,
  `../common/NavLink`, `../taskService`) against files that were never
  generated. This is the *cross-file coherence* class that Task 4 targets.
- **4 tier-0** — build config (`package.json`, `vite.config.js`,
  `tailwind.config.js`, `postcss.config.js`) never generated.

### The medium baseline the plan assumed does not exist

Task 0 asked for "the Day 25 simple **and medium** persisted projects". There is
no medium project to score. Day 25 recorded the medium and complex runs as
`_not run_ — blocked: provider quota exhausted`, and Day 30's capstone halted in
`research` before writing a single file. Every other persisted project scored
below is a pre-Gate-3 halt, not a comparable run:

| Project | Planned | Generated | Usable | Note |
|---|---|---|---|---|
| `14e1209b` HabitTest | 86 | 15 | 17.4% | halted before frontend phase |
| `f1a063f8` FreelanceInvoicer | 70 | 13 | 18.6% | halted before frontend phase |
| `341b1dc2` NotesTags Day15 | 59 | 13 | 20.3% | halted before frontend phase |
| `113cf67c` NotesTags | 77 | 12 | 14.1% | halted before frontend phase |
| `ffd448ab` SplitTest | 48 | 1 | 2.1% | halted before frontend phase |

Each generated 12–15 files, all backend/database, and 0 substantive frontend
files. Their `% usable` is dominated by `missing`, so comparing a treatment run
against them would measure *how far the run got*, not *how good the frontend
was*. They are recorded here for completeness and excluded from the A/B.

**Consequence for Task 6:** the A/B baseline is `2901fb46`'s frontend column —
**35.3% usable, 51 files** — and the treatment must be a medium brief run
end-to-end for a like-for-like comparison. That is a live-run cost, and the
constraint that stopped Days 25 and 30 has not changed.

---

## Tasks 1–5 — Implementation

_Filled in as each lands._

## Task 6 — A/B result and verdict

_Filled in after the measurement._
