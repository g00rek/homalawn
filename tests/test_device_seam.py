"""Tests against the test AVD (HOMALAWN_SERIAL=emulator-5560).

One session fixture saves the AVD's state first (launcher DB, preferences, wallpaper, media-read
permission state, default home) into a temporary HOMALAWN_DATA, clears the crash log, and at the
end puts everything back and ASSERTS the AVD is as it was: same layout (ids aside), no unbound
widget rows, Lawnchair still the default home, no crash logged, no computer left pinned.

The seam tests run once per transport (`la` is parametrised: `adb`, then `link`). The link variant
reaches the phone through `adb forward tcp:47100 tcp:47100`, pairs ONE desktop identity per session
the real way (CLI `pair` + tapping Accept in the shade) and unpairs it at the end. adb (`d`) is
still used to check what the phone did; the operation under test goes through `la.transport`."""
import os
import re
import time
from pathlib import Path

import pytest

from conftest import DEVICE_PACKAGE as PKG
from homalawn.adb import AdbTransport
from homalawn.session import Launcher

pytestmark = pytest.mark.device
SERIAL = os.environ.get("HOMALAWN_SERIAL")
needs_avd = pytest.mark.skipif(not SERIAL, reason="HOMALAWN_SERIAL not set")
BUILD = Path(__file__).parent.parent / "build"


def _norm(items):
    out = []
    for i in items:
        i = {k: v for k, v in i.items() if k not in ("id", "appWidgetId")}
        if "items" in i:
            i["items"] = _norm(i["items"])
        out.append(i)
    return out


def norm_layout(lay):
    return {"grid": lay["grid"], "hotseat": _norm(lay["hotseat"]),
            "screens": [{"index": s["index"], "items": _norm(s["items"])} for s in lay["screens"]]}


def unbound_widgets(la):
    bad = []
    for s in la.get_layout()["screens"]:
        for i in s["items"]:
            if i["type"] == "widget":
                wid = i["appWidgetId"]
                if wid <= 0 or not la.transport.call("launcher.widget_status", {"id": wid})["bound"]:
                    bad.append(i)
    return bad


def lock_kept(before, now):
    """The lock screen still shows what it showed in `before`: the same lock wallpaper, or, when it
    showed the home one, the old home picture Android moved there (it keeps the old home id)."""
    if before.get("lock_follows_home"):
        return now["lock_id"] in (before["lock_id"], before["system_id"])
    return now["lock_id"] == before["lock_id"]


def crash_log(d):
    return d.shell("logcat", "-b", "crash", "-d").strip()


def shown(d, pkg, screen, x, y):
    """Is the app in Lawnchair's loaded model (what it draws) at that desktop cell?
    Only the launcher's own activity is dumped: `dumpsys activity top` also asks every other app's
    task, and one Android has frozen in the background (a Settings left open by an earlier test)
    answers only after a timeout, so the whole dump hits dumpsys's 10 s limit and is cut off before
    Lawnchair's part."""
    dump = d.shell("dumpsys", "activity", f"{PKG}/app.lawnchair.LawnchairLauncher")
    pat = re.compile(r"targetComponent=ComponentInfo\{" + re.escape(pkg) + r"/[^}]*\} screen=" + str(screen)
                     + r" cell\(" + f"{x},{y}" + r"\)")
    return bool(pat.search(dump))


@pytest.fixture(autouse=True)
def _hygiene(avd_hygiene):
    """Every test here runs inside the one session-wide hygiene check (tests/conftest.py)."""


def avd_hygiene_impl(tmp_path_factory):
    """Body of the session fixture `avd_hygiene` (defined once, in tests/conftest.py, so that all
    device modules share ONE instance; a fixture imported into two modules would run twice)."""
    if not SERIAL:
        yield None
        return
    from homalawn import prefs, snapshots
    from homalawn.session import PREFS_FILE, Launcher
    old_env = os.environ.get("HOMALAWN_DATA")
    os.environ["HOMALAWN_DATA"] = str(tmp_path_factory.mktemp("homalawn-data"))
    t = AdbTransport(SERIAL)
    d = t.device
    la = Launcher(t)
    la.wait_loaded()
    assert d.call("ping")["default_home"], "AVD must start with Lawnchair as the default home"
    media = t.media_access()
    layout0 = norm_layout(la.get_layout())
    wp_id = d.call("wallpaper_info")["system_id"]
    wp_snap = la.backup_wallpaper(prefix="hygiene-wallpaper-")
    snapshots.save("hygiene-start", {PREFS_FILE: __import__("json").dumps(prefs.current(t)).encode(),
                                     **la._read_all()}, device=la.device_key())
    pins0 = pins(d)
    d.shell("logcat", "-b", "crash", "-c")
    yield la
    # put everything back
    la.refresh()
    la.wait_loaded()
    la.restore("hygiene-start")
    if d.call("wallpaper_info")["system_id"] != wp_id:
        f = tmp_path_factory.mktemp("wp") / "wp.png"
        f.write_bytes(snapshots.load(wp_snap)["wallpaper.png"])
        la._push_wallpaper(str(f))
    perm, mode = media
    d.shell("pm", "grant" if perm else "revoke", PKG, "android.permission.READ_EXTERNAL_STORAGE")
    d.shell("appops", "set", "--uid", PKG, "READ_MEDIA_IMAGES", mode)
    la.wait_loaded()
    if old_env is None:
        os.environ.pop("HOMALAWN_DATA", None)
    else:
        os.environ["HOMALAWN_DATA"] = old_env
    # and prove it
    assert norm_layout(la.get_layout()) == layout0
    assert unbound_widgets(la) == []
    assert d.call("ping")["default_home"] is True
    assert t.media_access() == media
    assert crash_log(d) == ""
    assert pins(d) == pins0 == 0, "computers left pinned on the AVD"


def pins(d):
    """How many computers the phone trusts (debug hook; unpinning an unknown fp changes nothing)."""
    return d.call("debug_unpin", "0" * 64)["pins"]


@pytest.fixture
def adb_la(avd_hygiene):
    avd_hygiene.refresh()
    avd_hygiene.wait_loaded()
    return avd_hygiene


def link_transport_impl(avd_hygiene, tmp_path_factory):
    """One paired LinkTransport for the whole session: real pairing once, unpaired at the end."""
    import subprocess
    from homalawn.link import identity, pairing
    from homalawn.link.client import LinkTransport
    d = avd_hygiene.transport.device
    subprocess.run(["adb", "-s", SERIAL, "forward", f"tcp:{LINK}", f"tcp:{LINK}"], check=True, capture_output=True)
    old = os.environ.get("HOMALAWN_CONFIG")
    os.environ["HOMALAWN_CONFIG"] = str(tmp_path_factory.mktemp("link-cfg") / "config.toml")
    peer = None
    t = None
    try:
        d.call("debug_pair_reset")
        pairing_gate(d, True)  # adb forward arrives as loopback; the window stands in for "Pair a computer"
        p, words, lines = _start_pair()
        _tap_pair_action(d, "Accept")
        out = p.communicate(timeout=30)[0]
        assert p.returncode == 0, "".join(lines) + out
        peer, = identity.load_peers()
        t = LinkTransport(peer)  # the address it paired through: 127.0.0.1:47100
        t.connect()
        yield t
    finally:
        if t is not None:
            t.close()
        if peer is not None and not pairing.unpair(peer):
            d.call("debug_unpin", identity.ensure().fingerprint)
        pairing_gate(d, False)
        d.call("debug_pair_reset")
        d.shell("cmd", "statusbar", "collapse")
        if old is None:
            os.environ.pop("HOMALAWN_CONFIG", None)
        else:
            os.environ["HOMALAWN_CONFIG"] = old


@pytest.fixture(scope="session", params=["adb", "link"])
def kind(request):
    return request.param


@pytest.fixture
def la(avd_hygiene, kind, request):
    from homalawn.session import Launcher
    lau = avd_hygiene if kind == "adb" else Launcher(request.getfixturevalue("link_transport"))
    lau.refresh()
    lau.wait_loaded()
    return lau


def skip_on_link(la, reason):
    if la.transport.name == "link":
        pytest.skip(f"not over the link: {reason}")


def pid(d):
    return d.shell("pidof", PKG).strip()


def io_leftovers(d):
    return [f for f in d.shell("run-as", PKG, "ls", "files/omalawn").split() if f.startswith(("import-", "export-"))]


def free_cell(la, screen=0):
    lay = la.get_layout()
    taken = {(i["x"] + dx, i["y"] + dy) for s in lay["screens"] if s["index"] == screen for i in s["items"]
             for dx in range(i.get("w", 1)) for dy in range(i.get("h", 1))}
    cols, rows = la.grid()
    return next((x, y) for y in range(1, rows) for x in range(cols) if (x, y) not in taken)


def new_app(la):
    from homalawn import apps
    lay = la.get_layout()
    present = set()

    def walk(items):
        for i in items:
            present.add(i.get("package"))
            walk(i.get("items", []))
    walk(lay["hotseat"])
    for s in lay["screens"]:
        walk(s["items"])
    return next(a for a in apps.list_apps(la.transport) if a["package"] not in present and a["package"] != PKG)


@needs_avd
def test_real_ping_and_db_read(la):
    info = la.transport.call("launcher.ping")
    assert info["package"] == PKG and info["model_loaded"] is True
    name, data = la.transport.export_layout()
    assert name == info["db"] and data.startswith(b"SQLite format 3\x00")


@needs_avd
def test_transport_call_and_private_file_is_owner_only(la, d):
    t = la.transport
    assert t.call("launcher.ping")["package"] == PKG
    path = t.write_private("seam-mode.bin", b"secret")
    try:
        assert path.endswith("/files/omalawn/seam-mode.bin")
        ls = d.shell("run-as", PKG, "ls", "-l", path)
        assert ls.startswith("-rw-------"), ls
        assert d.run_as_read(path) == b"secret"
    finally:
        d.shell("run-as", PKG, "rm", "-f", path)


@needs_avd
def test_run_as_write_binary_roundtrip(la, d):
    """Binary bytes reach the phone unchanged: run-as over adb, a blob frame over the link."""
    import random
    rnd = random.Random(1)
    data = b"\x00\r\n\x1a\xff\r\n\x00" + bytes(rnd.randrange(256) for _ in range(64 * 1024)) + b"\r\n\x1a\xff"
    if la.transport.name == "adb":
        path = "files/omalawn-seam.bin"
        d.run_as_write(path, data)
    else:
        path = la.transport.write_private("seam-bin.bin", data)
    assert d.run_as_read(path) == data
    d.shell("run-as", PKG, "rm", "-f", path)


@needs_avd
def test_real_place_shows_and_undo(la, d):
    cell = free_cell(la)
    app = new_app(la)
    before = pid(d)
    notes = la.place(f"{app['package']}/{app['activity']}", 0, *cell)
    assert notes == []
    assert shown(d, app["package"], 0, *cell)
    BUILD.mkdir(exist_ok=True)
    (BUILD / f"seam-place-{la.transport.name}.png").write_bytes(d.screencap())
    la.undo()
    assert not shown(d, app["package"], 0, *cell)
    assert pid(d) == before  # written in process: Lawnchair was never stopped
    assert io_leftovers(d) == []
    assert d.shell("run-as", PKG, "ls", "databases").count("homalawn-tmp") == 0
    assert crash_log(d) == ""


@needs_avd
def test_slow_link_still_shows_the_new_layout(la, d, monkeypatch):
    """C3: a slow upload must not leave Lawnchair showing (or crashing on) the old DB."""
    skip_on_link(la, "the DB travels inside the import request itself; there is no gap between "
                     "staging and import to slow down")
    cell = free_cell(la)
    app = new_app(la)
    real_write = d.run_as_write

    def slow_write(path, data):
        real_write(path, data)
        time.sleep(8)  # several Tailscale round trips' worth, between staging and the import
    monkeypatch.setattr(d, "run_as_write", slow_write)
    la.place(f"{app['package']}/{app['activity']}", 0, *cell)
    monkeypatch.undo()
    assert shown(d, app["package"], 0, *cell)
    assert d.call("ping")["default_home"] is True
    assert crash_log(d) == ""
    la.undo()


@needs_avd
def test_real_widget_add_binds_and_renders(la, d):
    """Needs bind permission granted (`adb shell appwidget grantbind --package app.homalawn.debug`)."""
    from homalawn import widgets
    provs = {p["provider"]: p for p in widgets.list_providers(la.transport)}
    # a widget with no setup screen: the Google app's search widget now opens one when placed, so
    # it stays a "needs setup" placeholder and never binds by itself (2026-11-12, API 35 AVD)
    prov = "com.android.chrome/org.chromium.chrome.browser.searchwidget.SearchWidgetProvider"
    if prov not in provs:
        pytest.skip("Chrome's search widget not installed")
    lay = la.get_layout()
    cols, rows = la.grid()
    taken = {(i["x"] + dx, i["y"] + dy) for s in lay["screens"] if s["index"] == 0 for i in s["items"]
             for dx in range(i.get("w", 1)) for dy in range(i.get("h", 1))}
    y = next((y for y in range(1, rows) if all((x, y) not in taken for x in range(cols))), None)
    if y is None:
        pytest.skip("no free full row on screen 0")
    wid = widgets.add(la, prov, 0, 0, y, cols, 1, wait_s=30)
    assert wid > 0
    assert la.transport.call("launcher.widget_status", {"id": wid})["bound"] is True
    assert d.call("widget_status", str(wid))["bound"] is True
    BUILD.mkdir(exist_ok=True)
    (BUILD / f"seam-widget-{la.transport.name}.png").write_bytes(d.screencap())
    la.undo()
    assert unbound_widgets(la) == []  # the widget that was there before is still live


@needs_avd
def test_grid_change_and_restore_puts_layout_back(la, d):
    """C1 + C2: an old-style prefs snapshot (with Launcher3's migration_src_* keys) is restored
    without touching those keys, and the grid snapshot puts the exact layout back."""
    from homalawn import prefs
    before = norm_layout(la.get_layout())
    cur = prefs.current(la.transport)
    assert any(k.startswith("migration_src_") for k in cur["shared"])
    old_style = la._snapshot("prefs-", cur, with_db=False)  # what the evidence snapshot looked like
    c, r = la.grid()
    g = la.set_grid(c + 1, r + 1)
    assert la.grid() == (c + 1, r + 1)
    la.restore(old_style)  # used to rewind migration_src_* -> crash -> default layout + no default home
    assert la.grid() == (c, r)
    assert crash_log(d) == ""
    assert d.call("ping")["default_home"] is True
    la.restore(g)  # same grid as now: puts the saved DB back
    assert norm_layout(la.get_layout()) == before
    assert unbound_widgets(la) == []
    assert crash_log(d) == ""
    assert d.call("ping")["default_home"] is True


@needs_avd
def test_grid_undo_restores_layout(la, d):
    before = norm_layout(la.get_layout())
    c, r = la.grid()
    la.set_grid(c, r + 1)
    name, _ = la.undo()
    assert name.startswith("grid-") and la.grid() == (c, r)
    assert norm_layout(la.get_layout()) == before
    assert unbound_widgets(la) == [] and crash_log(d) == ""


@needs_avd
def test_lost_default_home_is_repaired(la, d):
    """I1: Android drops a crashing home app's default status; the guard puts it back."""
    skip_on_link(la, "only a device shell can make Lawnchair the default home again; over the link "
                     "homalawn says what to do on the phone instead (tests/test_transport.py, fake phone)")
    assert crash_log(d) == ""
    msgs = []

    def home_lost():
        try:
            return d.call("ping")["default_home"] is False
        except Exception:  # Lawnchair still restarting after the crash
            return False
    try:
        with la.home_guard(notify=msgs.append):
            d.shell("am", "crash", PKG)
            end = time.time() + 20
            while not home_lost() and time.time() < end:
                time.sleep(0.5)
            if not home_lost():
                # Android decides from its own crash history; crashing again to force it is
                # covered by tests/test_device_durability.py (double crash), so do not
                pytest.skip("Android kept Lawnchair as the default home after this crash; nothing to repair")
        assert d.call("ping")["default_home"] is True
        assert msgs and "Homa Lawn was set as the home app again" in msgs[0], msgs
    finally:
        # our own deliberate crash: never leave it for the hygiene fixture, even when this test fails
        d.shell("logcat", "-b", "crash", "-c")
        la.wait_loaded()
        # before Task 7, a crash closely after another one left Lawnchair answering ping but never
        # running layout work ("busy") until force-stopped (fixed: CrashExit + headless model load,
        # tested in tests/test_device_durability.py). Kept as a guard: the warning shows in the test
        # output and the AVD is put back so the session can go on.
        try:
            d.call("layout_export")
        except Exception as e:
            import warnings
            warnings.warn(f"Lawnchair stuck after the deliberate crash ({e}); force-stopped and restarted it")
            d.force_stop()
            d.start_home()
            time.sleep(8)
            la.wait_loaded()
        for f in io_leftovers(d):
            d.shell("run-as", PKG, "rm", f"files/omalawn/{f}")


@needs_avd
def test_real_wallpaper_backup_restore_keeps_lock_and_permissions(la, d):
    from homalawn import snapshots
    from homalawn.session import forget_wallpaper
    skip_on_link(la, "the temporary media-read grant needs a device shell; the link variant is "
                     "test_link_wallpaper_needs_photos_allowed_once_then_works")
    forget_wallpaper()  # this test is about reading the phone's wallpaper, not homalawn's own copy
    d.shell("pm", "revoke", PKG, "android.permission.READ_EXTERNAL_STORAGE")
    d.shell("appops", "set", "--uid", PKG, "READ_MEDIA_IMAGES", "default")
    la.wait_loaded()
    before_state = la.transport.media_access()
    before = d.call("wallpaper_info")
    assert before["live"] is False
    msgs = []
    snap = la.set_wallpaper(str(Path(__file__).parent / "fixtures" / "wp.png"), notify=msgs.append)
    assert len(msgs) == 1 and len(snapshots.load(snap)["wallpaper.png"]) > 100
    after = d.call("wallpaper_info")
    assert after["system_id"] != before["system_id"]      # home wallpaper changed
    assert lock_kept(before, after)                       # lock screen untouched
    assert la.transport.media_access() == before_state             # temporary permissions reverted
    name, _ = la.undo()
    assert name == snap
    assert lock_kept(before, d.call("wallpaper_info"))
    assert la.transport.media_access() == before_state


@needs_avd
def test_link_wallpaper_needs_photos_allowed_once_then_works(la, d):
    """Over the link nobody can grant the media-read permission for a moment, and on Android 13+
    no prompt on the phone can (Task 8): without it the backup stops with the one-line fix and no
    notification is posted; once it is allowed (here via adb, standing in for the user
    tapping Allow on the phone) backup, set and undo all run over the link."""
    from homalawn import snapshots
    from homalawn.session import MSG_MEDIA_NOT_GRANTABLE, forget_wallpaper
    from homalawn.transport import NotSupported
    if la.transport.name != "link":
        pytest.skip("link-only variant of the wallpaper test")
    forget_wallpaper()  # as if the wallpaper had been picked on the phone, not by homalawn
    d.shell("pm", "revoke", PKG, "android.permission.READ_EXTERNAL_STORAGE")  # kills Lawnchair: the link re-dials
    d.shell("appops", "set", "--uid", PKG, "READ_MEDIA_IMAGES", "default")
    la.wait_loaded()
    before = d.call("wallpaper_info")
    img = str(Path(__file__).parent / "fixtures" / "wp.png")
    with pytest.raises(NotSupported) as e:
        la.set_wallpaper(img)
    assert str(e.value) == MSG_MEDIA_NOT_GRANTABLE
    assert d.call("wallpaper_info")["system_id"] == before["system_id"]  # nothing changed
    assert "id=47102 " not in d.shell("dumpsys", "notification", "--noredact")  # nothing posted
    AdbTransport(SERIAL).allow_media_read(False, "default")  # the same two grants the adb path makes
    la.wait_loaded()
    snap = la.set_wallpaper(img)
    assert len(snapshots.load(snap)["wallpaper.png"]) > 100
    after = d.call("wallpaper_info")
    assert after["system_id"] != before["system_id"] and lock_kept(before, after)
    name, _ = la.undo()
    assert name == snap and lock_kept(before, d.call("wallpaper_info"))
    assert crash_log(d) == ""


def _solid_png(path, rgb, w=64, h=64):
    import struct
    import zlib

    def chunk(kind, data):
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data))
    rows = b"".join(b"\x00" + bytes(rgb) * w for _ in range(h))
    path.write_bytes(b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0))
                     + chunk(b"IDAT", zlib.compress(rows)) + chunk(b"IEND", b""))
    return str(path)


@needs_avd
def test_link_second_wallpaper_backs_up_the_first_without_reading_the_phone(la, d, tmp_path, monkeypatch):
    """Over the link Lawnchair may not read the wallpaper (Android 13+), but homalawn keeps the
    picture it set and the phone's wallpaper id: the second set needs no phone read and its backup
    is the first picture; undo puts the first one back the same way."""
    from homalawn import snapshots
    from homalawn.session import forget_wallpaper
    if la.transport.name != "link":
        pytest.skip("link-only: over adb the phone read is always possible")
    forget_wallpaper()

    def no_phone_read():
        raise AssertionError("the phone was asked to read its wallpaper")
    first = _solid_png(tmp_path / "first.png", (200, 30, 30))
    second = _solid_png(tmp_path / "second.png", (30, 30, 200))
    info0 = d.call("wallpaper_info")
    assert la.set_wallpaper(first, backup=False) is None
    id1 = d.call("wallpaper_info")["system_id"]
    monkeypatch.setattr(la.transport, "wallpaper_backup", no_phone_read)
    snap = la.set_wallpaper(second)
    assert snapshots.load(snap)["wallpaper.png"] == Path(first).read_bytes()
    after = d.call("wallpaper_info")
    assert after["system_id"] != id1 and lock_kept(info0, after)
    name, _ = la.undo()
    assert name == snap and d.call("wallpaper_info")["system_id"] != after["system_id"]
    assert crash_log(d) == ""
    forget_wallpaper()


@needs_avd
def test_link_undo_never_overwrites_a_wallpaper_it_cannot_save(la, d, tmp_path):
    """Final review I2: the wallpaper was changed outside this link session (here over adb), so
    the phone would have to read it, which the link cannot on Android 13+: undo stops with the
    one-line fix and changes nothing; `--no-backup` (no_backup=True) then goes ahead."""
    from homalawn import snapshots
    from homalawn.session import MSG_MEDIA_NOT_GRANTABLE, forget_wallpaper
    from homalawn.transport import NotSupported
    if la.transport.name != "link":
        pytest.skip("link-only: over adb the phone read is always possible")
    forget_wallpaper()
    first = _solid_png(tmp_path / "first.png", (20, 160, 20))
    second = _solid_png(tmp_path / "second.png", (160, 20, 160))
    outside = _solid_png(tmp_path / "outside.png", (240, 240, 20))
    assert la.set_wallpaper(first, backup=False) is None
    snap = la.set_wallpaper(second)  # backed up from homalawn's own copy of `first`
    Launcher(AdbTransport(SERIAL))._push_wallpaper(outside)  # changed outside the link's record
    d.shell("pm", "revoke", PKG, "android.permission.READ_EXTERNAL_STORAGE")  # kills Lawnchair: the link re-dials
    d.shell("appops", "set", "--uid", PKG, "READ_MEDIA_IMAGES", "default")
    la.wait_loaded()
    shown = d.call("wallpaper_info")["system_id"]
    with pytest.raises(NotSupported) as e:
        la.undo()
    assert str(e.value) == MSG_MEDIA_NOT_GRANTABLE
    assert d.call("wallpaper_info")["system_id"] == shown  # not overwritten
    assert snapshots.list_undoable(la.device_key())[0] == snap  # the undo step is still there
    name, _ = la.undo(no_backup=True)
    assert name == snap and d.call("wallpaper_info")["system_id"] != shown
    assert crash_log(d) == ""
    forget_wallpaper()


@needs_avd
def test_widget_survives_export_import_round_trip(la, d):
    from homalawn import widgets
    # the starting state has no widget (testing.md): place one that needs no setup, undone at the end
    cols, rows = la.grid()
    lay = la.get_layout()
    taken = {(i["x"] + dx, i["y"] + dy) for s in lay["screens"] if s["index"] == 0 for i in s["items"]
             for dx in range(i.get("w", 1)) for dy in range(i.get("h", 1))}
    y = next(y for y in range(1, rows) if all((x, y) not in taken for x in range(cols)))
    widgets.add(la, "com.android.chrome/org.chromium.chrome.browser.searchwidget.SearchWidgetProvider", 0, 0, y, cols, 1,
                wait_s=30)
    try:
        _widget_round_trip(la, d)
    finally:
        la.undo()


def _widget_round_trip(la, d):
    lay = la.get_layout()
    wids = [i["appWidgetId"] for s in lay["screens"] for i in s["items"] if i["type"] == "widget"]
    assert wids
    before, p = norm_layout(lay), pid(d)
    files = la._read_all()
    assert la._write_db(la._single_file(files, la.db_name()), files) == []
    after = la.get_layout()
    assert norm_layout(after) == before
    assert [i["appWidgetId"] for s in after["screens"] for i in s["items"] if i["type"] == "widget"] == wids
    assert all(la.transport.call("launcher.widget_status", {"id": w})["bound"] for w in wids)
    assert pid(d) == p and io_leftovers(d) == [] and crash_log(d) == ""


@needs_avd
def test_import_of_another_grid_is_refused_and_changes_nothing(la, d):
    import sqlite3
    from homalawn.adb import ProviderError
    before, p = norm_layout(la.get_layout()), pid(d)
    cols, _ = la.grid()
    name, data = la._export()

    def widen(path):  # what a DB from a wider grid looks like: an item past the last column
        con = sqlite3.connect(path)
        con.execute("update favorites set cellX=? where _id=(select min(_id) from favorites where container=-100)",
                    (cols,))
        con.commit()
        con.close()
    wide = la._single_file({name: data}, name, edit=widen)
    with pytest.raises(ProviderError, match="grid_mismatch"):
        la.transport.import_layout(wide)
    with pytest.raises(ProviderError, match="grid_mismatch"):  # and by name, for a DB of another grid
        la.transport.import_layout(data, db="launcher_9_9_9.db")
    assert norm_layout(la.get_layout()) == before
    assert pid(d) == p and io_leftovers(d) == [] and crash_log(d) == ""


@needs_avd
def test_cli_picks_the_link_and_says_so(link_transport):
    """`homalawn status` with no device named: the paired phone answers over the link; `--via adb`
    forces adb; a named serial (HOMALAWN_SERIAL / --serial) means adb to it, never the link."""
    r = _cli("status", unset=("HOMALAWN_SERIAL",))
    assert r.returncode == 0, r.stdout + r.stderr
    first = r.stdout.splitlines()[0]
    assert first.startswith("Phone: ") and first.endswith(", over the link (127.0.0.1)"), r.stdout
    assert "Home app: yes" in r.stdout.splitlines()
    over_adb = f"Phone: {SERIAL}, over adb ({SERIAL})"
    r = _cli("--via", "adb", "status")
    assert r.returncode == 0 and r.stdout.splitlines()[0] == over_adb, r.stdout + r.stderr
    r = _cli("status")  # HOMALAWN_SERIAL is set in this environment
    assert r.returncode == 0 and r.stdout.splitlines()[0] == over_adb, r.stdout + r.stderr
    r = _cli("--serial", SERIAL, "status", unset=("HOMALAWN_SERIAL",))
    assert r.returncode == 0 and r.stdout.splitlines()[0] == over_adb, r.stdout + r.stderr
    r = _cli("--via", "link", "apps")  # app list over the link comes from Lawnchair itself
    assert r.returncode == 0 and "com.android.settings/" in r.stdout, r.stdout + r.stderr


# ---- pairing (Task 5) ---------------------------------------------------------------------------
# The desktop reaches the phone's link through `adb forward tcp:47100 tcp:47100`. Each test uses a
# fresh desktop identity in a temp HOMALAWN_CONFIG, resets the phone's pairing rate window through
# the debug-only provider hook, and unpins that identity again at the end.

LINK = 47100
ROOT = Path(__file__).parent.parent


def _ui(d):
    """The current window hierarchy as (text, desc, (cx, cy)) tuples, via uiautomator."""
    import xml.etree.ElementTree as ET
    for _ in range(5):
        out = d.shell("uiautomator", "dump", "/sdcard/omalawn-ui.xml")
        if "dumped" in out:
            break
        time.sleep(0.5)
    xml = d.shell("cat", "/sdcard/omalawn-ui.xml")
    d.shell("rm", "-f", "/sdcard/omalawn-ui.xml")
    nodes = []
    for n in ET.fromstring(xml[xml.index("<"):]).iter("node"):
        m = re.match(r"\[(\d+),(\d+)\]\[(\d+),(\d+)\]", n.get("bounds", ""))
        if m:
            x1, y1, x2, y2 = map(int, m.groups())
            nodes.append((n.get("text", ""), n.get("content-desc", ""), ((x1 + x2) // 2, (y1 + y2) // 2)))
    return nodes


def _shade_text(d):
    return " | ".join(t for t, _, _ in _ui(d) if t)


def pairing_gate(d, on):
    """The test stand-ins for the user opening pairing on the phone: the shell-only local-pairing
    toggle (adb forward arrives as loopback) and the pairing window. Both off again with False."""
    if on:
        d.call("debug_allow_local_pairing", "on")
        d.call("debug_open_pairing")
    else:
        d.call("debug_close_pairing")
        d.call("debug_allow_local_pairing", "off")


def _tap_pair_action(d, label, timeout=20):
    """Open the notification shade, check the pairing prompt is there, and answer it: "Reject"
    taps the notification's Reject; "Accept" taps Review, which opens Lawnchair's confirmation
    screen, and then that screen's Accept (the only place a pairing can be accepted).
    Returns all text the shade showed (for the words check)."""
    end = time.time() + timeout
    action = "Review" if label == "Accept" else label
    text = ""
    while time.time() < end:
        d.shell("cmd", "statusbar", "expand-notifications")
        time.sleep(1)
        nodes = _ui(d)
        text = " | ".join(t for t, _, _ in nodes if t)
        hit = [c for t, desc, c in nodes if action.lower() in (t.lower(), desc.lower())]
        if "Pair with" in text and hit:
            d.shell("input", "tap", str(hit[0][0]), str(hit[0][1]))
            time.sleep(1)
            if label == "Accept":
                _tap_confirm(d, "Accept")
            else:
                d.shell("cmd", "statusbar", "collapse")
            return text
        time.sleep(1)
    d.shell("cmd", "statusbar", "collapse")
    raise AssertionError(f"no pairing prompt with {action!r} in the shade: {text}")


def _confirm_screen(d):
    """The confirmation screen's (text, centre) nodes when it is in front, else None."""
    nodes = _ui(d)
    if not any(t.startswith("Pair with ") and t.endswith("?") for t, _, _ in nodes):
        return None
    return nodes


def _tap_confirm(d, label, timeout=10):
    """Tap `label` (Accept / Reject) on Lawnchair's pairing confirmation screen, as a finger does."""
    end = time.time() + timeout
    while time.time() < end:
        nodes = _confirm_screen(d)
        hit = [c for t, _, c in nodes or [] if t.lower() == label.lower()]
        if hit:
            d.shell("input", "tap", str(hit[0][0]), str(hit[0][1]))
            time.sleep(1)
            return
        time.sleep(0.5)
    raise AssertionError(f"no {label!r} on the confirmation screen: {_shade_text(d)}")


def _pair_notification_posted(d):
    """Is the pairing prompt (notification id 47101) posted right now?"""
    return bool(re.search(r"NotificationRecord\([^)]*pkg=" + re.escape(PKG) + r" user=\S+ id=47101 ",
                          d.shell("dumpsys", "notification", "--noredact")))


@pytest.fixture
def link_env(adb_la, tmp_path, monkeypatch):
    import subprocess
    from homalawn.link import identity
    d = adb_la.transport.device
    subprocess.run(["adb", "-s", SERIAL, "forward", f"tcp:{LINK}", f"tcp:{LINK}"], check=True, capture_output=True)
    monkeypatch.setenv("HOMALAWN_CONFIG", str(tmp_path / "cfg" / "config.toml"))
    d.call("debug_pair_reset")
    pairing_gate(d, True)
    ident = identity.ensure()
    yield d, ident
    d.call("debug_unpin", ident.fingerprint)
    pairing_gate(d, False)
    d.call("debug_pair_reset")
    d.shell("cmd", "statusbar", "collapse")
    _go_home(d)  # every pairing test leaves the AVD on its home screen


def _top(d):
    """The activity in front, as package/class (empty when none is resumed)."""
    m = re.search(r"topResumedActivity=ActivityRecord\{\S+ \S+ (\S+)", d.shell("dumpsys", "activity", "activities"))
    return m.group(1) if m else ""


def _go_home(d, timeout=10):
    """Screen on and Lawnchair's home screen in front."""
    d.shell("input", "keyevent", "KEYCODE_WAKEUP")
    d.shell("input", "keyevent", "KEYCODE_HOME")
    end = time.time() + timeout
    while not _top(d).endswith("LawnchairLauncher") and time.time() < end:
        time.sleep(0.3)
    assert _top(d).endswith("LawnchairLauncher"), "home screen not in front: " + _top(d)
    time.sleep(0.5)


def _open_settings(d, timeout=10):
    """Another app (Settings) in front of the home screen."""
    d.shell("am", "start", "-W", "-a", "android.settings.SETTINGS")
    end = time.time() + timeout
    while (PKG in _top(d) or "settings" not in _top(d).lower()) and time.time() < end:
        time.sleep(0.3)
    assert "settings" in _top(d).lower() and PKG not in _top(d), "Settings not in front: " + _top(d)
    time.sleep(0.5)


def _ask(c, name="homalawn-test-desk", reveal=True, reopen=True):
    """Raw protocol: pair_request with a commitment, then (optionally) pair_reveal. `reopen`
    re-opens the pairing window first (it lasts 2 minutes; some tests run longer)."""
    from homalawn.link import sas
    if reopen:
        AdbTransport(SERIAL).device.call("debug_open_pairing")
    nd = os.urandom(16)
    r = c.call("link.pair_request", {"name": name, "commit": sas.commitment(nd).hex()})
    rv = c.call("link.pair_reveal", {"nonce_desktop": nd.hex()}) if reveal else None
    return r, rv, nd


def _wait_posted(d, want=True, timeout=10):
    end = time.time() + timeout
    while _pair_notification_posted(d) != want and time.time() < end:
        time.sleep(0.5)
    return _pair_notification_posted(d) == want


def _conn(ident, timeout=10):
    from homalawn.link.wire import Conn
    return Conn("127.0.0.1", LINK, str(ident.cert), str(ident.key), timeout=timeout)


def _hello_paired(ident):
    with _conn(ident) as c:
        return c.call("link.hello")["paired"]


def _cli(*args, input_=None, timeout=60, unset=()):
    """The CLI as a separate process. HOMALAWN_SERIAL stays set (so a command without --via goes to
    the AVD over adb); `unset` drops environment variables for this run."""
    import subprocess
    import sys
    gone = set(unset) | {"OMALAWN_" + k[len("HOMALAWN_"):] for k in unset if k.startswith("HOMALAWN_")}  # and the old names
    env = {k: v for k, v in os.environ.items() if k not in gone}
    return subprocess.run([sys.executable, "-m", "homalawn.cli", *args], capture_output=True, text=True,
                          input=input_, timeout=timeout, env=env)


def _start_pair(*extra):
    """`homalawn pair 127.0.0.1:47100` in the background; returns (process, words)."""
    import subprocess
    import sys
    p = subprocess.Popen([sys.executable, "-m", "homalawn.cli", "pair", f"127.0.0.1:{LINK}", "--name",
                          "homalawn-test-desk", *extra], stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                         text=True, env=os.environ.copy())
    lines = []
    while line := p.stdout.readline():  # readline, not iteration: no read-ahead lost to communicate()
        lines.append(line)
        if line.startswith("Words: "):
            lines.append(p.stdout.readline())  # "Confirm on the phone ..."
            return p, line.split()[1:], lines
    p.wait()
    raise AssertionError("pair printed no words: " + "".join(lines))


@needs_avd
def test_kotlin_words_match_the_shared_vectors(adb_la):
    import json
    d = adb_la.transport.device
    vec = json.loads((ROOT / "tests" / "vectors" / "sas.json").read_text())
    for v in vec["vectors"]:
        r = d.call("debug_sas", extras={"phone": v["phone_cert"], "desktop": v["desktop_cert"],
                                        "nonce_phone": v["nonce_phone"], "nonce_desktop": v["nonce_desktop"]})
        assert r["words"] == v["words"], v["name"]
        assert r["commit"] == v["commit"], v["name"]
        assert r["words_sha256"] == vec["wordlist_sha256"]


@needs_avd
def test_real_pairing(link_env):
    d, ident = link_env
    assert _hello_paired(ident) is False
    p, words, lines = _start_pair()
    shade = _tap_pair_action(d, "Accept")
    assert "Pair with homalawn-test-desk?" in shade and " ".join(words) in shade
    out = p.communicate(timeout=30)[0]
    assert p.returncode == 0, "".join(lines) + out
    assert "Confirm on the phone" in lines[-1] and "Paired with" in out
    peers = _cli("peers")
    with _conn(ident) as c:
        assert c.call("link.hello")["paired"] is True
        assert c.call("launcher.ping")["package"] == PKG
        assert c.peer_fp[:16] in peers.stdout
    assert not _pair_notification_posted(d)
    r = _cli("unpair")
    assert r.returncode == 0 and "forgotten each other" in r.stdout, r.stdout + r.stderr
    assert "No phone is paired yet" in _cli("phones").stdout
    from homalawn.link.wire import LinkError
    with _conn(ident) as c:
        assert c.call("link.hello")["paired"] is False
        with pytest.raises(LinkError) as e:
            c.call("launcher.ping")
        assert e.value.code == "not_paired"


@needs_avd
def test_only_a_tap_on_the_confirmation_screen_pins(link_env):
    """Final review C2: an app with notification access can fire the pairing notification's
    actions. The old Accept (a service intent) pins nothing any more; Review only opens
    Lawnchair's confirmation screen, which pins nothing by being opened; a finger on its Accept
    does (here `input tap`, like the user)."""
    d, ident = link_env
    c = _conn(ident, timeout=60)
    try:
        _ask(c)
        assert _wait_posted(d)
        assert d.call("debug_pair_legacy_accept")["sent"] is True
        time.sleep(2)
        assert _hello_paired(ident) is False and _pair_notification_posted(d)
        assert "pairing Accept sent to the service was ignored" in d.shell("logcat", "-d", "-s", "OmalawnLink")
        assert d.call("debug_pair_fire_current")["current"] == "sent"  # Review, as a listener could send it
        end = time.time() + 10
        while _confirm_screen(d) is None and time.time() < end:
            time.sleep(0.5)
        nodes = _confirm_screen(d)
        assert nodes, "Review did not open the confirmation screen: " + _shade_text(d)
        shown = " | ".join(t for t, _, _ in nodes if t)
        assert "Pair with homalawn-test-desk?" in shown and "Accept" in shown and "Reject" in shown
        time.sleep(2)
        assert _hello_paired(ident) is False  # opening it pinned nothing
        _tap_confirm(d, "Accept")
        r = c.call("link.pair_wait", {})
        assert r["accepted"] is True and r["outcome"] == "accepted", r
        assert _hello_paired(ident) is True
        assert _confirm_screen(d) is None  # it closed itself
        assert d.call("debug_link_state")["pairing_open_ms"] == 0  # one pairing per "Pair a computer"
    finally:
        c.close()


@needs_avd
def test_reject_on_the_confirmation_screen_pins_nothing(link_env):
    d, ident = link_env
    c = _conn(ident, timeout=60)
    try:
        _ask(c)
        assert _wait_posted(d)
        assert d.call("debug_pair_fire_current")["current"] == "sent"
        _tap_confirm(d, "Reject")
        assert c.call("link.pair_wait", {}) == {"accepted": False, "outcome": "rejected"}
        assert _hello_paired(ident) is False and not _pair_notification_posted(d)
    finally:
        c.close()


@needs_avd
def test_pairing_from_the_phone_itself_is_refused_without_the_debug_toggle(link_env):
    """Final review C2: adb forward (like an app on the phone) arrives as loopback; without the
    shell-only toggle such a pair request is refused before any prompt. The toggle survives a
    restart of Lawnchair (it lasts until off or reboot)."""
    from homalawn.link.wire import LinkError
    d, ident = link_env
    d.call("debug_allow_local_pairing", "off")
    try:
        with _conn(ident) as c:
            with pytest.raises(LinkError) as e:
                _ask(c, reveal=False)
            assert e.value.code == "local_pairing"
        assert not _pair_notification_posted(d)
    finally:
        d.call("debug_allow_local_pairing", "on")
    p0 = pid(d)
    d.shell("run-as", PKG, "kill", "-9", p0)
    _wait_link(ident)
    assert pid(d) not in ("", p0)
    assert d.call("debug_link_state")["local_pairing"] is True
    d.call("debug_pair_reset")
    pairing_gate(d, True)
    with _conn(ident) as c:  # and with it, the request is taken
        _ask(c)
        assert _wait_posted(d)
    adb_la_wait(d)


def adb_la_wait(d):
    Launcher(AdbTransport(SERIAL)).wait_loaded()


WAITING = "Waiting for a computer…"


def _waiting_screen(d):
    """The pairing screen's nodes while it waits for a computer, else None."""
    nodes = _ui(d)
    return nodes if any(t == WAITING for t, _, _ in nodes) else None


def _tap_pair_a_computer(d, timeout=30):
    """The user's way in: open Homa Lawn's settings (its "Homa Lawn" icon) and tap "Pair a
    computer"; returns the screen's nodes once the pairing screen is in front (waiting, or already
    showing a computer's request)."""
    from homalawn import appid
    end = time.time() + timeout
    while time.time() < end:
        # a fresh settings task at its dashboard: a plain start resumes the screen an earlier test
        # left on top (e.g. Wallpaper & style), where there is no Computers row (NEW_TASK|CLEAR_TASK)
        d.shell("am", "start", "-f", "0x10008000", "-n", f"{PKG}/{appid.SETTINGS_ALIAS}")
        time.sleep(1.5)
        for row in ("computers", "pair a computer"):   # the settings row, then the Computers screen's row
            hit = [c for t, desc, c in _ui(d) if row in (t.lower(), desc.lower())]
            if hit:  # the dashboard's banners can still move the rows: tap where it stays put
                time.sleep(0.7)
                again = [c for t, desc, c in _ui(d) if row in (t.lower(), desc.lower())]
                hit = again if again == hit else []
            if not hit:
                break
            d.shell("input", "tap", str(hit[0][0]), str(hit[0][1]))
            time.sleep(1.5)
        else:
            nodes = _waiting_screen(d) or _confirm_screen(d)
            if nodes:
                return nodes
    raise AssertionError("Pair a computer did not open the pairing screen: " + _shade_text(d))


def _pair_screens(d):
    """How many pairing screens the activity stack holds."""
    out = d.shell("dumpsys", "activity", "activities")
    # one record appears in several lists (history, resumed, focused…): count distinct ones
    return len(set(re.findall(r"ActivityRecord\{(\w+) [^}]*PairConfirmActivity", out)))


def _start_pair_waiting(*extra):
    """`homalawn pair 127.0.0.1:47100 --name homalawn-test-desk …` in the background, its output
    collected line by line by a thread (it waits while pairing is closed on the phone)."""
    import subprocess
    import sys
    import threading
    p = subprocess.Popen([sys.executable, "-m", "homalawn.cli", "pair", f"127.0.0.1:{LINK}", "--name",
                          "homalawn-test-desk", *extra], stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                         text=True, env=os.environ.copy())
    lines = []
    threading.Thread(target=lambda: [lines.append(l) for l in iter(p.stdout.readline, "")], daemon=True).start()
    return p, lines


def _wait_line(lines, prefix, timeout=30):
    end = time.time() + timeout
    while time.time() < end:
        hit = [l for l in list(lines) if l.startswith(prefix)]
        if hit:
            return hit[0]
        time.sleep(0.3)
    raise AssertionError(f"no line starting {prefix!r}: " + "".join(lines))


@needs_avd
def test_pair_a_computer_opens_one_screen_where_the_words_appear_and_accept_pairs(link_env):
    """The owner's flow (2026-10-12, after a real phone grouped the prompt out of sight): pairing
    is closed, `homalawn pair` says how to open it and keeps asking; "Pair a computer" in
    Homa Lawn's settings opens Lawnchair's pairing screen ("Waiting for a computer…"), tapping it
    again only brings that one screen forward; the computer's request then shows on that same
    screen (no notification), Accept there pairs, and a link call works."""
    from homalawn.link.wire import LinkError
    d, ident = link_env
    d.call("debug_close_pairing")
    _open_settings(d)  # the home screen showing would take the request by itself
    with _conn(ident) as c:  # closed: refused with no prompt
        with pytest.raises(LinkError) as e:
            _ask(c, reveal=False, reopen=False)
        assert e.value.code == "pairing_closed"
    assert not _pair_notification_posted(d)
    d.shell("logcat", "-c")
    p, lines = _start_pair_waiting()
    try:
        _wait_line(lines, "On the phone: unlock it and show its home screen (or open Homa Lawn's settings › "
                          "Computers › Pair a computer)")
        _tap_pair_a_computer(d)  # the computer asks again within 2 s: the words may already be there
        words = _wait_line(lines, "Words: ").split()[1:]
        end = time.time() + 15
        while (nodes := _confirm_screen(d)) is None and time.time() < end:
            time.sleep(0.5)
        assert nodes, "the words did not appear on the pairing screen: " + _shade_text(d)
        shown = " | ".join(t for t, _, _ in nodes if t)
        assert "Pair with homalawn-test-desk?" in shown and " ".join(words) in shown, shown
        assert not _pair_notification_posted(d)  # the screen shows it; nothing grouped out of sight
        assert _hello_paired(ident) is False  # showing it pinned nothing
        time.sleep(1.2)  # Accept is armed 1 s after the words appear
        _tap_confirm(d, "Accept")
        out = p.communicate(timeout=30)[0]
        assert p.returncode == 0 and "Paired with" in "".join(lines) + out, "".join(lines) + out
    finally:
        if p.poll() is None:
            p.kill()
    with _conn(ident) as c:
        assert c.call("link.hello")["paired"] is True
        assert c.call("launcher.ping")["package"] == PKG
    assert _waiting_screen(d) is None and _confirm_screen(d) is None and _pair_screens(d) == 0
    assert d.call("debug_link_state")["pairing_open_ms"] == 0
    log = d.shell("logcat", "-d", "-s", "OmalawnLink")
    assert log.count("pairing opened on the pairing screen") == 1, log


@needs_avd
def test_home_screen_showing_takes_a_pair_request_and_shows_it_in_front(link_env):
    """Flux-style (spec N5): with Lawnchair's home screen in front and the phone unlocked, a
    computer's request is taken without "Pair a computer": the pairing screen comes up in front
    at once with the name and the words (no notification, the pairing window stays closed), and a
    finger on Accept pairs."""
    d, ident = link_env
    d.call("debug_close_pairing")
    _go_home(d)
    p, lines = _start_pair_waiting()
    try:
        words = _wait_line(lines, "Words: ", timeout=20).split()[1:]
        end = time.time() + 15
        while (nodes := _confirm_screen(d)) is None and time.time() < end:
            time.sleep(0.5)
        assert nodes, "the pairing screen did not come up: " + _shade_text(d)
        shown = " | ".join(t for t, _, _ in nodes if t)
        assert "Pair with homalawn-test-desk?" in shown and " ".join(words) in shown, shown
        assert not any("show its home screen" in l for l in lines), "".join(lines)
        assert not _pair_notification_posted(d)
        assert d.call("debug_link_state")["pairing_open_ms"] == 0  # the window was never opened
        assert _hello_paired(ident) is False  # showing it pinned nothing
        time.sleep(1.2)  # Accept is armed 1 s after the words appear
        _tap_confirm(d, "Accept")
        out = p.communicate(timeout=30)[0]
        assert p.returncode == 0 and "Paired with" in "".join(lines) + out, "".join(lines) + out
    finally:
        if p.poll() is None:
            p.kill()
    assert _hello_paired(ident) is True
    assert _confirm_screen(d) is None and _pair_screens(d) == 0


@needs_avd
def test_leaving_the_home_screen_request_falls_back_to_the_notification(link_env):
    d, ident = link_env
    d.call("debug_close_pairing")
    _go_home(d)
    c = _conn(ident, timeout=60)
    try:
        _ask(c, reopen=False)
        end = time.time() + 15
        while _confirm_screen(d) is None and time.time() < end:
            time.sleep(0.5)
        assert _confirm_screen(d) and not _pair_notification_posted(d)
        d.shell("input", "keyevent", "KEYCODE_BACK")
        assert _wait_posted(d)  # still open: Review / Reject in the notification
        _tap_pair_action(d, "Reject")
        assert c.call("link.pair_wait", {}) == {"accepted": False, "outcome": "rejected"}
        assert _hello_paired(ident) is False
    finally:
        c.close()


@needs_avd
def test_another_app_in_front_refuses_a_pair_request(link_env):
    from homalawn.link.wire import LinkError
    d, ident = link_env
    d.call("debug_close_pairing")
    _open_settings(d)
    with _conn(ident) as c:
        with pytest.raises(LinkError) as e:
            _ask(c, reveal=False, reopen=False)
    assert e.value.code == "pairing_closed"
    assert "open the home screen on the phone (or Computers › Pair a computer in Homa Lawn's settings)" in str(e.value), str(e.value)
    assert not _pair_notification_posted(d) and _confirm_screen(d) is None


@needs_avd
def test_home_screen_path_still_refuses_a_pair_request_from_the_phone_itself(link_env):
    """Final review T: the home-screen path only changes WHEN a request is admitted, never WHO may
    ask. With the debug local-pairing hook off, Lawnchair's home screen in front and the phone
    unlocked, a request from the phone's own address (adb forward arrives as loopback, as an app
    on the phone would) is refused as local_pairing: no pairing screen, no notification."""
    from homalawn.link.wire import LinkError
    d, ident = link_env
    d.call("debug_close_pairing")
    d.call("debug_allow_local_pairing", "off")
    try:
        _go_home(d)
        assert "isKeyguardShowing=false" in d.shell("dumpsys", "window")
        assert d.call("debug_link_state")["local_pairing"] is False
        with _conn(ident) as c:
            with pytest.raises(LinkError) as e:
                _ask(c, reveal=False, reopen=False)
        assert e.value.code == "local_pairing"
        time.sleep(1.5)
        assert _confirm_screen(d) is None and _pair_screens(d) == 0
        assert not _pair_notification_posted(d)
        assert _top(d).endswith("LawnchairLauncher")
        assert _hello_paired(ident) is False
    finally:
        d.call("debug_allow_local_pairing", "on")


@needs_avd
def test_a_locked_phone_refuses_a_pair_request(link_env):
    """Locked (the lock screen showing, screen on): refused even though the home screen is behind
    it. The AVD has no lock screen by default; a swipe lock is switched on for the test."""
    from homalawn.link.wire import LinkError
    d, ident = link_env
    d.call("debug_close_pairing")
    _go_home(d)
    d.shell("locksettings", "set-disabled", "false")
    try:
        d.shell("input", "keyevent", "26")  # screen off: the lock screen comes on
        time.sleep(2)
        d.shell("input", "keyevent", "KEYCODE_WAKEUP")
        time.sleep(1.5)
        assert "isKeyguardShowing=true" in d.shell("dumpsys", "window"), "the lock screen did not show"
        with _conn(ident) as c:
            with pytest.raises(LinkError) as e:
                _ask(c, reveal=False, reopen=False)
        assert e.value.code == "pairing_closed"
        assert not _pair_notification_posted(d)
    finally:
        d.shell("locksettings", "set-disabled", "true")
        d.shell("wm", "dismiss-keyguard")
        time.sleep(1)
        _go_home(d)
    assert "isKeyguardShowing=false" in d.shell("dumpsys", "window")


@needs_avd
def test_pair_a_computer_again_from_settings_leaves_one_screen(link_env):
    """Going back to the settings to tap Pair a computer again leaves the pairing screen (which
    closes pairing, as leaving it always does) and opens a fresh one: never two screens, and
    pairing is open again while the new one shows."""
    d, ident = link_env
    d.call("debug_close_pairing")
    d.shell("logcat", "-c")
    nodes = _tap_pair_a_computer(d)
    assert any(t == WAITING for t, _, _ in nodes)
    assert any(t == "On your computer open Homa Lawn › Add phone, or run homalawn pair." for t, _, _ in nodes)
    assert any(t == "This screen waits 5 minutes." for t, _, _ in nodes)   # PairingGate.SCREEN_WINDOW_MS
    assert any(t == "Cancel" for t, _, _ in nodes)
    left = d.call("debug_link_state")["pairing_open_ms"]
    assert 280_000 < left <= 300_000, left  # open while the screen shows, at most 5 minutes
    _tap_pair_a_computer(d)
    _tap_pair_a_computer(d)
    assert _pair_screens(d) == 1
    assert d.call("debug_link_state")["pairing_open_ms"] > 280_000
    log = d.shell("logcat", "-d", "-s", "OmalawnLink")
    assert log.count("pairing opened on the pairing screen") == 3, log
    assert log.count("pairing closed: the pairing screen was left") == 2, log
    cancel, = [c for t, _, c in _waiting_screen(d) if t == "Cancel"]
    d.shell("input", "tap", str(cancel[0]), str(cancel[1]))
    time.sleep(1)
    assert _waiting_screen(d) is None and _pair_screens(d) == 0
    assert d.call("debug_link_state")["pairing_open_ms"] == 0  # Cancel closes pairing


@needs_avd
def test_leaving_the_screen_with_a_request_open_falls_back_to_the_notification(link_env):
    d, ident = link_env
    d.call("debug_close_pairing")
    _tap_pair_a_computer(d)
    c = _conn(ident, timeout=60)
    try:
        _ask(c, reopen=False)
        end = time.time() + 15
        while _confirm_screen(d) is None and time.time() < end:
            time.sleep(0.5)
        assert _confirm_screen(d) and not _pair_notification_posted(d)
        d.shell("input", "keyevent", "KEYCODE_HOME")
        assert _wait_posted(d)  # the request is still open: Review / Reject in the notification
        assert d.call("debug_link_state")["pairing_open_ms"] == 0  # leaving closed pairing
        _tap_pair_action(d, "Reject")
        assert c.call("link.pair_wait", {}) == {"accepted": False, "outcome": "rejected"}
        assert _hello_paired(ident) is False
    finally:
        c.close()


@needs_avd
def test_reject_pins_nothing(link_env):
    d, ident = link_env
    p, words, lines = _start_pair()
    _tap_pair_action(d, "Reject")
    out = p.communicate(timeout=30)[0]
    assert p.returncode == 1 and "Reject was tapped" in out, out
    assert "No phone is paired yet" in _cli("phones").stdout
    assert _hello_paired(ident) is False
    assert not _pair_notification_posted(d)


@needs_avd
def test_accepted_connection_is_promoted_and_outlives_the_unpaired_lifetime(link_env):
    """Raw protocol: the connection that paired keeps working as a paired one past 30 s."""
    d, ident = link_env
    c = _conn(ident, timeout=150)
    try:
        from homalawn.link import sas
        t0 = time.time()
        r, rv, nd = _ask(c)
        assert r["expires_in"] == 120 and "words" not in r  # no words before the nonce is revealed
        # the phone's words (Kotlin, real certificates) equal the desktop's own derivation
        assert rv["words"] == sas.sas(c.peer_der, ident.cert_der, bytes.fromhex(r["nonce_phone"]), nd)
        _tap_pair_action(d, "Accept")
        r = c.call("link.pair_wait", {})
        assert r["accepted"] is True and r["outcome"] == "accepted" and r["name"] and r["device"], r
        while time.time() - t0 < 35:
            assert c.call("link.ping")["t"] > 0
            time.sleep(5)
        assert c.call("launcher.ping")["package"] == PKG
    finally:
        c.close()


@needs_avd
@pytest.mark.slow
def test_pending_pairing_survives_strangers_and_times_out_after_120s(link_env, tmp_path):
    """While a request waits: strangers cannot evict it, a second request is refused, the 30 s
    unpaired lifetime does not apply; after 120 s it ends as timeout, nothing pinned, closed."""
    from homalawn.link import identity
    from homalawn.link.wire import Conn, LinkError
    d, ident = link_env
    c = _conn(ident, timeout=150)
    strangers = []
    try:
        t0 = time.time()
        _ask(c)
        assert _wait_posted(d)
        # other computers: three unpaired connections (only two unpaired slots) and a rival request
        other = tmp_path / "other"
        os.environ["HOMALAWN_CONFIG"] = str(other / "config.toml")
        try:
            o = identity.ensure()
        finally:
            os.environ["HOMALAWN_CONFIG"] = str(tmp_path / "cfg" / "config.toml")
        for _ in range(3):
            s = Conn("127.0.0.1", LINK, str(o.cert), str(o.key), timeout=10)
            s.call("link.hello")
            strangers.append(s)
        # only two unpaired slots: the oldest stranger was evicted, not the pending pairing
        with pytest.raises((ConnectionError, OSError)):
            strangers[0].call("link.hello")
        rival = Conn("127.0.0.1", LINK, str(o.cert), str(o.key), timeout=10)
        strangers.append(rival)
        with pytest.raises(LinkError) as e:
            _ask(rival, name="rival")
        assert e.value.code == "pair_busy"
        r = c.call("link.pair_wait", {})
        took = time.time() - t0
        assert r == {"accepted": False, "outcome": "timeout"}
        assert 118 <= took <= 130, took
        with pytest.raises((ConnectionError, OSError)):
            c.call("link.hello")
        assert _hello_paired(ident) is False
        assert not _pair_notification_posted(d)
    finally:
        c.close()
        for s in strangers:
            s.close()


@needs_avd
def test_three_requests_per_ten_minutes(link_env):
    from homalawn.link.wire import LinkError
    d, ident = link_env
    for _ in range(3):  # each withdrawn by hanging up before pair_wait
        with _conn(ident) as c:
            _ask(c)
            assert _wait_posted(d)
        assert _wait_posted(d, False), "hanging up did not withdraw the prompt"
    with _conn(ident) as c:
        with pytest.raises(LinkError) as e:
            _ask(c, reveal=False)
        assert e.value.code == "rate_limited" and "minute" in e.value.message
    with _conn(ident) as c:  # bad params are refused before anything counts
        with pytest.raises(LinkError) as e:
            c.call("link.pair_request", {"name": "x", "commit": "zz"})
        assert e.value.code == "bad_params"


def _notify_perm(d):
    m = re.search(r"android\.permission\.POST_NOTIFICATIONS: granted=(\w+), flags=\[([^\]]*)\]",
                  d.shell("dumpsys", "package", PKG))
    return m.group(1) == "true", set(m.group(2).split("|")) - {""} if m else set()


def _wait_link(ident, timeout=30):
    end = time.time() + timeout
    while True:
        try:
            return _hello_paired(ident)
        except (OSError, ConnectionError):
            if time.time() > end:
                raise
            time.sleep(1)


@needs_avd
def test_pair_request_with_notifications_off_is_refused_and_the_phone_asks(link_env, adb_la):
    """No invisible waiting: the request is refused with notifications_off, and the next time the
    home screen opens the phone asks for the permission (tapped Allow here, which also restores
    the AVD's starting state)."""
    from homalawn.link.wire import LinkError
    d, ident = link_env
    granted0, flags0 = _notify_perm(d)
    assert granted0, "AVD must start with Homa Lawn notifications allowed"
    d.shell("pm", "revoke", PKG, "android.permission.POST_NOTIFICATIONS")  # kills the app
    try:
        _wait_link(ident)
        with _conn(ident) as c:
            with pytest.raises(LinkError) as e:
                _ask(c, reveal=False)
            assert e.value.code == "notifications_off"
        assert not _pair_notification_posted(d)
        # the user comes back to the home screen: the permission dialog shows; Allow
        d.shell("am", "start", "-W", "-a", "android.settings.SETTINGS")
        d.shell("input", "keyevent", "KEYCODE_HOME")
        end = time.time() + 20
        while time.time() < end:
            hit = [c for t, _, c in _ui(d) if t.lower() == "allow"]
            if hit:
                d.shell("input", "tap", str(hit[0][0]), str(hit[0][1]))
                break
            time.sleep(1)
        else:
            raise AssertionError("the home screen did not ask for the notification permission")
        time.sleep(1)
        assert _notify_perm(d)[0] is True
        with _conn(ident) as c:  # now the request is shown (and withdrawn by hanging up)
            _ask(c)
            assert _wait_posted(d)
    finally:
        if not _notify_perm(d)[0]:
            d.shell("pm", "grant", PKG, "android.permission.POST_NOTIFICATIONS")
        missing = flags0 - _notify_perm(d)[1]
        if missing:
            d.shell("pm", "set-permission-flags", PKG, "android.permission.POST_NOTIFICATIONS",
                    *[f.lower().replace("_", "-") for f in missing])
        adb_la.wait_loaded()
    assert _notify_perm(d) == (True, flags0)


@needs_avd
def test_stale_accept_from_an_ended_request_does_nothing(link_env):
    """Request A ends; request B is shown; A's Accept action (kept by the debug build) is fired:
    it is dead, B stays pending, nothing is pinned. B's prompt waits out the cool-down after A."""
    d, ident = link_env
    with _conn(ident) as a:
        _ask(a)
        assert _wait_posted(d)
    assert _wait_posted(d, False)
    b = _conn(ident, timeout=30)
    try:
        t0 = time.time()
        _ask(b)
        assert not _pair_notification_posted(d)  # within the 3 s cool-down after A ended
        assert _wait_posted(d, timeout=8)
        assert d.call("debug_pair_fire_stale")["stale"] == "canceled"
        time.sleep(2)
        assert _pair_notification_posted(d)  # B is still waiting
        assert _hello_paired(ident) is False
    finally:
        b.close()
    assert _wait_posted(d, False)
    assert _hello_paired(ident) is False


@needs_avd
def test_a_reveal_that_breaks_the_commitment_ends_the_request(link_env):
    from homalawn.link import sas
    from homalawn.link.wire import LinkError
    d, ident = link_env
    with _conn(ident) as c:
        c.call("link.pair_request", {"name": "homalawn-test-desk", "commit": sas.commitment(os.urandom(16)).hex()})
        with pytest.raises(LinkError) as e:
            c.call("link.pair_reveal", {"nonce_desktop": os.urandom(16).hex()})
        assert e.value.code == "bad_commit"
        with pytest.raises((ConnectionError, OSError)):
            c.call("link.hello")
    assert not _pair_notification_posted(d)
    with _conn(ident) as c:  # the slot is free again
        _ask(c)
        assert _wait_posted(d)


@needs_avd
def test_the_name_shown_has_no_control_or_bidi_characters(link_env):
    d, ident = link_env
    with _conn(ident) as c:
        r, _, _ = _ask(c, name="\u202eevil\u2066-desk\u2069\u200b\x07\t", reveal=False)
        assert r["name"] == "evil-desk"


@needs_avd
def test_a_computer_that_hangs_up_during_pair_wait_gets_nothing_pinned(link_env):
    """Ctrl-C while waiting for the tap: the phone notices and an Accept afterwards pins nothing."""
    import json
    d, ident = link_env
    c = _conn(ident, timeout=30)
    _ask(c)
    assert _wait_posted(d)
    c.sock.sendall(json.dumps({"id": 99, "method": "link.pair_wait", "params": {}}).encode() + b"\n")
    time.sleep(1)
    c.close()
    assert _wait_posted(d, False, timeout=5), "the phone did not notice the computer left"
    assert d.call("debug_pair_fire_current")["current"] == "none"  # no request left to accept
    assert d.call("debug_pair_fire_stale")["stale"] == "canceled"
    assert _hello_paired(ident) is False
    log = d.shell("logcat", "-d", "-s", "OmalawnLink")
    assert "ended: gone" in log


@needs_avd
def test_a_prompt_left_by_a_dead_process_is_removed_at_start(link_env, adb_la):
    """The launcher process dies with a prompt showing (no clean shutdown); the restarted link
    service removes the dead prompt."""
    d, ident = link_env
    c = _conn(ident, timeout=30)
    try:
        _ask(c)
        assert _wait_posted(d)
        pid = d.shell("pidof", PKG).strip()
        d.shell("run-as", PKG, "kill", "-9", pid)
    finally:
        c.close()
    end = time.time() + 30
    while time.time() < end:
        try:
            _hello_paired(ident)
            break
        except (OSError, ConnectionError):
            time.sleep(1)
    assert d.shell("pidof", PKG).strip() not in ("", pid)
    assert _wait_posted(d, False, timeout=10), "a dead request's prompt is still showing"
    adb_la.wait_loaded()


@needs_avd
def test_real_lock_wallpaper_set_and_undo(la, d, tmp_path):
    """`wallpaper --lock --crop center` changes the lock screen only (home keeps its id); undo puts
    the lock screen back as it was (its own picture, or showing the home one). `--both` changes
    both in one snapshot and one undo."""
    from homalawn import snapshots
    from homalawn.session import LOCK_FOLLOWS_HOME, LOCK_WALLPAPER_FILE, WALLPAPER_FILE, forget_wallpaper
    forget_wallpaper()  # back up what the phone shows, not homalawn's own copy
    if la.transport.name == "link":
        AdbTransport(SERIAL).allow_media_read(False, "default")  # stands in for the user allowing photos once
        la.wait_loaded()
    before = d.call("wallpaper_info")
    assert 0 < before["width"] <= before["height"]
    assert la.screen_size() == (before["width"], before["height"])
    img = _solid_png(tmp_path / "wide.png", (20, 120, 200), w=384, h=216)
    snap = la.set_wallpaper(img, target="lock", size=la.screen_size())
    assert set(snapshots.load(snap)) == ({LOCK_FOLLOWS_HOME} if before["lock_follows_home"] else {LOCK_WALLPAPER_FILE})
    after = d.call("wallpaper_info")
    assert after["lock_id"] >= 0 and after["lock_id"] != before["lock_id"]  # the lock screen changed
    assert after["system_id"] == before["system_id"]                        # the home screen did not
    assert la.undo()[0] == snap
    back = d.call("wallpaper_info")
    assert back["lock_follows_home"] == before["lock_follows_home"]
    if not before["lock_follows_home"]:
        assert back["system_id"] == before["system_id"] and back["lock_id"] != after["lock_id"]

    snap = la.set_wallpaper(img, target="both", size=la.screen_size())
    held = snapshots.load(snap)
    assert WALLPAPER_FILE in held and (LOCK_FOLLOWS_HOME in held or LOCK_WALLPAPER_FILE in held)
    mid = d.call("wallpaper_info")
    assert mid["lock_follows_home"] and mid["system_id"] != back["system_id"]  # one picture on both
    assert la.undo()[0] == snap
    end = d.call("wallpaper_info")
    assert end["lock_follows_home"] == before["lock_follows_home"]
    assert crash_log(d) == ""
