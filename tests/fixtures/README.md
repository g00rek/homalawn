# Fixtures

## launcher-empty.db

Pulled from the test AVD (emulator-5560) right after the first boot of the
pristine debug Lawnchair (`app.lawnchair.debug`, v15.0.0-beta3.0), after
`am force-stop` so nothing is mid-write:

    adb -s emulator-5560 exec-out run-as app.lawnchair.debug cat databases/launcher_5_4_4.db > tests/fixtures/launcher-empty.db

Database filename in the app's `databases/` dir: `launcher_5_4_4.db`
(grid-named; a `-journal` file exists but is empty, journaling is rollback, not WAL).
Other files there: `app_icons.db`, `preferences` (+shm/wal).

Despite the name it is not literally empty: it holds the 16 default-layout rows
(4 hotseat apps, a "Google" folder with 6 apps, 4 workspace apps on screens 0/1).

### Schema (`.schema favorites`)

    CREATE TABLE favorites (_id INTEGER PRIMARY KEY, title TEXT, intent TEXT, container INTEGER, screen INTEGER, cellX INTEGER, cellY INTEGER, spanX INTEGER, spanY INTEGER, itemType INTEGER, appWidgetId INTEGER NOT NULL DEFAULT -1, icon BLOB, appWidgetProvider TEXT, modified INTEGER NOT NULL DEFAULT 0, restored INTEGER NOT NULL DEFAULT 0, profileId INTEGER DEFAULT 0, rank INTEGER NOT NULL DEFAULT 0, options INTEGER NOT NULL DEFAULT 0, appWidgetSource INTEGER NOT NULL DEFAULT -1);

Containers: -100 = desktop, -101 = hotseat, positive = folder id.

## Grid name

`launcher_5_4_4.db` = `launcher_{rows}_{cols}_{hotseatCount}.db` (Lawnchair
`DeviceProfileOverrides.DBGridInfo.dbFile`): 5 rows, 4 columns, 4 hotseat slots.
Hotseat rows use `screen` = `cellX` = slot index, `cellY` 0.
Correction to the note above: the "Google" folder (id 6) has 7 children (ids 7-13).

## launcher-populated.db

`launcher-empty.db` plus one Chrome Dino widget (2x2, screen 0, cell 0,1; row 18,
appWidgetId 25), so every item type (hotseat apps, desktop apps, folder, widget) is covered.
Created on emulator-5560:

1. `am force-stop`, pull the DB, insert a favorites row: itemType 4, container -100,
   cellX 0, cellY 1, spans 2x2, `appWidgetProvider`
   `com.android.chrome/org.chromium.chrome.browser.quickactionsearchwidget.QuickActionSearchWidgetProvider$QuickActionSearchWidgetProviderDino`,
   `appWidgetId` -1, `restored` 1 (FLAG_ID_NOT_VALID); push back; start home.
2. On load Lawnchair allocated and bound widget id 25 itself (the "always allow" bind
   grant) and rewrote the row with `restored` 0. Screenshot showed the widget rendered.
3. `am force-stop`, pull the DB again -> this file.

Things that did NOT work (matter for `apply`):
- Binding an id first via the `widget_bind` provider call (since removed from the provider) and inserting a row with that id:
  the id is dropped as soon as the launcher process restarts (force-stop or kill), so the row
  was deleted on load ("Unrestored Pending widget removed").
- A widget at cell (0,0) of screen 0 was deleted on load; row 0 of screen 0 is where the
  "Tap to set up" smartspace sits. Cell (0,1) works.

## chat/claude-real.jsonl

The stream of one real `claude -p --verbose --output-format stream-json --include-partial-messages`
run (Claude Code 2.1.300, model haiku), made by the chat's own command line through the service in
`tests/test_device_shell_chat.py::test_real_claude_answers_through_the_window` (2026-10-15), asking
"What apps are in the dock?" of the test AVD. Kept: the `stream_event`, `assistant`, `user` and
`result` lines, and the `init` line cut to its session id, tools and MCP servers; the user's hook,
status and rate-limit lines (they name local plugins) were dropped.

## manifest-app.homalawn.apk, manifest-app.lawnchair.debug.apk

Manifest-only APKs (no code, about 700 bytes) for reading an APK's package
(`homalawn.appid.apk_package`): a `<manifest package="...">` with versionCode
1600020331, linked with aapt2 from build-tools 37.0.0 against
`platforms/android-37.2/android.jar`:

    aapt2 link -o manifest-app.homalawn.apk --manifest AndroidManifest.xml -I "$ANDROID_HOME/platforms/android-37.2/android.jar"
