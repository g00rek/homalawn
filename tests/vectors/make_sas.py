"""One-off generator for tests/vectors/sas.json. Run: python tests/vectors/make_sas.py > tests/vectors/sas.json

Deliberately independent of src/homalawn/link/sas.py: it reads the word list straight from the
canonical BIP-39 file, hashes with hashlib, and takes the first 44 bits as a string of '0'/'1'
characters cut into four 11-character pieces (sas.py does integer shifts instead). Inputs are
fixed pseudo-random byte strings (sha256 chains of a seed) of varying lengths; the words depend
only on the bytes, so real certificates are not needed here (the device test uses real ones).
"""
import hashlib
import json
from pathlib import Path

WORDS = (Path(__file__).resolve().parents[2] / "src" / "homalawn" / "link" / "words.txt").read_text().split("\n")[:2048]
assert len(WORDS) == 2048 and WORDS[0] == "abandon" and WORDS[-1] == "zoo"


def chain(seed: str, n: int) -> bytes:
    out, h = b"", seed.encode()
    while len(out) < n:
        h = hashlib.sha256(h).digest()
        out += h
    return out[:n]


def words(phone: bytes, desktop: bytes, np: bytes, nd: bytes) -> list[str]:
    digest = hashlib.sha256(b"omalawn-sas-v1" + phone + desktop + np + nd).digest()
    bits = "".join(f"{b:08b}" for b in digest)[:44]
    return [WORDS[int(bits[i * 11:(i + 1) * 11], 2)] for i in range(4)]


CASES = [
    ("pseudo-certs of typical DER size", chain("phone-1", 389), chain("desktop-1", 412), chain("np-1", 16), chain("nd-1", 16)),
    ("all-zero nonces", chain("phone-2", 401), chain("desktop-2", 398), bytes(16), bytes(16)),
    ("all-ff nonces, short inputs", chain("phone-3", 1), chain("desktop-3", 2), b"\xff" * 16, b"\xff" * 16),
    ("swapped certs give other words", chain("desktop-1", 412), chain("phone-1", 389), chain("np-1", 16), chain("nd-1", 16)),
    ("one nonce bit flipped", chain("phone-1", 389), chain("desktop-1", 412), chain("np-1", 16),
     bytes([chain("nd-1", 16)[0] ^ 1]) + chain("nd-1", 16)[1:]),
]

vectors = []
for name, phone, desktop, np, nd in CASES:
    assert len(np) == 16 and len(nd) == 16
    vectors.append({"name": name, "phone_cert": phone.hex(), "desktop_cert": desktop.hex(),
                    "nonce_phone": np.hex(), "nonce_desktop": nd.hex(), "words": words(phone, desktop, np, nd),
                    # what the desktop sends in link.pair_request before it learns nonce_phone
                    "commit": hashlib.new("sha256", b"omalawn-commit-v1" + nd).hexdigest()})
print(json.dumps({"domain": "omalawn-sas-v1", "commit_domain": "omalawn-commit-v1", "wordlist_sha256": hashlib.sha256(
    "\n".join(WORDS).encode() + b"\n").hexdigest(), "vectors": vectors}, indent=2))
