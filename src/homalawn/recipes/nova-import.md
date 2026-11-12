---
name: nova-import
title: Move a Nova Launcher home screen
description: Put the user's Nova Launcher home screen (grid, dock, folders, apps, widgets, icon pack) on the phone with the phone_nova_import tool, after a dry run they agreed to
needs: a Nova backup file (.novabackup) on this computer, the user's yes after the dry run, the user's tap on Create for widgets Android asks about
verified: yes
---
# Move a Nova Launcher home screen

The work is done by one tool, `phone_nova_import` (the same as `homalawn nova-import`): the phone
runs Lawnchair's own Nova converter on the backup, and Homa Lawn applies the result the safe way
(one snapshot first, then the grid, the layout and the icon pack). Walked on the test emulator
with a synthetic Nova backup (the device tests: dry run, import over adb and over the link, undo
back to the exact layout before). A backup made on a real phone's Nova has not been walked yet:
read the dry run's account carefully the first time.

## Goal

The user's Nova home screen on their phone: the same grid and dock, apps and folders on the same
cells, widgets placed (and live once Android allows them), the Nova icon pack if it is installed.
Everything the converter could not place is listed for the user with the reason. `phone_undo`
brings back the home screen from before.

## Check first

1. The user gave you the path of the Nova backup on this computer (in Nova: Settings › Backup &
   restore › Backup, then copy the `.novabackup` file to the computer). No file: stop and ask;
   making the backup in Nova and copying it is theirs.
2. `phone_status`: the phone answers and Homa Lawn is the home app.
3. `phone_nova_import` with `path` and `dry_run: true`. Nothing changes on the phone. It answers
   with the grid it would set, how many items it would place, and every item it would leave out,
   grouped by reason (an app not installed on the phone, no room in the grid or dock, a Nova-only
   item, a folder left with nothing in it, ...). A file that is not a Nova backup, or a damaged
   one, is refused here with one line saying so.

## Steps

1. Tell the user the dry run's account in plain words: the grid (columns x rows, dock size), what
   would be placed, and each item left out with its reason. Mention that their current home
   screen is saved first and `phone_undo` puts it back.
2. Ask about the At a Glance row only if it matters to them: `glance_row: true` keeps an extra
   top row for the date and weather bar ("Add extra row to show At a Glance"), `false` does not;
   leaving it out keeps what the phone does now.
3. Only after their yes: `phone_nova_import` with `path` (and `glance_row` if they chose). It
   answers with the same account as the dry run, plus each widget's state.
4. Widgets that wait for the user's tap on Create: follow recipe `widget-permission` (the tap is
   theirs). Widgets that need their setup screen: finish it as in recipe `clock-widget` (steps 3
   and 4). Widgets still loading: check again with `phone_layout` after a few seconds.
5. Apps left out because they are not installed: tell the user which; installing them is theirs.
   After they install some, a second import places them (it replaces the home screen again, with
   a new snapshot).

## Verify

- `phone_layout`: the grid and dock are the ones the import named, and the screens hold the
  placed items (spot-check a few apps and every folder against what the user expects).
- `phone_layout`: every widget has `"state": "ready"`.
- `phone_screenshot` of each screen (swipe between them with `screen_swipe`) and show the user.

## If it goes wrong

- The import says the file is not a Nova backup or is damaged: tell the user, and ask for a fresh
  backup from Nova.
- The grid the backup needs is outside Homa Lawn's limits: the import says so and changes
  nothing; tell the user.
- The result is not what the user wanted: `phone_undo` puts back the home screen from before the
  import (grid, layout and settings in one step).

## The user's part

Making the backup in Nova and copying it to this computer. The yes after the dry run. The Create
tap on Android's widget dialog. Installing apps the backup used that the phone does not have.
