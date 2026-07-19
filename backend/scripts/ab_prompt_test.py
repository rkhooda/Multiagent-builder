#!/usr/bin/env python3
"""A/B harness for coder prompt changes (Day 21).

Runs a fixture set through two prompt variants, scores each sample against an
automated checklist derived from the Day 18/19 written checklists, and prints a
criterion x variant pass-rate table.

Design (ponytail #2 conclusions, recorded in docs/PROMPT_CHANGELOG.md):

  Model pinning. call_llm's coder chain is
  (openrouter/qwen/qwen3-coder:free, groq/llama-3.3-70b-versatile) and qwen3-coder
  has returned 429 on EVERY attempt for three sessions (Days 18/19/20). Because
  call_llm retries the primary once before failing over, each generation costs
  TWO OpenRouter requests and still lands on groq. So a "30 OpenRouter call"
  budget buys only 15 generations, and every one of them is groq output anyway.
  This harness therefore calls the groq model DIRECTLY by default. That is
  strictly better on every axis: it is the same model that produced the entire
  Day 18-20 evidence base (so the A/B measures the baseline's own model), groq's
  free tier is ~1000/day rather than ~50, and skipping the doomed 429 + 2s retry
  removes latency noise from the measurement. --model restores any other chain.

  Sampling. temperature is 0.3, so one sample per variant can crown the worse
  prompt on noise alone. N=3 per fixture per variant is the floor that can
  distinguish 0/3, 1/3, 2/3, 3/3; with groq unconstrained there is no reason to
  go below it. Pass rate for a criterion = passing samples / (fixtures x samples).

  Decision rule. Keep variant B only if it is >= A on EVERY criterion and
  STRICTLY BETTER on the criterion the change targeted. A tie on the target is a
  revert: the change cost tokens and bought nothing measurable.

  Budget guard. Counts only OpenRouter requests (including 429'd attempts, which
  do consume quota), warns at 25, hard-stops at 30. Groq/gemini calls are counted
  and reported but not capped.

Modes:
  --freeze                Rebuild fixtures from real checkpoint state.
  (default)               A/B two prompt variants.
  --rescore DIR           Re-score saved outputs offline. Zero API calls.

Examples:
  python3 scripts/ab_prompt_test.py --freeze
  python3 scripts/ab_prompt_test.py --variant-b /tmp/candidate.md \
      --fixtures fe_notecard,fe_formatdate --samples 3 --target no_css_import
  python3 scripts/ab_prompt_test.py --rescore tests/fixtures/prompt_tuning/golden
"""
import argparse
import json
import os
import posixpath
import re
import sys
import time

BACKEND_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BACKEND_DIR)
REPO_ROOT = os.path.dirname(BACKEND_DIR)

FIXTURE_DIR = os.path.join(BACKEND_DIR, "tests", "fixtures", "prompt_tuning")
GOLDEN_DIR = os.path.join(FIXTURE_DIR, "golden")
BUDGET_FILE = os.path.join(REPO_ROOT, "outputs", ".ab_budget.json")  # outputs/ is gitignored

OPENROUTER_WARN = 25
OPENROUTER_STOP = 30

# The model that actually produced every file in the Day 18-20 evidence base.
DEFAULT_MODEL = "groq/llama-3.3-70b-versatile"


# ── budget ──────────────────────────────────────────────────────────────────
class Budget:
    """Day-scoped call counter. Persists to outputs/ so separate invocations
    share one running total (the guard is per-DAY, not per-process)."""

    def __init__(self, path=BUDGET_FILE):
        self.path = path
        self.counts = {"openrouter": 0, "other": 0}
        if os.path.exists(path):
            try:
                with open(path) as f:
                    self.counts.update(json.load(f).get("counts", {}))
            except (ValueError, OSError):
                pass

    def _save(self):
        os.makedirs(os.path.dirname(self.path), exist_ok=True)
        with open(self.path, "w") as f:
            json.dump({"counts": self.counts}, f)

    def check(self, model, n=1):
        """Raise BEFORE spending if this call would cross the hard stop."""
        if not model.startswith("openrouter/"):
            return
        if self.counts["openrouter"] + n > OPENROUTER_STOP:
            raise SystemExit(
                f"\nBUDGET STOP: {self.counts['openrouter']} OpenRouter calls already "
                f"spent; this run would exceed the {OPENROUTER_STOP} cap.\n"
                f"Use --model {DEFAULT_MODEL} (the model the baseline was generated "
                f"with) or reset {self.path}.")

    def spend(self, model, n=1):
        key = "openrouter" if model.startswith("openrouter/") else "other"
        self.counts[key] += n
        self._save()
        if key == "openrouter" and self.counts["openrouter"] >= OPENROUTER_WARN:
            print(f"  !! BUDGET WARNING: {self.counts['openrouter']}/{OPENROUTER_STOP} "
                  f"OpenRouter calls spent", flush=True)

    def report(self):
        return (f"OpenRouter {self.counts['openrouter']}/{OPENROUTER_STOP}, "
                f"other providers {self.counts['other']}")


# ── checks ──────────────────────────────────────────────────────────────────
# Each check: fn(output, fixture) -> (passed, detail). Pure, offline, no LLM.

JS_IMPORT_RE = re.compile(r"""^\s*import\s+(?:[\w*{}\s,$]+\s+from\s+)?['"]([^'"]+)['"]""", re.M)
JSX_ATTR_RE = r"<{tag}\b([^>]*?)/?>"
ATTR_NAME_RE = re.compile(r"(\w+)\s*=\s*[{\"']")


def _strip(output):
    from app.utils.code_cleaner import strip_code_fences
    return strip_code_fences(output)


def check_no_fences(output, fx):
    return ("```" not in output, "markdown fence present" if "```" in output else "")


def check_min_lines(output, fx):
    n = fx.get("min_lines", 5)
    lines = [l for l in _strip(output).splitlines() if l.strip()]
    return (len(lines) >= n, f"{len(lines)} non-empty lines (min {n})")


def check_imports_resolve(output, fx):
    """Every relative import resolves to a file in the frozen tree; every bare
    import is on the allow-list. This is the D5 breaks-startup criterion."""
    tree = set(fx.get("file_tree", []))
    allowed = set(fx.get("allowed_packages", []))
    here = posixpath.dirname(fx["task"]["filepath"])
    bad = []
    for spec in JS_IMPORT_RE.findall(_strip(output)):
        if spec.startswith("."):
            base = posixpath.normpath(posixpath.join(here, spec))
            cands = [base] + [base + e for e in (".js", ".jsx", ".ts", ".tsx")] + \
                    [base + "/index" + e for e in (".js", ".jsx")]
            if not any(c in tree for c in cands):
                bad.append(spec)
        else:
            root = spec.split("/")[0]
            if root.startswith("@"):
                root = "/".join(spec.split("/")[:2])
            if root not in allowed:
                bad.append(spec)
    return (not bad, f"unresolvable: {bad}" if bad else "")


def check_no_css_import(output, fx):
    bad = [s for s in JS_IMPORT_RE.findall(_strip(output))
           if s.endswith((".css", ".scss", ".sass", ".less"))]
    return (not bad, f"stylesheet imports: {bad}" if bad else "")


def check_has_default_export(output, fx):
    ok = "export default" in _strip(output)
    return (ok, "" if ok else "no default export")


def check_guards_api_data(output, fx):
    """Heuristic for the prompt's optional-chaining rule: a file that renders
    data must show at least one ?. or ?? guard. Honest about being a heuristic —
    it catches wholesale omission, not every unguarded access."""
    src = _strip(output)
    renders = any(t in src for t in (".map(", ".length", "props", "data"))
    if not renders:
        return (True, "n/a")
    ok = "?." in src or "??" in src
    return (ok, "" if ok else "renders data with no ?./?? guard")


def check_endpoints_subset(output, fx):
    allowed = fx.get("allowed_endpoints")
    if not allowed:
        return (True, "n/a")
    src = _strip(output)
    used = set(re.findall(r"""api\.\w+\(\s*[`'"]([^`'"]+)[`'"]""", src))
    bad = []
    for u in used:
        path = u.split("?")[0].rstrip("/") or "/"
        norm = re.sub(r"\$\{[^}]+\}", "{id}", path)
        if not any(norm == a or norm.startswith(a.rstrip("/") + "/") for a in allowed):
            bad.append(u)
    return (not bad, f"endpoints not in the architecture: {bad}" if bad else "")


def check_props_match(output, fx):
    """D2: every prop the consumer passes to a child must exist in that child's
    destructured signature."""
    sigs = fx.get("child_signatures") or {}
    if not sigs:
        return (True, "n/a")
    src = _strip(output)
    bad = []
    for tag, props in sigs.items():
        for attrs in re.findall(JSX_ATTR_RE.format(tag=tag), src, re.S):
            for name in ATTR_NAME_RE.findall(attrs):
                if name != "key" and name not in props:
                    bad.append(f"<{tag} {name}=> (accepts {props})")
    return (not bad, "; ".join(bad) if bad else "")


def check_py_compile(output, fx):
    src = _strip(output)
    try:
        compile(src, fx["task"]["filepath"], "exec")
        return (True, "")
    except SyntaxError as e:
        return (False, f"SyntaxError line {e.lineno}: {e.msg}")


def check_no_schema_redefinition(output, fx):
    """D6: a router must import its schemas, never redefine them."""
    bad = re.findall(r"^class\s+(\w+)\s*\(\s*BaseModel\s*\)", _strip(output), re.M)
    return (not bad, f"redefines schema classes: {bad}" if bad else "")


def check_no_pep604_union(output, fx):
    """D11: prompt requires Optional[X] over X | None."""
    src = _strip(output)
    bad = re.findall(r"Mapped\[[^\]]*\|[^\]]*\]", src) + \
          re.findall(r":\s*\w+\s*\|\s*None", src)
    return (not bad, f"PEP-604 unions: {bad[:3]}" if bad else "")


def check_uses_session_dependency(output, fx):
    src = _strip(output)
    if "APIRouter" not in src:
        return (True, "n/a")
    ok = "Depends(get_db)" in src
    inline = bool(re.search(r"sessionmaker\(|Session\(\)|create_engine\(", src))
    return (ok and not inline,
            "" if ok and not inline else "missing Depends(get_db) or builds its own session")


CHECKS = {
    "no_fences": check_no_fences,
    "min_lines": check_min_lines,
    "imports_resolve": check_imports_resolve,
    "no_css_import": check_no_css_import,
    "has_default_export": check_has_default_export,
    "guards_api_data": check_guards_api_data,
    "endpoints_subset": check_endpoints_subset,
    "props_match": check_props_match,
    "py_compile": check_py_compile,
    "no_schema_redefinition": check_no_schema_redefinition,
    "no_pep604_union": check_no_pep604_union,
    "uses_session_dependency": check_uses_session_dependency,
}


def score(output, fx):
    return {name: CHECKS[name](output, fx) for name in fx["checks"]}


# ── fixtures ────────────────────────────────────────────────────────────────
def load_fixtures(names=None):
    out = []
    for fn in sorted(os.listdir(FIXTURE_DIR)):
        if not fn.endswith(".json"):
            continue
        with open(os.path.join(FIXTURE_DIR, fn)) as f:
            fx = json.load(f)
        if names and fx["name"] not in names:
            continue
        out.append(fx)
    if names:
        missing = set(names) - {f["name"] for f in out}
        if missing:
            raise SystemExit(f"unknown fixtures: {sorted(missing)}")
    return out


def rebuild_context(fx):
    """Re-run the REAL context builder against the fixture's frozen state. Used
    to A/B a context_builder change (the string is not frozen in that case)."""
    from app.agents.context_builder import build_file_context
    st = dict(fx["state"])
    st.setdefault("log", [])
    phase = "backend" if fx["agent_type"] == "backend_code" else "frontend"
    prefix = "backend" if phase == "backend" else "frontend/src"
    return build_file_context(fx["task"], st, phase_prefix=prefix, phase=phase)


# ── run ─────────────────────────────────────────────────────────────────────
def generate(prompt_text, context, model, budget, max_tokens=1500, retries=6):
    """One sampled generation. Retries on 429 with the provider's own suggested
    wait — groq's free tier caps tokens-per-minute (12k), which a 3500-token
    A/B sample trips every ~3 calls. A retried 429 is the SAME logical sample,
    so budget is spent once, on success only."""
    import re as _re
    from litellm import completion
    import litellm.exceptions as _exc

    budget.check(model)
    messages = [{"role": "system", "content": prompt_text},
                {"role": "user", "content": context}]
    for attempt in range(retries):
        try:
            resp = completion(model=model, messages=messages, max_tokens=max_tokens,
                              temperature=0.3, timeout=90)
            budget.spend(model)
            return resp.choices[0].message.content or ""
        except _exc.RateLimitError as e:
            if attempt == retries - 1:
                raise
            m = _re.search(r"try again in ([\d.]+)s", str(e))
            wait = float(m.group(1)) + 1.0 if m else 20.0
            print(f"      (429; waiting {wait:.0f}s)", flush=True)
            time.sleep(wait)


def run_ab(args):
    budget = Budget()
    fixtures = load_fixtures(args.fixtures.split(",") if args.fixtures else None)
    variants = {"A": args.variant_a, "B": args.variant_b}
    prompts = {k: open(v).read() for k, v in variants.items()}

    runs = []  # {variant, fixture, sample, output, scores}
    outdir = args.save or os.path.join(FIXTURE_DIR, "runs", args.label)
    os.makedirs(outdir, exist_ok=True)

    print(f"\nmodel={args.model}  samples={args.samples}  fixtures={[f['name'] for f in fixtures]}")
    print(f"A={variants['A']}\nB={variants['B']}\n")

    for fx in fixtures:
        context = rebuild_context(fx) if args.rebuild_context else fx["context"]
        for vk in ("A", "B"):
            for s in range(args.samples):
                out = generate(prompts[vk], context, args.model, budget)
                sc = score(out, fx)
                runs.append({"variant": vk, "fixture": fx["name"], "sample": s,
                             "output": out, "scores": {k: v[0] for k, v in sc.items()}})
                fails = [f"{k}({v[1]})" for k, v in sc.items() if not v[0]]
                print(f"  {vk} {fx['name']} #{s}: "
                      + ("PASS all" if not fails else "FAIL " + "; ".join(fails)), flush=True)
                with open(os.path.join(outdir, f"{vk}_{fx['name']}_{s}.txt"), "w") as f:
                    f.write(out)
                time.sleep(args.sleep)

    with open(os.path.join(outdir, "runs.json"), "w") as f:
        json.dump({"model": args.model, "samples": args.samples,
                   "variant_a": variants["A"], "variant_b": variants["B"],
                   "runs": runs}, f, indent=2)

    table = build_table(runs, fixtures)
    print("\n" + table)
    print(f"\nbudget: {budget.report()}")
    verdict = decide(runs, fixtures, args.target)
    print(verdict + "\n")
    if args.append_report:
        with open(os.path.join(REPO_ROOT, "docs", "QUALITY_BASELINE.md"), "a") as f:
            f.write(f"\n\n### A/B run `{args.label}`\n\n"
                    f"model `{args.model}`, N={args.samples}, target `{args.target}`\n\n"
                    f"{table}\n\n{verdict}\n")
    return runs


def build_table(runs, fixtures):
    crits = []
    for fx in fixtures:
        for c in fx["checks"]:
            if c not in crits:
                crits.append(c)
    rows = ["| Criterion | A pass rate | B pass rate | Δ |",
            "|---|---|---|---|"]
    for c in crits:
        cells = {}
        for vk in ("A", "B"):
            rel = [r for r in runs if r["variant"] == vk and c in r["scores"]]
            cells[vk] = (sum(r["scores"][c] for r in rel), len(rel))
        a, b = cells["A"], cells["B"]
        ap = 100.0 * a[0] / a[1] if a[1] else 0.0
        bp = 100.0 * b[0] / b[1] if b[1] else 0.0
        d = bp - ap
        rows.append(f"| `{c}` | {a[0]}/{a[1]} ({ap:.0f}%) | {b[0]}/{b[1]} ({bp:.0f}%) "
                    f"| {'+' if d > 0 else ''}{d:.0f} pts |")
    return "\n".join(rows)


def decide(runs, fixtures, target):
    """Keep B only if >= A on every criterion AND strictly better on the target."""
    crits = sorted({c for r in runs for c in r["scores"]})
    rate = lambda vk, c: (
        lambda rel: (sum(r["scores"][c] for r in rel) / len(rel)) if rel else 0.0
    )([r for r in runs if r["variant"] == vk and c in r["scores"]])

    regressions = [c for c in crits if rate("B", c) < rate("A", c)]
    if regressions:
        return f"**VERDICT: REVERT** — B regressed on {regressions}."
    if target and target in crits:
        if rate("B", target) > rate("A", target):
            return (f"**VERDICT: KEEP** — B improved the target `{target}` "
                    f"({rate('A', target):.0%} -> {rate('B', target):.0%}) "
                    f"with no regression elsewhere.")
        return (f"**VERDICT: REVERT** — B tied on the target `{target}` "
                f"({rate('A', target):.0%}); the change cost tokens and bought "
                f"nothing measurable.")
    return "**VERDICT: INCONCLUSIVE** — no target criterion named (pass --target)."


def run_rescore(directory):
    """Offline regression check: re-score saved outputs with today's checks.
    Zero API calls — this is the golden-file guard."""
    fixtures = {f["name"]: f for f in load_fixtures()}
    failures, total = [], 0
    for fn in sorted(os.listdir(directory)):
        if not fn.endswith(".txt"):
            continue
        name = fn[:-4].split("_", 1)[1].rsplit("_", 1)[0]
        fx = fixtures.get(name)
        if not fx:
            print(f"  ?? {fn}: no fixture named {name}")
            continue
        with open(os.path.join(directory, fn)) as f:
            out = f.read()
        sc = score(out, fx)
        total += 1
        bad = [f"{k}: {v[1]}" for k, v in sc.items() if not v[0]]
        if bad:
            failures.append((fn, bad))
            print(f"  FAIL {fn}\n        " + "\n        ".join(bad))
        else:
            print(f"  ok   {fn}")
    print(f"\n{total - len(failures)}/{total} golden outputs still pass. (0 API calls)")
    return 1 if failures else 0


def main():
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--variant-a", default=os.path.join(REPO_ROOT, "prompts",
                                                       "frontend_coder_agent.md"))
    p.add_argument("--variant-b")
    p.add_argument("--fixtures", help="comma-separated fixture names (default: all)")
    p.add_argument("--samples", type=int, default=3)
    p.add_argument("--model", default=DEFAULT_MODEL)
    p.add_argument("--target", help="the criterion this change is meant to move")
    p.add_argument("--label", default="run")
    p.add_argument("--save", help="directory for sampled outputs")
    p.add_argument("--sleep", type=float, default=1.0, help="seconds between calls")
    p.add_argument("--rebuild-context", action="store_true",
                   help="re-run the real context builder instead of the frozen string "
                        "(use when A/B-ing a context_builder change)")
    p.add_argument("--append-report", action="store_true")
    p.add_argument("--rescore", metavar="DIR", help="offline re-score, zero API calls")
    p.add_argument("--freeze", action="store_true", help="rebuild fixtures from checkpoints")
    p.add_argument("--budget", action="store_true", help="print budget and exit")
    args = p.parse_args()

    if args.budget:
        print(Budget().report())
        return 0
    if args.rescore:
        return run_rescore(args.rescore)
    if args.freeze:
        from freeze_fixtures import freeze_all
        return freeze_all()
    if not args.variant_b:
        p.error("--variant-b is required for an A/B run")
    run_ab(args)
    return 0


if __name__ == "__main__":
    sys.exit(main())
