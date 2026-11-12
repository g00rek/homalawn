import os
import sqlite3
import tempfile

import pytest
from test_session import DB, FakeDevice

from homalawn import snapshots, widgets
from homalawn.adb import AdbTransport
from homalawn.session import Launcher

CLOCK = "com.google.android.deskclock/com.android.alarmclock.AnalogAppWidgetProvider"
PROVIDERS = [{"provider": CLOCK, "label": "Analog", "minW": 1, "minH": 1, "cells": [2, 2]},
             {"provider": "com.x/.Tiny", "label": "Tiny", "minW": 1, "minH": 1, "cells": [0, 0]}]


@pytest.fixture(autouse=True)
def data(tmp_path, monkeypatch):
    monkeypatch.setenv("HOMALAWN_DATA", str(tmp_path / "data"))
    monkeypatch.setattr("homalawn.session.time.sleep", lambda s: None)


class FakeClock:
    def __init__(self):
        self.t = 0.0
        self.sleeps = []

    def monotonic(self):
        return self.t

    def sleep(self, s):
        self.sleeps.append(s)
        self.t += s


@pytest.fixture
def clock(monkeypatch):
    c = FakeClock()
    monkeypatch.setattr(widgets, "time", c)
    return c


class WidgetDevice(FakeDevice):
    """After start_home, each DB read first applies the next scripted loader step."""

    def __init__(self, tmp_path):
        super().__init__(tmp_path)
        self.steps = []
        self.homed = False

    def call(self, method, arg=None, extras=None):
        self.events.append(method)
        if method == "widgets_list":
            return PROVIDERS
        if method == "widget_bind":
            raise AssertionError(f"{method} must not be called")
        if method == "layout_import":
            self.homed = True  # Lawnchair reloads the imported rows; the scripted loader steps start
        return super().call(method, arg, extras)

    shell_out = ""

    def shell(self, *args):
        if args[:1] == ("logcat",):
            return self.shell_out
        return super().shell(*args)

    def start_home(self):
        super().start_home()
        self.homed = True

    def run_as_read(self, path):
        if self.homed and path == DB and self.steps:
            step = self.steps.pop(0)
            if step is not None:
                self.files[DB] = sql(self.files[DB], step)
        return super().run_as_read(path)


def sql(db: bytes, stmt: str) -> bytes:
    fd, p = tempfile.mkstemp(suffix=".db")
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(db)
        con = sqlite3.connect(p)
        con.execute(stmt)
        con.commit()
        con.close()
        with open(p, "rb") as f:
            return f.read()
    finally:
        os.unlink(p)


def widget_rows(dev):
    fd, p = tempfile.mkstemp(suffix=".db")
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(dev.files[DB])
        con = sqlite3.connect(p)
        rows = con.execute("select appWidgetId, restored, appWidgetProvider, spanX, spanY from favorites "
                           "where itemType=4").fetchall()
        con.close()
        return rows
    finally:
        os.unlink(p)


ALLOCATED = "update favorites set appWidgetId=42, restored=17 where itemType=4"
BOUND = "update favorites set appWidgetId=42, restored=0 where itemType=4"
UI_NOT_READY = "update favorites set appWidgetId=42, restored=4 where itemType=4"
DELETED = "delete from favorites where itemType=4"


def test_occupied_cell_errors_before_touching_the_phone(tmp_path, clock):
    dev = WidgetDevice(tmp_path)  # com.a sits at (0,1)
    dev.events.clear()
    with pytest.raises(ValueError, match="overlaps"):
        widgets.add(Launcher(AdbTransport(device=dev)), CLOCK, 0, 0, 1, None, None)
    assert not {"force_stop", "write", "start_home"} & set(dev.events)
    assert snapshots.list_() == []


def test_reserved_row_and_unknown_provider_error_first(tmp_path, clock):
    dev = WidgetDevice(tmp_path)
    with pytest.raises(ValueError, match="reserved"):
        widgets.add(Launcher(AdbTransport(device=dev)), CLOCK, 0, 1, 0, None, None)
    with pytest.raises(LookupError, match="com.nope/.W"):
        widgets.add(Launcher(AdbTransport(device=dev)), "com.nope/.W", 0, 1, 2, None, None)
    assert "force_stop" not in dev.events


def test_pending_then_bound_on_second_poll_returns_id(tmp_path, clock):
    dev = WidgetDevice(tmp_path)
    dev.steps = [None, ALLOCATED, BOUND]  # first read: the write path checking what the loader kept
    msgs = []
    wid = widgets.add(Launcher(AdbTransport(device=dev)), CLOCK, 0, 2, 2, None, None, notify=msgs.append)
    assert wid == 42
    assert widget_rows(dev) == [(42, 0, CLOCK, 2, 2)]
    assert len(msgs) == 1 and "Tap the widget" in msgs[0]
    assert clock.sleeps and set(clock.sleeps) == {widgets.POLL_S}
    assert "widget_bind" not in dev.events


def test_written_row_is_a_fresh_placeholder(tmp_path, clock):
    dev = WidgetDevice(tmp_path)
    dev.steps = [None, BOUND]  # first poll sees our row untouched (launcher still loading)
    snap = {}
    orig = dev.run_as_read

    def spy(path):
        if dev.homed and not snap:
            snap["rows"] = widget_rows(dev)
        return orig(path)

    dev.run_as_read = spy
    assert widgets.add(Launcher(AdbTransport(device=dev)), "com.x/.Tiny", 0, 3, 3, None, None) == 42
    assert snap["rows"] == [(-1, 1, "com.x/.Tiny", 1, 1)]  # cells [0,0] -> min 1x1


def test_explicit_size(tmp_path, clock):
    dev = WidgetDevice(tmp_path)
    dev.steps = [BOUND]
    widgets.add(Launcher(AdbTransport(device=dev)), CLOCK, 0, 0, 2, 4, 1)
    assert widget_rows(dev)[0][3:] == (4, 1)


def test_timeout_raises_and_leaves_layout(tmp_path, clock):
    dev = WidgetDevice(tmp_path)
    dev.steps = [ALLOCATED] + [None] * 100
    msgs = []
    with pytest.raises(TimeoutError):
        widgets.add(Launcher(AdbTransport(device=dev)), CLOCK, 0, 2, 2, None, None, wait_s=10, notify=msgs.append)
    assert widget_rows(dev) == [(42, 17, CLOCK, 2, 2)]  # placeholder stays, no rollback
    assert len(snapshots.list_()) == 1
    assert 8 <= clock.t <= 12
    assert len(msgs) == 1


def test_needs_configuration_is_reported(tmp_path, clock):
    dev = WidgetDevice(tmp_path)
    dev.steps = [None, UI_NOT_READY, BOUND]
    msgs = []
    assert widgets.add(Launcher(AdbTransport(device=dev)), CLOCK, 0, 2, 2, None, None, notify=msgs.append) == 42
    assert len(msgs) == 1 and "set it up" in msgs[0]


def test_row_vanished_raises(tmp_path, clock):
    dev = WidgetDevice(tmp_path)
    dev.steps = [DELETED]
    dev.shell_out = "E LoaderCursor: Removing restored widget: id=42 belongs to component " + CLOCK
    with pytest.raises(RuntimeError, match="removed the widget") as e:
        widgets.add(Launcher(AdbTransport(device=dev)), CLOCK, 0, 2, 2, None, None)
    assert "Removing restored widget" in str(e.value)


def test_list_providers(tmp_path):
    dev = WidgetDevice(tmp_path)
    assert widgets.list_providers(AdbTransport(device=dev))[0]["provider"] == CLOCK


# -- without waiting for a person (the agent tool server) ---------------------------------------
@pytest.mark.parametrize("step,state", [(UI_NOT_READY, "needs setup"), (ALLOCATED, "needs permission"),
                                        (BOUND, "ready")])
def test_place_returns_at_the_first_state_a_person_or_nobody_has_to_finish(tmp_path, clock, step, state):
    dev = WidgetDevice(tmp_path)
    dev.steps = [None, step] + [None] * 100  # the row would stay in that state for the whole 120 s
    wid, got = widgets.place(Launcher(AdbTransport(device=dev)), CLOCK, 0, 2, 2, None, None)
    assert (wid, got) == (42, state)
    assert clock.t <= 2 * widgets.POLL_S  # returned at the second poll, not after a wait for a person


def test_place_still_loading_after_the_window_is_not_an_error(tmp_path, clock):
    dev = WidgetDevice(tmp_path)
    dev.steps = [None] * 100  # the launcher never touches the row
    wid, state = widgets.place(Launcher(AdbTransport(device=dev)), CLOCK, 0, 2, 2, None, None)
    assert state == "loading" and wid == -1
    assert widgets.PLACE_WAIT_S - 2 <= clock.t <= widgets.PLACE_WAIT_S + widgets.POLL_S
    assert widgets.PLACE_WAIT_S <= 15


def test_place_row_vanished_still_raises(tmp_path, clock):
    dev = WidgetDevice(tmp_path)
    dev.steps = [DELETED]
    with pytest.raises(RuntimeError, match="removed the widget"):
        widgets.place(Launcher(AdbTransport(device=dev)), CLOCK, 0, 2, 2, None, None)


def _session(dev):
    from homalawn import ops
    s = ops.Session()
    s._t = AdbTransport(device=dev)
    return s


@pytest.mark.parametrize("step,state,want", [
    (UI_NOT_READY, "needs setup", "Placed on screen 0 at (2,2); it needs its setup screen finished: tap it (screen_tree shows it) "
                   "and complete the setup, then check with phone_layout."),
    (ALLOCATED, "needs permission", "Placed on screen 0 at (2,2); Android needs the user to allow widgets: ask them to tap Create (or Allow) "
                "on the phone's dialog; do not tap it yourself (recipe widget-permission), then check with phone_layout."),
    (BOUND, "ready", "Widget added on screen 0 at (2,2), id 42. `homalawn undo` removes it."),
])
def test_widget_add_without_waiting_says_what_is_pending(tmp_path, clock, step, state, want):
    from homalawn import ops
    dev = WidgetDevice(tmp_path)
    dev.steps = [None, step] + [None] * 100
    progress = []
    r = ops.widget_add(_session(dev), CLOCK, 0, 2, 2, None, None, progress=progress.append, wait_for_person=False)
    assert r.text == want
    assert r.data == {"appWidgetId": 42, "state": state}
    assert progress == []
    assert clock.t <= 2 * widgets.POLL_S


def test_widget_add_waiting_for_a_person_is_unchanged(tmp_path, clock):
    from homalawn import ops
    dev = WidgetDevice(tmp_path)
    dev.steps = [None, UI_NOT_READY, BOUND]
    progress = []
    r = ops.widget_add(_session(dev), CLOCK, 0, 2, 2, None, None, progress=progress.append)
    assert r.data == 42 and r.text == "Widget added on screen 0 at (2,2), id 42. `homalawn undo` removes it."
    assert len(progress) == 1 and "set it up" in progress[0]


@pytest.mark.parametrize("wid,restored,state", [
    (42, 0, "ready"), (42, 4, "needs setup"), (42, 16, "needs permission"), (42, 17, "needs permission"),
    (-1, 1, "loading"), (-1, 5, "loading"), (-1, 0, "loading")])
def test_widget_state_from_the_row(wid, restored, state):
    from homalawn.layout import widget_state
    assert widget_state(wid, restored) == state
