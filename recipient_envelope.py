"""Public, encrypted recipient configuration using the existing bot token.

Only the public key and sealed envelope may be published. The token must remain
high entropy and secret; rotating it requires sealing a new envelope. Encryption
hides the recipient but does not establish its identity or authorize delivery.
The separate activation checks must still verify the bot and private chat.
"""
import hashlib
import hmac
import json
import os
from pathlib import Path
import re
import sys

from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric.x25519 import (
    X25519PrivateKey,
    X25519PublicKey,
)
from cryptography.hazmat.primitives.ciphers.aead import ChaCha20Poly1305
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

EXPECTED_BOT = "charubak_web3jobs_bot"
VERSION = 1
DEFAULT_PATH = Path(__file__).with_name("recipient.json")
_KEY_DOMAIN = b"web3-job-bot/recipient-envelope/x25519/v1\x00"
_HKDF_INFO = b"web3-job-bot/recipient-envelope/chacha20poly1305/v1\x00"
_AAD_DOMAIN = b"web3-job-bot/recipient-envelope/metadata/v1\x00"
_FIELDS = {"version", "recipient_public_key", "ephemeral_public_key", "nonce", "ciphertext"}
_ERROR = "Recipient configuration is missing or invalid"
_MAX_FILE_BYTES = 4096


class RecipientEnvelopeError(ValueError):
    """A deliberately value-free configuration error."""


def _private_key(token):
    if not isinstance(token, str) or not token.strip() or len(token) > 4096:
        raise RecipientEnvelopeError(_ERROR)
    return X25519PrivateKey.from_private_bytes(
        hashlib.sha256(_KEY_DOMAIN + token.encode("utf-8")).digest()
    )


def _chat_id(value):
    if type(value) is int:
        value = str(value)
    if not isinstance(value, str) or len(value) > 32:
        raise RecipientEnvelopeError(_ERROR)
    value = value.strip()
    if not re.fullmatch(r"[0-9]+", value) or not 0 < int(value) < 2**63:
        raise RecipientEnvelopeError(_ERROR)
    return str(int(value))


def _hex(value, length):
    if not isinstance(value, str) or not re.fullmatch(r"[0-9a-f]{%d}" % (2 * length), value):
        raise RecipientEnvelopeError(_ERROR)
    return bytes.fromhex(value)


def _json(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("ascii")


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise RecipientEnvelopeError(_ERROR)
        result[key] = value
    return result


def _aead(private, peer, recipient, ephemeral):
    shared = private.exchange(X25519PublicKey.from_public_bytes(peer))
    key = HKDF(
        algorithm=hashes.SHA256(), length=32, salt=None,
        info=_HKDF_INFO + recipient + ephemeral,
    ).derive(shared)
    return ChaCha20Poly1305(key)


def public_key(token) -> str:
    """Return a hex public key; never persist or expose the derived private key."""
    try:
        return _private_key(token).public_key().public_bytes_raw().hex()
    except Exception:
        raise RecipientEnvelopeError(_ERROR) from None


def seal(chat_id, public_key) -> dict:
    """Seal one positive numeric private-chat ID without needing the bot token."""
    try:
        chat_id = _chat_id(chat_id)
        recipient = _hex(public_key, 32)
        private = X25519PrivateKey.generate()
        ephemeral = private.public_key().public_bytes_raw()
        nonce = os.urandom(12)
        metadata = {
            "version": VERSION,
            "recipient_public_key": recipient.hex(),
            "ephemeral_public_key": ephemeral.hex(),
            "nonce": nonce.hex(),
        }
        plaintext = _json({"version": VERSION, "bot": EXPECTED_BOT, "chat_id": chat_id})
        ciphertext = _aead(private, recipient, recipient, ephemeral).encrypt(
            nonce, plaintext, _AAD_DOMAIN + _json(metadata)
        )
        return {**metadata, "ciphertext": ciphertext.hex()}
    except Exception:
        raise RecipientEnvelopeError(_ERROR) from None


def open_envelope(token, payload) -> str:
    """Authenticate and decrypt an envelope, failing closed without value leaks."""
    try:
        if not isinstance(payload, dict) or set(payload) != _FIELDS:
            raise RecipientEnvelopeError(_ERROR)
        if type(payload["version"]) is not int or payload["version"] != VERSION:
            raise RecipientEnvelopeError(_ERROR)
        recipient = _hex(payload["recipient_public_key"], 32)
        ephemeral = _hex(payload["ephemeral_public_key"], 32)
        nonce = _hex(payload["nonce"], 12)
        ciphertext_hex = payload["ciphertext"]
        if not isinstance(ciphertext_hex, str) or not 32 <= len(ciphertext_hex) <= 2048:
            raise RecipientEnvelopeError(_ERROR)
        ciphertext = _hex(ciphertext_hex, len(ciphertext_hex) // 2)
        private = _private_key(token)
        if not hmac.compare_digest(recipient, private.public_key().public_bytes_raw()):
            raise RecipientEnvelopeError(_ERROR)
        metadata = {key: payload[key] for key in _FIELDS if key != "ciphertext"}
        plaintext = _aead(private, ephemeral, recipient, ephemeral).decrypt(
            nonce, ciphertext, _AAD_DOMAIN + _json(metadata)
        )
        data = json.loads(plaintext, object_pairs_hook=_unique_object)
        if (not isinstance(data, dict) or set(data) != {"version", "bot", "chat_id"}
                or type(data["version"]) is not int or data["version"] != VERSION
                or data["bot"] != EXPECTED_BOT or not isinstance(data["chat_id"], str)):
            raise RecipientEnvelopeError(_ERROR)
        chat_id = _chat_id(data["chat_id"])
        if data["chat_id"] != chat_id:
            raise RecipientEnvelopeError(_ERROR)
        return chat_id
    except Exception:
        raise RecipientEnvelopeError(_ERROR) from None


def resolve_chat_id(token, configured="", path=None) -> str:
    """Resolve configuration without requiring secrets for offline previews.

    An existing encrypted recipient must match any explicit setting.
    Offline previews need no token; invalid or conflicting configuration fails.
    """
    try:
        if not isinstance(configured, str):
            raise RecipientEnvelopeError(_ERROR)
        if not token:
            return configured
        try:
            with Path(DEFAULT_PATH if path is None else path).open("rb") as stream:
                encoded = stream.read(_MAX_FILE_BYTES + 1)
        except FileNotFoundError:
            return configured
        if len(encoded) > _MAX_FILE_BYTES:
            raise RecipientEnvelopeError(_ERROR)
        payload = json.loads(encoded, object_pairs_hook=_unique_object)
        resolved = open_envelope(token, payload)
        if configured and _chat_id(configured) != resolved:
            raise RecipientEnvelopeError(_ERROR)
        return resolved
    except Exception:
        raise RecipientEnvelopeError(_ERROR) from None


def main(argv=None):
    """No network, private-key files, or CLI arguments containing credentials."""
    argv = sys.argv[1:] if argv is None else argv
    if argv != ["--public-key"]:
        print("Usage: recipient_envelope.py --public-key", file=sys.stderr)
        return 2
    try:
        key = public_key(os.environ.get("TELEGRAM_BOT_TOKEN", ""))
    except Exception:
        print(_ERROR, file=sys.stderr)
        return 1
    print("recipient_public_key=" + key)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
