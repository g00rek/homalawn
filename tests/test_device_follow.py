"""The follower end to end on the test AVD (HOMALAWN_SERIAL=emulator-5560): a real `homalawn serve`
process, the AVD paired into a temp config, and a temp stand-in for Omarchy's `current` folder.

The phone is set to follow, then the stand-in's background link is switched to another real
Omarchy theme picture; the AVD wallpaper must change within 5 seconds, with exactly one more
look snapshot. Undo then returns the phone, and the AVD ends as found. Nothing real on this
computer is touched: no ~/.local/state/omarchy, no ~/.config/homalawn, no omarchy-shell."""
import json
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import pytest
from PIL import Image

from homalawn import look, lookconf, snapshots
from homalawn.session import Launcher

pytestmark = pytest.mark.device
SERIAL = os.environ.get("HOMALAWN_SERIAL")
if not SERIAL:
    pytest.skip("HOMALAWN_SERIAL not set", allow_module_level=True)

THEMES = Path("/usr/share/omarchy/themes")
PICTURES = (".png", ".webp", ".jpg", ".jpeg")  # Omarchy ships its backgrounds as .webp since 2026-11


def background(theme: str, stem: str) -> Path | None:
    """The theme's background `stem` (e.g. "1-totoro") in whatever picture format Omarchy ships it."""
    return next((p for p in sorted((THEMES / theme / "backgrounds").glob(stem + ".*")) if p.suffix in PICTURES), None)


def backgrounds(theme: str, pattern: str) -> list[Path]:
    return [p for p in sorted((THEMES / theme / "backgrounds").glob(pattern)) if p.suffix in PICTURES]


def ask(sock_path, method, **params):
    s = socket.socket(socket.AF_UNIX)
    s.settimeout(60)
    s.connect(str(sock_path))
    s.sendall((json.dumps({"id": 1, "method": method, "params": params}) + "\n").encode())
    buf = b""
    while not buf.endswith(b"\n"):
        buf += s.recv(65536)
    s.close()
    msg = json.loads(buf)
    assert "error" not in msg, msg
    return msg["result"]


def looks(la):
    return [n for n in snapshots.list_undoable(la.device_key()) if n.startswith("look-")]


def test_follower_changes_the_phone_when_the_omarchy_background_changes(avd_hygiene, link_transport, tmp_path):
    bgs = backgrounds("catppuccin", "[12]-*")
    if len(bgs) < 2 or not (THEMES / "catppuccin" / "colors.toml").is_file():
        pytest.skip("the catppuccin Omarchy theme is not installed here")
    one, two = bgs[:2]
    d = avd_hygiene.transport.device
    avd_hygiene.refresh()
    avd_hygiene.wait_loaded()
    saved = avd_hygiene.backup_wallpaper(prefix="follow-test-saved-", target="both")

    la = Launcher(link_transport)
    la.refresh()
    la.wait_loaded()
    seed = tmp_path / "seed.png"
    Image.new("RGB", (400, 200), (30, 60, 200)).save(seed)
    la.set_wallpaper(str(seed), backup=False, target="both")   # a first picture, so the link can write wallpapers

    current = tmp_path / "current"
    current.mkdir()
    (current / "theme.name").write_text("catppuccin\n")
    (current / "background").symlink_to(one)
    run_dir = Path(tempfile.mkdtemp(prefix="hf"))   # short: unix socket paths are capped at 108 bytes
    sock = run_dir / "homalawnd.sock"
    env = dict(os.environ, HOMALAWN_SOCKET=str(sock), HOMALAWN_STATE_DIR=str(tmp_path / "state"),
               HOMALAWN_OMARCHY_STATE=str(current))
    env.pop("XDG_RUNTIME_DIR", None)
    log = open(tmp_path / "serve.log", "w")
    svc = subprocess.Popen([sys.executable, "-c", "from homalawn import daemon; daemon.run()"], env=env,
                           stdout=log, stderr=log)
    name = link_transport.label
    snap_names = []
    try:
        for _ in range(100):
            if sock.exists():
                break
            time.sleep(0.1)
        else:
            pytest.fail("the service did not start: " + (tmp_path / "serve.log").read_text())
        for _ in range(300):
            ph = [p for p in ask(sock, "phones") if p["name"] == name]
            if ph and ph[0]["connected"]:
                break
            time.sleep(0.1)
        else:
            pytest.fail("the service never connected to the phone")
        before = looks(la)
        first = ask(sock, "look.set", phone=name, settings=lookconf.LookSettings(source="follow").to_dict())
        assert first["text"].startswith(f"Wallpaper & style applied (Catppuccin, {one.name}"), first
        after_first = looks(la)
        assert len(after_first) == len(before) + 1
        wp1 = d.call("wallpaper_info")

        (current / "background.new").symlink_to(two)
        t0 = time.time()
        os.replace(current / "background.new", current / "background")   # atomic, like Omarchy's own switch
        wp2 = wp1
        while time.time() - t0 < 5:
            wp2 = d.call("wallpaper_info")
            if wp2["system_id"] != wp1["system_id"]:
                break
            time.sleep(0.25)
        took = time.time() - t0
        assert wp2["system_id"] != wp1["system_id"], f"the phone did not change within 5 s ({took:.1f})"
        time.sleep(3)   # let it settle: still exactly one more snapshot, no second apply
        after_second = looks(la)
        assert len(after_second) == len(after_first) + 1, after_second
        ph = [p for p in ask(sock, "phones") if p["name"] == name][0]
        assert ph["applied"]["background"] == str(two) and ph["error"] is None
        snap_names = after_second
        print(f"follower: phone changed {took:.1f}s after the background switch")

        ask(sock, "look.set", phone=name, settings=lookconf.LookSettings(source="off").to_dict())
        for _ in snap_names[:len(snap_names) - len(before)]:
            ask(sock, "undo", phone=name)
        assert looks(la) == before
    finally:
        svc.terminate()
        try:
            svc.wait(10)
        except subprocess.TimeoutExpired:
            svc.kill()
        log.close()
        avd_hygiene.restore(saved, force=True, no_backup=True)
        snapshots.delete(saved)
        shutil.rmtree(run_dir, ignore_errors=True)


def _start_service(tmp_path, current):
    run_dir = Path(tempfile.mkdtemp(prefix="hl"))   # short: unix socket paths are capped at 108 bytes
    sock = run_dir / "homalawnd.sock"
    env = dict(os.environ, HOMALAWN_SOCKET=str(sock), HOMALAWN_STATE_DIR=str(tmp_path / "state"),
               HOMALAWN_OMARCHY_STATE=str(current))
    env.pop("XDG_RUNTIME_DIR", None)
    log = open(tmp_path / "serve.log", "w")
    svc = subprocess.Popen([sys.executable, "-c", "from homalawn import daemon; daemon.run()"], env=env,
                           stdout=log, stderr=log)
    for _ in range(100):
        if sock.exists():
            break
        time.sleep(0.1)
    else:
        pytest.fail("the service did not start: " + (tmp_path / "serve.log").read_text())
    return svc, log, sock, run_dir


def _screen_off():
    subprocess.run(["adb", "-s", SERIAL, "shell", "input", "keyevent", "223"], check=True)   # sleep (screen off)


def _unlock():
    subprocess.run(["adb", "-s", SERIAL, "shell", "input", "keyevent", "224"], check=True)   # wake
    subprocess.run(["adb", "-s", SERIAL, "shell", "wm", "dismiss-keyguard"], check=True)
    subprocess.run(["adb", "-s", SERIAL, "shell", "input", "keyevent", "3"], check=False)   # home screen


def _dumpsys_wallpaper() -> str:
    out = subprocess.run(["adb", "-s", SERIAL, "shell", "dumpsys", "wallpaper"], capture_output=True, text=True).stdout
    return "\n".join(line.strip() for line in out.splitlines()
                     if line.strip().startswith(("System wallpaper state", "Lock wallpaper state", "mWidth", "mWallpaperId",
                                                 "mWhich", "mSystemWasBoth", "mWallpaperComponent")))


def _labels_on_screen() -> list[tuple[str, tuple[int, int]]]:
    import re
    subprocess.run(["adb", "-s", SERIAL, "shell", "uiautomator", "dump", "/sdcard/hl-ui.xml"], capture_output=True)
    xml = subprocess.run(["adb", "-s", SERIAL, "shell", "cat", "/sdcard/hl-ui.xml"], capture_output=True, text=True).stdout
    out = []
    for m in re.finditer(r"<node [^>]*>", xml):
        a = dict(re.findall(r'(\w[\w-]*)="([^"]*)"', m.group(0)))
        b = [int(v) for v in re.findall(r"\d+", a.get("bounds", ""))]
        if a.get("text") and len(b) == 4:
            out.append((a["text"], ((b[0] + b[2]) // 2, (b[1] + b[3]) // 2)))
    return out


def test_a_locked_phone_gets_the_look_and_the_home_screen_at_once(avd_hygiene, link_transport, tmp_path):
    """Owner, 2026-10-17: nothing waits for the unlock. Screen off (the AVD's lock): `look.set` sets
    the wallpaper and the colours at once (no 60 s timeout, no waiting state); `home.apply` with an
    app on a free cell is written at once, and after the unlock the home screen shows it in its
    cell without another apply."""
    if "locked" not in link_transport.call("launcher.update_status", {}):
        pytest.skip("this Lawnchair build does not report locked")
    bgs = backgrounds("catppuccin", "[12]-*")
    if not bgs or not (THEMES / "catppuccin" / "colors.toml").is_file():
        pytest.skip("the catppuccin Omarchy theme is not installed here")
    d = avd_hygiene.transport.device
    avd_hygiene.refresh()
    avd_hygiene.wait_loaded()
    saved = avd_hygiene.backup_wallpaper(prefix="lock-test-saved-", target="both")
    la = Launcher(link_transport)
    la.refresh()
    la.wait_loaded()
    seed = tmp_path / "seed.png"
    Image.new("RGB", (400, 200), (30, 60, 200)).save(seed)
    la.set_wallpaper(str(seed), backup=False, target="both")

    current = tmp_path / "current"
    current.mkdir()
    (current / "theme.name").write_text("catppuccin\n")
    (current / "background").symlink_to(bgs[0])
    svc, log, sock, run_dir = _start_service(tmp_path, current)
    name = link_transport.label

    def me():
        return [p for p in ask(sock, "phones") if p["name"] == name][0]

    def locked():
        return link_transport.call("launcher.update_status", {}).get("locked")

    homes_before = [n for n in snapshots.list_undoable(la.device_key()) if n.startswith("home-")]
    try:
        for _ in range(300):
            if me()["connected"]:
                break
            time.sleep(0.1)
        else:
            pytest.fail("the service never connected to the phone")
        before = looks(la)
        wp0 = d.call("wallpaper_info")
        prefs0 = link_transport.call("launcher.prefs_list", {})
        _screen_off()
        for _ in range(50):
            if locked():
                break
            time.sleep(0.2)
        assert locked() is True

        # the look: wallpaper and colours at once
        t0 = time.time()
        r = ask(sock, "look.set", phone=name, settings=lookconf.LookSettings(source="follow").to_dict())
        took = time.time() - t0
        assert r["text"].startswith("Wallpaper & style applied (Catppuccin"), r
        assert took < 45, f"look.set took {took:.1f}s"
        ph = me()
        assert ph["waiting"] is None and not ph["pending"] and ph["error"] is None, ph
        wp1 = d.call("wallpaper_info")
        assert wp1["system_id"] != wp0["system_id"] and locked() is True
        palette = [k for k, _ in look.palette_prefs(look.theme_named("catppuccin").colors)]
        prefs1 = link_transport.call("launcher.prefs_list", {})

        def value(p, k):
            return (p["datastore"].get(k) or p["shared"].get(k) or {}).get("value")
        changed = [k for k in palette if value(prefs0, k) != value(prefs1, k)]
        assert changed, f"no colour setting changed: {palette}"      # the colours, while locked
        print(f"locked: look applied in {took:.1f}s; wallpaper ids {wp0} -> {wp1}")
        print("dumpsys wallpaper (locked, after the apply):\n" + _dumpsys_wallpaper())
        assert len(looks(la)) == len(before) + 1

        # the home screen: an app on a free cell, written while locked
        lay = la.get_layout()
        cols, rows = lay["grid"]["cols"], lay["grid"]["rows"]
        taken = {(i["x"], i["y"]) for s in lay["screens"] if s["index"] == lay["screens"][0]["index"] for i in s["items"]}
        on_home = {i.get("package") for s in lay["screens"] for i in s["items"]} | {i.get("package") for i in lay["hotseat"]}
        cell = next((x, y) for y in range(1, rows) for x in range(cols) if (x, y) not in taken)
        app = next(a for a in ask(sock, "home.apps", phone=name) if a["package"] not in on_home)
        lay["screens"][0]["items"].append({"type": "app", "package": app["package"], "activity": app["activity"],
                                           "x": cell[0], "y": cell[1]})
        t0 = time.time()
        r = ask(sock, "home.apply", phone=name, layout=lay)
        took = time.time() - t0
        assert r["text"].startswith("Home screen applied") and "waiting" not in r, r
        assert took < 45, f"home.apply took {took:.1f}s"
        assert locked() is True
        print(f"locked: home screen written in {took:.1f}s; notes {r['notes']}")
        after = la.get_layout()
        assert any(i.get("package") == app["package"] and (i["x"], i["y"]) == cell
                   for i in after["screens"][0]["items"])
        homes = [n for n in snapshots.list_undoable(la.device_key()) if n.startswith("home-")]
        assert len(homes) == len(homes_before) + 1

        # unlock: the home screen shows it in its cell, with no further apply
        _unlock()
        geo = link_transport.call("launcher.device_profile", {})
        ws = geo["workspace"]
        x0 = ws["x"] + cell[0] * (ws["cell_w"] + ws["gap_x"])
        y0 = ws["y"] + cell[1] * (ws["cell_h"] + ws["gap_y"])
        seen = None
        for _ in range(30):
            seen = next((c for t, c in _labels_on_screen() if t == app["label"]), None)
            if seen:
                break
            time.sleep(0.5)
        assert seen, f"{app['label']} is not on the home screen after the unlock"
        assert x0 <= seen[0] <= x0 + ws["cell_w"] and y0 <= seen[1] <= y0 + ws["cell_h"], (seen, x0, y0, ws)
        subprocess.run(["adb", "-s", SERIAL, "exec-out", "screencap", "-p"], check=True,
                       stdout=open(tmp_path / "after-unlock.png", "wb"))
        shots = Path(os.environ.get("HOMALAWN_LOCK_SHOTS") or Path(__file__).parent / "build" / "locked")
        shots.mkdir(parents=True, exist_ok=True)
        shutil.copy(tmp_path / "after-unlock.png", shots / "home-after-unlock.png")
        assert [n for n in snapshots.list_undoable(la.device_key()) if n.startswith("home-")] == homes
        print(f"unlocked: {app['label']} shows at {seen} in cell {cell}")

        ask(sock, "undo", phone=name)                 # the home screen
        ask(sock, "look.set", phone=name, settings=lookconf.LookSettings(source="off").to_dict())
        ask(sock, "undo", phone=name)                 # the look
        assert looks(la) == before
    finally:
        _unlock()
        svc.terminate()
        try:
            svc.wait(10)
        except subprocess.TimeoutExpired:
            svc.kill()
        log.close()
        avd_hygiene.restore(saved, force=True, no_backup=True)
        snapshots.delete(saved)
        shutil.rmtree(run_dir, ignore_errors=True)
