"""Applying an Omarchy look on the test AVD (HOMALAWN_SERIAL=emulator-5560), over the paired link.

The look goes through ops.look_apply with a tiny generated background; afterwards `undo` must put
the preferences and both wallpapers back. The AVD ends as it was found (the session hygiene
fixture in conftest.py asserts that too)."""
import io
import os
import time
from pathlib import Path

import pytest
from PIL import Image

from homalawn import lookconf, ops, prefs, snapshots
from homalawn.look import Theme
from homalawn.session import Launcher

pytestmark = pytest.mark.device
SERIAL = os.environ.get("HOMALAWN_SERIAL")
if not SERIAL:
    pytest.skip("HOMALAWN_SERIAL not set", allow_module_level=True)

BUILD = Path(__file__).parent.parent / "build"


def two_colour(path: Path) -> Path:
    img = Image.new("RGB", (400, 200), (30, 60, 200))
    img.paste((230, 120, 20), (200, 0, 400, 200))
    img.save(path, "PNG")
    return path


def accent(t):
    return prefs.current(t)["datastore"].get("accent_color", {}).get("value")


@pytest.fixture
def phone(link_transport, avd_hygiene, tmp_path):
    """Over the link Android may not let Lawnchair read the current wallpapers, so a first picture
    is set over the link without a backup (homalawn then holds both pictures and the look can be
    applied over the link). The wallpapers are saved over adb first and put back at the end."""
    over_adb = avd_hygiene
    over_adb.refresh()
    over_adb.wait_loaded()
    saved = over_adb.backup_wallpaper(prefix="look-test-saved-", target="both")
    s = ops.Session()
    s._t = link_transport
    la = Launcher(link_transport)
    la.refresh()
    la.wait_loaded()
    la.set_wallpaper(str(two_colour(tmp_path / "seed.png")), backup=False, target="both")
    yield s, la, over_adb.transport.device
    over_adb.restore(saved, force=True, no_backup=True)
    snapshots.delete(saved)


def test_apply_changes_both_wallpapers_and_the_accent_and_undo_puts_them_back(phone, tmp_path):
    s, la, d = phone
    bg = two_colour(tmp_path / "bg.png")
    theme = Theme("test-night", tmp_path, {"mode": "dark", "accent": "#7aa2f7", "background": "#1a1b26",
                                           "lighter_background": "#24283b"}, [bg], None)
    before_info = d.call("wallpaper_info")
    before_accent = accent(la.transport)
    before_mode = prefs.current(la.transport)["shared"].get("pref_launcherTheme", {}).get("value")
    wall = lookconf.wallpaper_default()
    # the lock screen has its own crop and colour layer
    wall["lock"]["layer"] = 40
    st = lookconf.LookSettings(source="chosen", theme="test-night", wallpaper=wall,
                               crops={str(bg): {"home": 0.0, "lock": 1.0}})
    r = ops.look_apply(s, st, theme=theme, background=bg)
    assert r.text.startswith("Wallpaper & style applied (test-night, bg.png: home and lock). The previous one is saved as look-")
    assert r.text.endswith("; `homalawn undo` puts it back.")
    assert r.data["shown"] is True and len(r.data["key"]) == 16      # through the phone's look book
    after = d.call("wallpaper_info")
    assert after["system_id"] != before_info["system_id"]
    assert after["lock_follows_home"] is False and after["lock_id"] != before_info["lock_id"]  # its own lock picture
    assert accent(la.transport) == "custom|#ff7aa2f7"
    assert prefs.current(la.transport)["shared"]["pref_launcherTheme"]["value"] == "dark"
    BUILD.mkdir(exist_ok=True)
    d.shell("input", "keyevent", "KEYCODE_HOME")
    time.sleep(2)
    (BUILD / "look-applied.png").write_bytes(d.screencap())
    un = ops.undo(s)
    assert un.text.startswith("Undid: ") and un.text.endswith(f" ({r.data['snapshot']})"), un.text
    assert accent(la.transport) == before_accent
    assert prefs.current(la.transport)["shared"].get("pref_launcherTheme", {}).get("value") == before_mode
    back = d.call("wallpaper_info")
    assert back["lock_follows_home"] == before_info["lock_follows_home"]
    assert r.data["snapshot"] not in snapshots.list_undoable(la.device_key())


def test_after_undo_the_same_look_is_shown_again(phone, tmp_path):
    """undo writes the screen with wallpaper_set / prefs_set_many, which clears the look book's
    `applied`: the same look (same key) goes on the screen again instead of "Already on"."""
    s, la, d = phone
    d.call("debug_look_reset")
    bg = two_colour(tmp_path / "bg.png")
    theme = Theme("test-night", tmp_path, {"mode": "dark", "accent": "#7aa2f7", "background": "#1a1b26",
                                           "lighter_background": "#24283b"}, [bg], None)
    st = lookconf.LookSettings(source="chosen", theme="test-night", wallpaper=lookconf.wallpaper_default())
    first = ops.look_apply(s, st, theme=theme, background=bg)
    assert first.data["shown"] is True
    again = ops.look_apply(s, st, theme=theme, background=bg)
    assert again.text.startswith("Already on") and again.data["shown"] is False   # really on the screen
    ops.undo(s)
    r = ops.look_apply(s, st, theme=theme, background=bg)
    assert r.data["key"] == first.data["key"] and r.data["shown"] is True, r.text
    assert r.text.startswith("Wallpaper & style applied")
    ops.undo(s)
    d.call("debug_look_reset")


def test_themed_icons_tint_check_saves_screenshots(phone):
    """Not a pass/fail on colour: it records what the home screen looks like with themed icons on,
    first with the system accent, then with a vivid custom accent, for a person to compare."""
    s, la, d = phone
    BUILD.mkdir(exist_ok=True)
    snaps = [la.set_pref("themed_icons", "true")]
    d.shell("input", "keyevent", "KEYCODE_HOME")
    time.sleep(2)
    (BUILD / "themed-icons-system.png").write_bytes(d.screencap())
    snaps.append(la.set_pref("accent_color", "custom|#ffff00aa"))
    d.shell("input", "keyevent", "KEYCODE_HOME")
    time.sleep(2)
    (BUILD / "themed-icons-accent.png").write_bytes(d.screencap())
    for _ in snaps:
        la.undo()
