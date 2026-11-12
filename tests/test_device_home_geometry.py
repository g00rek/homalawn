"""The Home screen page's phone picture against the phone itself (HOMALAWN_SERIAL=emulator-5560).

The AVD is set close to the owner's phone (1440x3120 at density 560) and given its home screen through
the window, as a person would: grid 4x8, a dock of 4, labels on at 90 %, a 4x2 widget at the top. Then
the window's picture (homeFacts' `preview`, drawn from the phone's own `launcher.device_profile`) is
compared with where the phone really draws: the icon size, each row's top and the dock, as fractions
of the screen; the cells' tops are also read from the phone's own view tree (uiautomator), which the
geometry call does not touch. Live edits of the draft (icon size, label size, grid) move the picture
by the same factors. Both pictures go to HOMALAWN_GEOMETRY_SHOTS (default tests/build/geometry), the
measured numbers to its compare.json. Everything is put back: undo of the one home snapshot, the
AVD's screen size and density."""
import json
import os
import re
import subprocess
import time
from pathlib import Path

import pytest

from test_device_shell_home import HomeWindow, phone  # noqa: F401  (the fixture)

pytestmark = pytest.mark.device
SERIAL = os.environ.get("HOMALAWN_SERIAL")
if not SERIAL:
    pytest.skip("HOMALAWN_SERIAL not set", allow_module_level=True)

SHOTS = Path(os.environ.get("HOMALAWN_GEOMETRY_SHOTS") or Path(__file__).parent / "build" / "geometry")
TOL = 0.03   # of the screen's width or height


def adb(*args, check=True) -> str:
    return subprocess.run(["adb", "-s", SERIAL, *args], check=check, capture_output=True, text=True).stdout


def cells_on_phone(label_of: dict) -> dict:
    """label -> (left, top, right, bottom) of its cell on the phone's home screen (uiautomator)."""
    adb("shell", "uiautomator", "dump", "/sdcard/hl-ui.xml")
    xml = adb("shell", "cat", "/sdcard/hl-ui.xml")
    out = {}
    for m in re.finditer(r"<node [^>]*>", xml):
        a = dict(re.findall(r'(\w[\w-]*)="([^"]*)"', m.group(0)))
        b = [int(v) for v in re.findall(r"\d+", a.get("bounds", ""))]
        t = a.get("text") or a.get("content-desc")
        if t in label_of and len(b) == 4 and a.get("package", "").startswith("app.homalawn"):
            out[t] = tuple(b)
    return out


def test_preview_matches_the_phone(phone, link_transport, tmp_path, monkeypatch):  # noqa: F811
    la = phone
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))
    SHOTS.mkdir(parents=True, exist_ok=True)
    size0 = adb("shell", "wm", "size")
    density0 = adb("shell", "wm", "density")
    w = HomeWindow(tmp_path, link_transport.label)
    applied = False
    try:
        adb("shell", "wm", "size", "1440x3120")
        adb("shell", "wm", "density", "560")
        adb("shell", "input", "keyevent", "KEYCODE_HOME")
        time.sleep(4)
        la.refresh()
        la.wait_loaded()
        w.wait_connected()
        w.open_home()
        f = w.home()
        top = 1 if w.ask("home.get", phone=w.name)["top_row_reserved"] else 0
        widgets = w.ask("home.widgets", phone=w.name)
        wide = next((x for x in widgets if x.get("cells") == [4, 2]), None) or \
            next((x for x in widgets if (x.get("cells") or [0])[0] >= 4), None)
        assert wide, [x.get("cells") for x in widgets]

        # the owner's home screen, through the window: grid 4x8, dock 4, labels 90 %, a 4x2 widget on top
        w.edit(grid=[4, 8])
        w.edit(dock=4)
        w.edit(set={"icon_scale": 1, "label_scale": 0.9, "show_labels": True})
        f = w.home()
        free = {(i["x"], i.get("y")) for s in f["screens"] if s["index"] == f["screen"] for i in s["items"]}
        rows = [y for y in range(top, 7) if all((x, y) not in free and (x, y + 1) not in free for x in range(4))]
        if rows:
            assert w.edit(drop={"src": {"widget": wide["label"]}, "target": {"cell": [f["screen"], 0, rows[0]]}}) == "ok"
        if w.home()["changes"]:
            assert w.edit(apply=True) == "started"
            applied = True
            w.wait_for(lambda: not w.home()["applying"], "Apply never finished", tries=600)
            assert not w.home()["applyError"], w.home()
        time.sleep(3)
        w.call("homeEdit", "{}")
        w.wait_for(lambda: w.home().get("preview"), "the picture never drew", tries=120)

        # what each side draws
        geo = link_transport.call("launcher.device_profile", {})
        f = w.home()
        pv = f["preview"]
        W, H = geo["screen"]["w"], geo["screen"]["h"]
        ws, hs = geo["workspace"], geo["hotseat"]
        rows_n = f["grid"][1]
        phone_m = {"icon": geo["icon"]["size"] / W,
                   "rows": [(ws["y"] + r * (ws["cell_h"] + ws["gap_y"])) / H for r in range(rows_n)],
                   "dock": hs["y"] / H, "dock_icon": hs["icon_size"] / W, "grid_bottom": (ws["y"] + ws["h"]) / H}
        g = pv["grid"]
        prev_m = {"icon": pv["icon"], "rows": [g["y"] + r * g["h"] / rows_n for r in range(rows_n)],
                  "dock": pv["dock"]["y"], "dock_icon": pv["dockIcon"], "grid_bottom": g["y"] + g["h"]}
        # the cells' tops from the phone's own view tree
        on_screen = {i["title"]: (i["x"], i["y"]) for s in f["screens"] if s["index"] == f["screen"]
                     for i in s["items"] if i["type"] == "app" and i.get("title")}
        adb("shell", "input", "keyevent", "KEYCODE_HOME")
        time.sleep(2)
        seen = cells_on_phone(on_screen)
        tree = {t: {"row": on_screen[t][1], "top": b[1] / H, "preview_top": prev_m["rows"][on_screen[t][1]]}
                for t, b in seen.items()}

        phone_png = SHOTS / "phone.png"
        phone_png.write_bytes(subprocess.run(["adb", "-s", SERIAL, "exec-out", "screencap", "-p"], check=True,
                                             capture_output=True).stdout)
        win_png = SHOTS / "window-home.png"
        win_png.unlink(missing_ok=True)
        w.call("snapshot", f"home:{win_png}")
        w.wait_for(lambda: win_png.exists(), "no picture of the window")

        diffs = {"icon": abs(prev_m["icon"] - phone_m["icon"]), "dock": abs(prev_m["dock"] - phone_m["dock"]),
                 "dock_icon": abs(prev_m["dock_icon"] - phone_m["dock_icon"]),
                 "grid_bottom": abs(prev_m["grid_bottom"] - phone_m["grid_bottom"]),
                 "rows": max(abs(a - b) for a, b in zip(prev_m["rows"], phone_m["rows"])),
                 "tree_rows": max([abs(v["top"] - v["preview_top"]) for v in tree.values()] or [0])}

        # live edits follow the draft by the same factors
        icon0, label0 = pv["icon"], pv["label"]
        w.edit(set={"icon_scale": 0.8})
        icon_ratio = w.home()["preview"]["icon"] / icon0
        w.edit(set={"icon_scale": 1, "label_scale": 1.1})
        label_ratio = w.home()["preview"]["label"] / label0
        w.edit(grid=[4, 6])
        p6 = w.home()["preview"]
        w.edit(discard=True)
        report = {"geometry": geo, "phone": phone_m, "preview": prev_m, "tree": tree,
                  "diff": diffs, "live": {"icon_x0.8": icon_ratio, "label_0.9_to_1.1": label_ratio,
                                                                  "grid_4x6_row_h": p6["grid"]["h"] / 6}}
        (SHOTS / "compare.json").write_text(json.dumps(report, indent=1))
        print(json.dumps({"diff": diffs, "live": report["live"]}, indent=1))
        assert all(v <= TOL for v in diffs.values()), diffs
        assert len(tree) >= 1, "no app of the shown screen found on the phone"
        assert abs(icon_ratio - 0.8) < 0.03 and abs(label_ratio - 1.1 / 0.9) < 0.03
        assert abs(p6["grid"]["h"] - g["h"]) < 0.01      # the same area, 6 rows of it
    finally:
        try:
            if applied:
                assert w.call("undo") == "started"
                time.sleep(15)
        finally:
            w.close()
            adb("shell", "wm", "size", "reset" if "Override" in size0 else size0.split(":")[-1].strip(), check=False)
            adb("shell", "wm", "density", "reset" if "Override" in density0 else density0.split(":")[-1].strip(), check=False)
            adb("shell", "input", "keyevent", "KEYCODE_HOME", check=False)
