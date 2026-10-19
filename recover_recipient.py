"""One no-send lookup of a previously authorized owner message; encrypted result only."""
import base64
import hashlib
import json
import logging
import os
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from cryptography.fernet import Fernet
import storage
from pair_telegram import PairingError, NoHandshake, api_call, select_chat, verify_window

EXPECTED_BOT = 'charubak_web3jobs_bot'


def cipher(token):
    return Fernet(base64.urlsafe_b64encode(hashlib.sha256(
        b'web3-job-bot-recipient-storage-v1\x00' + token.encode()).digest()))


def save_recipient(token, chat_id, db_path):
    path = Path(db_path)
    if not path.is_file():
        raise PairingError('Existing delivery state is required; no recipient saved')
    sealed = cipher(token).encrypt(json.dumps({'version': 1, 'bot': EXPECTED_BOT,
                                              'chat_id': str(chat_id)}).encode()).decode()
    with sqlite3.connect(path.resolve().as_uri() + '?mode=rw', uri=True) as conn:
        if not conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='seen_jobs'").fetchone():
            raise PairingError('Existing delivery state is invalid; no recipient saved')
        conn.execute('CREATE TABLE IF NOT EXISTS recovered_recipient (bot TEXT PRIMARY KEY, ciphertext TEXT NOT NULL)')
        existing = conn.execute('SELECT ciphertext FROM recovered_recipient WHERE bot=?', (EXPECTED_BOT,)).fetchone()
        if existing:
            old = json.loads(cipher(token).decrypt(existing[0].encode()))
            if old != {'version': 1, 'bot': EXPECTED_BOT, 'chat_id': str(chat_id)}:
                raise PairingError('Existing recipient differs; recovery refused')
            return
        conn.execute('INSERT INTO recovered_recipient (bot, ciphertext) VALUES (?, ?)', (EXPECTED_BOT, sealed))


def load_recipient(token, db_path=None):
    path = Path(db_path or storage.DB_PATH)
    with sqlite3.connect(path.resolve().as_uri() + '?mode=ro', uri=True) as conn:
        row = conn.execute('SELECT ciphertext FROM recovered_recipient WHERE bot=?', (EXPECTED_BOT,)).fetchone()
    if not row:
        raise PairingError('Verified recovered recipient unavailable')
    data = json.loads(cipher(token).decrypt(row[0].encode()))
    chat_id = data.get('chat_id') if isinstance(data, dict) else None
    if (data.get('version') != 1 or data.get('bot') != EXPECTED_BOT or
            not isinstance(chat_id, str) or not chat_id.isascii() or not chat_id.isdecimal() or
            not 0 < int(chat_id) < 2**63):
        raise PairingError('Verified recovered recipient invalid')
    return chat_id


def recover(token, expected_bot, digest, issued, expires, *, call=api_call, now=None, db_path=None):
    if not token or expected_bot != EXPECTED_BOT:
        raise PairingError('Expected bot configuration is missing')
    # This is a historical source lookup, never a new pairing/send authorization.
    original_start = datetime.fromisoformat(issued.replace('Z', '+00:00'))
    start, end = verify_window(digest, issued, expires, original_start)
    now = now or datetime.now(timezone.utc)
    bot = call(token, 'getMe')
    if not isinstance(bot, dict) or bot.get('username') != EXPECTED_BOT or bot.get('is_bot') is not True:
        raise PairingError('Unexpected bot; recipient recovery refused')
    webhook = call(token, 'getWebhookInfo')
    if not isinstance(webhook, dict) or webhook.get('url') != '':
        raise PairingError('Webhook present or invalid status; no settings changed')
    updates = call(token, 'getUpdates', {'limit': 100, 'timeout': 0})
    try:
        chat_id, _ = select_chat(updates, digest, start, end, now.timestamp())
    except NoHandshake:
        return False
    save_recipient(token, chat_id, db_path or storage.DB_PATH)
    return True


def main():
    logging.disable(logging.CRITICAL)
    try:
        if os.getenv('GITHUB_RUN_ATTEMPT') != '1' or os.getenv('GITHUB_RUN_NUMBER') != '1':
            raise PairingError('Repeated recipient recovery is disabled')
        found = recover(os.getenv('TELEGRAM_BOT_TOKEN', ''), os.getenv('EXPECTED_BOT', ''),
                        os.getenv('PAIRING_SHA256', ''), os.getenv('PAIRING_ISSUED', ''),
                        os.getenv('PAIRING_EXPIRES', ''))
        print('recovered=' + str(found).lower())
        return 0
    except Exception:
        # Even parser/crypto/SQLite exceptions may embed private data: never print them.
        print('Recipient recovery stopped safely; no messages were sent', file=__import__('sys').stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
