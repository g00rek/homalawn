"""Themed (monochrome) icons on the test AVD (HOMALAWN_SERIAL=emulator-5560) with no icon pack.

Upstream Lawnchair switched `themed_icons` back off at every launcher start when no themed icon
pack (Lawnicons) was installed; patch 0022 keeps it on, so apps' own monochrome layer is drawn.
The test needs an AVD without Lawnicons and puts the previous value back."""
import os
import subprocess
import time

import pytest

pytestmark = pytest.mark.device
SERIAL = os.environ.get("HOMALAWN_SERIAL")
if not SERIAL:
    pytest.skip("HOMALAWN_SERIAL not set", allow_module_level=True)

from conftest import DEVICE_PACKAGE as PKG
from homalawn import prefs
from homalawn.adb import AdbTransport


def sh(*args):
    return subprocess.run(["adb", "-s", SERIAL, "shell", *args], check=True, capture_output=True,
                          text=True).stdout


def themed(t) -> bool:
    return bool(prefs.current(t)["shared"].get("themed_icons", {}).get("value", False))


def wait_for(fn, seconds):
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        try:
            if fn():
                return True
        except Exception:
            pass
        time.sleep(1)
    return False


def test_themed_icons_survive_a_launcher_restart_without_an_icon_pack():
    if "lawnicons" in sh("pm", "list", "packages").lower():
        pytest.skip("Lawnicons is installed; this test needs an AVD without an icon pack")
    t = AdbTransport(SERIAL)
    before = themed(t)
    try:
        prefs.set_(t, "themed_icons", "true")
        assert themed(t) is True
        sh("am", "force-stop", PKG)
        sh("input", "keyevent", "KEYCODE_HOME")
        # The home activity's onCreate ran the old reset; once the model is loaded it has run.
        assert wait_for(lambda: t.device.call("ping")["model_loaded"], 30)
        time.sleep(2)
        assert themed(t) is True, "themed_icons was switched back off at launcher start"
    finally:
        prefs.set_(t, "themed_icons", "true" if before else "false")
