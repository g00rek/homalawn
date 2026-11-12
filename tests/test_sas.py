import hashlib
import json
from pathlib import Path

import pytest

from homalawn.link import sas

VECTORS = json.loads((Path(__file__).parent / "vectors" / "sas.json").read_text())


@pytest.mark.parametrize("v", VECTORS["vectors"], ids=lambda v: v["name"])
def test_words_match_shared_vectors(v):
    got = sas.sas(bytes.fromhex(v["phone_cert"]), bytes.fromhex(v["desktop_cert"]),
                  bytes.fromhex(v["nonce_phone"]), bytes.fromhex(v["nonce_desktop"]))
    assert got == v["words"]


@pytest.mark.parametrize("v", VECTORS["vectors"], ids=lambda v: v["name"])
def test_commitment_matches_shared_vectors(v):
    assert sas.commitment(bytes.fromhex(v["nonce_desktop"])).hex() == v["commit"]


def test_word_list_is_canonical_bip39_english():
    words = sas.words()
    assert len(words) == 2048 and len(set(words)) == 2048
    assert hashlib.sha256(sas.WORDS_FILE.read_bytes()).hexdigest() == VECTORS["wordlist_sha256"] \
        == "2f5eed53a4727b4bf8880d8f3f199efc90e58503646d9ff8eff3a2ed3b24dbda"


def test_nonces_must_be_16_bytes():
    with pytest.raises(ValueError):
        sas.sas(b"a", b"b", bytes(15), bytes(16))
    with pytest.raises(ValueError):
        sas.sas(b"a", b"b", bytes(16), bytes(17))


def test_the_same_vectors_are_in_the_phone_patch():
    """The phone ships the same word list as an asset; the patch must carry it byte for byte."""
    patch = (Path(__file__).parent.parent / "patches" / "0003-omalawn-link.patch").read_text()
    marker = "+++ b/lawnchair/assets/omalawn_words.txt\n"
    assert marker in patch
    body = patch.split(marker, 1)[1].split("\ndiff --git", 1)[0]
    lines = [l[1:] for l in body.splitlines() if l.startswith("+")]
    assert lines == sas.words()
