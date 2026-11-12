"""The Wallpaper & style page's Apply / Discard and its Replace question, against a real
`homalawn serve` and the test AVD (HOMALAWN_SERIAL=emulator-5560).

The offscreen test shell (its own throwaway home; never the live shell) shows the page and drives it
through the harness.
- Edits (crop, a layer, a switch) reach nothing on the phone (no new look snapshot, same wallpaper);
  leaving the page and coming back keeps the draft; Discard puts the saved settings back; Apply sends
  the draft as exactly one look snapshot and the AVD wallpaper changes.
- With a wallpaper homalawn did not set (Android 15 does not let Lawnchair read it over the link), Apply
  asks "Replace current wallpaper?" (Cancel / Replace) instead of failing; Cancel leaves the phone alone;
  Replace replaces it, the look snapshot holds the settings only, and Undo puts the colours back.
Pictures go to HOMALAWN_APPLY_SHOTS (default /tmp/claude-1000/homalawn-apply-shots)."""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import pytest
from PIL import Image

from homalawn import lookconf, prefs as prefs_mod, snapshots
from homalawn.session import PREFS_FILE, WALLPAPER_FILE, Launcher, forget_wallpaper
from test_device_follow import THEMES, ask, looks

pytestmark = pytest.mark.device
SERIAL = os.environ.get("HOMALAWN_SERIAL")
if not SERIAL:
    pytest.skip("HOMALAWN_SERIAL not set", allow_module_level=True)

ROOT = Path(__file__).parent.parent
HARNESS = ROOT / "src" / "homalawn" / "shell" / "tools" / "test-offscreen.sh"
SHOTS = Path(os.environ.get("HOMALAWN_APPLY_SHOTS", "/tmp/claude-1000/homalawn-apply-shots"))


class Window:
    """A temp `homalawn serve` paired with the AVD, plus the offscreen test shell showing the page."""

    def __init__(self, tmp_path, name):
        self.name = name
        self.run_dir = Path(tempfile.mkdtemp(prefix="ha"))   # short: unix socket paths are capped
        self.sock = self.run_dir / "homalawnd.sock"
        self.env = dict(os.environ, HOMALAWN_SOCKET=str(self.sock), HOMALAWN_STATE_DIR=str(tmp_path / "state"),
                        HOMALAWN_OMARCHY_STATE=str(tmp_path / "current"))
        self.env.pop("XDG_RUNTIME_DIR", None)
        self.log = open(tmp_path / "serve.log", "w")
        self.svc = subprocess.Popen([sys.executable, "-c", "from homalawn import daemon; daemon.run()"],
                                    env=self.env, stdout=self.log, stderr=self.log)
        self.shell_dir = tmp_path / "shell"
        self.shell_log = self.shell_dir / "shell.log"

    def wait_connected(self):
        for _ in range(100):
            if self.sock.exists():
                break
            time.sleep(0.1)
        for _ in range(300):
            if any(p["name"] == self.name and p["connected"] for p in ask(self.sock, "phones")):
                return
            time.sleep(0.1)
        pytest.fail("the service never connected to the phone")

    def ask(self, method, **params):
        return ask(self.sock, method, **params)

    def harness(self, *args):
        return subprocess.run([str(HARNESS), *args], env=self.env, capture_output=True, text=True, timeout=60)

    def open_look(self):
        assert self.harness("start", str(self.shell_dir), "--socket", str(self.sock)).returncode == 0
        for _ in range(150):
            if self.shell_log.exists() and "homalawn" in self.shell_log.read_text().lower():
                break
            time.sleep(0.2)
        time.sleep(3)
        self.harness("summon", str(self.shell_dir), "look")
        self.wait_for(lambda: self.facts().get("look"), "the page never read the phone's look")

    def call(self, fn, arg=""):
        r = self.harness("call", str(self.shell_dir), fn, arg)
        assert r.returncode == 0, r.stdout + r.stderr
        return r.stdout.strip()

    def facts(self):
        return json.loads(self.call("lookFacts"))

    def shot(self, section, file):
        out = SHOTS / file
        out.unlink(missing_ok=True)
        self.call("snapshot", f"{section}:{out}")
        for _ in range(50):
            if out.exists():
                break
            time.sleep(0.2)
        time.sleep(0.5)

    def wait_for(self, check, what, tries=100):
        for _ in range(tries):
            got = check()
            if got:
                return got
            time.sleep(0.25)
        pytest.fail(what + "\n" + (self.shell_log.read_text()[-3000:] if self.shell_log.exists() else ""))

    def close(self):
        self.harness("stop", str(self.shell_dir))
        self.svc.terminate()
        try:
            self.svc.wait(10)
        except subprocess.TimeoutExpired:
            self.svc.kill()
        self.log.close()
        shutil.rmtree(self.run_dir, ignore_errors=True)


@pytest.fixture
def phone(avd_hygiene, link_transport, tmp_path):
    """The AVD with a plain wallpaper homalawn set (so it can be saved first); everything put back after:
    the window's look steps undone, the wallpaper restored."""
    if not shutil.which("qs") or not (THEMES / "catppuccin" / "colors.toml").is_file():
        pytest.skip("needs quickshell (qs) and the catppuccin Omarchy theme")
    SHOTS.mkdir(parents=True, exist_ok=True)
    d = avd_hygiene.transport.device
    avd_hygiene.refresh()
    avd_hygiene.wait_loaded()
    saved = avd_hygiene.backup_wallpaper(prefix="apply-test-saved-", target="both")
    la = Launcher(link_transport)
    la.refresh()
    la.wait_loaded()
    seed = tmp_path / "seed.png"
    Image.new("RGB", (400, 200), (30, 60, 200)).save(seed)
    la.set_wallpaper(str(seed), backup=False, target="both")
    pre_looks = looks(la)
    try:
        yield d, la
    finally:
        for _ in range(len(set(looks(la)) - set(pre_looks))):   # the colours and mode go back too
            try:
                la.undo(no_backup=True)
            except Exception:
                break
        avd_hygiene.restore(saved, force=True, no_backup=True)
        snapshots.delete(saved)


def test_window_edits_wait_for_apply(phone, link_transport, tmp_path):
    d, la = phone
    w = Window(tmp_path, link_transport.label)
    try:
        w.wait_connected()
        w.ask("look.set", phone=w.name, settings=lookconf.LookSettings(source="chosen", theme="Catppuccin").to_dict())
        base_looks = looks(la)
        base_wall = d.call("wallpaper_info")["system_id"]
        base = w.ask("look.get", phone=w.name)

        w.open_look()
        f = w.facts()
        assert f["changed"] is False and f["applyActive"] is False
        assert w.call("lookEdit", '{"apply":true}') == "refused"   # nothing to apply yet

        # Crop, a layer and a switch: only the draft changes.
        edit = json.dumps({"crop": {"home": 0.2}, "layer": {"home": 40}, "parallax": True})
        assert w.call("lookEdit", edit) == "ok"
        f = w.facts()
        assert f["changed"] is True and f["applyActive"] is True
        assert f["crops"]["home"] == pytest.approx(0.2) and f["layers"]["home"] == 40 and f["parallax"] is True
        time.sleep(3)                                            # the old auto-save waited 600 ms
        assert looks(la) == base_looks
        assert d.call("wallpaper_info")["system_id"] == base_wall
        assert w.ask("look.get", phone=w.name) == base
        w.shot("look", "draft-pending.png")

        # Away and back: the draft is still there, still unsent.
        w.shot("overview", "overview-while-drafting.png")
        w.shot("look", "draft-after-coming-back.png")
        w.wait_for(lambda: w.facts().get("look"), "the page did not come back")
        f = w.facts()
        assert f["changed"] is True and f["crops"]["home"] == pytest.approx(0.2) and f["layers"]["home"] == 40
        assert looks(la) == base_looks

        # Discard: back to the saved settings, nothing sent.
        assert w.call("lookEdit", '{"discard":true}') == "discarded"
        f = w.facts()
        assert f["changed"] is False and f["crops"]["home"] == pytest.approx(0.5)
        assert f["layers"]["home"] == 0 and f["parallax"] is False
        w.shot("look", "after-discard.png")
        assert looks(la) == base_looks

        # Edit again and Apply: one snapshot, a new wallpaper on the phone.
        w.call("lookEdit", edit)
        assert w.call("lookEdit", '{"apply":true}') == "started"
        w.wait_for(lambda: not w.facts()["applying"], "Apply never finished", tries=480)
        f = w.facts()
        assert f["changed"] is False, f
        assert len(looks(la)) == len(base_looks) + 1
        assert d.call("wallpaper_info")["system_id"] != base_wall
        now = w.ask("look.get", phone=w.name)
        assert now["wallpaper"]["home"]["layer"] == 40 and now["wallpaper"]["parallax"] is True
        w.shot("look", "after-apply.png")
    finally:
        w.close()


def accent(t):
    cur = prefs_mod.current(t)
    return (cur["datastore"].get("accent_color") or cur["shared"].get("accent_color") or {}).get("value")


def test_window_asks_before_overwriting_a_wallpaper_it_cannot_save(phone, link_transport, tmp_path):
    d, la = phone
    forget_wallpaper()   # as if the wallpaper had been picked on the phone: over the link it cannot be read
    w = Window(tmp_path, link_transport.label)
    try:
        w.wait_connected()
        w.ask("look.set", phone=w.name, settings=lookconf.LookSettings(source="off").to_dict())  # sends nothing
        base_looks = looks(la)
        base_wall = d.call("wallpaper_info")["system_id"]
        base_accent = accent(link_transport)
        w.open_look()

        # Apply a chosen theme: the service cannot save the wallpaper first, so the page asks.
        assert w.call("lookEdit", '{"theme":"Catppuccin"}') == "ok"
        assert w.call("lookEdit", '{"apply":true}') == "started"
        f = w.wait_for(lambda: (lambda f: f if f["askOverwrite"] else None)(w.facts()),
                       "the Replace question never showed", tries=480)
        assert f["errorCode"] == "wallpaper_unsaved" and f["canRetry"] is False
        assert looks(la) == base_looks and d.call("wallpaper_info")["system_id"] == base_wall
        w.shot("look", "overwrite-question.png")

        # Cancel: the question goes, the phone is left alone.
        assert w.call("overwrite", "no") == "started"
        assert w.facts()["askOverwrite"] is False
        w.shot("look", "overwrite-cancelled.png")
        assert looks(la) == base_looks and d.call("wallpaper_info")["system_id"] == base_wall

        # Another change and Apply asks again; Replace replaces the wallpaper without a backup.
        w.call("lookEdit", '{"layer":{"home":30}}')
        assert w.call("lookEdit", '{"apply":true}') == "started"
        w.wait_for(lambda: w.facts()["askOverwrite"], "the question did not come back", tries=480)
        assert w.call("overwrite", "yes") == "started"
        w.wait_for(lambda: not w.facts()["overwriting"], "Replace never finished", tries=480)
        f = w.facts()
        assert f["askOverwrite"] is False and f["errorCode"] is None, f
        new = [n for n in looks(la) if n not in base_looks]
        assert len(new) == 1
        held = set(snapshots.load(new[0]))
        assert PREFS_FILE in held and WALLPAPER_FILE not in held   # the settings, not the unsaved picture
        assert d.call("wallpaper_info")["system_id"] != base_wall
        assert accent(link_transport) != base_accent
        w.shot("look", "after-overwrite.png")

        # Undo from the window: the colours go back (the wallpaper had nothing to go back to).
        assert w.call("undo") == "started"
        w.wait_for(lambda: looks(la) == base_looks, "Undo never finished", tries=480)
        assert accent(link_transport) == base_accent
    finally:
        w.close()
