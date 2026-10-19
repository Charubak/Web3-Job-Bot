"""One bounded owner-confirmed handshake. Never print or persist private payloads."""
import hashlib
import hmac
import json
import logging
import os
import re
from datetime import datetime, timezone

import httpx

MAX_BODY = 2 * 1024 * 1024


class PairingError(RuntimeError):
    pass


def api_call(token: str, method: str, payload: dict | None = None):
    if method not in {"getMe", "getWebhookInfo", "getUpdates", "sendMessage"}:
        raise PairingError("Unsupported pairing operation")
    try:
        with httpx.stream("POST", f"https://api.telegram.org/bot{token}/{method}",
                          json=payload or {}, timeout=15, follow_redirects=False) as response:
            body = bytearray()
            for chunk in response.iter_bytes():
                body.extend(chunk)
                if len(body) > MAX_BODY:
                    raise ValueError("response too large")
            if response.status_code != 200:
                raise ValueError("request rejected")
            data = json.loads(body)
            if not isinstance(data, dict) or data.get("ok") is not True:
                raise ValueError("request rejected")
            return data["result"]
    except Exception:
        # Do not include request URLs (token), descriptions, or response bodies.
        if method == "sendMessage":
            raise PairingError("Setup reply delivery is unconfirmed; inspect the original chat before any retry") from None
        raise PairingError("Telegram verification unavailable; no setup reply was attempted") from None


def verify_window(digest: str, issued: str, expires: str, now: datetime):
    try:
        start = datetime.fromisoformat(issued.replace("Z", "+00:00"))
        end = datetime.fromisoformat(expires.replace("Z", "+00:00"))
        if not re.fullmatch(r"[0-9a-f]{64}", digest):
            raise ValueError
        if start.tzinfo is None or end.tzinfo is None or not 0 < (end - start).total_seconds() <= 3600:
            raise ValueError
        if not start <= now <= end:
            raise ValueError
        return start.timestamp(), end.timestamp()
    except (ValueError, TypeError):
        raise PairingError("Pairing code is invalid, not yet active, or expired") from None


def select_chat(updates, digest: str, start: float, end: float, now: float):
    if not isinstance(updates, list) or len(updates) > 100:
        raise PairingError("Unexpected update response; no setup reply was attempted")
    matches = {}
    for update in updates:
        if not isinstance(update, dict):
            continue
        message = update.get("message")
        if not isinstance(message, dict):
            continue
        text = message.get("text")
        if not isinstance(text, str) or len(text) > 1024:
            continue
        if not hmac.compare_digest(hashlib.sha256(text.encode()).hexdigest(), digest):
            continue
        chat, sender = message.get("chat", {}), message.get("from", {})
        if not isinstance(chat, dict) or not isinstance(sender, dict):
            continue
        chat_id, message_id, timestamp = chat.get("id"), message.get("message_id"), message.get("date")
        if (chat.get("type") != "private" or type(chat_id) is not int or chat_id <= 0 or
                sender.get("id") != chat_id or sender.get("is_bot") is not False or
                type(message_id) is not int or message_id <= 0 or type(timestamp) is not int or
                not start <= timestamp <= min(end, now + 30) or
                any(k in message for k in ("forward_origin", "forward_from", "via_bot", "sender_chat"))):
            continue
        matches[chat_id] = message_id
    if len(matches) != 1:
        raise PairingError("No unique fresh owner handshake found; no setup reply was attempted")
    return next(iter(matches.items()))


def pair(token: str, expected_bot: str, digest: str, issued: str, expires: str, *, now=None, call=api_call, clock=None):
    clock = clock or (lambda: now or datetime.now(timezone.utc))
    checked_at = clock()
    start, end = verify_window(digest, issued, expires, checked_at)
    if not token or expected_bot != "charubak_web3jobs_bot":
        raise PairingError("Expected bot configuration is missing")
    bot = call(token, "getMe")
    if not isinstance(bot, dict) or bot.get("is_bot") is not True or bot.get("username") != expected_bot:
        raise PairingError("Configured token belongs to an unexpected bot; pairing refused")
    webhook = call(token, "getWebhookInfo")
    if not isinstance(webhook, dict) or webhook.get("url") != "":
        raise PairingError("Existing webhook or unexpected status prevents pairing; no webhook was changed")
    # No offset: do not acknowledge/discard updates. No allowed_updates: preserve settings.
    updates = call(token, "getUpdates", {"limit": 100, "timeout": 0})
    chat_id, message_id = select_chat(updates, digest, start, end, checked_at.timestamp())
    text = (
        "Your original Web3 job chat is verified. One final setup step:\n\n"
        "In GitHub → Charubak/Web3-Job-Bot → Settings → Secrets and variables → Actions, "
        "add a repository secret named TELEGRAM_CHAT_ID with this value:\n\n"
        f"{chat_id}\n\n"
        "Then tell dot it is saved. Job alerts are not enabled yet. "
        "Never post your bot API token in a message."
    )
    # Reads may have taken time; never start a send after the approved window.
    verify_window(digest, issued, expires, clock())
    result = call(token, "sendMessage", {"chat_id": chat_id, "text": text,
                  "reply_parameters": {"message_id": message_id}, "disable_web_page_preview": True})
    if (not isinstance(result, dict) or not isinstance(result.get("chat"), dict) or
            result["chat"].get("id") != chat_id or result["chat"].get("type") != "private" or
            type(result.get("message_id")) is not int):
        raise PairingError("Setup reply acknowledgment is unconfirmed; inspect the original chat before any retry")
    return True


def main():
    logging.disable(logging.CRITICAL)
    try:
        if os.getenv("GITHUB_RUN_ATTEMPT") != "1" or os.getenv("GITHUB_RUN_NUMBER") != "1":
            raise PairingError("Automatic/repeated pairing attempts are disabled")
        pair(os.getenv("TELEGRAM_BOT_TOKEN", ""), os.getenv("EXPECTED_BOT", ""),
             os.getenv("PAIRING_SHA256", ""), os.getenv("PAIRING_ISSUED", ""), os.getenv("PAIRING_EXPIRES", ""))
    except PairingError as exc:
        print(str(exc))
        return 1
    except Exception:
        print("Pairing stopped safely; inspect the original chat before any retry")
        return 1
    print("Expected bot and private owner chat verified; setup reply acknowledged. No private identifiers were logged or saved.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
