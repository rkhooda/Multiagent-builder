import sqlite3
import os
from datetime import datetime

from app.models import status

# Path to projects.db in the backend root folder
backend_dir = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
db_path = os.path.join(backend_dir, "projects.db")

def get_db_connection():
    conn = sqlite3.connect(db_path, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    conn = get_db_connection()
    with conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS projects (
                id TEXT PRIMARY KEY,
                name TEXT,
                brief TEXT,
                status TEXT,
                current_stage TEXT,
                created_at TEXT
            )
        """)
        # updated_at: every status write stamps it, so the list can sort by
        # "last activity" and the zombie check can spot a `running` row that
        # nothing has touched since the server died.
        existing = {row["name"] for row in conn.execute("PRAGMA table_info(projects)")}
        if "updated_at" not in existing:
            conn.execute("ALTER TABLE projects ADD COLUMN updated_at TEXT")
            conn.execute("UPDATE projects SET updated_at = created_at WHERE updated_at IS NULL")
        for column, default in (("files_generated", 0), ("qa_issues_count", 0)):
            if column not in existing:
                conn.execute(f"ALTER TABLE projects ADD COLUMN {column} INTEGER DEFAULT {default}")
        migrated = status.migrate_legacy_rows(conn)
        if migrated:
            print(f"[DB] migrated {migrated} legacy status rows to the canonical vocabulary", flush=True)
    conn.close()

# Initialize database on module load
init_db()

# DB Helper functions
def insert_project(project_id: str, name: str, brief: str, project_status: str, current_stage: str):
    conn = get_db_connection()
    now = datetime.now().isoformat()
    with conn:
        conn.execute(
            "INSERT OR REPLACE INTO projects (id, name, brief, status, current_stage, created_at, updated_at)"
            " VALUES (?, ?, ?, ?, ?, ?, ?)",
            (project_id, name, brief, status.canonical(project_status), current_stage, now, now)
        )
    conn.close()

def update_project_status(project_id: str, project_status: str, current_stage: str):
    conn = get_db_connection()
    with conn:
        conn.execute(
            "UPDATE projects SET status = ?, current_stage = ?, updated_at = ? WHERE id = ?",
            (status.canonical(project_status), current_stage, datetime.now().isoformat(), project_id)
        )
    conn.close()


def update_project_rollups(project_id: str, files_generated=None, qa_issues_count=None):
    """Denormalised counts for the projects list.

    Freshness tradeoff: these are stamped opportunistically as the graph streams
    (see run_graph_background), NOT recomputed on read. Recomputing would mean a
    checkpoint load plus a directory walk per row on every list request. The cost
    is that a gate-4 per-file fix does not bump them — it changes neither the file
    count nor the QA issue count, so the staleness is not observable today.
    """
    sets, params = [], []
    if files_generated is not None:
        sets.append("files_generated = ?")
        params.append(files_generated)
    if qa_issues_count is not None:
        sets.append("qa_issues_count = ?")
        params.append(qa_issues_count)
    if not sets:
        return
    params.append(project_id)
    conn = get_db_connection()
    with conn:
        conn.execute(f"UPDATE projects SET {', '.join(sets)} WHERE id = ?", params)
    conn.close()


SORT_COLUMNS = {"created_at": "created_at", "updated_at": "updated_at", "name": "name", "status": "status"}

def get_all_projects(status_filter: str = None, sort: str = "created_at"):
    """Enriched project list, newest-first by default.

    sort/status are validated against fixed maps rather than interpolated — the
    column name cannot come from user input.
    """
    column = SORT_COLUMNS.get(sort, "created_at")
    direction = "ASC" if column == "name" else "DESC"
    sql = (
        "SELECT id, name, brief, status, current_stage, created_at, updated_at,"
        " files_generated, qa_issues_count FROM projects"
    )
    params = ()
    if status_filter:
        sql += " WHERE status = ?"
        params = (status.canonical(status_filter),)
    sql += f" ORDER BY {column} {direction}"

    conn = get_db_connection()
    rows = conn.execute(sql, params).fetchall()
    conn.close()
    return [dict(row) for row in rows]


def delete_project_row(project_id: str) -> int:
    conn = get_db_connection()
    with conn:
        cur = conn.execute("DELETE FROM projects WHERE id = ?", (project_id,))
    n = cur.rowcount
    conn.close()
    return n
