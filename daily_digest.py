"""Daily status with a durable fence, separate from existing job history."""
import argparse
import hashlib
import html
import json
import os
import re
import sqlite3
import sys
from contextlib import contextmanager
from datetime import datetime, timezone, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo
import scraper
import storage

IST = ZoneInfo('Asia/Kolkata')
PLAN = Path(os.environ.get('DATA_DIR', Path(__file__).parent)) / 'daily_plan.json'
TEST_KEY = 'delivery-test-2026-10-22-v1'


def daily_key(now=None):
    local = (now or datetime.now(timezone.utc)).astimezone(IST)
    return local.date().isoformat() if local.hour >= 13 else None


@contextmanager
def connect():
    if not storage.DB_PATH.is_file():
        raise RuntimeError('Existing delivery history required; never initialize automatically')
    conn = sqlite3.connect(storage.DB_PATH.resolve().as_uri() + '?mode=rw', uri=True)
    conn.execute('PRAGMA synchronous=FULL')
    conn.execute('''CREATE TABLE IF NOT EXISTS daily_digests (
        day TEXT PRIMARY KEY, status TEXT NOT NULL, plan_hash TEXT NOT NULL,
        acknowledged_at TEXT)''')
    conn.commit()
    try:
        with conn:
            yield conn
    finally:
        conn.close()


def render(report, *, test=False, now=None):
    local = (now or datetime.now(timezone.utc)).astimezone(IST)
    entries = report.get('digest_jobs', [])
    new = sum(e['label'] == 'new' for e in entries)
    shared = sum(e['label'] == 'previously_shared' for e in entries)
    heading = 'PREVIEW | Jobs to consider' if test else 'Jobs to consider'
    return (f'<b>{heading}</b> · {local:%d %b}\n'
            f'{new} new · {shared} previously shared')



def verify_activation():
    from activation import check
    from config import TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID
    if not check(TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID):
        raise RuntimeError('Verified activation required')


def prepare(*, test=False, test_id=None, now=None):
    if test_id and (not test or not re.fullmatch(r'[A-Za-z0-9_-]{1,80}', test_id)):
        raise ValueError('A bounded test identifier is allowed only for explicit test delivery')
    key = ('requested-test-' + test_id if test_id else TEST_KEY) if test else daily_key(now)
    if key is None:
        return False
    with connect() as conn:
        row = conn.execute('SELECT status FROM daily_digests WHERE day=?', (key,)).fetchone()
    if row:
        if row[0] != 'confirmed':
            raise RuntimeError('Daily delivery pending/unconfirmed; reconcile before retrying')
        return False
    verify_activation()
    # Public aggregate counts only: no recipient identifiers or configuration.
    with connect() as conn:
        for table in ('seen_jobs', 'unconfirmed_jobs', 'telegram_latest_review_jobs', 'job_history'):
            if conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)).fetchone():
                print(f"[history] {table}: {conn.execute('SELECT count(*) FROM '+table).fetchone()[0]} retained rows")
    report = scraper.main(report_path=str(PLAN.with_name('run_report.json')),
                          refresh_history=not test, digest=True, allow_empty=True)
    from digest_content import messages
    chunks = messages(report, render(report, test=test, now=now))
    data = {'messages': chunks, 'key': key, 'created_at': datetime.now(timezone.utc).isoformat(),
            'text': render(report, test=test, now=now), 'test': test,
            'selected': report['counts']['selected']}
    print('[daily-preview] ' + json.dumps([c['text'] for c in chunks], ensure_ascii=False))
    payload = json.dumps(data, sort_keys=True)
    digest = hashlib.sha256(payload.encode()).hexdigest()
    PLAN.parent.mkdir(parents=True, exist_ok=True)
    PLAN.write_text(payload)
    if not test:
        from boards import Job
        storage.mark_unconfirmed([Job(**j) for chunk in chunks for j in chunk['new_jobs']])
    with connect() as conn:
        conn.execute('INSERT INTO daily_digests VALUES (?, ?, ?, NULL)', (key, 'pending', digest))
    return True


def deliver():
    from notifier import _send, DeliveryRejected
    payload = PLAN.read_text()
    data = json.loads(payload)
    created = datetime.fromisoformat(data['created_at'])
    now = datetime.now(timezone.utc)
    if created.tzinfo is None or not now - timedelta(minutes=30) <= created <= now:
        raise RuntimeError('Daily plan expired; reconcile before retrying')
    if not data['test'] and data['key'] != daily_key(now):
        raise RuntimeError('Daily plan does not belong to current India date')
    digest = hashlib.sha256(payload.encode()).hexdigest()
    verify_activation()
    with connect() as conn:
        result = conn.execute("UPDATE daily_digests SET status='attempted' WHERE day=? AND status='pending' AND plan_hash=?", (data['key'], digest))
        if result.rowcount != 1:
            raise RuntimeError('Daily fence mismatched or already attempted; refusing replay')
    from boards import Job
    chunks = data['messages']
    acknowledged = 0
    for index, chunk in enumerate(chunks):
        try:
            _send(chunk['text'])
        except DeliveryRejected:
            if not data['test']:
                storage.clear_unconfirmed([Job(**j) for c in chunks[index:] for j in c['new_jobs']])
            if not acknowledged:
                with connect() as conn:
                    conn.execute('DELETE FROM daily_digests WHERE day=?', (data['key'],))
            # After any acknowledged chunk, retain the daily fence: no duplicate blast.
            raise
        if not data['test']:
            storage.mark_seen([Job(**j) for j in chunk['new_jobs']])
        acknowledged += 1
    with connect() as conn:
        conn.execute("UPDATE daily_digests SET status='confirmed', acknowledged_at=? WHERE day=?", (now.isoformat(), data['key']))
    print(f"[daily] Telegram acknowledged {'labeled test digest' if data['test'] else 'daily digest'} ({acknowledged} message(s)).")


def cli():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=['prepare', 'deliver'])
    parser.add_argument('--test', action='store_true')
    parser.add_argument('--test-id', help='Unique identifier for one explicitly requested additional test')
    args = parser.parse_args()
    try:
        if args.action == 'prepare':
            ready = prepare(test=args.test, test_id=args.test_id)
            with open(os.environ['GITHUB_OUTPUT'], 'a') as out:
                out.write(f'prepared={str(ready).lower()}\n')
        else:
            deliver()
    except Exception as exc:
        print(f'[daily] FAILED ({type(exc).__name__}); inspect delivery fences and source report. No automatic uncertain retries.', file=sys.stderr)
        sys.exit(1)


if __name__ == '__main__':
    cli()
