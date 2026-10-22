import time
import httpx
import html as html_lib
from datetime import datetime, timezone
from config import TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID, SILENT_IF_EMPTY, validate_telegram_config
from filters import _parse_posted_date, classify_job
from urllib.parse import urlsplit

TELEGRAM_API = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}"
MAX_MSG_LEN = 4096
_SPLIT_BUFFER = 200


class DeliveryRejected(RuntimeError):
    """Telegram explicitly rejected a request; no delivery took place."""


class DeliveryUnconfirmed(RuntimeError):
    """Telegram may have accepted the request; manual reconciliation is needed."""


def _sort_by_recency(jobs: list) -> list:
    """Sort newest-first; jobs with unparseable dates go to the end."""
    _epoch = datetime.min.replace(tzinfo=timezone.utc)

    def _key(j):
        dt = _parse_posted_date(j.posted)
        return dt if dt else _epoch

    return sorted(jobs, key=_key, reverse=True)


def _age_label(posted: str) -> str:
    """Return a human-readable age string e.g. '2d ago', 'today'."""
    dt = _parse_posted_date(posted)
    if not dt:
        return ""
    delta = datetime.now(timezone.utc) - dt
    days = delta.days
    if days == 0:
        return " · today"
    if days == 1:
        return " · 1d ago"
    if days <= 30:
        return f" · {days}d ago"
    return ""


def _format_job(job) -> str:
    """Concise recommendations; detailed evidence stays in the audit report."""
    def esc(value, limit):
        return html_lib.escape(str(value or "")[:limit])
    title, company = esc(job.title, 220), esc(job.company, 100)
    raw_url = str(job.url or "")
    safe_url = raw_url if len(raw_url) <= 1500 and urlsplit(raw_url).scheme in ("http", "https") else ""
    link = f'<a href="{html_lib.escape(safe_url, quote=True)}">Apply</a>' if safe_url else "Listing URL unavailable"
    role = str(job.title or "").casefold()
    fit = ("Product marketing and GTM ownership." if "product marketing" in role or "pmm" in role else
           "Web3 content and product storytelling." if "content" in role or "writer" in role else
           "Growth and GTM ownership.")
    # Salary fields lack reliable employer-versus-estimate provenance. Omit them.
    age = _age_label(getattr(job, "posted", "")) or " · original posting date unknown"
    return f"<b>{title}</b> — {company}{age}\n{fit}\n{link}"


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


def _send(text: str, _retries: int = 3) -> None:
    validate_telegram_config()
    for attempt in range(_retries):
        try:
            resp = httpx.post(
                f"{TELEGRAM_API}/sendMessage",
                json={"chat_id": TELEGRAM_CHAT_ID, "text": text,
                      "parse_mode": "HTML", "disable_web_page_preview": True},
                timeout=15,
            )
            if resp.status_code == 429 and attempt < _retries - 1:
                retry_after = int(resp.json().get("parameters", {}).get("retry_after", 5))
                # A 429 is a confirmed rejection; ambiguous network errors are not retried.
                if retry_after > 30:
                    raise DeliveryRejected("Telegram requested a longer retry delay; try a later run")
                time.sleep(max(1, retry_after))
                continue
            if resp.status_code >= 500:
                raise DeliveryUnconfirmed("Telegram server error; delivery needs reconciliation")
            if resp.is_error or not resp.json().get("ok"):
                raise DeliveryRejected(f"Telegram rejected the message (HTTP {resp.status_code})")
            return
        except (httpx.HTTPError, ValueError) as exc:
            # Never leak the bot token embedded in request URLs.
            raise DeliveryUnconfirmed("Telegram delivery unconfirmed; not retried automatically") from None
    raise DeliveryRejected("Telegram rate limit retries exhausted")


def send_jobs(jobs: list, on_sent=None, on_unconfirmed=None, on_attempt=None, on_rejected=None) -> None:
    """Persist each acknowledged chunk before sending the next one."""
    if not jobs:
        if not SILENT_IF_EMPTY:
            _send("No new qualified Web3 Growth / GTM / Product Marketing jobs.")
        return
    jobs = _sort_by_recency(jobs)
    header = f"<b>{len(jobs)} new role(s) to apply for</b>\n\n"
    chunks = []
    current_jobs, current_lines = [], []
    for job in jobs:
        line = _format_job(job)
        if len(line) > MAX_MSG_LEN - 300:
            raise ValueError("Formatted job exceeds Telegram message limit")
        if current_lines and len("\n\n".join(current_lines + [line])) > MAX_MSG_LEN - 300:
            chunks.append((current_jobs, current_lines))
            current_jobs, current_lines = [], []
        current_jobs.append(job)
        current_lines.append(line)
    if current_lines:
        chunks.append((current_jobs, current_lines))
    for index, (batch, lines) in enumerate(chunks):
        prefix = header if index == 0 else f"<i>continued {index + 1}/{len(chunks)}</i>\n\n"
        if on_attempt:
            on_attempt(batch)
        try:
            _send(prefix + "\n\n".join(lines))
        except DeliveryRejected:
            if on_rejected:
                on_rejected(batch)
            raise
        except DeliveryUnconfirmed:
            if on_unconfirmed:
                on_unconfirmed(batch)
            raise
        if on_sent:
            on_sent(batch)
        if index < len(chunks) - 1:
            time.sleep(1)
    print(f"[notifier] Sent {len(jobs)} job(s) across {len(chunks)} message(s).")
