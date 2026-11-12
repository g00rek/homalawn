---
name: widget-permission
title: Allow a new widget (Android's Create dialog)
description: Recognise Android's "Create widget and allow access?" dialog for Lawnchair, hand the Create tap to the user, then check the widget is live
needs: screen control on, the user's tap on Create (and on "Always allow" if they want it), nothing installed
verified: yes
---
# Widget permission

Walked on the test AVD on 2026-10-12 (bind permission revoked for the walk, then given back),
and again the same day after `phone_widget_add` stopped waiting. **Samsung (One UI) is not yet
walked**: the user's phone may word the dialog differently (see step 4).

## Goal

A widget that Lawnchair is not yet allowed to create ends up live on the home screen. Android
asks once per widget (or never again, if the user ticks "Always allow"). Recognising the dialog,
stopping, and checking afterwards is the agent's job; the Create tap and the checkbox are theirs.

## Check first

1. `screen_status` → "Screen control is on." Without it, stop: tell the user to turn on Lawnchair in
   Settings › Accessibility (see the Homa Lawn README); do not work around it.
2. You got here from `phone_widget_add` (or another recipe) and its result (within about 15 s,
   not an error) said **"Placed on screen S at (X,Y); Android needs the user to allow widgets: ask
   them to tap Create (or Allow) on the phone's dialog; do not tap it yourself (recipe
   widget-permission), then check with phone_layout."** That means
   Lawnchair has no permission to bind the widget. ("…it needs its setup screen finished…" is a
   different case: the widget's own setup screen, handled by the recipe that added it.)
   Do NOT `phone_undo` here: the placeholder is the widget, waiting.
3. `phone_layout` lists the widget at the cells you asked for with `"state": "needs permission"`.

## Steps

1. Go to the widget's home screen page. `screen_key` `home` shows page 0; for page 1 and further,
   swipe right-to-left across the middle, once per page, with the points scaled to the screen:
   from (83% of the width, 50% of the height) to (14%, 50%). S23 (1080×2340): 900,1170 → 150,1170;
   AVD (1080×2400): 900,1200 → 150,1200. (How to read the screen size: `clock-widget` step 2.)
2. `screen_tree` → Homa Lawn (`app.homalawn…`, or `app.lawnchair…` on a phone still on the old app) is in front. The placeholder is
   `PendingAppWidgetHostView "Tap to finish setup"` with a `View [click]` under it, at the
   widget's place. A live widget would be a `LauncherAppWidgetHostView "<widget label>"` instead.
3. `screen_tap` that `View [click]` (tapping the placeholder is not a permission tap; it only
   opens the dialog).
4. `screen_tree` → the dialog, from `com.android.settings` (AVD, stock Android):
   - `TextView "Create widget and allow access?"`
   - `TextView "After you create the widget, the application can access everything displayed. … Application: Lawnchair … Widget: <label>"`
   - `CheckBox "Always allow Lawnchair (Debug) to create widgets and access their data" [click] [unchecked]`
   - `Button "Cancel"`, `Button "Create"`

   Recognise it by the words **"create widgets"** plus a **Cancel / Create** button pair, not by
   the exact title or app name: on the AVD the app reads "Homa Lawn" ("Lawnchair (Debug)" on builds before the rename), on the user's phone it
   is the name their build shows, and Samsung's One UI may word the title and text differently (not
   yet walked on a phone). If neither "create widgets" nor a Cancel/Create pair is there, do not
   guess: `screen_shot` and tell the user what the screen says.
5. **Stop. Do not tap Create or the checkbox.** Tell the user, in one message: which widget, on which
   page and cells, that Android asks whether Lawnchair may create it, and that they can tick
   "Always allow" so they are not asked again for future widgets. Wait until they say they answered.
6. `screen_tree` → Lawnchair is in front again and the widget's place is now
   `LauncherAppWidgetHostView "<label>"` with the widget's own content under it (on the AVD the
   Battery widget showed `"Loading…"` under it; that is the widget's content, it is bound).
   If the widget has its own setup screen, that screen opens now instead; go back to the recipe
   that added the widget (for clocks: `clock-widget`, step 4).

`screen_tree` shows whether "Always allow" is ticked: the CheckBox line ends in `[checked]` or
`[unchecked]` (walked: `[unchecked]` before, `[checked]` after the tick). Read it before they tap
Create if they asked you to make sure.

## Verify

- `phone_screenshot` shows the widget's own content at its cells, not the "Tap to finish setup"
  card. (Swipe to its page first; the screenshot shows the page in front.)
- `screen_tree` shows `LauncherAppWidgetHostView "<label>"` at that place, and no
  `PendingAppWidgetHostView "Tap to finish setup"` there.
- `phone_layout` lists the widget with that provider at the cells you asked for with
  `"state": "ready"` (walked: Battery went from `"needs permission"` to `"ready"` after Create).
  `"needs setup"` means the widget's own setup screen is next (step 6).

## If it goes wrong

- The user tapped **Cancel**: the placeholder stays. Ask if they want to try again (back to step 2) or
  remove it with `phone_undo` (only if the widget add was the newest Homa Lawn change).
- The dialog does not appear after the tap: `screen_tree` again; if Lawnchair is in front and the
  widget is live, Lawnchair already had permission. If an app's setup screen appeared instead,
  continue with that widget's recipe.
- `stale_node`: `screen_tree` again and use the new id.
- `screen_off`: stop and tell the user (Settings › Accessibility › Homa Lawn).
- The placeholder is gone and `phone_layout` no longer lists the widget: Lawnchair dropped it
  while loading (provider uninstalled, or no room). Tell the user; add it again with
  `phone_widget_add`.

## The user's part

The tap on **Create**, and the "Always allow Lawnchair to create widgets and access their data"
checkbox if they want it. Both are permission taps: the agent never does them on their phone.
