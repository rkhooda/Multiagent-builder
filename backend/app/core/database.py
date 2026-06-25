import sqlite3
import os
from datetime import datetime

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
    conn.close()

# Initialize database on module load
init_db()

# DB Helper functions
def insert_project(project_id: str, name: str, brief: str, status: str, current_stage: str):
    conn = get_db_connection()
    created_at = datetime.now().isoformat()
    with conn:
        conn.execute(
            "INSERT OR REPLACE INTO projects (id, name, brief, status, current_stage, created_at) VALUES (?, ?, ?, ?, ?, ?)",
            (project_id, name, brief, status, current_stage, created_at)
        )
    conn.close()

def update_project_status(project_id: str, status: str, current_stage: str):
    conn = get_db_connection()
    with conn:
        conn.execute(
            "UPDATE projects SET status = ?, current_stage = ? WHERE id = ?",
            (status, current_stage, project_id)
        )
    conn.close()

def get_all_projects():
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT id, name, brief, status, current_stage, created_at FROM projects ORDER BY created_at DESC")
    rows = cursor.fetchall()
    conn.close()
    return [dict(row) for row in rows]
