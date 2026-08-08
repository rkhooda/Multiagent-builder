"""Phase 4 of Build Verification: a retroactive baseline across every
persisted project already on disk. Zero LLM cost — only CPU and Docker time.
Writes docs/BUILD_VERIFICATION_BASELINE.md. See that document's own
Methodology section for the ponytail decision this pins.

Run from the HOST (repo root), same as score_project.py's own CLI:
    cd backend && python scripts/build_verify_baseline.py [project_id ...]
Omit project_id(s) to auto-discover every candidate under outputs/.

Reuses score_project() for file-tier numbers rather than re-deriving them.
The live install/build/boot data has a real environment constraint this
script exists to route around: score_project()/load_state() need
`backend/projects.db` — the real historical checkpoint database, visible
only when running on the host (DATA_DIR defaults to the backend directory
here; the `backend` CONTAINER sets DATA_DIR=/data, a different, near-empty
database — **/*.db is dockerignored on purpose, so a stale copy never bakes
into the image). But verify_target() needs the `backend` container's network
position to reach `sandbox` by service name. So: file/checkpoint reads and
outputs/ staging happen here on the host; the live ladder call happens via
`docker compose exec backend`, the exact pattern
tests/test_build_verify.py's _exec_ladder already proved for Phase 2.
"""
import json
import re
import shutil
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from score_project import OUTPUTS_DIR, load_state, score_project  # noqa: E402
from app.profiles import get_profile  # noqa: E402

REPO_ROOT = Path(__file__).resolve().parents[2]
DATA_OUTPUTS = REPO_ROOT / "data" / "outputs"

# pip and npm both mark their real error with a recognizable prefix, then
# print a much longer trailer afterward (a usage/help dump for npm's EUSAGE,
# a version-update nag for pip) — so tail[-1] lands in that trailer far more
# often than on the actual error. Anchoring on the prefix instead of trying
# to out-guess an ever-growing list of trailer shapes is what makes "top
# recurring failure causes" trustworthy: a Python traceback has no such
# prefix, but its OWN last line already is the real exception, which is the
# fallback below.
_ERROR_LINE = re.compile(r"^(ERROR:|npm error code\b)", re.IGNORECASE)
_NOISE_TAIL_MARKERS = (
    "[notice]", "you can rerun the command with", "a complete log of this run",
    "warning: the directory", "log files were not written",
)


def _meaningful_tail(text: str) -> str:
    lines = [ln.strip() for ln in (text or "").splitlines() if ln.strip()]
    for line in lines:
        if _ERROR_LINE.match(line):
            return line
    for line in reversed(lines):
        if not any(marker in line.lower() for marker in _NOISE_TAIL_MARKERS):
            return line
    return lines[-1] if lines else ""

# The exact denylist audit_ceilings.py already established for the same
# reason: the offline suites and fault-injection fixtures write into the same
# outputs/ tree as real runs. Reused, not reinvented.
TEST_PROJECT_PREFIXES = ("test", "e2e", "restart", "cachetest", "smoke",
                         "stale", "t-trunc", "local-tier-check")


def candidate_projects() -> list:
    if not OUTPUTS_DIR.is_dir():
        return []
    return sorted(
        p.name for p in OUTPUTS_DIR.iterdir()
        if p.is_dir() and not p.name.startswith(TEST_PROJECT_PREFIXES)
        and not p.name.startswith(".")
    )


def _stage_for_sandbox(project_id: str) -> Path:
    """The sandbox only ever sees ./data/outputs (docker-compose.yml's
    read-only mount); historical projects live in the repo-root outputs/
    score_project.py reads. Stage a copy, verify, remove — the same
    copy/verify/cleanup shape runner.make_workspace already uses one layer
    down, applied here to the SOURCE side."""
    dest = DATA_OUTPUTS / project_id
    if dest.exists():
        shutil.rmtree(dest)
    shutil.copytree(OUTPUTS_DIR / project_id, dest)
    return dest


def _run_live_ladder(project_id: str, profile_name: str) -> dict:
    """One docker compose exec call into the already-running `backend`
    container — verify_target() needs nothing from a checkpoint, only
    project_id and the profile's own declared targets, so this needs no
    database access at all, only the compose network position."""
    script = (
        "import json\n"
        "from app.profiles import get_profile\n"
        "from app.build_verify.ladder import verify_target\n"
        f"profile = get_profile({profile_name!r})\n"
        "out = {}\n"
        "for target in profile.verify_targets:\n"
        f"    out[target.name] = verify_target({project_id!r}, target)\n"
        "print(json.dumps(out))\n"
    )
    proc = subprocess.run(
        ["docker", "compose", "exec", "-T", "backend", "python3", "-c", script],
        cwd=REPO_ROOT, capture_output=True, text=True, timeout=1800,
    )
    if proc.returncode != 0:
        raise RuntimeError(f"exec failed: {proc.stderr[-2000:]}")
    return json.loads(proc.stdout.strip().splitlines()[-1])


def verify_one(project_id: str) -> dict:
    file_score = score_project(project_id)
    checkpoint = load_state(project_id)
    profile = get_profile(checkpoint.get("stack_profile", ""))

    targets_report = {}
    live_error = None
    if profile.verify_targets:
        staged = _stage_for_sandbox(project_id)
        try:
            targets_report = _run_live_ladder(project_id, profile.name)
        except Exception as e:  # noqa: BLE001 — one bad project must not stop the baseline
            live_error = str(e)
        finally:
            shutil.rmtree(staged, ignore_errors=True)

    return {
        "project_id": project_id,
        "project_name": file_score.get("project_name") or project_id,
        "profile": profile.name,
        "usable_pct": file_score["usable_pct"],
        "generated": file_score["generated"],
        "qa_issues_count": file_score["qa_issues_count"],
        "build_verification": {
            "enabled": bool(profile.verify_targets),
            "targets": targets_report,
            "live_error": live_error,
        },
    }


def render_markdown(results: list) -> str:
    lines = [
        "# Build Verification — Retroactive Baseline",
        "",
        "Every quality claim this system made before Build Verification was",
        "inferred — parsed, not run. This is the first honest measurement of",
        "what fraction of persisted projects actually install, build, and boot.",
        "",
        "## Methodology",
        "",
        "Candidates: every directory under `outputs/` NOT matching the prefix",
        "denylist `audit_ceilings.py` already established for the same reason",
        "(`test*, e2e*, restart*, cachetest*, smoke*, stale*, t-trunc*,",
        "local-tier-check*`) — offline-suite and fault-injection noise, not",
        "real generated projects. A project whose profile declares no verify",
        "targets (or predates Stack Profiles) reports unavailable, never a",
        "false pass. File-tier numbers are `score_project()`, unmodified —",
        "this baseline adds only the live install/build/boot calls it didn't",
        "already have. Zero LLM cost.",
        "",
        "## Results",
        "",
        "| project | profile | % usable (files) | build verified |",
        "|---|---|---|---|",
    ]
    for r in results:
        bv = r["build_verification"]
        if not bv["enabled"]:
            cell = "n/a (no verify targets for this profile)"
        elif bv["live_error"]:
            cell = f"unverified ({bv['live_error'][:80]})"
        else:
            all_pass = all(
                v.get("verdict") == "pass"
                for t in bv["targets"].values()
                for v in (t.get("tiers") or {}).values()
            ) and bool(bv["targets"])
            cell = "**all pass**" if all_pass else "not all passing"
        lines.append(f"| {r['project_name']} | {r['profile']} | {r['usable_pct']}% | {cell} |")

    lines += ["", "## Per-project detail", ""]
    for r in results:
        bv = r["build_verification"]
        lines.append(f"### {r['project_name']} (`{r['project_id']}`)")
        lines.append(f"profile: `{r['profile']}` — {r['generated']} files on disk, "
                     f"{r['usable_pct']}% usable, {r['qa_issues_count']} QA issues")
        if not bv["enabled"]:
            lines.append("- build verification: not applicable (profile declares no verify targets)")
        elif bv["live_error"]:
            lines.append(f"- build verification: unverified — {bv['live_error']}")
        else:
            for name, t in bv["targets"].items():
                tiers = t.get("tiers")
                if not tiers:
                    lines.append(f"- {name}: unverified ({t.get('unverified_reason', 'sandbox unavailable')})")
                    continue
                for tier, v in tiers.items():
                    tail_line = (_meaningful_tail(v.get("stderr") or v.get("logs") or "")
                                if v.get("verdict") not in ("pass", "skipped") else "")
                    lines.append(f"- {name}.{tier}: **{v.get('verdict')}**" + (f" — `{tail_line[:150]}`" if tail_line else ""))
        lines.append("")

    # Top recurring failure causes — the evidenced backlog input a future
    # repair-loop improvement would need, per the brief.
    causes = {}
    for r in results:
        for t in r["build_verification"].get("targets", {}).values():
            for tier, v in (t.get("tiers") or {}).items():
                verdict = v.get("verdict")
                if verdict not in ("pass", "skipped", None):
                    tail_line = _meaningful_tail(v.get("stderr") or v.get("logs") or "")
                    key = f"{verdict}: {tail_line[:100] if tail_line else '(no output captured)'}"
                    causes[key] = causes.get(key, 0) + 1
    lines += ["## Top recurring failure causes", ""]
    if not causes:
        lines.append("None — every live-verified target/tier passed, or nothing was live-verifiable.")
    else:
        for cause, count in sorted(causes.items(), key=lambda kv: -kv[1]):
            lines.append(f"- ({count}×) {cause}")
    lines.append("")

    return "\n".join(lines)


def main():
    ids = sys.argv[1:] or candidate_projects()
    print(f"[baseline] {len(ids)} candidate project(s): {', '.join(ids)}")
    results = []
    for pid in ids:
        print(f"[baseline] verifying {pid} ...")
        results.append(verify_one(pid))

    doc = render_markdown(results)
    out_path = REPO_ROOT / "docs" / "BUILD_VERIFICATION_BASELINE.md"
    out_path.write_text(doc)
    print(f"[baseline] wrote {out_path}")


if __name__ == "__main__":
    main()
