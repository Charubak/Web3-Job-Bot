import hashlib
import io
import sqlite3
import tempfile
import unittest
from contextlib import redirect_stdout, redirect_stderr
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch
from cryptography.fernet import InvalidToken
import recover_recipient as recovery
from pair_telegram import PairingError

TEXT = 'offline owner message'
DIGEST = hashlib.sha256(TEXT.encode()).hexdigest()
START, END = '2026-10-19T15:05:00Z', '2026-10-19T16:05:00Z'
NOW = datetime(2026, 10, 19, 16, 10, tzinfo=timezone.utc)
TOKEN = 'offline-fake-token-not-a-credential'
CHAT = 98234765012


def message(chat=CHAT):
    return {'message': {'message_id': 123, 'date': 1792422600, 'text': TEXT,
            'chat': {'id': chat, 'type': 'private'}, 'from': {'id': chat, 'is_bot': False}}}


class RecoveryTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / 'seen_jobs.db'
        with sqlite3.connect(self.path) as conn:
            conn.execute('CREATE TABLE seen_jobs (id TEXT PRIMARY KEY)')
            conn.execute("INSERT INTO seen_jobs VALUES ('existing-public-job')")

    def call_recovery(self, updates):
        call = MagicMock(side_effect=[{'username': recovery.EXPECTED_BOT, 'is_bot': True}, {'url': ''}, updates])
        result = recovery.recover(TOKEN, recovery.EXPECTED_BOT, DIGEST, START, END, call=call, now=NOW, db_path=self.path)
        self.assertEqual([x.args[1] for x in call.call_args_list], ['getMe', 'getWebhookInfo', 'getUpdates'])
        self.assertEqual(call.call_args_list[-1].args[2], {'limit': 100, 'timeout': 0})
        return result

    def test_historical_authorized_message_recovers_without_any_send(self):
        self.assertTrue(self.call_recovery([message()]))
        self.assertEqual(recovery.load_recipient(TOKEN, self.path), str(CHAT))
        raw = self.path.read_bytes()
        for value in [str(CHAT), TOKEN, TEXT]: self.assertNotIn(value.encode(), raw)
        with sqlite3.connect(self.path) as conn:
            self.assertEqual(conn.execute('SELECT id FROM seen_jobs').fetchall(), [('existing-public-job',)])

    def test_empty_queue_leaves_database_byte_for_byte_unchanged(self):
        before = self.path.read_bytes()
        self.assertFalse(self.call_recovery([]))
        self.assertEqual(self.path.read_bytes(), before)

    def test_wrong_nonce_private_sender_and_original_window_never_recover(self):
        variants = [ {'text': 'wrong'}, {'date': 1792420000}, {'date': 1792426000},
                     {'chat': {'id': CHAT, 'type': 'group'}}, {'from': {'id': CHAT+1, 'is_bot': False}}, {'forward_origin': {}} ]
        before = self.path.read_bytes()
        for change in variants:
            update = message(); update['message'].update(change)
            self.assertFalse(self.call_recovery([update]))
            self.assertEqual(self.path.read_bytes(), before)

    def test_multiple_private_chats_refused(self):
        with self.assertRaises(PairingError): self.call_recovery([message(), message(CHAT+1)])

    def test_wrong_bot_stops_before_updates(self):
        call = MagicMock(return_value={'username': 'wrong', 'is_bot': True})
        with self.assertRaises(PairingError):
            recovery.recover(TOKEN, recovery.EXPECTED_BOT, DIGEST, START, END, call=call, now=NOW, db_path=self.path)
        self.assertEqual(call.call_count, 1)

    def test_webhook_is_never_removed(self):
        call = MagicMock(side_effect=[{'username': recovery.EXPECTED_BOT, 'is_bot': True}, {'url': 'https://private.example'}])
        with self.assertRaises(PairingError):
            recovery.recover(TOKEN, recovery.EXPECTED_BOT, DIGEST, START, END, call=call, now=NOW, db_path=self.path)
        self.assertEqual(call.call_count, 2)

    def test_wrong_token_and_tampered_ciphertext_cannot_decrypt(self):
        self.call_recovery([message()])
        with self.assertRaises(InvalidToken): recovery.load_recipient('different-token', self.path)
        with sqlite3.connect(self.path) as conn: conn.execute("UPDATE recovered_recipient SET ciphertext='tampered'")
        with self.assertRaises(InvalidToken): recovery.load_recipient(TOKEN, self.path)

    def test_missing_state_is_not_created(self):
        target = self.path.parent / 'absent.db'
        with self.assertRaises(PairingError): recovery.save_recipient(TOKEN, CHAT, target)
        self.assertFalse(target.exists())

    def test_existing_different_recipient_is_never_overwritten(self):
        recovery.save_recipient(TOKEN, CHAT, self.path)
        with self.assertRaises(PairingError): recovery.save_recipient(TOKEN, CHAT+1, self.path)
        self.assertEqual(recovery.load_recipient(TOKEN, self.path), str(CHAT))

    def test_main_errors_are_sanitized_and_reruns_refused(self):
        for attempt in ['1', '2']:
            output, errors = io.StringIO(), io.StringIO()
            with patch.dict(recovery.os.environ, {'GITHUB_RUN_NUMBER': '1', 'GITHUB_RUN_ATTEMPT': attempt}), patch.object(recovery, 'recover', side_effect=ValueError(TOKEN + TEXT + str(CHAT))) as call, redirect_stdout(output), redirect_stderr(errors):
                self.assertEqual(recovery.main(), 1)
            for private in [TOKEN, TEXT, str(CHAT)]: self.assertNotIn(private, output.getvalue()+errors.getvalue())
            self.assertEqual(call.call_count, 1 if attempt == '1' else 0)
