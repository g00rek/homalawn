# Patching Lawnchair

This document covers how the phone app is built from Lawnchair and our patches: requirements, the
app's identity and version, the patch policy, the patches, changing and rebasing them, and signing.

The phone app is upstream Lawnchair with the patches in `patches/` applied in order. `lawnchair/` is
a clone of upstream (gitignored); `scripts/build-lawnchair.sh` resets it to the upstream commit
named by the first word of `patches/BASE` (now Lawnchair 16-dev `59396cd`, the nightly
CI_5432-59396cd of 2026-11-02), applies every patch and builds Homa Lawn's APKs:

- the **release** build `app.homalawn`, what phones run: not debuggable, shrunk by R8, signed with
  a release key (see [Signing](#signing));
- the **test** build `app.homalawn.debug`, labelled **Homa Lawn Test** on the phone, for the
  emulator: debuggable, with the shell-only test hooks the device tests use, signed with the
  Android debug key. It installs next to the release build.

```sh
scripts/build-lawnchair.sh            # both; without a signing key only the test build, and it says so
scripts/build-lawnchair.sh release    # only the release build (stops at once without a signing key)
scripts/build-lawnchair.sh debug      # only the test build (needs no signing key)
scripts/build-lawnchair.sh --dry-run  # say what would be built, build nothing
```

The script prints each APK's path, release first. They land in
`lawnchair/build/outputs/apk/lawnWithQuickstepHomalawn/{release,debug}/` as
`HomaLawn-<versionName>-<buildType>.apk`. Keep the paths quoted: they contain a `+`.

## Requirements

- A **full git clone** of this repository (the build number comes from its history; a source
  archive or a shallow clone is refused).
- **JDK 21** (`mise.toml` pins Temurin 21; `mise install` sets it up).
- The **Android SDK** (`ANDROID_HOME`, or `local.properties` in `lawnchair/`, which the script
  keeps) with `platforms;android-37.2` and `build-tools;37.0.0`:
  `sdkmanager "platforms;android-37.2" "build-tools;37.0.0"`.
- The **Lawnchair clone**: `git clone https://github.com/LawnchairLauncher/lawnchair.git lawnchair`.
  The script fetches the pinned commit if the clone lacks it and updates the submodules.
- About 10 GB of disk for the clone and Gradle's caches. Gradle comes with the wrapper.

The script **hard-resets `lawnchair/`**. Never keep uncommitted work there.

## App identity and version

| | Release build | Test build |
|---|---|---|
| Package | `app.homalawn` | `app.homalawn.debug` |
| Name on the phone | Homa Lawn | Homa Lawn Test |
| Debuggable | no | yes (`run-as` and the shell-only test hooks) |
| Signed with | a release key (Homa Lawn's for published builds, or your own) | the Android debug key |

Both are built by our `homalawn.gradle` (patch 0019; one `apply from` line in upstream's
`build.gradle`): a `homalawn` product flavor, the signing, the version, the APK name, and the
Lawnchair name replaced by Homa Lawn in the strings a person sees (generated from upstream's strings
in every language; credits and upstream's own statements untouched). Class names inside stay
Lawnchair's (`app.lawnchair.*`); only the package differs. The old app `app.lawnchair.debug`
(Lawnchair's debug build, which early phones ran) is recognised but no longer supported: `update`
and `setup` refuse it.

**The build number** is the commit count of HEAD plus the number in `patches/BUILD_OFFSET`
(`scripts/build-number.sh`). The offset exists so that a history that starts over (a fresh public
repository) never numbers a build below one a phone already runs. From it:

- versionCode = upstream's versionCode + the build number;
- versionName = `<upstream major>.0-dev.<upstream base>+homalawn.<build number>`, for example
  `16.0-dev.59396cd+homalawn.325`, with `.dirty` added when the patches or the build scripts have
  uncommitted changes;
- the release tag is `build-<build number>` (see [releasing.md](releasing.md)).

The same commit always gives the same version, and every later commit a larger one. The phone's
Settings › About shows "Homa Lawn 16" and "build <N>".

## Patch policy

So that following Lawnchair stays cheap:

1. **A setting first.** If a Lawnchair setting can do it, use the setting (`homalawn prefs set`),
   not a code change.
2. **Our own folder, thin hooks.** New behavior is a new class in
   `lawnchair/src/app/lawnchair/omalawn/`, registered from upstream files with a short hook (a line
   or two). Never rework an upstream mechanism from inside. For example, the "Search (instant)"
   gesture is its own handler registered in the gesture config, not an edit of Lawnchair's search
   gesture.
3. **Generic launcher bugs go to Lawnchair.** A bug every Lawnchair user has is reported to
   [Lawnchair](https://github.com/LawnchairLauncher/lawnchair/issues), so our patch can disappear
   when they fix it.

Keep upstream copyright notices intact in any upstream file a patch touches. New files carry no
upstream notice and fall under this repository's license.

## The patches

| Patch | What it adds |
|---|---|
| `0001` | The control provider (answers only the adb shell) |
| `0002` | In-process layout export and import (no database swap, no restart) |
| `0003` | The link: TLS server, pairing, the link service, mDNS, crash recovery |
| `0004` | Screen control (accessibility service) |
| `0005` | App updates over the link |
| `0006` | Lock screen wallpaper and screen size |
| `0007` | Silent updates once "Install unknown apps" is allowed |
| `0008` | Ask to be the default home app on first run |
| `0009` | Update status; a locked phone installs at its next unlock |
| `0010` | A deferred update prepares its session under the update lock |
| `0011` | The app is called Homa Lawn (launcher label and Settings) |
| `0012` | Nova backup conversion with Lawnchair's own converter. On 16-dev it also brings the converter up to 15 beta3's version (At a Glance row shift, subgrid flag, rounding, dropping items outside the grid), which upstream has not forward-ported yet; that part can go when upstream catches up |
| `0013` | The app icon (made by `assets/make_icons.py`; see [`assets/README.md`](../../assets/README.md)) |
| `0014` | A setting for the space between a home-screen icon and its label (`homar_label_gap_dp`; its meaning since 0025) |
| `0015` | A "Search (instant)" gesture: app search with the keyboard at once |
| `0016` | App icons and widget previews as the home screen draws them, for the desktop editor |
| `0017` | The phone's wallpaper screen in settings (named Wallpaper & style since 0033): previews of the lock and home screens and the wallpaper & style model the window edits (a Lock screen panel and a Home screen panel, always set separately; per screen keep, a wallpaper of the followed theme or a gradient from its colors, and a darken / lighten layer; parallax for the home screen while it shows a theme wallpaper), a draft sent by Apply (list and choice format 3, see `phonewall.py`) |
| `0018` | A "Homa Lawn" icon in the app drawer and search that opens the settings (like Nova's "Nova Settings"); the launcher's own home entry stays out of the drawer |
| `0019` | Homa Lawn's own app identity, in our `homalawn.gradle` (one `apply from` line in upstream's `build.gradle`): the `homalawn` flavor (`app.homalawn`, `.debug` for the test build), release signing with our key, the growing version, and the Lawnchair name replaced by Homa Lawn in the strings a person sees (generated from upstream's strings in every language; credits and the About screen untouched) |
| `0020` | A quiet link: no status notification and no foreground service. The link runs in the launcher's process from its start; a plain sticky service keeps the process up while Android allows it. Reminders for the home app and running in the background are posted only while one is missing (each asked once); "Pair a computer" moves to a row in Homa Lawn's settings. Reachable behind other apps and in Doze on Android 15+ only with the battery-optimization exemption (Run in background) |
| `0021` | `launcher.device_profile`: where the home screen really draws (screen, insets, the workspace's cells, icon and label sizes, the dock), for the window's home-screen preview |
| `0022` | Themed (monochrome) icons without an icon pack: Lawnchair no longer switches `themed_icons` off at start when no themed pack (Lawnicons) is installed, so Launcher3's mono theme draws the monochrome layer apps ship in their adaptive icons (Android 13+); the "Themed icons" choice in Lawnchair's icon settings is enabled without a pack too (`tests/test_device_themed_icons.py`) |
| `0023` | Themed icon colors follow the launcher's colors: the accent's swatches, light/dark mode and two shared settings, `homalawn_icon_color` (the symbol) and `homalawn_icon_bg_color` (the plate), `"#rrggbb"`, empty = the accent's color, are part of the launcher's icon state, so any change (link, Lawnchair's settings, the system's night mode) reloads the icons at once and a restart keeps them; icons that bake the colors in (icon-pack and clock icons) are rebuilt too (`tests/test_device_icon_tint.py`) |
| `0024` | `launcher.device_profile` as one reading in every state: the portrait home screen's `DeviceProfile` (the object the launcher lays out with) put through the same sums as Workspace, CellLayout, ShortcutAndWidgetContainer and Hotseat, never measured from views, so it is the same whether the launcher is shown, the phone is locked, the app was just updated or no launcher activity exists; the debug build's `debug_home_views` reads the views back for `tests/test_device_geometry_states.py`, which checks the two agree to the pixel |
| `0025` | The label gap is the visible distance in dp from the bottom of the icon's shape to the top of the label's capitals (0, also unset = touching, never overlapping), on the fixed grid: label padding = round(gap × density) − the transparent band below the shape − the label font's cap inset (the label's own font at its own size), fitted once the cell's final height is known so the gap moves the label and nothing else; held at the most the cell has room for, which `launcher.device_profile` reports as `icon.label_gap` (`dp`, `max_dp` and the parts of the sum) so the window offers only reachable gaps and draws each as the phone does; `debug_home_views` reads the label's padding and cap inset back from the view |
| `0026` | No Lawnchair name where a person reads: Lawnchair's announcements (lawnchair.app's live information, e.g. "a first look at Lawnchair 16") no longer show at the top of the settings, through Homa Lawn's default `config_default_live_information_show_announcements=false` in `homalawn/res` (in the `lawnWithQuickstepHomalawn` source set, so it wins over `lawnchair/res`; live information stays on for its remote defaults); Settings › About is Homa Lawn's own screen (`omalawn/HomaLawnAbout.kt`, one hook in `PreferenceNavigation`): the app's icon, name and version, then "Built on Lawnchair" with a row to lawnchair.app and the open-source licenses (Lawnchair's links, team list, nightly updater and privacy policy are not shown); five strings that name Lawnchair only in some translations join `BRAND_KEYS`; the suggested backup file name is `HomaLawn_Backup …` (the `.lawnchairbackup` format unchanged); our own phone messages (Nova import errors, screen control off) say Homa Lawn |
| `0027` | Homa Lawn's About says what Homa Lawn is and what it adds: "What Homa Lawn is" (the phone as part of the Omarchy desktop; no Homa Lawn server between phone and computer) with rows to omarchy.org and github.com/omacom/omarchy; then under "Built on Lawnchair" a paragraph (Homa Lawn is Lawnchair with a layer on top; what the Lawnchair team made, what Homa Lawn adds, Homa Lawn problems come to us), then rows to lawnchair.app, Lawnchair on GitHub and the open-source licenses |
| `0028` | Edit-mode page reorder moves pages (an upstream fix, from Lawnchair's #6809): `Workspace.movePageGroup` changes only the page order (`mScreenOrder`, saved as `workspace_screen_order`) and no longer also rewrites the items' screen ids (`ModelWriter.moveWorkspaceScreensInDatabase`, removed with its strip-deferral flag), which moved every item back and could put top-row items under screen 0's search bar, where the loader deletes them; a page moves with its items, screen 0 keeps the date bar, the default home page follows its page. The page strip shows a thumbnail of each page (its CellLayout recorded into a `Picture`, drawn when the strip opens and after each drag), its chips are keyed by screen id, a move no longer rebinds the chip being dragged, and the strip is centered and fits five pages on a 411 dp wide screen; the desktop reads `workspace_screen_order` and numbers pages by position (`layout.visible_screen_ids`) |
| `0029` | A tap on the empty part of the "Search (instant)" page goes home: below the last result, or anywhere while there are none, never on the search field's bar (a tap: no move past the touch slop, up before a long press). Upstream's search page swallows such taps (the results list fills the page below the field and consumes them, and `ActivityAllAppsContainerView.onTouchEvent` consumes the rest while searching). `HomarSearchGesture.onSearchTouch`, one hook in `LauncherAllAppsContainerView.dispatchTouchEvent`, watches without consuming, so scrolling, results, the field and upstream's swipe down that closes the drawer are untouched; only in a session opened by the gesture |
| `0030` | The computers on the phone: Settings › Computers lists each paired computer (online now / last seen, paired date) with Forget one and Forget all; the calling computer's fingerprint reaches every command; a forgotten computer is told (`link.hello` `forgotten`, `not_paired` data) and removes the phone; four link connections per computer and sixteen in all, a computer's fifth closes its oldest |
| `0031` | One wallpaper & style per paired computer kept on the phone (key, `changed_at`, theme, color settings, home and lock pictures), which computer changed last, and which one the phone shows; a computer unpaired or forgotten loses its entry (`tests/test_device_look_book.py`) |
| `0032` | The paired `link.hello` also reports `addresses`: the phone's interface addresses (up, not loopback, not link-local, no `%scope`); the computer keeps the Tailscale ones (100.64.0.0/10, fd7a:115c:a1e0::/48) as the phone's `learned` addresses (`tests/test_device_computers.py`) |
| `0033` | Homa Lawn's settings in three groups (Your computer: Computers, Wallpaper & style, Permissions; Launcher: Lawnchair's rows untouched; About), the **Permissions** screen (Home app, Run in background, Notifications needed; Screen control, Install updates optional; each opening its Android page) and `launcher.ping`'s `permissions`, notification channels in one group "From your computer" (Pairing requests, Setup reminders, App updates) with Homa Lawn's own status icon, the test build labelled "Homa Lawn Test", About with the build number, project links, licenses, the Flux MIT text and the BIP-39 word list credit |
| `0034` | Computers holds only computers (one dialog per computer with its details and Forget), Wallpaper & style gets the **Source** card (Latest change or one computer) and one Apply bar for the whole screen, one shared "Replace current wallpaper?" dialog, and the pairing screen redone in Compose to match the settings |
| `0035` | Screen control stands still while pairing is on screen: from a pair request until it ends, and while the pairing screen shows, every `screen.*` call but `screen.status` is refused with `pairing_open` (actions, the tree and the screenshot), so a paired computer can neither tap Accept nor read the four words |
| `0036` | Launcher3's app objects are built on the main thread, never by a request (`omalawn/MainBuilt.kt`, called first by `OmalawnCommands.dispatch`, which every link and provider request passes): a request that arrived while the app started could build them on its own thread, holding Dagger's device-profile and theme-manager locks while ThemeProvider waited for the main thread (WallpaperManagerCompat is created there only), while the main thread, starting the home screen, waited for the theme manager's lock; the app froze for good (`tests/test_device_startup.py`) |

## Changing a patch

1. Inside `lawnchair/`, apply the patches as commits on top of the pinned commit:
   ```sh
   cd lawnchair
   git reset --hard "$(awk 'NF {print $1; exit}' ../patches/BASE)" && git clean -fd -e local.properties
   git am ../patches/*.patch
   ```
2. Make your change in the commit of the patch that owns the file (`git commit --fixup` +
   `git rebase -i --autosquash`, or amend while that commit is checked out).
3. Re-export every changed patch from its own commit (find it with `git log --oneline`), for
   example patch 0004:
   `git format-patch -1 --stdout --no-signature <commit> > ../patches/0004-omalawn-screen.patch`.
4. Back in the repository root, run `scripts/build-lawnchair.sh` to prove all patches apply clean
   and the APK builds. (Export first: the script resets `lawnchair/`.)
5. Install on the test emulator and run the device tests (see [testing.md](testing.md)).

A new patch gets the next number and a name `NNNN-homalawn-<topic>.patch`.

## Rebasing onto a newer Lawnchair

The base is the first word of `patches/BASE` (the rest of the line is a comment). Pick a commit
upstream CI built green (a nightly), never an untested branch head.

1. In a scratch clone **outside** `lawnchair/` (the script hard-resets it), check out the OLD base
   and apply the patches as one commit each (`git am patches/*.patch`).
2. Rebase them with a real three-way merge: `git rebase --onto <new> <old>`. Resolve each conflict
   in the commit that owns the file, and fix compile errors from upstream API changes in the commit
   that introduced the line. Keep the patch policy: our code stays in `omalawn/`, upstream files
   keep only thin hooks.
3. Re-export every patch from its own commit:
   `git format-patch -1 --stdout --no-signature <commit> > patches/00NN-<name>.patch`.
4. Put the new commit in `patches/BASE` and run the script until every patch applies clean and the
   APK builds; then run the unit and device tests.

For example, the move from 15 beta3 to 16-dev needed real conflict work only in the hooks: the app
and launcher classes (link start, the folded `onNewIntent`), the label-gap hooks in the refactored
`DeviceProfile`, the gesture option list (moved to `GestureHandlerOptions.kt`), the settings
navigation and dashboard, and `AppFilter`. Our own code needed the 16 API names
(`LauncherAppState.getInstance`, `isModelLoaded()`, `getCreatedContext`, `LauncherPrefs.getPrefs`,
the icon cache's `CacheLookupFlag`, `newIcon` creation flags, `PreferenceGroup` without dividers).
One behavior needed a new hook: on 16 the search field loses focus when the keyboard goes down,
so Back from "Search (instant)" reaches `LawnchairLauncher.onStateBack`; patch 0015 sends it home
there (without it the drawer stayed open on an empty page).

Thanks to the patch policy most of the code is in our own folder and a rebase mostly touches the
hooks.

## Signing

A release build is signed with the key that `homalawn.gradle` finds: the properties file
`~/.config/homalawn/signing/homalawn-release.properties` (`storeFile`, `storePassword`,
`keyAlias`, `keyPassword`), or the one `HOMALAWN_SIGNING_PROPERTIES` names, or the variables
`HOMALAWN_SIGNING_STORE_FILE`, `HOMALAWN_SIGNING_STORE_PASSWORD`, `HOMALAWN_SIGNING_KEY_ALIAS` and
`HOMALAWN_SIGNING_KEY_PASSWORD`. Without a key a release build stops with that message; it is never
signed with another key.

**Your own key.** `scripts/make-signing-key.sh` makes one: an RSA 4096 key (PKCS12, valid 10000
days) in `~/.config/homalawn/signing/` with a random password in the properties file next to it,
both mode 600. It prints the certificate's SHA-256 and never replaces a key that is already there.

A phone accepts an update only when it is signed with the same key as the installed app. So:

- a phone running the **official release** cannot take a build signed with your own key, and a
  phone running your build cannot take the official release, without uninstalling Homa Lawn first
  (which wipes its home screen, settings and pairing key);
- **losing your key** means every phone running your builds has to uninstall Homa Lawn to take a
  new one. Keep a backup of the key and its properties file outside this machine.

**Published builds** are signed with Homa Lawn's release key, held by the maintainer. Its
certificate's SHA-256 is

```
eadef047d0f7401d545d0945e52e43c3461ae3b61a9d26be586de07847e8a7a9
```

(`CERT_SHA256` in `src/homalawn/release.py`, which refuses a downloaded release signed otherwise).
Check a build with `apksigner verify --print-certs <apk>`.

The test build is signed with the Android debug key (`~/.android/debug.keystore`, or under
`ANDROID_USER_HOME`).
