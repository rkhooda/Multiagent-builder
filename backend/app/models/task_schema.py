from typing import List, Literal, Optional

from pydantic import BaseModel, Field, field_validator


VALID_PHASES = Literal["database", "backend", "frontend", "devops"]
VALID_COMPLEXITY = Literal["low", "medium", "high"]

# Real files the devops phase must be able to plan, none of which have an
# extension. Without these the entire plan fails validation the moment the model
# proposes a Dockerfile — which it does on essentially every run, taking the
# coder and QA stages down with it.
EXTENSIONLESS_FILENAMES = frozenset({
    "Dockerfile", "Makefile", "Procfile", "Jenkinsfile", "Caddyfile",
    "LICENSE", "CODEOWNERS",
})


class TaskSchema(BaseModel):
    id: str = Field(
        description="Unique task ID. Format: {phase_prefix}_{3_digit_number}. E.g. db_001, be_003, fe_012, dv_001"
    )
    phase: VALID_PHASES = Field(description="Which phase this task belongs to")
    filename: str = Field(description="Just the filename. E.g. models.py or InvoiceCard.jsx")
    filepath: str = Field(
        description="Full relative path from project root. E.g. backend/app/models.py"
    )
    description: str = Field(
        description=(
            "Detailed description of what this file must contain. Be specific - reference actual "
            "table names, endpoint paths, component names from the architecture."
        )
    )
    requires: List[str] = Field(
        default_factory=list,
        description="List of task IDs that must complete before this task. Empty list if no dependencies.",
    )
    context_sections: List[str] = Field(
        default_factory=list,
        description="Architecture document sections most relevant to generating this file.",
    )
    estimated_complexity: VALID_COMPLEXITY = Field(
        description="Complexity estimate: low, medium, or high"
    )

    @field_validator("id")
    @classmethod
    def validate_id_format(cls, v: str) -> str:
        import re

        if not re.match(r"^(db|be|fe|dv)_\d{3}$", v):
            raise ValueError(
                f"Task ID '{v}' must match format: db_001, be_003, fe_012, or dv_001"
            )
        return v

    @field_validator("filepath")
    @classmethod
    def validate_filepath_has_extension(cls, v: str) -> str:
        name = v.split("/")[-1]
        # Dockerfile.dev / Dockerfile.prod are conventional variants.
        if name.split(".")[0] in EXTENSIONLESS_FILENAMES:
            return v
        if "." not in name:
            raise ValueError(
                f"Filepath '{v}' must include a file extension "
                f"(or be one of: {', '.join(sorted(EXTENSIONLESS_FILENAMES))})"
            )
        return v

    @field_validator("description")
    @classmethod
    def validate_description_length(cls, v: str) -> str:
        if len(v) < 50:
            raise ValueError(
                f"Description too short ({len(v)} chars). Must be at least 50 chars to be useful for code generation."
            )
        return v


class ImplementationPlan(BaseModel):
    tasks: List[TaskSchema]

    def get_phase_tasks(self, phase: str) -> List[TaskSchema]:
        return [t for t in self.tasks if t.phase == phase]

    def get_task_by_id(self, task_id: str) -> Optional[TaskSchema]:
        return next((t for t in self.tasks if t.id == task_id), None)

    def validate_dependencies(self) -> tuple[bool, list[str]]:
        """Check that all dependency IDs reference real tasks in the plan."""
        all_ids = {t.id for t in self.tasks}
        errors: list[str] = []
        for task in self.tasks:
            for dep_id in task.requires:
                if dep_id not in all_ids:
                    errors.append(f"Task {task.id} references unknown dependency '{dep_id}'")
        return len(errors) == 0, errors

    def get_execution_order(self) -> List[List[TaskSchema]]:
        """
        Return tasks grouped into execution waves.
        Wave 1: tasks with no dependencies.
        Wave 2: tasks whose dependencies are all in wave 1.
        Wave N: tasks whose dependencies are all in previous waves.
        """
        completed = set()
        waves = []
        remaining = list(self.tasks)

        while remaining:
            wave = []
            still_remaining = []
            for task in remaining:
                if all(dep in completed for dep in task.requires):
                    wave.append(task)
                else:
                    still_remaining.append(task)

            if not wave:
                waves.append(still_remaining)
                break

            waves.append(wave)
            completed.update(t.id for t in wave)
            remaining = still_remaining

        return waves

    def summary(self) -> dict:
        return {
            "total": len(self.tasks),
            "database": len(self.get_phase_tasks("database")),
            "backend": len(self.get_phase_tasks("backend")),
            "frontend": len(self.get_phase_tasks("frontend")),
            "devops": len(self.get_phase_tasks("devops")),
            "low_complexity": len([t for t in self.tasks if t.estimated_complexity == "low"]),
            "medium_complexity": len(
                [t for t in self.tasks if t.estimated_complexity == "medium"]
            ),
            "high_complexity": len([t for t in self.tasks if t.estimated_complexity == "high"]),
        }
