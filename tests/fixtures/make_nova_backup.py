"""Build tests/fixtures/nova-synthetic.novabackup: a small, made-up Nova Launcher backup.

Every label and package that is not a stock emulator app is invented. The layout covers each
way an item can end up: placed (dock, screens, a folder, a widget), and left out because it is
outside the grid, beyond the dock, not installed, a Nova-only action, a widget whose app is
missing, in a folder the backup does not have, or on a cell another item already holds.

    uv run python tests/fixtures/make_nova_backup.py
"""
from __future__ import annotations

import io
import sqlite3
import sys
import tempfile
import zipfile
from pathlib import Path

# Nova's favorites columns (nova.db, Nova 8); positions are REAL in Nova
COLUMNS = ("_id INTEGER PRIMARY KEY, title TEXT, intent TEXT, container INTEGER, screen INTEGER, cellX REAL, "
           "cellY REAL, spanX REAL, spanY REAL, itemType INTEGER, appWidgetId INTEGER NOT NULL DEFAULT -1, "
           "iconPackage TEXT, iconResource TEXT, icon BLOB, appWidgetProvider TEXT, modified INTEGER NOT NULL "
           "DEFAULT 0, restored INTEGER NOT NULL DEFAULT 0, profileId INTEGER DEFAULT 0, rank INTEGER NOT NULL "
           "DEFAULT 0, options INTEGER NOT NULL DEFAULT 0, appWidgetSource INTEGER NOT NULL DEFAULT -1, "
           "customIconSource TEXT, customIconLoadedState INTEGER, flingUpIntent TEXT, flingDownIntent TEXT, "
           "zOrder INTEGER, novaFlags INTEGER")

DESK, DOCK = -100, -101
APP, SHORTCUT, FOLDER, WIDGET = 0, 1, 2, 4


def app(pkg: str, act: str) -> str:
    return ("#Intent;action=android.intent.action.MAIN;category=android.intent.category.LAUNCHER;"
            f"launchFlags=0x10200000;component={pkg}/{act};end")


NOVA_ACTION = ("#Intent;action=com.teslacoilsw.launcher.ACTION;launchFlags=0x10000000;"
               "component=com.teslacoilsw.launcher/.NovaShortcutHandler;S.LAUNCHER_ACTION=APP_DRAWER;end")

# (_id, title, intent, container, screen, cellX, cellY, spanX, spanY, itemType, appWidgetProvider)
ROWS = [
    (1, "Chrome", app("com.android.chrome", "com.google.android.apps.chrome.Main"), DOCK, 0, 0, 0, 1, 1, APP, None),
    (2, "Messages", app("com.google.android.apps.messaging", ".ui.ConversationListActivity"), DOCK, 0, 1, 0, 1, 1,
     APP, None),
    (3, "Apps", NOVA_ACTION, DOCK, 1, 2, 0, 1, 1, SHORTCUT, None),
    (4, "Phone", app("com.google.android.dialer", ".extensions.GoogleDialtactsActivity"), DOCK, 0, 4, 0, 1, 1, APP,
     None),
    (10, "Settings", app("com.android.settings", ".Settings"), DESK, 0, 0, 1, 1, 1, APP, None),
    (11, "Tools", None, DESK, 0, 1, 1, 1, 1, FOLDER, None),
    (12, "Contacts", app("com.google.android.contacts", "com.android.contacts.activities.PeopleActivity"), 11, 0, 0,
     0, 1, 1, APP, None),
    (13, "Clock", app("com.google.android.deskclock", "com.android.deskclock.DeskClock"), 11, 0, 1, 0, 1, 1, APP,
     None),
    (14, "Missing App", app("com.example.missing", ".Main"), 11, 0, 2, 0, 1, 1, APP, None),
    (15, None, None, DESK, 1, 0, 0, 2, 2, WIDGET, "com.google.android.deskclock/com.android.alarmclock.AnalogAppWidgetProvider"),
    (16, None, None, DESK, 1, 2, 0, 2, 2, WIDGET, "com.example.weather/com.example.weather.BigWidget"),
    (17, "Calendar", app("com.google.android.calendar", "com.android.calendar.AllInOneActivity"), DESK, 1, 0, 2, 1, 1,
     APP, None),
    (18, "Maps", app("com.google.android.apps.maps", "com.google.android.maps.MapsActivity"), DESK, 1, 0, 2, 1, 1,
     APP, None),
    (19, None, None, DESK, 1, 3, 3, 2, 2, WIDGET,
     "com.android.chrome/org.chromium.chrome.browser.quickactionsearchwidget.QuickActionSearchWidgetProvider"
     "$QuickActionSearchWidgetProviderDino"),
    (20, "Gmail", app("com.google.android.gm", ".ConversationListActivityGmail"), -202, 0, 0, 0, 1, 1, APP, None),
    (21, "Orphan Notes", app("com.example.notes", ".Main"), 999, 0, 0, 0, 1, 1, APP, None),
    (22, "Photos", app("com.google.android.apps.photos", ".home.HomeActivity"), DESK, 2, 3, 4, 1, 1, APP, None),
]

NOVA_XML = """<?xml version='1.0' encoding='utf-8' standalone='yes' ?>
<map>
    <string name="desktop_grid">5x4</string>
    <int name="dock_grid_cols" value="4" />
    <string name="theme_icon_pack">:BUILTIN::null</string>
</map>
"""


def build() -> bytes:
    with tempfile.TemporaryDirectory() as d:
        db = Path(d) / "nova.db"
        con = sqlite3.connect(db)
        con.execute(f"CREATE TABLE favorites ({COLUMNS})")
        con.executemany("INSERT INTO favorites (_id, title, intent, container, screen, cellX, cellY, spanX, spanY, "
                        "itemType, appWidgetProvider) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                        [tuple(float(v) if i in (5, 6, 7, 8) else v for i, v in enumerate(r)) for r in ROWS])
        con.commit()
        con.close()
        out = io.BytesIO()
        with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
            z.writestr("nova.xml", NOVA_XML)
            z.write(db, "nova.db")
            z.writestr("supportDetails.txt", "synthetic test backup\n")
        return out.getvalue()


if __name__ == "__main__":
    target = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(__file__).with_name("nova-synthetic.novabackup")
    target.write_bytes(build())
    print(target)
