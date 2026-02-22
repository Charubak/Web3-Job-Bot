#!/usr/bin/env python3
"""
Telegram bot — on-demand job fetcher + built-in 6-hour scheduler.

Commands:
  /jobs    — fetch fresh jobs from all boards (ignores dedup)
  /new     — send only jobs not yet seen in the dedup DB
  /clear   — delete all bot messages in this chat
  /twitter — X profiles of companies currently hiring for marketing
  /help    — show available commands

Run with: python3 bot.py
On cloud (Fly.io): this is the sole entry point — bot + scheduler in one process.
"""

import sys
import time
import threading
import schedule
import httpx
from config import TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID, SCRAPE_INTERVAL_HOURS

API = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}"
POLL_TIMEOUT = 30  # long-poll seconds
STALE_COMMAND_SECS = 60  # ignore commands older than this on startup

# Track IDs of messages the bot sends so /clear can delete them
_sent_msg_ids: list[int] = []

# Lock to prevent simultaneous /jobs or /new fetches
_fetch_lock = threading.Lock()


# ---------------------------------------------------------------------------
# Telegram helpers
# ---------------------------------------------------------------------------

def get_updates(offset: int) -> list:
    try:
        resp = httpx.get(
            f"{API}/getUpdates",
            params={"timeout": POLL_TIMEOUT, "offset": offset},
            timeout=POLL_TIMEOUT + 10,
        )
        data = resp.json()
        return data.get("result", [])
    except Exception as e:
        print(f"[bot] getUpdates error: {e}")
        time.sleep(5)
        return []


def send(text: str) -> None:
    try:
        resp = httpx.post(
            f"{API}/sendMessage",
            json={
                "chat_id": TELEGRAM_CHAT_ID,
                "text": text,
                "parse_mode": "Markdown",
                "disable_web_page_preview": True,
            },
            timeout=15,
        )
        data = resp.json()
        if data.get("ok"):
            _sent_msg_ids.append(data["result"]["message_id"])
    except Exception as e:
        print(f"[bot] send error: {e}")


# ---------------------------------------------------------------------------
# Command handlers
# ---------------------------------------------------------------------------

def handle_clear(user_msg_id: int) -> None:
    """Delete messages sent by this bot in the current process lifetime."""
    del user_msg_id
    to_delete = sorted(set(_sent_msg_ids))
    _sent_msg_ids.clear()
    if not to_delete:
        send("Nothing to clear yet.")
        return
    deleted = 0
    for msg_id in to_delete:
        try:
            resp = httpx.post(
                f"{API}/deleteMessage",
                json={"chat_id": TELEGRAM_CHAT_ID, "message_id": msg_id},
                timeout=5,
            )
            if resp.json().get("ok"):
                deleted += 1
        except Exception:
            pass
    print(f"[bot] /clear - deleted {deleted} of {len(to_delete)} tracked messages")


def _do_fetch_jobs(new_only: bool = False) -> None:
    """Actual job fetch — runs in a background thread."""
    if not _fetch_lock.acquire(blocking=False):
        send("⏳ Already fetching — please wait.")
        return
    try:
        send("🔍 Fetching jobs... give me a sec.")
        from boards import fetch_all
        from filters import apply_filters
        from notifier import send_jobs

        raw = fetch_all()
        jobs = apply_filters(raw)

        if new_only:
            from storage import filter_unseen
            jobs = filter_unseen(jobs)

        if not jobs:
            send("✅ No new jobs found right now. Check back later!")
            return

        label = "new (unseen)" if new_only else "latest"
        send(f"*🚀 {len(jobs)} {label} Web3 marketing job{'s' if len(jobs) != 1 else ''}:*")
        send_jobs(jobs)
        if new_only:
            from storage import mark_seen
            mark_seen(jobs)
    except Exception as e:
        send(f"❌ Error fetching jobs: {e}")
        print(f"[bot] handle_jobs error: {e}")
    finally:
        _fetch_lock.release()


def handle_jobs(new_only: bool = False) -> None:
    """Kick off job fetch in a background thread so the bot stays responsive."""
    threading.Thread(target=_do_fetch_jobs, args=(new_only,), daemon=True).start()


def handle_command(text: str, msg_id: int = 0) -> None:
    cmd = text.strip().lower().split()[0]
    if cmd in ("/jobs", "/jobs@" + "your_bot"):
        handle_jobs(new_only=False)
    elif cmd in ("/new",):
        handle_jobs(new_only=True)
    elif cmd in ("/clear",):
        handle_clear(msg_id)
    elif cmd in ("/twitter", "/x"):
        import json
        import os
        from company_handles import HANDLES
        cache_file = os.path.join(os.path.dirname(__file__), "current_companies.json")
        # Also check DATA_DIR (cloud volume)
        if not os.path.exists(cache_file):
            data_dir = os.environ.get("DATA_DIR", "")
            if data_dir:
                cache_file = os.path.join(data_dir, "current_companies.json")
        try:
            with open(cache_file) as f:
                companies = json.load(f)
        except Exception:
            companies = []

        links = []
        seen_handles: set[str] = set()
        for company in companies:
            handle = HANDLES.get(company.lower().strip())
            if handle and handle not in seen_handles:
                links.append(f"[{company}](https://x.com/{handle})")
                seen_handles.add(handle)

        if links:
            send(
                "*Companies currently hiring for marketing on X:*\n"
                "_Tap any to view their profile_\n\n"
                + "\n".join(links)
            )
        else:
            send(
                "No company data cached yet.\n"
                "Send /jobs to fetch fresh listings first."
            )
    elif cmd in ("/help", "/start"):
        send(
            "*Web3 Job Bot* 🤖\n\n"
            "/jobs — show latest Web3 marketing jobs\n"
            "/new — show only jobs you haven't seen yet\n"
            "/twitter — X profiles of companies hiring for marketing\n"
            "/clear — delete all bot messages in this chat\n"
            "/help — this message"
        )
    else:
        send("Unknown command. Try /jobs, /new, /clear, /twitter, or /help.")


# ---------------------------------------------------------------------------
# Built-in 6-hour scheduler (for cloud deployment where there's no cron)
# ---------------------------------------------------------------------------

def _scheduled_scrape() -> None:
    """Run the full scraper pipeline and send new jobs to Telegram."""
    if not _fetch_lock.acquire(blocking=False):
        print("[scheduler] Skipping run - fetch already in progress.")
        return
    print("[scheduler] Starting scheduled scrape...")
    try:
        import scraper
        scraper.main()
        print("[scheduler] Scrape complete.")
    except Exception as e:
        print(f"[scheduler] ERROR: {e}")
        send(f"⚠️ Scheduled scrape failed: {e}")
    finally:
        _fetch_lock.release()


def _run_scheduler() -> None:
    """Run _scheduled_scrape every configured interval. Executes once on start."""
    _scheduled_scrape()                       # run now on startup
    interval_hours = max(1, SCRAPE_INTERVAL_HOURS)
    print(f"[scheduler] Interval set to every {interval_hours} hour(s).")
    schedule.every(interval_hours).hours.do(_scheduled_scrape)
    while True:
        schedule.run_pending()
        time.sleep(60)


# ---------------------------------------------------------------------------
# Board failure alert — sent if total raw jobs drops below threshold
# ---------------------------------------------------------------------------

_RAW_JOB_THRESHOLD = 500   # alert if scrape returns fewer than this


def _check_board_health(raw_count: int) -> None:
    if raw_count < _RAW_JOB_THRESHOLD:
        send(
            f"⚠️ *Board health alert:* only {raw_count} raw jobs fetched "
            f"(expected >{_RAW_JOB_THRESHOLD}). One or more boards may be down."
        )


# ---------------------------------------------------------------------------
# Main loop
# ---------------------------------------------------------------------------

def main() -> None:
    print("[bot] Starting — listening for commands...")

    # Start the scheduler in a background thread
    scheduler_thread = threading.Thread(target=_run_scheduler, daemon=True)
    scheduler_thread.start()

    offset = 0
    while True:
        updates = get_updates(offset)
        for update in updates:
            offset = update["update_id"] + 1
            message = update.get("message", {})
            text = message.get("text", "")
            chat_id = str(message.get("chat", {}).get("id", ""))

            if chat_id != TELEGRAM_CHAT_ID:
                continue

            if text.startswith("/"):
                msg_id = message.get("message_id", 0)
                msg_date = message.get("date", 0)
                if time.time() - msg_date > STALE_COMMAND_SECS:
                    print(f"[bot] Ignoring stale command: {text}")
                    continue
                print(f"[bot] Command received: {text}")
                handle_command(text, msg_id=msg_id)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\n[bot] Stopped.")
        sys.exit(0)
