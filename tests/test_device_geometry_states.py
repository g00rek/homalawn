"""launcher.device_profile is the same reading in every state, and equals the launcher's laid-out views
(HOMALAWN_SERIAL=emulator-5560, the test build).

Patch 0024: the phone computes the home screen's geometry from the DeviceProfile its launcher lays
out with, never from the views. Here the AVD is set to the owner's phone's screen (1440x3120 at
density 560), and the reading is compared, to the pixel, with what the debug build's
`debug_home_views` reads back from the laid-out views (the cell layouts' and an icon's positions on
the screen): with the launcher shown, with the screen locked, right after the app is updated
(install -r while locked: the launcher process restarts, no activity), and in a freshly started
process with no launcher activity (force-stop while locked), and with the phone turned sideways.
Everything is put back: screen size, density, rotation, and the screen woken and unlocked."""
import json
import os
import subprocess
import time

import pytest

pytestmark = pytest.mark.device
SERIAL = os.environ.get("HOMALAWN_SERIAL")
if not SERIAL:
    pytest.skip("HOMALAWN_SERIAL not set", allow_module_level=True)
PKG = os.environ.get("HOMALAWN_PACKAGE") or "app.homalawn.debug"


def adb(*args, check=True) -> str:
    return subprocess.run(["adb", "-s", SERIAL, *args], check=check, capture_output=True, text=True).stdout


def provider(method: str) -> dict:
    out = adb("shell", "content", "call", "--uri", f"content://{PKG}.omalawn", "--method", method)
    assert "ok=true" in out, out
    start = out.index("result=") + len("result=")
    return json.loads(out[start:out.rindex("}, ok=") + 1])


def reading() -> dict:
    g = provider("device_profile")
    ws, hs, ic = g["workspace"], g["hotseat"], g["icon"]
    return {"workspace": [ws[k] for k in ("x", "y", "w", "h", "cell_w", "cell_h")],
            "icon": [ic["size"], ic["top"]],
            "hotseat": [hs[k] for k in ("x", "y", "w", "h", "cell_w", "cell_h")],
            "dock_icon": [hs["icon_size"], hs["icon_top"]], "insets": g["insets"]}


def views() -> dict | None:
    v = provider("debug_home_views")
    if not v:
        return None
    ws, hs = v["workspace"], v["hotseat"]
    return {"workspace": [ws[k] for k in ("x", "y", "w", "h", "cell_w", "cell_h")],
            "icon": [v["icon"]["size"], v["icon"]["top"]] if v.get("icon") else None,
            "hotseat": [hs[k] for k in ("x", "y", "w", "h", "cell_w", "cell_h")],
            "dock_icon": [v["dock_icon"]["size"], v["dock_icon"]["top"]] if v.get("dock_icon") else None,
            "insets": v["insets"]}


def wake():
    adb("shell", "input", "keyevent", "KEYCODE_WAKEUP")
    time.sleep(1)
    adb("shell", "wm", "dismiss-keyguard")
    time.sleep(1)
    adb("shell", "input", "keyevent", "KEYCODE_HOME")
    time.sleep(5)


def lock():
    adb("shell", "input", "keyevent", "KEYCODE_SLEEP")
    time.sleep(3)


def installed_apk() -> str:
    path = adb("shell", "pm", "path", PKG).split("package:")[1].split()[0]
    local = os.path.join(os.environ.get("TMPDIR", "/tmp"), f"hl-geometry-{os.getpid()}.apk")
    adb("pull", path, local)
    return local


def test_one_reading_in_every_state_equals_the_views():
    size0 = adb("shell", "wm", "size")
    density0 = adb("shell", "wm", "density")
    acc0 = adb("shell", "settings", "get", "system", "accelerometer_rotation").strip()
    rot0 = adb("shell", "settings", "get", "system", "user_rotation").strip()
    apk = installed_apk()
    try:
        adb("shell", "wm", "size", "1440x3120")
        adb("shell", "wm", "density", "560")
        wake()
        laid_out = views()
        assert laid_out and laid_out["icon"] and laid_out["dock_icon"], "the home screen needs an app and a dock app"
        seen = {"shown": (reading(), laid_out)}
        lock()
        seen["locked"] = (reading(), views())
        adb("install", "-r", apk)
        time.sleep(2)
        seen["just updated"] = (reading(), views())
        adb("shell", "am", "force-stop", PKG)
        time.sleep(2)
        seen["no activity"] = (reading(), views())
        wake()
        adb("shell", "settings", "put", "system", "accelerometer_rotation", "0")
        adb("shell", "settings", "put", "system", "user_rotation", "1")
        time.sleep(4)
        seen["sideways"] = (reading(), None)
        adb("shell", "settings", "put", "system", "user_rotation", "0")
        time.sleep(4)
        after = views()
        print(json.dumps({k: {"reading": r, "views": v} for k, (r, v) in seen.items()} | {"views after": after}))
        assert seen["just updated"][1] is None and seen["no activity"][1] is None, "those states must have no views"
        for state, (r, v) in seen.items():
            assert r == laid_out, (state, r, laid_out)
            assert v is None or v == laid_out, (state, v, laid_out)
        assert after == laid_out
    finally:
        adb("shell", "settings", "put", "system", "user_rotation", rot0 or "0", check=False)
        adb("shell", "settings", "put", "system", "accelerometer_rotation", acc0 or "1", check=False)
        adb("shell", "wm", "size", "reset" if "Override" in size0 else size0.split(":")[-1].strip(), check=False)
        adb("shell", "wm", "density", "reset" if "Override" in density0 else density0.split(":")[-1].strip(), check=False)
        wake()
        os.unlink(apk)
