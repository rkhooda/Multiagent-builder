from typing import TypedDict, List, Dict

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
    file_list: List[str]           # list of file paths to generate
    generated_files: Dict[str, str] # filepath -> code content
    qa_report: str
    devops_files: Dict[str, str]

    # Control fields
    current_stage: str
    human_feedback: str            # injected at approval gates
    human_decision: str            # 'approve', 'edit', 'reject'
    log: List[str]                 # trace of every agent action
    errors: List[str]
