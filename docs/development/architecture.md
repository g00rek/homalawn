# Architecture

This document covers how Homa Lawn is put together: the desktop package, the link between computer
and phone, how writes and undo work, the desktop service's protocol, and the phone side.

Homa Lawn has a phone side and a desktop side.

- **Phone**: one app, a build of [Lawnchair](https://github.com/LawnchairLauncher/lawnchair) 16
  (upstream at the commit in `patches/BASE` plus the patch series in `patches/`, never a hard fork).
  The patches add a control layer that runs inside the launcher process: a link server, in-process
  layout import and export, screen control, app updates, the Homa Lawn settings screens and a few
  small launcher features.
- **Desktop**: the Python package `homalawn` (`src/homalawn/`): the CLI, the background service,
  the MCP server for agents and the omarchy-shell plugin.

```
 Omarchy computer                                            Android phone
┌───────────────────────────────────────────────┐           ┌──────────────────────────────┐
│ omarchy-shell                                 │           │ Homa Lawn (patched Lawnchair)│
│  └ homalawn plugin (bar icon, window, QML)    │           │                              │
│        │ unix socket, JSON lines              │           │ link server :47100 (TLS 1.3) │
│        ▼                                      │  mutual   │  ├ launcher.*  layout, prefs, │
│ homalawnd  (homalawn serve)                   │  TLS,     │  │             wallpaper,     │
│  ├ one link per paired phone ─────────────────┼──pinned──▶│  │             updates, Nova  │
│  ├ Omarchy theme follower                     │  certs    │  ├ screen.*    accessibility  │
│  └ app download server (:47101, for Add phone)│           │  └ link.*      pairing        │
│                                               │           │                              │
│ homalawn (CLI)  ─┐                            │           │                              │
│ homalawn mcp  ───┼─ ops ─ Session ─ Transport ┼──────────▶│ control provider (adb shell) │
│   (AI agents)    │                 ├ link     │   adb     │                              │
│                  │                 └ adb ─────┼──────────▶│                              │
└───────────────────────────────────────────────┘           └──────────────────────────────┘
```

## Desktop package

| Module | Role |
|---|---|
| `cli.py` | The Typer CLI. Thin: parses options, calls `ops`, prints. |
| `ops.py` | Every operation behind a command, shared by the CLI and the MCP server. Prints nothing: returns a `Result` or raises `OpError` with the exact line to show. |
| `mcp_server.py` | `homalawn mcp`: the ops as typed MCP tools over stdio, plus the agent rules; `phone_list` / `phone_select` choose among several paired phones. |
| `session.py` | High-level launcher operations: read, validate, apply with snapshot and rollback, undo. |
| `transport.py` | The `Transport` interface; `adb.py` (adb) and `link/client.py` (the link) implement it. |
| `layout.py`, `widgets.py`, `prefs.py`, `apps.py`, `wallcrop.py` | The launcher model: the home screen as JSON with validation, widget placeholders, the settings catalog (`prefs_catalog.json`), apps, picture cropping. |
| `snapshots.py` | History: local snapshots, each tied to one phone, with a `kind` (the window section) and a human `title`. |
| `nova.py` | Nova Launcher import (the phone converts, the computer applies). |
| `screen.py` | Screen control helpers (tree compaction, argument parsing). |
| `setup.py`, `adbpair.py` | `homalawn setup`: a phone over adb in one go; adb pairing by QR code. |
| `update.py` | App updates over the link, the `--download` server, and which APK to use (`--apk`, a checkout build, the newest release). |
| `release.py` | The phone app from GitHub releases: finds the newest one, downloads it into `~/.cache/homalawn/apk/`, checks its SHA-256 and reads its signing certificate from the APK Signing Block (v3, else v2), standard library only. |
| `appid.py` | Which Homa Lawn app a phone runs (`app.homalawn`, the test build `app.homalawn.debug`, the unsupported old `app.lawnchair.debug`) and where the builds in a checkout are. |
| `look.py`, `lookconf.py`, `gradients.py` | Omarchy themes, wallpaper cut and tint, the theme's palette to launcher colors, six gradients from a palette; per-phone Wallpaper & style settings in `look.json`, keyed by the phone's fingerprint. |
| `phonewall.py` | The phone's Wallpaper & style screen: the followed theme's wallpapers and gradients as thumbnails pushed to the phone; the phone's Apply (one choice per screen: keep, a wallpaper or a gradient) is read back by the service and applied. |
| `daemon.py` | `homalawn serve`: the desktop service. |
| `desktop.py` | `homalawn desktop install` / `uninstall`: the preflight checks, the systemd user unit and the shell plugin copy. |
| `agents.py` | The coding agents `homalawn mcp` is registered with: a registry (each agent with its verified add/remove command or file format), registration on install and by the service for agents installed later, and `agents.json` in the state folder recording what Homa Lawn added. |
| `chat.py` | The Agents page's chat: runs the Omarchy default agent headless, with only Homa Lawn's tools allowed. |
| `recipes.py`, `recipes/` | Recipes for agents, shipped in the package and read with `importlib.resources` (`HOMALAWN_RECIPES` overrides the folder). |
| `env.py`, `config.py` | Environment variables (with the older names), folders, `config.toml`. |
| `link/` | The link: `wire.py` (framing), `identity.py` (this computer's key and paired phones), `pairing.py`, `sas.py` (the four words), `words.txt` (the BIP-39 English word list), `discovery.py` (Avahi hints), `addresses.py` (each phone's addresses), `client.py` (`LinkTransport`). |
| `shell/` | The omarchy-shell plugin (QML); see [`src/homalawn/shell/README.md`](../../src/homalawn/shell/README.md). |

## The link

- **Transport**: TLS 1.3 on port 47100, client certificate required. Neither side uses a CA: each
  compares the other's certificate fingerprint (SHA-256 of the DER) with its pin. The phone's key
  is an EC P-256 key in Android Keystore; the computer's is made with `openssl` and kept in
  `~/.config/homalawn/link/`.
- **Wire**: newline-delimited JSON requests and replies (at most 1 MiB a line), plus binary frames
  (`{"id":n,"blob":len}` followed by the bytes, at most 32 MiB) for layout databases, pictures and
  APK chunks. Idle connections close after 60 s.
- **Methods**: `link.*` (hello, pairing, unpair), `launcher.*` (ping, layout export/import, apps,
  widgets, prefs, wallpaper, app info and updates, device profile, Nova conversion), `screen.*`
  (tree, tap, swipe, type, key, launch, shot). An unpaired computer can call only `link.hello` and
  the pairing methods. `launcher.ping` reports `permissions`: `{home, background, notifications,
  screen_control, install_updates}`, each true or false (missing from older phone builds).
- **Pairing**: the computer sends a commitment to a random value, the phone answers with its own,
  then the computer reveals. Both derive four words from SHA-256 over both certificates and both
  values (44 bits as four words of the BIP-39 English list); the phone shows them on its
  confirmation screen. Shared test vectors keep the Python and Kotlin implementations in step:
  `tests/vectors/sas.json`.
- **Finding the phone**: each phone's addresses live in its pairing record (`link/peers.json`) and
  are tried in one order: the address that answered last, the last LAN address that answered, the
  added ones, the Tailscale addresses the phone reported (only when this computer is on Tailscale),
  the address it paired through, then Avahi (`_omalawn._tcp`, no identity in the announcement; the
  pin decides). A phone that does not answer is retried at growing intervals, and at once when the
  computer's network changes or a person acts on it.

## Writes and undo

A write never swaps the launcher database from outside. The computer reads the current layout,
validates the new one against the grid, takes a snapshot, then sends the rows to the phone, where
Lawnchair replaces its home-screen items in one step and reloads (patch 0002). If the result is
wrong the snapshot is restored. Settings, grid changes, wallpapers, Nova imports and Wallpaper &
style applies are snapshotted the same way, so `undo` walks one list for all of them.

Each snapshot's `meta.json` records the phone, and, for History, the section it belongs to and
what changed (the number of changes, the theme, a setting's name). `snapshots.describe()` turns that
into `{kind, title}`: `kind` is one of `home`, `search`, `style`, `wallpaper`, `manual`,
`before_restore`, `other`; `title` is the line History shows, such as "Home screen: 3 changes",
"Wallpaper & style: Tokyo Night", "Saved by hand: <name>" or "Before a restore".

## The desktop service

`homalawn serve` (`daemon.py`) keeps one `LinkTransport` per paired phone, follows the Omarchy
theme (the current theme and background under `~/.local/state/omarchy/current/`) and serves the
shell plugin over a unix socket: `$HOMALAWN_SOCKET`, else
`$XDG_RUNTIME_DIR/homalawn/homalawnd.sock`. Only the same user may connect (`SO_PEERCRED`).

Protocol: one JSON object per line. Request `{"id": n, "method": str, "params": {}}`; reply
`{"id": n, "result": ...}` or `{"id": n, "error": {"code": str, "message": str}}`, where `message`
is one sentence a person can read. After `subscribe`, every change is pushed as
`{"event": "state", "state": {...}}`; pairing pushes `{"event": "pair", ...}` and the Agents chat
`{"event": "chat", ...}`.

Methods: `subscribe`, `phones`, `service.info`, `phone.info`, `phone.unpair`, `phone.addresses`,
`phone.addresses_set`, `pair.find`, `pair.start`, `pair.cancel`, `themes.list`, `look.get`,
`look.set`, `look.apply`, `look.gradients`, `home.get`, `home.apps`, `home.widgets`, `home.apply`,
`search.get`, `search.apply`, `undo`, `snapshots`, `snapshot.restore`, `app.update`, `app.serve`,
`app.serve_stop`, `agents.status`, `agents.connect`, `agents.changes`, `recipes.list`,
`chat.history`, `chat.send`, `chat.cancel`, `chat.new`.

Recent additions the window relies on:

- **Per phone, in the state**: `permissions` (passed through from the phone's `launcher.ping`;
  `null` from an older phone app) and `app_latest`, `{version, newer, source}` with `source`
  `"release"` (the newest GitHub release; offered only to a phone running the release app) or
  `"checkout"` (a build in this source checkout), or `null` when nothing is offered.
- **`app.serve`** `{}` → `{url, urls, expires_at, qr, version, source, file}`: offer the phone app
  for a new phone to download, as `homalawn update --download` does. `source` `"release"`: `url`
  is the release file's HTTPS download address on GitHub (checked here first), nothing is served
  on this computer and `expires_at` is `null` (no time limit). `source` `"checkout"`: served over
  plain HTTP at one random-token address on this computer's LAN and Tailscale addresses, port
  47101, 10 minutes (`expires_at` unix time). `qr` is the QR code of `url` as rows of 0/1. Errors:
  `no_app`, `no_network`, `port_busy`. **`app.serve_stop`** `{}` → `{stopped}`. While the app is
  offered, the service's state carries `serving`: `{url, urls, expires_at, version, source,
  message}` (`expires_at` may be `null`; `message` is always "The phone downloads Homa Lawn from
  GitHub." for a release, else the server's latest line), else `null`.
- **`snapshots`** rows are `{name, time, kind, title}` as described under
  [Writes and undo](#writes-and-undo).

The QML side of the contract is in
[`src/homalawn/shell/README.md`](../../src/homalawn/shell/README.md#backend-contract).

## The phone side

The patches are listed in [patching-lawnchair.md](patching-lawnchair.md#the-patches). Our Kotlin
code lives in `lawnchair/src/app/lawnchair/omalawn/` (the folder keeps the project's first name so
existing installs stay compatible); upstream files get only short hooks into it.

The phone app's settings have three groups: **Your computer** (Computers, Wallpaper & style,
Permissions: Homa Lawn's own screens), **Launcher** (Lawnchair's own settings, untouched) and
**About**. Android's surfaces carry only what Android owns: the accessibility service (screen
control), and notification channels in one group, "From your computer": Pairing requests, Setup
reminders and App updates.
