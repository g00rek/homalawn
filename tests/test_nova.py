"""Nova import on the computer: backup slimming, the account of every item, the summary, the
dock-first grid write. The phone's converter is stood in for by a copy of what it does (the
synthetic backup's rows, desktop rows one row down for the At a Glance row); the real converter
runs in tests/test_device_nova.py."""
from __future__ import annotations

import io
import sqlite3
import sys
import zipfile
from pathlib import Path

import pytest

from homalawn import nova
from homalawn.layout import CONTAINER_DESKTOP, CONTAINER_HOTSEAT

FIX = Path(__file__).parent / "fixtures"
sys.path.insert(0, str(FIX))
import make_nova_backup  # noqa: E402

LAUNCHER_SCHEMA = ("CREATE TABLE favorites (_id INTEGER PRIMARY KEY, title TEXT, intent TEXT, container INTEGER, "
                   "screen INTEGER, cellX INTEGER, cellY INTEGER, spanX INTEGER, spanY INTEGER, itemType INTEGER, "
                   "appWidgetId INTEGER NOT NULL DEFAULT -1, icon BLOB, appWidgetProvider TEXT, modified INTEGER "
                   "NOT NULL DEFAULT 0, restored INTEGER NOT NULL DEFAULT 0, profileId INTEGER DEFAULT 0, rank "
                   "INTEGER NOT NULL DEFAULT 0, options INTEGER NOT NULL DEFAULT 0, appWidgetSource INTEGER NOT "
                   "NULL DEFAULT -1)")

APPS = [{"package": p, "activity": "x", "label": l} for p, l in [
    ("com.android.chrome", "Chrome"), ("com.google.android.apps.messaging", "Messages"),
    ("com.google.android.dialer", "Phone"), ("com.android.settings", "Settings"),
    ("com.google.android.contacts", "Contacts"), ("com.google.android.deskclock", "Clock"),
    ("com.google.android.calendar", "Calendar"), ("com.google.android.apps.maps", "Maps"),
    ("com.google.android.apps.photos", "Photos")]]
WIDGETS = [{"provider": "com.google.android.deskclock/com.android.alarmclock.AnalogAppWidgetProvider",
            "label": "Analog"},
           {"provider": "com.android.chrome/org.chromium.chrome.browser.quickactionsearchwidget."
                        "QuickActionSearchWidgetProvider$QuickActionSearchWidgetProviderDino", "label": "Chrome Dino"}]
META = {"columns": 4, "nova_rows": 5, "rows": 6, "hotseat": 4, "glance_row": True, "subgrid": False,
        "icon_pack": None}


def fake_convert(backup: bytes, cols=4, rows=6, hot=4, glance=True) -> bytes:
    """What NovaBackupConverter.insertNovaItems keeps: home rows only, rounded, desktop rows one
    down with the glance row, anything off the grid or past the dock left out."""
    nova_db = zipfile.ZipFile(io.BytesIO(backup)).read("nova.db")
    src = sqlite3.connect(":memory:")
    src.deserialize(nova_db)
    out = sqlite3.connect(":memory:")
    out.execute(LAUNCHER_SCHEMA)
    for r in src.execute("select _id,title,intent,container,screen,cellX,cellY,spanX,spanY,itemType,"
                         "appWidgetProvider from favorites where itemType != -1"):
        rid, title, intent, c, screen, x, y, w, h, t, prov = r
        if not (c in (CONTAINER_DESKTOP, CONTAINER_HOTSEAT) or c >= 0):
            continue
        x, y, w, h = round(x), round(y), max(1, round(w)), max(1, round(h))
        if c == CONTAINER_DESKTOP and glance:
            y += 1
        if c == CONTAINER_HOTSEAT:
            screen = x
        if c == CONTAINER_DESKTOP and (x + w > cols or y + h > rows):
            continue
        if c == CONTAINER_HOTSEAT and x >= cols:
            continue
        out.execute("insert into favorites (_id,title,intent,container,screen,cellX,cellY,spanX,spanY,itemType,"
                    "appWidgetProvider) values (?,?,?,?,?,?,?,?,?,?,?)",
                    (rid, title, intent, c, screen, x, 0 if c == CONTAINER_HOTSEAT else y, w, h, t, prov))
    out.commit()
    return out.serialize()


@pytest.fixture(scope="module")
def backup() -> bytes:
    return make_nova_backup.build()


@pytest.fixture
def plan(backup):
    return nova.build_plan(backup, META, fake_convert(backup), APPS, WIDGETS, (4, 5, 4))


def reasons(plan) -> dict[str, str]:
    return {i.label: i.reason for i in plan.dropped}


def test_slim_backup_keeps_only_what_the_converter_reads(backup):
    z = zipfile.ZipFile(io.BytesIO(nova.slim_backup(backup)))
    assert sorted(z.namelist()) == ["nova.db", "nova.xml"]


@pytest.mark.parametrize("data,msg", [(b"not a zip", "is not one"), (None, "nova.db missing")])
def test_slim_backup_refuses_what_is_not_a_nova_backup(data, msg):
    if data is None:
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as z:
            z.writestr("nova.xml", "<map/>")
        data = buf.getvalue()
    with pytest.raises(nova.NovaError, match=msg):
        nova.slim_backup(data)


def test_drawer_groups_are_not_home_screen_items(backup):
    ids = {r["_id"] for r in nova.nova_rows(backup)}
    assert 20 not in ids  # Gmail sits only in an app drawer tab
    assert {1, 11, 12, 21} <= ids


def test_every_item_is_placed_or_left_out_with_its_reason(plan):
    assert reasons(plan) == {
        "Apps": nova.R_NOVA_ONLY,
        "Phone": nova.R_DOCK,
        "Missing App": nova.R_NOT_INSTALLED,
        "BigWidget": nova.R_WIDGET_APP,
        "Maps": "its place is taken by Calendar",
        "Chrome Dino": nova.R_GRID,
        "Orphan Notes": nova.R_NO_FOLDER,
    }
    assert sorted(i.label for i in plan.placed) == ["Analog", "Calendar", "Chrome", "Clock", "Contacts",
                                                    "Messages", "Photos", "Settings", "Tools"]


def test_rows_left_out_are_not_written_and_widgets_get_a_fresh_id(plan):
    con = sqlite3.connect(":memory:")
    con.deserialize(plan.db)
    ids = {r[0] for r in con.execute("select _id from favorites")}
    assert ids == {i.id for i in plan.placed}
    assert con.execute("select appWidgetId, restored from favorites where _id=15").fetchone() == (-1, 1)


def test_a_folder_of_missing_apps_goes_and_a_folder_of_one_is_unfolded(backup):
    apps = [a for a in APPS if a["package"] != "com.google.android.contacts"]
    p = nova.build_plan(backup, META, fake_convert(backup), apps, WIDGETS, (4, 5, 4))
    assert [i.label for i in p.unfolded] == ["Tools"]
    gone = [a for a in apps if a["package"] != "com.google.android.deskclock"]
    p = nova.build_plan(backup, META, fake_convert(backup), gone, WIDGETS, (4, 5, 4))
    assert reasons(p)["Tools"] == nova.R_EMPTY_FOLDER


def test_without_the_glance_row_the_first_row_is_free(backup):
    meta = {**META, "rows": 5, "glance_row": False}
    p = nova.build_plan(backup, meta, fake_convert(backup, rows=5, glance=False), APPS, WIDGETS, (4, 5, 4))
    assert p.grid == (4, 5, 4) and not p.glance_row
    assert "Settings" in [i.label for i in p.placed]


def test_backup_without_a_grid_keeps_the_phones(backup):
    meta = {**META, "columns": None, "rows": None}
    p = nova.build_plan(backup, meta, fake_convert(backup), APPS, WIDGETS, (5, 6, 5))
    assert p.grid == (5, 6, 5) and not p.grid_from_backup


def test_summary_lists_dropped_apps_by_label_and_the_widgets_waiting(plan):
    dry = "\n".join(nova.summary(plan, dry_run=True))
    assert "9 items would be placed" in dry and "7 left out" in dry
    assert "not installed (1): Missing App (folder Tools)" in dry
    assert "Grid: 4 columns x 6 rows, dock 4 (an extra top row for At a Glance)" in dry
    for i in plan.placed:
        if i.kind == "widget":
            i.state = "needs permission"
    real = "\n".join(nova.summary(plan, dry_run=False, snap="nova-20261014-120000"))
    assert "1 widget waits for your tap on the phone: tap it, then Create on Android's dialog: Analog" in real
    assert "`homalawn undo` puts it back" in real


def test_repeated_names_are_counted(backup, plan):
    for i in plan.items:
        if i.label in ("Apps", "Phone"):
            i.status, i.reason, i.label = "dropped", "same", "Twin"
    assert "same (2): Twin x2" in "\n".join(nova.summary(plan, dry_run=True))


def test_grid_beyond_lawnchairs_limit_is_refused():
    with pytest.raises(nova.NovaError, match="--no-glance-row"):
        nova.check_grid({"shared": {}}, (5, 11, 5))
    nova.check_grid({"shared": {"pref_workspace_increase_max_grid_size": {"value": True}}}, (5, 11, 5))


def test_dock_size_is_written_alone_before_columns_and_rows(monkeypatch):
    from homalawn import session
    sent = []

    class T:
        has_shell = False
        label = "t"

        def call(self, method, params=None):
            if method == "launcher.prefs_set_many":
                sent.append([w["key"] for w in params["writes"]])
            return {}
    la = session.Launcher(T())
    monkeypatch.setattr(la, "_grid3", lambda: (4, 5, 4))
    waited = []
    monkeypatch.setattr(la, "wait_loaded", lambda expect_db=None, timeout=0: waited.append(expect_db))
    monkeypatch.setattr(session.time, "sleep", lambda s: None)
    la._send_prefs([("shared", "pref_workspaceColumns", "int", "5"), ("shared", "pref_hotseatColumns", "int", "5")],
                   expect_db="launcher_9_5_5.db")
    assert sent == [["pref_hotseatColumns"], ["pref_workspaceColumns"]]
    assert waited == ["launcher_5_4_5.db", "launcher_9_5_5.db"]


def test_a_file_that_is_not_a_backup_is_refused_before_the_phone_is_asked(tmp_path):
    from homalawn import ops
    f = tmp_path / "x.novabackup"
    f.write_bytes(b"nope")
    s = ops.Session()
    with pytest.raises(ops.OpError, match="not a Nova backup"):
        ops.nova_import(s, str(f), dry_run=True)
    assert s._t is None  # no phone was chosen, let alone touched


# -- run(): refusals change nothing, a failed write is put back ------------------------------------
class FakeLa:
    """Just enough of session.Launcher for nova.run; records every change."""

    def __init__(self, fail_write=False, fail_restore=False):
        self.did: list[str] = []
        self.fail_write, self.fail_restore = fail_write, fail_restore
        self.transport = object()

    def wait_loaded(self, *a, **k):
        pass

    def _grid3(self):
        return (4, 5, 4)

    def _snapshot(self, prefix, cur, with_db):
        self.did.append("snapshot")
        return prefix + "20261014-120000"

    def _send_prefs(self, writes, removals=(), expect_db=None):
        self.did.append(f"prefs {expect_db}")

    def _write_db(self, data, rollback=None, recover_from=None):
        self.did.append("write")
        if self.fail_write:
            raise RuntimeError("phone said no")
        return []

    def restore(self, name, force=False, no_backup=False):
        self.did.append(f"restore {name}")
        if self.fail_restore:
            raise RuntimeError("restore broke too")


@pytest.fixture
def phone(monkeypatch, backup):
    from homalawn import apps, widgets
    state = {"meta": dict(META), "pins": []}

    def convert(la, b, glance_row, pin):
        state["pins"].append(pin)
        return state["meta"], fake_convert(backup)
    monkeypatch.setattr(nova, "convert", convert)
    monkeypatch.setattr(nova.prefs_mod, "current", lambda t: {"shared": {}, "datastore": {}})
    monkeypatch.setattr(nova.prefs_mod, "resolve", lambda t, k, v, cur=None: ("shared", k, "int", v))
    monkeypatch.setattr(apps, "list_apps", lambda t: APPS)
    monkeypatch.setattr(widgets, "list_providers", lambda t: WIDGETS)
    monkeypatch.setattr(nova, "_settle", lambda la, plan: None)
    deleted = []
    monkeypatch.setattr(nova.snapshots, "delete", deleted.append)
    state["deleted"] = deleted
    return state


def test_a_refusal_changes_nothing_and_rolls_nothing_back(phone, backup):
    phone["meta"] = {**META, "rows": 11}  # beyond Lawnchair's 10 rows
    la = FakeLa()
    with pytest.raises(nova.NovaError, match="rows"):
        nova.run(la, backup)
    assert la.did == [] and phone["pins"] == [False]


def test_a_dock_lawnchair_cannot_have_is_refused(phone, backup):
    phone["meta"] = {**META, "hotseat": 0}
    with pytest.raises(nova.NovaError, match="dock has 0 places"):
        nova.run(FakeLa(), backup)


def test_dry_run_takes_no_snapshot(phone, backup):
    la = FakeLa()
    plan, snap, _ = nova.run(la, backup, dry_run=True)
    assert snap is None and la.did == [] and len(plan.placed) == 9


def test_import_snapshots_first_then_settings_then_rows(phone, backup):
    phone["meta"] = {**META, "hotseat": 5}
    la = FakeLa()
    plan, snap, _ = nova.run(la, backup)
    assert la.did == ["snapshot", "prefs launcher_6_4_5.db", "write"]
    assert snap == "nova-20261014-120000"


def test_deep_shortcuts_are_pinned_only_once_the_import_goes_ahead(phone, backup):
    phone["meta"] = {**META, "deep_shortcuts": 2}
    nova.run(FakeLa(), backup)
    assert phone["pins"] == [False, True]


def test_a_failed_write_puts_the_snapshot_back(phone, backup):
    la = FakeLa(fail_write=True)
    told = []
    with pytest.raises(RuntimeError, match="phone said no"):
        nova.run(la, backup, notify=told.append)
    assert la.did[-1] == "restore nova-20261014-120000"
    assert phone["deleted"] == ["nova-20261014-120000"]
    assert told == ["The import failed; putting the previous home screen back."]


def test_a_failed_put_back_keeps_the_snapshot_and_says_how_to_recover(phone, backup):
    la = FakeLa(fail_write=True, fail_restore=True)
    told = []
    with pytest.raises(RuntimeError, match="phone said no"):
        nova.run(la, backup, notify=told.append)
    assert phone["deleted"] == []
    assert "homalawn snapshot restore nova-20261014-120000" in told[-1]


def test_negative_cells_are_outside_the_grid(backup):
    conv = sqlite3.connect(":memory:")
    conv.deserialize(fake_convert(backup))
    conv.execute("update favorites set cellX=-1 where _id=17")  # Calendar
    conv.commit()
    p = nova.build_plan(backup, META, conv.serialize(), APPS, WIDGETS, (4, 5, 4))
    r = reasons(p)
    assert r["Calendar"] == nova.R_GRID and "Maps" not in r  # Maps keeps the cell Calendar never held


def test_a_damaged_nova_db_is_one_plain_line():
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("nova.xml", "<map/>")
        z.writestr("nova.db", b"SQLite format 3\x00" + b"\x00" * 200)
    with pytest.raises(nova.NovaError, match="damaged"):
        nova.slim_backup(buf.getvalue())
