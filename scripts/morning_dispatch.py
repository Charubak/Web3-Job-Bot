#!/usr/bin/env python3
"""No-cost Mac wake-up for the existing GitHub workflow; no Telegram credentials.

launchd invokes every minute. A local India-date marker bounds dispatches, and
GitHub's shared concurrency/daily delivery ledger prevents duplicate messages.
The Mac must be logged in and online; GitHub cron is independent fallback.
"""
import argparse
import fcntl
import json
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

GH = '/opt/homebrew/bin/gh'
REPO = 'Charubak/Web3-Job-Bot'
STATE = Path(__file__).resolve().parents[1] / 'data' / 'morning_dispatch.json'
# The authorized labeled test runs today; daily delivery begins tomorrow.
START_DATE = '2026-10-23'


def due(now, state):
    local = now.astimezone(ZoneInfo('Asia/Kolkata'))
    today = local.date().isoformat()
    return today if local.hour >= 13 and today >= START_DATE and state.get('dispatched') != today else None


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--check', action='store_true', help='Show readiness without dispatching or writing')
    args = parser.parse_args()
    now = datetime.now(timezone.utc)
    if args.check:
        state = json.loads(STATE.read_text()) if STATE.exists() else {}
        print(json.dumps({'repository': REPO, 'target': '13:00 Asia/Kolkata',
                          'start_date': START_DATE, 'due': bool(due(now, state)),
                          'gh_available': Path(GH).is_file()}))
        return
    STATE.parent.mkdir(parents=True, exist_ok=True)
    with STATE.with_suffix('.lock').open('w') as lock:
        try: fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError: return
        state = json.loads(STATE.read_text()) if STATE.exists() else {}
        day = due(now, state)
        if day is None: return
        # Bound retries to once per 15 minutes even if the API is unavailable.
        attempted = state.get('attempted_at', 0)
        if now.timestamp() - attempted < 900: return
        state['attempted_at'] = now.timestamp()
        STATE.write_text(json.dumps(state))
        result = subprocess.run([GH, 'workflow', 'run', 'jobs.yml', '--repo', REPO,
                                 '--ref', 'main', '-f', 'mode=send'],
                                capture_output=True, text=True, timeout=60)
        if result.returncode:
            # Never print CLI stderr: credentials or account context may appear.
            print('[morning] Dispatch not confirmed; later checks use the workflow daily guard.', flush=True)
            return
        state['dispatched'] = day
        STATE.write_text(json.dumps(state))
        print(f'[morning] Requested daily workflow for {day} India date.', flush=True)


if __name__ == '__main__': main()
