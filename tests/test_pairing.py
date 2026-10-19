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
        clock = MagicMock(side_effect=[NOW, NOW, datetime(2026, 10, 19, 16, 6, tzinfo=timezone.utc)])
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

    def test_api_probe_never_sends_or_reveals_private_updates(self):
        call = MagicMock(side_effect=[{"username": BOT, "is_bot": True}, {"url": ""}, [update()]])
        output = io.StringIO()
        with redirect_stdout(output):
            pairing.verify_api("fake-test-token", BOT, call=call)
        self.assertEqual([x.args[1] for x in call.call_args_list], ["getMe", "getWebhookInfo", "getUpdates"])
        self.assertEqual(call.call_args_list[2].args[2], {"limit": 1, "timeout": 0})
        for private in [TEXT, "fake-test-token", "123", "77"]:
            self.assertNotIn(private, output.getvalue())
        self.assertIn("method=getUpdates category=ok", output.getvalue())

    def test_api_probe_existing_webhook_stops_before_updates(self):
        call = MagicMock(side_effect=[{"username": BOT, "is_bot": True}, {"url": "https://private-webhook.example/secret"}])
        output = io.StringIO()
        with redirect_stdout(output):
            pairing.verify_api("token", BOT, call=call)
        self.assertEqual(call.call_count, 2)
        self.assertNotIn("private-webhook", output.getvalue())
        self.assertIn("webhook_present", output.getvalue())

    def test_api_errors_keep_only_method_and_safe_http_category(self):
        for status in [400, 401, 403, 404, 409, 429, 500, 503]:
            response = MagicMock(status_code=status)
            response.iter_bytes.return_value = [b'{"description":"private-response-body"}']
            with patch.object(pairing.httpx, "stream") as stream:
                stream.return_value.__enter__.return_value = response
                with self.assertRaises(pairing.PairingError) as ctx:
                    pairing.api_call("fake-token", "getUpdates")
            self.assertEqual(str(ctx.exception), f"Telegram verification failed: method=getUpdates category=HTTP_{status}; no setup reply was attempted")
            self.assertEqual(stream.call_count, 1)

    def test_api_timeout_never_exposes_request_or_exception(self):
        with patch.object(pairing.httpx, "stream", side_effect=pairing.httpx.ReadTimeout("private-token-and-url")):
            with self.assertRaises(pairing.PairingError) as ctx:
                pairing.api_call("fake-token", "getMe")
        self.assertIn("method=getMe category=timeout", str(ctx.exception))
        self.assertNotIn("private", str(ctx.exception))
        self.assertNotIn("fake-token", str(ctx.exception))

    def test_api_probe_cli_never_calls_pair(self):
        with patch.dict(pairing.os.environ, {"GITHUB_RUN_ATTEMPT": "1", "GITHUB_RUN_NUMBER": "1"}), patch.object(pairing.sys, "argv", ["pair_telegram.py", "--verify-api"]), patch.object(pairing, "verify_api") as probe, patch.object(pairing, "pair") as send:
            self.assertEqual(pairing.main(), 0)
            probe.assert_called_once()
            send.assert_not_called()

    def test_later_distinct_run_refused_before_api(self):
        with patch.dict(pairing.os.environ, {"GITHUB_RUN_ATTEMPT": "1", "GITHUB_RUN_NUMBER": "2"}), patch.object(pairing, "pair") as call, redirect_stdout(io.StringIO()):
            self.assertEqual(pairing.main(), 1)
            call.assert_not_called()

    def test_diagnostic_reports_booleans_without_send_or_private_data(self):
        call = MagicMock(side_effect=[{"username": BOT, "is_bot": True}, {"url": ""}, [update()]])
        output = io.StringIO()
        with redirect_stdout(output):
            self.assertFalse(pairing.pair("token", BOT, DIGEST, ISSUED, EXPIRES, now=NOW, call=call, diagnostic=True))
        self.assertEqual(call.call_count, 3)
        self.assertIn('"exact_nonce_present": true', output.getvalue())
        self.assertNotIn(TEXT, output.getvalue())
        self.assertNotIn("123", output.getvalue())

    def test_diagnostic_distinguishes_whitespace_without_matching_it_for_send(self):
        item = update(); item["message"]["text"] = "  " + TEXT + "\n"
        result = pairing.diagnose_updates([item], DIGEST, NOW.timestamp()-60, NOW.timestamp()+60, NOW.timestamp())
        self.assertFalse(result["exact_nonce_present"])
        self.assertTrue(result["trimmed_nonce_present"])
        with self.assertRaises(pairing.PairingError):
            pairing.select_chat([item], DIGEST, NOW.timestamp()-60, NOW.timestamp()+60, NOW.timestamp())

    def test_bounded_listener_can_match_later_message_without_acknowledging(self):
        call = MagicMock(side_effect=[{"username": BOT, "is_bot": True}, {"url": ""}, [], [update()], {"chat": {"id": 123, "type": "private"}, "message_id": 9}])
        sleeper = MagicMock()
        with redirect_stdout(io.StringIO()):
            self.assertTrue(pairing.pair("token", BOT, DIGEST, ISSUED, EXPIRES, now=NOW, call=call, wait_seconds=5, sleep=sleeper, monotonic=MagicMock(return_value=0)))
        self.assertEqual(call.call_count, 5)
        self.assertEqual(sleeper.call_args.args, (2,))
        self.assertEqual(call.call_args_list[3].args[2], {"limit": 100, "timeout": 0})

    def test_bounded_listener_stops_without_sending_at_deadline(self):
        call = MagicMock(side_effect=[{"username": BOT, "is_bot": True}, {"url": ""}, []])
        with redirect_stdout(io.StringIO()), self.assertRaises(pairing.NoHandshake):
            pairing.pair("token", BOT, DIGEST, ISSUED, EXPIRES, now=NOW, call=call, wait_seconds=5, monotonic=MagicMock(side_effect=[0, 0, 5]))
        self.assertEqual(call.call_count, 3)

    def test_bounded_listener_does_not_retry_api_conflicts(self):
        call = MagicMock(side_effect=[{"username": BOT, "is_bot": True}, {"url": ""}, pairing.PairingError("verification unavailable")])
        with redirect_stdout(io.StringIO()), self.assertRaises(pairing.PairingError):
            pairing.pair("token", BOT, DIGEST, ISSUED, EXPIRES, now=NOW, call=call, wait_seconds=5)
        self.assertEqual(call.call_count, 3)

    def test_listener_does_not_poll_again_after_sleep_reaches_deadline(self):
        state = {"time": 0, "polls": 0}
        def call(token, method, payload=None):
            if method == "getMe": return {"username": BOT, "is_bot": True}
            if method == "getWebhookInfo": return {"url": ""}
            if method == "getUpdates":
                state["polls"] += 1
                state["time"] = 179
                return []
            self.fail("Must not send")
        def sleep(seconds): state["time"] += seconds
        with redirect_stdout(io.StringIO()), self.assertRaises(pairing.NoHandshake):
            pairing.pair("token", BOT, DIGEST, ISSUED, EXPIRES, now=NOW, call=call, wait_seconds=180, sleep=sleep, monotonic=lambda: state["time"])
        self.assertEqual(state, {"time": 180, "polls": 1})

    def test_listener_rejects_match_returned_after_deadline(self):
        state = {"time": 0}
        def call(token, method, payload=None):
            if method == "getMe": return {"username": BOT, "is_bot": True}
            if method == "getWebhookInfo": return {"url": ""}
            if method == "getUpdates":
                state["time"] = 181
                return [update()]
            self.fail("Must not send")
        with redirect_stdout(io.StringIO()), self.assertRaises(pairing.NoHandshake):
            pairing.pair("token", BOT, DIGEST, ISSUED, EXPIRES, now=NOW, call=call, wait_seconds=180, monotonic=lambda: state["time"])

    def test_listener_checks_expiry_before_poll(self):
        call = MagicMock(side_effect=[{"username": BOT, "is_bot": True}, {"url": ""}])
        clock = MagicMock(side_effect=[NOW, datetime(2026, 10, 19, 16, 6, tzinfo=timezone.utc)])
        with redirect_stdout(io.StringIO()), self.assertRaises(pairing.PairingError):
            pairing.pair("token", BOT, DIGEST, ISSUED, EXPIRES, clock=clock, call=call, wait_seconds=180)
        self.assertEqual(call.call_count, 2)

    def test_rerun_refused_before_api(self):
        with patch.dict(pairing.os.environ, {"GITHUB_RUN_ATTEMPT": "2"}), patch.object(pairing, "pair") as call, redirect_stdout(io.StringIO()):
            self.assertEqual(pairing.main(), 1)
            call.assert_not_called()
