# Build Verification — Retroactive Baseline

Every quality claim this system made before Build Verification was
inferred — parsed, not run. This is the first honest measurement of
what fraction of persisted projects actually install, build, and boot.

## Methodology

Candidates: every directory under `outputs/` NOT matching the prefix
denylist `audit_ceilings.py` already established for the same reason
(`test*, e2e*, restart*, cachetest*, smoke*, stale*, t-trunc*,
local-tier-check*`) — offline-suite and fault-injection noise, not
real generated projects. A project whose profile declares no verify
targets (or predates Stack Profiles) reports unavailable, never a
false pass. File-tier numbers are `score_project()`, unmodified —
this baseline adds only the live install/build/boot calls it didn't
already have. Zero LLM cost.

## Results

| project | profile | % usable (files) | build verified |
|---|---|---|---|
| NotesTags | react-fastapi | 15.4% | not all passing |
| HabitTest | react-fastapi | 17.2% | not all passing |
| TodoSimple | react-fastapi | 21.6% | not all passing |
| NotesTags Day15 | react-fastapi | 20.0% | not all passing |
| CRM system | react-fastapi | 65.5% | not all passing |
| attr-test | react-fastapi | 0.0% | not all passing |
| day18-3file-test | react-fastapi | 66.7% | not all passing |
| day18-fullphase | react-fastapi | 90.9% | not all passing |
| day19-fault-test | react-fastapi | 66.7% | not all passing |
| day19-fullphase | react-fastapi | 90.0% | not all passing |
| day19-pair-test | react-fastapi | 85.7% | not all passing |
| day22-chain | react-fastapi | 100.0% | not all passing |
| day22-live | react-fastapi | 100.0% | not all passing |
| day22-live-run | react-fastapi | 100.0% | not all passing |
| NotesTags | react-fastapi | 100.0% | not all passing |
| FreelanceInvoicer | react-fastapi | 18.6% | not all passing |
| SplitTest | react-fastapi | 2.1% | not all passing |
| Parcel Tracking API | node-express-api | 100.0% | n/a (no verify targets for this profile) |
| Parcel Tracking API | node-express-api | 37.5% | n/a (no verify targets for this profile) |
| Fernwood Studio | static-site | 100.0% | n/a (no verify targets for this profile) |
| Fernwood Studio | static-site | 100.0% | n/a (no verify targets for this profile) |
| Fernwood Studio | static-site | 100.0% | n/a (no verify targets for this profile) |

## Per-project detail

### NotesTags (`113cf67c-8c63-4713-bcc8-a5dd34e0b9d9`)
profile: `react-fastapi` — 12 files on disk, 15.4% usable, 2 QA issues
- backend.install: **fail_code** — `ERROR: Could not open requirements file: [Errno 2] No such file or directory: 'requirements.txt'`
- backend.build: **skipped**
- backend.boot: **skipped**
- frontend.install: **fail_code** — `npm error code EUSAGE`
- frontend.build: **skipped**
- frontend.boot: **skipped**

### HabitTest (`14e1209b-a8c1-48f9-a962-300534a8e093`)
profile: `react-fastapi` — 15 files on disk, 17.2% usable, 8 QA issues
- backend.install: **fail_code** — `ERROR: Could not open requirements file: [Errno 2] No such file or directory: 'requirements.txt'`
- backend.build: **skipped**
- backend.boot: **skipped**
- frontend.install: **fail_code** — `npm error code EUSAGE`
- frontend.build: **skipped**
- frontend.boot: **skipped**

### TodoSimple (`2901fb46-1154-4df2-85de-f8b0e5a7784b`)
profile: `react-fastapi` — 77 files on disk, 21.6% usable, 0 QA issues
- backend.install: **pass**
- backend.build: **fail_code** — `IndentationError: unexpected indent`
- backend.boot: **skipped**
- frontend.install: **fail_code** — `npm error code EUSAGE`
- frontend.build: **skipped**
- frontend.boot: **skipped**

### NotesTags Day15 (`341b1dc2-2ce7-4c79-a147-6ab45095e1fa`)
profile: `react-fastapi` — 13 files on disk, 20.0% usable, 2 QA issues
- backend.install: **fail_code** — `ERROR: Could not open requirements file: [Errno 2] No such file or directory: 'requirements.txt'`
- backend.build: **skipped**
- backend.boot: **skipped**
- frontend.install: **fail_code** — `npm error code EUSAGE`
- frontend.build: **skipped**
- frontend.boot: **skipped**

### CRM system (`87f7061b-a2a9-4c7a-94e7-882aa3dc7fd4`)
profile: `react-fastapi` — 99 files on disk, 65.5% usable, 0 QA issues
- backend.install: **fail_code** — `ERROR: Could not find a version that satisfies the requirement csv (from versions: none)`
- backend.build: **skipped**
- backend.boot: **skipped**
- frontend.install: **fail_code** — `npm error code EUSAGE`
- frontend.build: **skipped**
- frontend.boot: **skipped**

### attr-test (`attr-test`)
profile: `react-fastapi` — 6 files on disk, 0.0% usable, 0 QA issues
- backend.install: **fail_code** — `ERROR: Could not open requirements file: [Errno 2] No such file or directory: 'requirements.txt'`
- backend.build: **skipped**
- backend.boot: **skipped**
- frontend.install: **fail_code** — `npm error code EUSAGE`
- frontend.build: **skipped**
- frontend.boot: **skipped**

### day18-3file-test (`day18-3file-test`)
profile: `react-fastapi` — 3 files on disk, 66.7% usable, 0 QA issues
- backend.install: **fail_code** — `ERROR: Could not open requirements file: [Errno 2] No such file or directory: 'requirements.txt'`
- backend.build: **skipped**
- backend.boot: **skipped**
- frontend.install: **fail_code** — `npm error code EUSAGE`
- frontend.build: **skipped**
- frontend.boot: **skipped**

### day18-fullphase (`day18-fullphase`)
profile: `react-fastapi` — 11 files on disk, 90.9% usable, 0 QA issues
- backend.install: **fail_code** — `ERROR: Could not open requirements file: [Errno 2] No such file or directory: 'requirements.txt'`
- backend.build: **skipped**
- backend.boot: **skipped**
- frontend.install: **fail_code** — `npm error code EUSAGE`
- frontend.build: **skipped**
- frontend.boot: **skipped**

### day19-fault-test (`day19-fault-test`)
profile: `react-fastapi` — 6 files on disk, 66.7% usable, 0 QA issues
- backend.install: **pass**
- backend.build: **fail_code** — `ModuleNotFoundError: No module named 'app.models'`
- backend.boot: **skipped**
- frontend.install: **fail_code** — `npm error code EUSAGE`
- frontend.build: **skipped**
- frontend.boot: **skipped**

### day19-fullphase (`day19-fullphase`)
profile: `react-fastapi` — 10 files on disk, 90.0% usable, 0 QA issues
- backend.install: **pass**
- backend.build: **pass**
- backend.boot: **pass**
- frontend.install: **fail_code** — `npm error code EUSAGE`
- frontend.build: **skipped**
- frontend.boot: **skipped**

### day19-pair-test (`day19-pair-test`)
profile: `react-fastapi` — 7 files on disk, 85.7% usable, 0 QA issues
- backend.install: **pass**
- backend.build: **pass**
- backend.boot: **pass**
- frontend.install: **fail_code** — `npm error code EUSAGE`
- frontend.build: **skipped**
- frontend.boot: **skipped**

### day22-chain (`day22-chain`)
profile: `react-fastapi` — 1 files on disk, 100.0% usable, 0 QA issues
- backend.install: **fail_code** — `ERROR: Could not open requirements file: [Errno 2] No such file or directory: 'requirements.txt'`
- backend.build: **skipped**
- backend.boot: **skipped**
- frontend.install: **fail_code** — `npm error code EUSAGE`
- frontend.build: **skipped**
- frontend.boot: **skipped**

### day22-live (`day22-live`)
profile: `react-fastapi` — 2 files on disk, 100.0% usable, 0 QA issues
- backend.install: **fail_code** — `ERROR: Could not open requirements file: [Errno 2] No such file or directory: 'requirements.txt'`
- backend.build: **skipped**
- backend.boot: **skipped**
- frontend.install: **fail_code** — `npm error code EUSAGE`
- frontend.build: **skipped**
- frontend.boot: **skipped**

### day22-live-run (`day22-live-run`)
profile: `react-fastapi` — 3 files on disk, 100.0% usable, 0 QA issues
- backend.install: **fail_code** — `ERROR: Could not open requirements file: [Errno 2] No such file or directory: 'requirements.txt'`
- backend.build: **skipped**
- backend.boot: **skipped**
- frontend.install: **fail_code** — `npm error code EUSAGE`
- frontend.build: **skipped**
- frontend.boot: **skipped**

### NotesTags (`day23-baseline`)
profile: `react-fastapi` — 4 files on disk, 100.0% usable, 1 QA issues
- backend.install: **fail_code** — `ERROR: Could not open requirements file: [Errno 2] No such file or directory: 'requirements.txt'`
- backend.build: **skipped**
- backend.boot: **skipped**
- frontend.install: **fail_code** — `npm error code EUSAGE`
- frontend.build: **skipped**
- frontend.boot: **skipped**

### FreelanceInvoicer (`f1a063f8-f61f-4cbb-86c4-a825640ae552`)
profile: `react-fastapi` — 13 files on disk, 18.6% usable, 2 QA issues
- backend.install: **fail_code** — `ERROR: Could not open requirements file: [Errno 2] No such file or directory: 'requirements.txt'`
- backend.build: **skipped**
- backend.boot: **skipped**
- frontend.install: **fail_code** — `npm error code EUSAGE`
- frontend.build: **skipped**
- frontend.boot: **skipped**

### SplitTest (`ffd448ab-d9f3-42f5-81c0-92a3d4645db9`)
profile: `react-fastapi` — 1 files on disk, 2.1% usable, 0 QA issues
- backend.install: **fail_code** — `ERROR: Could not open requirements file: [Errno 2] No such file or directory: 'requirements.txt'`
- backend.build: **skipped**
- backend.boot: **skipped**
- frontend.install: **fail_code** — `npm error code EUSAGE`
- frontend.build: **skipped**
- frontend.boot: **skipped**

### Parcel Tracking API (`gen-express-v2`)
profile: `node-express-api` — 8 files on disk, 100.0% usable, 0 QA issues
- build verification: not applicable (profile declares no verify targets)

### Parcel Tracking API (`gen-node-express-api`)
profile: `node-express-api` — 8 files on disk, 37.5% usable, 0 QA issues
- build verification: not applicable (profile declares no verify targets)

### Fernwood Studio (`gen-static-site`)
profile: `static-site` — 5 files on disk, 100.0% usable, 0 QA issues
- build verification: not applicable (profile declares no verify targets)

### Fernwood Studio (`gen-static-site-v2`)
profile: `static-site` — 5 files on disk, 100.0% usable, 0 QA issues
- build verification: not applicable (profile declares no verify targets)

### Fernwood Studio (`gen-static-site-v3`)
profile: `static-site` — 5 files on disk, 100.0% usable, 0 QA issues
- build verification: not applicable (profile declares no verify targets)

## Top recurring failure causes

- (17×) fail_code: npm error code EUSAGE
- (12×) fail_code: ERROR: Could not open requirements file: [Errno 2] No such file or directory: 'requirements.txt'
- (1×) fail_code: IndentationError: unexpected indent
- (1×) fail_code: ERROR: Could not find a version that satisfies the requirement csv (from versions: none)
- (1×) fail_code: ModuleNotFoundError: No module named 'app.models'
