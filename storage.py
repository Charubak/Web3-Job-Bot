import os
import sqlite3
import hashlib
from copy import copy
from datetime import datetime, timezone, timedelta
from pathlib import Path

from boards import canonical_job_url
from filters import MAX_AGE_DAYS, _parse_posted_date

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
    # History is append-only in identity/expiry: never prune expired or sent jobs.
    # Keep delivery and activation tables unchanged, including pending fences.
    conn.execute("""CREATE TABLE IF NOT EXISTS job_history (
        id TEXT PRIMARY KEY, title TEXT, url TEXT,
        first_seen_at TEXT NOT NULL, first_posted_at TEXT, expired_at TEXT
    )""")
    conn.commit()
    return conn


def _history_id(job) -> str:
    url = canonical_job_url(job.url)
    return hashlib.sha256(url.encode()).hexdigest()[:16] if url else job.id


def reconcile_job_history(jobs: list, *, persist: bool = False,
                          now: datetime | None = None) -> list:
    """Keep the earliest posting date and permanent expiry across repeated runs.

    First observation is only an upper bound on publication, never proof of a
    posting date. Unknown original dates require a verified current opening; future dates cannot qualify. Preview only reads;
    explicit prepare/send/initialize calls may persist history in the same DB
    already saved alongside delivery fences and activation state.
    """
    if not jobs:
        return []
    now = now or datetime.now(timezone.utc)
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    now = now.astimezone(timezone.utc)
    cutoff = now - timedelta(days=MAX_AGE_DAYS)
    history = {}
    if DB_PATH.exists():
        conn = sqlite3.connect(DB_PATH.resolve().as_uri() + "?mode=ro", uri=True)
        try:
            if conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='job_history'").fetchone():
                history = {row[0]: row[1:] for row in conn.execute(
                    "SELECT id, first_seen_at, first_posted_at, expired_at FROM job_history")}
            # Legacy delivery records also prove the job existed by that date.
            # They provide an age ceiling only, never a fabricated posting date.
            for table, date_column in (("seen_jobs", "seen_at"), ("unconfirmed_jobs", "attempted_at")):
                if conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)).fetchone():
                    for job_id, url, recorded_at in conn.execute(f"SELECT id, url, {date_column} FROM {table}"):
                        recorded = _parse_posted_date(recorded_at, now=now)
                        if recorded is None or recorded > now:
                            continue
                        canonical = canonical_job_url(url)
                        key = hashlib.sha256(canonical.encode()).hexdigest()[:16] if canonical else job_id
                        first_seen, first_posted, expired = history.get(key, (recorded.isoformat(), None, None))
                        oldest_seen = min(recorded, _parse_posted_date(first_seen, now=now))
                        history[key] = (oldest_seen.isoformat(), first_posted, expired)
        finally:
            conn.close()
    # Reconcile all aliases before returning so listing order cannot rejuvenate
    # an older copy of the same canonical job within one scrape.
    updates = {}
    for job in jobs:
        key = _history_id(job)
        first_seen, first_posted, expired = history.get(key, (now.isoformat(), None, None))
        observed = _parse_posted_date(job.posted, now=now)
        known = _parse_posted_date(first_posted, now=now)
        if observed is not None and observed <= now:
            known = min(known, observed) if known else observed
        first_seen_date = _parse_posted_date(first_seen, now=now)
        upper = _parse_posted_date(getattr(job, "published_at_upper_bound", ""), now=now)
        if upper is not None and upper <= now:
            # This is an existence bound, not a claimed original posting date.
            first_seen_date = min(first_seen_date, upper)
            first_seen = first_seen_date.isoformat()
        oldest = min(d for d in (known, first_seen_date) if d is not None)
        if oldest < cutoff and not expired:
            expired = now.isoformat()
        first_posted = known.isoformat() if known else None
        history[key] = (first_seen, first_posted, expired)
        updates[key] = (key, job.title, job.url, first_seen, first_posted, expired)
    reconciled = []
    for job in jobs:
        first_seen, first_posted, expired = history[_history_id(job)]
        original = _parse_posted_date(job.posted, now=now)
        listing = copy(job)
        if expired:
            # Expiry wins even if an upstream source refreshes or removes its date.
            oldest = min(d for d in (_parse_posted_date(first_seen, now=now),
                                    _parse_posted_date(first_posted, now=now)) if d is not None)
            listing.posted = oldest.isoformat()
        elif original is not None and original <= now and first_posted:
            listing.posted = first_posted
        reconciled.append(listing)
    if persist:
        conn = _connect()
        try:
            conn.executemany("""INSERT INTO job_history
                (id, title, url, first_seen_at, first_posted_at, expired_at)
                VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    first_seen_at = MIN(job_history.first_seen_at, excluded.first_seen_at),
                    first_posted_at = CASE
                        WHEN job_history.first_posted_at IS NULL THEN excluded.first_posted_at
                        WHEN excluded.first_posted_at IS NULL THEN job_history.first_posted_at
                        ELSE MIN(job_history.first_posted_at, excluded.first_posted_at) END,
                    expired_at = COALESCE(job_history.expired_at, excluded.expired_at)
                """, updates.values())
            conn.commit()
        finally:
            conn.close()
    return reconciled


def filter_unseen(jobs: list) -> list:
    """Return only jobs whose id has not been seen before."""
    if not jobs:
        return []
    # Reading/previewing must never create or modify delivery state.
    if not DB_PATH.exists():
        return list(jobs)
    conn = sqlite3.connect(DB_PATH.resolve().as_uri() + "?mode=ro", uri=True)
    seen = set()
    seen_urls = set()
    seen_cards = set()
    try:
        # Compare legacy URLs as well as IDs: older DBs hashed tracking parameters.
        for table in ("seen_jobs", "unconfirmed_jobs"):
            if conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)).fetchone():
                for job_id, url in conn.execute(f"SELECT id, url FROM {table}"):
                    seen.add(job_id)
                    if url:
                        seen_urls.add(canonical_job_url(url))
        if conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='telegram_latest_review_jobs'").fetchone():
            for job_id, url, title in conn.execute("SELECT id, url, title FROM telegram_latest_review_jobs"):
                seen.add(job_id)
                if title:
                    seen_cards.add((canonical_job_url(url), title.strip().casefold()))
                elif url:
                    seen_urls.add(canonical_job_url(url))
        if conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='job_history'").fetchone():
            seen.update(row[0] for row in conn.execute("SELECT id FROM job_history WHERE expired_at IS NOT NULL"))
    finally:
        conn.close()
    return [j for j in jobs if j.id not in seen and _history_id(j) not in seen
            and (not j.url or canonical_job_url(j.url) not in seen_urls)
            and (canonical_job_url(j.url), j.title.strip().casefold()) not in seen_cards]


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


def delivery_labels(jobs: list) -> dict[str, str]:
    """Read historical acknowledgments separately from uncertain delivery fences."""
    labels = {j.id: 'new' for j in jobs}
    if not DB_PATH.exists():
        return labels
    conn = sqlite3.connect(DB_PATH.resolve().as_uri() + '?mode=ro', uri=True)
    try:
        tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        confirmed, pending, cards = set(), set(), {}
        for table, dest in (('seen_jobs', confirmed), ('unconfirmed_jobs', pending)):
            if table in tables:
                for identity, url in conn.execute(f'SELECT id, url FROM {table}'):
                    dest.add(identity); dest.add(canonical_job_url(url))
        if 'telegram_latest_review_jobs' in tables:
            acknowledged = ('telegram_latest_job_review' in tables and
                            conn.execute("SELECT 1 FROM telegram_latest_job_review WHERE status='confirmed'").fetchone())
            for identity, url, title in conn.execute('SELECT id, url, title FROM telegram_latest_review_jobs'):
                state = 'previously_shared' if acknowledged else 'unconfirmed'
                if title:
                    cards[(canonical_job_url(url), title.strip().casefold())] = state
                else:
                    dest = confirmed if acknowledged else pending
                    dest.add(identity); dest.add(canonical_job_url(url))
        for j in jobs:
            url = canonical_job_url(j.url)
            if j.id in pending or url in pending:
                labels[j.id] = 'unconfirmed'
            elif j.id in confirmed or url in confirmed:
                labels[j.id] = 'previously_shared'
            elif (url, j.title.strip().casefold()) in cards:
                labels[j.id] = cards[(url, j.title.strip().casefold())]
    finally:
        conn.close()
    return labels
