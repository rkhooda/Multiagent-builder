"""Canonical project status vocabulary.

Every prior day introduced a status ad hoc; the Day 24 projects list has to
render all of them, so this is the consolidation point. Plain string constants
rather than an Enum: the values are already strings in SQLite, in the WebSocket
payloads and in the frontend badge maps, and an Enum would only add .value
noise at every boundary.

Deliberately NOT added:
  - `draft`   — project creation starts the graph immediately; nothing would
                ever hold this status.
  - `failed`  — `error_paused` already means "stopped, recoverable, waiting for
                the user". A second terminal failure status nothing sets and
                nothing renders differently is dead vocabulary.
"""

RUNNING = "running"
AWAITING_APPROVAL = "awaiting_approval"
RATE_LIMITED = "rate_limited"
ERROR_PAUSED = "error_paused"
COMPLETED = "completed"
CANCELLED = "cancelled"

ALL = frozenset({RUNNING, AWAITING_APPROVAL, RATE_LIMITED, ERROR_PAUSED, COMPLETED, CANCELLED})

# Terminal = the graph will not advance again on its own.
TERMINAL = frozenset({COMPLETED, CANCELLED})
# The user can re-enter the graph from here (checkpoint holds a resumable `next`).
RESUMABLE = frozenset({AWAITING_APPROVAL, ERROR_PAUSED, RATE_LIMITED, RUNNING})

# Legacy values written before this module existed -> canonical replacement.
# `complete` never reached SQLite (only the frontend's badge map accepted it),
# but it is mapped here so a stray row from an older checkpoint normalises too.
LEGACY = {
    "error": ERROR_PAUSED,
    "complete": COMPLETED,
    "done": COMPLETED,
}


def canonical(status: str) -> str:
    """Normalise any historical status string. Unknown values fall back to
    ERROR_PAUSED rather than being rendered raw — an unrecognised status in the
    list is a bug, and 'stopped, needs attention' is the honest reading."""
    if status in ALL:
        return status
    return LEGACY.get(status, ERROR_PAUSED)


def migrate_legacy_rows(conn) -> int:
    """Rewrite legacy status values in the projects table. Idempotent."""
    total = 0
    for legacy, replacement in LEGACY.items():
        cur = conn.execute(
            "UPDATE projects SET status = ? WHERE status = ?", (replacement, legacy)
        )
        total += cur.rowcount
    return total
