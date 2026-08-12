"""Stack Profiles — Improvement 03.

A profile is everything the pipeline previously assumed about React+FastAPI,
made explicit: which phases exist, which prompt each coder reads, how files are
classified, which structural dependency edges are implied, which deterministic
infra is rendered, and what the devops stage produces. The full inventory of
what forced each field is docs/STACK_COUPLING_AUDIT.md — a field exists here
only because a row there demands it.

Shape (ponytail #1 conclusion): a hybrid. Declarations are data on one frozen
dataclass; rules that are inherently code (file-kind classification, implicit
dependency edges, infra renderers) are plain functions carried by the profile
module. No rule DSL, no plugin framework — a registry dict of modules is the
whole loader. Phase names are a SUBSET of the four canonical slots
(database/backend/frontend/devops) because those map 1:1 onto the fixed
LangGraph nodes; a new phase name would need a new node and no current target
needs one.
"""
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Callable, Optional

PROMPTS_DIR = Path(__file__).resolve().parents[3] / "prompts"

#: The only phase names a profile may declare — each is a LangGraph node.
CANONICAL_PHASES = ("database", "backend", "frontend", "devops")


@dataclass(frozen=True)
class PhaseSpec:
    """One phase a profile declares. `agent_type` is the llm_router routing key
    (which model chain runs it); `context_recipe` picks the context_builder
    path ('frontend' = component-tree recipe, 'backend' = resource-kind
    recipe); the note fields override the recipe's closing/structure lines
    (None = the recipe's built-in default, which is the react-fastapi text)."""
    name: str                 # one of CANONICAL_PHASES
    id_prefix: str            # task-id prefix: db | be | fe | dv
    label: str                # human label for UI / docs
    agent_type: str           # llm_router MODELS key
    prompt_file: str          # filename under prompts/ for this phase's coder
    context_recipe: str = "frontend"   # 'frontend' | 'backend'
    context_prefix: str = ""  # folder-map prefix (e.g. 'frontend/src')
    import_note: Optional[str] = None     # template: {filepath} {resource}
    structure_note: Optional[str] = None  # template: {prefix}
    #: one line telling the planner what belongs in this phase FOR THIS STACK —
    #: "frontend" means something different in a React app and a static site.
    plan_guidance: str = ""


@dataclass(frozen=True)
class TierSpec:
    """One tier of one verify target's build-verification ladder — Build
    Verification improvement. `command` is an argv tuple, `image` a Docker
    image tag the sandbox pulls, `timeout_s` this tier's wall-clock ceiling.
    `workdir` is relative to the target's own root (e.g. "" or "dist").
    `port`/`health_path`/`ready_timeout_s` are read only for a boot tier —
    see docs/SANDBOX_THREAT_MODEL.md and backend/sandbox/runner.probe_boot."""
    command: tuple
    image: str
    timeout_s: int
    workdir: str = ""
    env: Optional[dict] = None
    port: Optional[int] = None
    health_path: str = "/health"
    ready_timeout_s: int = 30
    #: Boot tiers only. Run the journey smoke once the container is
    #: healthy: fetch /openapi.json, then call every parameterless GET it
    #: declares and fail on any 5xx. Boot proves a process started;
    #: this proves the application answers. See sandbox/runner.py.
    journey: bool = False


@dataclass(frozen=True)
class VerifyTarget:
    """One independently installable/buildable/bootable unit of a project —
    a react-fastapi project has two (backend, frontend), each with its own
    three-tier ladder. `root` is relative to the project root."""
    name: str
    root: str
    install: Optional[TierSpec] = None
    build: Optional[TierSpec] = None
    boot: Optional[TierSpec] = None


@dataclass(frozen=True)
class StackProfile:
    name: str
    label: str
    phases: tuple                       # ordered PhaseSpec tuple
    #: (filepath, description) -> file kind string; drives context recipes and
    #: structural edges. Kind vocabulary: config/database/main/model/schema/
    #: router/service/other.
    file_kind: Callable = None
    #: phase name -> callable(task, by_id) -> [task ids] structural edges the
    #: planner may have omitted (lib-first, model-before-router).
    implicit_deps: dict = field(default_factory=dict)
    #: (tech_stack_str, plan_json) -> shared contract injected into every
    #: frontend context; None = no contract for this stack.
    ui_contract: Optional[Callable] = None
    #: callable rendering deterministic infra after the backend phase
    #: (state, generated_files, ok_router_paths, project_id, project_name,
    #: log, errors) -> files written; None = no deterministic infra.
    infra: Optional[Callable] = None
    #: basenames excluded from the per-task LLM loop because infra renders them.
    infra_basenames: frozenset = frozenset()
    #: callable rendering deterministic infra after the FRONTEND phase
    #: (state, generated_files, project_id, project_name, log, errors) -> files
    #: written; None = no deterministic frontend infra. Separate from `infra`
    #: because it must run after a different phase: a manifest's dependency list
    #: is derived from what the frontend files actually import.
    frontend_infra: Optional[Callable] = None
    #: basenames excluded from the frontend LLM loop because infra renders them.
    frontend_infra_basenames: frozenset = frozenset()
    #: devops stage output: tuple of {filepath, description} dicts.
    devops_files: tuple = ()
    #: whether the Improvement-01 selective reviewer understands this stack's
    #: output (its prompt is React-specific).
    review_supported: bool = False
    #: worked example task array injected into the planning prompt — a plan for
    #: THIS stack's file conventions. The single biggest planner-quality lever,
    #: same as the coders' few-shot examples.
    plan_example: str = ""
    #: what this profile is for, shown at Gate 1 / in the selector.
    summary: str = ""
    #: plan-size floor. Per-profile because scope differs by an order of
    #: magnitude: a full-stack app below 8 tasks is a truncated plan, a static
    #: site of 5 files is complete.
    min_tasks: int = 8
    #: Build Verification improvement: independently-verified units (backend,
    #: frontend, ...). Empty = this profile declares no verification recipe
    #: yet — the verify stage treats that as nothing to check, not a failure.
    verify_targets: tuple = ()

    def phase(self, name: str) -> Optional[PhaseSpec]:
        return next((p for p in self.phases if p.name == name), None)

    def phase_names(self) -> list:
        return [p.name for p in self.phases]

    def phase_table(self) -> str:
        """The planner's phase vocabulary, DERIVED from the declarations above
        rather than authored, so the prompt cannot drift from what the
        validator enforces."""
        rows = "\n".join(
            f"| `{p.name}` | `{p.id_prefix}_` | {p.plan_guidance} |" for p in self.phases)
        order = " → ".join(p.name for p in self.phases)
        return (
            f"| Phase | ID prefix | What belongs in it |\n"
            f"|---|---|---|\n{rows}\n\n"
            f"Execution order: {order}.")

    def prompt_for(self, phase_name: str) -> str:
        spec = self.phase(phase_name)
        if spec is None:
            raise KeyError(f"profile '{self.name}' declares no phase '{phase_name}'")
        return _read_prompt(str(PROMPTS_DIR / spec.prompt_file))


@lru_cache(maxsize=None)
def _read_prompt(path: str) -> str:
    return Path(path).read_text(encoding="utf-8")


DEFAULT_PROFILE = "react-fastapi"


def get_profile(name: str) -> StackProfile:
    """The named profile, or the default when the name is unknown/empty.
    Falling back (rather than raising) keeps pre-profile checkpoints loadable —
    every project built before Improvement 03 is implicitly react-fastapi."""
    return PROFILES.get((name or "").strip().lower(), PROFILES[DEFAULT_PROFILE])


def active_profile(state: dict) -> StackProfile:
    return get_profile(state.get("stack_profile", "") if isinstance(state, dict) else "")


# ── Selection from a TechStackSchema recommendation (ponytail #3) ────────────
#
# The requirements agent recommends a stack in prose; this maps that
# recommendation onto a profile DETERMINISTICALLY. It is deliberately not an
# LLM call: the mapping is a fixed lookup over a three-entry registry, and a
# model asked to pick would introduce a failure mode where a table cannot.
#
# Signals are matched against the whole recommendation blob. Ordered
# most-specific first, because "Express + React" must not match on "react"
# alone — a project with both is a full-stack app, which react-fastapi serves.
AUTO = "auto"

_SIGNALS = (
    # (profile, positive signals, disqualifying signals)
    ("node-express-api", ("express", "nestjs", "fastify", "koa"),
     ("react", "vue", "svelte", "angular", "next.js", "nuxt")),
    ("static-site", ("static site", "static website", "plain html", "html/css",
                     "no backend", "jekyll", "hugo", "eleventy", "11ty",
                     "astro", "gatsby"),
     ("database", "postgres", "mysql", "mongodb", "api server")),
    ("react-fastapi", ("fastapi", "react", "vite", "django", "flask", "next.js"),
     ()),
)


def _blob(tech_stack: dict) -> str:
    if not isinstance(tech_stack, dict):
        return str(tech_stack or "").lower()
    parts = [str(tech_stack.get(k, "")) for k in
             ("frontend", "backend", "database", "auth", "hosting")]
    parts += [str(x) for x in (tech_stack.get("key_libraries") or [])]
    return " ".join(parts).lower()


def resolve_profile(tech_stack: dict) -> tuple:
    """Map a TechStackSchema recommendation to (profile_name, mismatch_reason).

    `mismatch_reason` is "" on a confident match and a human-readable sentence
    otherwise. It is NEVER resolved by silently picking the nearest profile:
    the caller records the reason on state and Gate 1 shows it, because a
    human is already reading there and "we built the wrong shape quietly" is
    the failure this exists to prevent. See docs/PROFILES.md.
    """
    blob = _blob(tech_stack)
    if not blob.strip():
        return DEFAULT_PROFILE, ""

    for name, positives, disqualifiers in _SIGNALS:
        if any(d in blob for d in disqualifiers):
            continue
        if any(p in blob for p in positives):
            return name, ""

    supported = ", ".join(f"{p.name} ({p.label})" for p in PROFILES.values())
    return DEFAULT_PROFILE, (
        f"The recommended stack does not match any stack this system can "
        f"build. Supported targets: {supported}. Nothing has been generated "
        f"for the recommended stack — choose a supported target to continue, "
        f"or approve to build with {DEFAULT_PROFILE}."
    )


# Registry. Imported at the bottom so profile modules can import the
# dataclasses from this package without a cycle.
from . import node_express_api, react_fastapi, static_site  # noqa: E402

PROFILES = {
    p.name: p for p in (react_fastapi.PROFILE, static_site.PROFILE,
                        node_express_api.PROFILE)
}
