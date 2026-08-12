"""The three-tier verification ladder for one VerifyTarget: install -> build
-> boot, each skipped once a lower tier is not `pass`. Talks to the `sandbox`
service over HTTP only — never imports anything from backend/sandbox/, by
construction, matching the isolation boundary in
docs/SANDBOX_THREAT_MODEL.md. Any failure to reach the sandbox is
`unverified`, never `pass` — see that document's Verdict semantics section.
"""
import json
import os
import urllib.error
import urllib.request

from .classify import (FAIL_CODE, FAIL_ENVIRONMENT, MAX_ENV_RETRIES, PASS,
                        SKIPPED, TIMEOUT, UNVERIFIED, classify_failure)

SANDBOX_URL = os.environ.get("SANDBOX_URL", "http://sandbox:8100")


def _call(endpoint: str, payload: dict, timeout_s: int) -> dict:
    req = urllib.request.Request(
        f"{SANDBOX_URL}{endpoint}", data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json"}, method="POST",
    )
    with urllib.request.urlopen(req, timeout=timeout_s + 15) as resp:
        return json.loads(resp.read())


def _workdir(target, spec) -> str:
    """A tier's workdir is relative to its OWN target's root (e.g. install's
    "" under target.root="backend" -> "backend"; boot's "dist" under
    target.root="frontend" -> "frontend/dist")."""
    return "/".join(p for p in (target.root, spec.workdir) if p)


# Docker CLI's own convention: exit 125 means `docker run` itself failed
# (image pull, daemon error) before the command inside the container ever
# ran — a registry/infrastructure problem, not a defect in generated code.
# More reliable than pattern-matching stderr for it.
_DOCKER_ITSELF_FAILED_EXIT = 125


def _run_tier(workspace: str, target, spec) -> tuple:
    """One /run call, classified. Returns (verdict, detail_dict)."""
    try:
        result = _call("/run", {
            "workspace": workspace, "command": list(spec.command),
            "image": spec.image, "timeout_s": spec.timeout_s,
            "workdir": _workdir(target, spec), "env": spec.env,
        }, spec.timeout_s)
    except (urllib.error.URLError, OSError, TimeoutError) as exc:
        return UNVERIFIED, {"error": str(exc)}

    if result.get("timed_out"):
        return TIMEOUT, result
    if result.get("exit_code") == 0:
        return PASS, result
    if result.get("exit_code") == _DOCKER_ITSELF_FAILED_EXIT:
        return FAIL_ENVIRONMENT, result
    return classify_failure(result.get("stderr", "")), result


def _boot_tier(workspace: str, target, spec) -> tuple:
    try:
        result = _call("/probe", {
            "workspace": workspace, "command": list(spec.command),
            "image": spec.image, "port": spec.port,
            "health_path": spec.health_path, "ready_timeout_s": spec.ready_timeout_s,
            "workdir": _workdir(target, spec), "env": spec.env,
            "journey": bool(spec.journey),
        }, spec.ready_timeout_s)
    except (urllib.error.URLError, OSError, TimeoutError) as exc:
        return UNVERIFIED, {"error": str(exc)}

    if result.get("healthy"):
        return PASS, result
    if result.get("crashed"):
        return classify_failure(result.get("logs", "")), result
    return TIMEOUT, result  # never healthy, never exited -> the ready window ran out


def _journey_tier(boot_detail: dict) -> tuple:
    """The journey smoke, promoted out of the boot result into its own rung.

    Separate from `boot` on purpose. In project e8935f86 /health returned 200
    while every real request returned 500, so a ladder that stops at boot
    reports a healthy project that does not work. Folding this into the boot
    verdict would lose exactly the distinction it exists to draw.
    """
    journey = (boot_detail or {}).get("journey")
    if journey is None:
        return SKIPPED, {}
    if journey.get("unverified"):
        return UNVERIFIED, {"error": _first_detail(journey), **journey}
    if journey.get("ok"):
        return PASS, journey
    return FAIL_CODE, {"stderr": _first_detail(journey), **journey}


def _first_detail(journey: dict) -> str:
    failures = journey.get("failures") or []
    return "\n".join(f"{f.get('status')} {f.get('path')}: {f.get('detail', '')}"
                     for f in failures[:5]) or "journey smoke failed"


def verify_target(project_id: str, target) -> dict:
    """Run one VerifyTarget's ladder against a fresh workspace, cleaned up
    unconditionally at the end. Returns {tier_name: {"verdict": ..., ...}}
    for whichever of install/build/boot the target declares."""
    try:
        workspace = _call("/workspace/start", {"project_id": project_id}, 30)["workspace"]
    except (urllib.error.URLError, OSError, TimeoutError, KeyError) as exc:
        return {"target": target.name, "unverified_reason": str(exc)}

    tiers = {}
    upstream_ok = True
    try:
        for tier_name, spec in (("install", target.install), ("build", target.build), ("boot", target.boot)):
            if spec is None:
                continue
            if not upstream_ok:
                tiers[tier_name] = {"verdict": SKIPPED}
                continue

            run = _boot_tier if tier_name == "boot" else _run_tier
            verdict, detail = run(workspace, target, spec)
            if verdict == FAIL_ENVIRONMENT:
                for _ in range(MAX_ENV_RETRIES):
                    verdict, detail = run(workspace, target, spec)
                    if verdict != FAIL_ENVIRONMENT:
                        break

            tiers[tier_name] = {"verdict": verdict, **detail}
            if verdict != PASS:
                upstream_ok = False

            # The journey rides the boot probe (same container, same exec
            # mechanism) but reports as its own rung — see _journey_tier.
            if tier_name == "boot" and spec.journey:
                jverdict, jdetail = (_journey_tier(detail) if verdict == PASS
                                     else (SKIPPED, {}))
                tiers["journey"] = {"verdict": jverdict, **jdetail}
                if jverdict != PASS:
                    upstream_ok = False
    finally:
        try:
            _call("/workspace/cleanup", {"workspace": workspace}, 30)
        except (urllib.error.URLError, OSError, TimeoutError):
            pass  # best-effort; the sandbox service's own scratch dir is disposable

    # A tier-level UNVERIFIED (the sandbox accepted /workspace/start and then
    # became unreachable) used to be visible ONLY inside `tiers`, while
    # build_verify_agent tested for a target-level `unverified_reason` — so it
    # was never counted, never surfaced, and read as an ordinary tier failure.
    # Same abstention bug as the one this whole change exists to remove, one
    # level down. Surfacing it here keeps the single test in the caller correct.
    unverified = [f"{name}: {t.get('error', 'sandbox unreachable')}"
                  for name, t in tiers.items() if t.get("verdict") == UNVERIFIED]
    if unverified:
        return {"target": target.name, "tiers": tiers,
                "unverified_reason": "; ".join(unverified)}
    return {"target": target.name, "tiers": tiers}
