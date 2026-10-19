"""One fail-closed, separately fenced final Telegram readiness message.

The caller must persist the prepared database before invoking deliver, and save
it again afterward. A restored prepared snapshot cannot authorize preparation
again. Never remove or reset an uncertain receipt to retry automatically.
"""
import hashlib
import hmac
import logging
import os
import re
import sys

import activation
from pair_telegram import api_call

EXPECTED_BOT = activation.EXPECTED_BOT
PURPOSE = "ready-2026-10-19"
READY_TEXT = (
    "Your updated Web3 job bot is ready. It will check once daily at 9:00 AM IST. "
    "Eligible listings are 14–30 days old, with your role, worldwide-remote and "
    "employer-location filters applied. Today: 1,154 listings checked; 0 verified "
    "matches. Expired listings with the same job ID will stay excluded. "
    "The older Fly deployment has not been verified as stopped."
)
_TABLE = "telegram_ready_status"
_UNCERTAIN = "Ready status is pending or unconfirmed; inspect the original chat before any retry"
_INACTIVE = "Current confirmed activation for the verified private chat is required"
ReadyStatusError = activation.ActivationError


def _digest():
    return hashlib.sha256(READY_TEXT.encode("utf-8")).hexdigest()


def _require_activation(conn, tag):
    if not activation._is_confirmed(activation._read_state(conn), tag):
        raise ReadyStatusError(_INACTIVE)


def _read_state(conn, *, missing_ok=False):
    exists = conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (_TABLE,)).fetchone()
    rows = conn.execute("SELECT purpose, destination_tag, text_digest, status FROM telegram_ready_status").fetchall() if exists else []
    if not rows:
        if missing_ok:
            return None
        raise ReadyStatusError("Ready status has not been prepared")
    if len(rows) != 1:
        raise ReadyStatusError("Ready status state is invalid")
    purpose, tag, digest, status = rows[0]
    if (purpose != PURPOSE or not isinstance(tag, str) or not re.fullmatch(r"[0-9a-f]{64}", tag) or
            not isinstance(digest, str) or not re.fullmatch(r"[0-9a-f]{64}", digest) or
            status not in {"prepared", "sending", "confirmed"}):
        raise ReadyStatusError("Ready status state is invalid")
    return tag, digest, status


def _matches(state, tag, digest, status):
    return (state is not None and state[2] == status and
            hmac.compare_digest(state[0], tag) and hmac.compare_digest(state[1], digest))


def _verify_activation(token, chat_id, expected_bot, call):
    # check is read-only and rechecks activation after verifying exact bot/chat.
    if not activation.check(token, chat_id, expected_bot, call=call):
        raise ReadyStatusError(_INACTIVE)


def prepare(token, chat_id, expected_bot=EXPECTED_BOT, *, call=None):
    """Create one durable preparation; any previous uncertainty blocks retries."""
    chat_id, tag = activation._configuration(token, chat_id, expected_bot)
    digest = _digest()
    with activation._connect() as conn:
        _require_activation(conn, tag)
        state = _read_state(conn, missing_ok=True)
    if state is not None and not _matches(state, tag, digest, "confirmed"):
        raise ReadyStatusError(_UNCERTAIN)
    _verify_activation(token, chat_id, expected_bot, call or api_call)
    with activation._connect(writable=True) as conn:
        conn.execute("BEGIN IMMEDIATE")
        _require_activation(conn, tag)
        state = _read_state(conn, missing_ok=True)
        if _matches(state, tag, digest, "confirmed"):
            return False
        if state is not None:
            raise ReadyStatusError(_UNCERTAIN)
        conn.execute("""CREATE TABLE IF NOT EXISTS telegram_ready_status (
            purpose TEXT PRIMARY KEY NOT NULL CHECK (purpose = 'ready-2026-10-19'),
            destination_tag TEXT NOT NULL CHECK (length(destination_tag) = 64),
            text_digest TEXT NOT NULL CHECK (length(text_digest) = 64),
            status TEXT NOT NULL CHECK (status IN ('prepared', 'sending', 'confirmed'))
        )""")
        conn.execute("INSERT INTO telegram_ready_status VALUES (?, ?, ?, 'prepared')", (PURPOSE, tag, digest))
        conn.commit()
    return True


def _transition(tag, digest, before, after):
    with activation._connect(writable=True) as conn:
        conn.execute("BEGIN IMMEDIATE")
        _require_activation(conn, tag)
        if not _matches(_read_state(conn), tag, digest, before):
            raise ReadyStatusError(_UNCERTAIN)
        conn.execute("UPDATE telegram_ready_status SET status=? WHERE purpose=?", (after, PURPOSE))
        conn.commit()


def _mark_confirmed(tag, digest):
    _transition(tag, digest, "sending", "confirmed")


def deliver(token, chat_id, expected_bot=EXPECTED_BOT, *, call=None):
    """Attempt one send after committing its fence; never retry an uncertain send."""
    chat_id, tag = activation._configuration(token, chat_id, expected_bot)
    digest = _digest()
    with activation._connect() as conn:
        _require_activation(conn, tag)
        state = _read_state(conn)
    confirmed = _matches(state, tag, digest, "confirmed")
    if not confirmed and not _matches(state, tag, digest, "prepared"):
        raise ReadyStatusError(_UNCERTAIN)
    call = call or api_call
    _verify_activation(token, chat_id, expected_bot, call)
    if confirmed:
        with activation._connect() as conn:
            _require_activation(conn, tag)
            if not _matches(_read_state(conn), tag, digest, "confirmed"):
                raise ReadyStatusError(_UNCERTAIN)
        return False
    _transition(tag, digest, "prepared", "sending")
    try:
        result = call(token, "sendMessage", {
            "chat_id": chat_id, "text": READY_TEXT, "disable_web_page_preview": True,
        })
    except Exception:
        raise ReadyStatusError(_UNCERTAIN) from None
    if (not isinstance(result, dict) or not isinstance(result.get("chat"), dict) or
            type(result["chat"].get("id")) is not int or result["chat"]["id"] != chat_id or
            result["chat"].get("type") != "private" or
            type(result.get("message_id")) is not int or result["message_id"] <= 0):
        raise ReadyStatusError(_UNCERTAIN)
    _mark_confirmed(tag, digest)
    return True


def main(argv=None):
    logging.disable(logging.CRITICAL)
    argv = sys.argv[1:] if argv is None else argv
    if argv not in (["prepare"], ["deliver"]):
        print("Ready status command must be prepare or deliver", file=sys.stderr)
        return 1
    try:
        from recipient_envelope import resolve_chat_id
        token = os.getenv("TELEGRAM_BOT_TOKEN", "")
        args = (token, resolve_chat_id(token, os.getenv("TELEGRAM_CHAT_ID", "")),
                os.getenv("EXPECTED_BOT", ""))
        if argv == ["prepare"]:
            print(f"prepared={'true' if prepare(*args) else 'false'}")
        else:
            deliver(*args)
            print("acknowledged=true")
        return 0
    except Exception:
        # Never echo API, recipient-envelope, database, or unexpected error data.
        print("Ready status stopped safely; inspect the original chat before any retry", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
