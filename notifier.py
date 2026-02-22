import httpx
import html as html_lib
from datetime import datetime, timezone
from config import TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID, SILENT_IF_EMPTY
from filters import _parse_posted_date

TELEGRAM_API = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}"
MAX_MSG_LEN = 4096
_SPLIT_BUFFER = 200


def _sort_by_recency(jobs: list) -> list:
    """Sort newest-first; jobs with unparseable dates go to the end."""
    _epoch = datetime.min.replace(tzinfo=timezone.utc)

    def _key(j):
        dt = _parse_posted_date(j.posted)
        return dt if dt else _epoch

    return sorted(jobs, key=_key, reverse=True)


def _format_job(job) -> str:
    salary_part = f" | 💰 {html_lib.escape(job.salary)}" if job.salary else ""
    location_part = html_lib.escape(job.location or "Remote")
    title = html_lib.escape(job.title or "")
    company = html_lib.escape(job.company or "")
    source = html_lib.escape(job.source or "")
    url = html_lib.escape(job.url or "", quote=True)
    link = f'<a href="{url}">Apply</a>' if url else "Apply"
    return (
        f"🟢 <b>{title}</b> - {company}\n"
        f"📍 {location_part}{salary_part}\n"
        f"🔗 {link} · <i>{source}</i>"
    )


def _split_messages(lines: list[str]) -> list[str]:
    """Chunk formatted job strings into Telegram-safe messages."""
    messages = []
    current = ""
    max_body_len = MAX_MSG_LEN - _SPLIT_BUFFER
    for line in lines:
        block = line + "\n\n"
        if len(current) + len(block) > max_body_len:
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
            "parse_mode": "HTML",
            "disable_web_page_preview": True,
        },
        timeout=15,
    )
    resp.raise_for_status()
    data = resp.json()
    if not data.get("ok"):
        raise RuntimeError(f"Telegram send failed: {data.get('description', data)}")


def send_jobs(jobs: list) -> None:
    if not jobs:
        if not SILENT_IF_EMPTY:
            _send("No new Web3 marketing jobs today.")
        return

    jobs = _sort_by_recency(jobs)

    header = f"<b>🚀 {len(jobs)} New Web3 Marketing Job{'s' if len(jobs) != 1 else ''}</b>\n\n"
    formatted = [_format_job(j) for j in jobs]
    chunks = _split_messages(formatted)

    for i, chunk in enumerate(chunks):
        prefix = header if i == 0 else f"<i>(continued {i+1}/{len(chunks)})</i>\n\n"
        _send(prefix + chunk)

    print(f"[notifier] Sent {len(jobs)} job(s) across {len(chunks)} message(s).")
