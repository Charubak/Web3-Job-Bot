import httpx
from datetime import datetime, timezone
from config import TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID, SILENT_IF_EMPTY
from filters import _parse_posted_date

TELEGRAM_API = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}"
MAX_MSG_LEN = 4096


def _sort_by_recency(jobs: list) -> list:
    """Sort newest-first; jobs with unparseable dates go to the end."""
    _epoch = datetime.min.replace(tzinfo=timezone.utc)

    def _key(j):
        dt = _parse_posted_date(j.posted)
        return dt if dt else _epoch

    return sorted(jobs, key=_key, reverse=True)


def _format_job(job) -> str:
    salary_part = f" | 💰 {job.salary}" if job.salary else ""
    location_part = job.location or "Remote"
    return (
        f"🟢 *{job.title}* — {job.company}\n"
        f"📍 {location_part}{salary_part}\n"
        f"🔗 [Apply]({job.url}) · _{job.source}_"
    )


def _split_messages(lines: list[str]) -> list[str]:
    """Chunk formatted job strings into Telegram-safe messages."""
    messages = []
    current = ""
    for line in lines:
        block = line + "\n\n"
        if len(current) + len(block) > MAX_MSG_LEN:
            if current:
                messages.append(current.strip())
            current = block
        else:
            current += block
    if current.strip():
        messages.append(current.strip())
    return messages


def _send(text: str) -> None:
    resp = httpx.post(
        f"{TELEGRAM_API}/sendMessage",
        json={
            "chat_id": TELEGRAM_CHAT_ID,
            "text": text,
            "parse_mode": "Markdown",
            "disable_web_page_preview": True,
        },
        timeout=15,
    )
    resp.raise_for_status()


def send_jobs(jobs: list) -> None:
    if not jobs:
        if not SILENT_IF_EMPTY:
            _send("No new Web3 marketing jobs today.")
        return

    jobs = _sort_by_recency(jobs)

    header = f"*🚀 {len(jobs)} New Web3 Marketing Job{'s' if len(jobs) != 1 else ''}*\n\n"
    formatted = [_format_job(j) for j in jobs]
    chunks = _split_messages(formatted)

    for i, chunk in enumerate(chunks):
        prefix = header if i == 0 else f"_(continued {i+1}/{len(chunks)})_\n\n"
        _send(prefix + chunk)

    print(f"[notifier] Sent {len(jobs)} job(s) across {len(chunks)} message(s).")
