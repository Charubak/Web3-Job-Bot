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
from filters import filter_jobs, classify_job
from storage import DB_PATH, filter_unseen, initialize_state, mark_seen, mark_unconfirmed, clear_unconfirmed, are_unconfirmed, reconcile_job_history

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
         max_alerts: int = 10, prepare_path: str | None = None,
         refresh_history: bool = False, allow_empty: bool = False, digest: bool = False) -> dict:
    if digest and not DB_PATH.exists():
        raise SetupError("Existing delivery history is required to label new roles and reminders")
    if max_alerts < 1 or max_alerts > 50:
        raise ValueError("max_alerts must be between 1 and 50")
    if refresh_history and (initialize or prepare_path or not dry_run):
        raise ValueError("History refresh cannot initialize, prepare, or send")
    if prepare_path and (initialize or not dry_run):
        raise ValueError("Preparation cannot also send or initialize")
    if prepare_path and not DB_PATH.exists():
        raise SetupError("Delivery state is missing. Preview and initialize a no-send baseline first.")
    if initialize and not dry_run:
        raise ValueError("Initialization cannot send notifications")
    if refresh_history and not DB_PATH.exists():
        raise SetupError("Delivery state is missing. History refresh requires the existing state database.")
    if not dry_run:
        from config import validate_telegram_config
        try:
            validate_telegram_config()
        except ValueError as exc:
            raise SetupError(str(exc)) from None
        if require_state and not DB_PATH.exists():
            raise SetupError("Delivery state is missing. Preview, then run --initialize-state to establish a no-send baseline before delivery.")

    print("=== Web3 Growth / GTM / Product Marketing jobs ===")
    print("[mode] " + ("REFRESH HISTORY (no delivery changes or Telegram)" if refresh_history else
                       "PREPARE (fence jobs, no Telegram)" if prepare_path else "INITIALIZE (no Telegram)" if initialize else
                       "PREVIEW (no state changes or Telegram)" if dry_run else "SEND"))
    raw_jobs = fetch_all()
    if not raw_jobs and not allow_empty:
        raise RuntimeError("No jobs were returned by any source; check source errors before retrying.")
    jobs = reconcile_job_history(raw_jobs, persist=bool(refresh_history or prepare_path or initialize or not dry_run))
    results = filter_jobs(enrich_employers(jobs))
    new_jobs = filter_unseen(results.qualified)
    # Dates are already checked by filters; newest first, without importing delivery code.
    from filters import _parse_posted_date
    new_jobs.sort(key=lambda j: _parse_posted_date(j.posted) or datetime.min.replace(tzinfo=timezone.utc), reverse=True)
    selected = new_jobs[:max_alerts]
    report_qualified = []
    for job in results.qualified if refresh_history else selected:
        decision = classify_job(job)
        report_qualified.append(_record(job, decision.reason, decision.timezone_note))
    from source_health import snapshot
    report = {
        "sources": snapshot(),
        "mode": "refresh-history" if refresh_history else "prepare" if prepare_path else "initialize" if initialize else "preview" if dry_run else "send",
        "counts": {"raw": len(raw_jobs), "qualified": len(results.qualified),
                   "review": len(results.review), "rejected": len(results.rejected),
                   "new": len(new_jobs), "selected": len(selected)},
        "qualified": report_qualified,
        "review": [_record(d.job, d.reason, d.timezone_note) for d in results.review],
        "rejected": [_record(d.job, d.reason) for d in results.rejected],
    }
    if digest:
        from storage import delivery_labels
        labels = delivery_labels(results.qualified)
        active = [j for j in results.qualified if j.current_open_verified is True]
        included = [j for j in active if labels[j.id] != 'unconfirmed']
        report['digest_jobs'] = [{'job': asdict(j), 'label': labels[j.id]} for j in included]
        report['counts']['reminders'] = sum(labels[j.id] == 'previously_shared' for j in included)
        report['counts']['digest_new'] = sum(labels[j.id] == 'new' for j in included)
        report['counts']['active_unverified'] = len(results.qualified) - len(active)
        report['counts']['unconfirmed_delivery'] = len(active) - len(included)
        report['counts']['unknown_age'] = sum(_parse_posted_date(j.posted) is None for j in included)
    print("[counts] " + json.dumps(report["counts"]))
    print("[coverage] " + json.dumps(report["sources"]))
    for job in selected:
        print(f"[qualified] {job.title} | {job.company} | {job.location} | {job.url}")
    for decision in results.review[:20]:
        print(f"[review, NOT alerted] {decision.job.title} | {decision.job.company}: {decision.reason}")
    if report_path:
        path = Path(report_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")

    if refresh_history:
        print("[history] Refreshed age and expiry history. Nothing marked as sent or pending; no Telegram Bot API requests or messages.")
    elif prepare_path:
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
    jobs = reconcile_job_history(jobs)
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
    modes.add_argument("--refresh-history", action="store_true", help="Persist age/expiry history only; requires existing state and never sends")
    modes.add_argument("--prepare-send", metavar="PLAN", help="Fence selected jobs and write a no-send plan")
    modes.add_argument("--deliver-prepared", metavar="PLAN", help="Send a fresh plan after its state fence is durably saved")
    parser.add_argument("--require-state", action="store_true", help="Refuse delivery if dedup state is missing")
    parser.add_argument("--report", help="Write a JSON assessment report to this explicit path")
    parser.add_argument("--max-alerts", type=int, default=10)
    parser.add_argument("--digest", action="store_true", help="Include freshly verified reminder roles in the read-only report")
    args = parser.parse_args()
    try:
        if args.deliver_prepared:
            deliver_prepared(args.deliver_prepared)
            return
        main(dry_run=not args.send, initialize=args.initialize_state,
             require_state=True, report_path=args.report, max_alerts=args.max_alerts,
             prepare_path=args.prepare_send, refresh_history=args.refresh_history, digest=args.digest)
    except SetupError as exc:
        print(f"[scraper] Setup required: {exc}", file=sys.stderr)
        sys.exit(1)
    except Exception as exc:
        # HTTP errors can embed URLs containing bot tokens. Avoid printing raw exceptions.
        print(f"[scraper] FAILED ({type(exc).__name__}). Check configuration and source/delivery summaries.", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    cli()
