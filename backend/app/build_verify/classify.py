"""Failure classification for the build-verification ladder — the reason an
unclassified "build pass rate" would measure registry flakiness as much as
generated-code quality. See docs/SANDBOX_THREAT_MODEL.md's Verdict semantics
section for why `unverified` must never read as `pass`.

Six outcomes, fixed vocabulary — `pass`/`skipped`/`unverified` are decided by
the caller without ever reaching this module (there is no RunResult to
classify: `pass` is a plain exit-code check, `skipped` means a lower tier
already failed, `unverified` means the sandbox itself never produced a
result). This module only disambiguates the failure case a completed
RunResult represents.
"""
PASS = "pass"
FAIL_CODE = "fail_code"
FAIL_ENVIRONMENT = "fail_environment"
TIMEOUT = "timeout"
SKIPPED = "skipped"
UNVERIFIED = "unverified"

MAX_ENV_RETRIES = 1

# Recognized by name, not by absence — an unfamiliar failure defaults to
# fail_code (fail-closed) rather than being excused as environmental. A false
# "fail_environment" quietly discounts a real defect from the metric; a false
# "fail_code" on a genuine flake costs one extra retry and nothing else.
_ENV_PATTERNS = (
    # pip / PyPI
    "could not fetch url", "read timed out", "connecttimeouterror",
    "newconnectionerror", "temporary failure in name resolution",
    "connection reset by peer", "502 bad gateway", "503 service unavailable",
    "max retries exceeded",
    # npm / registry — "code X" rather than bare X: bare "enotfound" is a
    # substring of the unrelated "ModuleNotFoundError" lowercased, which
    # misclassified a real Python import error as a network flake.
    "code econnreset", "code etimedout", "code enotfound", "code eai_again",
    "code econnrefused", "network timeout", "npm error code e5",
)


def classify_failure(stderr: str) -> str:
    """Classify a completed, non-timed-out, nonzero-exit RunResult's stderr.
    Callers handle `pass` (exit_code == 0), `timeout` (RunResult.timed_out),
    and `unverified` (no RunResult at all) themselves — this only chooses
    between fail_code and fail_environment."""
    low = (stderr or "").lower()
    if any(p in low for p in _ENV_PATTERNS):
        return FAIL_ENVIRONMENT
    return FAIL_CODE


def demo() -> None:
    assert classify_failure("ERROR: Could not find a version that satisfies the requirement foo==9.9.9") == FAIL_CODE
    assert classify_failure("npm error code ETARGET\nnpm error notarget No matching version found") == FAIL_CODE
    assert classify_failure("Could not fetch URL https://pypi.org/simple/foo/: Read timed out.") == FAIL_ENVIRONMENT
    assert classify_failure("npm error code ECONNRESET") == FAIL_ENVIRONMENT
    assert classify_failure("Traceback (most recent call last):\nModuleNotFoundError: No module named 'app.foo'") == FAIL_CODE
    assert classify_failure("") == FAIL_CODE
    print("classify.demo: OK")


if __name__ == "__main__":
    demo()
