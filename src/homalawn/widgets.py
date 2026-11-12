"""Add home screen widgets.

The widget row is written as a placeholder (appWidgetId=-1, restored=FLAG_ID_NOT_VALID) and
Lawnchair allocates and binds the id itself when it loads (WidgetInflater.inflateAppWidget).
An id bound in advance through the provider would be dropped on the launcher restart that
the write needs. End states of the row after load (LauncherAppWidgetInfo.FLAG_*):

  restored == 0, appWidgetId > 0   bound and ready                         -> done
  restored has FLAG_ID_ALLOCATED   id allocated, bind not allowed           -> user taps it
  restored has FLAG_UI_NOT_READY   bound, provider has a configure screen   -> user taps it
  restored == FLAG_ID_NOT_VALID    launcher has not loaded the row yet      -> keep waiting
  row gone                         loader deleted it (no provider, overlap) -> error

`add` (the CLI) waits through the middle two for the person to act; `place` (the agent tool
server) returns at the first of the first three, or as "loading" after PLACE_WAIT_S, so no tool
call ever waits for a person. `layout.widget_state` names the same states for a layout read.
"""
from __future__ import annotations

import sqlite3
import time
from collections.abc import Callable

from . import appid
from .transport import Transport, TransportError
from .layout import (CONTAINER_DESKTOP, ITEM_WIDGET, RESTORE_ID_ALLOCATED, RESTORE_ID_NOT_VALID,
                     RESTORE_UI_NOT_READY, screen_id, widget_state, with_screen_ids)
from .session import Launcher

POLL_S = 2
PLACE_WAIT_S = 14  # how long `place` waits for the launcher to load the row

FLAG_ID_NOT_VALID = RESTORE_ID_NOT_VALID
FLAG_UI_NOT_READY = RESTORE_UI_NOT_READY
FLAG_ID_ALLOCATED = RESTORE_ID_ALLOCATED

MSG_BIND = "Tap the widget on the phone to finish adding it"


def msg_bind(transport: Transport) -> str:
    """What to do about a widget Lawnchair may not bind by itself; the adb way only over adb."""
    if not transport.has_shell:
        return MSG_BIND
    serial = getattr(transport, "serial", None)
    dash_s = f"-s {serial} " if serial else ""
    pkg = getattr(getattr(transport, "device", None), "package", None) or appid.RELEASE
    return (f"{MSG_BIND}, or allow Homa Lawn to create widgets "
            f"(`adb {dash_s}shell appwidget grantbind --package {pkg}`)")
MSG_CONFIGURE = "Tap the widget on the phone to set it up"


def list_providers(transport: Transport) -> list[dict]:
    return transport.call("launcher.widgets_list")


def normalize(provider: str) -> str:
    pkg, _, cls = provider.partition("/")
    if cls.startswith("."):
        cls = pkg + cls
    return f"{pkg}/{cls}"


def _row(launcher: Launcher, screen: int, x: int, y: int, provider: str) -> dict | None:
    def q(path: str):
        con = sqlite3.connect(path)
        con.row_factory = sqlite3.Row
        try:
            return [dict(r) for r in con.execute(
                "select * from favorites where itemType=? and container=? and screen=? and cellX=? and cellY=?",
                (ITEM_WIDGET, CONTAINER_DESKTOP, screen, x, y))]
        finally:
            con.close()

    rows = launcher._with_local_db(launcher._export()[1], q)
    want = normalize(provider)
    return next((r for r in rows if normalize(r["appWidgetProvider"] or "") == want), None)


def _removal_cause(transport: Transport, provider: str) -> str:
    """The loader's own log line about the removal; only a device shell can read the log."""
    if not transport.has_shell:
        return ""
    try:
        log = transport.shell("logcat", "-d", "-t", "500", "-s", "LoaderCursor:E", "WorkspaceItemProcessor:*")
    except TransportError:
        return ""
    cls = normalize(provider).split("/", 1)[1]
    hits = [l for l in log.splitlines() if cls in l or "overlap" in l.lower()]
    return hits[-1].strip() if hits else ""


def _write_row(launcher: Launcher, provider: str, screen: int, x: int, y: int, w: int | None,
               h: int | None) -> tuple[str, int]:
    """Write the widget's placeholder row on the page at position `screen`; returns the provider as
    the phone names it and the screen id the row was written to."""
    info = next((p for p in list_providers(launcher.transport)
                 if normalize(p["provider"]) == normalize(provider)), None)
    if info is None:
        raise LookupError(f"{provider} is not an installed widget (see `homalawn widgets`)")
    cells = info.get("cells") or [1, 1]
    w = w if w is not None else max(1, int(cells[0]))
    h = h if h is not None else max(1, int(cells[1]))

    layout = launcher.get_layout()
    visible = [s["id"] for s in layout["screens"]]
    Launcher._screen(layout, screen).append(
        {"type": "widget", "provider": info["provider"], "x": x, "y": y, "w": w, "h": h})
    layout = with_screen_ids(layout, visible)
    launcher._check_layout(layout, None)  # bad cells fail here, before the phone is touched
    launcher.apply_layout(layout)
    return info["provider"], next(screen_id(s) for s in layout["screens"] if s["index"] == screen)


def _polls(launcher: Launcher, provider: str, screen: int, x: int, y: int, wait_s: int):
    """(appWidgetId, restored) of the row every POLL_S until `wait_s` has passed; raises
    RuntimeError if the loader deleted the row."""
    deadline = time.monotonic() + wait_s
    while True:
        time.sleep(POLL_S)
        try:
            row = _row(launcher, screen, x, y, provider)
        except (TransportError, sqlite3.DatabaseError):
            row = {"appWidgetId": -1, "restored": FLAG_ID_NOT_VALID}  # read raced a launcher write
        if row is None:
            cause = _removal_cause(launcher.transport, provider)
            raise RuntimeError("Homa Lawn removed the widget while loading it"
                               + (f": {cause}" if cause else "")
                               + "; `homalawn undo` restores the previous layout")
        yield row["appWidgetId"], row["restored"]
        if time.monotonic() >= deadline:
            return


def add(launcher: Launcher, provider: str, screen: int, x: int, y: int, w: int | None, h: int | None,
        wait_s: int = 120, notify: Callable[[str], None] = print) -> int:
    """Place a widget and wait until Lawnchair has bound it. Returns the appWidgetId."""
    provider, sid = _write_row(launcher, provider, screen, x, y, w, h)
    told: set[str] = set()
    for wid, flags in _polls(launcher, provider, sid, x, y, wait_s):
        if wid > 0 and flags == 0:
            return wid
        msg = None
        if flags & FLAG_ID_ALLOCATED:
            msg = msg_bind(launcher.transport)
        elif flags & FLAG_UI_NOT_READY and not flags & FLAG_ID_NOT_VALID:
            msg = MSG_CONFIGURE
        if msg and msg not in told:
            told.add(msg)
            notify(msg)
    raise TimeoutError(f"widget not ready after {wait_s}s; it stays on the home screen as a "
                       "placeholder you can tap to finish adding it")


def place(launcher: Launcher, provider: str, screen: int, x: int, y: int, w: int | None, h: int | None,
          wait_s: int = PLACE_WAIT_S) -> tuple[int, str]:
    """Place a widget without waiting for a person: (appWidgetId, state) as soon as the row is
    ready or needs its setup screen or the bind permission, else (…, "loading") after `wait_s`."""
    provider, sid = _write_row(launcher, provider, screen, x, y, w, h)
    wid = -1
    for wid, flags in _polls(launcher, provider, sid, x, y, wait_s):
        state = widget_state(wid, flags)
        if state != "loading":
            return wid, state
    return wid, "loading"
