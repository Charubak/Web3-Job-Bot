import os
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

# On Fly.io the /data volume is mounted here; locally use the project dir
_data_dir = Path(os.environ.get("DATA_DIR", Path(__file__).parent))
DB_PATH = _data_dir / "seen_jobs.db"


def _connect() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
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
    conn.execute("CREATE TABLE IF NOT EXISTS unconfirmed_jobs (id TEXT PRIMARY KEY, title TEXT, url TEXT, attempted_at TIMESTAMP)")
    conn.commit()
    return conn


def filter_unseen(jobs: list) -> list:
    """Return only jobs whose id has not been seen before."""
    if not jobs:
        return []
    # Reading/previewing must never create or modify delivery state.
    if not DB_PATH.exists():
        return list(jobs)
    conn = sqlite3.connect(DB_PATH.resolve().as_uri() + "?mode=ro", uri=True)
    ids = [j.id for j in jobs]
    seen = set()
    try:
        for start in range(0, len(ids), 900):
            batch = ids[start:start + 900]
            placeholders = ",".join("?" * len(batch))
            seen.update(row[0] for row in conn.execute(
                f"SELECT id FROM seen_jobs WHERE id IN ({placeholders})", batch
            ))
        has_unconfirmed = conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='unconfirmed_jobs'").fetchone()
        if has_unconfirmed:
            seen.update(row[0] for row in conn.execute("SELECT id FROM unconfirmed_jobs"))
    finally:
        conn.close()
    return [j for j in jobs if j.id not in seen]


def mark_seen(jobs: list) -> None:
    """Insert jobs into seen_jobs to prevent future duplicates."""
    if not jobs:
        return
    conn = _connect()
    now = datetime.now(timezone.utc).isoformat()
    conn.executemany(
        "INSERT OR IGNORE INTO seen_jobs (id, title, url, seen_at) VALUES (?, ?, ?, ?)",
        [(j.id, j.title, j.url, now) for j in jobs],
    )
    conn.executemany("DELETE FROM unconfirmed_jobs WHERE id = ?", [(j.id,) for j in jobs])
    conn.commit()
    conn.close()


def mark_unconfirmed(jobs: list) -> None:
    """Quarantine ambiguous sends: automated runs must not resend them blindly."""
    conn = _connect()
    now = datetime.now(timezone.utc).isoformat()
    conn.executemany(
        "INSERT OR REPLACE INTO unconfirmed_jobs (id, title, url, attempted_at) VALUES (?, ?, ?, ?)",
        [(j.id, j.title, j.url, now) for j in jobs],
    )
    conn.commit()
    conn.close()


def initialize_state() -> None:
    """Create a valid empty state database only on explicit initialization."""
    conn = _connect()
    conn.close()



def clear_unconfirmed(jobs: list) -> None:
    """Release only definitely rejected requests for a later fresh attempt."""
    conn = _connect()
    conn.executemany('DELETE FROM unconfirmed_jobs WHERE id = ?', [(j.id,) for j in jobs])
    conn.commit()
    conn.close()


def are_unconfirmed(jobs: list) -> bool:
    if not DB_PATH.exists():
        return False
    conn = sqlite3.connect(DB_PATH.resolve().as_uri() + '?mode=ro', uri=True)
    try:
        rows = set(conn.execute('SELECT id, title, url FROM unconfirmed_jobs'))
        return all((j.id, j.title, j.url) in rows for j in jobs)
    except sqlite3.OperationalError:
        return False
    finally:
        conn.close()
