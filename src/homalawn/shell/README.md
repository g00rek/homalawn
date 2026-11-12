# Homa Lawn plugin for omarchy-shell

The Omarchy face of Homa Lawn: the Homa Lawn mark in the bar and the Homa Lawn window. It talks to the
Homa Lawn service (`homalawn serve`, systemd unit `homalawnd`) over its unix socket. `homalawn desktop
install` copies this folder to `~/.config/omarchy/plugins/homalawn/`, leaving out `tools/` and this
README (they are for development).

| Kind | File | Job |
| --- | --- | --- |
| `service` | `Service.qml` | Always loaded. Owns the one connection (`Backend.qml`) and the active `colors.toml` text. |
| `bar-widget` | `BarWidget.qml` | The mark (the phone from Homa Lawn's picture: its outline and app square): outline in the theme's accent and app square in its red while a phone is connected, both muted otherwise, dimmed while the service is down, and all in the warning colour while a phone needs attention (the service reports an error for it, or it is connected and has not allowed the home app, running in background or notifications). Tooltip lists phones (`Pixel 8 · connected`, `Pixel 8 · needs attention: run in background is not allowed`) and a line per phone with an update available. The rules live in `HomaLawn/Status.js`. Left click toggles the window. |
| `panel` | `Panel.qml` | `FloatingWindow` titled `Homa Lawn`, 1180×760, minimum 900×640. |

Open the window: `omarchy-shell shell toggle homalawn '{}'`, or on a page:
`omarchy-shell shell summon homalawn '{"page":"look"}'` (`overview`, `home`, `look`, `search`, `history`, `agents`, `add`, or
`settings` for the Service page; `service` names the same page).

## Layout

```
HomaLawn/
  qmldir           Theme (singleton), HomaLawnView
  HomaLawnView.qml the rail, the page header, the page, and the states every page shares
  Theme.qml        the visual system: colours named after colors.toml keys (Tokyo Night when there
                   is no file), five type sizes, the page margin, the disabled look, the date format
  Status.js        what the bar mark says: which phone needs attention and why, updates available
  components/      the controls; the shared patterns below
  pages/           one file per page (Overview, Home screen, Wallpaper & style, Search & gestures,
                   History, Agents, Service = SettingsPage.qml, Add phone)
  assets/          the mark (outline and app square masks) at 16-256 px and the picture for the header; made by assets/make_icons.py
tools/             offscreen test harness (not installed)
```

The window's patterns, one component each, used by every page:

| Pattern | Component | Rule |
| --- | --- | --- |
| Rail | `RailButton` in `HomaLawnView` | Phones, "+ Add phone", a divider, the selected phone's sections, Service at the bottom. Labelled (224 px) and pushing the content by default; ☰ collapses it to icons (tooltips, phones as initials). Below 1100 px of window width it stays collapsed and ☰ lays it over the content until a pick. An accent dot marks a section with changes not applied yet. |
| Apply | `ApplyBar` | At the bottom of every editing page (Home screen, Wallpaper & style, Search & gestures, Overview's addresses): "No changes" / "N changes — not applied yet" / "Applying…" / "Applied", Discard, Apply. Nothing else applies. |
| Confirm | `ConfirmBar` | Where the action was pressed: the question, one line on what happens, Cancel and the action (a red outline when it cannot be taken back). Restore, Unpair, Replace current wallpaper, Update app, Restart. |
| Status | `StatusLine` | One full-width line under the header (an error, the phone offline), at most one action. |
| Service not running | `ServiceDown` | The whole body of every page, with one Start. |
| Headings | `Heading` | Card titles and every heading (Theme.heading, bold). |
| Details | `Details` | A disclosure for technical facts (the Service page's socket and build). |
| Tooltip | `Tip` | Buttons' `tip`, the collapsed rail. |

The view owns the shared states: the service not running (`ServiceDown`), a phone offline (one line;
Home screen and Search & gestures need the phone and show only that line), toasts (dropped when the
section or the phone changes). History rows show the service's `title` (with the section's name as
the kind); Undo's tooltip and toast name the newest History entry.

With no phone paired, every phone section shows Add phone (the window's first screen, with a
one-sentence welcome), except Agents, which says it needs a phone and offers Add phone beside its
Coding agents card. Add phone has two paths: **New phone** (1: Show QR code serves the app with
`app.serve` and draws the code from `qr`, the address, the version, a countdown to `expires_at`, the
service's `serving.message` and Stop (`app.serve_stop`); 2: the taps on the phone; 3: Pair) and
**This phone already has Homa Lawn** (Pair). Both pair with the same flow. Overview shows the app on
the phone with "Up to date" / "Update available: <version>" from the phone's `app_latest` (Update
app only when `newer`), the Permissions card from the phone's `permissions` (phone.info's two rows
for an older phone app), and the addresses in plain words with the service's tags under Details.
The Wallpaper & style preview draws the phone's own first page and dock (home.get) while the phone
is connected, and only the wallpaper otherwise.

## Backend contract

`Backend.qml` gives the views: `connected`, `attempted` (first connection try finished),
`state` (latest `{"event":"state"}` from the service), `phones`, `busy`, `omarchy`,
`call(method, params, done)` (`done(err, result)` runs exactly once; `err` is
`{code, message}`, `offline` when the connection is missing or closes first),
`startService(done)` (`systemctl --user start homalawnd`, `done(ok, message)`), and
`signal toast(text)`, and `pairState` (the latest `{"event":"pair"}`: `stage` `opening`, `words`, `waiting`, `done`, `failed` or `cancelled`, with `words`, `message` and, on `done`, `phone`; the view selects that phone). Add phone calls `pair.find`, `pair.start {host}` and `pair.cancel`; Overview calls `phone.unpair {phone}` and shows its `message`, and edits the phone's addresses with `phone.addresses {phone}` (`{addresses: [{address, kind, removable, answered, saved}]}` in dial order; only `added` rows are removable) and `phone.addresses_set {phone, addresses}` (the added list, in order; `invalid` with the sentence for a bad one or more than 5). Each entry of `phones` carries `id` (the phone's fingerprint, 64 lowercase hex) and `name`; each `busy` entry is `{phone (id), name, what}`. The `phone` parameter of every method takes an id or a unique name; the window always sends the id. A phone's state also carries `look_source` (`{self, name, scope}`: whose look the phone shows, `scope` `chosen` or `latest`; `null` when unread) and `kept` (the last apply was kept on the phone because another computer's look is shown); the Look page shows a banner when `look_source.self` is false. The pages' drafts live on the connection, per phone id, until Apply or Discard: `lookDrafts`, `homeDrafts`, `searchDrafts`, `addressDrafts` (the rail's draft dots read them). The phone's state also carries `permissions` (`{home, background, notifications, screen_control, install_updates}`, each true/false/null; null from an older phone app) and `app_latest` (`{version, newer, source}` or null), and the service's state `serving` (null, or `{url, urls, expires_at, version, source, message}` while the app is offered to a new phone). Agents reads `recipes.list` (`{name, title, description, needs, verified}`; only verified ones are shown, by title) and `agents.status` / `agents.connect` (the Coding agents card). History, Agents and Undo read `snapshots {phone}` rows `{name, time, kind, title}`: `kind` the section (`home`, `search`, `style`, `wallpaper`, `manual`, `before_restore`, `other`), `title` the line History shows (the snapshot's name is shown only when a row has no `title`). Socket path, same rule as the service: `$HOMALAWN_SOCKET`, else
`$XDG_RUNTIME_DIR/homalawn/homalawnd.sock`, else `/run/user/<uid>/homalawn/homalawnd.sock`. While the
service is down it retries after 2 s, doubling to 60 s; opening the window retries at once.

## Offscreen test

`tools/test-offscreen.sh` runs a throwaway, screenless omarchy-shell with this plugin; it never
touches the desktop's shell, `~/.config/omarchy` or the session bus. Everything it owns lives in
the folder you give it (log in `DIR/shell.log`, pictures in `DIR/shots`).

```
tools/test-offscreen.sh start  DIR --socket SOCKET [--theme catppuccin-latte]
tools/test-offscreen.sh summon DIR look
tools/test-offscreen.sh call   DIR snapshot look:DIR/shots/look.png
tools/test-offscreen.sh call   DIR facts           # JSON: section, phone, rail, drafts (sections with changes not applied), offline, serviceUp/Down, phones, serving
tools/test-offscreen.sh call   DIR rail open       # or closed: the ☰ button (in a narrow window, open lays the rail over the content)
tools/test-offscreen.sh call   DIR ask update      # raise a confirm without answering: "restore NAME" (History), update (Overview), restart (Service), cancel
tools/test-offscreen.sh call   DIR probe phones    # log: "homalawn test: probe phones ok"
tools/test-offscreen.sh call   DIR size 900x640    # lay the window out at another size
tools/test-offscreen.sh call   DIR lookFacts       # JSON: the Look tab's settings and what it shows (previewHome: the phone's own page in the preview)
tools/test-offscreen.sh call   DIR lookEdit '{"crop":{"home":0.2},"layer":{"home":30}}'  # as the controls do
tools/test-offscreen.sh call   DIR searchFacts     # JSON: the Search & gestures draft, the phone's values, what differs
tools/test-offscreen.sh call   DIR searchEdit '{"gesture":{"swipe_down":"homarSearch"}}'  # also search, open, apply
tools/test-offscreen.sh call   DIR agentsFacts     # JSON: the Agents chat (agent, messages, running), agent changes, recipes (shownRecipes: titles shown), codingAgents, noPhone
tools/test-offscreen.sh call   DIR agentsEdit '{"send":"What apps are in the dock?"}'   # also type, stop, new, ask/restore a change
tools/test-offscreen.sh call   DIR addFacts        # JSON: Add phone's path (new/existing), welcome, the QR code offered (offer, qrShown, qrPixels, remaining, ended expired|stopped, error, message)
tools/test-offscreen.sh call   DIR addEdit '{"path":"existing"}'   # also {"serve":true} (Show QR code), {"stop":true}
tools/test-offscreen.sh call   DIR overviewFacts   # JSON: Overview's app line (version, latest, updateOffered), permissions (or the older app's two), found addresses
tools/test-offscreen.sh call   DIR barFacts        # JSON: what the bar mark shows (problem, connected, updates, tooltip), by BarWidget's own rules
tools/test-offscreen.sh call   DIR pairFacts       # JSON: the Add phone page's pairing (found, stage, words) and Overview's Unpair question
tools/test-offscreen.sh call   DIR pairEdit '{"start":"127.0.0.1:47100"}'   # also find, cancel; unpair / cancelUnpair on Overview
tools/test-offscreen.sh call   DIR addrFacts       # JSON: Overview's Addresses card (draft, saved, read-only rows, error)
tools/test-offscreen.sh call   DIR addrEdit '{"add":"pixel.ts.net"}'   # also remove N, discard, apply
tools/test-offscreen.sh call   DIR settingsFacts   # JSON: the Service page's facts (service) or, on Agents, its coding agents (agents)
tools/test-offscreen.sh call   DIR settingsEdit '{"connect":"claude"}' # on Agents; only with temp agent configs
tools/test-offscreen.sh stop   DIR
```

By default the plugin is installed with the same file list as `homalawn desktop install`, plus the
test stand-ins: the bar widget writes `DIR/shots/bar-N.png` every 3 s and logs its tooltip and
state (`accent`, `problem`, `up`).
`--as-is` links the checkout instead.

Against the real service: run `homalawn serve` with a temporary `HOMALAWN_CONFIG`, `HOMALAWN_SOCKET`
and `HOMALAWN_STATE_DIR` (a short socket path: unix sockets cap at 108 bytes), with the AVD paired
into that config the way `tests/test_device_seam.py` pairs it, and pass the same socket. Never
add made-up phones to that config: the service then searches the network for them and dials
real phones it finds.
