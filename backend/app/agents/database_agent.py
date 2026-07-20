import json
from pathlib import Path
from ..llm_router import call_llm
from ..utils.file_writer import write_project_file
from .utils import get_tasks_for_phase, get_completed_file_content, truncate_for_context, assert_single_owner

SYSTEM_PROMPT = (Path(__file__).resolve().parents[3] / "prompts" / "database_agent.md").read_text(encoding="utf-8")


def database_agent(state: dict) -> dict:
    """
    Database Agent — Phase 4 Code Generation (first coder agent)

    Reads: state['implementation_plan'], state['architecture_doc'], state['tech_stack']
    Writes: state['generated_files'] (merged with existing), state['log'], state['current_stage']

    Uses Groq Llama 3.3 70B — fast, reliable for SQL/ORM generation.
    Generates one file per database task, writes each to disk immediately,
    broadcasts progress via WebSocket after each file.
    """
    implementation_plan = state.get("implementation_plan", "[]")
    architecture_doc = state.get("architecture_doc", "")
    tech_stack_str = state.get("tech_stack", "")
    project_id = state.get("project_id", "")
    fast_mode = bool(state.get("fast_mode"))
    project_name = state.get("project_name", "Unknown Project")
    generated_files = state.get("generated_files", {})
    log = state.get("log", [])
    errors = state.get("errors", [])

    print(f"[DatabaseAgent] Starting for project: {project_name}")
    log.append("database_agent: started")

    # Ownership boundary (Day 19): database owns models/migrations, backend
    # coder owns the rest. Fail loudly here (before any write) if the plan
    # gave one filepath to both phases.
    assert_single_owner(implementation_plan)

    # ── Get database tasks from the plan ───────────────────────────
    db_tasks = get_tasks_for_phase(implementation_plan, "database")

    if not db_tasks:
        warning = "database_agent: no database tasks found in implementation plan"
        log.append(warning)
        print(f"[DatabaseAgent] WARNING: {warning}")
        return {
            "generated_files": generated_files,
            "log": log,
            "errors": errors,
            "current_stage": "backend_code"
        }

    print(f"[DatabaseAgent] Found {len(db_tasks)} database tasks")

    # ── Extract database schema section from architecture for context ─
    schema_context = ""
    if "## Database Schema" in architecture_doc:
        start = architecture_doc.find("## Database Schema")
        end = architecture_doc.find("## API Endpoints")
        if end == -1:
            end = start + 3000
        schema_context = architecture_doc[start:end]
    schema_context = truncate_for_context(schema_context, max_chars=3000)

    # ── Format tech stack ────────────────────────────────────────────
    tech_stack_formatted = ""
    if tech_stack_str:
        try:
            stack = json.loads(tech_stack_str)
            tech_stack_formatted = f"Database: {stack.get('database', 'PostgreSQL')}\nBackend: {stack.get('backend', 'FastAPI')}"
        except Exception:
            pass

    files_written = 0
    files_failed = 0

    # ── Generate each database file one at a time ──────────────────
    for i, task in enumerate(db_tasks):
        task_id = task.get("id", f"unknown_{i}")
        filepath = task.get("filepath", "")
        description = task.get("description", "")
        requires = task.get("requires", [])

        print(f"[DatabaseAgent] Generating {filepath} ({task_id})...")

        # Get content from required files for context injection
        context_content = get_completed_file_content(generated_files, requires, db_tasks)

        user_content = f"""PROJECT NAME: {project_name}

TECH STACK:
{tech_stack_formatted}

DATABASE SCHEMA FROM ARCHITECTURE:
{schema_context}

{context_content}

TASK:
File to generate: {filepath}
Description: {description}

Generate the complete file content now. Output ONLY the raw file code — no explanation, no markdown fences, no preamble. Start with the first import statement and end with the last line of code."""

        messages = [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": user_content}
        ]

        try:
            code = call_llm(messages, "database", max_tokens=2500,
                            project_id=project_id, label=filepath,
                            fast_mode=fast_mode)

            if len(code.strip()) < 30:
                # Retry once with stronger instruction
                messages.append({"role": "assistant", "content": code})
                messages.append({
                    "role": "user",
                    "content": "Your response was too short or empty. Generate the complete, full file content now."
                })
                code = call_llm(messages, "database", max_tokens=2500,
                            project_id=project_id, label=filepath,
                            fast_mode=fast_mode)

            # Write to disk immediately
            result = write_project_file(project_id, filepath, code)

            if result["success"]:
                generated_files[filepath] = code
                files_written += 1
                log.append(f"database_agent: wrote {filepath} ({result['size_bytes']} bytes)")

                # Broadcast progress
                from ..core.connection_manager import manager
                manager.broadcast_sync(project_id, {
                    "type": "file_written",
                    "filename": Path(filepath).name,
                    "filepath": filepath,
                    "phase": "database",
                    "task_id": task_id,
                    "progress": f"{i+1}/{len(db_tasks)}"
                })
            else:
                files_failed += 1
                errors.append(f"database_agent: failed to write {filepath}: {result['error']}")

        except Exception as e:
            files_failed += 1
            error_msg = f"database_agent: LLM call failed for {filepath}: {e}"
            errors.append(error_msg)
            print(f"[DatabaseAgent] ERROR: {error_msg}")

    # ── Final summary ────────────────────────────────────────────────
    log.append(f"database_agent: completed — {files_written} files written, {files_failed} failed")
    print(f"[DatabaseAgent] Done. {files_written} written, {files_failed} failed")

    from ..core.connection_manager import manager
    manager.broadcast_sync(project_id, {
        "type": "agent_complete",
        "agent": "database",
        "stage": "database",
        "output_preview": f"Generated {files_written} database files",
        "files_written": files_written,
        "files_failed": files_failed
    })

    return {
        "generated_files": generated_files,
        "log": log,
        "errors": errors,
        "current_stage": "backend_code",
        "_agent_event": True
    }