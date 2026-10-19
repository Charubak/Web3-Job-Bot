import hashlib
import hmac
import io
import json
import sqlite3
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest.mock import MagicMock, patch

import activation
import job_review_status
import ready_status
import recipient_envelope
import storage

BOT = "charubak_web3jobs_bot"
TOKEN = "fake-review-test-token-never-valid"
CHAT = 123456789
EXACT_TEXT = (
    'Latest job review — 19 October 2026\n'
    '\n'
    '0 verified matches meet all your filters.\n'
    '\n'
    'Open role to review: Gains — Chief Marketing Officer / Head of Growth\n'
    'https://hiring.gains.trade/roles/head-of-growth/\n'
    'The official careers index and application page currently list it as open. Growth/GTM scope looks relevant. There is no verified posting date, so I cannot confirm it is within your 14–30-day window. Permission to work remotely from India and employer headquarters also remain unverified. It is not a qualified alert.\n'
    '\n'
    'Coverage now includes seven additional official Ashby boards, direct Gains careers checks, and broader best-effort indexed X/Twitter searches. X indexing can miss posts; complete internet coverage cannot be guaranteed. Daily checks remain at 9:00 AM IST.'
)


class JobReviewStatusTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.db = Path(self.tmp.name) / "seen_jobs.db"
        self.envelope = Path(self.tmp.name) / "recipient.json"
        for target, attribute, value in [(storage, "DB_PATH", self.db),
                                         (recipient_envelope, "DEFAULT_PATH", self.envelope)]:
            p = patch.object(target, attribute, value)
            p.start()
            self.addCleanup(p.stop)
        self.call = MagicMock(side_effect=self.reply)
        for target in (activation, job_review_status):
            p = patch.object(target, "api_call", self.call)
            p.start()
            self.addCleanup(p.stop)
        storage.initialize_state()
        # Seed the pre-existing activation using mocked transport only.
        activation.prepare(TOKEN, str(CHAT), BOT)
        activation.deliver(TOKEN, str(CHAT), BOT)
        self.call.reset_mock()

    def reply(self, token, method, payload=None):
        if method == "getMe":
            return {"is_bot": True, "username": BOT}
        if method == "getChat":
            return {"id": payload["chat_id"], "type": "private"}
        if method == "sendMessage":
            return {"chat": {"id": payload["chat_id"], "type": "private"}, "message_id": 8}
        self.fail("Unexpected API operation")

    def prepare(self, **kwargs):
        return job_review_status.prepare(TOKEN, str(CHAT), BOT, **kwargs)

    def deliver(self, **kwargs):
        return job_review_status.deliver(TOKEN, str(CHAT), BOT, **kwargs)

    def methods(self, call=None):
        return [item.args[1] for item in (call or self.call).call_args_list]

    def state(self):
        with sqlite3.connect(self.db) as conn:
            return conn.execute("SELECT purpose, destination_tag, text_digest, status FROM telegram_job_review_status").fetchone()

    def test_prepare_is_durable_and_contains_only_opaque_identifiers(self):
        self.assertTrue(self.prepare())
        tag = hmac.new(TOKEN.encode(), str(CHAT).encode(), hashlib.sha256).hexdigest()
        digest = hashlib.sha256(EXACT_TEXT.encode()).hexdigest()
        self.assertEqual(self.state(), ("review-2026-10-19", tag, digest, "prepared"))
        self.assertEqual(self.methods(), ["getMe", "getChat"])
        with sqlite3.connect(self.db) as conn:
            dump = "\n".join(conn.iterdump())
        for value in [TOKEN, str(CHAT), EXACT_TEXT]:
            self.assertNotIn(value, dump)

    def test_exactly_one_send_after_durable_claim_and_verified_noops(self):
        self.prepare()
        self.call.reset_mock()
        def reply(token, method, payload=None):
            if method == "sendMessage":
                self.assertEqual(self.state()[-1], "sending")
                self.assertEqual(payload, {"chat_id": CHAT, "text": EXACT_TEXT, "disable_web_page_preview": True})
            return self.reply(token, method, payload)
        self.call.side_effect = reply
        self.assertTrue(self.deliver())
        self.assertEqual(self.state()[-1], "confirmed")
        self.assertEqual(self.methods(), ["getMe", "getChat", "sendMessage"])
        before = self.db.read_bytes()
        self.assertFalse(self.prepare())
        self.assertFalse(self.deliver())
        self.assertEqual(self.methods().count("sendMessage"), 1)
        self.assertEqual(self.db.read_bytes(), before)

    def test_prepared_snapshot_blocks_preparation_after_cache_restore(self):
        self.prepare()
        snapshot = self.db.read_bytes()
        self.deliver()
        self.db.write_bytes(snapshot)
        self.call.reset_mock()
        with self.assertRaises(job_review_status.JobReviewStatusError):
            self.prepare()
        self.assertEqual(self.state()[-1], "prepared")
        self.call.assert_not_called()
        self.assertEqual(self.db.read_bytes(), snapshot)

    def test_every_existing_uncertain_preparation_blocks_retry_and_rotation(self):
        self.prepare()
        for status in ["prepared", "sending"]:
            with sqlite3.connect(self.db) as conn:
                conn.execute("UPDATE telegram_job_review_status SET status=?", (status,))
            self.call.reset_mock()
            for token, chat in [(TOKEN, CHAT), ("rotated-fake-token", CHAT), (TOKEN, CHAT + 1)]:
                with self.assertRaises(job_review_status.JobReviewStatusError):
                    job_review_status.prepare(token, str(chat), BOT)
            self.call.assert_not_called()

    def test_missing_database_is_never_created(self):
        self.db.unlink()
        for function in [job_review_status.prepare, job_review_status.deliver]:
            with self.assertRaises(job_review_status.JobReviewStatusError):
                function(TOKEN, str(CHAT), BOT)
        self.assertFalse(self.db.exists())
        self.assertEqual(list(self.db.parent.iterdir()), [])
        self.call.assert_not_called()

    def test_invalid_or_uninitialized_database_is_not_repaired(self):
        for content in [b"", b"not a sqlite database"]:
            self.db.write_bytes(content)
            for function in [self.prepare, self.deliver]:
                with self.assertRaises(job_review_status.JobReviewStatusError):
                    function()
                self.assertEqual(self.db.read_bytes(), content)
        self.call.assert_not_called()

    def test_missing_seen_schema_is_not_repaired(self):
        with sqlite3.connect(self.db) as conn:
            conn.execute("DROP TABLE seen_jobs")
            conn.execute("CREATE TABLE seen_jobs (id TEXT PRIMARY KEY)")
        before = self.db.read_bytes()
        with self.assertRaises(job_review_status.JobReviewStatusError):
            self.prepare()
        self.assertEqual(self.db.read_bytes(), before)
        self.call.assert_not_called()

    def test_missing_pending_or_wrong_activation_blocks_prepare_and_delivery(self):
        for sql in ["UPDATE telegram_activation SET status='pending'",
                    "UPDATE telegram_activation SET destination_tag='" + "0" * 64 + "'",
                    "DELETE FROM telegram_activation", "DROP TABLE telegram_activation"]:
            with sqlite3.connect(self.db) as conn:
                conn.execute(sql)
            before = self.db.read_bytes()
            for function in [self.prepare, self.deliver]:
                with self.assertRaises(job_review_status.JobReviewStatusError):
                    function()
                self.assertEqual(self.db.read_bytes(), before)
        self.call.assert_not_called()

    def test_invalid_configuration_is_read_only_and_never_calls_api(self):
        before = self.db.read_bytes()
        values = [("", str(CHAT), BOT), (TOKEN, str(CHAT), "wrong_bot")]
        values += [(TOKEN, value, BOT) for value in ["", "0", "-1", "@user", True, None, "1" * 33]]
        for args in values:
            for function in [job_review_status.prepare, job_review_status.deliver]:
                with self.assertRaises(job_review_status.JobReviewStatusError):
                    function(*args)
        self.call.assert_not_called()
        self.assertEqual(self.db.read_bytes(), before)

    def test_wrong_bot_or_unverified_private_chat_never_prepares(self):
        replies = [[{"is_bot": True, "username": "wrong_bot"}],
                   [{"is_bot": False, "username": BOT}], [None],
                   [{"is_bot": True, "username": BOT}, {"id": CHAT, "type": "group"}],
                   [{"is_bot": True, "username": BOT}, {"id": CHAT + 1, "type": "private"}],
                   [{"is_bot": True, "username": BOT}, {"id": str(CHAT), "type": "private"}]]
        before = self.db.read_bytes()
        for responses in replies:
            call = MagicMock(side_effect=responses)
            with self.subTest(responses=responses), self.assertRaises(job_review_status.JobReviewStatusError):
                self.prepare(call=call)
            self.assertNotIn("sendMessage", self.methods(call))
            self.assertEqual(self.db.read_bytes(), before)

    def test_deliver_revalidates_bot_and_chat_before_claim(self):
        self.prepare()
        for replies in [[{"is_bot": True, "username": "wrong_bot"}],
                        [{"is_bot": True, "username": BOT}, {"id": CHAT, "type": "supergroup"}]]:
            call = MagicMock(side_effect=replies)
            with self.assertRaises(job_review_status.JobReviewStatusError):
                self.deliver(call=call)
            self.assertEqual(self.state()[-1], "prepared")
            self.assertNotIn("sendMessage", self.methods(call))

    def test_deliver_without_preparation_cannot_create_table(self):
        before = self.db.read_bytes()
        with self.assertRaises(job_review_status.JobReviewStatusError):
            self.deliver()
        self.assertEqual(self.db.read_bytes(), before)
        self.call.assert_not_called()

    def test_deliver_requires_exact_tag_and_text_digest(self):
        self.prepare()
        self.call.reset_mock()
        for column in ["destination_tag", "text_digest"]:
            with sqlite3.connect(self.db) as conn:
                conn.execute(f"UPDATE telegram_job_review_status SET {column}=?", ("0" * 64,))
            for function in [self.prepare, self.deliver]:
                with self.assertRaises(job_review_status.JobReviewStatusError):
                    function()
            self.call.assert_not_called()
            with sqlite3.connect(self.db) as conn:
                conn.execute("UPDATE telegram_job_review_status SET destination_tag=?, text_digest=?", (
                    activation._configuration(TOKEN, CHAT, BOT)[1], job_review_status._digest()))

    def test_changed_text_cannot_reuse_receipt(self):
        self.prepare()
        self.call.reset_mock()
        with patch.object(job_review_status, "REVIEW_TEXT", "Different content"):
            with self.assertRaises(job_review_status.JobReviewStatusError):
                self.deliver()
        self.call.assert_not_called()

    def test_invalid_acknowledgments_leave_fence_and_never_retry(self):
        acknowledgments = [None, {}, {"chat": None, "message_id": 8},
            {"chat": {"id": CHAT + 1, "type": "private"}, "message_id": 8},
            {"chat": {"id": CHAT, "type": "group"}, "message_id": 8},
            {"chat": {"id": str(CHAT), "type": "private"}, "message_id": 8},
            {"chat": {"id": True, "type": "private"}, "message_id": 8}]
        acknowledgments += [{"chat": {"id": CHAT, "type": "private"}, "message_id": value}
                           for value in [None, True, 0, -1, "8", 8.0]]
        for ack in acknowledgments:
            with self.subTest(ack=ack):
                self.prepare()
                call = MagicMock(side_effect=[self.reply(TOKEN, "getMe"),
                    {"id": CHAT, "type": "private"}, ack])
                with self.assertRaises(job_review_status.JobReviewStatusError):
                    self.deliver(call=call)
                self.assertEqual(self.methods(call).count("sendMessage"), 1)
                self.assertEqual(self.state()[-1], "sending")
                self.call.reset_mock()
                for function in [self.prepare, self.deliver]:
                    with self.assertRaises(job_review_status.JobReviewStatusError):
                        function()
                self.call.assert_not_called()
                with sqlite3.connect(self.db) as conn:
                    conn.execute("DELETE FROM telegram_job_review_status")

    def test_send_error_is_sanitized_and_stays_fenced(self):
        self.prepare()
        def reply(token, method, payload=None):
            if method == "sendMessage":
                raise RuntimeError(f"https://secret/{TOKEN}/{CHAT}")
            return self.reply(token, method, payload)
        self.call.side_effect = reply
        with self.assertRaises(job_review_status.JobReviewStatusError) as error:
            self.deliver()
        self.assertNotIn(TOKEN, str(error.exception))
        self.assertNotIn(str(CHAT), str(error.exception))
        self.assertNotIn("https", str(error.exception))
        self.assertEqual(self.state()[-1], "sending")
        self.assertEqual(self.methods().count("sendMessage"), 1)
        self.call.reset_mock()
        for function in [self.prepare, self.deliver]:
            with self.assertRaises(job_review_status.JobReviewStatusError):
                function()
        self.call.assert_not_called()

    def test_claim_write_failure_never_sends(self):
        self.prepare()
        with sqlite3.connect(self.db) as conn:
            conn.execute("""CREATE TRIGGER reject_claim BEFORE UPDATE ON telegram_job_review_status
                WHEN NEW.status='sending' BEGIN SELECT RAISE(ABORT, 'private failure'); END""")
        self.call.reset_mock()
        with self.assertRaises(job_review_status.JobReviewStatusError):
            self.deliver()
        self.assertEqual(self.state()[-1], "prepared")
        self.assertEqual(self.methods(), ["getMe", "getChat"])

    def test_mark_confirmed_failure_leaves_fence_and_blocks_retry(self):
        self.prepare()
        with sqlite3.connect(self.db) as conn:
            conn.execute("""CREATE TRIGGER reject_confirmation BEFORE UPDATE ON telegram_job_review_status
                WHEN NEW.status='confirmed' BEGIN SELECT RAISE(ABORT, 'private failure'); END""")
        self.call.reset_mock()
        with self.assertRaises(job_review_status.JobReviewStatusError) as error:
            self.deliver()
        self.assertNotIn("private failure", str(error.exception))
        self.assertEqual(self.state()[-1], "sending")
        self.assertEqual(self.methods().count("sendMessage"), 1)
        self.call.reset_mock()
        for function in [self.prepare, self.deliver]:
            with self.assertRaises(job_review_status.JobReviewStatusError):
                function()
        self.call.assert_not_called()

    def test_concurrent_preparation_is_not_replaced(self):
        def reply(token, method, payload=None):
            if method == "getChat":
                self.prepare(call=MagicMock(side_effect=self.reply))
            return self.reply(token, method, payload)
        with self.assertRaises(job_review_status.JobReviewStatusError):
            self.prepare(call=reply)
        self.assertEqual(self.state()[-1], "prepared")

    def test_activation_changes_during_verification_block_preparation(self):
        def reply(token, method, payload=None):
            if method == "getChat":
                with sqlite3.connect(self.db) as conn:
                    conn.execute("UPDATE telegram_activation SET status='pending'")
            return self.reply(token, method, payload)
        with self.assertRaises(job_review_status.JobReviewStatusError):
            self.prepare(call=reply)
        with sqlite3.connect(self.db) as conn:
            self.assertFalse(conn.execute("SELECT 1 FROM sqlite_master WHERE name='telegram_job_review_status'").fetchone())

    def test_claim_rechecks_current_activation_under_write_lock(self):
        self.prepare()
        def verify(*args):
            with sqlite3.connect(self.db) as conn:
                conn.execute("UPDATE telegram_activation SET status='pending'")
        self.call.reset_mock()
        with patch.object(job_review_status, "_verify_activation", side_effect=verify):
            with self.assertRaises(job_review_status.JobReviewStatusError):
                self.deliver()
        self.assertEqual(self.state()[-1], "prepared")
        self.call.assert_not_called()

    def test_concurrent_send_claim_is_not_repeated(self):
        self.prepare()
        def reply(token, method, payload=None):
            if method == "getChat":
                with sqlite3.connect(self.db) as conn:
                    conn.execute("UPDATE telegram_job_review_status SET status='sending'")
            return self.reply(token, method, payload)
        call = MagicMock(side_effect=reply)
        with self.assertRaises(job_review_status.JobReviewStatusError):
            self.deliver(call=call)
        self.assertEqual(self.methods(call), ["getMe", "getChat"])
        self.assertEqual(self.state()[-1], "sending")

    def test_confirmed_noop_rechecks_receipt_after_verification(self):
        self.prepare()
        self.deliver()
        def reply(token, method, payload=None):
            if method == "getChat":
                with sqlite3.connect(self.db) as conn:
                    conn.execute("UPDATE telegram_job_review_status SET status='sending'")
            return self.reply(token, method, payload)
        call = MagicMock(side_effect=reply)
        with self.assertRaises(job_review_status.JobReviewStatusError):
            self.deliver(call=call)
        self.assertNotIn("sendMessage", self.methods(call))

    def test_malformed_state_fails_closed_without_writes_or_api(self):
        self.prepare()
        for column, value in [("purpose", "wrong-purpose"), ("destination_tag", "not-a-digest"),
                              ("text_digest", "not-a-digest"), ("status", "unknown")]:
            original = self.db.read_bytes()
            with sqlite3.connect(self.db) as conn:
                conn.execute("PRAGMA ignore_check_constraints=ON")
                conn.execute(f"UPDATE telegram_job_review_status SET {column}=?", (value,))
            before = self.db.read_bytes()
            self.call.reset_mock()
            for function in [self.prepare, self.deliver]:
                with self.assertRaises(job_review_status.JobReviewStatusError):
                    function()
            self.call.assert_not_called()
            self.assertEqual(self.db.read_bytes(), before)
            self.db.write_bytes(original)

    def test_activation_seen_pending_and_expiry_history_are_unchanged(self):
        with sqlite3.connect(self.db) as conn:
            conn.execute("INSERT INTO seen_jobs VALUES ('sent', 'Title', 'https://example.test/sent', '2026-10-19')")
            conn.execute("INSERT INTO unconfirmed_jobs VALUES ('pending', 'Title', 'https://example.test/pending', '2026-10-19')")
            conn.execute("INSERT INTO job_history VALUES ('expired', 'Title', 'https://example.test/expired', '2026-09-01', '2026-09-01', '2026-10-19')")
            tables = ["telegram_activation", "seen_jobs", "unconfirmed_jobs", "job_history"]
            before = {table: conn.execute(f"SELECT * FROM {table}").fetchall() for table in tables}
            schemas = {table: conn.execute("SELECT sql FROM sqlite_master WHERE name=?", (table,)).fetchone() for table in tables}
        self.prepare()
        self.deliver()
        self.assertFalse(self.prepare())
        self.assertFalse(self.deliver())
        with sqlite3.connect(self.db) as conn:
            for table in tables:
                self.assertEqual(conn.execute(f"SELECT * FROM {table}").fetchall(), before[table])
                self.assertEqual(conn.execute("SELECT sql FROM sqlite_master WHERE name=?", (table,)).fetchone(), schemas[table])

    def run_cli(self, command, env=None):
        if env is None:
            env = {"TELEGRAM_BOT_TOKEN": TOKEN, "TELEGRAM_CHAT_ID": str(CHAT), "EXPECTED_BOT": BOT}
        stdout, stderr = io.StringIO(), io.StringIO()
        with patch.dict(job_review_status.os.environ, env, clear=True), patch.object(job_review_status.logging, "disable"), redirect_stdout(stdout), redirect_stderr(stderr):
            code = job_review_status.main(command)
        for output in [stdout.getvalue(), stderr.getvalue()]:
            for private in [TOKEN, str(CHAT), "https://"]:
                self.assertNotIn(private, output)
        return code, stdout.getvalue(), stderr.getvalue()

    def test_cli_only_outputs_prepared_and_acknowledged_status(self):
        self.assertEqual(self.run_cli(["prepare"]), (0, "prepared=true\n", ""))
        self.assertEqual(self.run_cli(["deliver"]), (0, "acknowledged=true\n", ""))
        self.assertEqual(self.run_cli(["prepare"]), (0, "prepared=false\n", ""))
        self.assertEqual(self.run_cli(["deliver"]), (0, "acknowledged=true\n", ""))
        self.assertEqual(self.methods().count("sendMessage"), 1)

    def test_cli_resolves_encrypted_recipient(self):
        self.envelope.write_text(json.dumps(recipient_envelope.seal(str(CHAT), recipient_envelope.public_key(TOKEN))))
        env = {"TELEGRAM_BOT_TOKEN": TOKEN, "EXPECTED_BOT": BOT}
        self.assertEqual(self.run_cli(["prepare"], env)[:2], (0, "prepared=true\n"))
        self.assertEqual(self.run_cli(["deliver"], env)[:2], (0, "acknowledged=true\n"))

    def test_cli_conflicting_recipient_never_calls_api_or_writes(self):
        self.envelope.write_text(json.dumps(recipient_envelope.seal(str(CHAT + 1), recipient_envelope.public_key(TOKEN))))
        before = self.db.read_bytes()
        self.assertEqual(self.run_cli(["prepare"])[:2], (1, ""))
        self.call.assert_not_called()
        self.assertEqual(self.db.read_bytes(), before)

    def test_cli_invalid_config_arguments_and_unexpected_errors_are_sanitized(self):
        for argv in [[], ["check"], [TOKEN], ["deliver", str(CHAT)]]:
            code, stdout, stderr = self.run_cli(argv)
            self.assertEqual((code, stdout), (1, ""))
            self.assertTrue(stderr)
        for env in [{}, {"TELEGRAM_BOT_TOKEN": TOKEN, "TELEGRAM_CHAT_ID": str(CHAT)}]:
            self.assertEqual(self.run_cli(["prepare"], env)[:2], (1, ""))
        for function in ["prepare", "deliver"]:
            with patch.object(job_review_status, function, side_effect=RuntimeError(f"https://private/{TOKEN}/{CHAT}")):
                code, stdout, stderr = self.run_cli([function])
            self.assertEqual((code, stdout), (1, ""))
            self.assertIn("stopped safely", stderr)
        self.call.assert_not_called()

    def test_cli_pending_preparation_does_not_report_success(self):
        self.prepare()
        self.call.reset_mock()
        code, stdout, stderr = self.run_cli(["prepare"])
        self.assertEqual((code, stdout), (1, ""))
        self.assertTrue(stderr)
        self.call.assert_not_called()


    def test_ready_receipts_remain_independent_and_unchanged(self):
        original = self.db.read_bytes()
        for status in ["prepared", "sending", "confirmed"]:
            with self.subTest(status=status):
                self.db.write_bytes(original)
                ready_status.prepare(TOKEN, str(CHAT), BOT, call=self.call)
                with sqlite3.connect(self.db) as conn:
                    conn.execute("UPDATE telegram_ready_status SET status=?", (status,))
                    before = conn.execute("SELECT * FROM telegram_ready_status").fetchall()
                    schema = conn.execute("SELECT sql FROM sqlite_master WHERE name='telegram_ready_status'").fetchone()
                self.call.reset_mock()
                self.assertTrue(self.prepare())
                self.assertTrue(self.deliver())
                self.assertFalse(self.prepare())
                self.assertFalse(self.deliver())
                self.assertEqual(self.methods().count("sendMessage"), 1)
                with sqlite3.connect(self.db) as conn:
                    self.assertEqual(conn.execute("SELECT * FROM telegram_ready_status").fetchall(), before)
                    self.assertEqual(conn.execute("SELECT sql FROM sqlite_master WHERE name='telegram_ready_status'").fetchone(), schema)

    def test_changed_text_cannot_reuse_confirmed_receipt(self):
        self.prepare()
        self.deliver()
        before = self.db.read_bytes()
        self.call.reset_mock()
        with patch.object(job_review_status, "REVIEW_TEXT", "Different content"):
            for function in [self.prepare, self.deliver]:
                with self.assertRaises(job_review_status.JobReviewStatusError):
                    function()
        self.call.assert_not_called()
        self.assertEqual(self.db.read_bytes(), before)

    def test_changed_purpose_cannot_reuse_receipt(self):
        self.prepare()
        before = self.db.read_bytes()
        self.call.reset_mock()
        with patch.object(job_review_status, "PURPOSE", ready_status.PURPOSE):
            for function in [self.prepare, self.deliver]:
                with self.assertRaises(job_review_status.JobReviewStatusError):
                    function()
        self.call.assert_not_called()
        self.assertEqual(self.db.read_bytes(), before)


class JobReviewWorkflowTests(unittest.TestCase):
    def test_review_workflow_preserves_reviewed_ready_template(self):
        root = Path(__file__).resolve().parent.parent
        expected = (root / ".github/workflows/ready-status.yml").read_text()
        for old, new in [
            ("Send requested final ready status", "Send requested job review status"),
            ("ready-status", "job-review-status"),
            ("ready_status.py", "job_review_status.py"),
            ("  ready:\n", "  review:\n"),
            ("Send the requested final status once", "Send the requested job review once"),
        ]:
            expected = expected.replace(old, new)
        actual = (root / ".github/workflows/job-review-status.yml").read_text()
        self.assertEqual(actual, expected)
        self.assertIn("github.run_attempt == 1", actual)
        self.assertIn("github.run_number == 1", actual)
        self.assertIn("group: scheduled-job-search", actual)
        self.assertNotIn("schedule:", actual)
        self.assertNotIn("workflow_dispatch:", actual)
        self.assertNotIn("scraper.py", actual)


if __name__ == "__main__":
    unittest.main()
