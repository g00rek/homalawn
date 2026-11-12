"""The Home screen page against a real `homalawn serve` and the test AVD (HOMALAWN_SERIAL=emulator-5560).

The offscreen test shell (its own throwaway home; never the live shell) shows the page and drives it
through the harness: an app from the list goes on a free cell, a second app dropped on it makes a
folder; nothing reaches the phone until Apply, which writes the draft as one home snapshot (the AVD's
layout then holds the folder with both apps); the header's Undo puts the home screen back. The
service's icon cache lives in a temporary XDG_CACHE_HOME. Pictures go to HOMALAWN_HOME_SHOTS
(default /tmp/claude-1000/homalawn-home-shots)."""
import json
import os
import shutil
import time
from pathlib import Path

import pytest

from homalawn import snapshots
from homalawn.session import Launcher
from test_device_follow import THEMES
from test_device_shell_apply import Window

pytestmark = pytest.mark.device
SERIAL = os.environ.get("HOMALAWN_SERIAL")
if not SERIAL:
    pytest.skip("HOMALAWN_SERIAL not set", allow_module_level=True)

SHOTS = Path(os.environ.get("HOMALAWN_HOME_SHOTS", "/tmp/claude-1000/homalawn-home-shots"))


class HomeWindow(Window):
    def open_home(self):
        assert self.harness("start", str(self.shell_dir), "--socket", str(self.sock)).returncode == 0
        for _ in range(150):
            if self.shell_log.exists() and "homalawn" in self.shell_log.read_text().lower():
                break
            time.sleep(0.2)
        time.sleep(3)
        self.harness("summon", str(self.shell_dir), "home")
        self.wait_for(lambda: (lambda f: f["loaded"] and f["apps"] > 0)(self.home()),
                      "the page never read the phone's home screen and apps", tries=240)

    def home(self):
        return json.loads(self.call("homeFacts"))

    def edit(self, **e):
        return self.call("homeEdit", json.dumps(e))

    def shot(self, file):
        out = SHOTS / file
        out.unlink(missing_ok=True)
        self.call("snapshot", f"home:{out}")
        for _ in range(50):
            if out.exists():
                break
            time.sleep(0.2)
        time.sleep(0.5)


def components(layout, screen):
    """Each item of one screen as (type, x, y, [components of a folder's apps] or component)."""
    def comp(i):
        act = i["activity"] or ""
        return i["package"] + "/" + (i["package"] + act if act.startswith(".") else act)
    for s in layout["screens"]:
        if s["index"] == screen:
            return sorted((i["type"], i["x"], i["y"],
                           sorted(comp(k) for k in i["items"]) if i["type"] == "folder"
                           else comp(i) if i["type"] == "app" else i.get("provider"))
                          for i in s["items"])
    return []


def free_cell(home, screen=0):
    cols, rows = home["grid"]
    taken = {(i["x"], i["y"]) for s in home["screens"] if s["index"] == screen for i in s["items"]}
    for y in range(1, rows):          # row 0 of the first screen may hold the search bar
        for x in range(cols):
            if (x, y) not in taken:
                return x, y
    pytest.fail("no free cell on screen 0")


@pytest.fixture
def phone(avd_hygiene, link_transport):
    if not shutil.which("qs") or not (THEMES / "tokyo-night" / "colors.toml").is_file():
        pytest.skip("needs quickshell (qs) and the tokyo-night Omarchy theme")
    SHOTS.mkdir(parents=True, exist_ok=True)
    la = Launcher(link_transport)
    la.refresh()
    la.wait_loaded()
    return la


def test_home_page_places_an_app_makes_a_folder_applies_and_undoes(phone, link_transport, tmp_path, monkeypatch):
    la = phone
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))   # the service's icon cache
    w = HomeWindow(tmp_path, link_transport.label)
    try:
        w.wait_connected()
        base = w.ask("home.get", phone=w.name)
        base_screen = components(base, 0)
        undoable = snapshots.list_undoable(la.device_key())
        on_phone = {a["component"] for a in w.ask("home.apps", phone=w.name)}
        first, second = sorted(on_phone)[:2]
        x, y = free_cell(base)

        w.open_home()
        f = w.home()
        assert f["changes"] == 0 and f["icons"] > 0
        assert w.edit(apply=True) == "refused"                  # nothing to apply yet
        w.shot("loaded.png")

        # An app from the list onto a free cell, a second one onto it: a folder.
        assert w.edit(drop={"src": {"app": first}, "target": {"cell": [0, x, y]}}) == "ok"
        assert w.edit(drop={"src": {"app": second}, "target": {"cell": [0, x, y]}}) == "ok"
        f = w.home()
        made = [i for s in f["screens"] if s["index"] == 0 for i in s["items"] if (i["x"], i.get("y")) == (x, y)]
        assert made and made[0]["type"] == "folder" and made[0]["title"] == "Folder"
        assert sorted(k["component"] for k in made[0]["items"]) == sorted([first, second])
        assert f["changes"] == 3
        # A drop where it cannot go is refused and changes nothing.
        if base["top_row_reserved"]:
            assert w.edit(drop={"src": {"app": first}, "target": {"cell": [0, 0, 0]}}).startswith("refused")
        w.edit(open=[0, x, y])
        w.shot("folder.png")
        w.edit(close=True)
        time.sleep(2)
        assert components(w.ask("home.get", phone=w.name), 0) == base_screen       # the draft only
        assert snapshots.list_undoable(la.device_key()) == undoable

        # Apply: one home snapshot; the AVD's layout holds the folder with both apps.
        assert w.edit(apply=True) == "started"
        w.wait_for(lambda: not w.home()["applying"], "Apply never finished", tries=480)
        f = w.home()
        assert f["changes"] == 0 and not f["problems"] and not f["applyError"], f
        now = snapshots.list_undoable(la.device_key())
        assert len(now) == len(undoable) + 1 and now[0].startswith("home-")
        expect = sorted(base_screen + [("folder", x, y, sorted([first, second]))])
        assert components(w.ask("home.get", phone=w.name), 0) == expect
        la.refresh()
        assert components(la.get_layout(), 0) == expect
        w.shot("applied.png")

        # Undo from the header: the home screen as it was.
        assert w.call("undo") == "started"
        w.wait_for(lambda: components(w.ask("home.get", phone=w.name), 0) == base_screen,
                   "Undo never put the home screen back", tries=480)
        w.wait_for(lambda: w.home()["changes"] == 0 and not any(
            (i["x"], i.get("y")) == (x, y) for s in w.home()["screens"] if s["index"] == 0 for i in s["items"]),
            "the page did not follow the undo")
        assert snapshots.list_undoable(la.device_key()) == undoable
    finally:
        w.close()
