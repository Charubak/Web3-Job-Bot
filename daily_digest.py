"""Daily status with a durable fence, separate from existing job history."""
import argparse
import hashlib
import html
import json
import os
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
    return local.date().isoformat() if local.hour >= 9 else None


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
    c = report['counts']
    sources = report.get('sources', [])
    failed = [s['source'] for s in sources if s['status'] != 'returned']
    zero = [s['source'] for s in sources if not s['count'] and s['status'] == 'returned']
    heading = 'TEST — Web3 daily digest' if test else 'Web3 daily digest'
    lines = [f'<b>{heading}</b> · {local:%d %b %Y, %H:%M} IST',
             f"{c['raw']} unique listings assessed; {c['qualified']} qualified; {c['review']} need review."]
    if c['new']:
        lines.append(f"{c['new']} new verified match(es); {c['selected']} selected" + (' (preview only).' if test else ' for delivery.'))
    else:
        lines.append('No new verified matches in the sources checked. Previously delivered roles are not repeated.')
    if not c['raw']:
        lines.append('Search coverage is unavailable or empty; this does not establish that there are no opportunities.')
    if failed:
        lines.append('Partial coverage — unavailable or incomplete: ' + ', '.join(html.escape(s) for s in failed) + '.')
    if not sources:
        lines.append('Source health unavailable; coverage could not be confirmed.')
    if zero:
        lines.append('No listings returned by: ' + ', '.join(html.escape(s) for s in zero) + '. Search/index coverage is best effort.')
    lines.append('Review candidates are withheld until eligibility is verified. Target: remote from India, foreign employers, suitable growth/GTM/PMM and marketing/content roles.')
    if test:
        lines.append('Delivery test only — no jobs are marked delivered or resent by this test.')
    return '\n\n'.join(lines)


def verify_activation():
    from activation import check
    from config import TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID
    if not check(TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID):
        raise RuntimeError('Verified activation required')


def prepare(*, test=False, now=None):
    key = TEST_KEY if test else daily_key(now)
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
                          prepare_path=None if test else str(PLAN.with_name('pending_plan.json')),
                          allow_empty=True)
    data = {'key': key, 'created_at': datetime.now(timezone.utc).isoformat(),
            'text': render(report, test=test, now=now), 'test': test,
            'selected': report['counts']['selected']}
    payload = json.dumps(data, sort_keys=True)
    digest = hashlib.sha256(payload.encode()).hexdigest()
    PLAN.parent.mkdir(parents=True, exist_ok=True)
    PLAN.write_text(payload)
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
    try:
        _send(data['text'])
    except DeliveryRejected:
        # Only a definitive rejection can release the fence. Timeouts/5xx cannot.
        with connect() as conn:
            conn.execute('DELETE FROM daily_digests WHERE day=?', (data['key'],))
        if not data['test']:
            from boards import Job
            jobs = json.loads(PLAN.with_name('pending_plan.json').read_text())['jobs']
            storage.clear_unconfirmed([Job(**j) for j in jobs])
        raise
    with connect() as conn:
        conn.execute("UPDATE daily_digests SET status='confirmed', acknowledged_at=? WHERE day=?", (now.isoformat(), data['key']))
    print('[daily] Telegram acknowledged ' + ('one labeled test digest.' if data['test'] else 'daily status.'))
    if not data['test'] and data['selected']:
        scraper.deliver_prepared(str(PLAN.with_name('pending_plan.json')))


def cli():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=['prepare', 'deliver'])
    parser.add_argument('--test', action='store_true')
    args = parser.parse_args()
    try:
        if args.action == 'prepare':
            ready = prepare(test=args.test)
            with open(os.environ['GITHUB_OUTPUT'], 'a') as out:
                out.write(f'prepared={str(ready).lower()}\n')
        else:
            deliver()
    except Exception as exc:
        print(f'[daily] FAILED ({type(exc).__name__}); inspect delivery fences and source report. No automatic uncertain retries.', file=sys.stderr)
        sys.exit(1)


if __name__ == '__main__':
    cli()
