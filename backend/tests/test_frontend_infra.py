"""Deterministic React/Vite frontend infra (2026-08-11). Zero API calls.

The gap these cover: across every project this system had ever generated, not
one produced a `frontend/package.json`. The backend shipped a real
requirements.txt while the frontend shipped components with no manifest, no
build config and no HTML entry — so `npm install` had nothing to read and the
app could not start.

Verified for real before this suite was written: `npm install` (166 packages)
then `npm run build` succeeded on the rendered output, and the emitted CSS
bundle contained the Tailwind utilities the components actually used.
"""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.profiles.react_fastapi import (                           # noqa: E402
    FRONTEND_INFRA_BASENAMES, PROFILE)
from app.utils import frontend_infra as FI                         # noqa: E402

COMPONENTS = {
    "frontend/src/App.jsx":
        "import React from 'react';\n"
        "import { BrowserRouter } from 'react-router-dom';\n"
        "export default function App(){ return <div className='p-4'/>; }",
    "frontend/src/lib/api.js":
        "import axios from 'axios';\nexport default axios.create({baseURL:'/api'});",
    "frontend/src/pages/Deals.jsx":
        "import { format } from 'date-fns';\n"
        "import unknownpkg from 'not-in-the-map';\n"
        "export default function D(){ return null; }",
}


def _pkg(files=None, name="Acme CRM"):
    content, warnings = FI.render_package_json(name, files if files is not None
                                               else COMPONENTS)
    return json.loads(content), warnings


# ── package.json ─────────────────────────────────────────────────────────────

def test_the_core_runtime_is_always_present():
    """Without these there is no application at all, whatever the plan said."""
    pkg, _ = _pkg({})
    for dep in ("react", "react-dom", "axios"):
        assert dep in pkg["dependencies"], dep
    for dev in ("vite", "@vitejs/plugin-react", "tailwindcss", "postcss",
                "autoprefixer"):
        assert dev in pkg["devDependencies"], dev


def test_dependencies_come_from_what_the_code_actually_imports():
    pkg, _ = _pkg()
    assert "react-router-dom" in pkg["dependencies"]
    assert "date-fns" in pkg["dependencies"]
    # Never added speculatively — an unused dependency is install time and
    # supply-chain surface bought for nothing.
    assert "recharts" not in pkg["dependencies"]


def test_an_unknown_package_is_warned_about_and_never_invented():
    """A fabricated version fails the install for the WHOLE project, which is
    strictly worse than one missing dependency named in a warning."""
    pkg, warnings = _pkg()
    assert "not-in-the-map" not in pkg["dependencies"]
    assert any("not-in-the-map" in w for w in warnings), warnings


def test_relative_css_and_asset_imports_are_not_dependencies():
    pkg, warnings = _pkg({"frontend/src/App.jsx":
                          "import './index.css';\nimport logo from './logo.svg';\n"
                          "import x from '../lib/api.js';"})
    assert pkg["dependencies"].keys() <= {"react", "react-dom", "axios"}
    assert warnings == []


def test_every_pinned_version_is_a_real_range():
    for name, spec in FI.KNOWN_GOOD_VERSIONS.items():
        assert spec.startswith("^"), f"{name} is not a caret range: {spec}"
        assert spec[1].isdigit(), f"{name} has no numeric version: {spec}"


def test_tailwind_is_pinned_to_v3_on_purpose():
    """npm's `latest` is v4, which renamed utilities and dropped the config +
    PostCSS setup. Generated components use v3-era class names, and in v4 an
    unrecognised utility renders UNSTYLED with no error — a silent failure in
    the one thing a user judges immediately. Tailwind ships a v3-lts tag, so
    this is a supported line. Move both this and the coder prompt together."""
    assert FI.KNOWN_GOOD_VERSIONS["tailwindcss"].startswith("^3."), (
        "moving to Tailwind v4 requires updating prompts/frontend_coder_agent.md "
        "and render_index_css/render_tailwind_config in the same commit")


def test_the_project_name_is_sanitised_into_a_legal_package_name():
    pkg, _ = _pkg({}, name="Acme CRM! (v2)")
    assert pkg["name"] == "acme-crm-v2-frontend"
    json.dumps(pkg)                              # must stay serialisable


def test_a_nameless_project_still_produces_a_valid_manifest():
    pkg, _ = _pkg({}, name="")
    assert pkg["name"] and pkg["private"] is True


# ── entry points and build config ────────────────────────────────────────────

def test_the_html_entry_points_at_the_react_entry():
    """Vite builds FROM index.html — without it `vite build` has no entry."""
    html = FI.render_index_html("Acme CRM")
    assert 'id="root"' in html
    assert '/src/main.jsx' in html
    assert "Acme CRM" in html


def test_the_project_name_is_escaped_into_the_html():
    html = FI.render_index_html("Tom & <script>alert(1)</script>")
    assert "<script>alert(1)</script>" not in html
    assert "&amp;" in html and "&lt;script&gt;" in html


def test_the_entry_imports_the_only_stylesheet():
    """The coder prompt forbids generating or importing a stylesheet, so if this
    import is missing every Tailwind class in the project renders as nothing."""
    assert "./index.css" in FI.render_main_jsx()
    assert "@tailwind" in FI.render_index_css()


def test_the_entry_targets_whichever_app_component_exists():
    assert "from './App.tsx'" in FI.render_main_jsx("./App.tsx")


def test_tailwind_scans_every_generated_source_file():
    """A content glob that misses the components purges the classes they use and
    ships a build with no styling."""
    cfg = FI.render_tailwind_config()
    assert "./index.html" in cfg
    assert "./src/**/*.{js,jsx,ts,tsx}" in cfg


def test_the_dev_server_proxies_the_api_the_client_calls():
    """The generated axios client calls same-origin /api. In dev, Vite and
    FastAPI are different ports, so without this every call 404s."""
    cfg = FI.render_vite_config()
    assert "'/api'" in cfg and "localhost:8000" in cfg
    assert "'/ws'" in cfg and "ws: true" in cfg


# ── the profile hook ─────────────────────────────────────────────────────────

def _run(files):
    generated = dict(files)
    log, errors = [], []
    written = PROFILE.frontend_infra({}, generated, "__test_fe_infra__",
                                     "Acme CRM", log, errors)
    return generated, written, errors


def test_the_hook_writes_a_runnable_scaffold():
    generated, written, _ = _run(COMPONENTS)
    for path in ("frontend/package.json", "frontend/vite.config.js",
                 "frontend/index.html", "frontend/tailwind.config.js",
                 "frontend/postcss.config.js", "frontend/src/main.jsx",
                 "frontend/src/index.css"):
        assert path in generated, path
    assert written >= 7


def test_a_generated_app_component_is_never_overwritten():
    """The coder's App knows about routes, providers and layout. Replacing it
    with a template would discard real work for a worse version."""
    generated, _, errors = _run(COMPONENTS)
    assert generated["frontend/src/App.jsx"] == COMPONENTS["frontend/src/App.jsx"]
    assert not any("placeholder" in e for e in errors)


def test_a_missing_app_component_gets_an_honest_placeholder():
    """A convincing empty shell is how a failed run gets mistaken for a working
    one, so the placeholder says generation was incomplete."""
    generated, _, errors = _run({"frontend/src/lib/api.js": "export default {};"})
    app = generated["frontend/src/App.jsx"]
    assert "incomplete" in app.lower()
    assert any("placeholder" in e for e in errors), errors


def test_a_generated_entry_point_or_stylesheet_is_preserved():
    custom = {"frontend/src/main.jsx": "// hand-rolled entry",
              "frontend/src/index.css": "/* custom */",
              "frontend/src/App.jsx": "export default function App(){}"}
    generated, _, _ = _run(custom)
    for path, content in custom.items():
        assert generated[path] == content, path


# ── contract with the rest of the pipeline ───────────────────────────────────

def test_infra_files_are_declared_so_the_planner_skips_them():
    assert PROFILE.frontend_infra_basenames == FRONTEND_INFRA_BASENAMES
    for basename in ("package.json", "vite.config.js", "index.html",
                     "tailwind.config.js", "postcss.config.js"):
        assert basename in FRONTEND_INFRA_BASENAMES


def test_the_planner_is_told_not_to_plan_them():
    """Filtering them out silently would leave the planner spending coder calls
    on files that are then overwritten."""
    guidance = PROFILE.phase("frontend").plan_guidance
    for basename in ("package.json", "vite.config.js", "index.html"):
        assert basename in guidance, basename


def test_verification_installs_without_a_lockfile():
    """`npm ci` refuses to run without package-lock.json, and this pipeline
    cannot produce a truthful one — a lockfile pins a fully resolved transitive
    tree, knowable only by resolving it against the registry."""
    frontend = next(t for t in PROFILE.verify_targets if t.name == "frontend")
    assert frontend.install.command == ("npm", "install"), frontend.install.command


def test_the_manifest_build_script_matches_what_verification_runs():
    pkg, _ = _pkg({})
    frontend = next(t for t in PROFILE.verify_targets if t.name == "frontend")
    assert frontend.build.command == ("npm", "run", "build")
    assert "build" in pkg["scripts"]


def test_the_built_output_dir_matches_what_boot_serves():
    """vite writes to dist/; the boot tier serves that directory. If these ever
    disagree, boot verification fails on a build that actually succeeded."""
    frontend = next(t for t in PROFILE.verify_targets if t.name == "frontend")
    assert frontend.boot.workdir == "dist"
    assert "outDir: 'dist'" in FI.render_vite_config()


if __name__ == "__main__":
    import shutil
    from app.utils.file_writer import OUTPUTS_ROOT
    passed = failed = 0
    for name, fn in sorted(globals().items()):
        if not name.startswith("test_") or not callable(fn):
            continue
        try:
            fn()
            print(f"  ok   {name}")
            passed += 1
        except Exception as e:                  # noqa: BLE001
            print(f"  FAIL {name}: {type(e).__name__}: {e}")
            failed += 1
    shutil.rmtree(os.path.join(OUTPUTS_ROOT, "__test_fe_infra__"), ignore_errors=True)
    print(f"\n{passed} passed, {failed} failed. (0 API calls)")
    sys.exit(1 if failed else 0)
