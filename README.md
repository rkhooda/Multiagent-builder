# Multi-Agent Product Builder

Turns a one-paragraph project brief into a reviewed, scaffolded software
project. Nine specialised LLM agents run in a fixed sequence — research →
requirements → architecture → planning → frontend code → backend code →
database → QA → devops — and the pipeline **pauses at four human approval
gates** so you read, edit or reject each stage before the next one builds on
it. You get research notes, a requirements doc, an architecture design, a task
plan, and a generated project tree you can browse, fix and download as a ZIP.

**What this is not:** it does not build finished applications. It produces a
correctly-structured *starting point* that you review and complete. Output
quality tracks brief quality and project complexity, and on free-tier models it
degrades honestly rather than silently — see
[What to actually expect](#what-to-actually-expect) before your first run.

---

## Quick start

**Prerequisites:** Docker Desktop (or Docker Engine + Compose v2), and at least
one free LLM API key. Nothing else — no Python, Node or local installs.

```bash
git clone <this-repo> && cd multiagent-builder
make start            # creates backend/.env, builds, and runs
```

The first run stops and tells you to add your keys. Open `backend/.env` and
fill in at least one of:

```
GEMINI_API_KEY=...      # https://aistudio.google.com/apikey
GROQ_API_KEY=...        # https://console.groq.com/keys
OPENROUTER_API_KEY=...  # https://openrouter.ai/keys
```

All three are free tiers. Add **all three** if you can — the router fails over
between providers, and one key alone will not carry a full run (see
[Provider limits](#provider-limits-read-this)).

```bash
make start            # again, now with keys
```

Open **http://localhost:3000**. Check it is alive:

```bash
curl http://localhost:3000/api/health
# {"status":"ok","version":"0.1","js_validation":true}
```

| Command | What it does |
|---|---|
| `make start` | Build if needed, then run |
| `make stop` | Stop. Your data in `./data` is untouched |
| `make logs` | Follow the logs |
| `make ollama` | Also start the optional local-model service |
| `make clean` | Remove containers and images. Never touches `./data` |

Everything you generate persists in `./data` — one directory to back up.

> **Port 3000 already in use?** It is a crowded default. Run
> `FRONTEND_PORT=3001 make start`, or put `FRONTEND_PORT=3001` in a `.env` at
> the repo root (that root file is read by Compose itself, and is separate from
> `backend/.env` where your API keys go).

## Your first brief

1. Click **New Project**.
2. Pick one of the three **starter templates** (SaaS Web App, Internal Tool /
   Dashboard, API Backend) and edit it. Do not skip this — the brief is the
   single biggest lever on output quality, and the templates encode the shape
   that works. Open **Brief Best Practices** on the form for the good-vs-bad
   examples.
3. Submit, and watch the agents stream in live.
4. **Gate 1** pauses after requirements. Read them, and either approve, edit
   with feedback, or send the pipeline back a stage.
5. Same at **Gate 2** (architecture) and **Gate 3** (the task plan — a full
   editor: cut tasks, fix dependencies, then approve).
6. Code generation runs. **Gate 4** is the final review: browse the files, read
   the QA report, request per-file AI fixes, and download the ZIP.

A good first brief is 5–10 sentences naming target users, at most 8 core
features, real constraints, and — importantly — what *not* to build. A
one-liner produces a plan the models cannot serve.

Full walkthrough: **[docs/USAGE.md](docs/USAGE.md)**.

## What to actually expect

This project has measured itself throughout, and the numbers are not
flattering. Read these before judging a run:

- **The gates are the product.** The value is a structured, inspectable first
  pass with a human veto at every consequential step — not autonomous coding.
  Every stage is correctable before the next one depends on it.
- **Complex projects degrade.** Real-time, heavily stateful or
  multi-user-collaborative designs sit at or past the ceiling. The pipeline
  keeps making progress rather than dying, but expect a thinner result.
- **Measured output quality is lower than you would guess.** The one scored
  full run ([Day 25](docs/INTEGRATION_RESULTS.md)) reached **21.9% of planned
  files "usable"** — where usable means the file exists, parses, its imports
  resolve, and it is not a stub. That run was *starved of provider quota*, so
  it measures free-tier throughput more than model capability, but it is the
  honest number this repo can defend. No run has yet been measured under
  unconstrained quota.
- **Planning over-decomposes.** A todo app produced a 96-task, 95-file plan.
  Gate 3 exists so you can cut it down; use it.

### Provider limits (read this)

Free tiers are the binding constraint, and it is not close:

| Provider | Limit that binds | Effect |
|---|---|---|
| Gemini 2.5 Flash | ~20 requests/day/model | Exhausts partway through one run |
| Groq llama-3.3-70b | 100,000 tokens/day | The ceiling that usually stops work |
| OpenRouter free | ~50 requests/day | Free model slugs also vanish without notice |

**The pipeline cannot reliably complete one project per day on free tiers
alone.** A single simple project issued 233 LLM attempts. Plan around it:
run one project at a time, use Gate 3 to cut the plan down, and enable Fast
Mode for cheaper runs.

An optional **local model tier** (Ollama, `make ollama`) removes the quota
ceiling entirely and costs nothing — but it is slow, and on an 8GB machine it
has not completed a full run. Treat it as a way to keep going when the cloud
is spent, not as a replacement. Details in [docs/USAGE.md](docs/USAGE.md).

## Documentation

| Doc | For |
|---|---|
| **[docs/USAGE.md](docs/USAGE.md)** | Writing briefs, the four gates, reading the QA report and metrics, the run modes, using the output |
| **[docs/ARCHITECTURE.md](docs/ARCHITECTURE.md)** | How the pipeline works, the state object, adding or changing an agent |
| [docs/OBSERVABILITY.md](docs/OBSERVABILITY.md) | Metrics store and LangSmith tracing |
| [docs/INTEGRATION_RESULTS.md](docs/INTEGRATION_RESULTS.md) | Measured quality baselines and known limits |
| [docs/QUALITY_BASELINE.md](docs/QUALITY_BASELINE.md) | Defect taxonomy and layer attribution |
| [ROADMAP.md](ROADMAP.md) | Current limitations and where this goes next |
| [docs/RELEASE_CHECKLIST.md](docs/RELEASE_CHECKLIST.md) | The v1.0 health check — per-subsystem pass/fail evidence |
| [docs/build-journal/](docs/build-journal/) | The 30-day build log, kept for history |

## Repository layout

```
backend/app/graph/      LangGraph pipeline: nodes, gates, routing, state
backend/app/agents/     One module per pipeline stage
backend/app/llm_router.py   Provider chain, retries, budgets, rate limits, cache
backend/app/routers/    REST API and the WebSocket event stream
prompts/                One system prompt per agent — the tuning surface
frontend/src/           React UI: live event stream and the gate approval screens
docs/                   Everything above
data/                   Your databases and generated projects (git-ignored)
```

## Development

Running without Docker, the test suites, and the prompt-change protocol are
covered in [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md). The short version:

```bash
cd backend && python -m venv venv && ./venv/bin/pip install -r requirements.txt
./venv/bin/python tests/run_all.py        # 15 offline suites, ~60s, no API calls
```

## Status

**v1.0** — see [CHANGELOG.md](CHANGELOG.md) for what that does and does not
mean, and [ROADMAP.md](ROADMAP.md) for the honest limitations and what comes
next.
