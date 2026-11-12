# Testing

This document covers Homa Lawn's tests: fast unit tests that fake the phone, device tests against a
real Android emulator, and the window tested offscreen.

## Unit tests

```sh
uv sync
uv run pytest -q -m "not device and not slow"   # what CI runs: no emulator, no minutes-long tests
uv run pytest -q -m "not device"                # adds the slow ones (the 120 s pairing timeout)
uv run pytest -q tests/test_layout.py           # one file
uv run ruff check                               # lint, as CI runs it
```

CI (`.github/workflows/ci.yml`) runs the first line on Python 3.12 and 3.13, and `ruff check`.

They never touch this computer's real config, data or paired phones: `tests/conftest.py` points
`HOMALAWN_CONFIG`, `HOMALAWN_DATA` and `HOMALAWN_STATE_DIR` at a temporary folder for every
non-device test. The phone is replaced by fakes (`tests/fake_link.py` for the link, fake adb
runners in the tests themselves). Fixtures (a launcher database from a fresh Lawnchair, a synthetic
Nova backup, Omarchy themes) are described in [`tests/fixtures/README.md`](../../tests/fixtures/README.md);
the pairing words share vectors with the Kotlin side in `tests/vectors/`.

## Device tests (emulator)

Device tests (`-m device`) run against a dedicated Android emulator on port 5560. **Never point
them at a phone you use**: they change the home screen, pair and unpair, and restart Lawnchair.

They drive the **test build** `app.homalawn.debug`, labelled **Homa Lawn Test** on the phone
(debuggable: the suite uses its shell-only test hooks and `run-as`), not the release build phones
run. `tests/conftest.py` sets `HOMALAWN_PACKAGE` to it for device tests; set it yourself to pick
another app. The test build needs no signing key.

```sh
scripts/avd.sh create          # once: an Android 15 Google Play x86_64 AVD (HOMALAWN_AVD, default homalawn-test)
scripts/avd.sh start           # emulator on port 5560
scripts/avd.sh wait            # until it has booted
adb -s emulator-5560 install -r "$(scripts/build-lawnchair.sh debug)"
tests/device/test_provider.sh                                   # the control provider over adb
HOMALAWN_SERIAL=emulator-5560 uv run pytest -m device -v        # the device suite
```

The starting state the suite expects of the test build, once after installing it (the suite puts
back whatever it changes, so this is what it compares against):

- the default home app: `adb -s emulator-5560 shell cmd role add-role-holder android.app.role.HOME app.homalawn.debug`;
- notifications allowed **by tapping Allow** on the emulator when the home screen asks (a
  `pm grant` leaves other permission flags, which the notifications test cannot put back);
- photos allowed once, the wallpaper still unreadable without the computer's temporary grant:
  `pm grant app.homalawn.debug android.permission.READ_MEDIA_IMAGES`;
- widgets may be created without a dialog: `appwidget grantbind --package app.homalawn.debug`;
- no widget on the home screen (the Nova undo test compares widget ids, which a rebound widget
  changes): remove the default clock widget once;
- the pages in their own order, the search bar's page first and the home page (where Home goes)
  the first page (several tests treat position 0 as screen id 0 and as where Home lands): no saved
  page order from reordering pages by hand, and Lawnchair's `default_home_page` 0;
- the wallpaper not readable by the app (the backup tests need it so): never
  `READ_EXTERNAL_STORAGE` granted nor All files access allowed
  (`appops set app.homalawn.debug MANAGE_EXTERNAL_STORAGE default`);
- `tests/test_device_durability.py` allows Run in background for its run (`dumpsys deviceidle
  whitelist +app.homalawn.debug`, as `homalawn setup` does) and puts the previous state back;
- no other Homa Lawn app running: another one holds the link's port 47100 and shows its own icons
  in the drawer. Disable it for the run (`pm disable-user --user 0 app.homalawn`) and enable it
  after (`pm enable app.homalawn`).

A run longer than the shell allows can be split by test id (`pytest --collect-only -q`).

The suite:

- saves the emulator's layout, settings, wallpaper and permission state first, works in a
  temporary data folder, and at the end puts everything back and checks the emulator is as it was
  (layout, widgets bound, Lawnchair the default home, no crash logged, no computer left paired);
- runs the layout tests once over adb and once over the link;
- pairs one test identity the real way and unpairs it at the end. Because `adb forward` reaches the
  phone as loopback, which pairing refuses, the tests switch on two debug-build, shell-only hooks
  (`debug_allow_local_pairing`, `debug_open_pairing`) and switch them off after.

`tests/test_device_mcp.py` starts the **installed** `homalawn mcp` (from `~/.local/bin`, not this
checkout's `.venv`), so a stale tool install fails it; reinstall with
`uv tool install --editable --reinstall .`.

Findings from walking the emulator by hand (widget binding, dialogs, coordinates) are kept in
[`tests/device/NOTES.md`](../../tests/device/NOTES.md).

## The window, offscreen

The omarchy-shell plugin is tested in a throwaway, screenless omarchy-shell that never touches your
desktop's shell, `~/.config/omarchy` or the session bus:

```sh
H=src/homalawn/shell/tools/test-offscreen.sh
$H start  /tmp/hl-shell --socket "$XDG_RUNTIME_DIR/homalawn/homalawnd.sock" --theme catppuccin-latte
$H summon /tmp/hl-shell look
$H call   /tmp/hl-shell snapshot look:/tmp/hl-shell/shots/look.png
$H call   /tmp/hl-shell lookFacts      # JSON: what the Wallpaper & style section shows
$H stop   /tmp/hl-shell
```

It needs quickshell (`qs`) and Omarchy's shell sources (`/usr/share/omarchy`, or `OMARCHY_SOURCE`).
Every call the harness supports is listed in
[`src/homalawn/shell/README.md`](../../src/homalawn/shell/README.md#offscreen-test).

The device tests `test_device_window_nav.py`, `test_device_shell_apply.py` and
`test_device_shell_undo.py` combine the harness with a temporary `homalawn serve` paired to the
emulator, and save their pictures to `HOMALAWN_NAV_SHOTS` / `HOMALAWN_APPLY_SHOTS`. The window
screenshots in `docs/images/` were made with this harness. Against a real service, use a temporary
`HOMALAWN_CONFIG`, `HOMALAWN_SOCKET` and `HOMALAWN_STATE_DIR`, and never add made-up phones to that
config: the service would search the network for them and dial real phones it finds.

