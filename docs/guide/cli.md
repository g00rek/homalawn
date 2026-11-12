# The `homalawn` command

This guide covers every `homalawn` command, how a command picks the phone, History and undo, the
Nova Launcher import and the environment variables.

The [desktop window](desktop-window.md) and the [agent tools](agents-mcp.md) use the same
operations. `homalawn --help` and `homalawn <command> --help` list every option; `homalawn
--version` prints the version.

## Which phone, and how

Two ways reach the phone:

- **The link** (normal): Homa Lawn's own encrypted connection on port 47100, paired once. Every
  command works over it, including `screen …`.
- **adb**: Wireless debugging or USB. It can also set the home app back after a crash and allow
  reading the current wallpaper for one backup; of the `screen …` commands only `screen status`
  works over it.

How a command picks:

- Naming an adb device (`--serial emulator-5560` or `HOMALAWN_SERIAL=emulator-5560`) means adb to
  that device, never the link, so a test run on an emulator cannot reach a paired phone.
- Otherwise the paired link is used when the phone answers, else adb (the device saved by
  `connect`, or the one attached device), and the command says why it switched.
- With more than one phone paired, name one: `--phone NAME` or `HOMALAWN_PHONE=NAME` (the name from
  `homalawn phones`, or the start of its id). Without it the command refuses rather than guess. A
  named phone that does not answer is an error, never a switch to adb.
- `--via link` or `--via adb` forces the way.
- Every command that changes the phone first prints where it goes, for example `via link (Pixel 8)`.

Global options go before the command: `homalawn --phone "Pixel 8" status`.

## Commands

Cells are counted from 0: `--screen 0` is the first home-screen page as the phone shows it, `--x 0`
the left column, `--y 0` the top row. While the date/search bar is on, the top row of the page that
holds it is reserved; `homalawn layout get` lists every page, empty ones too.

### Phones and pairing

| Command | What it does |
|---|---|
| `phones` | List the phones paired with this computer. |
| `pair [HOST[:PORT]] [--name NAME]` | Pair with a phone; both screens show four words, tap Accept on the phone if they match. Without HOST it looks on this network. `--name` sets how the phone shows this computer. |
| `unpair [NAME]` | Forget a paired phone here and ask the phone to forget this computer. |
| `status [--json]` | How the phone is reached and whether Homa Lawn answers (`--json`: everything, with technical details). |
| `connect [HOST]` | Connect, remember how, and check Homa Lawn answers. With a paired phone HOST is tried over the link and added to its addresses; with an adb port (`:5555`) it is an `adb connect`. |
| `addresses` | List where the phone is looked for and which address answered last. |
| `addresses add HOST` / `addresses remove HOST` | Add an address to try (at most 5) / remove one you added. |

### Installing and updating the phone app

| Command | What it does |
|---|---|
| `setup [--new] [--serial SERIAL] [--apk FILE] [--via link] [--phone NAME] [--no-screen-control]` | Set up a phone over adb in one go: install Homa Lawn, make it the home app, let it run in the background, switch on screen control, pair it and check it works. See [setup-phone.md](setup-phone.md#with-wireless-debugging-homalawn-setup). |
| `update [--apk FILE] [--phone NAME]` | Send a new Homa Lawn to the paired phone over the link. With **Install updates** allowed the phone installs it by itself; otherwise tap the update notification, then Update. |
| `update --download [--port N]` | Give the phone the app to download instead (QR code and address): the first install, or a phone whose Homa Lawn cannot update itself yet. A release: the release file's HTTPS address on GitHub, nothing served here. A build from this checkout or `--apk FILE`: served over plain HTTP on your own network for 10 minutes, port 47101 by default. |

Which app file is used, everywhere one is needed: `--apk FILE`, else a build in this source
checkout (when you run from one and it matches the phone's app), else the newest GitHub release,
downloaded and checked (SHA-256 and signing certificate). A phone running the test build or a build
signed with your own key never gets the release offered: Android would refuse it.

### Home screen

| Command | Example |
|---|---|
| `layout get [--json]` | `homalawn layout get --json > layout.json` (the dock is `hotseat` there) |
| `layout apply FILE` | `homalawn layout apply layout.json` (build it from a fresh `layout get`) |
| `apps` | `homalawn apps` (launchable apps) |
| `place APP --screen --x --y` | `homalawn place com.android.chrome --screen 0 --x 1 --y 2` (moves it if it is already there) |
| `remove APP\|ID` | `homalawn remove com.android.chrome` |
| `folder create NAME APP... --screen --x --y` | `homalawn folder create Work com.slack com.google.android.gm --screen 0 --x 0 --y 3` |
| `widgets` | `homalawn widgets` (provider, label, default size in cells) |
| `widget add PROVIDER --screen --x --y [--w] [--h] [--wait SECONDS]` | `homalawn widget add com.google.android.deskclock/com.android.alarmclock.AnalogAppWidgetProvider --screen 1 --x 0 --y 0` (waits up to 120 s for it to be live) |
| `grid COLSxROWS` | `homalawn grid 5x6` |
| `nova-import FILE [--dry-run] [--glance-row/--no-glance-row]` | `homalawn nova-import ~/Downloads/home.novabackup --dry-run` (see [below](#nova-import)) |
| `prefs list [FILTER]` / `prefs get KEY` / `prefs set KEY VALUE` | `homalawn prefs set homar_label_gap_dp 6` (`--store` and `--type` only for keys Homa Lawn does not know) |

### Wallpaper & style

| Command | Example |
|---|---|
| `look show` | This phone's Wallpaper & style settings as JSON (they are changed in the window). |
| `look apply` | `homalawn --phone "Pixel 8" look apply` (see [theme-sync.md](theme-sync.md)) |
| `wallpaper FILE [--lock \| --both] [--crop center] [--no-backup]` | `homalawn wallpaper ~/Pictures/wall.png --both --crop center` |

### History and undo

| Command | What it does |
|---|---|
| `undo [--no-backup]` | Undo the last change Homa Lawn made on this phone; repeat to go further back. |
| `snapshot save [NAME]` | Save the current home screen under a name (default `manual-<date>-<time>`). |
| `snapshot list` | Every snapshot, newest first, with the phone it was taken on. |
| `snapshot restore NAME [--force] [--no-backup]` | Restore one (layout, settings and grid, or wallpaper: whatever it holds). |

### Screen control (any app)

These need **Screen control** switched on on the phone (Homa Lawn's settings › Permissions) and the
link. Ids such as `n12` come from `screen tree` and are valid until the next `screen tree`.
Points are in screen pixels.

| Command | Example |
|---|---|
| `screen status` | Is screen control on? |
| `screen tree [--json]` | `homalawn screen tree` (items with ids and center points) |
| `screen tap NODE\|X,Y` | `homalawn screen tap n12` |
| `screen long-press NODE\|X,Y [--ms 800]` | `homalawn screen long-press 540,1200` |
| `screen swipe X1,Y1 X2,Y2 [--ms 300]` | `homalawn screen swipe 540,1800 540,600` |
| `screen type TEXT [--node N]` | `homalawn screen type "wifi"` (replaces what the field holds) |
| `screen key back\|home\|recents\|notifications\|quick_settings` | `homalawn screen key back` |
| `screen launch PACKAGE` | `homalawn screen launch com.android.settings` |
| `screen shot [FILE]` / `screenshot [FILE]` | Save a screenshot (the two are the same; `screenshot` also works over adb). Default: `~/Pictures/homalawn-…png`. |

### Desktop and agents

| Command | What it does |
|---|---|
| `desktop install [--no-agents]` | Add Homa Lawn to the Omarchy desktop: the service, the bar icon and window, and the tool server for coding agents. See [desktop-window.md](desktop-window.md). |
| `desktop uninstall` | Take them out again; pairings, keys and History stay, and it says where. |
| `serve` | Run the Homa Lawn service (normally started as `homalawnd` by `desktop install`). |
| `mcp` | Run Homa Lawn's tool server for coding agents (MCP over stdio). See [agents-mcp.md](agents-mcp.md). |

## History and undo

- Every change is saved first, under `~/.local/share/homalawn/snapshots/`: the home screen, a
  setting, the grid (with the layout, since changing the grid loses positions), a wallpaper, a Nova
  import, a Wallpaper & style apply. `undo` walks all of them as one list, newest first; running it
  again goes further back. Snapshots taken before a restore, and the ones you save by hand, are not
  in that list: restore them by name.
- Each snapshot records the phone it was taken on (the phone's certificate over the link, the adb
  serial over adb). `undo` walks only this phone's snapshots, and `snapshot restore` refuses another
  phone's unless you add `--force`. The same phone over adb counts as another phone: undo a change
  the way it was made.
- Restoring a wallpaper first saves the one shown now. When that is not possible it stops before
  changing anything; see [Wallpaper backups](theme-sync.md#wallpaper-backups).

## Things worth knowing

- **Writes happen in place.** The computer sends the new layout, the launcher checks it fits the
  grid, replaces its home-screen items in one step and reloads, with no restart. Items the launcher
  dropped (app not installed, no room) are listed.
- **Home app.** Edits refuse to run while Homa Lawn is not the phone's home app (`undo` and
  `snapshot restore` still run). If Android drops Homa Lawn as the home app after a crash, the
  command sets it back over adb, or tells you to pick it again on the phone.
- **Settings.** `prefs set` and restores touch only the launcher's known settings. The launcher's
  internal bookkeeping is never written.
- **Widgets** are written as placeholders the launcher fills in. Widgets with their own setup screen
  (clocks, for example) need one tap on the phone, and the first widget may need Android's Create
  dialog unless you ticked "Always allow" there.
- **Wallpaper.** `wallpaper` sets the home screen; `--lock` the lock screen instead, `--both` both.
  `--crop center` scales the picture to cover the phone's screen and keeps the middle.
- **Screenshots** are limited to about three a second.

## Nova import

`homalawn nova-import FILE` moves a Nova Launcher home screen (a `.novabackup` file: make one in
Nova's settings) onto the phone: grid, dock, screens, folders, apps and widgets. The phone converts
it with Lawnchair's own "Restore Nova backup" converter (only the layout and settings are sent),
then the computer saves the current home screen, settings and grid, sets the grid the backup needs
and writes the layout. It prints what was placed and what was left out and why (not installed,
outside the grid, a Nova-only action…), and which widgets wait for a tap. `--dry-run` reports the
same without changing anything. `--glance-row` / `--no-glance-row` is the "Add extra row to show At
a Glance" switch of the restore screen. `homalawn undo` puts the previous home screen back.

## Environment variables

| Variable | Meaning |
|---|---|
| `HOMALAWN_PHONE` | Which paired phone, by name or the start of its id (as `--phone`) |
| `HOMALAWN_SERIAL` | Use adb to this device, never the link (as `--serial`) |
| `HOMALAWN_PACKAGE` | Which Homa Lawn app adb talks to (`app.homalawn`, or `app.homalawn.debug` for the test build); default: the one installed |
| `HOMALAWN_CONFIG` | The `config.toml` file; this computer's key (`link/`) and `look.json` live next to it. Default `~/.config/homalawn/config.toml` |
| `HOMALAWN_DATA` | The data folder holding `snapshots/` (default `~/.local/share/homalawn`) |
| `HOMALAWN_STATE_DIR` | The service's state folder (default `~/.local/state/homalawn`) |
| `HOMALAWN_SOCKET` | The service's socket (default `$XDG_RUNTIME_DIR/homalawn/homalawnd.sock`) |
| `HOMALAWN_RECIPES` | A folder of agent recipes to use instead of the ones in the package |
| `HOMALAWN_RELEASES_REPO` | The GitHub repository (`owner/name`) the phone app is downloaded from (default `g00rek/homalawn`); another repository needs `HOMALAWN_RELEASES_CERT_SHA256` too |
| `HOMALAWN_RELEASES_CERT_SHA256` | With `HOMALAWN_RELEASES_REPO` set to a fork: the SHA-256 (hex) of that fork's signing certificate, which its releases must carry |
| `HOMALAWN_OMARCHY_STATE` | Where Omarchy's current theme is read from (default `~/.local/state/omarchy/current`) |

Names from before the project was called Homa Lawn (`HOMAR_*`, `OMALAWN_*`) are still read when the
new name is not set.

`config.toml` may hold two settings, `serial` (the adb device) and `package`; the variables above
win over them.
