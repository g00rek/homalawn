"""The window's navigation against a real `homalawn serve` and the test AVD (HOMALAWN_SERIAL=emulator-5560).

The offscreen test shell (its own throwaway home; never the live shell) loads the plugin against a
temp service paired with the AVD. Every section is photographed with the rail collapsed and
expanded (into HOMALAWN_NAV_SHOTS, default /tmp/claude-1000/homalawn-nav-shots), the Overview must show
what the phone reports (paired date, address, Homa Lawn app version, permissions), and History's
Restore must put back one of the AVD's own snapshots (a pre-restore snapshot appears)."""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import pytest

from homalawn import snapshots
from homalawn.session import Launcher
from test_device_follow import THEMES, ask

pytestmark = pytest.mark.device
SERIAL = os.environ.get("HOMALAWN_SERIAL")
if not SERIAL:
    pytest.skip("HOMALAWN_SERIAL not set", allow_module_level=True)

ROOT = Path(__file__).parent.parent
HARNESS = ROOT / "src" / "homalawn" / "shell" / "tools" / "test-offscreen.sh"
SHOTS = Path(os.environ.get("HOMALAWN_NAV_SHOTS") or "/tmp/claude-1000/homalawn-nav-shots")
SECTIONS = ["overview", "home", "look", "search", "history", "add", "agents", "settings"]


def _wait(pred, seconds, step=0.2):
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        if pred():
            return True
        time.sleep(step)
    return False


def test_window_navigation_on_the_avd(avd_hygiene, link_transport, tmp_path):
    if not shutil.which("qs") or not (THEMES / "tokyo-night" / "colors.toml").is_file():
        pytest.skip("needs quickshell (qs) and the tokyo-night Omarchy theme")
    la = Launcher(link_transport)
    la.refresh()
    la.wait_loaded()
    mine = snapshots.auto_name().replace("auto-", "manual-")
    snapshots.save(mine, la._read_all(), device=la.device_key())   # the AVD as it is now

    run_dir = Path(tempfile.mkdtemp(prefix="hn"))   # short: unix socket paths are capped at 108 bytes
    sock = run_dir / "homalawnd.sock"
    env = dict(os.environ, HOMALAWN_SOCKET=str(sock), HOMALAWN_STATE_DIR=str(tmp_path / "state"),
               HOMALAWN_OMARCHY_STATE=str(tmp_path / "current"))
    env.pop("XDG_RUNTIME_DIR", None)
    log = open(tmp_path / "serve.log", "w")
    svc = subprocess.Popen([sys.executable, "-c", "from homalawn import daemon; daemon.run()"], env=env,
                           stdout=log, stderr=log)
    shell_dir = tmp_path / "shell"
    shell_log = shell_dir / "shell.log"
    name = link_transport.label

    def harness(*args):
        return subprocess.run([str(HARNESS), *args], env=env, capture_output=True, text=True, timeout=60)

    def logged(prefix):
        return [l.split("homalawn test: ", 1)[1] for l in (shell_log.read_text(errors="replace")
                if shell_log.exists() else "").splitlines() if f"homalawn test: {prefix}" in l]

    def shoot(section, file):
        harness("call", str(shell_dir), "snapshot", f"{section}:{file}")
        assert _wait(lambda: any(str(file) in l for l in logged("snapshot saved")), 20), logged("snapshot")

    try:
        assert _wait(sock.exists, 20)
        assert _wait(lambda: any(p["name"] == name and p["connected"] for p in ask(sock, "phones")), 60), \
            "the service never connected to the phone"

        info = ask(sock, "phone.info", phone=name)
        assert info["connected"] is True and info["paired"] and info["address"]
        assert info["app"]["versionName"] and info["permissions"]["default_home"] is True
        assert info["permissions"]["wallpaper_backup"] is None

        assert harness("start", str(shell_dir), "--socket", str(sock)).returncode == 0
        assert _wait(lambda: shell_log.exists() and "homalawn test: bar" in shell_log.read_text(errors="replace"), 30)
        harness("summon", str(shell_dir), "overview")
        time.sleep(2)

        SHOTS.mkdir(parents=True, exist_ok=True)
        for rail in ("closed", "open"):
            harness("call", str(shell_dir), "rail", rail)
            for section in SECTIONS:
                shoot(section, SHOTS / f"{section}-{rail}.png")
        harness("call", str(shell_dir), "rail", "closed")

        # Overview: what the window knows of the phone
        harness("call", str(shell_dir), "snapshot", "overview:" + str(SHOTS / "overview-info.png"))
        harness("call", str(shell_dir), "info")
        assert _wait(lambda: logged("info {"), 10)
        shown = json.loads(logged("info ")[-1][len("info "):])
        assert shown["name"] == name and shown["app"]["versionName"] == info["app"]["versionName"]
        assert shown["address"] == info["address"] and shown["paired"] == info["paired"]

        # History: the AVD's own snapshot is listed and restored from the window
        harness("call", str(shell_dir), "snapshot", "history:" + str(SHOTS / "history-before.png"))
        time.sleep(2)
        r = harness("call", str(shell_dir), "history", "rows")
        rows = json.loads(logged("history rows ")[-1][len("history rows "):])
        assert mine in [x["name"] for x in rows], (r.stdout, rows)
        assert all(x["device"] == la.device_key() for x in rows)
        before = {x["name"] for x in ask(sock, "snapshots", phone=name)["data"]}
        r = harness("call", str(shell_dir), "history", f"restore {mine}")
        assert "started" in r.stdout, r.stdout + r.stderr
        assert _wait(lambda: any(n.startswith("pre-restore-") and n not in before
                                 for n in (x["name"] for x in ask(sock, "snapshots", phone=name)["data"])), 120)
        assert _wait(lambda: svc_busy(sock) == [], 60)
        time.sleep(2)
        shoot("history", SHOTS / "history-after-restore.png")
    finally:
        harness("stop", str(shell_dir))
        svc.terminate()
        try:
            svc.wait(10)
        except subprocess.TimeoutExpired:
            svc.kill()
        log.close()
        for n in snapshots.list_():
            if n == mine or n.startswith("pre-restore-"):
                snapshots.delete(n)
        shutil.rmtree(run_dir, ignore_errors=True)


def svc_busy(sock):
    s = __import__("socket").socket(__import__("socket").AF_UNIX)
    s.settimeout(10)
    s.connect(str(sock))
    s.sendall(b'{"id": 1, "method": "subscribe"}\n')
    buf = b""
    while buf.count(b"\n") < 2:
        buf += s.recv(65536)
    s.close()
    state = next(json.loads(l)["state"] for l in buf.splitlines() if b'"event"' in l)
    return state["busy"]
