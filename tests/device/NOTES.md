# Device notes (test AVD, API 35 google_apis_playstore, Lawnchair 15 debug)

## Widget bind (widget_bind / widget_status)

`widget_bind` and OmalawnBindActivity were removed in the final fix wave (they started
activities and leaked ids); `widget_status` stays. Notes below are kept as history.
- First bind for a package: `bindAppWidgetIdIfAllowed` returns false -> provider opens
  `OmalawnBindActivity` (invisible trampoline) which calls `startActivityForResult(ACTION_APPWIDGET_BIND)`.
- Launching ACTION_APPWIDGET_BIND directly from the provider with `startActivity` does NOT work:
  ActivityTaskManager logs the START (BAL_ALLOW_VISIBLE_WINDOW) but Settings' AllowBindAppWidgetActivity
  finishes at once with no UI (it needs a calling package, which only startActivityForResult gives).
- Dialog (com.android.settings): title "Create widget and allow access?", message
  "After you create the widget, the application can access everything displayed. Application: Lawnchair (Debug) Widget: Analog",
  checkbox `android:id/alwaysUse` "Always allow Lawnchair (Debug) to create widgets and access their data",
  buttons `android:id/button2` "Cancel", `android:id/button1` "Create".
  Bounds on 1080x2400: checkbox [107,1292][971,1428], Cancel [633,1439][807,1581], Create [807,1439][978,1581].
- Create without ticking -> widget_status bound:true. Cancel -> bound:false (trampoline frees the id).
- Tick "Always allow" + Create -> the next widget_bind returns bound:true immediately, no dialog.
  The grant is held by the system AppWidgetService (persists across launcher restarts).
- Alternative with no dialog at all: `adb shell appwidget grantbind --package app.homalawn.debug` (not used yet).
- Bound ids that are never placed in the favorites table stay allocated in host 1024 (leak, not a crash).

## Adding widgets (`homalawn widget add`, Task 6)
- Pre-binding with `widget_bind` is useless here: the id is deleted on the launcher restart the
  DB write needs. The row is written as a placeholder (appWidgetId=-1, restored=1 FLAG_ID_NOT_VALID);
  on load WidgetInflater allocates an id, sets FLAG_ID_ALLOCATED (16) and tries `bindAppWidgetIdIfAllowed`.
- Observed end states (2026-10-01, emulator-5560):
  - Bind allowed, provider without configure activity (Google search 4x1): restored=0, id>0 within ~4 s;
    widget renders live.
  - Bind allowed, provider WITH configure activity (deskclock Analog): restored=4 FLAG_UI_NOT_READY, id>0,
    shown as "Tap to finish setup" with a gear. Tapping opens the clock's config ("Select a Clock Face");
    after choosing, restored=0 and the clock renders. CLI prints "Tap the widget on the phone to set it up"
    and keeps waiting.
  - Bind NOT allowed (`adb shell appwidget revokebind --package app.homalawn.debug`; `dumpsys appwidget`
    then shows an empty Grants list): restored=17 (ID_NOT_VALID|ID_ALLOCATED), id allocated but unbound;
    shown as a grey placeholder with the provider icon and a gear. Tapping it opens Settings'
    AllowBindAppWidgetActivity ("Create" = android:id/button1 at [807,1439][978,1581]); after Create the
    launcher binds a NEW id (allocated 28 -> final 29) and restored=0. CLI prints the tap/grantbind
    instruction once, keeps polling, and returns the final id. On timeout the placeholder stays.
- Re-grant: `adb shell appwidget grantbind --package app.homalawn.debug` (current state of the AVD).
- The "Tap to set up" text at the top of screen 0 is Lawnchair's own smartspace, unrelated.

## Nova import (Task 8)
- Checked 2026-10-01: there is no Nova backup file in ~/Downloads. Step skipped; waiting for a real Nova backup.
- When it arrives: push to /sdcard/Download/ on the AVD, Lawnchair settings -> Home -> Nova import, then
  `homalawn layout get` and compare screen/hotseat counts with what Nova showed.

## Link server (patch 0003, Task 4)
- Phone identity: Keystore EC P-256 alias `omalawn_link`, self-signed `CN=OmaLawn link`; survives
  `install -r`. TLS 1.3 only, client cert required (any cert passes TLS; pins decide per request).
- Wire: JSON lines (max 1 MiB) plus blob frames `{"id":n,"blob":len[,"result":{...}]}` + raw bytes
  (max 32 MiB). A request with bytes (`launcher.layout_import {db?}`, `launcher.stage_file {name}`)
  sends its JSON line, then its blob frame with the same id. `launcher.layout_export` and
  `launcher.wallpaper_backup` answer with a blob frame whose `result` carries the metadata
  (`db`, `size`, `how`; no `path`). Oversized line → `{"id":null,"error":{"code":"frame_too_large"}}`
  and the phone closes. Idle 60 s → closed. 5th concurrent connection → closed at once.
- Unpaired computer: only `link.hello` (and Task 5's `link.pair_request`); anything else →
  `not_paired`; a blob request from it also closes the connection (its bytes are never read).
- Debug build only, shell uid only: provider `debug_pin <fp> --extra name:s:x`, `debug_unpin <fp>`.
- `adb forward` reaches the phone over loopback; for a path through the per-app network firewall
  use `adb -s emulator-5560 emu redir add tcp:47101:47100` and dial 127.0.0.1:47101
  (`HOMALAWN_LINK_DIAL_PORT=47101`). Remove with `emu redir del tcp:47101`.
- On this AVD Lawnchair has no POST_NOTIFICATIONS grant, so the link notification is hidden (the
  service still runs). `wallpaper_backup` needs the READ_EXTERNAL_STORAGE grant over the link too.

## Link durability (Task 7, `tests/test_device_durability.py`, 2026-10-02)
Observed on the AVD (API 35) unless marked *assumed*. AOSP references are android15-release.
- **Reboot:** the link is back ~1 s after `sys.boot_completed` with no app interaction. What started
  the FGS was the app start for the home activity (`Start proc … for top-activity`, then
  `Background started FGS: Allowed … code:PROC_STATE_TOP`); `BOOT_COMPLETED` arrived ~5 s later
  ("link start from android.intent.action.BOOT_COMPLETED") and only poked the running service. The
  receiver-only path (FGS from BOOT_COMPLETED with no home start, e.g. another default home) is
  *assumed* from the docs (connectedDevice is not on the 15+ boot-restricted list), not observed.
- **One crash (`am crash`):** ActivityManager schedules the sticky restart in 1000 ms; the process
  comes back for the home activity (Lawnchair in front) or for the service; link.ping ~1.6 s.
- **Two crashes in quick succession (the "busy until force-stop" bug):** Android shows "Lawnchair
  keeps stopping" for the second one. The default handler (RuntimeInit.KillApplicationHandler)
  calls `handleApplicationCrash`, which blocks until that dialog is answered
  (AppErrors.crashApplicationInner `result.get()`; AppErrorDialog DISMISS_TIMEOUT = 5 min), and only
  then kills the process. Seen: the crashed process stayed alive 5 min (pid unchanged, window
  "Application Error", "Crashing app skipping ANR"), binder/link threads answered ping, every
  layout call waited on the dead main thread → `busy`; at 5 min "Killing … crash"; the default home
  was cleared (chooser shown) and the link service was NOT restarted (a provider call started the
  process, its FGS start was "Background started FGS: Disallowed … uidState: CEM").
  Fixed in patch 0003: `CrashExit` ends the process if it is still alive 8 s after an uncaught
  exception (the crash is still recorded, dialog included). It is installed from
  `LawnchairApp.attachBaseContext`, i.e. directly over Android's handler (logcat "installed over
  com.android.internal.os.RuntimeInit$KillApplicationHandler"); Lawnchair's bug reporter, installed
  later by the provider's process initializer, wraps it and writes its report first. Whether Android shows the dialog depends on its crash
  history (a crash within 60 s of the previous one: "repeating" → dialog, or "crashed quickly" →
  killed at once); the test repeats the double crash, a quiet minute apart, until the dialog shows.
- **Default home after a double crash:** whenever the dialog path ran, Android cleared Lawnchair as
  default home (AppErrors.handleAppCrashLSPB → `clearPackagePreferredForHomeActivities`): the
  next Home press shows the chooser. Over the link this cannot be repaired (no shell); the tests
  put it back with `cmd package set-home-activity`.
- **Restart back-off after crashes:** a crash within 60 s of the service's previous restart makes
  ActivityManager wait 30 min × (crashCount − 1) (ActiveServices.scheduleServiceRestartLocked,
  BOUND_SERVICE_CRASH_RESTART_DURATION = 30 min; seen: 1 800 000 ms, later 9 000 000 ms). So
  `CrashExit` sets a plain alarm 10 s ahead → `BootReceiver` (action
  `app.lawnchair.omalawn.link.RESTART`, extra marking it as ours) → `LinkService.start`, only when
  Android will not restart soon by itself: the crash came < 60 s after this process created the
  link service, or CrashExit had to end a stuck process. Bounds: at most 3 alarms per 10 min, each
  armed alarm counted; none within 60 s of our own alarm firing (the receiver records the time).
  Seen with "alarm only for a stuck process" (review round 1 draft): a plain crash 49 s after an
  alarm restart got a 5 400 000 ms back-off and no alarm, and the in-front double-crash case lost
  the link for 90 min — hence the "< 60 s after the link service started" rule. State in
  `shared_prefs/omalawn_crash_restart.xml`; debug hook `debug_crash_budget_reset` clears it.
  Seen (Settings in front, NO battery exemption): `Start proc … for broadcast {…BootReceiver}`,
  "link start from …RESTART", FGS running, link back ~15–25 s after the 2nd crash although
  ActivityManager had postponed its own restart by 30 min. Why Android
  allowed that background FGS start is not logged (dumpsys shows the service record's earlier
  `infoAllowStartForeground … PROC_STATE_TOP`; *assumed*: the pending-restart record keeps it). On a
  real phone it may be refused without the battery exemption ("Keep connected") — not verified;
  with the exemption it is allowed by the documented rules.
- **Crash loop (debug hook `debug_crash_link_start 8`: the next 8 LinkService starts throw):** final
  run: 6 crashes, 3 restart alarms (the cap; 3 were armed before the first one fired), then quiet
  for 113 s at the end of the 150 s window; 3 deliberate crashes never used. An earlier variant
  (alarm only for a stuck process): 3 crashes, 1 alarm.
- **Process without the home activity:** in this Launcher3 the loader runs only for registered
  callbacks (`LauncherModel.startLoader` posts a LoaderTask only when `getCallbacks()` is non-empty),
  so a process started for the service/provider never loaded the home screen (`model_loaded:false`
  forever, layout export `busy`). Fixed: a headless load (a no-op `BgDataModel.Callbacks`,
  removed once loaded) is started by layout export (waits up to 30 s), by the new
  `launcher.ensure_loaded` (waits up to 10 s, returns `{loaded, db}`; the CLI's wait_loaded uses it)
  and by `LinkService.onCreate` 5 s after start. `launcher.ping` has no side effects. Seen: after a
  force-stop + provider call, ping said model_loaded false for 8 s; ensure_loaded loaded it in 2.2 s.
- **Doze:** screen off + `dumpsys deviceidle force-idle` (deep = IDLE): pings through the NAT path
  (firewall applies) answered in 0.05 s. Real Doze entry after a long idle period was not waited
  for (*assumed* equal: the FGS procstate 4 passes the Doze chain).
- **Settings in front 5 min:** pings every minute through the NAT path all answered (~0.05 s),
  Lawnchair `curProcState=4` (foreground service).
- **App update (`adb install -r`, Settings in front):** ActivityManager kills the app
  ("due to installPackageLI"), then `Start proc … for broadcast {…BootReceiver}` and
  `Background started FGS: Allowed … code:PACKAGE_REPLACED`; link.ping ~1 s after install returned.
  (The start from `LawnchairApp.onCreate` in that process is refused, `uidState: CEM`, as expected.)
- **Idle connection:** a LinkTransport left idle for 90 s answered on the SAME connection (keep-alive
  every 20 s); a connection without keep-alive was hung up by the phone within that time.

## Screen control (patch 0004, Task 8)
- Restricted settings, API 35 AVD, Lawnchair installed with `adb install -r` (installer null):
  the in-Settings path (Accessibility › Downloaded apps › Lawnchair (Debug) › Use; since patch
  0033 the test build is listed as "Homa Lawn Test") was NOT
  blocked. It showed only the normal "Allow Lawnchair (Debug) to have full control of your
  device?" dialog (View and control screen / View and perform actions); Allow bound the service.
  `appops get app.homalawn.debug ACCESS_RESTRICTED_SETTINGS` stays `default` (a new rejectTime
  is logged at the toggle tap), so an app cannot tell from the op alone on 15+:
  `screen.status.restricted` is null there. A Play-Store/browser-installed build may differ.
- Settings › Permissions › Screen control (patch 0033) opens Accessibility's list, not the
  service's own page: `ACTION_ACCESSIBILITY_DETAILS_SETTINGS` needs
  `OPEN_ACCESSIBILITY_DETAILS_SETTINGS`, which only system apps get (SecurityException on API 35).
- Enabling for tests: `settings put secure enabled_accessibility_services
  app.homalawn.debug/app.lawnchair.LawnchairAccessibilityService` + `accessibility_enabled 1`
  binds within ~1 s; `dumpsys accessibility` shows `capabilities=161` (window content 1 +
  gestures 32 + screenshots 128) and `eventTypes=` empty.
- Settings' search page is its own app (com.google.android.settings.intelligence) in Settings'
  task: force-stopping Settings alone leaves it on top.
- Screenshots: our guard refuses closer than 340 ms; spaced 350 ms, 5 of 5 succeed in ~2 s.
- Wallpaper read on API 35 (targetSdk 35): the wallpaper service checks READ_EXTERNAL_STORAGE
  (`SecurityException: Permission android.permission.READ_EXTERNAL_STORAGE denied`). Requesting it
  at run time answers -1 with no UI (`GrantPermissionsViewModel ... READ_EXTERNAL_STORAGE result=1`);
  the photo prompt ("Allow all") grants READ_MEDIA_IMAGES and the read still fails. `appops set
  app.homalawn.debug MANAGE_EXTERNAL_STORAGE allow` (All files access) makes it readable.
- Lawnchair's notifications are grouped in the shade (link status + request); a tap on the
  collapsed group only expands it, so tests tap Expand first.

## Wallpaper backup over the link (Task 9)
- After every successful `wallpaper_set` the desktop reads `wallpaper_info.system_id`
  (`getWallpaperId(FLAG_SYSTEM)`, readable over the link without any media permission) and keeps
  `{device, system_id, snapshot}` (device = `link:<phone cert fp>` or `adb:<serial>`; must match)
  in `$HOMALAWN_DATA/wallpaper-set.json` with the picture in the
  `current-wallpaper` snapshot (not in the undo list). While the phone reports the same id, that
  picture is the backup and `wallpaper_backup` is never called; a different id (changed on the
  phone) → the old phone-read path. Seen on the AVD: each set gives a new system id, lock id
  unchanged (`test_link_second_wallpaper_backs_up_the_first_without_reading_the_phone`).
- Tests that exercise the phone read call `session.forget_wallpaper()` first, because the
  session's data dir may already hold a record from an earlier wallpaper test.

## Final review fix wave (2026-10-05)
- **Pairing confirmation (C2).** The pairing notification has Review (opens the non-exported
  `PairConfirmActivity`, also its content tap) and Reject; no Accept. The screen shows
  "Pair with <name>?", the four words, Reject and Accept. `input tap` on Accept pins (a real
  injected touch, not obscured); `debug_pair_fire_current` (the Review PendingIntent, as a
  notification listener could send it) only opens the screen; `debug_pair_legacy_accept` (the
  old service ACTION_ACCEPT) logs "a pairing Accept sent to the service was ignored". The screen
  closes itself when the request ends. The obscured-touch rejection and the ignored
  accessibility click are code-read only (no overlay app on the AVD).
- **Local pairing / window (C2, I3).** `adb forward` arrives as loopback, so pair requests from
  it are refused (`local_pairing`) unless `debug_allow_local_pairing on` (stored with
  Settings.Global.BOOT_COUNT: survives a process kill, off after a reboot — both checked).
  Outside the window: `pairing_closed`, no notification. "Pair a computer" sits in the silent
  Homa Lawn notification, collapsed in the shade: expand it (desc "Expand" next to the title row)
  before the action shows. `debug_link_state` reports window ms left, toggle, connected names,
  screen-in-use ms.
- **Handshake limit (I3).** 10 non-paired attempts / 60 s per address, counted before TLS (an
  over-limit socket is closed at once: the client sees an SSL/EOF error); a pinned computer's
  attempt is refunded after the handshake, but a paired computer that dials while its address
  is already over the limit waits up to a minute. Loopback is exempt while the local toggle is on.
- **Refused foreground start (I4).** `debug_refuse_foreground 1` + `run-as kill -9` with Settings in
  front: the sticky restart created a new process, onCreate logged "foreground start refused,
  stopping the link service: ForegroundServiceStartNotAllowedException", the process stayed up,
  no crash, no FGS; Home brought it back. The same refusal in onStartCommand (start request from
  the launcher's onResume while the service ran): service stopped, no
  "did not then call startForeground" crash within 15 s, same process.
- **Indicators (I5).** `dumpsys notification --noredact` shows the status notification's
  `android.text` / `android.bigText`: "Connected: homalawn-test-desk", "Screen control in use"
  (30 s after the last screen.* call other than screen.status, only while the accessibility
  service is bound), "Pairing open for N s".
- Unpaired `link.hello` is exactly `{"paired":false,"v":1}`; mDNS logs "mDNS registered as 'Homa Lawn'".

## Pairing screen (2026-10-12, after the Galaxy S23 trial)
- "Pair a computer" is now an activity PendingIntent (action `app.lawnchair.omalawn.link.PAIR_WAIT`,
  NEW_TASK|SINGLE_TOP|CLEAR_TOP; manifest `launchMode="singleTop"`) to `PairConfirmActivity`. On the
  AVD the shade collapses on the tap and the dialog shows "Waiting for a computer…", "On your computer
  open Homa Lawn › Add phone, or run homalawn pair.", "This screen waits 5 minutes." (the window,
  `PairingGate.SCREEN_WINDOW_MS`), Cancel; `debug_link_state.pairing_open_ms` is just under 300 000.
  Since patch 0034 the screen is Compose drawn in the activity's own window (Theme.Transparent, a
  scrim and the settings' dialog surface; no separate dialog window): each line is its own text node
  for uiautomator, the four words one node joined by spaces, and Accept a plain node with no click
  action whose touch is read raw (`pointerInteropFilter`): `input tap` on its text still pins. Three taps:
  one distinct ActivityRecord (count distinct `ActivityRecord{hash …PairConfirmActivity}`: one record
  is listed several times in `dumpsys activity activities`) and logcat "pairing opened on the pairing
  screen" once. Pulling the shade down over the screen did not stop it (window stayed open).
- With the CLI already waiting (`pairing_closed` every 2 s), the words replaced "Waiting…" within
  ~2 s of the tap, often before a uiautomator dump could see the waiting state. No pairing
  notification is posted while the screen waits; Home with a request open → the notification is
  posted (fallback) and `pairing_open_ms` is 0.
- Accept ignores a finger that went down < 1 s after the words appeared (tests sleep 1.2 s).

## Quiet link: no foreground service, no status notification (2026-10-17, API 35 AVD)
- Measured by hand before the tests were written (link.ping through `emu redir` 47101, where the
  per-app firewall applies; `dumpsys netpolicy` and `dumpsys activity processes`):
  - **With "Keep connected"** (`dumpsys deviceidle whitelist +app.homalawn.debug`): Settings in
    front 5 min, a ping every minute all answered in 0.1 s (procState SVC, adj 500, network
    `blocked=APP_BACKGROUND, allowed=POWER_SAVE_ALLOWLIST…, effective=NONE`); screen off + forced
    deep Doze 4 min, all answered (`blocked=DOZE|APP_BACKGROUND, effective=NONE`); `kill -9` behind
    Settings: Android restarted the sticky LinkService in ~1 s and the link answered with Settings
    still in front.
  - **Without it**: the link answered for the first minute behind Settings, then Android stopped the
    idle app's service ("Stopping service due to app idle"), the process fell to procState HOME
    (adj 600, not frozen) and its network was cut: `blocked=APP_BACKGROUND, allowed=NONE,
    effective=APP_BACKGROUND`, TLS handshakes time out. Forced Doze with the home screen showing
    before sleep: `effective=DOZE`, no answer. Being the home app keeps the process alive but does
    not give it network on Android 15.
- So "Keep connected" is required now (it was optional with the foreground service). LinkAsks posts
  notification 47103 (channel `omalawn_requests`, flags `ONLY_ALERT_ONCE|AUTO_CANCEL`, not ongoing)
  while it is missing; `homalawn setup` grants it over adb.
- Since patch 0033 (2026-11-12) the person-facing name is "Run in background" (Settings ›
  Permissions); the 47103 ask is titled "Let Homa Lawn run in the background", its action "Run in
  background", and its tap opens Settings › Permissions (`lawnchair://settings/homalawn-permissions`).
  The three Homa Lawn channels sit in the group "From your computer": `omalawn_pair` "Pairing
  requests", `omalawn_requests` "Setup reminders", `omalawn_update` "App updates" (same ids), all
  with the phone-mark small icon. To post the ask again by hand: `dumpsys deviceidle whitelist
  +app.homalawn.debug`, HOME, `whitelist -app.homalawn.debug`, open the settings and HOME again
  (the "asked" record clears only while nothing is missing).
- Android 15 sends BOOT_COMPLETED to a force-stopped app on its next start (here: a provider call):
  BootReceiver must not load the home screen for that one (`test_ping_is_pure…`).

## Starting-state drift and a cold-start deadlock (2026-11-12, API 35 AVD, build 337)
- Three leftovers from hand experiments had made eight tests fail for weeks, all looking like
  product bugs: (1) a saved page order `workspace_screen_order = "2, 0, 1, 3"`: the page with
  screen id 0 (the search bar's) was second, so tests that place on position 0 and then look for
  `screen=0` in `dumpsys activity top` (seam place/slow link, settings icon), drop on the reserved
  top row (shell home), compare the Nova import's first page (nova) or the window's page with the
  phone's (home geometry) all missed. Removed with `prefs.send_many(t, [], [("shared",
  "workspace_screen_order")])`; the launcher writes "0, 1, 2, 3" back. Its partner `default_home_page` (a position, datastore
  int) was 1, the search bar's page under the old order; after the order reset Home landed on the
  second page (look_parallax's "page 0" and home_geometry's phone picture), so it was set to 0 with
  `prefs.send(t, ("datastore", "default_home_page", "int", "0"))`. (2) four widgets (three
  Google search widgets "needs setup", a Chrome one). (3) the wallpaper readable over the link:
  `READ_EXTERNAL_STORAGE` granted and `appops MANAGE_EXTERNAL_STORAGE allow` (the All files access
  experiment above), so "explains once when no prompt can help" and the window's Replace question
  never happened. Undone with `pm revoke … READ_EXTERNAL_STORAGE` and `appops set …
  MANAGE_EXTERNAL_STORAGE default`. The hygiene fixture puts back whatever it found, so such
  drift survives every run; testing.md's starting state now names all three.
- The photo prompt test needs READ_MEDIA_IMAGES not granted (Android shows no prompt for a granted
  permission, and the starting state grants it): the test revokes it, and puts the grant and the
  flags back after (revoking the limited access marks READ_MEDIA_IMAGES `revoked-compat`, so that
  flag is put back last).
- **Deadlock (fixed by patch 0036, `tests/test_device_startup.py`):** a provider call (`ping`) that arrives while the process
  is starting can hang it for good. Seen twice in `dumpsys dropbox --print data_app_anr`
  (06:45 and 08:03): a binder thread in `OmalawnCommands.ping` → `InvariantDeviceProfile.INSTANCE.get`
  holds Dagger's IDP and ThemeManager locks and waits in `MainThreadInitializedObject.get`
  (ThemeProvider) for the main thread; the main thread, in `Launcher.onCreate` →
  `LauncherAppState.getInstance` → `LawnchairIconProvider`, waits for the ThemeManager lock. The
  launcher shows "Homa Lawn Test isn't responding" and every later call hangs. Tests that kill the
  app should wait for the home screen in front before the first provider call.
- Other causes found the same day: the Google app's search widget now opens a setup screen when
  placed, so `test_real_widget_add_binds_and_renders` never saw it bind (it left "needs setup"
  placeholders); the test uses Chrome's search widget. `_tap_pair_a_computer` resumed the settings
  task an earlier test had left on Wallpaper & style (no Computers row there); it now starts the
  settings with NEW_TASK|CLEAR_TASK. Omarchy now ships `catppuccin/backgrounds/*.webp`, so the theme
  tests in test_device_follow / test_device_phone_wallpaper had skipped ("not installed") for weeks;
  they take the theme's own picture format now.
- `test_crash_loop_is_bounded` after the double-crash cases: our restart alarm starts a process
  ~10 s after the previous case, and a crash ~60 s later was "crashed too many times, killing!
  Reason: crashed quickly" (logcat ActivityManager): no restart, one crash, nothing to bound. The
  test now waits until Android logged no crash of ours for 2 × QUIET_S first.
- Reproduced on demand (build 337, 2026-11-12): stop the app, then start the home screen while six
  provider pings and three link callers race in; it froze in round 1, 2 or 4 of the runs. The ANR
  only gets recorded once something waits on the frozen process (a key event, or a service start
  after 20 s); its traces name the exact locks: the binder thread in `ping` holds the
  InvariantDeviceProfile and ThemeManager `DoubleCheck` locks and waits in
  `MainThreadInitializedObject.get` (ThemeProvider.<init> line 46, WallpaperManagerCompat); main,
  in `Launcher.onCreate` -> `LauncherAppState` -> `LawnchairIconProvider`, waits for the
  ThemeManager lock.
- "Without Run in background the link is cut" (durability) was not Android's doing: with the app
  not exempt, standby bucket 10 (active), on battery (AVD: AC off, status 4) and Doze ACTIVE,
  Android stopped the link service 50-60 s after Settings came to the front ("Stopping service
  due to app idle", uid state SERVICE -> HOME), as the test claims. The test's check matched the
  "Last ANR service:" section of `dumpsys activity services`, which keeps the service of the last
  ANR (from the startup freeze) until a reboot; it now looks only at running entries
  (`* ServiceRecord{`).
