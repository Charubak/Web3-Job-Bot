"""Fail-closed activation for the original private Telegram job-alert chat.

Prepare and persist the database/cache before delivering. A pending activation
must never be automatically prepared again, even after changing credentials.
Only a positively acknowledged setup message permits scheduled delivery.
"""
import hashlib
import hmac
import logging
import os
import re
import sqlite3
import sys
from contextlib import contextmanager
from pathlib import Path

import storage
from pair_telegram import api_call

EXPECTED_BOT = "charubak_web3jobs_bot"
CONFIRMATION_TEXT = (
    "Your updated Web3 job bot is connected to this private chat. "
    "Final activation is being verified. It will check four times daily and only send "
    "strong, fully remote Growth/GTM/PMM matches that allow India, "
    "from non-India-based employers."
)
_TABLE = "telegram_activation"
_UNCERTAIN = "Activation is pending or unconfirmed; inspect the original chat before any retry"


class ActivationError(RuntimeError):
    """A fixed, safe-to-log error with no private Telegram response data."""


def _configuration(token, chat_id, expected_bot):
    if not isinstance(token, str) or not token.strip() or expected_bot != EXPECTED_BOT:
        raise ActivationError("Expected bot configuration is missing or invalid")
    if type(chat_id) is int:
        chat_id = str(chat_id)
    if not isinstance(chat_id, str) or not re.fullmatch(r"[0-9]{1,32}", chat_id.strip()):
        raise ActivationError("A positive numeric private-chat destination is required")
    chat_id = int(chat_id.strip())
    if chat_id <= 0:
        raise ActivationError("A positive numeric private-chat destination is required")
    tag = hmac.new(token.encode(), str(chat_id).encode(), hashlib.sha256).hexdigest()
    return chat_id, tag


def _verify_destination(token, chat_id, expected_bot, call):
    try:
        bot = call(token, "getMe")
        if (not isinstance(bot, dict) or bot.get("is_bot") is not True or
                bot.get("username") != expected_bot):
            raise ActivationError("Configured token belongs to an unexpected bot")
        chat = call(token, "getChat", {"chat_id": chat_id})
        if (not isinstance(chat, dict) or type(chat.get("id")) is not int or
                chat["id"] != chat_id or chat.get("type") != "private"):
            raise ActivationError("Configured destination is not the verified private chat")
    except ActivationError:
        raise
    except Exception:
        raise ActivationError("Telegram destination verification is unavailable") from None


@contextmanager
def _connect(*, writable=False):
    """Never create the shared database or initialize delivery history here."""
    conn = None
    try:
        path = Path(storage.DB_PATH)
        if not path.is_file():
            raise ActivationError("Existing initialized delivery state is required")
        conn = sqlite3.connect(path.resolve().as_uri() + ("?mode=rw" if writable else "?mode=ro"), uri=True)
        if not conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='seen_jobs'").fetchone():
            raise ActivationError("Existing initialized delivery state is required")
        conn.execute("SELECT id, title, url, seen_at FROM seen_jobs LIMIT 0")
        if writable:
            conn.execute("PRAGMA synchronous=FULL")
        yield conn
    except ActivationError:
        raise
    except Exception:
        raise ActivationError("Activation state could not be verified or saved safely") from None
    finally:
        if conn is not None:
            conn.close()


def _read_state(conn, *, missing_ok=False):
    exists = conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (_TABLE,)).fetchone()
    if not exists:
        if missing_ok:
            return None
        raise ActivationError("Activation has not been prepared")
    rows = conn.execute("SELECT singleton, destination_tag, status, send_started FROM telegram_activation").fetchall()
    if not rows:
        if missing_ok:
            return None
        raise ActivationError("Activation has not been prepared")
    if len(rows) != 1:
        raise ActivationError("Activation state is invalid")
    singleton, tag, status, started = rows[0]
    if (singleton != 1 or not isinstance(tag, str) or not re.fullmatch(r"[0-9a-f]{64}", tag) or
            status not in {"pending", "confirmed"} or type(started) is not int or started not in {0, 1} or
            (status == "confirmed" and started != 1)):
        raise ActivationError("Activation state is invalid")
    return tag, status, started


def _is_confirmed(state, tag):
    return state is not None and state[1] == "confirmed" and hmac.compare_digest(state[0], tag)


def _require_pending(state, tag, *, started):
    if (state is None or state[1] != "pending" or state[2] != started or
            not hmac.compare_digest(state[0], tag)):
        raise ActivationError(_UNCERTAIN)


def prepare(token, chat_id, expected_bot=EXPECTED_BOT, *, call=None):
    """Persist a fresh fence; return False for an already-confirmed destination."""
    chat_id, tag = _configuration(token, chat_id, expected_bot)
    with _connect() as conn:
        state = _read_state(conn, missing_ok=True)
    if state is not None and state[1] == "pending":
        raise ActivationError(_UNCERTAIN)
    _verify_destination(token, chat_id, expected_bot, call or api_call)
    if _is_confirmed(state, tag):
        return False
    with _connect(writable=True) as conn:
        conn.execute("BEGIN IMMEDIATE")
        # Recheck under the write lock: concurrent/repeated preparation cannot
        # replace an uncertain attempt, including one for another destination.
        state = _read_state(conn, missing_ok=True)
        if state is not None and state[1] == "pending":
            raise ActivationError(_UNCERTAIN)
        if _is_confirmed(state, tag):
            return False
        conn.execute("""CREATE TABLE IF NOT EXISTS telegram_activation (
            singleton INTEGER PRIMARY KEY CHECK (singleton = 1),
            destination_tag TEXT NOT NULL CHECK (length(destination_tag) = 64),
            status TEXT NOT NULL CHECK (status IN ('pending', 'confirmed')),
            send_started INTEGER NOT NULL CHECK (send_started IN (0, 1)),
            CHECK (status != 'confirmed' OR send_started = 1)
        )""")
        conn.execute("""INSERT INTO telegram_activation VALUES (1, ?, 'pending', 0)
            ON CONFLICT(singleton) DO UPDATE SET destination_tag=excluded.destination_tag,
                status='pending', send_started=0""", (tag,))
        conn.commit()
    return True


def _claim_pending(tag):
    with _connect(writable=True) as conn:
        conn.execute("BEGIN IMMEDIATE")
        _require_pending(_read_state(conn), tag, started=0)
        conn.execute("UPDATE telegram_activation SET send_started=1 WHERE singleton=1")
        conn.commit()


def _mark_confirmed(tag):
    with _connect(writable=True) as conn:
        conn.execute("BEGIN IMMEDIATE")
        _require_pending(_read_state(conn), tag, started=1)
        conn.execute("UPDATE telegram_activation SET status='confirmed' WHERE singleton=1")
        conn.commit()


def deliver(token, chat_id, expected_bot=EXPECTED_BOT, *, call=None):
    """Make at most one setup-send attempt; return False for an active no-op.

    An attempted marker is committed before sendMessage. Any send error, bad
    acknowledgment, or confirmation-write failure leaves the pending fence.
    Repeated calls cannot send again without an explicit external reconciliation.
    """
    chat_id, tag = _configuration(token, chat_id, expected_bot)
    with _connect() as conn:
        state = _read_state(conn)
    if not _is_confirmed(state, tag):
        _require_pending(state, tag, started=0)
    call = call or api_call
    _verify_destination(token, chat_id, expected_bot, call)
    if _is_confirmed(state, tag):
        with _connect() as conn:
            if not _is_confirmed(_read_state(conn), tag):
                raise ActivationError(_UNCERTAIN)
        return False
    _claim_pending(tag)
    try:
        result = call(token, "sendMessage", {"chat_id": chat_id, "text": CONFIRMATION_TEXT,
                                           "disable_web_page_preview": True})
    except Exception:
        raise ActivationError(_UNCERTAIN) from None
    if (not isinstance(result, dict) or not isinstance(result.get("chat"), dict) or
            type(result["chat"].get("id")) is not int or result["chat"]["id"] != chat_id or
            result["chat"].get("type") != "private" or
            type(result.get("message_id")) is not int or result["message_id"] <= 0):
        raise ActivationError(_UNCERTAIN)
    _mark_confirmed(tag)
    return True


def check(token, chat_id, expected_bot=EXPECTED_BOT, *, call=None):
    """Read-only scheduled-send gate. Every uncertainty returns False."""
    try:
        chat_id, tag = _configuration(token, chat_id, expected_bot)
        with _connect() as conn:
            if not _is_confirmed(_read_state(conn), tag):
                return False
        _verify_destination(token, chat_id, expected_bot, call or api_call)
        # Verification may take time. Do not bless a destination whose state
        # changed while the network reads were in flight.
        with _connect() as conn:
            return _is_confirmed(_read_state(conn), tag)
    except Exception:
        return False


def main(argv=None):
    logging.disable(logging.CRITICAL)
    argv = sys.argv[1:] if argv is None else argv
    if argv not in (["prepare"], ["deliver"], ["check"]):
        print("Activation command must be prepare, deliver, or check", file=sys.stderr)
        return 1
    try:
        from recipient_envelope import resolve_chat_id
        token = os.getenv("TELEGRAM_BOT_TOKEN", "")
        args = (token, resolve_chat_id(token, os.getenv("TELEGRAM_CHAT_ID", "")),
                os.getenv("EXPECTED_BOT", ""))
        if argv == ["check"]:
            active = check(*args)
            print(f"active={'true' if active else 'false'}")
            return 0 if active else 1
        if argv == ["prepare"]:
            print(f"prepared={'true' if prepare(*args) else 'false'}")
        else:
            deliver(*args)
            print("active=true")
        return 0
    except ActivationError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    except Exception:
        print("Activation stopped safely; inspect the original chat before any retry", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
