# Changelog

All notable changes to Homa Lawn. The format follows [Keep a Changelog](https://keepachangelog.com/)
and the desktop tool uses [Semantic Versioning](https://semver.org/). The phone app's releases are
numbered by build (`build-<N>`); each desktop release names the phone build it was made with.

## 0.1.0 — first public release

The first public release of Homa Lawn: an Android home screen built on Lawnchair 16, run from an
Omarchy computer.

### Phone app

- **Homa Lawn**, a build of Lawnchair 16 with a control layer on top: its own app (`app.homalawn`),
  its own name and icon (a phone on a sunset hill), signed with Homa Lawn's release key, updated in
  place with a version that grows with every build.
- **Settings in three groups**: **Your computer** (Computers, Wallpaper & style, Permissions),
  **Launcher** (Lawnchair's own settings, untouched) and **About**.
- **Computers**: every paired computer with **Online now** or **Last seen …**; tap one for its
  details and **Forget**; **Pair a computer**; **Forget all computers**.
- **Wallpaper & style**: the lock screen and the home screen each take one of the theme's
  wallpapers, a gradient made from its colors, or keep what they have, with a darken / lighten
  layer and parallax. **Source** picks which computer's wallpaper & style shows when several are
  paired (**Latest change** or one computer). One Apply bar; **Replace current wallpaper?** when the
  current wallpaper cannot be backed up.
- **Permissions**: Home app, Run in background and Notifications (needed), Screen control and
  Install updates (optional), each with its state, why it matters, and a tap to the right Android
  page. The settings row says **All set** or what needs attention.
- **Screen control** (optional): a paired computer or its agent can read and operate the screen
  of any app, through an accessibility service you switch on yourself.
- **Notifications only when a tap is needed**, in one group "From your computer": Pairing
  requests, Setup reminders and App updates. Nothing stays in the notification shade.
- **Launcher additions**: a "Search (instant)" gesture (app search with the keyboard up at once;
  a tap on the empty page closes it), a setting for the gap between icon and label, monochrome
  icons without an icon pack, tinted to the launcher's colors, and reordering home pages in edit
  mode that really moves the pages.
- The test build is labelled **Homa Lawn Test** and installs next to the release.

### Desktop window

- **A bar icon** in omarchy-shell: connected, offline, service down, a phone that needs attention,
  and updates in its tooltip.
- **The Homa Lawn window**: phones and **Add phone** in the rail, and per phone **Overview**,
  **Home screen**, **Wallpaper & style**, **Search & gestures**, **History** and **Agents**, plus
  **Service**.
- **Add phone › New phone**: a QR code the phone's camera opens to download Homa Lawn, the taps
  on the phone, and **Pair** with four words to compare. **This phone already has Homa Lawn** pairs
  one that runs it.
- **Overview**: connection, the app version with **Update available** and **Update app**, the
  phone's permissions, its addresses (add a Tailscale name to reach it away from home), **Unpair**.
- **Home screen**: drag apps and widgets onto a picture of the phone drawn from its real
  measurements, make folders, change the grid, icon and label sizes, add pages.
- **Wallpaper & style**: **Source** (Follow Omarchy, Fixed theme, Off), **Wallpaper** for the lock
  and home screens with a crop frame, **Colors & icons**, and a live preview.
- **Search & gestures**: the drawer's search sources and the home-screen gestures.
- **History** with plain titles ("Home screen: 3 changes") and **Restore**; **Undo** in the header.
- **Agents**: a chat with your Omarchy default coding agent using only Homa Lawn's tools, recent
  agent changes with Restore, coding agents with **Connect**, and recipes.
- One pattern everywhere: changes are a draft until **Apply**; questions are asked in place before
  anything that cannot simply be undone.

### Command line and agents

- `homalawn`: `phones`, `pair`, `unpair`, `status`, `connect`, `addresses`, `setup`, `update`,
  `layout get/apply`, `apps`, `place`, `remove`, `folder create`, `widgets`, `widget add`, `grid`,
  `prefs`, `look`, `wallpaper` (home, lock or both, cropped to the screen), `screenshot`, `undo`,
  `snapshot save/list/restore`, `nova-import`, `screen …`, `desktop install/uninstall`, `serve`,
  `mcp`, and `--version`.
- **Undo across everything**: every change to the home screen, a setting, the grid, the wallpaper
  or Wallpaper & style is saved first and can be undone; each snapshot belongs to one phone.
- **Nova Launcher import**: move a `.novabackup` home screen onto the phone, undoable.
- **Several phones and several computers**: name a phone with `--phone`; each phone keeps its
  addresses and learns its Tailscale addresses.
- **`homalawn mcp`**: the operations and screen control as tools for AI agents, with `phone_list`
  and `phone_select`, safety rules handed to the agent, and recipes for jobs inside other apps
  (shipped in the package).

### Install and updates

- `uv tool install git+https://github.com/g00rek/homalawn.git`, then `homalawn desktop install`:
  it checks for Omarchy first, says each step, starts the service and adds Homa Lawn's tool server
  to the coding agents it finds (`--no-agents` skips it; `desktop uninstall` takes it out and says
  what stays).
- **No building needed**: the phone app comes from the newest GitHub release, checked by SHA-256
  and signing certificate before it is used.
- **First install by QR code**, no developer options or cable; `homalawn setup --new` does
  everything over Wireless debugging for test phones.
- **Updates over the link**: silent once **Install updates** is allowed; a locked phone installs at
  its next unlock.
- Building from source is optional: `scripts/build-lawnchair.sh` (the test build without a key),
  `scripts/make-signing-key.sh` for your own key, `scripts/release.sh` for draft releases.

### Security

- **The link**: TLS 1.3 with pinned certificates on both sides, directly over your network or
  Tailscale; no cloud, no telemetry. Unpaired computers learn nothing about the phone.
- **Pairing needs a person**: four words on both screens and **Accept** on the unlocked phone;
  taps from overlays, accessibility or the notification do not count; requests are rate-limited.
  Screen control is paused while a pairing request is open, so a paired computer can neither
  tap **Accept** nor read the words.
- **Updates** are accepted only for the same app, signed with the same key, not older, with a
  matching checksum.
- Access is removed from either side: **Forget** on the phone, **Unpair** on the computer.
