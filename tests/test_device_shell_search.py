"""The Search & gestures page against a real `homalawn serve` and the test AVD (HOMALAWN_SERIAL=emulator-5560).

The offscreen test shell (its own throwaway home; never the live shell) shows the page and drives it
through the harness: Swipe down is set to "Search (instant)" in the draft; nothing reaches the phone
until Apply, which writes it as one prefs snapshot (read back from Lawnchair's own settings as
kotlinx's {"type":"homarSearch"}); the header's Undo puts the setting back. Pictures go to
HOMALAWN_SEARCH_SHOTS (default /tmp/claude-1000/homalawn-search-shots)."""
import json
import os
import shutil
import time
from pathlib import Path

import pytest

from homalawn import prefs, snapshots
from homalawn.session import Launcher
from test_device_follow import THEMES
from test_device_shell_apply import Window

pytestmark = pytest.mark.device
SERIAL = os.environ.get("HOMALAWN_SERIAL")
if not SERIAL:
    pytest.skip("HOMALAWN_SERIAL not set", allow_module_level=True)

SHOTS = Path(os.environ.get("HOMALAWN_SEARCH_SHOTS", "/tmp/claude-1000/homalawn-search-shots"))
KEY = "swipe_down_gesture_handler"


class SearchWindow(Window):
    def open_search(self):
        assert self.harness("start", str(self.shell_dir), "--socket", str(self.sock)).returncode == 0
        for _ in range(150):
            if self.shell_log.exists() and "homalawn" in self.shell_log.read_text().lower():
                break
            time.sleep(0.2)
        time.sleep(3)
        self.harness("summon", str(self.shell_dir), "search")
        self.wait_for(lambda: self.search()["loaded"], "the page never read the phone's settings", tries=240)

    def search(self):
        return json.loads(self.call("searchFacts"))

    def edit(self, **e):
        return self.call("searchEdit", json.dumps(e))

    def shot(self, file):
        out = SHOTS / file
        out.unlink(missing_ok=True)
        self.call("snapshot", f"search:{out}")
        for _ in range(50):
            if out.exists():
                break
            time.sleep(0.2)
        time.sleep(0.5)


def stored(la):
    """The swipe-down setting as Lawnchair stores it (None: not set, Lawnchair's default)."""
    e = prefs.current(la.transport).get("datastore", {}).get(KEY)
    return e["value"] if e else None


@pytest.fixture
def phone(avd_hygiene, link_transport):
    if not shutil.which("qs") or not (THEMES / "tokyo-night" / "colors.toml").is_file():
        pytest.skip("needs quickshell (qs) and the tokyo-night Omarchy theme")
    SHOTS.mkdir(parents=True, exist_ok=True)
    la = Launcher(link_transport)
    la.refresh()
    la.wait_loaded()
    return la


def test_search_page_sets_swipe_down_to_instant_search_applies_and_undoes(phone, link_transport, tmp_path):
    la = phone
    w = SearchWindow(tmp_path, link_transport.label)
    try:
        w.wait_connected()
        base = w.ask("search.get", phone=w.name)
        if base["gestures"]["swipe_down"]["type"] == "homarSearch":
            pytest.skip("the AVD's swipe down already opens Search (instant)")
        assert "homarSearch" in [h["type"] for h in base["handlers"]]       # the build has patch 0015
        before = stored(la)
        undoable = snapshots.list_undoable(la.device_key())

        w.open_search()
        f = w.search()
        assert f["changes"] == 0 and f["saved"]["gestures"]["swipe_down"] == base["gestures"]["swipe_down"]["type"]
        assert w.edit(apply=True) == "refused"                                  # nothing to apply yet
        w.shot("loaded.png")

        # The draft only: the phone keeps its setting, no snapshot.
        assert w.edit(gesture={"swipe_down": "homarSearch"}) == "ok"
        assert w.edit(open="swipe_down") == "ok"
        w.shot("dropdown.png")
        w.edit(open="")
        f = w.search()
        assert f["changes"] == 1 and f["diff"] == {"gestures": {"swipe_down": "homarSearch"}, "search": {}}
        time.sleep(1)
        assert stored(la) == before and snapshots.list_undoable(la.device_key()) == undoable
        w.shot("draft.png")

        # Apply: one prefs snapshot; Lawnchair holds {"type":"homarSearch"}.
        assert w.edit(apply=True) == "started"
        w.wait_for(lambda: not w.search()["applying"], "Apply never finished", tries=240)
        f = w.search()
        assert f["changes"] == 0 and not f["applyError"], f
        now = snapshots.list_undoable(la.device_key())
        assert len(now) == len(undoable) + 1 and now[0].startswith("prefs-")
        assert json.loads(stored(la)) == {"type": "homarSearch"}
        assert w.ask("search.get", phone=w.name)["gestures"]["swipe_down"] == {"type": "homarSearch"}
        w.shot("applied.png")

        # Undo from the header: the setting as it was, and the page follows.
        assert w.call("undo") == "started"
        w.wait_for(lambda: stored(la) == before, "Undo never put the setting back", tries=240)
        w.wait_for(lambda: w.search()["saved"]["gestures"]["swipe_down"] == base["gestures"]["swipe_down"]["type"],
                   "the page did not follow the undo")
        assert snapshots.list_undoable(la.device_key()) == undoable
    finally:
        w.close()
