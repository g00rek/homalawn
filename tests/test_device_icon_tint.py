"""Themed icon colours follow the launcher's colours, on the test AVD (HOMALAWN_SERIAL=emulator-5560).

Patch 0023: the accent, light/dark mode and the desktop's own icon colours (`homalawn_icon_color`
for the symbol, `homalawn_icon_bg_color` for the plate, "#rrggbb") are part of the launcher's icon
state, so a change redraws themed icons at once and a launcher restart keeps the new colours.
The colours are read from screenshots of the dock (the AVD's default dock apps all ship a
monochrome layer). Every setting touched is put back."""
import io
import os
import subprocess
import time
from collections import Counter

import pytest
from PIL import Image

pytestmark = pytest.mark.device
SERIAL = os.environ.get("HOMALAWN_SERIAL")
if not SERIAL:
    pytest.skip("HOMALAWN_SERIAL not set", allow_module_level=True)

from conftest import DEVICE_PACKAGE as PKG
from homalawn import prefs
from homalawn.adb import AdbTransport

KEYS = [("shared", "themed_icons", "bool"), ("shared", "pref_launcherTheme", "string"),
        ("datastore", "accent_color", "string"), ("shared", "homalawn_icon_color", "string"),
        ("shared", "homalawn_icon_bg_color", "string")]
YELLOW, PLATE = (0xfc, 0xee, 0x0a), (0x16, 0x18, 0x22)


def sh(*args):
    return subprocess.run(["adb", "-s", SERIAL, "shell", *args], check=True, capture_output=True,
                          text=True).stdout


def dock() -> Counter:
    """Pixel colours of the dock row (the bottom fifth above the search bar)."""
    png = subprocess.run(["adb", "-s", SERIAL, "exec-out", "screencap", "-p"], check=True,
                         capture_output=True).stdout
    im = Image.open(io.BytesIO(png)).convert("RGB")
    w, h = im.size
    return Counter(im.crop((0, int(h * 0.77), w, int(h * 0.84))).get_flattened_data())


def write(t, *writes):
    prefs.send_many(t, [w for w in writes if w[3] is not None],
                    [(s, k) for s, k, _, v in writes if v is None])


def settle(t):
    sh("input", "keyevent", "KEYCODE_HOME")
    end = time.monotonic() + 30
    while time.monotonic() < end:
        try:
            if t.device.call("ping")["model_loaded"]:
                break
        except Exception:
            pass
        time.sleep(1)
    time.sleep(4)


def restart(t):
    sh("am", "force-stop", PKG)
    settle(t)


@pytest.fixture
def t():
    t = AdbTransport(SERIAL)
    cur = prefs.current(t)
    old = {k: cur.get(s, {}).get(k, {}).get("value") for s, k, _ in KEYS}
    write(t, ("shared", "themed_icons", "bool", "true"), ("shared", "pref_launcherTheme", "string", "dark"),
          ("shared", "homalawn_icon_color", "string", None), ("shared", "homalawn_icon_bg_color", "string", None))
    settle(t)
    yield t
    write(t, *[(s, k, ty, None if old[k] is None else prefs.value_text(old[k])) for s, k, ty in KEYS])
    settle(t)


def top(c: Counter, n=2):
    return {col for col, _ in c.most_common(n)}


def test_accent_change_redraws_themed_icons_and_survives_a_restart(t):
    write(t, ("datastore", "accent_color", "string", "custom|#ff7aa2f7"))
    settle(t)
    blue = top(dock())
    write(t, ("datastore", "accent_color", "string", "custom|#fffcee0a"))
    settle(t)
    yellow = top(dock())
    assert blue != yellow, "the icons kept the previous accent's colours"
    restart(t)
    assert top(dock()) == yellow
    write(t, ("shared", "pref_launcherTheme", "string", "light"))
    settle(t)
    assert top(dock()) != yellow, "light mode kept the dark mode's icon colours"


def test_desktop_icon_colours_override_the_accent_until_cleared(t):
    write(t, ("datastore", "accent_color", "string", "custom|#ff7aa2f7"))
    settle(t)
    accent = top(dock())
    write(t, ("shared", "homalawn_icon_color", "string", "#fcee0a"),
          ("shared", "homalawn_icon_bg_color", "string", "#161822"))
    settle(t)
    c = dock()
    assert c[YELLOW] > 2000 and c[PLATE] > 20000, c.most_common(4)
    restart(t)
    c = dock()
    assert c[YELLOW] > 2000 and c[PLATE] > 20000, c.most_common(4)
    write(t, ("shared", "homalawn_icon_color", "string", ""), ("shared", "homalawn_icon_bg_color", "string", None))
    settle(t)
    c = dock()
    assert c[YELLOW] < 100 and c[PLATE] < 100
    assert top(c) == accent
