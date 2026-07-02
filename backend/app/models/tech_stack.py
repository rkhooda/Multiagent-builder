from pydantic import BaseModel, Field
from typing import List


class TechStackSchema(BaseModel):
    frontend: str = Field(
        description="Frontend framework and key tools, e.g. 'React 19 + Vite + TailwindCSS'"
    )
    backend: str = Field(
        description="Backend framework and language, e.g. 'FastAPI (Python 3.11)'"
    )
    database: str = Field(
        description="Primary database, e.g. 'PostgreSQL' or 'SQLite'"
    )
    auth: str = Field(
        description="Authentication approach, e.g. 'JWT + bcrypt' or 'Supabase Auth'"
    )
    hosting: str = Field(
        description="Deployment target, e.g. 'Docker + VPS' or 'Vercel + Railway'"
    )
    key_libraries: List[str] = Field(
        default_factory=list,
        description="Important third-party libraries, e.g. ['SQLAlchemy', 'Stripe', 'SendGrid']"
    )

    def to_prompt_string(self) -> str:
        """Format the tech stack as a readable string for injection into coder agent prompts."""
        libs = ", ".join(self.key_libraries) if self.key_libraries else "None specified"
        return (
            f"Frontend: {self.frontend}\n"
            f"Backend: {self.backend}\n"
            f"Database: {self.database}\n"
            f"Auth: {self.auth}\n"
            f"Hosting: {self.hosting}\n"
            f"Key Libraries: {libs}"
        )


# Sensible defaults used when JSON parsing fails
DEFAULT_TECH_STACK = TechStackSchema(
    frontend="React 19 + Vite + TailwindCSS",
    backend="FastAPI (Python 3.11)",
    database="PostgreSQL",
    auth="JWT + bcrypt",
    hosting="Docker + VPS",
    key_libraries=["SQLAlchemy", "Alembic", "Pydantic"]
)
