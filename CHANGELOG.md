# Changelog

## v1.0.0 — Day 30

The first tagged release. Built over 30 days as a daily-increment project.

### What v1.0 is

A human-gated, nine-agent pipeline that turns a project brief into a reviewed
project scaffold. Research, requirements, architecture, a task plan, generated
frontend/backend/database code, deterministic validation, an LLM QA pass and
devops files — with four human approval gates where you read, edit, or reject
each stage before the next one builds on it.

It runs as a one-command containerised stack, streams every agent's progress
live, checkpoints continuously so nothing is lost to a reload or a crash, fails
over across three cloud providers, degrades to local models when cloud quota is
spent, and records per-attempt metrics for every call it makes.

### What v1.0 is not

**It does not build finished applications.** It produces a correctly-structured
starting point that you review and complete.

The one fully scored run measured **21.9% of planned files "usable"** — file
exists, parses, imports resolve, not a stub. That run was starved of provider
quota (188 of 233 attempts were rate-limit failures), so it measures free-tier
throughput more than model capability. It is nonetheless the honest number this
project can defend, and **no run has been measured under unconstrained quota.**
Treat any "60–80% complete" framing of this class of tool as unproven.

What *is* consistently good is the structure: the plan, the architecture, the
folder layout, and the boilerplate.

### Known boundaries you will hit

- **Free-tier quota is the binding constraint.** One simple project consumed
  193,409 tokens and 233 LLM attempts. The pipeline cannot reliably complete
  one project per day on free tiers.
- **Local models do not replace cloud on modest hardware.** 30–50× slower, and
  on 8GB it has never completed a full run.
- **Complex, real-time and heavily stateful designs sit at or past the
  ceiling.** Aim at conventional CRUD applications.

Full detail, with the measurements behind each: [ROADMAP.md](ROADMAP.md).

### Where it points next

Level 1 of the vision — structured, human-approved, not autonomous — is what
shipped, and the gates are the design rather than a limitation on the way to
something else. The nearest credible next steps are splitting the planning call
(currently ~26,900 tokens in one request, and the single point where runs die),
constraining plan size at the source, and coder-critic review loops for
cross-file consistency. See [ROADMAP.md](ROADMAP.md).

### Verification

Every subsystem was checked against the containerised stack and recorded:
[docs/RELEASE_CHECKLIST.md](docs/RELEASE_CHECKLIST.md) — 14/14 automated suites
green, plus per-item pass/fail across the REST surface, the container boundary
and the live pipeline, with the four items that were *not* verified stated
rather than omitted.

### Added on Day 30

- `README.md` front door, `docs/USAGE.md`, `docs/ARCHITECTURE.md`
- Three brief templates and a Brief Best Practices panel in the New Project form
- `backend/tests/run_all.py` — single-command regression gate
- `ROADMAP.md`, `docs/RELEASE_CHECKLIST.md`, this changelog
- Build journal archived to `docs/build-journal/`
- Fixed: `.env.example` documented the wrong env file path (a first-run blocker)
