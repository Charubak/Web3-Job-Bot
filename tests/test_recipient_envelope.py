"""Entirely offline tests using synthetic, never-valid Telegram credentials."""
import io
import json
import os
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from cryptography.hazmat.primitives.asymmetric.x25519 import X25519PrivateKey

import recipient_envelope as envelope

TOKEN = "fake-unit-test-token-never-valid"
OTHER_TOKEN = "different-fake-unit-test-token-never-valid"
CHAT = "123456789"  # Synthetic fixture, not a real recipient.


class RecipientEnvelopeTests(unittest.TestCase):
    def setUp(self):
        self.key = envelope.public_key(TOKEN)
        self.payload = envelope.seal(CHAT, self.key)
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / "recipient.json"
        self.path.write_text(json.dumps(self.payload), encoding="utf-8")

    def assert_rejected(self, function, *args, **kwargs):
        with self.assertRaises(envelope.RecipientEnvelopeError) as error:
            function(*args, **kwargs)
        self.assertEqual(str(error.exception), envelope._ERROR)
        self.assertNotIn(TOKEN, str(error.exception))
        self.assertNotIn(CHAT, str(error.exception))
        self.assertTrue(error.exception.__suppress_context__)

    def forged_plaintext(self, plaintext):
        """Produce authenticated invalid content to exercise semantic validation."""
        recipient = bytes.fromhex(self.key)
        private = X25519PrivateKey.generate()
        ephemeral = private.public_key().public_bytes_raw()
        nonce = os.urandom(12)
        metadata = {
            "version": 1, "recipient_public_key": self.key,
            "ephemeral_public_key": ephemeral.hex(), "nonce": nonce.hex(),
        }
        ciphertext = envelope._aead(private, recipient, recipient, ephemeral).encrypt(
            nonce, plaintext, envelope._AAD_DOMAIN + envelope._json(metadata)
        )
        return {**metadata, "ciphertext": ciphertext.hex()}

    def test_public_key_is_deterministic_and_token_bound(self):
        self.assertRegex(self.key, r"^[0-9a-f]{64}$")
        self.assertEqual(self.key, envelope.public_key(TOKEN))
        self.assertNotEqual(self.key, envelope.public_key(OTHER_TOKEN))

    def test_round_trip_and_public_json_shape(self):
        self.assertEqual(envelope.open_envelope(TOKEN, self.payload), CHAT)
        self.assertEqual(set(self.payload), {
            "version", "recipient_public_key", "ephemeral_public_key", "nonce", "ciphertext",
        })
        serialized = json.dumps(self.payload)
        self.assertNotIn(TOKEN, serialized)
        self.assertNotIn('"chat_id"', serialized)
        self.assertNotIn('"' + CHAT + '"', serialized)
        self.assertEqual(envelope.open_envelope(TOKEN, json.loads(serialized)), CHAT)

    def test_every_seal_uses_fresh_ephemeral_key_nonce_and_ciphertext(self):
        second = envelope.seal(CHAT, self.key)
        for key in ("ephemeral_public_key", "nonce", "ciphertext"):
            self.assertNotEqual(self.payload[key], second[key])
        self.assertEqual(envelope.open_envelope(TOKEN, second), CHAT)

    def test_seal_normalizes_valid_numeric_inputs(self):
        for value in (int(CHAT), " 000" + CHAT + "\n", str(2**63 - 1)):
            with self.subTest(value=value):
                sealed = envelope.seal(value, self.key)
                self.assertEqual(envelope.open_envelope(TOKEN, sealed), str(int(value)))

    def test_invalid_tokens_fail_without_values(self):
        for token in ("", "  ", None, True, 1, b"fake", "x" * 4097, "\ud800"):
            with self.subTest(type=type(token).__name__):
                self.assert_rejected(envelope.public_key, token)
                self.assert_rejected(envelope.open_envelope, token, self.payload)

    def test_wrong_token_fails_closed(self):
        self.assert_rejected(envelope.open_envelope, OTHER_TOKEN, self.payload)

    def test_invalid_chat_ids_are_rejected(self):
        for value in (None, True, False, 0, -1, 2**63, 1.5, "", " ", "0", "-123",
                      "+123", "@someone", "1.0", "1e3", "١٢٣", "1\x002", "1" * 33):
            with self.subTest(value=value):
                self.assert_rejected(envelope.seal, value, self.key)

    def test_invalid_public_keys_are_rejected(self):
        for value in (None, True, "", "00" * 31, "00" * 33, "00" * 32,
                      "01" + "00" * 31, "gg" * 32, self.key.upper(), " " + self.key):
            with self.subTest(value=value):
                self.assert_rejected(envelope.seal, CHAT, value)

    def test_tampering_with_any_envelope_field_is_rejected(self):
        for key in self.payload:
            changed = self.payload.copy()
            if key == "version":
                changed[key] = 2
            else:
                value = changed[key]
                changed[key] = ("1" if value[0] == "0" else "0") + value[1:]
            with self.subTest(field=key):
                self.assert_rejected(envelope.open_envelope, TOKEN, changed)

    def test_schema_missing_extra_wrong_type_and_malformed_fields_are_rejected(self):
        invalid = [None, [], "{}", {}, {**self.payload, "extra": CHAT}]
        for key in self.payload:
            missing = self.payload.copy()
            del missing[key]
            invalid.append(missing)
            invalid.append({**self.payload, key: None})
        invalid += [{**self.payload, "version": value} for value in (True, "1", 1.0)]
        invalid += [{**self.payload, "ciphertext": value} for value in (
            "", "aa" * 15, "a" * 33, "gg" * 80, "aa" * 1025,
        )]
        invalid += [{**self.payload, key: value} for key in (
            "recipient_public_key", "ephemeral_public_key", "nonce",
        ) for value in ("", "00", " 00", "gg" * 32)]
        invalid.append({**self.payload, "ephemeral_public_key": "00" * 32})
        for payload in invalid:
            with self.subTest(payload=payload):
                self.assert_rejected(envelope.open_envelope, TOKEN, payload)

    def test_authenticated_wrong_bot_version_and_invalid_recipient_are_rejected(self):
        valid = {"version": 1, "bot": envelope.EXPECTED_BOT, "chat_id": CHAT}
        invalid = [[], None, {}, {**valid, "extra": True}]
        invalid += [{**valid, "bot": value} for value in (
            "another_bot", envelope.EXPECTED_BOT.upper(), None,
        )]
        invalid += [{**valid, "version": value} for value in (0, 2, True, 1.0, "1")]
        invalid += [{**valid, "chat_id": value} for value in (
            1, True, None, "0", "-1", str(2**63), "0" + CHAT, " " + CHAT, "١٢٣",
        )]
        for data in invalid:
            with self.subTest(data=data):
                self.assert_rejected(envelope.open_envelope, TOKEN,
                                     self.forged_plaintext(envelope._json(data)))

    def test_authenticated_malformed_or_duplicate_json_is_rejected(self):
        invalid = [b"not json", b"\xff", b'{"version":1,"version":1}',
                   b'{"version":1,"bot":"charubak_web3jobs_bot",'
                   b'"chat_id":"123456789","chat_id":"123456789"}']
        for plaintext in invalid:
            with self.subTest(plaintext=plaintext):
                self.assert_rejected(envelope.open_envelope, TOKEN,
                                     self.forged_plaintext(plaintext))

    def test_resolve_reads_explicit_path_and_module_default(self):
        self.assertEqual(envelope.resolve_chat_id(TOKEN, path=self.path), CHAT)
        with patch.object(envelope, "DEFAULT_PATH", self.path):
            self.assertEqual(envelope.resolve_chat_id(TOKEN), CHAT)

    def test_explicit_configuration_must_match_encrypted_recipient(self):
        self.assertEqual(envelope.resolve_chat_id(TOKEN, CHAT, self.path), CHAT)
        self.assert_rejected(envelope.resolve_chat_id, TOKEN, "987654321", self.path)
        self.assert_rejected(envelope.resolve_chat_id, TOKEN, "bad", self.path)

    def test_explicit_configuration_works_without_envelope(self):
        missing = self.path.with_name("missing.json")
        self.assertEqual(envelope.resolve_chat_id(TOKEN, CHAT, missing), CHAT)

    def test_non_string_explicit_configuration_does_not_fall_back(self):
        for value in (None, True, 1):
            self.assert_rejected(envelope.resolve_chat_id, TOKEN, value, self.path)

    def test_missing_token_or_file_returns_empty_for_offline_previews(self):
        missing = self.path.with_name("missing.json")
        self.assertEqual(envelope.resolve_chat_id(TOKEN, path=missing), "")
        self.path.write_bytes(b"invalid envelope")
        with patch.object(Path, "open", side_effect=AssertionError("Must not read a file")):
            self.assertEqual(envelope.resolve_chat_id("", path=self.path), "")
            self.assertEqual(envelope.resolve_chat_id("", CHAT, self.path), CHAT)

    def test_file_errors_are_generic_and_never_include_paths(self):
        self.assert_rejected(envelope.resolve_chat_id, TOKEN, path=self.path.parent)
        for encoded in (b"", b"not-json", b"\xff", b" " * 4097,
                        b'{"version":1,"version":1}'):
            self.path.write_bytes(encoded)
            self.assert_rejected(envelope.resolve_chat_id, TOKEN, path=self.path)

    def test_cli_outputs_only_labeled_public_key_and_writes_nothing(self):
        stdout, stderr = io.StringIO(), io.StringIO()
        with patch.dict(os.environ, {"TELEGRAM_BOT_TOKEN": TOKEN}, clear=True), \
                patch.object(Path, "open", side_effect=AssertionError("No files")), \
                redirect_stdout(stdout), redirect_stderr(stderr):
            result = envelope.main(["--public-key"])
        self.assertEqual(result, 0)
        self.assertEqual(stdout.getvalue(), "recipient_public_key=" + self.key + "\n")
        self.assertEqual(stderr.getvalue(), "")

    def test_cli_missing_token_and_invalid_arguments_never_echo_values(self):
        for argv, expected_status in ((["--public-key"], 1), ([TOKEN, CHAT], 2), ([], 2)):
            stdout, stderr = io.StringIO(), io.StringIO()
            with patch.dict(os.environ, {}, clear=True), \
                    redirect_stdout(stdout), redirect_stderr(stderr):
                result = envelope.main(argv)
            self.assertEqual(result, expected_status)
            self.assertEqual(stdout.getvalue(), "")
            self.assertNotIn(TOKEN, stderr.getvalue())
            self.assertNotIn(CHAT, stderr.getvalue())
            self.assertEqual(len(stderr.getvalue().splitlines()), 1)


if __name__ == "__main__":
    unittest.main()
