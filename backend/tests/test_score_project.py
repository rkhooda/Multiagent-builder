"""The tier ladder must mean the same thing on every project — Day 25.

This suite exists for one reason: the three integration runs are only comparable
if the rubric is stable. A silent change to stub detection or the usable
threshold would move all three numbers and invalidate the degradation curve
without failing anything. These assertions pin the ladder down.

Zero API cost, no fixtures, no network.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import score_project as sp  # noqa: E402

passed = failed = 0


def check(label, condition):
    global passed, failed
    if condition:
        passed += 1
        print(f"  ok   {label}")
    else:
        failed += 1
        print(f"  FAIL {label}")


# ── the ladder is monotonic and usable is pinned to the top rung ─────────────
check("tiers ascend missing < present < syntax < imports < substantive",
      sp.TIER_MISSING < sp.TIER_PRESENT < sp.TIER_SYNTAX < sp.TIER_IMPORTS < sp.TIER_SUBSTANTIVE)
check("usable is defined as the substantive tier",
      sp.USABLE_TIER == sp.TIER_SUBSTANTIVE)

# ── stub detection: the judgement most likely to drift ───────────────────────
check("empty file is a stub", sp.is_stub("", "a.py"))
check("whitespace-only file is a stub", sp.is_stub("   \n\n  ", "a.py"))
check("imports-and-pass scaffolding is a stub",
      sp.is_stub("import os\nfrom x import y\npass\n", "backend/thing.py"))
check("a module docstring alone is a stub",
      sp.is_stub('"""TODO: implement."""\n', "backend/thing.py"))
check("a real function is not a stub",
      not sp.is_stub("def add(a, b):\n    return a + b\n", "backend/thing.py"))
check("a real class is not a stub",
      not sp.is_stub("class User:\n    name = 'x'\n", "backend/models/user.py"))
check("empty __init__.py is exempt, not a stub",
      not sp.is_stub("import os\n", "backend/__init__.py"))
check("syntactically broken python is not called a stub (caught one rung down)",
      not sp.is_stub("def broken(:\n", "backend/thing.py"))
# The generator's own failure placeholder must never score as a real file. The
# JSX form parses and clears the size floor, so only an explicit check catches
# it — otherwise "% usable" counts files that were never generated.
check("the JSX failure placeholder is a stub",
      sp.is_stub('// Placeholder — regenerate with "Request AI Fix" at the review gate.\n\n'
                 'export default function GenerationFailedPlaceholder() {\n  return null;\n}\n',
                 "frontend/src/x.jsx"))
check("the python failure placeholder is a stub",
      sp.is_stub('# Placeholder — regenerate with "Request AI Fix" at the review gate.\n\npass\n',
                 "backend/x.py"))
check("a placeholder non-python file is a stub",
      sp.is_stub("// TODO: implement\n", "frontend/src/App.jsx"))
# The tolerant direction matters: a small-but-real component must NOT be called
# a stub, or the score is deflated by files that are simply concise.
check("a small but real component is not a stub",
      not sp.is_stub("export default () => <App title='hi' />\n", "frontend/src/App.jsx"))
check("a substantial non-python file is not a stub",
      not sp.is_stub("export default function App() {\n  return <div>hello world here</div>\n}\n",
                     "frontend/src/App.jsx"))

# ── planned_files unions devops output ──────────────────────────────────────
# Regression guard: devops files are generated after planning and are absent
# from file_list. Dropping this union silently penalises every project.
state = {"file_list": ["backend/main.py"], "devops_files": {"Dockerfile": "FROM python"}}
check("planned_files includes devops output",
      sp.planned_files(state) == {"backend/main.py", "Dockerfile"})
check("planned_files falls back to the plan JSON when file_list is empty",
      sp.planned_files({"implementation_plan": '[{"filepath": "a.py"}]'}) == {"a.py"})
check("planned_files survives an unparseable plan",
      sp.planned_files({"implementation_plan": "not json"}) == set())

print(f"\n{passed} passed, {failed} failed")
sys.exit(1 if failed else 0)
