"""Short authentication string: four words both screens show during pairing.

words = the first 44 bits of SHA-256("omalawn-sas-v1" ‖ phone cert DER ‖ desktop cert DER ‖
nonce_phone ‖ nonce_desktop), as four 11-bit indexes (big-endian) into the BIP-39 English word
list. A man in the middle presents a different certificate to at least one side, so the words
differ. The phone computes the same in Kotlin (shared vectors: tests/vectors/sas.json).
"""
from __future__ import annotations

import hashlib
from functools import lru_cache
from pathlib import Path

DOMAIN = b"omalawn-sas-v1"  # kept: wire compatibility (OmaLawn-era name)
COMMIT_DOMAIN = b"omalawn-commit-v1"  # kept: wire compatibility
WORDS_FILE = Path(__file__).with_name("words.txt")


@lru_cache(maxsize=1)
def _words() -> tuple[str, ...]:
    w = tuple(WORDS_FILE.read_text().splitlines())
    if len(w) != 2048:
        raise RuntimeError(f"{WORDS_FILE} must hold 2048 words, has {len(w)}")
    return w


def words() -> list[str]:
    return list(_words())


def commitment(nonce_desktop: bytes) -> bytes:
    """SHA-256("omalawn-commit-v1" ‖ nonce_desktop): sent before the phone reveals nonce_phone, so a
    fake phone cannot pick its nonce to steer the words."""
    if len(nonce_desktop) != 16:
        raise ValueError("nonce must be 16 bytes")
    return hashlib.sha256(COMMIT_DOMAIN + nonce_desktop).digest()


def sas(phone_cert_der: bytes, desktop_cert_der: bytes, nonce_phone: bytes, nonce_desktop: bytes) -> list[str]:
    if len(nonce_phone) != 16 or len(nonce_desktop) != 16:
        raise ValueError("nonces must be 16 bytes")
    h = hashlib.sha256(DOMAIN + phone_cert_der + desktop_cert_der + nonce_phone + nonce_desktop).digest()
    top44 = int.from_bytes(h[:6], "big") >> 4
    w = _words()
    return [w[(top44 >> (11 * (3 - i))) & 0x7FF] for i in range(4)]
