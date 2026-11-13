# Troubleshooting

This guide covers the problems people run into most, from the phone going offline to refused
updates, and what each error message means.

## The phone shows as offline while it sleeps

The window and the bar say the phone is offline, and it comes back as soon as someone picks the
phone up. **Run in background is not allowed:** Android then cuts Homa Lawn's network while the
phone sleeps. On the phone: Homa Lawn's settings › **Permissions** › **Run in background** ›
**Allow** (in Android: App battery usage › Allow background usage). On a Samsung phone also add
Homa Lawn to **Never auto sleeping apps** (in Samsung's battery settings, under Background usage limits). The
window's Overview shows this permission for each phone. See
[Run in background](setup-phone.md#run-in-background).

## The first tap after going home does nothing (Samsung)

On a Samsung phone with gesture navigation, the swipe that goes home is drawn by Samsung's own
launcher, which still handles gestures and recent apps. Its animation lasts about 0.8 s, and until
it ends every touch goes to Samsung's launcher, not to Homa Lawn: the home screen is visible but
the first tap or swipe is lost. This happens with every launcher that is not Samsung's.

The animation follows Android's animation speed. Make it shorter:

- On the phone: Settings › Developer options › **Animator duration scale** › 0.5x (about 0.45 s
  without touch).
- Shorter still, from a computer with adb: `adb shell settings put global animator_duration_scale 0.1`
  (about 0.16 s; `1` puts it back). All animations on the phone get faster with it.

## No phone is found

- **Find phones (or `homalawn pair`) finds nothing.** Finding phones by themselves needs the phone
  and the computer on the same network and Avahi on the computer (the `avahi-browse` command and a
  running `avahi-daemon`); without it, `homalawn pair` says so. Type the phone's address instead:
  in the window's address field, or `homalawn pair 192.168.1.50`. The address is under the phone's
  Settings › About phone › Status (IP address), or in the Wi-Fi network's details.
- **Homa Lawn must be running on the phone**: open it (press Home) once after installing or
  rebooting.
- **The link worked before and now does not.** Check the phone is online (Tailscale up, if you use
  it) and allowed to [run in background](#the-phone-shows-as-offline-while-it-sleeps). Pin its
  address with `homalawn addresses add HOST` or **Overview › Addresses**; `homalawn addresses`
  shows what is tried and what answered last.

## Installing the phone app

- **The phone's browser cannot open the QR code's address.** For a release (an address on
  github.com) the phone needs internet access. For a build of your own served from the computer
  (an `http://` address), the phone must be on the same Wi-Fi (or Tailscale) as the computer. A firewall on the computer may block port 47101; if ufw is
  active, `homalawn update --download` prints the exact line that opens it for your network, of the
  form `sudo ufw allow from 192.168.1.0/24 to any port 47101 proto tcp comment homalawn-download`.
- **Google Play Protect warns or blocks the install.** Homa Lawn is not on the Play Store, so Play
  Protect does not know it: **More details › Install anyway**. If it does not let you, use
  `homalawn setup --new` over Wireless debugging.
- **A Samsung phone blocks the install outright.** Auto Blocker is on: Settings › Security and
  privacy › **Auto Blocker**, off for the install.
- **The install hangs** (with `setup` or `update`): look at the phone; a Play Protect or install
  prompt may be waiting.

## Screen control

- **"Screen control is off on the phone…"** (`screen_off`): turn it on in Homa Lawn's settings ›
  Permissions › **Screen control** (in Android: Accessibility › Downloaded apps › Homa Lawn › Use
  Homa Lawn).
- **Android says "Restricted setting"** and the switch is grey: Settings › Apps › Homa Lawn › **⋮** ›
  **Allow restricted settings**, then try again. Android does this for apps installed from a
  browser.

## Updates

- **"The phone refused it: this build is signed with another key than Homa Lawn on the phone"**
  (`wrong_key`). A phone takes an update only when it is signed with the same key as the Homa Lawn
  it runs. The official releases are signed with Homa Lawn's release key; your own builds with your
  own key; the test build with the Android debug key. Moving between them needs an uninstall, which
  wipes Homa Lawn's home screen and settings and its pairing key, so Homa Lawn never does it for
  you. Either keep updating from the same source, or save what you need, uninstall Homa Lawn on the
  phone, install the other build and pair again.
- **"The phone refused it: Homa Lawn on the phone is newer than this build."** Nothing to do; the
  phone already has something newer.
- **"There is no Homa Lawn release on GitHub yet…"**, **"GitHub could not be reached…"**,
  **"…not signed with Homa Lawn's key, so it was not used"**: the computer could not get a release
  it trusts. Check the internet connection and try again, or pass a file you trust with `--apk`.
- **"…runs Homa Lawn's test build, which comes only from a build on this computer"**: the test
  build is never replaced by the release; build it, or pass `--apk FILE`.
- **The update does not install by itself.** Allow **Install updates** in Homa Lawn's settings ›
  Permissions; until then tap the update notification on the phone, then **Update**. A locked phone
  installs at its next unlock.

## Error messages

| Code or message | What happened | What to do |
|---|---|---|
| "Something other than <phone> answers at …" (`pin_mismatch`) | Something answered at the phone's address with another certificate. Nothing was sent to it. | If the phone was reset or Homa Lawn reinstalled: `homalawn unpair`, then `homalawn pair HOST`. Until then `--via adb`. Otherwise check what is at that address. |
| "<phone> no longer trusts this computer…" / "<phone> forgot this computer…" (`not_paired`) | The phone forgot this computer. | `homalawn pair` (after `homalawn unpair` when it asks). |
| "… did not answer over the link … Using adb instead." | No paired phone answered at any address, so the command used adb. | See [No phone is found](#no-phone-is-found). |
| "Pairing is closed on the phone. On the phone: unlock it and show its home screen…" (`pairing_closed`) | Pairing is not open on the phone; the computer keeps asking for 5 minutes. | Unlock the phone and show its home screen, or open Homa Lawn's settings › Computers › Pair a computer. |
| "The phone cannot show the pairing request because notifications for Homa Lawn are off…" (`notifications_off`) | The phone cannot show the pairing question. | Allow notifications (Homa Lawn's settings › Permissions › Notifications), then pair again. |
| "The phone is already showing another pairing request…" (`pair_busy`) / "too many pairing requests in the last 10 minutes" (`rate_limited`) | Another pairing question is open, or three requests came within 10 minutes. | Answer it or let it expire (2 minutes); the message says how long to wait. |
| "pairing requests from the phone itself are not accepted" (`local_pairing`) | The request came from the phone's own address. | Pair from the computer over the network. |
| "Several phones are paired (…)" | More than one paired phone and none named. | `--phone NAME` or `HOMALAWN_PHONE`; `--serial` for adb. |
| "snapshot … was taken on …, not on this phone" / "does not say which phone it was taken on" | Restoring another phone's (or an unknown) snapshot. | `snapshot restore NAME --force`, only if it really is this phone's. |
| "Android does not let Homa Lawn read the current wallpaper on this phone…" | The wallpaper was set on the phone, so it cannot be saved before it is replaced ([why](theme-sync.md#wallpaper-backups)). | That one change with `--via adb`, or `--no-backup`. |
| "Homa Lawn needs access to photos to save the current wallpaper first…" | Android needs one tap to allow it. | Tap the Homa Lawn notification on the phone and allow it, then run again. |
| `busy` | The launcher is still loading, or another change or screen action is running. | Wait a few seconds and run again. |
| `rate_limited` (screenshots) | More than about three screenshots a second. | Slow down. |
| "Screen control is paused while the phone shows a pairing request…" (`pairing_open`) | The phone is asking whether to pair a computer. Screen control cannot tap or read anything until a person answers. | Answer the request on the phone (Accept or Reject), then run again. |
| `stale_node` | The item id is from an older `screen tree`. | `screen tree` again and use the new id. |
| `no_window` | No app window to read right now (an app is opening). | Wait a moment, `screen tree` again. |
| `not_editable` | `screen type` on something that is not a text field. | Pick the field's id from `screen tree` (`--node`). |
| `gesture_failed` | Android cancelled the tap or swipe (the screen changed under it). | `screen tree`, then try again. |
| `shot_failed` | Android refused the screenshot (a secure window). | Use `screen tree` instead. |
| `not_found` | `screen launch` of a package with nothing to open. | `homalawn apps` for the exact package. |
| "Homa Lawn needs the openssl command…" | `openssl` is missing; it makes this computer's pairing key. | Install `openssl`. |

## After a crash on the phone

If the launcher crashes, Android normally restarts it and the link within a few seconds. After
Android's "keeps stopping" dialog it may drop Homa Lawn as the home app: press Home on the phone
and pick Homa Lawn again (over adb, `homalawn` sets it back by itself).

## Logs

```sh
journalctl --user -u homalawnd -f          # the service on the computer
adb logcat | grep -i omalawn               # the phone, when it is on adb
```

When you [report a problem](https://github.com/g00rek/homalawn/issues), replace addresses, phone
names and serials with placeholders.
