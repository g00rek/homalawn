"""The lock screen wallpaper (`wallpaper --lock / --both`, `--crop center`): backup, set, undo."""
import io
import json

import pytest
from PIL import Image

from homalawn import ops, snapshots
from homalawn.adb import AdbTransport
from homalawn.session import (LOCK_FOLLOWS_HOME, LOCK_WALLPAPER_FILE, MSG_LOCK_NEEDS_UPDATE, WALLPAPER_FILE,
                             WALLPAPER_RECORD, Launcher)
from homalawn.transport import NotSupported, ProviderError
from test_session import FakeDevice
from test_transport import _img, _phone_cannot_read_wallpaper, linked  # noqa: F401 (fixture)


def _sets(phone):
    return [p.get("which", "home") for _, m, p, _ in phone.log if m == "launcher.wallpaper_set"]


def test_lock_sets_only_the_lock_screen_and_undo_puts_its_picture_back(linked, tmp_path):
    t, dev, phone = linked
    home_id, home_now = dev.wallpaper_id, dev.wallpaper_now
    la = Launcher(t)
    snap = la.set_wallpaper(_img(tmp_path, "a.png", b"\x89PNG new lock"), target="lock")
    assert snapshots.load(snap) == {LOCK_WALLPAPER_FILE: b"\x89PNG lock"}  # the lock picture only
    assert _sets(phone) == ["lock"]
    assert dev.lock_now == b"\x89PNG new lock"
    assert (dev.wallpaper_id, dev.wallpaper_now) == (home_id, home_now)  # home untouched
    name, _ = la.undo()
    assert name == snap and dev.lock_now == b"\x89PNG lock"
    assert (dev.wallpaper_id, dev.wallpaper_now) == (home_id, home_now)
    assert _sets(phone)[-1] == "lock"


def test_lock_that_showed_the_home_picture_goes_back_to_showing_it(linked, tmp_path):
    t, dev, phone = linked
    dev.lock_id, dev.lock_now = -1, None
    la = Launcher(t)
    snap = la.set_wallpaper(_img(tmp_path, "a.png", b"\x89PNG new lock"), target="lock")
    assert snapshots.load(snap) == {LOCK_FOLLOWS_HOME: b""}
    assert "launcher.wallpaper_backup" not in phone.methods()  # nothing to read: there is no lock picture
    la.undo()
    assert dev.lock_id < 0 and phone.methods().count("launcher.wallpaper_lock_reset") == 1
    assert _sets(phone) == ["lock"]  # the undo did not send a picture


def test_both_keeps_both_in_one_snapshot_and_undo_restores_each(linked, tmp_path):
    t, dev, phone = linked
    la = Launcher(t)
    snap = la.set_wallpaper(_img(tmp_path, "a.png", b"\x89PNG both"), target="both")
    assert snapshots.load(snap) == {WALLPAPER_FILE: b"\x89PNG wallpaper", LOCK_WALLPAPER_FILE: b"\x89PNG lock"}
    assert _sets(phone) == ["both"]
    assert dev.wallpaper_now == b"\x89PNG both" and dev.lock_id < 0  # one picture on both screens
    assert snapshots.list_undoable(la.device_key()) == [snap]  # one change, one undo step
    la.undo()
    assert dev.wallpaper_now == b"\x89PNG wallpaper" and dev.lock_now == b"\x89PNG lock"
    assert _sets(phone)[1:] == ["home", "lock"]  # home first, then the lock screen's own picture


def test_both_when_the_lock_showed_home_restores_that_too(linked, tmp_path):
    t, dev, phone = linked
    dev.lock_id, dev.lock_now = -1, None
    la = Launcher(t)
    snap = la.set_wallpaper(_img(tmp_path, "a.png", b"\x89PNG both"), target="both")
    assert snapshots.load(snap) == {WALLPAPER_FILE: b"\x89PNG wallpaper", LOCK_FOLLOWS_HOME: b""}
    la.undo()
    assert dev.wallpaper_now == b"\x89PNG wallpaper" and dev.lock_id < 0
    assert "launcher.wallpaper_lock_reset" in phone.methods()


def test_older_phone_build_is_refused_before_anything_changes(linked, tmp_path):
    t, dev, phone = linked
    dev.old_build = True  # it would ignore `which` and put the picture on the home screen
    la = Launcher(t)
    for target in ("lock", "both"):
        with pytest.raises(NotSupported) as e:
            la.set_wallpaper(_img(tmp_path, "a.png", b"\x89PNG x"), target=target)
        assert str(e.value) == MSG_LOCK_NEEDS_UPDATE
    assert _sets(phone) == [] and snapshots.list_undoable(la.device_key()) == []


def test_restoring_a_lock_snapshot_on_an_older_build_is_refused(linked, tmp_path):
    t, dev, phone = linked
    la = Launcher(t)
    snap = la.set_wallpaper(_img(tmp_path, "a.png", b"\x89PNG new lock"), target="lock")
    dev.old_build = True
    with pytest.raises(NotSupported):
        la.restore(snap)
    assert dev.lock_now == b"\x89PNG new lock" and _sets(phone) == ["lock"]


def test_second_lock_change_backs_up_the_first_without_reading_the_phone(linked, tmp_path):
    t, dev, phone = linked
    _phone_cannot_read_wallpaper(phone)
    phone.lock_wallpaper = phone.wallpaper  # the lock screen cannot be read either
    la = Launcher(t)
    la.set_wallpaper(_img(tmp_path, "a.png", b"\x89PNG lock 1"), backup=False, target="lock")
    rec = json.loads((snapshots.root().parent / WALLPAPER_RECORD).read_text())
    assert rec["lock_id"] == dev.lock_id and rec["lock_snapshot"] == "current-lock-wallpaper"
    snap = la.set_wallpaper(_img(tmp_path, "b.png", b"\x89PNG lock 2"), target="lock")
    assert snapshots.load(snap) == {LOCK_WALLPAPER_FILE: b"\x89PNG lock 1"}
    assert "launcher.wallpaper_backup" not in phone.methods()


def test_home_and_lock_records_are_kept_apart(linked, tmp_path):
    t, dev, phone = linked
    _phone_cannot_read_wallpaper(phone)
    phone.lock_wallpaper = phone.wallpaper
    la = Launcher(t)
    la.set_wallpaper(_img(tmp_path, "h.png", b"\x89PNG home 1"), backup=False)
    la.set_wallpaper(_img(tmp_path, "l.png", b"\x89PNG lock 1"), backup=False, target="lock")
    snap = la.set_wallpaper(_img(tmp_path, "h2.png", b"\x89PNG home 2"))  # home held survived the lock change
    assert snapshots.load(snap) == {WALLPAPER_FILE: b"\x89PNG home 1"}
    assert dev.lock_now == b"\x89PNG lock 1"
    snap = la.set_wallpaper(_img(tmp_path, "l2.png", b"\x89PNG lock 2"), target="lock")
    assert snapshots.load(snap) == {LOCK_WALLPAPER_FILE: b"\x89PNG lock 1"}
    assert "launcher.wallpaper_backup" not in phone.methods()


def test_home_change_moves_the_held_picture_to_the_lock_screen(linked, tmp_path):
    """Android leaves the old home picture on a lock screen that showed it: homalawn still knows it."""
    t, dev, phone = linked
    _phone_cannot_read_wallpaper(phone)
    phone.lock_wallpaper = phone.wallpaper
    dev.lock_id, dev.lock_now = -1, None
    la = Launcher(t)
    la.set_wallpaper(_img(tmp_path, "a.png", b"\x89PNG one"), backup=False, target="both")
    la.set_wallpaper(_img(tmp_path, "b.png", b"\x89PNG two"), backup=False)  # home only
    assert dev.lock_now == b"\x89PNG one"
    snap = la.set_wallpaper(_img(tmp_path, "c.png", b"\x89PNG three"), target="lock")
    assert snapshots.load(snap) == {LOCK_WALLPAPER_FILE: b"\x89PNG one"}
    assert "launcher.wallpaper_backup" not in phone.methods()


def test_lock_reset_keeps_the_home_picture_known(linked, tmp_path):
    """Android re-numbers the home wallpaper when the lock screen goes back to it."""
    t, dev, phone = linked
    _phone_cannot_read_wallpaper(phone)
    dev.lock_id, dev.lock_now = -1, None
    la = Launcher(t)
    la.set_wallpaper(_img(tmp_path, "a.png", b"\x89PNG home"), backup=False, target="both")
    la.set_wallpaper(_img(tmp_path, "b.png", b"\x89PNG lock"), target="lock")
    la.undo()  # the lock screen shows the home picture again; home id changed
    snap = la.set_wallpaper(_img(tmp_path, "c.png", b"\x89PNG next"))
    assert snapshots.load(snap) == {WALLPAPER_FILE: b"\x89PNG home"}
    assert "launcher.wallpaper_backup" not in phone.methods()


def test_crop_sends_a_picture_of_the_screen_size(linked, tmp_path):
    t, dev, phone = linked
    p = tmp_path / "wide.png"
    Image.new("RGB", (3840, 2160), (10, 20, 30)).save(p)
    s = ops.Session()
    s._t = t
    r = ops.wallpaper(s, str(p), no_backup=True, target="lock", crop="center")
    assert r.text == "Lock screen wallpaper set (cropped to the screen, 1080x2400)."
    assert Image.open(io.BytesIO(dev.lock_now)).size == (1080, 2400)
    assert dev.wallpaper_now is None  # home untouched


def test_wallpaper_messages_name_what_changed(linked, tmp_path):
    t, dev, phone = linked
    s = ops.Session()
    s._t = t
    r = ops.wallpaper(s, _img(tmp_path, "a.png", b"\x89PNG x"), target="both")
    assert r.text.startswith("Home and lock screen wallpaper set. Previous ones saved as wallpaper-")
    assert r.text.endswith("`homalawn undo` puts them back.")
    with pytest.raises(ops.OpError):
        ops.wallpaper(s, "/x.png", target="side")


def test_screen_size_of_an_older_build_over_adb_comes_from_the_window_manager(tmp_path, monkeypatch):
    monkeypatch.setenv("HOMALAWN_DATA", str(tmp_path / "data"))
    dev = FakeDevice(tmp_path)
    dev.old_build = True
    shell = dev.shell
    dev.shell = lambda *a: ("Physical size: 1440x3120\nOverride size: 1080x2340\n" if a == ("wm", "size")
                            else shell(*a))
    assert Launcher(AdbTransport(device=dev)).screen_size() == (1080, 2340)


def test_screen_size_unknown_over_the_link_says_to_update(linked):
    t, dev, phone = linked
    dev.old_build = True
    with pytest.raises(NotSupported, match="update"):
        Launcher(t).screen_size()


def test_lock_backup_param_goes_to_the_phone_over_adb():
    seen = []

    class Dev:
        def call(self, m, arg=None, extras=None):
            seen.append((m, arg, extras))
            return {"path": "/d/files/omalawn/wp-backup-1.png"}

        def run_as_read(self, p):
            return b"PNG"

        def run_as(self, *a):
            return ""

        def shell(self, *a):
            return ""
    t = AdbTransport(device=Dev())
    assert t.wallpaper_backup("lock") == b"PNG"
    assert t.wallpaper_backup() == b"PNG"
    assert seen == [("wallpaper_backup", None, {"which": "lock"}), ("wallpaper_backup", None, None)]


def test_lock_follows_home_answer_from_the_phone_becomes_the_marker(linked, tmp_path):
    """The phone changed between the info and the read: its refusal still means 'shows home'."""
    t, dev, phone = linked

    def gone():
        raise ProviderError("lock_follows_home: the lock screen shows the home wallpaper", code="lock_follows_home")
    phone.lock_wallpaper = gone
    snap = Launcher(t).backup_wallpaper(target="lock")
    assert snapshots.load(snap) == {LOCK_FOLLOWS_HOME: b""}
