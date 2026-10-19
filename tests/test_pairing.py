import copy
import hashlib
import io
import unittest
from contextlib import redirect_stdout
from datetime import datetime, timezone
from unittest.mock import MagicMock, patch
import pair_telegram as pairing

NOW = datetime(2026, 10, 19, 15, 10, tzinfo=timezone.utc)
TEXT = "unit-test nonce, not a real pairing code"
DIGEST = hashlib.sha256(TEXT.encode()).hexdigest()
ISSUED, EXPIRES = "2026-10-19T15:05:00Z", "2026-10-19T16:05:00Z"
BOT = "charubak_web3jobs_bot"


def update(chat_id=123):
    return {"update_id": 77, "message": {"message_id": 8, "date": int(NOW.timestamp()),
        "text": TEXT, "chat": {"type": "private", "id": chat_id},
        "from": {"id": chat_id, "is_bot": False}}}


class PairingTests(unittest.TestCase):
    def run_pair(self, updates=None, bot=None, webhook=None, send=None):
        call = MagicMock(side_effect=[bot or {"username": BOT, "is_bot": True},
            webhook if webhook is not None else {"url": ""},
            updates if updates is not None else [update()],
            send if send is not None else {"chat": {"id": 123, "type": "private"}, "message_id": 9}])
        result = pairing.pair("fake-test-token", BOT, DIGEST, ISSUED, EXPIRES, now=NOW, call=call)
        return result, call

    def test_pair_uses_exact_private_chat_and_preserves_update_settings(self):
        result, call = self.run_pair()
        self.assertTrue(result)
        self.assertEqual(call.call_args_list[2].args[2], {"limit": 100, "timeout": 0})
        payload = call.call_args_list[3].args[2]
        self.assertEqual(payload["chat_id"], 123)
        self.assertIn("123", payload["text"])
        self.assertNotIn(TEXT, payload["text"])
        self.assertEqual([x.args[1] for x in call.call_args_list], ["getMe", "getWebhookInfo", "getUpdates", "sendMessage"])

    def test_unknown_or_multiple_chats_never_send(self):
        for updates in [[], [update(123), update(456)]]:
            with self.assertRaises(pairing.PairingError):
                self.run_pair(updates=updates)

    def test_wrong_bot_stops_before_updates(self):
        with self.assertRaisesRegex(pairing.PairingError, "unexpected bot"):
            self.run_pair(bot={"username": "wrong_bot", "is_bot": True})

    def test_webhook_is_never_removed(self):
        with self.assertRaisesRegex(pairing.PairingError, "webhook"):
            self.run_pair(webhook={"url": "https://private.example/webhook"})

    def test_wrong_nonce_and_nonprivate_messages_are_ignored(self):
        changes = [({"text": "not the nonce"}), {"chat": {"type": "group", "id": 123}},
                   {"from": {"id": 456, "is_bot": False}}, {"from": {"id": 123, "is_bot": True}},
                   {"forward_origin": {}}, {"via_bot": {}}, {"date": int(NOW.timestamp()) - 3600},
                   {"date": int(NOW.timestamp()) + 60}]
        for change in changes:
            item = update(); item["message"].update(change)
            with self.subTest(change=change), self.assertRaises(pairing.PairingError):
                self.run_pair(updates=[item])

    def test_duplicate_same_chat_handshakes_have_one_reply(self):
        _, call = self.run_pair(updates=[update(), update()])
        self.assertEqual(sum(x.args[1] == "sendMessage" for x in call.call_args_list), 1)

    def test_expired_or_invalid_window_stops_before_network(self):
        for digest, issued, expires in [("bad", ISSUED, EXPIRES),
             (DIGEST, ISSUED, "2026-10-19T15:06:00Z"),
             (DIGEST, ISSUED, "2026-10-20T16:05:00Z")]:
            call = MagicMock()
            with self.assertRaises(pairing.PairingError):
                pairing.pair("token", BOT, digest, issued, expires, now=NOW, call=call)
            call.assert_not_called()

    def test_no_match_does_not_log_private_updates(self):
        stream = io.StringIO()
        with redirect_stdout(stream), self.assertRaises(pairing.PairingError):
            self.run_pair(updates=[{"message": {"text": "unrelated private message"}}])
        self.assertEqual(stream.getvalue(), "")

    def test_expiry_during_reads_stops_before_send(self):
        call = MagicMock(side_effect=[{"username": BOT, "is_bot": True}, {"url": ""}, [update()]])
        clock = MagicMock(side_effect=[NOW, datetime(2026, 10, 19, 16, 6, tzinfo=timezone.utc)])
        with self.assertRaises(pairing.PairingError):
            pairing.pair("token", BOT, DIGEST, ISSUED, EXPIRES, clock=clock, call=call)
        self.assertEqual(call.call_count, 3)

    def test_bad_ack_is_uncertain_not_retried(self):
        with self.assertRaisesRegex(pairing.PairingError, "unconfirmed"):
            self.run_pair(send={"chat": {"id": 456, "type": "private"}, "message_id": 9})

    def test_api_failure_never_exposes_token_or_response_and_never_retries(self):
        with patch.object(pairing.httpx, "stream", side_effect=RuntimeError("fake-token private payload")) as request:
            with self.assertRaises(pairing.PairingError) as ctx:
                pairing.api_call("fake-token", "sendMessage", {"chat_id": 123})
            self.assertNotIn("fake-token", str(ctx.exception))
            self.assertNotIn("private payload", str(ctx.exception))
            self.assertEqual(request.call_count, 1)

    def test_later_distinct_run_refused_before_api(self):
        with patch.dict(pairing.os.environ, {"GITHUB_RUN_ATTEMPT": "1", "GITHUB_RUN_NUMBER": "2"}), patch.object(pairing, "pair") as call, redirect_stdout(io.StringIO()):
            self.assertEqual(pairing.main(), 1)
            call.assert_not_called()

    def test_rerun_refused_before_api(self):
        with patch.dict(pairing.os.environ, {"GITHUB_RUN_ATTEMPT": "2"}), patch.object(pairing, "pair") as call, redirect_stdout(io.StringIO()):
            self.assertEqual(pairing.main(), 1)
            call.assert_not_called()
