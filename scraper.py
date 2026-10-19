#!/usr/bin/env python3
"""Fetch → assess evidence → deduplicate → optionally deliver confirmed matches.

Preview is the default. --send is explicit; preview never marks listings as sent.
"""
import argparse
import json
import os
import sys
from pathlib import Path
from dataclasses import asdict
from datetime import datetime, timezone, timedelta

from boards import fetch_all, Job
from employers import enrich_employers
from filters import filter_jobs
from storage import DB_PATH, filter_unseen, initialize_state, mark_seen, mark_unconfirmed, clear_unconfirmed, are_unconfirmed

_DATA_DIR = Path(os.environ.get("DATA_DIR", Path(__file__).parent))
COMPANIES_CACHE = _DATA_DIR / "current_companies.json"


class SetupError(RuntimeError):
    """An actionable, safe-to-display setup problem, with no secret values."""


def _save_companies(jobs: list) -> None:
    COMPANIES_CACHE.parent.mkdir(parents=True, exist_ok=True)
    names = {j.company.strip() for j in jobs if (j.company or "").strip()}
    COMPANIES_CACHE.write_text(json.dumps(sorted(names)), encoding="utf-8")


def _record(job, reason: str = "", timezone_note: str = "") -> dict:
    # Only public listing metadata; never credentials, chat IDs or full descriptions.
    return {"title": job.title, "company": job.company, "location": job.location,
            "url": job.url, "source": job.source, "posted": job.posted,
            "reason": reason, "timezone_note": timezone_note}


def main(dry_run: bool = True, *, initialize: bool = False,
         require_state: bool = True, report_path: str | None = None,
         max_alerts: int = 10, prepare_path: str | None = None) -> dict:
    if max_alerts < 1 or max_alerts > 50:
        raise ValueError("max_alerts must be between 1 and 50")
    if prepare_path and (initialize or not dry_run):
        raise ValueError("Preparation cannot also send or initialize")
    if prepare_path and not DB_PATH.exists():
        raise SetupError("Delivery state is missing. Preview and initialize a no-send baseline first.")
    if initialize and not dry_run:
        raise ValueError("Initialization cannot send notifications")
    if not dry_run:
        from config import validate_telegram_config
        try:
            validate_telegram_config()
        except ValueError as exc:
            raise SetupError(str(exc)) from None
        if require_state and not DB_PATH.exists():
            raise SetupError("Delivery state is missing. Preview, then run --initialize-state to establish a no-send baseline before delivery.")

    print("=== Web3 Growth / GTM / Product Marketing jobs ===")
    print("[mode] " + ("PREPARE (fence jobs, no Telegram)" if prepare_path else "INITIALIZE (no Telegram)" if initialize else
                       "PREVIEW (no state changes or Telegram)" if dry_run else "SEND"))
    raw_jobs = fetch_all()
    if not raw_jobs:
        raise RuntimeError("No jobs were returned by any source; check source errors before retrying.")
    results = filter_jobs(enrich_employers(raw_jobs))
    new_jobs = filter_unseen(results.qualified)
    # Dates are already checked by filters; newest first, without importing delivery code.
    from filters import _parse_posted_date
    new_jobs.sort(key=lambda j: _parse_posted_date(j.posted) or datetime.min.replace(tzinfo=timezone.utc), reverse=True)
    selected = new_jobs[:max_alerts]
    report = {
        "mode": "prepare" if prepare_path else "initialize" if initialize else "preview" if dry_run else "send",
        "counts": {"raw": len(raw_jobs), "qualified": len(results.qualified),
                   "review": len(results.review), "rejected": len(results.rejected),
                   "new": len(new_jobs), "selected": len(selected)},
        "qualified": [_record(j) for j in selected],
        "review": [_record(d.job, d.reason, d.timezone_note) for d in results.review],
        "rejected": [_record(d.job, d.reason) for d in results.rejected],
    }
    print("[counts] " + json.dumps(report["counts"]))
    for job in selected:
        print(f"[qualified] {job.title} | {job.company} | {job.location} | {job.url}")
    for decision in results.review[:20]:
        print(f"[review, NOT alerted] {decision.job.title} | {decision.job.company}: {decision.reason}")
    if report_path:
        path = Path(report_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")

    if prepare_path:
        path = Path(prepare_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"created_at": datetime.now(timezone.utc).isoformat(),
                                    "jobs": [asdict(j) for j in selected]}), encoding="utf-8")
        mark_unconfirmed(selected)
        print(f"[prepare] Fenced {len(selected)} job(s). Persist state before delivering the plan.")
    elif initialize:
        initialize_state()
        mark_seen(results.qualified)
        print(f"[initialize] Baselined {len(results.qualified)} current matches without sending.")
    elif not dry_run:
        from notifier import send_jobs
        # Successful chunks are recorded immediately, even if a later chunk fails.
        send_jobs(selected, on_sent=mark_seen, on_attempt=mark_unconfirmed, on_rejected=clear_unconfirmed)
        _save_companies(results.qualified)
        print("[scraper] Delivery complete.")
    else:
        print(f"[preview] Would send {len(selected)} job(s). Nothing marked as sent.")
    return report


def deliver_prepared(path: str) -> None:
    """Deliver one freshly prepared, fenced plan after durable state is verified."""
    from config import validate_telegram_config
    from filters import classify_job
    from notifier import send_jobs
    try:
        validate_telegram_config()
    except ValueError as exc:
        raise SetupError(str(exc)) from None
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    created = datetime.fromisoformat(data["created_at"])
    now = datetime.now(timezone.utc)
    if created.tzinfo is None or not now - timedelta(minutes=30) <= created <= now:
        raise SetupError("Prepared plan expired or has an invalid timestamp. Preview and reconcile it first.")
    jobs = [Job(**entry) for entry in data["jobs"]]
    if len(jobs) > 50 or len({j.id for j in jobs}) != len(jobs):
        raise SetupError("Prepared plan contains too many or duplicate jobs")
    if not are_unconfirmed(jobs):
        raise SetupError("Prepared delivery fence is missing or mismatched; refusing to send")
    if any(classify_job(j).status != "qualified" for j in jobs):
        raise SetupError("Prepared job no longer qualifies; refusing to send")
    send_jobs(jobs, on_sent=mark_seen, on_attempt=mark_unconfirmed, on_rejected=clear_unconfirmed)
    print(f"[delivery] Acknowledged {len(jobs)} prepared job(s).")


def cli() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    modes = parser.add_mutually_exclusive_group()
    modes.add_argument("--dry-run", action="store_true", help="Preview without sending or changing delivery state (default)")
    modes.add_argument("--send", action="store_true", help="Send qualified jobs to the configured Telegram chat")
    modes.add_argument("--initialize-state", action="store_true", help="Baseline current matches without sending")
    modes.add_argument("--prepare-send", metavar="PLAN", help="Fence selected jobs and write a no-send plan")
    modes.add_argument("--deliver-prepared", metavar="PLAN", help="Send a fresh plan after its state fence is durably saved")
    parser.add_argument("--require-state", action="store_true", help="Refuse delivery if dedup state is missing")
    parser.add_argument("--report", help="Write a JSON assessment report to this explicit path")
    parser.add_argument("--max-alerts", type=int, default=10)
    args = parser.parse_args()
    try:
        if args.deliver_prepared:
            deliver_prepared(args.deliver_prepared)
            return
        main(dry_run=not args.send, initialize=args.initialize_state,
             require_state=True, report_path=args.report, max_alerts=args.max_alerts, prepare_path=args.prepare_send)
    except SetupError as exc:
        print(f"[scraper] Setup required: {exc}", file=sys.stderr)
        sys.exit(1)
    except Exception as exc:
        # HTTP errors can embed URLs containing bot tokens. Avoid printing raw exceptions.
        print(f"[scraper] FAILED ({type(exc).__name__}). Check configuration and source/delivery summaries.", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    cli()
