"""home.apply's write: the editor's draft (grid, dock, icon and label settings, layout) in one
snapshot, checked before anything changes, and one undo puts all of it back."""
import json
import sqlite3
import tempfile

import pytest

from homalawn import ops, prefs, snapshots
from homalawn.adb import AdbTransport
from homalawn.session import PREFS_FILE, LayoutInvalid, Launcher
from test_prefs import StatefulDev
from test_session import make_db


@pytest.fixture(autouse=True)
def setup(tmp_path, monkeypatch):
    monkeypatch.setenv("HOMALAWN_DATA", str(tmp_path / "data"))
    monkeypatch.setattr("time.sleep", lambda s: None)
    monkeypatch.setattr(prefs, "catalog", lambda: {
        "pref_workspaceRows": {"store": "shared", "type": "int"},
        "pref_workspaceColumns": {"store": "shared", "type": "int"},
        "pref_hotseatColumns": {"store": "shared", "type": "int"},
        "home_icon_size_factor": {"store": "datastore", "type": "float"},
        "home_icon_label_size_factor": {"store": "datastore", "type": "float"},
        "homar_label_gap_dp": {"store": "datastore", "type": "float"},
        "show_icon_labels_on_home_screen": {"store": "datastore", "type": "bool"}})


class Dev(StatefulDev):
    """StatefulDev (grid migration scrambles positions) with a full Lawnchair favorites table."""

    def __init__(self, tmp_path):
        super().__init__()
        p = tmp_path / "home.db"
        make_db(str(p), [("com.a", 1, 1), ("com.b", 2, 2)])
        self.files = {"databases/" + self.db(): p.read_bytes()}

    def _write(self, e):
        if e.get("type") == "float" and not e.get("remove"):
            self.ds[e["key"]] = {"type": "float", "value": float(e["value"])}
            return
        super()._write(e)


def positions(dev, grid):
    data = dev.files["databases/" + dev.db()]
    with tempfile.NamedTemporaryFile(suffix=".db") as f:
        f.write(data)
        f.flush()
        con = sqlite3.connect(f.name)
        try:
            return sorted(con.execute("select title, screen, cellX, cellY from favorites where container=-100"))
        finally:
            con.close()


def test_grid_settings_and_layout_in_one_snapshot_and_one_undo(tmp_path):
    dev = Dev(tmp_path)
    la = Launcher(AdbTransport(device=dev))
    lay = la.get_layout()
    lay["grid"] = {"cols": 5, "rows": 6}
    a, b = lay["screens"][0]["items"]
    a.update(x=4, y=5)
    b.update(x=0, y=3)
    snap, notes = la.apply_home(lay, (5, 6), 5, [("home_icon_size_factor", "1.3"), ("homar_label_gap_dp", "-4.0")])
    assert snap.startswith("home-") and notes == []
    assert snapshots.list_undoable(la.device_key()) == [snap]   # one undo step for all of it
    # History: 2 apps moved, columns, rows, dock, icon size, label gap
    assert snapshots.describe(snap) == {"kind": "home", "title": "Home screen: 7 changes"}
    files = snapshots.load(snap)
    assert set(files) == {PREFS_FILE, "launcher_5_4_4.db"}
    assert dev.db() == "launcher_6_5_5.db"
    assert dev.ds["home_icon_size_factor"]["value"] == 1.3 and dev.ds["homar_label_gap_dp"]["value"] == -4.0
    assert positions(dev, (5, 6)) == [("com.a", 0, 4, 5), ("com.b", 0, 0, 3)]   # not the migration's scramble
    name, _ = la.undo()
    assert name == snap
    assert dev.db() == "launcher_5_4_4.db"
    assert "home_icon_size_factor" not in dev.ds and "homar_label_gap_dp" not in dev.ds
    assert positions(dev, (4, 5)) == [("com.a", 0, 1, 1), ("com.b", 0, 2, 2)]


def test_only_settings_and_layout_keep_the_database(tmp_path):
    dev = Dev(tmp_path)
    la = Launcher(AdbTransport(device=dev))
    lay = la.get_layout()
    lay["screens"][0]["items"][0].update(x=3, y=4)
    la.apply_home(lay, None, None, [("home_icon_label_size_factor", "0.8")])
    assert dev.db() == "launcher_5_4_4.db"
    assert positions(dev, (4, 5))[0] == ("com.a", 0, 3, 4)
    assert dev.ds["home_icon_label_size_factor"]["value"] == 0.8


def test_a_bad_layout_changes_nothing_and_says_where(tmp_path):
    dev = Dev(tmp_path)
    la = Launcher(AdbTransport(device=dev))
    lay = la.get_layout()
    a, b = lay["screens"][0]["items"]
    b.update(x=1, y=1)                                    # on top of com.a
    lay["hotseat"] = [{"type": "app", "package": "com.h", "activity": ".H", "x": 4}]   # dock is 4 wide
    with pytest.raises(LayoutInvalid) as e:
        la.apply_home(lay, None, None, [("home_icon_size_factor", "1.1")])
    probs = e.value.problems
    assert {"where": "screen", "screen": 0, "x": 1, "y": 1, "id": b["id"]}.items() <= probs[0].items()
    assert "overlaps" in probs[0]["message"]
    assert probs[1]["where"] == "hotseat" and probs[1]["x"] == 4 and "dock" in probs[1]["message"]
    assert snapshots.list_() == [] and "home_icon_size_factor" not in dev.ds
    assert not [m for m, _ in dev.log if m in ("prefs_set_many", "layout_import")]


def test_a_wider_dock_takes_items_beyond_the_columns(tmp_path):
    dev = Dev(tmp_path)
    la = Launcher(AdbTransport(device=dev))
    lay = la.get_layout()
    lay["hotseat"] = [{"type": "app", "package": "com.h", "activity": ".H", "x": 4}]
    la.apply_home(lay, None, 5)
    assert dev.db() == "launcher_5_4_5.db"


def test_grid_and_dock_limits(tmp_path):
    dev = Dev(tmp_path)
    la = Launcher(AdbTransport(device=dev))
    lay = la.get_layout()
    with pytest.raises(ValueError, match="dock icons must be between 3 and 10"):
        la.apply_home(lay, None, 11)
    lay["grid"] = {"cols": 2, "rows": 5}
    with pytest.raises(ValueError, match="columns must be between 3"):
        la.apply_home(lay, (2, 5))
    lay["grid"] = {"cols": 4, "rows": 5}
    with pytest.raises(ValueError, match="does not match the grid it is applied with, 5x5"):
        la.apply_home(lay, (5, 5))


def test_editor_setting_names_and_ranges():
    assert ops._home_prefs({"icon_scale": 1.25, "label_scale": 0.5, "label_gap_dp": 4, "show_labels": False}) == [
        ("home_icon_size_factor", "1.25"), ("home_icon_label_size_factor", "0.5"), ("homar_label_gap_dp", "4.0"),
        ("show_icon_labels_on_home_screen", "false")]
    with pytest.raises(ops.OpError) as e:
        # the label gap is the visible distance from the icon's shape: 0 (touching) at the least
        ops._home_prefs({"icon_scale": 2, "label_gap_dp": -1, "colour": 1, "show_labels": "yes"})
    assert e.value.code == ops.INVALID
    assert [p["setting"] for p in e.value.problems] == ["icon_scale", "label_gap_dp", "colour", "show_labels"]


def test_op_error_carries_the_located_problems(tmp_path, monkeypatch):
    dev = Dev(tmp_path)
    s = ops.Session()
    s._t = AdbTransport(device=dev)
    monkeypatch.setattr(ops, "_known", lambda la: None)
    lay = s.launcher().get_layout()
    lay["screens"][0]["items"][1].update(x=1, y=1)
    with pytest.raises(ops.OpError) as e:
        ops.home_apply(s, lay)
    assert e.value.code == ops.INVALID and e.value.problems[0]["where"] == "screen"
    lay["screens"][0]["items"][1].update(x=3, y=3)
    r = ops.home_apply(s, lay, look={"icon_scale": 1.1})
    assert r.data["snapshot"].startswith("home-") and "undo" in r.text
    assert json.loads(snapshots.load(r.data["snapshot"])[PREFS_FILE])["shared"]["pref_workspaceColumns"]["value"] == 4
