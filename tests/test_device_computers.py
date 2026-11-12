"""Several computers on one phone (spec section 5). Uses the session's paired link and a second,
pinned-by-hook identity in its own config folder (fixtures `second`, `second_transport`,
`computers` live in conftest.py)."""
import hashlib
import json
import time
from pathlib import Path

import pytest

from conftest import computers, screen_text, tap_text
from homalawn import appid
from test_device_screen import SERVICE, _get, _put, _wait_enabled
from homalawn.link import identity, pairing
from homalawn.link.client import LinkTransport, NotPaired
from homalawn.link.wire import Conn

pytestmark = pytest.mark.device


def identity_fp_of(t):
    return hashlib.sha256(t._identity().cert_der).hexdigest()


def test_list_shows_both_computers(link_transport, second, d):
    link_transport.call("link.ping")
    cs = computers(d)
    assert cs[second.fingerprint]["name"] == "Laptop"
    me = cs[identity_fp_of(link_transport)]
    assert me["online"] is True and me["added"] > 0 and me["seen"] > 0


def test_unpair_by_computer_leaves_no_tombstone(link_transport, second, d):
    peer = identity.Peer(fp=link_transport.peer.fp, name="AVD", hosts=["127.0.0.1:47100"])
    assert pairing.unpair(peer) == "127.0.0.1:47100"   # the second computer unpairs itself
    assert second.fingerprint not in computers(d)
    assert "forgotten" not in hello(second)        # unpaired itself: not forgotten


def hello(ident):
    with Conn("127.0.0.1", 47100, str(ident.cert), str(ident.key)) as c:
        return c.call("link.hello")


def test_pairing_again_clears_the_tombstone(second, d):
    d.call("debug_forget", second.fingerprint)
    assert hello(second)["forgotten"] is True
    d.call("debug_pin", second.fingerprint, extras={"name": "Laptop"})
    assert hello(second)["paired"] is True
    d.call("debug_unpin", second.fingerprint)      # taken off without a tombstone
    assert "forgotten" not in hello(second)


def test_forgotten_computer_is_told(link_transport, second, d, tmp_path):
    peer = identity.Peer(fp=link_transport.peer.fp, name="AVD", hosts=["127.0.0.1:47100"])
    identity.save_peer(peer)                       # the second computer's own peers.json
    t = LinkTransport(peer, ident=second, discover=None)
    t.call("link.ping")
    d.call("debug_forget", second.fingerprint)
    with pytest.raises(NotPaired) as e:
        t.call("link.ping")
    assert hello(second)["forgotten"] is True      # the phone says so; the client's text is Task 8
    assert "forgot this computer" in str(e.value)
    assert identity.load_peers() == []             # removed on this computer (Task 8)
    t.close()


def test_fifth_connection_of_one_computer_drops_its_oldest(link_transport):
    peer, ident = link_transport.peer, link_transport._identity()
    ts = [LinkTransport(peer, ident=ident, keepalive_s=None) for _ in range(5)]
    try:
        for t in ts:
            t.call("link.ping")
        ts[0]._conn.sock.settimeout(3)
        assert ts[0]._conn.readable_eof() or _closed(ts[0])   # the oldest was closed
        for t in ts[1:]:
            t.call("link.ping")                                 # the newest four still work
    finally:
        for t in ts:
            t.close()


def test_forget_closes_open_connection_at_once(link_transport, second, d):
    peer = identity.Peer(fp=link_transport.peer.fp, name="AVD", hosts=["127.0.0.1:47100"])
    t = LinkTransport(peer, ident=second, keepalive_s=None)
    t.call("link.ping")
    d.call("debug_forget", second.fingerprint)
    import time; time.sleep(0.5)
    assert t._conn.readable_eof()
    t.close()


def _closed(t):
    try:
        return t._conn.sock.recv(1) == b""
    except OSError:
        return True


@pytest.fixture
def screen_on(d):
    """Lawnchair's accessibility service on (as test_device_screen does), the old value put back after."""
    before = (_get(d, "enabled_accessibility_services"), _get(d, "accessibility_enabled"))
    services = before[0] or ""
    if SERVICE not in services.split(":"):
        _put(d, "enabled_accessibility_services", ":".join(x for x in (services, SERVICE) if x))
    _put(d, "accessibility_enabled", "1")
    assert _wait_enabled(d, True), "Lawnchair's accessibility service did not bind"
    yield
    _put(d, "enabled_accessibility_services", before[0])
    _put(d, "accessibility_enabled", before[1])
    d.shell("input", "keyevent", "HOME")


def open_settings_row(t, d, row):
    """Homa Lawn's settings (its "Homa Lawn" icon), then one of its rows; returns the screen's text."""
    d.shell("input", "keyevent", "KEYCODE_HOME")
    time.sleep(0.5)
    d.shell("am", "start", "-f", "0x10008000", "-n", f"app.homalawn.debug/{appid.SETTINGS_ALIAS}")  # a new task: whatever screen an earlier test left is gone
    time.sleep(1.5)
    tap_text(t, row)
    time.sleep(1.0)
    return screen_text(t)


def open_computers(t, d):
    """The Computers screen: only the paired computers, Pair a computer, Forget all computers."""
    return open_settings_row(t, d, "Computers")


def open_style(t, d):
    """The Wallpaper & style screen: what is on screen, the Source card, then the source's picker."""
    return open_settings_row(t, d, "Wallpaper & style")


def tap_paired(t, text):
    """Tap the last node showing `text`: the computer's row under "Paired computers"."""
    nodes = [n for n in t.call("screen.tree")["nodes"] if text in (n.get("text"), n.get("desc"))]
    t.call("screen.tap", {"node": nodes[-1]["id"]})


def test_screen_lists_and_forgets_one(link_transport, screen_on, second, d):
    text = open_computers(link_transport, d)
    assert "Laptop" in text and "Not seen yet" in text and "Pair a computer" in text
    assert "Online now" in text                    # the session's own computer
    assert "Forget all computers" in text
    tap_paired(link_transport, "Laptop")
    time.sleep(1.0)
    text = screen_text(link_transport)              # one dialog: status, paired date, details, consequence, Forget
    assert "Fingerprint " + second.fingerprint[:16] in text and "Details" in text and "Paired " in text
    assert "If you forget Laptop, it can no longer reach this phone" in text
    tap_text(link_transport, "Forget")             # the dialog's button: no second confirmation
    time.sleep(1.0)
    assert second.fingerprint not in computers(d)
    assert "Laptop" not in screen_text(link_transport)


def test_screen_forget_all_asks_first(link_transport, screen_on, second, d):
    open_computers(link_transport, d)
    tap_text(link_transport, "Forget all computers")
    time.sleep(1.0)
    assert "Forget all computers?" in screen_text(link_transport)
    tap_text(link_transport, "Cancel")
    time.sleep(0.5)
    assert second.fingerprint in computers(d)


def test_screen_sheet_closes_when_the_computer_is_forgotten_elsewhere(link_transport, screen_on, second, d):
    open_computers(link_transport, d)
    tap_paired(link_transport, "Laptop")
    time.sleep(1.0)
    assert "Fingerprint " in screen_text(link_transport)
    d.call("debug_forget", second.fingerprint)
    time.sleep(1.0)
    assert "Fingerprint " not in screen_text(link_transport)


# Source on the Wallpaper & style screen (spec 5.1, 6.1; moved off Computers in patch 0034): a draft
# until Apply; no pictures here unless a test says so.
K1, X1 = "0123456789abcdef", "00112233445566ff"
PNG = (Path(__file__).parent / "fixtures" / "wp.png").read_bytes()


@pytest.fixture
def looks(d, link_transport, second_transport):
    d.call("debug_look_reset")
    yield
    d.call("debug_look_reset")


def put(t, key, at, theme, **pictures):
    paths = {part: t.write_private(f"look-{key}-{part}.img", PNG) for part in pictures}
    t.call("launcher.look_put", {"key": key, "changed_at": at, "theme": theme, "prefs": [], "pictures": paths})


def test_source_is_a_draft_until_apply(link_transport, second_transport, screen_on, second, looks, d):
    put(link_transport, K1, 1, "Red")
    put(second_transport, X1, 2, "Blue")
    text = open_style(link_transport, d)
    assert "Source" in text and "Latest change" in text and "Apply" not in text.split(" | ")  # no draft: no bar
    tap_text(link_transport, "Laptop")               # the radio row under "Source"
    time.sleep(0.5)
    assert d.call("debug_look_book")["scope"] is None # only a draft
    tap_text(link_transport, "Apply")
    time.sleep(2.0)
    assert d.call("debug_look_book")["scope"] == second.fingerprint
    text = screen_text(link_transport)
    assert "On screen: Laptop's wallpaper & style (Blue)" in text and "Apply" not in text.split(" | ")


def test_discard_returns_to_the_saved_choice(link_transport, second_transport, screen_on, second, looks, d):
    put(second_transport, X1, 2, "Blue")
    open_style(link_transport, d)
    tap_text(link_transport, "Laptop")
    time.sleep(0.5)
    tap_text(link_transport, "Discard")
    time.sleep(0.5)
    assert "Apply" not in screen_text(link_transport).split(" | ")
    assert d.call("debug_look_book")["scope"] is None


def test_replacing_a_wallpaper_set_elsewhere_is_asked(link_transport, second_transport, screen_on, second, looks, d):
    put(second_transport, X1, 2, "Blue", home=1)
    link_transport.call("launcher.wallpaper_set", {"path": link_transport.write_private("elsewhere.img", PNG), "which": "home"})
    open_style(link_transport, d)
    tap_text(link_transport, "Laptop")
    tap_text(link_transport, "Apply")
    time.sleep(1.0)
    text = screen_text(link_transport)
    assert "Replace current wallpaper?" in text and "Replace" in text and "Cancel" in text
    assert "The wallpaper set on this phone can't be backed up, so Undo can't bring it back." in text
    tap_text(link_transport, "Cancel")
    time.sleep(1.0)
    assert d.call("debug_look_book")["scope"] is None
    tap_text(link_transport, "Apply")
    time.sleep(1.0)
    tap_text(link_transport, "Replace")
    time.sleep(2.5)
    assert d.call("debug_look_book")["scope"] == second.fingerprint
    assert "On screen: Laptop's wallpaper & style (Blue)" in screen_text(link_transport)


def test_a_source_without_a_look_says_so(link_transport, screen_on, second, looks, d):
    d.call("debug_look_put", json.dumps({"fp": identity_fp_of(link_transport), "key": "k", "changed_at": 1, "theme": "Red"}))
    d.call("debug_look_scope", second.fingerprint)
    text = open_style(link_transport, d)
    assert "Laptop has not sent its wallpapers yet. Open Homa Lawn on that computer." in text
    assert "Nothing received yet" in text            # Laptop's row in the Source card


def test_forgetting_the_source_hands_the_screen_to_the_next(link_transport, second_transport, screen_on, second, looks, d):
    put(link_transport, K1, 1, "Red")
    put(second_transport, X1, 2, "Blue")
    d.call("debug_look_scope", second.fingerprint)
    open_computers(link_transport, d)
    tap_paired(link_transport, "Laptop")
    time.sleep(0.5)
    assert "The phone will show " in screen_text(link_transport)   # in the computer's one dialog
    tap_text(link_transport, "Forget")
    time.sleep(2.0)
    b = d.call("debug_look_book")
    assert b["scope"] is None and b["source"] == identity_fp_of(link_transport)
    assert b["applied"]["fp"] == identity_fp_of(link_transport)      # the next look reached the screen
    text = screen_text(link_transport)
    assert "Shown now" in text                                       # on the session computer's row
    assert "(Red), since " in open_style(link_transport, d)


def test_forgetting_another_computer_leaves_the_screen_alone(link_transport, second_transport, screen_on, second, looks, d):
    """Only forgetting the look source hands the screen over: after a screen write outside the book
    (undo, a pref) forgetting another computer must not put the source's look back."""
    pref = "homalawn_look_test"  # a datastore key Lawnchair does not read
    put(link_transport, K1, 1, "Red")                 # the first look: this computer is the source
    put(second_transport, X1, 2, "Blue")              # a new computer does not take over
    link_transport.call("launcher.look_show", {"key": K1})
    try:
        link_transport.call("launcher.prefs_set_many",
                            {"writes": [{"store": "datastore", "key": pref, "type": "string", "value": "x"}]})
        assert d.call("debug_look_book")["applied"] is None    # the screen no longer holds the look
        open_computers(link_transport, d)
        tap_paired(link_transport, "Laptop")
        time.sleep(0.5)
        assert "The phone will show " not in screen_text(link_transport)
        tap_text(link_transport, "Forget")
        time.sleep(2.0)
        b = d.call("debug_look_book")
        assert second.fingerprint not in computers(d)
        assert b["source"] == identity_fp_of(link_transport) and b["applied"] is None   # not re-applied
        assert "Replace current wallpaper?" not in screen_text(link_transport)
    finally:
        link_transport.call("launcher.prefs_set_many",
                            {"writes": [{"store": "datastore", "key": pref, "remove": True}]})


def test_a_damaged_look_book_does_not_crash_the_screens(link_transport, screen_on, second, looks, d):
    open_style(link_transport, d)
    d.call("debug_look_corrupt")                      # the open screen hears of it as it would after a restart
    time.sleep(1.0)
    text = screen_text(link_transport)
    assert "can't be read" in text and "forget all computers in Computers" in text
    text = open_computers(link_transport, d)          # where the remedy is: still lists every computer
    assert "Laptop" in text and "Forget all computers" in text
    # "Forget all computers" (which empties the book) is not driven here: it would unpair the session's own computer


def test_hello_reports_addresses(link_transport):
    addrs = link_transport.call("link.hello")["addresses"]
    assert any(a.startswith("10.0.2.") for a in addrs)        # the emulator's own network
    assert not any(a.startswith("127.") or a.startswith("fe80") for a in addrs)
    assert all("%" not in a for a in addrs)
