"""The look book on the phone (spec section 6.1): one look per computer, which computer changed
last, the scope. Driven through the debug hooks (no pictures)."""
import json

import pytest

pytestmark = pytest.mark.device
A, B = "a" * 64, "b" * 64


@pytest.fixture
def book(d):
    d.call("debug_look_reset")
    d.call("debug_pin", A, extras={"name": "Desk"})
    d.call("debug_pin", B, extras={"name": "Laptop"})
    yield lambda: d.call("debug_look_book")
    d.call("debug_forget", A); d.call("debug_forget", B)


def put(d, fp, key, at, **more):
    d.call("debug_look_put", json.dumps({"fp": fp, "key": key, "changed_at": at, "theme": key, **more}))


def test_rules(d, book):
    put(d, A, "a1", 1000)
    assert book()["last"] == A                    # nobody was last
    put(d, B, "b1", 5000)
    assert book()["last"] == A                    # a new computer does not take over
    put(d, B, "b2", 6000)
    assert book()["last"] == B                    # a real change does
    put(d, A, "a1", 9000)
    assert book()["last"] == B                    # same key again: nothing
    put(d, A, "a2", 5500)
    assert book()["last"] == B                    # older change than B's
    put(d, A, "a3", 10 ** 15)
    b = book()
    assert b["last"] == A and b["computers"][A]["changed_at"] <= b["now"]   # clamped to the phone's clock
    d.call("debug_forget", A)
    assert book()["last"] == B                    # forget hands over


def test_scope(d, book):
    put(d, A, "a1", 1000); put(d, B, "b1", 2000); put(d, B, "b2", 3000)
    d.call("debug_look_scope", A)
    assert book()["source"] == A
    d.call("debug_forget", A)
    assert book()["scope"] is None and book()["source"] == B


def test_unpair_by_the_computer_drops_its_look_too(d, book):
    put(d, A, "a1", 1000); put(d, B, "b1", 2000)
    d.call("debug_unpin", A)                      # the computer's own unpair, not a forget
    b = book()
    assert A not in b["computers"] and b["last"] == B


def test_parts_are_kept_per_picture(d, book):
    put(d, A, "a1", 1000, parts=["home", "lock"])
    assert sorted(book()["computers"][A]["parts"]) == ["home", "lock"]
    put(d, A, "a2", 2000, parts=["home"])         # a put naming only home: the book still holds lock's picture
    assert sorted(book()["computers"][A]["parts"]) == ["home", "lock"]


def test_last_is_handed_over_when_load_drops_it(d, book):
    put(d, A, "a1", 1000); put(d, B, "b1", 2000)
    assert book()["last"] == A
    d.force_stop()
    d.call("debug_unpin", A)                      # the book is not loaded in this process: nothing hears of it
    b = book()                                    # the load from disk drops A and hands `last` over
    assert A not in b["computers"] and b["last"] == B


def test_pictures_written_before_the_first_put_survive(d, book):
    d.force_stop()                                # nothing has touched the book in this process
    put(d, A, "a1", 1000, parts=["home", "lock"])
    assert sorted(book()["computers"][A]["parts"]) == ["home", "lock"]
    d.force_stop()                                # parts that are not on disk would be dropped on load
    assert sorted(book()["computers"][A]["parts"]) == ["home", "lock"]


def test_same_key_adds_parts(d, book):
    put(d, A, "a1", 1000, parts=["home"])
    put(d, A, "a1", 1000, parts=["lock"])
    assert sorted(book()["computers"][A]["parts"]) == ["home", "lock"]


def test_scope_must_be_a_paired_computer(d, book):
    with pytest.raises(Exception):
        d.call("debug_look_scope", "c" * 64)
    assert book()["scope"] is None


# The link commands (spec 6.2/6.3): a real computer identity puts its look, the phone keeps it and
# shows it only through LookBook.show. Home pictures only: the session hygiene restores the home
# wallpaper, not the lock one.
from pathlib import Path  # noqa: E402

from homalawn.transport import ProviderError  # noqa: E402

K1, K2, X1 = "0123456789abcdef", "fedcba9876543210", "00112233445566ff"
PNG = (Path(__file__).parent / "fixtures" / "wp.png").read_bytes()
TEST_PREF = "homalawn_look_test"  # a datastore key Lawnchair does not read


@pytest.fixture
def looks(d, link_transport):
    d.call("debug_look_reset")
    yield link_transport
    link_transport.call("launcher.prefs_set_many", {"writes": [{"store": "datastore", "key": TEST_PREF, "remove": True}]})
    d.call("debug_look_reset")


def look(key, at=1, pictures=None, prefs=None, theme="Red"):
    return {"key": key, "changed_at": at, "theme": theme, "prefs": prefs or [], "pictures": pictures or {}}


def code(e):
    return e.value.code


def test_put_then_show_changes_the_screen(looks, d):
    t = looks
    path = t.write_private(f"look-{K1}-home.img", PNG)
    pref = {"store": "datastore", "key": TEST_PREF, "type": "string", "value": "red"}
    r = t.call("launcher.look_put", look(K1, pictures={"home": path}, prefs=[pref]))
    assert r["stored"] is True and r["show"] is True and r["scope"] == "latest" and r["source"]["fp"]
    before = t.call("launcher.wallpaper_info")["system_id"]
    assert TEST_PREF not in t.call("launcher.prefs_list")["datastore"]    # a put never touches the screen
    assert t.call("launcher.look_show", {"key": K1}) == {"shown": True}
    assert t.call("launcher.wallpaper_info")["system_id"] != before
    assert t.call("launcher.prefs_list")["datastore"][TEST_PREF] == {"type": "string", "value": "red"}
    st = t.call("launcher.look_state")
    assert st["you"] == {"key": K1, "shown": True} and st["applied"]["fp"] == st["source"]["fp"]
    assert t.call("launcher.look_put", look(K1))["show"] is False       # already on the screen
    with pytest.raises(ProviderError) as e:                             # the staged picture was moved in
        t.call("launcher.look_put", look(K1, pictures={"home": path}))
    assert code(e) == "bad_path"


def test_a_screen_write_outside_the_book_clears_applied(looks):
    """`applied` = the screen holds exactly this look: prefs_set_many (as undo sends it) ends that."""
    t = looks
    t.call("launcher.look_put", look(K1))
    t.call("launcher.look_show", {"key": K1})
    assert t.call("launcher.look_put", look(K1))["show"] is False
    t.call("launcher.prefs_set_many", {"writes": [{"store": "datastore", "key": TEST_PREF, "type": "string", "value": "x"}]})
    assert t.call("launcher.look_state")["applied"] is None
    assert t.call("launcher.look_put", look(K1))["show"] is True


def test_an_unreadable_book_does_not_stop_other_screen_writes(looks, d):
    """Undo and every other screen write go ahead (an unreadable book has no `applied` to clear);
    the look commands still say the book is unreadable."""
    t = looks
    d.call("debug_look_corrupt")
    t.call("launcher.pref_set", {"store": "datastore", "key": TEST_PREF, "type": "string", "value": "y"})
    assert t.call("launcher.prefs_list")["datastore"][TEST_PREF] == {"type": "string", "value": "y"}
    with pytest.raises(ProviderError) as e:
        t.call("launcher.look_state")
    assert code(e) == "look_book_unreadable"
    d.call("debug_look_reset")


def test_put_from_another_computer_is_kept_not_shown(looks, second_transport):
    looks.call("launcher.look_put", look(K1))
    r = second_transport.call("launcher.look_put", look(X1, at=2, theme="Blue"))
    assert r["show"] is False and r["source"]["name"]
    assert r["source"] == looks.call("launcher.look_state")["source"]
    with pytest.raises(ProviderError) as e:
        second_transport.call("launcher.look_show", {"key": X1})
    assert code(e) == "not_source"
    assert second_transport.call("launcher.look_state")["you"] == {"key": X1, "shown": False}


def test_show_of_an_old_key_is_refused(looks):
    looks.call("launcher.look_put", look(K1))
    looks.call("launcher.look_put", look(K2, at=2))
    with pytest.raises(ProviderError) as e:
        looks.call("launcher.look_show", {"key": K1})
    assert code(e) == "stale_key"


def test_a_look_never_shown_asks_to_be_shown_again(looks):
    assert looks.call("launcher.look_put", look(K1))["show"] is True
    assert looks.call("launcher.look_put", look(K1))["show"] is True    # the computer never sent look_show


def test_a_put_naming_some_parts_shows_only_those(looks, d):
    t = looks
    t.call("launcher.look_put", look(K1, pictures={"home": t.write_private(f"look-{K1}-home.img", PNG)}))
    t.call("launcher.look_show", {"key": K1})
    shown = t.call("launcher.wallpaper_info")["system_id"]
    t.call("launcher.look_put", look(K2, at=2))                          # settings only: no picture sent
    assert d.call("debug_look_book")["computers"][t.call("launcher.look_state")["source"]["fp"]]["parts"] == ["home"]
    t.call("launcher.look_show", {"key": K2})
    assert t.call("launcher.wallpaper_info")["system_id"] == shown     # the home picture was not set again


@pytest.mark.parametrize("bad", ["k1", "0123456789ABCDEF", "0123456789abcde", "0123456789abcdef0"])
def test_key_is_16_lowercase_hex(looks, bad):
    with pytest.raises(ProviderError) as e:
        looks.call("launcher.look_put", look(bad))
    assert code(e) == "bad_params"


def test_pictures_must_be_staged_and_prefs_valid(looks):
    with pytest.raises(ProviderError) as e:
        looks.call("launcher.look_put", look(K1, pictures={"home": "/system/etc/hosts"}))
    assert code(e) == "bad_path"
    with pytest.raises(ProviderError) as e:
        looks.call("launcher.look_put", look(K1, pictures={"side": looks.write_private(f"look-{K1}-side.img", PNG)}))
    assert code(e) == "bad_params"
    with pytest.raises(ProviderError) as e:
        looks.call("launcher.look_put", look(K1, prefs=[{"store": "datastore", "key": "x", "type": "colour", "value": "1"}]))
    assert code(e) == "bad_params"
    assert looks.call("launcher.look_state")["you"]["key"] is None     # nothing was stored


def test_a_corrupt_book_is_an_error_not_an_empty_book(looks, d):
    looks.call("launcher.look_put", look(K1))
    d.call("debug_look_corrupt")
    with pytest.raises(ProviderError) as e:
        looks.call("launcher.look_state")
    assert code(e) == "look_book_unreadable"
    with pytest.raises(Exception, match="look_book_unreadable"):
        d.call("debug_look_book")
    d.call("debug_look_reset")                                          # the test hook clears it
    assert d.call("debug_look_book")["computers"] == {}


def test_two_computers_the_later_change_is_shown_and_the_scope_switches(looks, second_transport, d):
    """Two real paired computers (the session's and `second`): the phone shows the look that
    changed last, a scope pins it to one computer, and no scope goes back to the latest."""
    first, second = looks, second_transport
    fp1 = first.call("launcher.look_put", look(K1, at=1000, prefs=[]))["source"]["fp"]
    r2 = second.call("launcher.look_put", look(X1, at=2000, theme="Blue"))
    assert r2["show"] is False and r2["source"]["fp"] == fp1       # a new computer does not take over
    r2 = second.call("launcher.look_put", look(K2, at=3000, theme="Green"))
    assert r2["show"] is True and r2["source"]["name"] == "Laptop"  # a real change does
    assert second.call("launcher.look_show", {"key": K2}) == {"shown": True}
    st = first.call("launcher.look_state")
    assert st["source"]["name"] == "Laptop" and st["scope"] == "latest" and st["you"]["shown"] is False
    book = d.call("debug_look_book")
    fp2 = book["last"]
    assert fp2 != fp1 and set(book["computers"]) == {fp1, fp2}
    d.call("debug_look_scope", fp1)                                 # "Sync look with" the first
    assert d.call("debug_look_book")["source"] == fp1
    assert first.call("launcher.look_state")["source"]["fp"] == fp1
    d.call("debug_look_scope", fp2)
    d.call("debug_forget", fp2)                                     # the scoped computer is forgotten
    b = d.call("debug_look_book")
    assert b["scope"] is None and b["source"] == fp1                # the scope is gone, the look moves on
