#!/usr/bin/env python3
"""
Web3 Job Scraper — main entry point.

Flow:
  fetch all boards → filter by keywords/location → dedup → send Telegram → mark seen

Usage:
  python scraper.py              # normal run (requires .env with Telegram credentials)
  python scraper.py --dry-run    # fetch + filter + dedup, print results, skip Telegram
"""

import sys
import json
import os
from pathlib import Path
from boards import fetch_all
from filters import apply_filters
from storage import filter_unseen, mark_seen

_DATA_DIR = os.environ.get("DATA_DIR", str(Path(__file__).parent))
COMPANIES_CACHE = str(Path(_DATA_DIR) / "current_companies.json")


_INVALID_COMPANIES = {"at", "@", "dev.fun", "", "n/a"}

def _save_companies(jobs: list) -> None:
    """Save unique companies with open marketing roles to a cache file."""
    seen_lower: set[str] = set()
    companies = []
    for j in jobs:
        name = (j.company or "").strip()
        key = name.lower()
        # Skip parse errors and duplicates (case-insensitive)
        if not name or key in _INVALID_COMPANIES or "." in key and len(key) < 8:
            continue
        if key in seen_lower:
            continue
        seen_lower.add(key)
        companies.append(name)
    companies.sort()
    with open(COMPANIES_CACHE, "w") as f:
        json.dump(companies, f)
    print(f"[scraper] Saved {len(companies)} companies to cache")


def main(dry_run: bool = False) -> None:
    print("=== Web3 Job Scraper ===")
    if dry_run:
        print("[mode] DRY RUN — Telegram notifications skipped\n")

    # 1. Fetch from all boards
    raw_jobs = fetch_all()
    print(f"\n[scraper] Total raw jobs fetched: {len(raw_jobs)}")

    # Alert if total is suspiciously low (boards may be down)
    if not dry_run and len(raw_jobs) < 500:
        try:
            from notifier import _send
            _send(
                f"⚠️ *Board health alert:* only {len(raw_jobs)} raw jobs fetched "
                f"(expected >500). One or more boards may be down."
            )
        except Exception:
            pass

    # 2. Apply keyword + location filters
    filtered = apply_filters(raw_jobs)
    print(f"[scraper] After filters: {len(filtered)}")

    # 3. Remove already-seen jobs
    new_jobs = filter_unseen(filtered)
    print(f"[scraper] New (unseen) jobs: {len(new_jobs)}")

    # Always save ALL filtered companies (not just new ones) so /twitter is fresh
    _save_companies(filtered)

    if dry_run:
        print("\n--- New Jobs Preview ---")
        if not new_jobs:
            print("  (none — all already seen or no matches)")
        for j in new_jobs:
            salary = f" | 💰 {j.salary}" if j.salary else ""
            print(f"  🟢 {j.title} — {j.company}")
            print(f"     📍 {j.location or 'Remote'}{salary}")
            print(f"     🔗 {j.url}")
            print(f"     [{j.source}]")
            print()
        print(f"[dry-run] Would send {len(new_jobs)} job(s) to Telegram.")
        print("[dry-run] Marking jobs as seen so re-runs test dedup.")
        mark_seen(new_jobs)
        return

    # 4. Send to Telegram (lazy import so --dry-run never touches config.py)
    from notifier import send_jobs
    send_jobs(new_jobs)

    # 5. Persist seen job IDs
    mark_seen(new_jobs)
    print("[scraper] Done.")


if __name__ == "__main__":
    dry_run = "--dry-run" in sys.argv
    if not dry_run:
        # Import config early to catch missing credentials before doing network work
        try:
            import config  # noqa: F401
        except ValueError as e:
            print(f"[scraper] Config error: {e}")
            print("[scraper] Tip: copy .env.example → .env and fill in your credentials.")
            print("[scraper] Or run with --dry-run to test without Telegram.")
            sys.exit(1)
    try:
        main(dry_run=dry_run)
    except Exception as e:
        print(f"[scraper] FATAL: {e}", file=sys.stderr)
        sys.exit(1)
