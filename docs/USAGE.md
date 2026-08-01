# Usage Guide

Everything from writing a brief to using the generated project. For how the
system works internally, see [ARCHITECTURE.md](ARCHITECTURE.md).

- [Writing a good brief](#writing-a-good-brief)
- [Starting a run](#starting-a-run)
- [The four gates](#the-four-gates)
- [Reading the QA report and quality banner](#reading-the-qa-report-and-quality-banner)
- [Reading the metrics panel](#reading-the-metrics-panel)
- [Run modes: Fast Mode, cloud-only, prefer-local](#run-modes)
- [When a run breaks](#when-a-run-breaks)
- [Using the output](#using-the-output)

---

## Writing a good brief

**This is the highest-leverage thing you control.** Every downstream artifact —
requirements, architecture, the task plan, every generated file — is derived
from the brief. A vague brief does not produce a vague-but-serviceable project;
it produces a plan the models cannot serve, and the failure shows up three
stages later as missing files.

The New Project form ships three **starter templates** (SaaS Web App, Internal
Tool / Dashboard, API Backend). Use one. They encode the structure below.

A good brief has five parts:

| Part | Why it matters |
|---|---|
| **What it is** — one or two sentences | Anchors every later stage |
| **Target users** | Drives the requirements agent's user stories |
| **Core features — at most 8** | The single biggest driver of plan size, and therefore of whether the run finishes |
| **Constraints** | Tech preferences, scale, auth model, deployment target |
| **Explicitly out of scope** | Prevents the planner inventing work you never wanted |

That last row is the one people skip and the one that pays most. The planner
over-decomposes by default — a todo app produced a **96-task, 95-file plan** —
so anything you do not exclude, you will be paying tokens to generate.

### Good vs bad

**Bad** — one vague line:

> A social app for sharing recipes.

Nothing here constrains anything. Target users, scale, auth, whether there is
a feed, comments, ratings, images, search — all of it gets invented, and the
plan balloons past what free-tier quota can generate.

**Good** — 5–10 sentences, bounded:

> A recipe-sharing web app for home cooks who want to keep and share family
> recipes. Users sign up with email, create recipes with ingredients, steps and
> one photo, and mark recipes public or private. Public recipes appear in a
> browsable list with search by title and tag. Users can save others' recipes
> to a personal collection.
>
> Constraints: React frontend, FastAPI backend, PostgreSQL, email/password auth
> only. Single-server deployment; expect hundreds of users, not millions.
>
> Out of scope: comments, ratings, following other users, notifications,
> meal planning, shopping lists, mobile apps, and any social feed.

Six features, named constraints, and seven things explicitly excluded. The
out-of-scope list alone removes dozens of tasks the planner would otherwise
generate.

### Ambition and the complexity ceiling

Output quality degrades with project complexity, and it is not a gentle slope.
Real-time collaboration, heavily stateful designs, and anything needing
sophisticated coordination between clients sit at or past the ceiling — the
pipeline keeps making progress rather than failing outright, but expect a
thinner, more skeletal result and more gaps to fill by hand.

The measured picture is in [INTEGRATION_RESULTS.md](INTEGRATION_RESULTS.md).
The short version: on free-tier quota, **CRUD-shaped applications with a
conventional stack are where this works best.** Aim there for your first run.

### Research sections

Three optional checkboxes add sections to the research report: Existing
Solutions & Competitors, Target Users, Market Risks. Always included regardless:
Problem Space, Technical Landscape, Execution Risks, Recommended Approach,
Confidence Score. Each optional section costs tokens — leave them off unless
you want them.

## Starting a run

Fill in **Project Name** and **Project Brief**, pick your research sections and
Fast Mode setting, and hit **Start Building →**. You land on the project detail
page and agents stream in live.

Runs are checkpointed continuously. Closing the tab, reloading, or restarting
the stack does not lose a run — reopen the project and it is where you left it.

## The four gates

The pipeline pauses at four points and waits for you. This is the product, not
an obstacle: everything downstream of a gate is built on what you approve at
it, so a correction here is enormously cheaper than one later.

Every gate offers **Cancel project** (two-step confirm). Gates 1–3 share the
same three-way vocabulary:

| Action | What happens |
|---|---|
| **Approve & continue** | Move to the next stage |
| **Request changes** (`edit`) | Regenerate *this* gate's document with your feedback |
| **Go back** (`back`) | Regenerate the *previous* stage, then flow forward and pause at **this same gate** again |

Both `edit` and `back` discard everything produced after the re-run target —
snapshotted first, so the diff toggles keep working. That is intentional: a
rewritten requirements doc must not leave a stale architecture behind it.

> **The retry soft cap.** After a stage has been regenerated 3 times, the
> button turns amber and warns that "repeated regeneration rarely converges" —
> suggesting you edit the output directly instead. It never blocks you. Heed
> it: if three attempts have not fixed it, the problem is usually in the brief
> or the previous stage, not in this one.

### Gate 1 — Research & Requirements

Two panels: the research report, and the requirements doc with a parsed tech
stack card (frontend, backend, database, auth, hosting, key libraries).

The requirements doc is **directly editable** — click Edit, change the text,
Save. This is often faster and more reliable than asking for a regeneration.

Check: are the requirements what you actually meant? Is the tech stack
something you want to work in? Both propagate into everything after this.

### Gate 2 — Architecture

Four panels when the document parses cleanly: folder structure, API endpoints,
database schema (as a Mermaid ER diagram, with the SQL in a collapsible block),
and security notes. If parsing fails you get the raw markdown instead — that
is a degraded render, not a broken run.

No inline editor here — use Request changes or Go back to Requirements.

Check: does the folder structure match how you would actually organise this?
Does the schema have the tables and relationships you expect? Architecture
errors are the expensive kind, because every generated file inherits them.

### Gate 3 — Implementation Plan

The most important gate, and a full editor. Tasks are grouped by phase
(frontend / backend / database / devops) and every one can be:

- **Unchecked** to skip it (archived as an audit trail, never generated)
- **Edited** — change the description inline
- **Removed** entirely
- **Added** — custom task with filename, filepath, description (50+ chars),
  complexity and dependencies

The sticky summary bar shows included task count per phase, complexity mix,
and a rough time estimate. **Preview Folder Structure** renders the file tree
your choices will produce.

Dependencies are real: `Requires` chips are clickable and turn red when they
point at an excluded task. **Approve is blocked while any included task
depends on an excluded one** — exclude a task with dependents and you are
offered "Exclude dependents too".

**This is where you control cost and completion.** The planner over-decomposes,
and free-tier quota will not generate 95 files. Cutting the plan to the tasks
you actually need is the difference between a run that finishes and one that
starves. Be aggressive.

### Gate 4 — Final Review

The output. A summary card (files generated, lines of code, QA issues by
severity, total pipeline time, models used), then two tabs:

**Files** — a folder tree with per-file QA issue badges and sizes, and a
syntax-highlighted preview. Each file has **🛠 Request AI Fix**: describe what
is wrong (QA findings are clickable chips that fill it in) and the file is
regenerated in place, with a diff toggle afterwards. **Capped at 3 fixes per
file** — past that, edit it yourself after downloading. Files that import the
fixed one are *not* regenerated; you are warned when that applies.

**QA Report** — findings by severity, each file path clickable to jump to it.

If some files failed to generate you get an explicit warning naming them.
They are written as placeholders, not silently omitted.

**⬇ Download Project ZIP** is in the header and always available. Then
**Mark project complete**.

> **PDF export is not here.** A one-to-two page handover summary lives on the
> completed-project view as **Export Summary**, after you mark the project
> complete. It works on partial and cancelled projects too.

## Reading the QA report and quality banner

Two different things check your project, and they are not interchangeable:

- **Validation** (`validation` stage) is plain Python — syntax parsing, import
  resolution, manifest checks. It is deterministic and always right about what
  it measures.
- **QA** is an LLM reviewing the code. It is useful and it is fallible.

### The quality-threshold banner

At Gate 4 you may see: *"Code quality below threshold: N% of files have
unresolved issues."*

This is from validation, not QA, so it is mechanical fact. The breakdown
toggle itemises: syntax errors (unrepaired), phantom imports, missing packages,
invalid JSON/YAML, failed or blocked generation — plus the affected file list.

**It warns; it never blocks.** Download stays enabled. What it is telling you
is *where* the gaps are, so you know what to fix first.

If it also says **"Repair budget exhausted"**, a run needed more automated
repairs than the ceiling allows. That usually points at the architecture or
the brief rather than at any individual file — worth a restart from an earlier
stage rather than 40 manual fixes.

The threshold is `QUALITY_THRESHOLD` in `backend/.env` (default `0.2`).

## Reading the metrics panel

Five tiles: total / input / output tokens, LLM wall-clock, and cache hit rate.
Then a per-agent time breakdown, a provider daily-budget bar, and a per-agent
table.

Three things to actually watch:

- **`Att.` vs `Calls`.** `Calls` counts successes; `Att.` counts every attempt
  including retries and failovers. When `Att.` goes amber it is far above
  `Calls` — you are being rate-limited and burning quota on retries.
- **Provider Daily Budget** turns red at 90% used. This is **process-wide, not
  per project** — it is your remaining budget across everything today.
- **Truncation warning** — *"N outputs hit the token ceiling and were cut off
  mid-content"*. Those files or documents are literally incomplete. Raise that
  agent's `LLM_MAX_TOKENS_{AGENT}` and regenerate.

Costs are not shown because everything here runs on free tiers.

## Run modes

### Fast Mode (per project, on the form)

Lighter token budgets and **no automated LLM repairs** — defects are still
detected and reported, just not fixed. Good for iterating on a brief or
checking that a plan looks right. Not for a final build.

> Do not combine Fast Mode with local models. Halved budgets plus a reasoning
> model that spends its allowance thinking produces **empty files**.

### LLM_MODE (server-wide, in `backend/.env`)

Not a form toggle — it is an environment variable, process-wide, and applies to
every run until changed.

| Value | Chain | Use when |
|---|---|---|
| `auto` (default) | cloud primary → cloud fallback → local | Normal use |
| `cloud-only` | cloud only; pause rather than degrade | A final build you will actually ship |
| `prefer-local` | local first, cloud behind it | Free unlimited iteration |

The header badge shows `Local` or `Cloud only` with the detected models — that
is how you confirm what the stack actually sees.

### Frontend decomposition and review (server-wide, in `backend/.env`)

Two independent switches that shape how frontend files are produced. **Both
default to off**, so out of the box you get exact v1.0 behaviour.

| Variable | Default | What it does |
|---|---|---|
| `DECOMPOSE_FRONTEND` | `false` | Split large pages into 2–5 section components plus a thin page shell, so each generation call gets a small precise job |
| `DECOMPOSE_COMPLEXITY_THRESHOLD` | `high` | Minimum task complexity at which a **page** is worth splitting. `medium` decomposes more aggressively, and costs proportionally more calls |
| `REVIEW_MODE` | `off` | `off` \| `selective` \| `all`. A second model judges a generated file against its spec; a failing file gets **one** targeted revision |

> **Why off by default.** These are complete and tested, not doubtful — but the
> A/B that would prove they raise quality needs about 460k tokens across two full
> pipeline runs, against a free-tier allowance of 100k/day on the provider that
> binds. It could not run. The keep/revert rule was fixed before any number was
> seen and requires a measured gain, so the feature ships **unproven** and does
> not become the default. See `docs/IMPROVEMENT_01_RESULTS.md`.

`selective` reviews page shells, shared primitives, files that already produced
validation warnings, and high-complexity tasks — roughly a quarter of a file
set, not all of it. `all` is for measurement only.

Two things worth knowing before you turn this up:

- **Revisions spend the repair budget.** They draw from the same
  `REPAIR_CEILING_PER_RUN` account as automated repairs, deliberately, so one
  file cannot quietly consume both. When the ceiling trips, remaining files ship
  unreviewed and the run says so.
- **The reviewer is routed to Gemini, not the coders' provider**, so review does
  not compete with generation for the pool that actually runs out first. Fast
  Mode withholds review entirely, exactly as it withholds repairs.

Reviewed files carry an eye mark in the Gate 4 file browser; revised ones carry
`↻`. Both are annotated live in the run feed.

### Local models (Ollama)

```bash
make ollama
docker compose exec ollama ollama pull qwen3:4b
```

The service starts with **no models**; the local tier stays inactive until you
pull at least one.

**Be realistic about this.** Local removes the quota ceiling and costs nothing,
and it is genuinely the difference between a run that stalls at a 429 and one
that keeps going. But it is roughly **30–50× slower** than cloud, and on an
8GB machine it has **not completed a full pipeline run** — small models fail at
the planning stage, which needs a large, strictly-structured JSON output. On
this hardware treat it as a fallback for individual stages, not a way to run
the whole pipeline. 16GB+ is where it becomes practical.

When any call ran locally you get an explicit banner in the metrics panel
naming the percentage and models, plus a `local` badge on each affected agent —
so you know **which artifact** to distrust, not merely that the run degraded.

## When a run breaks

Free-tier quota exhaustion is the normal failure, not an exceptional one.

A stalled run pauses as `error_paused` or `rate_limited` and offers:

- **Retry** — try the failed agent again. After 3 manual retries you get a cap
  warning.
- **Skip** — continue without that agent (refused for agents nothing can
  proceed without).
- **Cancel**.

If quota is the cause, waiting for the daily reset and retrying is usually
right — the checkpoint holds, and you resume rather than restart.

**Restart from a stage** (research / requirements / architecture / planning /
code generation) re-runs from that point on the same project. You get a preview
first: what is discarded, what is kept, and a cost estimate. Discarded files
move to `.archived/` rather than being deleted.

**Delete** removes a project across all four stores. It is a hard cascade
delete and cannot be undone.

## Using the output

The ZIP contains the generated tree under a folder named after your project,
including the devops files (Dockerfiles, CI config, a README).

Treat it as a **scaffold, not a finished application**:

1. Read the QA report and the validation breakdown first — they tell you what
   is known-broken before you run anything.
2. Check the files flagged as failed or placeholder. Those are gaps, not code.
3. Expect to install dependencies, wire config and env vars, and fix imports
   across module boundaries — cross-file consistency is the weakest area.
4. Get it running before adding anything. The structure is the deliverable; the
   last mile is yours.

What this reliably gives you is a correctly-shaped project with the boilerplate
written, decisions documented, and a plan you approved — not an application you
can deploy unread.
