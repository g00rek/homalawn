---
name: ha-entity-widget
title: Add a Home Assistant widget
description: Add a Home Assistant companion app widget (entity state, toggle for a light, or action button), pick the entity on its setup screen, verify the state shows
needs: Home Assistant companion app installed and logged in (logging in is the user's), screen control on, the entity id (e.g. light.kitchen), the user's tap only if the widget-permission dialog appears
verified: no
---
# Home Assistant entity widget

**Not yet walked on a phone.** The AVD has no logged-in Home Assistant. The provider names and
screen texts below come from the companion app's source (cited in Check first); every step marked
"(not yet walked on a phone)" is inferred and must be checked on the first real run. The parts
shared with every widget (the add returning "needs setup", the placeholder, the permission
dialog) were walked on the AVD with clock widgets.

The first real-phone run must confirm, and then remove from this list:
- the setup screen texts in steps 3 and 4;
- that **Entity state with "Tap action: Toggle"** both shows the light's state and switches it
  (the default this recipe picks for "a toggle for the kitchen light");
- the widget's look and texts in step 6 and in Verify.

## Goal

A Home Assistant widget at the cells the user chose, bound to the entity they named, showing that
entity's current state (or, for a button, labelled and ready to run its action).

Which widget:
- **Show a state** (temperature, door, light on/off): "Entity state".
- **Toggle a light and see whether it is on**: "Entity state" with **Tap action: Toggle**.
- **A button that runs an action** (e.g. `light.toggle`, `homeassistant.toggle`, a script):
  "Action button" (older app versions call it the Button widget), action `light.toggle` (or
  `homeassistant.toggle`) with `entity_id` set to the light.
If the user just says "a toggle for the kitchen light", ask which they want only if it matters to
them; otherwise use Entity state + Toggle (it also shows the state).

## Check first

1. `phone_apps` lists `io.homeassistant.companion.android` (Play Store build) or
   `io.homeassistant.companion.android.minimal` (F-Droid "minimal" build). Neither: installing it
   is the user's; stop and tell them.
2. Logged in? `screen_launch` the package, then `screen_tree`. If it shows a welcome / "Connect
   to Home Assistant" / server address or login screen, stop: logging in is the user's. When it
   shows their dashboard, `screen_key` `home`. (not yet walked on a phone)
3. `screen_status` → "Screen control is on."
4. `phone_widgets` → the provider. The receivers in the app's manifest
   (github.com/home-assistant/android, `app/src/main/AndroidManifest.xml`, checked 2026-10-12 at
   commit 2356b6b; labels from `common/src/main/res/values/strings.xml`):
   | Label (English) | Provider (Play Store build) |
   |---|---|
   | Entity state | `io.homeassistant.companion.android/.widgets.entity.EntityWidget` |
   | Action button | `io.homeassistant.companion.android/.widgets.button.ButtonWidget` |
   | Template widget | `io.homeassistant.companion.android/.widgets.template.TemplateWidget` |
   | Picture widget | `…/.widgets.camera.CameraWidget` |
   | Media player widget | `…/.widgets.mediaplayer.MediaPlayerControlsWidget` |
   | To-do list | `…/.widgets.todo.TodoWidget` |
   In the minimal build the package is `io.homeassistant.companion.android.minimal` and the class
   keeps its full name (`io.homeassistant.companion.android.widgets.entity.EntityWidget`). Always
   pass the provider exactly as `phone_widgets` prints it on their phone.
5. `phone_layout` → free cells for the widget's default size (first column of `phone_widgets`).
   Row 0 of the page with screen `id` 0 is the search bar (pages are numbered by position; that page may not be first).
   **"Next to the clock"** (or next to any widget the user names): in `phone_layout`, look through
   `screens[].items` for `"type": "widget"` whose `provider` is a clock app's (contains `clock` /
   `Clock`, e.g. `com.google.android.deskclock/…`, `com.sec.android.app.clockpackage/…`; when
   unsure, match it against the label in `phone_widgets`). Note its screen (`index` of the
   screen it is listed under), `x`, `y`, `w`, `h`. Then pick free cells beside it, in this order:
   - right of it: x from `x + w`, same `y`, if `x + w + width ≤ cols`;
   - left of it: x from `x - width`, same `y`, if that is ≥ 0;
   - else directly below it: same `x`, y from `y + h`, if `y + h + height ≤ rows`.
   `cols` and `rows` are the grid from `phone_status` (also `phone_layout`'s `grid`); width and
   height are the widget's size. Free means no item on that screen covers any of those cells
   (an item covers x..x+w-1, y..y+h-1; apps and folders are 1×1). If none fits, ask the user
   where. No clock widget at all: tell them, and ask whether to add one first (`clock-widget`).
6. You know the entity id (e.g. `light.kitchen`). If the user gave a name ("kitchen light"), the setup
   screen's search finds it by name too.

## Steps

1. `phone_widget_add` with the provider and cells. It returns within about 15 s, not as an error.
   Every one of these widgets has a setup screen, so expect
   "Placed on screen S at (X,Y); it needs its setup screen finished: tap it (screen_tree shows it)
   and complete the setup, then check with phone_layout." Do NOT `phone_undo`: the placeholder is
   the widget, waiting for its setup. If it says "Android needs the user to allow widgets…", follow
   `widget-permission` first; the setup screen opens after the user's Create.
2. Show the widget's page (`screen_key` `home`; for later pages swipe right-to-left across the
   middle, once per page: see `clock-widget` step 2 for the coordinates), `screen_tree`. At the
   widget's place it shows `PendingAppWidgetHostView "Tap to finish setup"` with an unlabelled
   `View [click]` under it: `screen_tap` that `View [click]`.
3. `screen_tree` → the companion app's setup screen. (not yet walked on a phone)
   - **Entity state**: a server picker ("Select server", only with several servers), an entity
     field ("Select entity to display", searchable), "Attribute" / "Add attribute",
     "State and attribute separator", "Label", "Widget text size", "Tap action" (choices
     "Toggle" and "Refresh"), theme and text colour, and at the bottom the "Add widget" button
     ("Update widget" when editing).
   - **Action button**: "Configure action", "Server", "Action" (a text field with suggestions),
     then fields for the action's data (an `entity_id` field appears once the action is chosen;
     "Add field" adds more), "Icon", "Label", "Widget theme", "Widget text/icon color",
     "Require authentication", and "Add widget".
4. Fill it in (not yet walked on a phone):
   - Entity state: tap the entity field, `screen_type` the entity id or name, tap the matching
     suggestion; for a toggle set "Tap action" to "Toggle"; optionally `screen_type` a Label.
   - Action button: `screen_type` `light.toggle` (or `homeassistant.toggle`) into "Action", tap
     the suggestion, then put the entity id into the `entity_id` field; `screen_type` a Label
     (e.g. "Kitchen"). Leave "Require authentication" as it is unless the user asked.
   After each `screen_type`, `screen_tree` again: suggestion lists appear and node ids change.
   `screen_swipe` up to reach fields further down.
5. `screen_tap` **"Add widget"**. This button only saves the widget's own settings: it is not a
   permission button, the agent may tap it.
6. `screen_tree` → Lawnchair in front; the placeholder became
   `LauncherAppWidgetHostView "<label>"` with the entity's state (or the button's label) under
   it. (not yet walked on a phone)

## Verify

First bring that home page to the front: `screen_key` `home` (shows page 0), then for page 1 and
further swipe right-to-left across the middle once per page (coordinates in `clock-widget` step
2); `screen_tree` should show the widget before you take the screenshot.

- `phone_screenshot` shows the widget at its cells with the entity's current state (e.g. "On",
  "21.5 °C") or the button's icon and label, and no "Tap to finish setup" card and no
  "Oops. It looks like this widget is not configured." text.
- `screen_tree` shows `LauncherAppWidgetHostView "<label>"` at that place, and its child text is
  the state, which should match what the Home Assistant app shows for that entity.
- `phone_layout` lists the widget with that provider at those cells with `"state": "ready"`
  (`"needs setup"` means the setup screen was not finished).
- For a toggle: tap it only if the user wants it tested (it really switches their light); then
  `screen_tree` again and the state text should have flipped.

## If it goes wrong

- Welcome / login screen in the companion app: stop, logging in is the user's.
- "Create widget and allow access?" dialog: recipe `widget-permission`.
- "Unable to fetch data for configured entity." on the widget: the entity id is wrong or the
  server is unreachable; tap the widget's gear / the widget to reopen its setup, or `phone_undo`
  and start again.
- "A custom component is preventing action data from loading." on the Action button setup: tell
  the user; type the action and the `entity_id` field by hand.
- "Oops. It looks like this widget is not configured.", or `phone_layout` still says
  `"needs setup"`: the setup screen closed without "Add widget"; tap the widget to reopen setup.
- The setup screen asks for a fingerprint/PIN ("Require authentication"): that is the user's.
- `stale_node`: `screen_tree` again. `screen_off`: stop and tell the user.

## The user's part

Installing the companion app and logging in to Home Assistant (server address, password, any
2FA). The Create tap and "Always allow" if the widget-permission dialog appears. Any
fingerprint/PIN prompt. Deciding whether a toggle may be test-tapped (it switches a real device).
