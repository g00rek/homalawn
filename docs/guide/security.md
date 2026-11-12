# Security and privacy

This guide covers who can control a phone through Homa Lawn, how the connection and pairing are
protected, what a paired computer can do, how to take access away, and how to check a release.

To report a vulnerability, see [SECURITY.md](../../SECURITY.md): privately, not in a public issue.

## The link

- **Mutual TLS with pinned certificates.** The phone runs a small TLS 1.3 server on port 47100.
  Each side has its own key and pins the other's certificate when they pair; there is no
  certificate authority. The phone's key stays in Android Keystore; the computer's is
  `~/.config/homalawn/link/key.pem` (mode 600), made with `openssl`.
- **Only paired computers get answers.** Any other computer can only ask to pair, and only while
  pairing is open on the phone. It learns nothing about the phone: not its name, model or key. The
  network announcement (`_omalawn._tcp`) carries no identity either. At most 10 connection attempts
  a minute are accepted from an address that is not a paired computer.
- **No cloud.** The computer connects to the phone directly over your network or a VPN such as
  Tailscale. Nothing goes through a third-party server, and Homa Lawn has no telemetry. The computer
  contacts GitHub only to look for and download the newest phone app release. A new phone
  downloads the app from the release's own HTTPS address on GitHub, never over plain HTTP from the
  computer (only a build you made yourself is served that way, on your own network).

## Pairing needs a person on the phone

- Pairing is open only while Homa Lawn's home screen shows on the unlocked phone, or while its
  **Computers › Pair a computer** screen is open (5 minutes at most).
- Both screens show four words derived from both certificates and fresh random values, with a
  commitment, so a fake phone cannot search for matching words. A computer in the middle would
  produce different words.
- The request is confirmed only by **Accept** on Homa Lawn's own confirmation screen, a real tap on
  the unlocked phone. Taps while another app draws over the screen, accessibility "clicks" and
  taps while locked are ignored, and the notification cannot accept (an app allowed to read
  notifications could press its buttons).
- At most one request at a time and three per 10 minutes. Requests from the phone itself (an app
  on it, or `adb forward`) are refused.

## What a paired computer can do

| Area | Allowed | Notes |
|---|---|---|
| Home screen | Read and replace the layout (apps, folders, dock, widgets), change the grid | Every change is saved on the computer first and can be undone. |
| Launcher settings | Read and set the launcher's known settings | Its internal bookkeeping is never written. |
| Wallpaper & style | Set home and lock screen wallpapers and launcher colors; read the current wallpaper where Android allows it | Android 13+ does not let a launcher read a wallpaper set on the phone. |
| Apps and widgets lists | Read | Launchable apps and available widgets only. |
| Homa Lawn updates | Send a new Homa Lawn | Only the same app, signed with the same key, not older, with a matching checksum. It installs without a tap only if you allowed **Install updates**. It cannot install any other app. |
| Screen control | See and operate everything on the screen (read the screen, tap, type, swipe, keys, open apps, screenshots) | **Only if you switched on Screen control** (Homa Lawn's settings › Permissions, or Android's Accessibility settings). Off by default. |

Android's Accessibility settings show when Homa Lawn's screen control is on. Homa Lawn is
deliberately not declared as an accessibility *tool*, so on Android 14 and newer, views that apps
mark as sensitive (banking or password screens, for example) stay hidden from it, and Android's
Advanced Protection mode may switch it off.

A coding agent you connect (see [Agents](agents-mcp.md)) acts as the computer does, with the
safety rules described there.

## Taking access away

- **On the phone**: Homa Lawn's settings › **Computers** › tap the computer › **Forget**, or
  **Forget all computers**. A forgotten computer can no longer reach the phone; it is told at its
  next contact and removes the phone from its own list.
- **On the computer**: **Unpair** on the window's Overview, or `homalawn unpair NAME`. Both sides
  forget each other.
- **Screen control** can be switched off at any time in Android's Accessibility settings.
- **adb**: anyone with adb access to a phone already controls it. Turn Wireless debugging off when
  you do not need it; Homa Lawn's link does not use it.

## Release builds and their signature

The phone app published on the [releases page](https://github.com/g00rek/homalawn/releases) is
built by the project's maintainer from the tagged commit and signed with Homa Lawn's release key.
The SHA-256 of its signing certificate is:

```
eadef047d0f7401d545d0945e52e43c3461ae3b61a9d26be586de07847e8a7a9
```

`homalawn` checks every release it downloads (from the window, `setup` or `update`): the file's
SHA-256 must match the release, and its signing certificate must be this one; otherwise it is not
used. A new phone downloads the same release file straight from GitHub over HTTPS; the QR code names
that exact file.

What these checks are, and are not:

- **The trust anchor for a first install is the SHA-256 that GitHub serves over HTTPS for the
  release.** Every address `homalawn` uses for a release is HTTPS on GitHub, and it follows GitHub's
  redirects only over HTTPS.
- **The certificate comparison catches a wrong or mislabelled file**: another key, a test build, an
  APK carrying a second, planted signing block. It reads the certificate each signer names and is
  strict about the file's shape (no ZIP comment, the signing block exactly where Android looks for
  it, exactly one signer in every signature scheme present, v2, v3 and v3.1), but it **does not
  verify the APK's cryptographic signature**. Android does that when it installs the file, and for an
  update the phone refuses any key other than the one its Homa Lawn was installed with.
- A fork set with `HOMALAWN_RELEASES_REPO` must also name its own certificate with
  `HOMALAWN_RELEASES_CERT_SHA256`; `homalawn` refuses its releases otherwise.

To check a release by hand:

```sh
sha256sum -c HomaLawn-<version>-release.apk.sha256
apksigner verify --print-certs HomaLawn-<version>-release.apk   # "Signer #1 certificate SHA-256 digest" must be the value above
```

(`apksigner` is part of the Android SDK build-tools.)

The release build is not debuggable. Its control provider, used by `homalawn` over adb, answers
only the adb shell. The test build (**Homa Lawn Test**, `app.homalawn.debug`) is debuggable and has
extra shell-only test hooks; it is meant for an emulator, not for a phone you use.
