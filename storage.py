import os
import sqlite3
from datetime import datetime
from pathlib import Path

# On Fly.io the /data volume is mounted here; locally use the project dir
_data_dir = Path(os.environ.get("DATA_DIR", Path(__file__).parent))
DB_PATH = _data_dir / "seen_jobs.db"


def _connect() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS seen_jobs (
            id TEXT PRIMARY KEY,
            title TEXT,
            url TEXT,
            seen_at TIMESTAMP
        )
        """
    )
    conn.commit()
    return conn


def filter_unseen(jobs: list) -> list:
    """Return only jobs whose id has not been seen before."""
    if not jobs:
        return []
    conn = _connect()
    ids = [j.id for j in jobs]
    placeholders = ",".join("?" * len(ids))
    seen = {
        row[0]
        for row in conn.execute(
            f"SELECT id FROM seen_jobs WHERE id IN ({placeholders})", ids
        )
    }
    conn.close()
    return [j for j in jobs if j.id not in seen]


def mark_seen(jobs: list) -> None:
    """Insert jobs into seen_jobs to prevent future duplicates."""
    if not jobs:
        return
    conn = _connect()
    now = datetime.utcnow().isoformat()
    conn.executemany(
        "INSERT OR IGNORE INTO seen_jobs (id, title, url, seen_at) VALUES (?, ?, ?, ?)",
        [(j.id, j.title, j.url, now) for j in jobs],
    )
    conn.commit()
    conn.close()
