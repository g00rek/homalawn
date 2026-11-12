"""The window's Undo button against a real `homalawn serve` and the test AVD (HOMALAWN_SERIAL=emulator-5560).

A look is applied through the service, then the offscreen test shell (its own throwaway home; never
the live shell) loads the plugin, the Look tab is shown and the header's Undo is pressed through the
harness. The AVD wallpaper must return to what it was and the look's undo snapshot must be gone.
A picture of the header goes to .superpowers/sdd/2026-10-14-homar-omarchy-look/shots/look/header-undo.png."""
import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import pytest
from PIL import Image

from homalawn import lookconf, snapshots
from homalawn.session import Launcher
from test_device_follow import THEMES, ask, looks

pytestmark = pytest.mark.device
SERIAL = os.environ.get("HOMALAWN_SERIAL")
if not SERIAL:
    pytest.skip("HOMALAWN_SERIAL not set", allow_module_level=True)

ROOT = Path(__file__).parent.parent
HARNESS = ROOT / "src" / "homalawn" / "shell" / "tools" / "test-offscreen.sh"
SHOTS = ROOT / ".superpowers" / "sdd" / "2026-10-14-homar-omarchy-look" / "shots" / "look"


def test_window_undo_puts_the_phone_back(avd_hygiene, link_transport, tmp_path):
    if not shutil.which("qs") or not (THEMES / "catppuccin" / "colors.toml").is_file():
        pytest.skip("needs quickshell (qs) and the catppuccin Omarchy theme")
    d = avd_hygiene.transport.device
    avd_hygiene.refresh()
    avd_hygiene.wait_loaded()
    saved = avd_hygiene.backup_wallpaper(prefix="undo-test-saved-", target="both")
    la = Launcher(link_transport)
    la.refresh()
    la.wait_loaded()
    seed = tmp_path / "seed.png"
    Image.new("RGB", (400, 200), (30, 60, 200)).save(seed)
    la.set_wallpaper(str(seed), backup=False, target="both")

    run_dir = Path(tempfile.mkdtemp(prefix="hu"))
    sock = run_dir / "homalawnd.sock"
    env = dict(os.environ, HOMALAWN_SOCKET=str(sock), HOMALAWN_STATE_DIR=str(tmp_path / "state"),
               HOMALAWN_OMARCHY_STATE=str(tmp_path / "current"))
    env.pop("XDG_RUNTIME_DIR", None)
    log = open(tmp_path / "serve.log", "w")
    svc = subprocess.Popen([sys.executable, "-c", "from homalawn import daemon; daemon.run()"], env=env,
                           stdout=log, stderr=log)
    shell_dir = tmp_path / "shell"
    name = link_transport.label

    def harness(*args):
        return subprocess.run([str(HARNESS), *args], env=env, capture_output=True, text=True, timeout=60)

    try:
        for _ in range(100):
            if sock.exists():
                break
            time.sleep(0.1)
        for _ in range(300):
            if any(p["name"] == name and p["connected"] for p in ask(sock, "phones")):
                break
            time.sleep(0.1)
        else:
            pytest.fail("the service never connected to the phone")
        before_looks = looks(la)
        before = d.call("wallpaper_info")
        settings = lookconf.LookSettings(source="chosen", theme="Catppuccin").to_dict()
        ask(sock, "look.set", phone=name, settings=settings)
        applied = d.call("wallpaper_info")
        assert applied["system_id"] != before["system_id"]
        assert len(looks(la)) == len(before_looks) + 1

        assert harness("start", str(shell_dir), "--socket", str(sock)).returncode == 0
        shell_log = shell_dir / "shell.log"
        for _ in range(150):
            if shell_log.exists() and "homalawn" in shell_log.read_text().lower():
                break
            time.sleep(0.2)
        time.sleep(3)
        harness("summon", str(shell_dir), "look")
        time.sleep(3)
        SHOTS.mkdir(parents=True, exist_ok=True)
        shot = shell_dir / "shots" / "header.png"
        harness("call", str(shell_dir), "snapshot", f"look:{shot}")
        for _ in range(50):
            if shot.exists():
                break
            time.sleep(0.2)
        time.sleep(1)
        if shot.exists():
            Image.open(shot).crop((0, 0, 1180, 56)).save(SHOTS / "header-undo.png")
        r = harness("call", str(shell_dir), "undo")
        assert "started" in r.stdout, r.stdout + r.stderr + (shell_log.read_text() if shell_log.exists() else "")
        for _ in range(240):
            if d.call("wallpaper_info")["system_id"] != applied["system_id"]:
                break
            time.sleep(0.25)
        back = d.call("wallpaper_info")
        assert back["system_id"] != applied["system_id"]
        assert looks(la) == before_looks
    finally:
        harness("stop", str(shell_dir))
        svc.terminate()
        try:
            svc.wait(10)
        except subprocess.TimeoutExpired:
            svc.kill()
        log.close()
        avd_hygiene.restore(saved, force=True, no_backup=True)
        snapshots.delete(saved)
        shutil.rmtree(run_dir, ignore_errors=True)
