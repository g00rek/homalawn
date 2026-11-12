"""search.get / search.apply's ops: Lawnchair's gesture and search settings read as the Search &
gestures page shows them, and the page's draft written in one prefs snapshot that one undo puts back."""
import json

import pytest

from homalawn import ops, snapshots
from homalawn.adb import AdbTransport
from homalawn.session import PREFS_FILE
from test_prefs import StatefulDev


@pytest.fixture(autouse=True)
def setup(tmp_path, monkeypatch):
    monkeypatch.setenv("HOMALAWN_DATA", str(tmp_path / "data"))
    monkeypatch.setattr("time.sleep", lambda s: None)


def session(dev):
    s = ops.Session()
    s._t = AdbTransport(device=dev)
    return s


def test_unset_settings_read_as_lawnchairs_defaults():
    d = ops.search_get(session(StatefulDev())).data
    assert d["gestures"] == {"double_tap": {"type": "sleep"}, "swipe_up": {"type": "openAppDrawer"},
                             "swipe_down": {"type": "openNotificationdata"}, "home_press": {"type": "noOp"},
                             "back_press": {"type": "noOp"}}
    assert d["search"] == {"search_bar": True, "auto_keyboard": False, "apps": True, "web": True, "people": False,
                           "files": False, "settings": False, "history": False, "calculator": False,
                           "algorithm": "localSearch", "web_provider": "google"}
    assert {"type": "homarSearch", "label": "Search (instant)"} in d["handlers"]
    assert [h["type"] for h in d["handlers"]][:3] == ["noOp", "sleep", "recents"]
    assert d["deck"] is False
    assert [c["value"] for c in d["fields"]["choices"][1]["options"]] == ["google", "duckduckgo", "startpage", "kagi"]
    assert [g["label"] for g in d["fields"]["gestures"]] == ["Double tap", "Swipe up", "Swipe down", "Home button",
                                                             "Back button"]


def test_stored_values_are_read_back():
    dev = StatefulDev()
    dev.ds.update({
        "swipe_down_gesture_handler": {"type": "string", "value": '{"type":"homarSearch"}'},
        "double_tap_gesture_handler": {"type": "string", "value": "not json"},          # Lawnchair: the default
        "home_press_gesture_handler": {"type": "string",
                                       "value": '{"type":"openApp","appName":"Clock","target":{}}'},
        "hide_app_drawer_search_bar": {"type": "bool", "value": True},
        "web_suggestion_provider": {"type": "string", "value": "custom"},
        "enable_lawn_deck": {"type": "bool", "value": True}})
    dev.shared["pref_searchResultCalculator"] = {"type": "bool", "value": True}
    d = ops.search_get(session(dev)).data
    assert d["gestures"]["swipe_down"] == {"type": "homarSearch"}
    assert d["gestures"]["double_tap"] == {"type": "sleep"}
    assert d["gestures"]["home_press"] == {"type": "openApp", "app": "Clock"}
    assert d["search"]["search_bar"] is False and d["search"]["calculator"] is True
    assert d["search"]["web_provider"] == "custom"
    assert "custom" in [c["value"] for c in d["fields"]["choices"][1]["options"]]   # shown because it is set
    assert d["deck"] is True and "homarSearch" not in [h["type"] for h in d["handlers"]]


def test_apply_writes_only_changes_in_one_snapshot_and_undo_puts_them_back():
    dev = StatefulDev()
    s = session(dev)
    r = ops.search_apply(s, gestures={"swipe_down": "homarSearch", "swipe_up": "openAppDrawer"},
                         search={"calculator": True, "search_bar": False, "web_provider": "duckduckgo", "apps": True})
    snap = r.data["snapshot"]
    assert snap.startswith("prefs-") and "undo" in r.text
    assert snapshots.describe(snap) == {"kind": "search", "title": "Search & gestures: 4 settings"}
    assert sorted(r.data["changed"]) == ["hide_app_drawer_search_bar", "pref_searchResultCalculator",
                                         "swipe_down_gesture_handler", "web_suggestion_provider"]
    assert dev.ds["swipe_down_gesture_handler"] == {"type": "string", "value": '{"type":"homarSearch"}'}
    assert dev.ds["hide_app_drawer_search_bar"]["value"] is True
    assert dev.ds["web_suggestion_provider"]["value"] == "duckduckgo"
    assert dev.shared["pref_searchResultCalculator"]["value"] is True
    commits = [e for m, e in dev.log if m == "prefs_set_many"]
    assert len(commits) == 1 and len(commits[0]) == 4                      # one commit
    la = s.launcher()
    assert snapshots.list_undoable(la.device_key()) == [snap]                # one undo step
    assert "swipe_down_gesture_handler" not in json.loads(snapshots.load(snap)[PREFS_FILE])["datastore"]
    assert ops.search_get(s).data["gestures"]["swipe_down"] == {"type": "homarSearch"}
    la.undo()
    assert "swipe_down_gesture_handler" not in dev.ds and "pref_searchResultCalculator" not in dev.shared
    assert ops.search_get(s).data["gestures"]["swipe_down"] == {"type": "openNotificationdata"}


def test_nothing_different_sends_nothing_and_saves_no_snapshot():
    dev = StatefulDev()
    s = session(dev)
    r = ops.search_apply(s, gestures={"double_tap": "sleep"}, search={"apps": True, "algorithm": "localSearch"})
    assert r.data == {"snapshot": None, "changed": []}
    assert not [m for m, _ in dev.log if m == "prefs_set_many"]
    assert snapshots.list_() == []


def test_bad_names_and_values_are_all_located_and_nothing_is_written():
    dev = StatefulDev()
    dev.ds["enable_lawn_deck"] = {"type": "bool", "value": True}
    with pytest.raises(ops.OpError) as e:
        ops.search_apply(session(dev), gestures={"swipe_down": "homarSearch", "pinch": "sleep", "double_tap": "openApp",
                                                 "back_press": "fly"},
                         search={"apps": "yes", "web_provider": "custom", "colour": 1})
    assert e.value.code == ops.INVALID
    assert [p["setting"] for p in e.value.problems] == ["swipe_down", "pinch", "double_tap", "back_press", "apps",
                                                        "web_provider", "colour"]
    assert "Lawn deck" in e.value.problems[0]["message"]
    assert "picked on the phone" in e.value.problems[2]["message"]
    assert not [m for m, _ in dev.log if m == "prefs_set_many"] and snapshots.list_() == []


def test_set_prefs_refuses_grid_keys():
    dev = StatefulDev()
    with pytest.raises(ValueError, match="grid"):
        session(dev).launcher().set_prefs([("pref_workspaceColumns", "5")])
