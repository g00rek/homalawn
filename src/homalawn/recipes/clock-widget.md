---
name: clock-widget
title: Add a clock widget
description: Put a clock widget on the home screen and finish its setup screen (pick a face or style)
needs: a clock app with a widget (Google Clock or Samsung Clock), screen control on, no taps from the user unless the widget-permission dialog appears
verified: yes
---
# Clock widget

Walked through the MCP tools on the test AVD on 2026-10-12 with Google Clock's "Analog"
(screen 0) and "Digital" (screen 1) widgets, and walked again the same day (Analog) after
`phone_widget_add` stopped waiting for the setup screen. Samsung Clock was not on the AVD: its
steps are marked "(not yet walked on a phone)".

## Goal

A working clock widget at cells the user chose (or free cells you picked and told them), its setup
screen finished, so it shows the time and not a "Tap to finish setup" card.

## Check first

1. `screen_status` → "Screen control is on." (needed to finish the setup screen). If off, stop and
   tell the user (Settings › Accessibility › Homa Lawn).
2. `phone_widgets` → pick a clock provider. Google Clock (`com.google.android.deskclock`):
   - `2x2 com.google.android.deskclock/com.android.alarmclock.AnalogAppWidgetProvider  Analog` (walked)
   - `2x2 …/com.android.alarmclock.DigitalAppWidgetProvider  Digital`,
     `2x2 …DigitalStackedAppWidgetProvider  Stacked`, `5x2 …DigitalCitiesAppWidgetProvider  World`
   - Samsung Clock is package `com.sec.android.app.clockpackage`; take the provider exactly as
     `phone_widgets` prints it (not yet walked on a phone).
   The first column is the default size in cells (columns x rows).
3. `phone_layout` → find free cells for that size. Cells are 0-based (`x` column, `y` row);
   **row 0 of the page whose `id` is 0 is the search/date bar** (screens are numbered by position, and that page may not be the first): never put the widget there. Every cell the widget
   covers (x … x+w-1, y … y+h-1) must be free, also of other widgets' spans (`w`, `h`).

## Steps

1. `phone_widget_add` with `provider`, `screen`, `x`, `y` (leave `w`/`h` out for the default size).
   It returns within a few seconds (at most about 15), not as an error. For a clock expect:
   ```
   via link (<phone>)
   Placed on screen 0 at (3,1); it needs its setup screen finished: tap it (screen_tree shows it) and complete the setup, then check with phone_layout.
   phone_undo reverts it.
   ```
   The widget is on the home screen as a placeholder waiting for its setup: do NOT `phone_undo`.
   - "Widget added on screen …, id N." instead: the clock needed no setup; go to Verify.
   - "…Android needs the user to allow widgets…" instead: follow `widget-permission`, then come
     back to step 4 here (the setup screen opens right after the user's Create).
   - "…Lawnchair has not loaded it yet…": `phone_layout` in a few seconds; its `state` tells
     which of the cases above it is.
2. Show the widget's page: `screen_key` `home` for screen 0; for screen 1 and on, swipe
   right-to-left across the middle, once per page. Scale the points to the screen: from
   (83% of the width, 50% of the height) to (14% of the width, 50% of the height). a Galaxy S23
   (1080×2340): 900,1170 → 150,1170. The AVD (1080×2400): 900,1200 → 150,1200. The middle of the
   screen is the centre point of the full-screen `ScrollView` at the top of Lawnchair's
   `screen_tree` (`@540,1200` on the AVD): use its y, and double its x for the width.
3. `screen_tree` → Lawnchair in front; the placeholder shows as
   ```
   PendingAppWidgetHostView "Tap to finish setup" @851,758
     View [click] @851,758
   ```
   at the widget's place. `screen_tap` the `View [click]` under it.
4. `screen_tree` → the clock app's setup screen. Google Clock (both walked); the screen differs
   per widget:
   - Analog:
     ```
     com.google.android.deskclock
       TextView "Select a Clock Face"
       RecyclerView [scroll]
         LinearLayout "Scallop design. Clock face with a wavy shape, …" [click]
         LinearLayout "Numeral design. Clock face with large numbers" [click]
         LinearLayout "Day design. Clock face with the day of the month" [click]
     ```
     More faces may be below: `screen_swipe` up inside the list, then `screen_tree` again.
   - Digital:
     ```
     com.google.android.deskclock
       TextView "Choose clock style"
       LinearLayout "Solid" [click]
       LinearLayout "Transparent" [click]
     ```
   `screen_tap` the choice the user asked for (the first one if they did not say). **Tapping a choice
   finishes the setup at once**: there is no Done/Save button on these screens.
   Samsung Clock (not yet walked on a phone): expect a style/colour screen with a Done or Save
   button at the bottom. Done/Save/OK on a widget setup screen are not permission buttons: the
   agent may tap them.
5. `screen_tree` → Lawnchair is in front again and the placeholder became
   `LauncherAppWidgetHostView "Analog"` with a `FrameLayout "17:53 · 17:53 · Monday, Oct 12, 5:53 PM" [click]`
   under it (Digital: `LauncherAppWidgetHostView "Digital"` with
   `LinearLayout "Mon, Oct 12 (Monday, October 12) · 5:39" [click]`): the time and date.

## Verify

- `phone_layout` lists `{"type": "widget", "provider": "<the provider>", …, "x": X, "y": Y,
  "state": "ready"}` on that screen. `"needs setup"` means the setup screen was not finished
  (back to step 2); `"needs permission"`: recipe `widget-permission`.
- `screen_tree` shows `LauncherAppWidgetHostView "<clock label>"` with the time in a child item at
  that place, and no `PendingAppWidgetHostView "Tap to finish setup"` anywhere. The label is the
  widget's own name: Google Clock says "Analog" / "Digital"; Samsung Clock's name may differ (take
  it from `phone_widgets`; not yet walked on a phone). The time under it is the check that holds
  for every clock.
- `phone_screenshot` shows the clock face at the chosen cells (on the widget's page).

## If it goes wrong

- `phone_widget_add` fails at once with a cell or overlap message: nothing was changed; pick
  other cells from `phone_layout`.
- "Lawnchair removed the widget while loading it…": the cells were taken or the provider is gone;
  `phone_undo` restores the previous layout, then pick other cells.
- The "Create widget and allow access?" dialog appears: recipe `widget-permission`.
- You pressed back on the setup screen, or it closed without a face picked: the placeholder card
  stays and `phone_layout` still says `"needs setup"`. Tap it again (step 3).
- A screenshot tool fails with "shot_failed": the tool has already tried twice; wait a moment and
  call it once more.
- `stale_node`: `screen_tree` again, use the new id. `no_window`: wait a moment, `screen_tree`.
- `screen_off`: stop and tell the user.
- The user does not want the clock after all: `phone_undo` (only if the widget add is still the
  newest Homa Lawn change; `phone_snapshots` shows the list).

## The user's part

None, unless the widget-permission dialog appears (then the Create tap and the "Always allow"
checkbox are theirs; see `widget-permission`).
