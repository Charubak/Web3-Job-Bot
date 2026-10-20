"""One fail-closed, separately fenced corrected-age Telegram job review message.

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
from boards import _make_id
from pair_telegram import api_call

EXPECTED_BOT = activation.EXPECTED_BOT
PURPOSE = "latest-review-2026-10-19"
REVIEW_TEXT = 'Apply for these:\n\n1. Defuse Labs — Head of Product Marketing & Growth\nOwn near.com positioning, GTM, acquisition and retention. Strong DeFi fit.\nhttps://job-boards.eu.greenhouse.io/defuselabs/jobs/4997160101\n\n2. GetBlock — Full-Stack Marketing Manager / Marketing PMM\nB2B positioning, campaigns and paid experiments. A hands-on IC role.\nhttps://getblock.factorial.com/job_posting/full-stack-marketing-manager-marketing-pmm-327722\n\n3. PancakeSwap — Head of Marketing\nDEX growth leadership across AMM, aggregator and RWA products.\nhttps://talent.yzilabs.com/jobs/c2ce0aad-fee1-49a3-9abc-b6ad32480795\n\n4. Intercepta — Marketing Lead\nOwn positioning and full-funnel marketing for Web3 security.\nhttps://intercepta.io/careers/marketing-marketing-lead-remote\n\n5. TYB — Lifecycle Marketing Lead\nOwn consumer activation, referrals and retention.\nhttps://jobs.sogalventures.com/companies/tyb/jobs/95076705-lifecycle-marketing-lead\n\n6. Trading Marketing Lead — via Jaya Talent\nPaid acquisition and trader-retention growth for a company serving PerpDEXs.\nhttps://www.linkedin.com/posts/blockchain-startup--recruitment_hiring-trading-marketing-lead-performance-activity-7508055235232210944-MdfN\n\n7. The Group Three — Performance Marketing Specialist (full-time), or Senior Content Writer (Web3, contract)\nRemote Web3 agency options for performance marketing or content.\nhttps://www.thegroupthree.com/careers/'
RECOMMENDATION_URLS = (
    "https://getblock.factorial.com/job_posting/full-stack-marketing-manager-marketing-pmm-327722",
    'https://www.linkedin.com/posts/blockchain-startup--recruitment_hiring-trading-marketing-lead-performance-activity-7508055235232210944-MdfN',
    "https://job-boards.eu.greenhouse.io/defuselabs/jobs/4997160101",
    "https://talent.yzilabs.com/jobs/c2ce0aad-fee1-49a3-9abc-b6ad32480795",
    "https://intercepta.io/careers/marketing-marketing-lead-remote",
    "https://jobs.sogalventures.com/companies/tyb/jobs/95076705-lifecycle-marketing-lead",
)
RECOMMENDATION_CARDS = (
    ("https://www.thegroupthree.com/careers/", "Performance Marketing Specialist"),
    ("https://www.thegroupthree.com/careers/", "Senior Content Writer (Web3)"),
)
_TABLE = "telegram_latest_job_review"
_UNCERTAIN = "Latest job review is pending or unconfirmed; inspect the original chat before any retry"
_INACTIVE = "Current confirmed activation for the verified private chat is required"
LatestJobReviewError = activation.ActivationError


def _digest():
    return hashlib.sha256(REVIEW_TEXT.encode("utf-8")).hexdigest()


def _require_activation(conn, tag):
    if not activation._is_confirmed(activation._read_state(conn), tag):
        raise LatestJobReviewError(_INACTIVE)


def _read_state(conn, *, missing_ok=False):
    exists = conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (_TABLE,)).fetchone()
    rows = conn.execute("SELECT purpose, destination_tag, text_digest, status FROM telegram_latest_job_review").fetchall() if exists else []
    if not rows:
        if missing_ok:
            return None
        raise LatestJobReviewError("Latest job review has not been prepared")
    if len(rows) != 1:
        raise LatestJobReviewError("Latest job review state is invalid")
    purpose, tag, digest, status = rows[0]
    if (purpose != PURPOSE or not isinstance(tag, str) or not re.fullmatch(r"[0-9a-f]{64}", tag) or
            not isinstance(digest, str) or not re.fullmatch(r"[0-9a-f]{64}", digest) or
            status not in {"prepared", "sending", "confirmed"}):
        raise LatestJobReviewError("Latest job review state is invalid")
    return tag, digest, status


def _matches(state, tag, digest, status):
    return (state is not None and state[2] == status and
            hmac.compare_digest(state[0], tag) and hmac.compare_digest(state[1], digest))


def _verify_activation(token, chat_id, expected_bot, call):
    # check is read-only and rechecks activation after verifying exact bot/chat.
    if not activation.check(token, chat_id, expected_bot, call=call):
        raise LatestJobReviewError(_INACTIVE)


def prepare(token, chat_id, expected_bot=EXPECTED_BOT, *, call=None):
    """Create one durable preparation; any previous uncertainty blocks retries."""
    chat_id, tag = activation._configuration(token, chat_id, expected_bot)
    digest = _digest()
    with activation._connect() as conn:
        _require_activation(conn, tag)
        state = _read_state(conn, missing_ok=True)
    if state is not None and not _matches(state, tag, digest, "confirmed"):
        raise LatestJobReviewError(_UNCERTAIN)
    _verify_activation(token, chat_id, expected_bot, call or api_call)
    with activation._connect(writable=True) as conn:
        conn.execute("BEGIN IMMEDIATE")
        _require_activation(conn, tag)
        state = _read_state(conn, missing_ok=True)
        if _matches(state, tag, digest, "confirmed"):
            return False
        if state is not None:
            raise LatestJobReviewError(_UNCERTAIN)
        conn.execute("""CREATE TABLE IF NOT EXISTS telegram_latest_job_review (
            purpose TEXT PRIMARY KEY NOT NULL CHECK (purpose = 'latest-review-2026-10-19'),
            destination_tag TEXT NOT NULL CHECK (length(destination_tag) = 64),
            text_digest TEXT NOT NULL CHECK (length(text_digest) = 64),
            status TEXT NOT NULL CHECK (status IN ('prepared', 'sending', 'confirmed'))
        )""")
        conn.execute("INSERT INTO telegram_latest_job_review VALUES (?, ?, ?, 'prepared')", (PURPOSE, tag, digest))
        # Fence these public listing identities in the same durable transaction.
        # Prepared/ambiguous delivery must not reappear in the normal daily loop.
        conn.execute("CREATE TABLE IF NOT EXISTS telegram_latest_review_jobs (id TEXT PRIMARY KEY, url TEXT NOT NULL, title TEXT NOT NULL DEFAULT '')")
        conn.executemany("INSERT OR IGNORE INTO telegram_latest_review_jobs (id, url) VALUES (?, ?)",
                         [(_make_id(url), url) for url in RECOMMENDATION_URLS])
        # Shared careers pages are scoped to the exact role title. Never mark
        # an entire careers page seen because one card was recommended.
        conn.executemany("INSERT OR IGNORE INTO telegram_latest_review_jobs (id, url, title) VALUES (?, ?, ?)",
                         [(hashlib.sha256((url + "\0" + title.casefold()).encode()).hexdigest()[:16], url, title)
                          for url, title in RECOMMENDATION_CARDS])
        conn.commit()
    return True


def _transition(tag, digest, before, after):
    with activation._connect(writable=True) as conn:
        conn.execute("BEGIN IMMEDIATE")
        _require_activation(conn, tag)
        if not _matches(_read_state(conn), tag, digest, before):
            raise LatestJobReviewError(_UNCERTAIN)
        conn.execute("UPDATE telegram_latest_job_review SET status=? WHERE purpose=?", (after, PURPOSE))
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
        raise LatestJobReviewError(_UNCERTAIN)
    call = call or api_call
    _verify_activation(token, chat_id, expected_bot, call)
    if confirmed:
        with activation._connect() as conn:
            _require_activation(conn, tag)
            if not _matches(_read_state(conn), tag, digest, "confirmed"):
                raise LatestJobReviewError(_UNCERTAIN)
        return False
    _transition(tag, digest, "prepared", "sending")
    try:
        result = call(token, "sendMessage", {
            "chat_id": chat_id, "text": REVIEW_TEXT, "disable_web_page_preview": True,
        })
    except Exception:
        raise LatestJobReviewError(_UNCERTAIN) from None
    if (not isinstance(result, dict) or not isinstance(result.get("chat"), dict) or
            type(result["chat"].get("id")) is not int or result["chat"]["id"] != chat_id or
            result["chat"].get("type") != "private" or
            type(result.get("message_id")) is not int or result["message_id"] <= 0):
        raise LatestJobReviewError(_UNCERTAIN)
    _mark_confirmed(tag, digest)
    return True


def main(argv=None):
    logging.disable(logging.CRITICAL)
    argv = sys.argv[1:] if argv is None else argv
    if argv not in (["prepare"], ["deliver"]):
        print("Latest job review command must be prepare or deliver", file=sys.stderr)
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
        print("Latest job review stopped safely; inspect the original chat before any retry", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
