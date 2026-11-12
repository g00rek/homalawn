"""The "Homa Lawn" settings icon (patch 0018) on the test AVD (HOMALAWN_SERIAL=emulator-5560).

The app drawer shows ONE icon with the app's name (the settings, not the launcher's home entry;
"Homa Lawn Test" for the test build since patch 0033, so it can be told from a release install next
to it), search finds it, a tap opens the settings with "Wallpaper & style", and Lawnchair stays the
default home app. The desktop's app list (adb and link) has the icon and not the home entry, and the
icon can be placed on the home screen like any app (then undone).
Screenshots go to HOMALAWN_ICON_SHOTS (default tests/build/settings-icon)."""
import html
import os
import re
import subprocess
import time
from pathlib import Path

import pytest

from homalawn import apps
from test_device_seam import PKG, free_cell, kind, la, shown  # noqa: F401  (fixtures)

pytestmark = pytest.mark.device
SERIAL = os.environ.get("HOMALAWN_SERIAL")
if not SERIAL:
    pytest.skip("HOMALAWN_SERIAL not set", allow_module_level=True)

ALIAS = "app.lawnchair.omalawn.HomaLawnSettings"
HOME_ENTRY = "app.lawnchair.LawnchairLauncher"
LABEL = "Homa Lawn Test" if PKG.endswith(".debug") else "Homa Lawn"  # the test build's name (patch 0033)
SHOTS = Path(os.environ.get("HOMALAWN_ICON_SHOTS") or Path(__file__).parent / "build" / "settings-icon")


def adb(*args, check=True) -> str:
    return subprocess.run(["adb", "-s", SERIAL, *args], check=check, capture_output=True, text=True).stdout


def shot(name: str) -> None:
    SHOTS.mkdir(parents=True, exist_ok=True)
    (SHOTS / f"{name}.png").write_bytes(
        subprocess.run(["adb", "-s", SERIAL, "exec-out", "screencap", "-p"], check=True, capture_output=True).stdout)


def nodes() -> list[dict]:
    adb("shell", "uiautomator", "dump", "/sdcard/hl-ui.xml")
    out = []
    for m in re.finditer(r"<node [^>]*>", adb("shell", "cat", "/sdcard/hl-ui.xml")):
        a = {k: html.unescape(v) for k, v in re.findall(r'(\w[\w-]*)="([^"]*)"', m.group(0))}  # "Wallpaper &amp; style"
        b = [int(v) for v in re.findall(r"\d+", a.get("bounds", ""))]
        if len(b) == 4:
            a["center"] = ((b[0] + b[2]) // 2, (b[1] + b[3]) // 2)
            out.append(a)
    return out


def labelled(text: str) -> list[dict]:
    return [n for n in nodes() if text in (n.get("text"), n.get("content-desc"))]


def wait_for(text: str, timeout=10.0) -> list[dict]:
    end = time.time() + timeout
    while time.time() < end:
        found = labelled(text)
        if found:
            return found
        time.sleep(0.5)
    raise AssertionError(f"{text!r} not on screen")


def top() -> str:
    m = re.search(r"topResumedActivity=ActivityRecord\{\S+ \S+ (\S+)", adb("shell", "dumpsys", "activity", "activities"))
    return m.group(1) if m else ""


def wait_top(suffix: str, timeout=10.0) -> None:
    end = time.time() + timeout
    while not top().endswith(suffix) and time.time() < end:
        time.sleep(0.3)
    assert top().endswith(suffix), f"{suffix} not in front: {top()}"


def home() -> None:
    adb("shell", "input", "keyevent", "KEYCODE_WAKEUP")
    adb("shell", "input", "keyevent", "KEYCODE_HOME")
    wait_top(HOME_ENTRY)
    time.sleep(1)


def open_drawer() -> None:
    w, h = (int(v) for v in re.search(r"(\d+)x(\d+)", adb("shell", "wm", "size")).groups())
    adb("shell", "input", "swipe", str(w // 2), str(h * 3 // 4), str(w // 2), str(h // 5), "300")
    time.sleep(1.5)


def tap(n: dict) -> None:
    adb("shell", "input", "tap", str(n["center"][0]), str(n["center"][1]))


def home_role() -> str:
    return adb("shell", "cmd", "role", "get-role-holders", "android.app.role.HOME").strip()


def test_the_drawer_shows_one_homa_lawn_icon_that_opens_settings(la):  # noqa: F811
    if la.transport.name != "adb":
        pytest.skip("the screen part runs once")
    assert home_role() == PKG
    home()
    open_drawer()
    # the drawer scrolls; "Homa Lawn" sorts near the top of a stock AVD's few apps
    icons = wait_for(LABEL)
    shot("01-drawer")
    # the top icon row is the drawer's suggestions once the icon was used; the A-Z list starts with
    # Calendar, Camera, Chrome on a stock AVD, so a Homa Lawn icon there is a suggestion
    top_row = min(n["center"][1] for n in nodes() if n.get("resource-id", "").endswith(":id/icon"))
    listed = [n for n in icons if n["center"][1] != top_row]
    assert len(listed) == 1, f"{len(listed)} {LABEL!r} icons in the drawer's app list"
    tap(icons[0])
    wait_top(ALIAS)
    wait_for("Wallpaper & style")
    shot("02-settings")
    assert home_role() == PKG

    home()
    open_drawer()
    tap(min((n for n in nodes() if n.get("class", "").endswith("EditText")), key=lambda n: n["center"][1]))
    time.sleep(1)
    adb("shell", "input", "text", "homa")
    hits = wait_for(LABEL)
    shot("03-search")
    tap(hits[0])
    wait_top(ALIAS)
    wait_for("Wallpaper & style")
    home()
    assert home_role() == PKG


def test_the_desktop_lists_the_icon_and_can_place_it(la):  # noqa: F811
    found = {(a["package"], a["activity"]) for a in apps.list_apps(la.transport)}
    assert (PKG, ALIAS) in found
    assert (PKG, HOME_ENTRY) not in found
    cell = free_cell(la)
    assert la.place(f"{PKG}/{ALIAS}", 0, *cell) == []
    d = la.transport.device if la.transport.name == "adb" else None
    if d is not None:
        assert shown(d, PKG, 0, *cell)
    placed = [i for s in la.get_layout()["screens"] if s["index"] == 0 for i in s["items"]
              if (i.get("x"), i.get("y")) == cell]
    assert placed and placed[0].get("package") == PKG
    la.undo()
    assert not [i for s in la.get_layout()["screens"] if s["index"] == 0 for i in s["items"]
                if (i.get("x"), i.get("y")) == cell]
