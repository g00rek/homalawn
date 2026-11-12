---
name: icon-pack
title: Use an icon pack
description: Apply an installed icon pack to the home screen through Lawnchair's icon-pack setting
needs: the icon pack already installed (installing it from a store is the user's), screen control on to read the pack list
verified: yes
---
# Icon pack

Walked on the test AVD on 2026-10-12 up to the pack list (the AVD has no icon pack, so the
list held only "System icons"). Applying a pack and the look of the result are marked
"(not yet walked on a phone)".

## Goal

Lawnchair draws app icons from the icon pack the user named, set through Homa Lawn so that
`phone_undo` can put the previous icons back.

## Check first

1. `phone_prefs` with filter `icon` → the key is **`pref_iconPackPackage`** (`[shared/string]`;
   "(default)" means system icons, otherwise its value is the pack's package name). Note the
   current value: that is what `phone_undo` returns to. (The themed-icon source is the separate
   `pref_themedIconPackPackage`; leave it unless the user asks for themed icons.)
2. Is the pack installed? Lawnchair's own list shows only installed packs:
   - `screen_long_press` an empty spot on the home screen (e.g. the middle of an empty row) →
     `screen_tree` shows `"Wallpaper & style"`, `"Widgets"`, `"Home settings"`.
   - `screen_tap` **"Home settings"** → `screen_tree`: `"General · Colors, icon packs, notification dots"`.
   - `screen_tap` it → under "Icons", `"Icon style · System icons"` (the second part is the current
     pack). `screen_tap` it.
   - `screen_tree` → tabs `"Icon pack"` and `"Themed icon source"`, and under them a scrollable row
     of packs: `"System icons"` and one item per installed pack, by its name.
     `screen_swipe` sideways along that row if there are more than fit.
   If the pack is not there, stop: installing it from a store is the user's. Tell them its name and
   where (Play Store), and wait.
3. Find the pack's package name: `phone_apps` (most packs have a launcher entry; match the name
   from step 2). If it has none, ask the user for the Play Store link (the package is the `id=` part).
4. `screen_key` `home` to leave the settings.

## Steps

1. `phone_pref_set` with `key` `pref_iconPackPackage`, `value` the pack's package name (e.g.
   `com.example.iconpack`). Result: "pref_iconPackPackage = … Previous values saved as prefs-…" and
   "phone_undo reverts it." (not yet walked on a phone)
2. Lawnchair reloads icons when this setting changes (its preference is declared with a
   reload-icons action in Lawnchair's `PreferenceManager.kt`). Give it a few seconds, then
   `screen_key` `home`. (not yet walked on a phone)

## Verify

- `phone_prefs` filter `icon` shows `pref_iconPackPackage` with the pack's package name.
- `phone_screenshot` shows the home screen with the pack's icons (compare with a screenshot taken
  before step 1: the app icons look different, labels and positions are unchanged).
- Lawnchair's own screen agrees: Home settings › General shows `"Icon style · <pack name>"` in
  `screen_tree` (path as in Check first, step 2).

## If it goes wrong

- Icons did not change after a few seconds: open Home settings › General › Icon style and check the
  pack is selected there; if it is, the "Restart Lawnchair" item, if present (in the long-press menu or
  under Home settings ⋮), reloads everything (tell the user first: the home screen blinks). (not yet walked on a phone)
- `phone_pref_set` refuses the key or the value: read the message; do not use `store`/`type` for
  this key (it is a known key).
- The user does not like it: `phone_undo` (while it is still the newest Homa Lawn change) puts the
  previous value back.
- Some apps keep their normal icon: the pack has no icon for them. Nothing to fix from here.
- Do not pick the pack by tapping it in Lawnchair's own list: that works too, but Homa Lawn takes no
  snapshot then, so `phone_undo` cannot revert it.

## The user's part

Installing the icon pack from a store (and paying for it, if it is paid). Applying it is not theirs:
the agent does that.
