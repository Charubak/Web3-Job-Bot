import hashlib
import hmac
import io
import sqlite3
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest.mock import MagicMock, patch

import activation
import recipient_envelope
import storage

BOT = "charubak_web3jobs_bot"
TOKEN = "fake-unit-test-token-never-valid"
CHAT = 123456789


class ActivationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.db = Path(self.tmp.name) / "seen_jobs.db"
        self.envelope_path = Path(self.tmp.name) / "recipient.json"
        self.recipient_path_patch = patch.object(recipient_envelope, "DEFAULT_PATH", self.envelope_path)
        self.recipient_path_patch.start()
        self.addCleanup(self.recipient_path_patch.stop)
        self.patch = patch.object(storage, "DB_PATH", self.db)
        self.patch.start()
        self.addCleanup(self.patch.stop)
        # Every API path is mocked, including calls made without explicit injection.
        self.call = MagicMock(side_effect=self.reply)
        self.transport = patch.object(activation, "api_call", self.call)
        self.transport.start()
        self.addCleanup(self.transport.stop)
        storage.initialize_state()

    def reply(self, token, method, payload=None):
        if method == "getMe":
            return {"is_bot": True, "username": BOT}
        if method == "getChat":
            return {"id": payload["chat_id"], "type": "private"}
        if method == "sendMessage":
            return {"chat": {"id": payload["chat_id"], "type": "private"}, "message_id": 8}
        self.fail("Unexpected API operation")

    def prepare(self, **kwargs):
        return activation.prepare(TOKEN, str(CHAT), BOT, **kwargs)

    def deliver(self, **kwargs):
        return activation.deliver(TOKEN, str(CHAT), BOT, **kwargs)

    def check(self, **kwargs):
        return activation.check(TOKEN, str(CHAT), BOT, **kwargs)

    def state(self):
        with sqlite3.connect(self.db) as conn:
            return conn.execute("SELECT destination_tag, status, send_started FROM telegram_activation").fetchone()

    def methods(self):
        return [item.args[1] for item in self.call.call_args_list]

    def test_prepare_validates_destination_and_persists_only_opaque_tag(self):
        self.assertTrue(self.prepare())
        expected = hmac.new(TOKEN.encode(), str(CHAT).encode(), hashlib.sha256).hexdigest()
        self.assertEqual(self.state(), (expected, "pending", 0))
        self.assertEqual(self.methods(), ["getMe", "getChat"])
        self.assertEqual(self.call.call_args_list[1].args[2], {"chat_id": CHAT})
        with sqlite3.connect(self.db) as conn:
            saved = "\n".join(conn.iterdump())
        self.assertNotIn(TOKEN, saved)
        self.assertNotIn(str(CHAT), saved)

    def test_tag_normalizes_numeric_id_and_binds_token(self):
        self.assertTrue(activation.prepare(TOKEN, f" 000{CHAT}\n", BOT))
        self.deliver()
        self.assertTrue(activation.check(TOKEN, f"00{CHAT}", BOT))
        self.assertFalse(activation.check("different-fake-token", str(CHAT), BOT))
        self.assertFalse(activation.check(TOKEN, str(CHAT + 1), BOT))

    def test_invalid_config_never_calls_api_or_changes_database(self):
        original = self.db.read_bytes()
        values = [("", str(CHAT), BOT), ("  ", str(CHAT), BOT), (TOKEN, str(CHAT), "wrong_bot")]
        values += [(TOKEN, value, BOT) for value in ["", "0", "-123", "@someone", "+123", "1.0", "1e3", "١٢٣", True, None, "1" * 33]]
        for args in values:
            with self.subTest(args=args), self.assertRaises(activation.ActivationError):
                activation.prepare(*args)
            self.assertFalse(activation.check(*args))
        self.call.assert_not_called()
        self.assertEqual(self.db.read_bytes(), original)

    def test_missing_database_is_never_created_by_any_command(self):
        self.db.unlink()
        for function in [activation.prepare, activation.deliver]:
            with self.assertRaises(activation.ActivationError):
                function(TOKEN, str(CHAT), BOT)
        self.assertFalse(self.check())
        self.assertFalse(self.db.exists())
        self.assertEqual(list(self.db.parent.iterdir()), [])
        self.call.assert_not_called()

    def test_uninitialized_or_invalid_database_is_not_repaired(self):
        for content in [b"", b"not a sqlite database"]:
            self.db.write_bytes(content)
            with self.assertRaises(activation.ActivationError):
                self.prepare()
            self.assertFalse(self.check())
            self.assertEqual(self.db.read_bytes(), content)
        self.call.assert_not_called()

    def test_missing_seen_schema_fails_closed(self):
        with sqlite3.connect(self.db) as conn:
            conn.execute("DROP TABLE seen_jobs")
            conn.execute("CREATE TABLE seen_jobs (id TEXT PRIMARY KEY)")
        before = self.db.read_bytes()
        with self.assertRaises(activation.ActivationError):
            self.prepare()
        self.assertFalse(self.check())
        self.assertEqual(self.db.read_bytes(), before)
        self.call.assert_not_called()

    def test_wrong_bot_or_unverified_chat_cannot_prepare(self):
        bad_responses = [
            [{"is_bot": True, "username": "another_bot"}],
            [{"is_bot": False, "username": BOT}],
            [None],
            [{"is_bot": True, "username": BOT}, {"id": CHAT, "type": "group"}],
            [{"is_bot": True, "username": BOT}, {"id": CHAT + 1, "type": "private"}],
            [{"is_bot": True, "username": BOT}, {"id": str(CHAT), "type": "private"}],
            [{"is_bot": True, "username": BOT}, None],
        ]
        before = self.db.read_bytes()
        for responses in bad_responses:
            call = MagicMock(side_effect=responses)
            with self.subTest(responses=responses), self.assertRaises(activation.ActivationError):
                self.prepare(call=call)
            self.assertNotIn("sendMessage", [x.args[1] for x in call.call_args_list])
            self.assertEqual(self.db.read_bytes(), before)

    def test_pending_preparation_blocks_every_repeat_and_rotation(self):
        self.prepare()
        before = self.db.read_bytes()
        self.call.reset_mock()
        for token, chat in [(TOKEN, CHAT), ("rotated-fake-token", CHAT), (TOKEN, CHAT + 1)]:
            with self.assertRaisesRegex(activation.ActivationError, "pending"):
                activation.prepare(token, str(chat), BOT)
        self.call.assert_not_called()
        self.assertEqual(self.db.read_bytes(), before)

    def test_prepare_already_confirmed_is_a_verified_noop(self):
        self.prepare()
        self.deliver()
        before = self.db.read_bytes()
        self.call.reset_mock()
        self.assertFalse(self.prepare())
        self.assertEqual(self.methods(), ["getMe", "getChat"])
        self.assertEqual(self.db.read_bytes(), before)

    def test_changed_destination_requires_new_confirmed_setup(self):
        self.prepare()
        self.deliver()
        self.call.reset_mock()
        self.assertTrue(activation.prepare(TOKEN, str(CHAT + 1), BOT))
        self.assertEqual(self.state()[1:], ("pending", 0))
        self.assertFalse(self.check())
        self.assertFalse(activation.check(TOKEN, str(CHAT + 1), BOT))
        self.assertEqual(self.methods(), ["getMe", "getChat"])

    def test_deliver_and_check_do_not_create_activation_table(self):
        before = self.db.read_bytes()
        with self.assertRaises(activation.ActivationError):
            self.deliver()
        self.assertFalse(self.check())
        self.assertEqual(self.db.read_bytes(), before)
        self.call.assert_not_called()

    def test_deliver_requires_matching_pending_tag(self):
        self.prepare()
        self.call.reset_mock()
        for token, chat in [("rotated-fake-token", CHAT), (TOKEN, CHAT + 1)]:
            with self.assertRaises(activation.ActivationError):
                activation.deliver(token, str(chat), BOT)
        self.call.assert_not_called()
        self.assertEqual(self.state()[1:], ("pending", 0))

    def test_deliver_revalidates_bot_and_private_chat_before_claim_or_send(self):
        self.prepare()
        bad_responses = [
            [{"is_bot": True, "username": "another_bot"}],
            [{"is_bot": True, "username": BOT}, {"id": CHAT, "type": "supergroup"}],
        ]
        for responses in bad_responses:
            with self.assertRaises(activation.ActivationError):
                self.deliver(call=MagicMock(side_effect=responses))
            self.assertEqual(self.state()[1:], ("pending", 0))

    def test_deliver_claim_is_durable_before_exactly_one_send(self):
        self.prepare()
        self.call.reset_mock()
        def reply(token, method, payload=None):
            if method == "sendMessage":
                self.assertEqual(self.state()[1:], ("pending", 1))
                self.assertFalse(self.check(call=MagicMock()))
            return self.reply(token, method, payload)
        self.call.side_effect = reply
        self.assertTrue(self.deliver())
        self.assertEqual(self.methods(), ["getMe", "getChat", "sendMessage"])
        payload = self.call.call_args.args[2]
        self.assertEqual(payload["chat_id"], CHAT)
        self.assertIn("once daily", payload["text"])
        self.assertIn("Growth/GTM/PMM", payload["text"])
        self.assertIn("strong, fully remote", payload["text"])
        self.assertIn("allow India", payload["text"])
        self.assertIn("non-India-based employers", payload["text"])
        self.assertIn("Final activation is being verified", payload["text"])
        self.assertNotIn("enabled", payload["text"])
        self.assertEqual(self.state()[1:], ("confirmed", 1))

    def test_confirmed_repeat_deliver_never_sends(self):
        self.prepare()
        self.deliver()
        before = self.db.read_bytes()
        self.call.reset_mock()
        self.assertFalse(self.deliver())
        self.assertEqual(self.methods(), ["getMe", "getChat"])
        self.assertEqual(self.db.read_bytes(), before)

    def test_send_error_is_redacted_pending_and_never_retried(self):
        self.prepare()
        self.call.reset_mock()
        def reply(token, method, payload=None):
            if method == "sendMessage":
                raise RuntimeError(f"private https://secret.example/{TOKEN}/{CHAT}")
            return self.reply(token, method, payload)
        self.call.side_effect = reply
        with self.assertRaises(activation.ActivationError) as caught:
            self.deliver()
        self.assertNotIn(TOKEN, str(caught.exception))
        self.assertNotIn(str(CHAT), str(caught.exception))
        self.assertNotIn("https", str(caught.exception))
        self.assertEqual(self.methods().count("sendMessage"), 1)
        self.assertEqual(self.state()[1:], ("pending", 1))
        self.call.reset_mock()
        with self.assertRaises(activation.ActivationError):
            self.deliver()
        with self.assertRaises(activation.ActivationError):
            self.prepare()
        self.assertFalse(self.check())
        self.call.assert_not_called()

    def test_malformed_or_wrong_acknowledgment_never_activates(self):
        acknowledgments = [None, {}, {"chat": {"id": CHAT + 1, "type": "private"}, "message_id": 8},
            {"chat": {"id": CHAT, "type": "group"}, "message_id": 8},
            {"chat": {"id": str(CHAT), "type": "private"}, "message_id": 8}]
        acknowledgments += [{"chat": {"id": CHAT, "type": "private"}, "message_id": value}
                           for value in [None, True, 0, -1, "8"]]
        for ack in acknowledgments:
            with self.subTest(ack=ack):
                self.prepare()
                call = MagicMock(side_effect=[{"is_bot": True, "username": BOT},
                    {"id": CHAT, "type": "private"}, ack])
                with self.assertRaises(activation.ActivationError):
                    self.deliver(call=call)
                self.assertEqual(call.call_count, 3)
                self.assertEqual(self.state()[1:], ("pending", 1))
                self.assertFalse(self.check())
                with self.assertRaises(activation.ActivationError):
                    self.deliver()
                with sqlite3.connect(self.db) as conn:
                    conn.execute("DELETE FROM telegram_activation")

    def test_boolean_chat_ack_is_not_an_integer_match(self):
        activation.prepare(TOKEN, "1", BOT)
        call = MagicMock(side_effect=[{"is_bot": True, "username": BOT},
            {"id": 1, "type": "private"}, {"chat": {"id": True, "type": "private"}, "message_id": 8}])
        with self.assertRaises(activation.ActivationError):
            activation.deliver(TOKEN, "1", BOT, call=call)
        self.assertEqual(self.state()[1:], ("pending", 1))

    def test_claim_write_failure_never_sends(self):
        self.prepare()
        with sqlite3.connect(self.db) as conn:
            conn.execute("""CREATE TRIGGER reject_claim BEFORE UPDATE OF send_started ON telegram_activation
                BEGIN SELECT RAISE(ABORT, 'private error details'); END""")
        self.call.reset_mock()
        with self.assertRaises(activation.ActivationError) as caught:
            self.deliver()
        self.assertNotIn("private error", str(caught.exception))
        self.assertEqual(self.methods(), ["getMe", "getChat"])
        self.assertEqual(self.state()[1:], ("pending", 0))

    def test_confirmation_write_failure_preserves_durable_pending_and_blocks_retry(self):
        self.prepare()
        with sqlite3.connect(self.db) as conn:
            conn.execute("""CREATE TRIGGER reject_confirmation BEFORE UPDATE OF status ON telegram_activation
                BEGIN SELECT RAISE(ABORT, 'private error details'); END""")
        self.call.reset_mock()
        with self.assertRaises(activation.ActivationError):
            self.deliver()
        self.assertEqual(self.methods().count("sendMessage"), 1)
        self.assertEqual(self.state()[1:], ("pending", 1))
        self.assertFalse(self.check())
        self.call.reset_mock()
        with self.assertRaises(activation.ActivationError):
            self.deliver()
        self.call.assert_not_called()

    def test_prepare_write_failure_never_sends_or_confirms(self):
        self.prepare()
        self.deliver()
        with sqlite3.connect(self.db) as conn:
            conn.execute("""CREATE TRIGGER reject_prepare BEFORE UPDATE ON telegram_activation
                BEGIN SELECT RAISE(ABORT, 'private error details'); END""")
        before = self.db.read_bytes()
        self.call.reset_mock()
        with self.assertRaises(activation.ActivationError):
            activation.prepare(TOKEN, str(CHAT + 1), BOT)
        self.assertEqual(self.methods(), ["getMe", "getChat"])
        self.assertEqual(self.db.read_bytes(), before)
        self.assertEqual(self.state()[1:], ("confirmed", 1))

    def test_preparation_rechecks_pending_state_after_network_validation(self):
        def reply(token, method, payload=None):
            if method == "getChat":
                self.prepare(call=MagicMock(side_effect=self.reply))
            return self.reply(token, method, payload)
        with self.assertRaises(activation.ActivationError):
            self.prepare(call=reply)
        self.assertEqual(self.state()[1:], ("pending", 0))

    def test_deliver_rechecks_attempted_state_under_write_lock(self):
        self.prepare()
        def reply(token, method, payload=None):
            if method == "getChat":
                with sqlite3.connect(self.db) as conn:
                    conn.execute("UPDATE telegram_activation SET send_started=1")
            return self.reply(token, method, payload)
        call = MagicMock(side_effect=reply)
        with self.assertRaises(activation.ActivationError):
            self.deliver(call=call)
        self.assertEqual([x.args[1] for x in call.call_args_list], ["getMe", "getChat"])
        self.assertEqual(self.state()[1:], ("pending", 1))

    def test_check_active_is_read_only_and_reverifies_expected_bot_and_chat(self):
        self.prepare()
        self.deliver()
        before, files = self.db.read_bytes(), set(self.db.parent.iterdir())
        self.call.reset_mock()
        self.assertTrue(self.check())
        self.assertEqual(self.methods(), ["getMe", "getChat"])
        self.assertEqual(self.db.read_bytes(), before)
        self.assertEqual(set(self.db.parent.iterdir()), files)

    def test_check_failures_are_inactive_without_writes_or_sends(self):
        self.prepare()
        self.deliver()
        before = self.db.read_bytes()
        replies = [[{"is_bot": True, "username": "wrong_bot"}],
            [{"is_bot": True, "username": BOT}, {"id": CHAT + 1, "type": "private"}],
            [RuntimeError("private token and chat")]]
        for response in replies:
            call = MagicMock(side_effect=response)
            self.assertFalse(self.check(call=call))
            self.assertNotIn("sendMessage", [x.args[1] for x in call.call_args_list])
            self.assertEqual(self.db.read_bytes(), before)

    def test_check_rechecks_state_after_api_verification(self):
        self.prepare()
        self.deliver()
        def reply(token, method, payload=None):
            if method == "getChat":
                with sqlite3.connect(self.db) as conn:
                    conn.execute("UPDATE telegram_activation SET status='pending'")
            return self.reply(token, method, payload)
        self.assertFalse(self.check(call=MagicMock(side_effect=reply)))
        self.assertEqual(self.state()[1:], ("pending", 1))

    def test_confirmed_deliver_noop_rechecks_state_after_api_verification(self):
        self.prepare()
        self.deliver()
        def reply(token, method, payload=None):
            if method == "getChat":
                with sqlite3.connect(self.db) as conn:
                    conn.execute("UPDATE telegram_activation SET status='pending'")
            return self.reply(token, method, payload)
        call = MagicMock(side_effect=reply)
        with self.assertRaises(activation.ActivationError):
            self.deliver(call=call)
        self.assertEqual([x.args[1] for x in call.call_args_list], ["getMe", "getChat"])

    def test_activation_never_changes_seen_delivery_history(self):
        with sqlite3.connect(self.db) as conn:
            conn.execute("INSERT INTO seen_jobs VALUES ('previous-job', 'Title', 'https://example.test/job', '2026-10-19')")
            conn.execute("INSERT INTO unconfirmed_jobs VALUES ('uncertain-job', 'Title', 'https://example.test/job', '2026-10-19')")
            before = (conn.execute("SELECT * FROM seen_jobs").fetchall(),
                      conn.execute("SELECT * FROM unconfirmed_jobs").fetchall())
        self.prepare()
        self.deliver()
        self.assertTrue(self.check())
        with sqlite3.connect(self.db) as conn:
            self.assertEqual(before, (conn.execute("SELECT * FROM seen_jobs").fetchall(),
                                      conn.execute("SELECT * FROM unconfirmed_jobs").fetchall()))

    def test_malformed_activation_state_fails_closed(self):
        self.prepare()
        with sqlite3.connect(self.db) as conn:
            conn.execute("PRAGMA ignore_check_constraints=ON")
            conn.execute("UPDATE telegram_activation SET status='confirmed', send_started=0")
        before = self.db.read_bytes()
        self.call.reset_mock()
        self.assertFalse(self.check())
        with self.assertRaises(activation.ActivationError):
            self.prepare()
        with self.assertRaises(activation.ActivationError):
            self.deliver()
        self.assertEqual(self.db.read_bytes(), before)
        self.call.assert_not_called()

    def run_cli(self, command, env=None):
        env = env or {"TELEGRAM_BOT_TOKEN": TOKEN, "TELEGRAM_CHAT_ID": str(CHAT), "EXPECTED_BOT": BOT}
        stdout, stderr = io.StringIO(), io.StringIO()
        with patch.dict(activation.os.environ, env, clear=True), patch.object(activation.logging, "disable"), redirect_stdout(stdout), redirect_stderr(stderr):
            code = activation.main(command)
        for text in [stdout.getvalue(), stderr.getvalue()]:
            self.assertNotIn(TOKEN, text)
            self.assertNotIn(str(CHAT), text)
            self.assertNotIn("https://", text)
        return code, stdout.getvalue(), stderr.getvalue()

    def test_cli_outputs_safe_machine_readable_state(self):
        self.assertEqual(self.run_cli(["check"])[:2], (1, "active=false\n"))
        self.assertEqual(self.run_cli(["prepare"])[:2], (0, "prepared=true\n"))
        self.assertEqual(self.run_cli(["check"])[:2], (1, "active=false\n"))
        self.assertEqual(self.run_cli(["deliver"])[:2], (0, "active=true\n"))
        self.assertEqual(self.run_cli(["check"])[:2], (0, "active=true\n"))
        self.assertEqual(self.run_cli(["prepare"])[:2], (0, "prepared=false\n"))
        self.assertEqual(self.run_cli(["deliver"])[:2], (0, "active=true\n"))
        self.assertEqual(self.methods().count("sendMessage"), 1)

    def test_cli_conflicting_secret_and_envelope_never_calls_api(self):
        import json
        self.envelope_path.write_text(json.dumps(recipient_envelope.seal(str(CHAT + 1), recipient_envelope.public_key(TOKEN))))
        before = self.db.read_bytes()
        code, stdout, stderr = self.run_cli(["prepare"])
        self.assertEqual((code, stdout), (1, ""))
        self.call.assert_not_called()
        self.assertEqual(self.db.read_bytes(), before)

    def test_cli_invalid_arguments_or_config_do_not_echo_input(self):
        for args in [[], ["delete"], ["check", TOKEN], [TOKEN]]:
            code, stdout, stderr = self.run_cli(args)
            self.assertEqual(code, 1)
            self.assertEqual(stdout, "")
            self.assertTrue(stderr)
        self.assertEqual(self.run_cli(["check"], env={"EXPECTED_BOT": BOT})[:2], (1, "active=false\n"))
        self.call.assert_not_called()

    def test_cli_pending_and_unexpected_errors_never_claim_success(self):
        self.prepare()
        code, stdout, stderr = self.run_cli(["prepare"])
        self.assertEqual((code, stdout), (1, ""))
        self.assertIn("pending", stderr)
        with patch.object(activation, "deliver", side_effect=RuntimeError(f"https://private/{TOKEN}/{CHAT}")):
            code, stdout, stderr = self.run_cli(["deliver"])
        self.assertEqual((code, stdout), (1, ""))
        self.assertIn("stopped safely", stderr)


if __name__ == "__main__":
    unittest.main()
