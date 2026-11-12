import os
import pytest
from homalawn import phonekeys as pk

A = "a" * 64
B = "b" * 64
C = "c" * 64


def test_is_fp():
    assert pk.is_fp(A)
    assert not pk.is_fp("Pixel 8")
    assert not pk.is_fp("A" * 64)      # upper case is not ours
    assert not pk.is_fp("a" * 63)


def test_rekey_moves_unique_name():
    new, moved = pk.rekey({"Pixel 8": {"x": 1}}, [(A, "Pixel 8")])
    assert new == {A: {"x": 1}} and moved


def test_rekey_copies_to_each_phone_of_that_name():
    new, moved = pk.rekey({"Pixel 8": {"x": 1}}, [(A, "Pixel 8"), (B, "Pixel 8")])
    assert new == {A: {"x": 1}, B: {"x": 1}} and moved
    assert new[A] is not new[B]           # deep copies, not one shared object


def test_rekey_existing_fp_entry_wins_and_unknown_name_stays():
    new, moved = pk.rekey({"Pixel 8": {"x": 1}, A: {"x": 2}, "Gone": {"x": 3}},
                          [(A, "Pixel 8")])
    assert new == {A: {"x": 2}, "Gone": {"x": 3}} and moved


def test_rekey_nothing_to_do():
    assert pk.rekey({A: {}}, [(A, "P")]) == ({A: {}}, False)


def test_find_exact_prefix_name_and_ambiguity():
    phones = [(A, "Pixel 8"), (B, "Pixel 8"), (C, "Tab")]
    assert pk.find(A, phones) == A
    assert pk.find("cccccccc", phones) == C
    assert pk.find("tab", phones) == C            # name without case
    with pytest.raises(pk.Ambiguous) as e:
        pk.find("Pixel 8", phones)
    assert "2 phones are named \"Pixel 8\"" in str(e.value)
    assert A[:8] in str(e.value) and B[:8] in str(e.value)
    with pytest.raises(pk.NotFound):
        pk.find("Nope", phones)
    with pytest.raises(pk.Ambiguous):
        pk.find(None, phones)
    assert pk.find(None, [(A, "Pixel 8")]) == A


def test_find_short_prefix_is_a_name():
    with pytest.raises(pk.NotFound):
        pk.find("aaaa", [(A, "Pixel 8")])     # under 8 characters: not a prefix


def test_keep_once(tmp_path):
    p = tmp_path / "look.json"
    p.write_text("one")
    pk.keep_once(p, ".names")
    p.write_text("two")
    pk.keep_once(p, ".names")
    b = tmp_path / "look.json.names"
    assert b.read_text() == "one"
    assert oct(os.stat(b).st_mode & 0o777) == "0o600"
