"""Reversible password encryption for data/users.json (standard library only).

HTTP Digest, WS-UsernameToken digest and RTSP Digest all need the plain password on the device side, so a
one-way hash is not an option. Passwords are encrypted with a key built into the program: the file can be
copied to another PC and still works, and nobody reads a password by opening the file. It is protection
against casual reading, not against someone who has this program - keep the data folder private.

Format: ``enc1:`` + urlsafe base64 of nonce(16) | ciphertext | tag(16).
Cipher: HMAC-SHA256 in counter mode as the keystream, encrypt-then-MAC with a separate HMAC key.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import os

PREFIX = "enc1:"
_MASTER = hashlib.sha256(b"onvif-emulator/users.json/v1/9c1f4e2ab7d84f06").digest()
_ENC_KEY = hmac.new(_MASTER, b"encrypt", hashlib.sha256).digest()
_MAC_KEY = hmac.new(_MASTER, b"authenticate", hashlib.sha256).digest()


def _keystream(nonce: bytes, length: int) -> bytes:
    out = bytearray()
    counter = 0
    while len(out) < length:
        out += hmac.new(_ENC_KEY, nonce + counter.to_bytes(8, "big"), hashlib.sha256).digest()
        counter += 1
    return bytes(out[:length])


def is_encrypted(value: str) -> bool:
    return isinstance(value, str) and value.startswith(PREFIX)


def encrypt(plain: str) -> str:
    data = plain.encode("utf-8")
    nonce = os.urandom(16)
    ct = bytes(a ^ b for a, b in zip(data, _keystream(nonce, len(data)), strict=True))
    tag = hmac.new(_MAC_KEY, nonce + ct, hashlib.sha256).digest()[:16]
    return PREFIX + base64.urlsafe_b64encode(nonce + ct + tag).decode("ascii")


def decrypt(token: str) -> str:
    """Plain text back; raises ValueError when the value was altered or is not ours."""
    if not is_encrypted(token):
        raise ValueError("not an encrypted value")
    try:
        raw = base64.urlsafe_b64decode(token[len(PREFIX):].encode("ascii"))
    except (ValueError, UnicodeEncodeError) as exc:
        raise ValueError("bad encoding") from exc
    if len(raw) < 32:
        raise ValueError("too short")
    nonce, ct, tag = raw[:16], raw[16:-16], raw[-16:]
    if not hmac.compare_digest(tag, hmac.new(_MAC_KEY, nonce + ct, hashlib.sha256).digest()[:16]):
        raise ValueError("integrity check failed")
    return bytes(a ^ b for a, b in zip(ct, _keystream(nonce, len(ct)), strict=True)).decode("utf-8")
