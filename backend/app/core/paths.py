"""Where the data that must outlive a container lives.

Three modules each recomputed this with their own dirname chain. They must
agree — if one ever drifted, the checkpointer and the project list would read
different projects.db files and the app would quietly disagree with itself.

DATA_DIR defaults to the backend directory, so running locally is byte-identical
to before. The container sets DATA_DIR=/data, a mounted DIRECTORY and never a
mounted file: sqlite in WAL mode writes -wal and -shm siblings that have to sit
beside the database, and a single-file mount leaves them in the container's
throwaway layer. A commit still in the WAL when the container stops would then
vanish with no error at all.
"""
import os

DATA_DIR = os.getenv("DATA_DIR") or os.path.dirname(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
)


def data_path(name: str) -> str:
    return os.path.join(DATA_DIR, name)
