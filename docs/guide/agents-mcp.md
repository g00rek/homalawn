# Agents (MCP)

This guide covers Homa Lawn's tool server for AI coding agents: how it gets connected to your
agents, the tools it offers, the safety rules agents are given, and the recipes.

`homalawn mcp` is a tool server (Model Context Protocol, over stdio) that gives an agent the same
powers as the `homalawn` command, as typed tools: read and change the home screen, and, with
**Screen control** switched on on the phone, read and operate the screen of any app. Nothing has to
keep running: the agent starts the server when it needs it. It logs to stderr only.

## Connecting a coding agent

**It happens by itself.** `homalawn desktop install` adds the server to every supported coding agent
it finds on this computer and names them; the Homa Lawn service adds it to agents you install later.
Supported: Claude Code, Codex, Gemini, Hermes, GitHub Copilot CLI, Grok, Pi, Oh My Pi, Cursor CLI,
Crush and OpenClaw. Each is registered with its own `mcp add` command or in its own config file,
under the name `homalawn`, running `homalawn mcp`.

**To turn that off**, install with `homalawn desktop install --no-agents`: nothing is added, now or
later. Homa Lawn remembers what it added (`~/.local/state/homalawn/agents.json`), so:

- `homalawn desktop uninstall` removes exactly those entries and nothing else;
- an entry you remove yourself is never added back on its own.

**To connect one by hand**, use **Connect** in the window's **Agents › Coding agents**, or register
a stdio server whose command is `homalawn mcp` in any MCP client. For example, in Claude Code:

```sh
claude mcp add --scope user homalawn -- homalawn mcp
```

To pin a server to one device, set `HOMALAWN_PHONE` (a paired phone) or `HOMALAWN_SERIAL` (an adb
device, for example a test emulator) in its environment.

The window's **Agents** section also has a chat with your Omarchy default agent that uses only
these tools; see [desktop-window.md](desktop-window.md#agents).

## Which phone

With one phone paired it is used by itself. With several, the agent calls `phone_list` (each
phone's name, whether it is connected, and which one the tools work on) and `phone_select` to pick
the one you mean. `HOMALAWN_PHONE` and `HOMALAWN_SERIAL` work as for the command.

## Tools

| Tool | What it does |
|---|---|
| `phone_list` / `phone_select` | the paired phones / work with this one from now on |
| `phone_status` | how the phone is reached and whether Homa Lawn answers |
| `phone_apps` | launchable apps |
| `phone_widgets` | widgets that can be added (default size, provider, label) |
| `phone_layout` | the home screen as JSON; each widget has a state (ready, needs setup, needs permission, loading) |
| `phone_prefs` | the launcher's settings |
| `phone_snapshots` | History, newest first |
| `phone_screenshot` | a screenshot, saved full size under `~/Pictures` (the server keeps its last 20) and returned scaled to 1280 px |
| `phone_place` | put an app at a cell (moves it if it is already there) |
| `phone_remove` | remove an app or item from the home screen |
| `phone_folder_create` | a folder holding the given apps |
| `phone_widget_add` | add a widget; returns within about 15 s, saying if it still needs its setup screen or a person's tap |
| `phone_layout_apply` | replace the whole home screen with a layout in `phone_layout`'s shape |
| `phone_grid` | set the grid (columns × rows) |
| `phone_nova_import` | move a Nova Launcher home screen (.novabackup) onto the phone; `dry_run` only reports |
| `phone_pref_set` | set a launcher setting |
| `phone_wallpaper` | set the home, lock or both wallpapers from an image file on this computer |
| `phone_snapshot_save` / `phone_snapshot_restore` | save the home screen under a name / restore a point in History |
| `phone_undo` | undo the last change on this phone; repeat to go further back |
| `screen_status` | is screen control on? |
| `screen_tree` | what is on the screen: items with ids (`n12`) and center points |
| `screen_tap` / `screen_long_press` | tap / touch and hold an item or a point |
| `screen_swipe` | swipe between two points |
| `screen_type` | put text into a field (replaces what it holds) |
| `screen_key` | back, home, recents, notifications, quick_settings |
| `screen_launch` | open an app by package name |
| `screen_shot` | a screenshot through screen control (any app), saved and returned like `phone_screenshot` |
| `recipe_list` / `recipe_get` | the recipes for jobs inside other apps / one recipe in full |

## Safety rules

The server hands the agent its rules when it connects, and the command's rules apply underneath:

- Every home-screen change is saved to History first, and `phone_undo` reverts it. Changes made
  by an agent are listed in the window's **Agents › Recent agent changes**, each with **Restore**.
- Layouts are checked against the grid; the agent is told never to apply a layout it did not build
  from a fresh `phone_layout`.
- These taps belong to you, on any phone: **Accept**, **Allow**, **Install**, **Buy**, pairing or
  permission buttons, password or payment entry, and **Create** (or its "Always allow" box) on
  Android's widget dialog. The agent is told never to make them, but to ask you and then check.
- The agent stops and tells you, instead of working around it, at `screen_off` (screen control is
  off), a wrong certificate, closed pairing, `pairing_open` (the phone is showing a pairing
  request, which only a person answers) and anything asking for a password, payment, install or
  permission.
- `no_backup` and `force` are used only after you agree.
- Pairing is not a tool. A named adb device never reaches the link.

Screen control is off until you switch it on on the phone, and it cannot see views apps mark as
sensitive (banking, passwords) on Android 14 and newer.

## Recipes

Recipes are written procedures for jobs inside other apps: allowing a new widget (Android's Create
dialog), adding a clock widget, using an icon pack, moving a Nova Launcher home screen, and adding
a Home Assistant widget. They ship inside the `homalawn` package
([`src/homalawn/recipes/`](../../src/homalawn/recipes/); format in its
[README](../../src/homalawn/recipes/README.md)), so they are there with any install.
`HOMALAWN_RECIPES` names another folder to read instead.

Each recipe says whether it has been walked through on a phone (`verified`); the window lists only
verified ones; an agent sees them all, with the unverified ones marked so it checks each step.
