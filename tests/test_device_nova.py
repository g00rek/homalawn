"""Nova import on the test AVD (HOMALAWN_SERIAL=emulator-5560), over adb and over the link, with
the synthetic backup (tests/fixtures/nova-synthetic.novabackup, built by make_nova_backup.py):
Lawnchair's real converter on the phone, the grid change, the write, and undo back to exactly
the layout before."""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent))
from test_device_seam import crash_log, kind, la, needs_avd, norm_layout  # noqa: E402,F401

pytestmark = [pytest.mark.device, needs_avd]
BACKUP = Path(__file__).parent / "fixtures" / "nova-synthetic.novabackup"


def _prefs(la):
    from homalawn import prefs
    cur = prefs.current(la.transport)
    return {k: cur["shared"].get(k) for k in ("pref_workspaceColumns", "pref_workspaceRows", "pref_hotseatColumns")}


def test_dry_run_changes_nothing(la):
    from homalawn import nova
    before, prefs0, db0 = norm_layout(la.get_layout()), _prefs(la), la.db_name()
    plan, snap, _ = nova.run(la, nova.slim_backup(BACKUP.read_bytes()), dry_run=True)
    assert snap is None
    assert {i.label for i in plan.placed} >= {"Chrome", "Settings", "Tools", "Calendar"}
    assert {i.label: i.reason for i in plan.dropped}["Missing App"] == nova.R_NOT_INSTALLED
    la.refresh()
    assert (norm_layout(la.get_layout()), _prefs(la), la.db_name()) == (before, prefs0, db0)


def test_import_then_undo_puts_back_exactly_what_was_there(la, d):
    from homalawn import nova, snapshots
    before, prefs0 = la.get_layout(), _prefs(la)
    d.shell("logcat", "-b", "crash", "-c")
    plan, snap, _ = nova.run(la, nova.slim_backup(BACKUP.read_bytes()), glance_row=True)
    assert snap and snap.startswith("nova-")
    la.refresh()
    assert la.db_name() == "launcher_6_4_4.db"  # 5 Nova rows + the At a Glance row, 4 columns, dock 4
    now = la.get_layout()
    dock = {i["x"]: i.get("package") for i in now["hotseat"]}
    assert dock == {0: "com.android.chrome", 1: "com.google.android.apps.messaging"}
    screen0 = {(i["x"], i["y"]): i for s in now["screens"] if s["index"] == 0 for i in s["items"]}
    assert screen0[(0, 2)]["package"] == "com.android.settings"  # Nova row 1, one down for the glance row
    assert screen0[(1, 2)]["type"] == "folder" and len(screen0[(1, 2)]["items"]) == 2
    assert {i.label for i in plan.placed if i.kind == "widget"} == {"Analog"}
    assert all(i.state in ("ready", "needs setup", "needs permission", "loading")
               for i in plan.placed if i.kind == "widget")
    la.undo()
    la.refresh()
    assert la.get_layout() == before  # ids included
    assert _prefs(la) == prefs0
    assert snap not in snapshots.list_()
    assert crash_log(d) == ""
