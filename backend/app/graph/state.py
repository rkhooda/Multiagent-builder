from typing import TypedDict, List, Dict, Optional

class ProjectState(TypedDict):
    # Input
    project_id: str
    brief: str
    project_name: str
    optional_sections: str  # JSON string of optional section flags — set at project creation
    fast_mode: bool         # lighter budgets + no LLM repairs — set at project creation

    # Agent outputs (filled progressively as pipeline runs)
    research_report: str
    requirements_doc: str
    tech_stack: str
    architecture_doc: str
    implementation_plan: str       # stored as JSON string
    ui_contract: str               # derived at planning: shared tokens/conventions injected into every frontend context
    excluded_tasks: List[dict]     # tasks cut from the plan at gate 3 (audit trail, never generated)
    file_list: List[str]           # list of file paths to generate
    generated_files: Dict[str, str] # filepath -> code content
    qa_report: str
    qa_issues_count: int
    devops_files: Dict[str, str]
    previous_versions: Dict[str, str]  # doc name -> prior content, set before an edit re-run overwrites it
    fix_counts: Dict[str, int]     # filepath -> AI fixes applied at gate 4 (capped per file)
    validation_report: dict        # Day 22 automated-checks report (counts, issues, failure_rate)

    # Control fields
    replan_after_architecture: bool  # set on gate-3 'back' rerun: skip gate 2, flow straight to planning
    skip_gate_1: bool              # set on gate-2 'back' rerun: skip gate 1, flow straight to architecture
    retry_counts: Dict[str, int]   # stage / "file_fix:{path}" / "repair:{path}" -> regeneration & repair count
    stage_history: List[dict]      # {stage, attempt, trigger, gate_origin, timestamp} per agent completion
    regen_cycle: Optional[dict]    # {"trigger": "edit"|"back", "gate": gate_name} during a feedback cycle
    # LEGACY (Day 24): failure info now lives on the projects row, because
    # writing it here re-derived the checkpoint's pending task and made a failed
    # run permanently unresumable. These fields are still read as a fallback for
    # projects that failed before the move — nothing writes them any more except
    # stage_node's clear-on-success, which keeps those legacy rows from showing a
    # stale error card. See core/database.py record_failure().
    failed_agent: str
    failure_context: Optional[dict]
    current_stage: str
    human_feedback: str            # injected at approval gates
    human_decision: str            # 'approve', 'edit', 'reject'
    log: List[str]                 # trace of every agent action
    errors: List                   # strings (agent-level notes) and structured boundary dicts
