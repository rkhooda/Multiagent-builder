"""Probe candidate models and emit the committed capability matrix.

WHY THIS EXISTS. The rule it enforces is: **no model routes traffic without a
row in this matrix.** Three times a routing premise in this project was silently
wrong — qwen3-coder:free delisted, nemotron demoted for ignoring max_tokens, the
whole NVIDIA `qwen/*` namespace 410 Gone — and each cost a session. Every one of
those would have been caught by contacting the model once before trusting it.

WHAT IT MEASURES, and why each one is here rather than assumed:

  reachable        A mapped slug is not a live slug. litellm maps deepseek-chat;
                   DeepSeek's live catalogue has only deepseek-v4-*. A key is not
                   access either: this probe is what found Cerebras answering
                   "Payment required" and DeepSeek "Insufficient Balance" on
                   accounts whose keys were perfectly valid.
  contract_code    Emits source with NO markdown fences. A model that fences its
                   output when told not to writes a file that does not parse.
                   This is the quality floor in its most concrete form.
  contract_json    Emits parseable JSON with no prose around it.
  output_tokens    Completion tokens spent on ONE fixed realistic task, so the
                   number is comparable ACROSS models. This is the input to the
                   ceiling pin: a verbose model silently truncates at a ceiling
                   that a terse one clears easily, and truncation here presents
                   as an error plus silent failover, not as visible truncation.
  usage_reported   Absence must be recorded as null, never 0 — a fake zero drags
                   every average down and hides spend (see _extract_usage).
  latency_ms       Orders the tier. Fastest-verified-first, as the existing
                   NVIDIA/OpenRouter lists already are.
  max_input_tokens Context window, for the pre-flight filter that skips a
                   too-small model instead of discovering the overflow as an
                   error. litellm.get_model_info covers mapped slugs; the probe
                   records it for the rest, and the MATRIX VALUE WINS.

Rejected models are written to the matrix too, with the reason. That is
deliberate: evidence of what was tried and why it failed is what stops the next
session re-adding a dead slug on the strength of a blog post.

Run:  venv/bin/python scripts/probe_models.py [--provider mistral] [--dry-run]

ponytail: no catalogue-snapshot file and no fetch/refresh script separate from
this one. The probe must contact every candidate anyway, so THE MATRIX IS THE
CATALOGUE — one dated artefact, not two that can disagree.
"""
import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app import llm_router as R                                    # noqa: E402
from litellm import completion                                     # noqa: E402

MATRIX_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                           "config", "model_capabilities.json")

# Providers this script knows how to enumerate. `catalogue` is the live
# /v1/models endpoint — preferred over any hardcoded list precisely because
# hardcoded lists are what went stale three times. `fallback` is used only when
# the catalogue call itself fails, so that an unreachable provider still gets
# probed and still lands in the matrix with its real error.
PROVIDERS = {
    "cerebras": {
        "key": "CEREBRAS_API_KEY",
        "catalogue": "https://api.cerebras.ai/v1/models",
        "fallback": ["gpt-oss-120b", "llama-3.3-70b", "qwen-3-coder-480b"],
    },
    "deepseek": {
        "key": "DEEPSEEK_API_KEY",
        "catalogue": "https://api.deepseek.com/models",
        "fallback": ["deepseek-v4-flash", "deepseek-v4-pro"],
    },
    "mistral": {
        "key": "MISTRAL_API_KEY",
        "catalogue": "https://api.mistral.ai/v1/models",
        "fallback": ["mistral-small-latest", "codestral-latest"],
    },
}

# The models ALREADY serving production traffic, probed by the same script on
# the same tasks. Two reasons, and neither is completeness for its own sake:
#
#   1. `output_tokens` is meaningless as an absolute number — 254 tokens is only
#      "terse" or "verbose" relative to something. The incumbents supply the
#      baseline, which turns the column into a verbosity RATIO that the ceiling
#      pin can actually reason about.
#   2. A matrix that covers only the new providers ranks them in a vacuum. One
#      ordering over every routed model is the artefact the router needs.
#
# No catalogue call: these slugs are already verified by daily production use,
# and their live catalogues are covered by docs/PROVIDERS.md.
REFERENCE_MODELS = ["groq/llama-3.3-70b-versatile", "gemini/gemini-2.5-flash"]
# The incumbent whose verbosity the per-agent ceilings were measured against —
# it is the primary for every file-producing agent, so its token count is the
# denominator that makes a ratio comparable to those ceilings.
BASELINE_MODEL = "groq/llama-3.3-70b-versatile"

# Model families that cannot serve a chat completion at all. Excluded BEFORE
# probing because a probe would spend a call to learn what the name already
# says, and a 400 from an OCR endpoint is noise in the matrix, not evidence.
NON_CHAT = ("embed", "ocr", "voxtral", "moderation", "-fim", "transcribe", "tts")

# Dated snapshots are dropped in favour of their `-latest` alias: Mistral alone
# publishes 55 ids that collapse to ~12 distinct chat models, and probing both
# spends three calls to measure the same weights twice. The alias can drift
# under us — that is exactly what `probed_at` in the matrix is for, and the
# dropped ids are LOGGED rather than silently discarded.
def _is_dated_snapshot(model_id: str, all_ids: set) -> bool:
    parts = model_id.rsplit("-", 1)
    return (len(parts) == 2 and parts[1].isdigit()
            and f"{parts[0]}-latest" in all_ids)


# ── The probes ───────────────────────────────────────────────────────────────
# One fixed task per contract, identical for every model, because the point is
# comparability. Changing a prompt here invalidates every measurement in the
# committed matrix — re-probe all providers in the same commit if you do.
CODE_PROBE = [{"role": "user", "content":
               "Return only the source of a Python function named `add` taking "
               "a and b and returning their sum. No markdown fences, no "
               "explanation, no commentary — source only."}]
JSON_PROBE = [{"role": "user", "content":
               'Return only this JSON object and nothing else: '
               '{"ok": true, "items": ["a", "b"]}'}]
# Deliberately shaped like real frontend_code work — the agent whose ceiling is
# tightest (1,500 tokens per file) and which produces the most files per run.
SIZING_PROBE = [{"role": "user", "content":
                 "Write a React function component `TaskForm` in JSX: controlled "
                 "title and description inputs, a submit handler calling the "
                 "`onSubmit` prop, and basic validation that title is non-empty. "
                 "Return only the component source, no fences, no commentary."}]
SIZING_MAX_TOKENS = 2000        # above frontend_code's 1,500 so verbosity SHOWS
                                # rather than being clipped into looking fine


def _fetch_catalogue(provider: str, cfg: dict) -> tuple:
    """(model ids, error). Never raises — an unreachable catalogue is a finding."""
    key = os.getenv(cfg["key"])
    if not key:
        return [], "no key configured"
    req = urllib.request.Request(cfg["catalogue"],
                                 headers={"Authorization": f"Bearer {key}"})
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            data = json.load(resp).get("data", [])
        return sorted({m["id"] for m in data if m.get("id")}), None
    except urllib.error.HTTPError as e:
        return [], f"HTTP {e.code} from {cfg['catalogue']}"
    except Exception as e:                       # noqa: BLE001
        return [], f"{type(e).__name__}: {e}"


def _candidates(provider: str, cfg: dict) -> tuple:
    ids, err = _fetch_catalogue(provider, cfg)
    source = "live catalogue"
    if err:
        ids, source = cfg["fallback"], f"fallback list ({err})"
    all_ids = set(ids)
    kept, dropped = [], []
    for mid in ids:
        low = mid.lower()
        if any(tok in low for tok in NON_CHAT):
            dropped.append((mid, "not a chat model"))
        elif _is_dated_snapshot(mid, all_ids):
            dropped.append((mid, "dated snapshot of a -latest alias"))
        else:
            kept.append(mid)
    return kept, dropped, source, err


def _context_window(model: str) -> tuple:
    """(max_input, max_output) from litellm's mapping, or (None, None)."""
    try:
        import litellm
        info = litellm.get_model_info(model)
        return info.get("max_input_tokens"), info.get("max_output_tokens")
    except Exception:                            # noqa: BLE001 — unmapped is normal
        return None, None


def _one_call(model: str, messages: list, max_tokens: int) -> dict:
    """A single probe call. Paced by the router's own limiter, so probing obeys
    the same intervals real traffic does and cannot itself cause the 429 it is
    trying to measure."""
    R._pace(model)
    started = time.monotonic()
    try:
        resp = completion(model=model, messages=messages, max_tokens=max_tokens,
                          temperature=0.3, timeout=90)
    except Exception as e:                       # noqa: BLE001 — failure IS the datum
        return {"ok": False, "error_class": type(e).__name__,
                "error": str(e)[:200],
                "latency_ms": int((time.monotonic() - started) * 1000)}
    usage = R._extract_usage(resp, model)
    return {"ok": True,
            "content": resp.choices[0].message.content or "",
            "completion_tokens": usage.get("completion_tokens"),
            "usage_reported": usage.get("completion_tokens") is not None,
            "finish_reason": R._finish_reason(resp),
            "latency_ms": int((time.monotonic() - started) * 1000)}


def _check_code(text: str) -> tuple:
    """(passed, reason). Fences are the disqualifier — a fenced response written
    to a .py/.jsx file does not parse, which is the failure this floor exists
    to prevent."""
    if "```" in text:
        return False, "wrapped output in markdown fences"
    try:
        compile(text, "<probe>", "exec")
    except SyntaxError as e:
        return False, f"not valid Python: {e.msg}"
    if "def add" not in text:
        return False, "did not emit the requested function"
    return True, ""


def _check_json(text: str) -> tuple:
    if "```" in text:
        return False, "wrapped output in markdown fences"
    try:
        obj = json.loads(text.strip())
    except Exception as e:                       # noqa: BLE001
        return False, f"not parseable JSON: {str(e)[:80]}"
    if not isinstance(obj, dict) or obj.get("ok") is not True:
        return False, "JSON parsed but did not match the requested object"
    return True, ""


def probe_model(provider: str, model_id: str) -> dict:
    model = f"{provider}/{model_id}"
    max_in, max_out = _context_window(model)
    row = {"model": model, "provider": provider, "model_id": model_id,
           "max_input_tokens": max_in, "max_output_tokens": max_out,
           "agents": [], "admitted": False}

    code = _one_call(model, CODE_PROBE, 300)
    if not code["ok"]:
        # One failed call is enough: every later probe would fail identically,
        # and spending three calls to learn one fact is how a probe run becomes
        # too expensive to re-run — which is how matrices go stale.
        row.update(reachable=False, reason=f"{code['error_class']}: {code['error']}",
                   latency_ms=code["latency_ms"])
        return row

    row["reachable"] = True
    row["latency_ms"] = code["latency_ms"]
    row["usage_reported"] = code["usage_reported"]
    code_ok, code_why = _check_code(code["content"])
    row["contract_code"] = code_ok
    if not code_ok:
        row["contract_code_reason"] = code_why

    js = _one_call(model, JSON_PROBE, 300)
    json_ok, json_why = (_check_json(js["content"]) if js["ok"]
                         else (False, f"{js['error_class']}: {js['error']}"))
    row["contract_json"] = json_ok
    if not json_ok:
        row["contract_json_reason"] = json_why

    # Sizing is only worth a call for a model that could actually serve source.
    if code_ok:
        sizing = _one_call(model, SIZING_PROBE, SIZING_MAX_TOKENS)
        if sizing["ok"]:
            row["output_tokens"] = sizing["completion_tokens"]
            # A model that hits the probe ceiling has not been measured, it has
            # been clipped — recorded so the ceiling pin cannot read a censored
            # number as a satisfied requirement.
            row["output_truncated"] = sizing["finish_reason"] == "length"
            row["sizing_latency_ms"] = sizing["latency_ms"]

    if code_ok:
        row["agents"] += list(R.CODE_AGENTS)
    if json_ok:
        row["agents"] += list(R.PROSE_AGENTS)
    row["admitted"] = bool(row["agents"])
    if not row["admitted"]:
        row["reason"] = "failed both contract probes"
    return row


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--provider", action="append",
                    help="probe only this provider (repeatable)")
    ap.add_argument("--dry-run", action="store_true",
                    help="list what would be probed; make no calls")
    args = ap.parse_args()

    targets = args.provider or list(PROVIDERS)
    unknown = [p for p in targets if p not in PROVIDERS]
    if unknown:
        sys.exit(f"unknown provider(s): {', '.join(unknown)}")

    matrix = {"probed_at": time.strftime("%Y-%m-%d", time.gmtime()),
              "providers": {}, "models": []}

    if not args.dry_run:
        print("\n=== incumbents (baseline for the verbosity ratio)")
        for ref in REFERENCE_MODELS:
            provider, model_id = ref.split("/", 1)
            row = probe_model(provider, model_id)
            row["role"] = "incumbent"
            matrix["models"].append(row)
            print(f"    {'ADMIT ' if row['admitted'] else 'reject'} {row['model']:44s} "
                  f"code={row.get('contract_code')} json={row.get('contract_json')} "
                  f"out={row.get('output_tokens')} {row.get('latency_ms')}ms")

    for provider in targets:
        cfg = PROVIDERS[provider]
        kept, dropped, source, err = _candidates(provider, cfg)
        print(f"\n=== {provider} ({source}) — {len(kept)} candidates, "
              f"{len(dropped)} filtered out")
        # Logged, never silent: a matrix that quietly covered half a catalogue
        # reads exactly like one that covered all of it.
        for mid, why in dropped:
            print(f"    skip {mid:42s} {why}")
        matrix["providers"][provider] = {
            "catalogue_source": source, "catalogue_error": err,
            "candidates": len(kept), "filtered_out": len(dropped),
            "key_configured": bool(os.getenv(cfg["key"])),
        }
        if args.dry_run:
            for mid in kept:
                print(f"    probe {mid}")
            continue
        for mid in kept:
            row = probe_model(provider, mid)
            row["role"] = "expansion"
            matrix["models"].append(row)
            if row["admitted"]:
                print(f"    ADMIT  {row['model']:44s} "
                      f"code={row.get('contract_code')} json={row.get('contract_json')} "
                      f"out={row.get('output_tokens')} {row.get('latency_ms')}ms")
            else:
                print(f"    reject {row['model']:44s} {row.get('reason', '')[:90]}")

    if args.dry_run:
        return

    # Verbosity relative to the model the per-agent ceilings were measured
    # against. A ratio of 2.0 means this model needs twice the budget for the
    # same file — the ceiling-starvation defect, visible BEFORE it truncates a
    # real generation rather than after. Pinned by test_token_budgets.
    baseline = next((r.get("output_tokens") for r in matrix["models"]
                     if r["model"] == BASELINE_MODEL and r.get("output_tokens")), None)
    matrix["baseline_model"] = BASELINE_MODEL
    matrix["baseline_output_tokens"] = baseline
    if baseline:
        for row in matrix["models"]:
            if row.get("output_tokens"):
                row["verbosity_ratio"] = round(row["output_tokens"] / baseline, 2)

    # Explicit None check rather than `or`: a measured 0 must not be demoted to
    # "unknown" by being falsy.
    matrix["models"].sort(key=lambda r: (
        not r["admitted"],
        10 ** 9 if r.get("latency_ms") is None else r["latency_ms"]))
    os.makedirs(os.path.dirname(MATRIX_PATH), exist_ok=True)
    with open(MATRIX_PATH, "w") as fh:
        json.dump(matrix, fh, indent=2, sort_keys=True)
        fh.write("\n")
    admitted = [r for r in matrix["models"] if r["admitted"]]
    print(f"\nwrote {MATRIX_PATH}")
    print(f"{len(admitted)} admitted of {len(matrix['models'])} probed")


if __name__ == "__main__":
    main()
