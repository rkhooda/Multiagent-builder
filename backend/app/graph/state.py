from typing import TypedDict, List, Dict, Optional

class ProjectState(TypedDict):
    # Input
    project_id: str
    brief: str
    project_name: str
    optional_sections: str  # JSON string of optional section flags — set at project creation

    # Agent outputs (filled progressively as pipeline runs)
    research_report: str
    requirements_doc: str
    tech_stack: str
    architecture_doc: str
    implementation_plan: str       # stored as JSON string
    excluded_tasks: List[dict]     # tasks cut from the plan at gate 3 (audit trail, never generated)
    file_list: List[str]           # list of file paths to generate
    generated_files: Dict[str, str] # filepath -> code content
    qa_report: str
    qa_issues_count: int
    devops_files: Dict[str, str]
    previous_versions: Dict[str, str]  # doc name -> prior content, set before an edit re-run overwrites it
    fix_counts: Dict[str, int]     # filepath -> AI fixes applied at gate 4 (capped per file)

    # Control fields
    replan_after_architecture: bool  # set on gate-3 'back' rerun: skip gate 2, flow straight to planning
    skip_gate_1: bool              # set on gate-2 'back' rerun: skip gate 1, flow straight to architecture
    current_stage: str
    human_feedback: str            # injected at approval gates
    human_decision: str            # 'approve', 'edit', 'reject'
    log: List[str]                 # trace of every agent action
    errors: List[str]
