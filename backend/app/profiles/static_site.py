"""Profile 2: static-site — plain HTML/CSS/JS, no build step.

Breaks the assumption that phases are always present. This profile declares
only `frontend` and `devops`: there is no database and no server, so those
phases are ABSENT from every plan it produces, and the database/backend graph
nodes no-op. That absence is the feature Phase 2 exists to make legal.

Second thing it breaks: the react-fastapi frontend recipe assumes a shared
axios client every component waits on, and a Tailwind class vocabulary. Here
the shared artifact is a stylesheet, so the implicit edge points at CSS instead,
and the UI contract is CSS custom properties rather than utility classes.
"""
from . import PhaseSpec, StackProfile

# The shared stylesheet plays the role the axios client plays in react-fastapi:
# every page depends on it, so it must exist before they generate. Without this
# edge a page invents its own class names and the stylesheet is written to match
# nothing.
_SHARED_EXTS = (".css",)


def _is_shared(filepath: str) -> bool:
    return filepath.lower().endswith(_SHARED_EXTS)


def frontend_implicit_deps(task: dict, by_id: dict) -> list:
    """Every page and script waits on the shared stylesheet(s)."""
    if _is_shared(task.get("filepath", "")):
        return []
    return [tid for tid, t in by_id.items() if _is_shared(t.get("filepath", ""))]


def file_kind(filepath: str, description: str = "") -> str:
    """Classify a static-site file. Deliberately extension-driven: there is no
    package layout to infer structure from, and a static site's kinds ARE its
    file types."""
    low = (filepath or "").lower()
    if low.endswith(".css"):
        return "style"
    if low.endswith((".js", ".mjs")):
        return "script"
    if low.endswith(".html"):
        return "page"
    if low.endswith(".json"):
        return "data"
    return "other"


IMPORT_NOTE = (
    "This file is at {filepath}. Reference the stylesheet, scripts, images and "
    "other pages by paths RELATIVE to this file, computed from the folder map. "
    "Output only the file's content.")

STRUCTURE_NOTE = (
    "FOLDER MAP ({prefix}) — every asset and page you reference must be in "
    "this list, addressed relatively")

# The static-site equivalent of the react-fastapi UI contract: the shared thing
# that stops N independently generated pages looking like N different websites.
# Custom-property names rather than utility classes, because that is what this
# stack's shared vocabulary actually is.
_CONTRACT = (
    "Shared conventions — every page and stylesheet in this project follows "
    "these exactly.\n"
    "Design tokens are CSS custom properties declared once on `:root` in the "
    "shared stylesheet and used through `var()` everywhere: `--color-bg`, "
    "`--color-ink`, `--color-muted`, `--color-accent`, `--color-line`, "
    "`--space-sm`, `--space-md`, `--space-lg`, `--font-body`, `--measure`. "
    "Never write a raw hex colour outside the `:root` block.\n"
    "Structure: `.site-header` with a `nav.nav-list`, one `<main>`, "
    "`.site-footer`. Cards are `.card` inside a `ul.gallery-grid`. The current "
    "nav link carries `aria-current=\"page\"`.\n"
    "Mobile first, one breakpoint at `@media (min-width: 48rem)`. Every page "
    "links the same stylesheet and no page defines its own `<style>` block."
)


def ui_contract(tech_stack_str: str, implementation_plan_str: str) -> str:
    """Static and deterministic — no LLM call, no I/O. Unlike react-fastapi's it
    derives nothing from the plan: a static site has no shared components to
    discover, only shared tokens, and those are fixed here on purpose. What
    prevents drift is that every page gets the SAME vocabulary."""
    return _CONTRACT


DEVOPS_FILES = (
    {
        "filepath": "Dockerfile",
        "description": "Single-stage Dockerfile on nginx:alpine. There is no build step — copy the site's HTML, CSS, JS and assets to /usr/share/nginx/html, copy nginx.conf into the image, expose 80. Do not add a node build stage; nothing needs compiling."
    },
    {
        "filepath": "nginx.conf",
        "description": "Nginx config serving the static files with try_files $uri $uri/ =404 — a real 404 for a missing page, NOT an SPA index.html fallback. Enable gzip for html/css/js/svg, long Cache-Control for assets and no-cache for .html, plus X-Content-Type-Options and X-Frame-Options headers."
    },
    {
        "filepath": ".github/workflows/ci.yml",
        "description": "GitHub Actions workflow triggered on push and pull_request to main. Steps suited to a site with no package manager: checkout, an HTML validity check, and a link check. No npm ci, no test runner, no build step — there is no package.json."
    },
    {
        "filepath": "README.md",
        "description": "Project README with the title and one-line description, the file layout, how to preview locally with any static file server, how to deploy (copy files to a static host or run the Docker image), and a note that this project was generated by an AI multi-agent pipeline and should be reviewed before production use."
    },
)


PLAN_EXAMPLE = '''[
  {
    "id": "fe_001",
    "phase": "frontend",
    "filename": "main.css",
    "filepath": "src/styles/main.css",
    "description": "Shared stylesheet for the whole site: design tokens on :root, base typography, the site header and nav, the card and gallery grid, the footer, and the single min-width 48rem breakpoint.",
    "requires": [],
    "context_sections": ["Component Hierarchy"],
    "estimated_complexity": "medium"
  },
  {
    "id": "fe_002",
    "phase": "frontend",
    "filename": "index.html",
    "filepath": "src/index.html",
    "description": "Home page: header with primary nav, hero section carrying the studio name and tagline, a short intro paragraph, and the shared footer. Links the shared stylesheet and defers the shared script.",
    "requires": ["fe_001"],
    "context_sections": ["Component Hierarchy"],
    "estimated_complexity": "medium"
  }
]'''


PROFILE = StackProfile(
    name="static-site",
    label="Static website (HTML/CSS/JS)",
    summary=("A static website served as plain files: semantic HTML pages, one "
             "shared CSS stylesheet, optional vanilla-JS behaviour. No build "
             "step, no server, no database."),
    phases=(
        PhaseSpec(name="frontend", id_prefix="fe", label="Site",
                  agent_type="frontend_code",
                  prompt_file="static_site_coder_agent.md",
                  context_recipe="frontend", context_prefix="src",
                  import_note=IMPORT_NOTE, structure_note=STRUCTURE_NOTE,
                  plan_guidance=("HTML pages, the shared stylesheet, vanilla-JS "
                                 "behaviour modules and static data files under "
                                 "`src/`. Plan the shared stylesheet first — "
                                 "every page depends on it.")),
        PhaseSpec(name="devops", id_prefix="dv", label="Deployment",
                  agent_type="devops",
                  prompt_file="static_site_devops_agent.md",
                  plan_guidance=("Deployment files at the project root. The "
                                 "devops stage generates its own set, so plan "
                                 "devops tasks only for files beyond it.")),
    ),
    file_kind=file_kind,
    implicit_deps={"frontend": frontend_implicit_deps},
    ui_contract=ui_contract,
    infra=None,             # nothing deterministic to render: no manifest, no entrypoint
    infra_basenames=frozenset(),
    devops_files=DEVOPS_FILES,
    # The Improvement-01 reviewer prompt judges React components — it would
    # score plain HTML against rules that do not apply.
    review_supported=False,
    plan_example=PLAN_EXAMPLE,
    # A complete brochure site is legitimately 4-6 files. The react-fastapi
    # floor of 8 measured as actively harmful here (docs/IMPROVEMENT_03_RESULTS).
    min_tasks=3,
)
