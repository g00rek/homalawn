"""The Homa Lawn service (`homalawn serve`): phones, Look and the theme follower over a unix socket.

Every test runs the real asyncio server on a temp socket with fake sessions and fake ops, and
short intervals, so nothing here touches a phone or sleeps for long."""
import asyncio
import hashlib
import json
import logging
import os
import stat
import threading
from pathlib import Path

import pytest

from homalawn import daemon, lookconf, ops as real_ops
from homalawn.ops import OpError, Result


def fp_of(name: str) -> str:
    return hashlib.sha256(name.encode()).hexdigest()


class FakeSession:
    def __init__(self, fp, name):
        self.fp = fp
        self.name = name
        self.resets = 0

    def reset(self):
        self.resets += 1


class FakeOps:
    """Stands in for homalawn.ops: status/look_apply/undo/snapshot_list per phone name."""

    def __init__(self, cur=None):
        self.cur = cur
        self.online = {}            # name -> bool (status answers)
        self.calls = []             # (op, phone name)
        self.fail = {}              # name -> OpError raised by look_apply
        self.gate = {}              # name -> threading.Event look_apply waits on
        self.no_wallpaper = set()   # names whose apply sends no wallpaper
        self.locked_ = {}           # name -> what locked() answers (True/False/None; default None)
        self.unsaved = set()        # names whose current wallpaper cannot be saved first
        self.overwrites = []        # names applied with overwrite_wallpaper=True
        self.book = False           # answer like the link does: key, changed_at, shown, mine
        self.stamps = []            # what stamp(key) answered in look_apply
        self.next_key = "k1"        # the key look_apply puts in its data
        self.shown = True           # ... and whether the phone showed the look
        self.mine = True
        self.state = {}             # name -> look_state answer (a callable replaces it)

    # addresses live in peers.json (the test's own config), so the real functions serve
    addresses_list = staticmethod(real_ops.addresses_list)
    addresses_set = staticmethod(real_ops.addresses_set)

    def locked(self, s):
        self.calls.append(("locked", s.name))
        return self.locked_.get(s.name)

    def status(self, s):
        self.calls.append(("status", s.name))
        if not self.online.get(s.name, True):
            raise OpError("link failed: phone not reachable", transport=True)
        return Result("ok", data={"via": "link", "phone": s.name})

    def look_state(self, s):
        self.calls.append(("look_state", s.name))
        st = self.state.get(s.name)
        if st is None:
            raise OpError("unknown_method: unknown method launcher.look_state", code="unknown_method")
        return st

    def look_apply(self, s, settings, theme=None, background=None, overwrite_wallpaper=False, parts=None, stamp=None):
        self.calls.append(("look_apply", s.name))
        g = self.gate.get(s.name)
        if g is not None:
            assert g.wait(5)
        if s.name in self.fail:
            raise self.fail[s.name]
        if overwrite_wallpaper:
            self.overwrites.append(s.name)
        elif s.name in self.unsaved:
            raise OpError("The current wallpaper cannot be saved first, so the look was not applied.",
                          code="wallpaper_unsaved")
        if settings.source == "follow":
            theme, bg = self.cur()
            theme, bg = theme, str(bg)
        else:
            theme, bg = settings.theme, settings.pick_for(settings.theme)
        if s.name in self.no_wallpaper:
            bg = None
        extra = {}
        if self.book and stamp is not None:
            extra = {"key": self.next_key, "changed_at": stamp(self.next_key), "shown": self.shown, "mine": self.mine}
            self.stamps.append(extra["changed_at"])
        return Result(f"Look applied on {s.name}.", ["a note"],
                      data={"snapshot": "snap-1", "theme": theme, "background": bg} | extra)

    def undo(self, s, no_backup=False):
        self.calls.append(("undo", s.name))
        return Result("Restored snap-1", [], data="snap-1")

    def snapshot_list(self, s):
        rows = [{"name": "snap-1", "device": "link:a"}, {"name": "snap-2", "device": "link:b"},
                {"name": "snap-3", "device": None}] + list(getattr(self, "extra_snaps", []))
        return Result("\n".join(r["name"] for r in rows), data=rows)

    def snapshot_restore(self, s, name, force=False, no_backup=False):
        self.calls.append(("restore", s.name, name, force))
        return Result(f"Restored {name}", [], data=name)

    def phone_info(self, s):
        self.calls.append(("phone_info", s.name))
        return Result("", data={"address": "100.1.2.3:47100", "app": {"versionName": "15.0-homalawn",
                                                                      "versionCode": 7, "sha256": "ab"},
                                "permissions": {"default_home": True, "screen_control": False,
                                                "wallpaper_backup": None}})

    def app_update(self, s):
        self.calls.append(("app_update", s.name))
        g = self.gate.get(("update", s.name))
        if g is not None:
            assert g.wait(5)
        return Result("Updated.", ["Sending x.apk (1.0 MB) to A over the link."], data={"outcome": "installed"})

    def home_get(self, s, size=None):
        self.calls.append(("home_get", s.name, size))
        return Result("", data={"grid": [4, 5], "hotseat_count": 4, "screens": [], "hotseat": [],
                                "icons": {"com.a/com.a.A": "/cache/a.png"}})

    def home_apps(self, s, size=None):
        self.calls.append(("home_apps", s.name, size))
        return Result("", data=[{"component": "com.a/com.a.A", "label": "A", "icon": "/cache/a.png"}])

    def home_widgets(self, s, size=None):
        self.calls.append(("home_widgets", s.name, size))
        return Result("", data=[{"provider": "com.w/.W", "label": "W", "cells": [2, 1], "preview": None}])

    def home_apply(self, s, layout, grid=None, hotseat=None, look=None):
        self.calls.append(("home_apply", s.name, layout, grid, hotseat, look))
        bad = getattr(self, "home_invalid", None)
        if bad:
            raise OpError("screen 0: com.a/com.a.A overlaps another item at (1,1)", code="invalid", problems=bad)
        return Result("Home screen applied.", [], data={"snapshot": "home-1"})

    def search_get(self, s):
        self.calls.append(("search_get", s.name))
        return Result("", data={"gestures": {"swipe_down": {"type": "openNotificationdata"}}, "search": {"apps": True}})

    def search_apply(self, s, gestures=None, search=None):
        self.calls.append(("search_apply", s.name, gestures, search))
        bad = getattr(self, "search_invalid", None)
        if bad:
            raise OpError("Swipe down: 'fly' cannot be set here", code="invalid", problems=bad)
        return Result("Search & gestures applied.", [], data={"snapshot": "prefs-1", "changed": ["k"]})

    def searches(self, name):
        return [c for c in self.calls if c[0] == "search_apply" and c[1] == name]

    def homes(self, name):
        return [c for c in self.calls if c[0] == "home_apply" and c[1] == name]

    def applied(self, name):
        return [c for c in self.calls if c == ("look_apply", name)]


class Current:
    def __init__(self):
        self.value = ("Alpha", Path("/bg/alpha-1.png"))

    def __call__(self):
        return self.value


def make(tmp_path, phones=("A", "B"), looks=None, applied="current", **kw):
    """`phones` may be a list the test changes later (peers are re-read). `applied`: what
    applied.json holds at start; "current" = every follow phone already shows the current look
    (so nothing is caught up at start), else a dict as stored."""
    cur = Current()
    ops = FakeOps(cur)
    look_path = tmp_path / "look.json"
    applied_path = tmp_path / "state" / "applied.json"
    if looks:
        lookconf.save({n: lookconf.validate(d) for n, d in looks.items()}, look_path)
    if applied == "current":
        applied = {n: {"theme": "Alpha", "background": "/bg/alpha-1.png", "at": 1.0}
                   for n, d in (looks or {}).items() if d.get("source") == "follow"}
    if applied:
        applied_path.parent.mkdir(parents=True)
        applied_path.write_text(json.dumps(applied))
    svc = daemon.Service(
        socket_path=tmp_path / "run" / "homalawn" / "homalawnd.sock",
        peers=lambda: {fp_of(n): {"name": n, "device": f"link:{n.lower()}"} for n in phones},
        applied_path=applied_path,
        session_factory=FakeSession,
        ops=ops,
        screen_size=lambda s: (1080, 2340),
        current=cur,
        list_themes=kw.pop("list_themes", lambda: []),
        look_path=look_path,
        check_interval=kw.pop("check_interval", 0.05),
        follow_interval=kw.pop("follow_interval", 0.05),
        backoff_min=kw.pop("backoff_min", 0.05),
        backoff_max=kw.pop("backoff_max", 0.2),
        clock=kw.pop("clock", lambda: 1000.0),
        agents=kw.pop("agents", None) or FakeAgents(),   # never the real agents' configs
        own_fp=kw.pop("own_fp", lambda: fp_of("THIS COMPUTER")),   # never the real identity
        **kw,
    )
    return svc, ops, cur


class Client:
    def __init__(self, r, w):
        self.r, self.w = r, w
        self.n = 0
        self.lines = []

    async def send(self, method, **params):
        self.n += 1
        self.w.write((json.dumps({"id": self.n, "method": method, "params": params}) + "\n").encode())
        await self.w.drain()
        return self.n

    async def line(self, timeout=3.0):
        raw = await asyncio.wait_for(self.r.readline(), timeout)
        assert raw, "connection closed"
        return json.loads(raw)

    async def reply(self, n, timeout=3.0):
        """The reply with id n; events seen meanwhile are kept in self.lines."""
        while True:
            m = await self.line(timeout)
            if m.get("id") == n:
                return m
            self.lines.append(m)

    async def call(self, method, **params):
        return await self.reply(await self.send(method, **params))

    async def event(self, pred=lambda st: True, timeout=3.0):
        while self.lines:
            m = self.lines.pop(0)
            if m.get("event") == "state" and pred(m["state"]):
                return m["state"]
        loop = asyncio.get_running_loop()
        end = loop.time() + timeout
        while True:
            m = await self.line(max(0.01, end - loop.time()))
            if m.get("event") == "state" and pred(m["state"]):
                return m["state"]


async def connect(svc):
    r, w = await asyncio.open_unix_connection(str(svc.socket_path))
    return Client(r, w)


def run(coro):
    return asyncio.run(asyncio.wait_for(coro, 20))


async def until(pred, timeout=3.0):
    loop = asyncio.get_running_loop()
    end = loop.time() + timeout
    while not pred():
        if loop.time() > end:
            raise AssertionError("condition not met in time")
        await asyncio.sleep(0.01)


def phone(state, name):
    return next(p for p in state["phones"] if p["name"] == name)


FOLLOW = {"source": "follow"}


# -- socket path and permissions ---------------------------------------------------------------
def test_socket_path_env_order(monkeypatch):
    monkeypatch.setenv("HOMALAWN_SOCKET", "/x/y.sock")
    assert daemon.socket_path() == Path("/x/y.sock")
    monkeypatch.delenv("HOMALAWN_SOCKET")
    monkeypatch.setenv("XDG_RUNTIME_DIR", "/run/user/77")
    assert daemon.socket_path() == Path("/run/user/77/homalawn/homalawnd.sock")
    monkeypatch.delenv("XDG_RUNTIME_DIR")
    assert daemon.socket_path() == Path(f"/run/user/{os.getuid()}/homalawn/homalawnd.sock")


def test_socket_dir_0700_and_socket_0600(tmp_path):
    svc, _, _ = make(tmp_path)

    async def go():
        await svc.start()
        try:
            assert stat.S_IMODE(svc.socket_path.parent.stat().st_mode) == 0o700
            assert stat.S_IMODE(svc.socket_path.stat().st_mode) == 0o600
            assert stat.S_ISSOCK(svc.socket_path.stat().st_mode)
        finally:
            await svc.stop()
        assert not svc.socket_path.exists()
    run(go())


def test_other_uid_refused(tmp_path, monkeypatch):
    svc, _, _ = make(tmp_path)
    monkeypatch.setattr(daemon, "peer_uid", lambda sock: os.getuid() + 1)

    async def go():
        await svc.start()
        try:
            c = await connect(svc)
            await c.send("phones")
            try:
                got = await asyncio.wait_for(c.r.readline(), 3)
            except ConnectionResetError:
                got = b""
            assert got == b""
        finally:
            await svc.stop()
    run(go())


def test_same_uid_peercred_is_read_from_the_socket(tmp_path):
    svc, _, _ = make(tmp_path)

    async def go():
        await svc.start()
        try:
            c = await connect(svc)
            assert (await c.call("phones"))["result"][0]["name"] == "A"
        finally:
            await svc.stop()
    run(go())


def test_second_service_refused_while_one_runs(tmp_path):
    svc, _, _ = make(tmp_path)
    other, _, _ = make(tmp_path)

    async def go():
        await svc.start()
        try:
            with pytest.raises(daemon.AlreadyRunning):
                await other.start()
        finally:
            await svc.stop()
    run(go())


def test_socket_path_too_long_is_a_plain_error(tmp_path):
    svc, _, _ = make(tmp_path)
    svc.socket_path = tmp_path / ("x" * 120) / "homalawnd.sock"

    async def go():
        with pytest.raises(daemon.CannotStart, match="Cannot open the Homa Lawn socket"):
            await svc.start()
        await svc.stop()
    run(go())


def test_stale_socket_file_replaced(tmp_path):
    svc, _, _ = make(tmp_path)
    svc.socket_path.parent.mkdir(parents=True, mode=0o700)   # left by an earlier run: private
    svc.socket_path.write_text("stale")

    async def go():
        await svc.start()
        try:
            c = await connect(svc)
            assert "result" in await c.call("phones")
        finally:
            await svc.stop()
    run(go())


# -- protocol ----------------------------------------------------------------------------------
def test_unknown_method_and_bad_json(tmp_path):
    svc, _, _ = make(tmp_path)

    async def go():
        await svc.start()
        try:
            c = await connect(svc)
            assert (await c.call("nope"))["error"]["code"] == "unknown_method"
            c.w.write(b"{not json\n")
            await c.w.drain()
            m = await c.line()
            assert m["error"]["code"] == "bad_request"
            r = await c.call("look.get", phone="Zed")
            assert r["error"]["code"] == "unknown_phone"
        finally:
            await svc.stop()
    run(go())


def test_line_over_8_mib_dropped_connection_kept(tmp_path):
    svc, _, _ = make(tmp_path)

    async def go():
        await svc.start()
        try:
            c = await connect(svc)
            c.w.write(b"x" * (daemon.MAX_LINE + 10) + b"\n")
            await c.w.drain()
            assert "result" in await c.call("phones")
        finally:
            await svc.stop()
    run(go())


def test_subscribe_gets_state_now_and_after_look_set(tmp_path):
    svc, ops, _ = make(tmp_path)

    async def go():
        await svc.start()
        try:
            c = await connect(svc)
            await c.call("subscribe")
            st = await c.event()
            assert {p["name"] for p in st["phones"]} == {"A", "B"}
            assert st["omarchy"] == {"theme": "Alpha", "background": "/bg/alpha-1.png"}
            assert set(phone(st, "A")) >= {"name", "connected", "screen", "look", "applied", "pending", "error"}
            await until(lambda: all(p.connected for p in svc.phones.values()))
            r = await c.call("look.set", phone="A", settings={"source": "chosen", "theme": "Beta"})
            assert r["result"]["text"] == "Look applied on A."
            assert r["result"]["notes"] == ["a note"]
            st = await c.event(lambda s: phone(s, "A")["look"]["source"] == "chosen"
                               and phone(s, "A")["applied"] is not None)
            assert phone(st, "A")["applied"] == {"theme": "Beta", "background": None, "at": 1000.0}
            assert phone(st, "A")["screen"] == {"w": 1080, "h": 2340}
            assert lookconf.load(svc.look_path)[fp_of("A")].theme == "Beta"
        finally:
            await svc.stop()
    run(go())


def test_look_set_invalid(tmp_path):
    svc, ops, _ = make(tmp_path)

    async def go():
        await svc.start()
        try:
            c = await connect(svc)
            r = await c.call("look.set", phone="A", settings={"source": "sideways"})
            assert r["error"]["code"] == "invalid"
            assert "sideways" in r["error"]["message"]
            assert not svc.look_path.exists()
            assert ops.applied("A") == []
        finally:
            await svc.stop()
    run(go())


def layered(home: int) -> dict:
    """An off look whose home screen has a colour layer (the current look.json shape)."""
    wp = lookconf.wallpaper_default()
    wp["home"]["layer"] = home
    return {"source": "off", "wallpaper": wp}


def test_concurrent_look_set_for_two_phones_both_persisted(tmp_path, monkeypatch):
    """Two look.set at once: neither save may overwrite the other with an older copy."""
    svc, ops, _ = make(tmp_path)
    real_save = lookconf.save
    import time as _time

    def slow_save(looks, path=None):
        if "B" not in looks:          # the older copy (A only) is written last unless saves queue
            _time.sleep(0.3)
        real_save(looks, path)
    monkeypatch.setattr(lookconf, "save", slow_save)

    async def go():
        await svc.start()
        try:
            c = await connect(svc)
            na = await c.send("look.set", phone="A", settings=layered(10))
            await asyncio.sleep(0.05)
            nb = await c.send("look.set", phone="B", settings=layered(20))
            got = {}
            while len(got) < 2:                    # B's reply may come first
                m = await c.line()
                if m.get("id") in (na, nb):
                    got[m["id"]] = m
            assert "result" in got[na] and "result" in got[nb]
            saved = lookconf.load(svc.look_path)
            assert saved[fp_of("A")].layer_of("home") == 10 and saved[fp_of("B")].layer_of("home") == 20
        finally:
            await svc.stop()
    run(go())


def test_look_set_takes_an_older_shape_draft_and_saves_the_current_one(tmp_path):
    svc, ops, _ = make(tmp_path)

    async def go():
        await svc.start()
        try:
            c = await connect(svc)
            r = await c.call("look.set", phone="A", settings={"source": "off", "target": "lock",
                                                               "layer": {"home": 0, "lock": 40}})
            look = r["result"]["look"]
            assert "target" not in look and "layer" not in look
            assert look["wallpaper"]["home"]["kind"] == "keep" and look["wallpaper"]["lock"]["kind"] == "wallpaper"
            assert look["wallpaper"]["lock"]["layer"] == 40 and "same" not in look["wallpaper"]
            disk = json.loads(svc.look_path.read_text())[fp_of("A")]
            assert disk == look
        finally:
            await svc.stop()
    run(go())


def test_look_gradients_draws_the_six_once_into_the_cache(tmp_path, monkeypatch):
    from PIL import Image
    from homalawn.look import Theme
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))
    themes = [Theme("Alpha", tmp_path / "alpha", {"accent": "#7aa2f7", "background": "#1a1b26"}, [], None,
                    slug="alpha"),
              Theme("Beta", tmp_path / "beta", {}, [], None, slug="beta")]
    svc, ops, _ = make(tmp_path, list_themes=lambda: themes)

    async def go():
        await svc.start()
        try:
            c = await connect(svc)
            got = (await c.call("look.gradients", theme="alpha", w=60, h=120))["result"]
            assert [g["style"] for g in got] == ["fade", "diagonal", "glow", "duo", "mesh", "vignette"]
            assert [g["name"] for g in got][:2] == ["Fade", "Diagonal"]
            folder = tmp_path / "cache" / "homalawn" / "gradients"
            for g in got:
                assert Path(g["path"]).parent == folder
                with Image.open(g["path"]) as im:
                    assert im.format == "PNG" and im.size == (60, 120)
            stamps = [os.stat(g["path"]).st_mtime_ns for g in got]
            os.utime(got[0]["path"], ns=(1, 1))          # a rewrite would move it again
            again = (await c.call("look.gradients", phone="A", w=60, h=120))["result"]   # Omarchy's: Alpha
            assert again == got and os.stat(got[0]["path"]).st_mtime_ns == 1
            assert [os.stat(g["path"]).st_mtime_ns for g in got][1:] == stamps[1:]
            assert not [p for p in folder.iterdir() if p.suffix != ".png"]     # no temp files left
            other = daemon.gradient_files({"accent": "#ff0000", "background": "#000000"}, 60, 120)
            assert not {g["path"] for g in other} & {g["path"] for g in got}   # other colours, other files
            assert (await c.call("look.gradients", theme="Nope", w=60, h=120))["result"] == []
            assert (await c.call("look.gradients", theme="Beta", w=60, h=120))["result"] == []   # no colours
            for bad in ({"w": 0, "h": 120}, {"w": 60, "h": 5000}, {"w": "60", "h": 120}, {"w": True, "h": 120}):
                assert (await c.call("look.gradients", theme="Alpha", **bad))["error"]["code"] == "invalid"
        finally:
            await svc.stop()
    run(go())


def test_look_set_off_saves_without_applying(tmp_path):
    svc, ops, _ = make(tmp_path, looks={"A": FOLLOW})

    async def go():
        await svc.start()
        try:
            await until(lambda: svc.phones[fp_of("A")].connected)
            c = await connect(svc)
            r = await c.call("look.set", phone="A", settings={"source": "off"})
            assert r["result"]["look"]["source"] == "off" and r["result"]["text"] is None
            assert ops.applied("A") == []
            assert (await c.call("look.get", phone="A"))["result"]["source"] == "off"
        finally:
            await svc.stop()
    run(go())


def test_themes_list(tmp_path):
    from homalawn.look import Theme
    t = Theme("Alpha", tmp_path, {"accent": "#7aa2f7"}, [tmp_path / "1.png"], None)
    svc, _, _ = make(tmp_path, list_themes=lambda: [t])

    async def go():
        await svc.start()
        try:
            c = await connect(svc)
            r = await c.call("themes.list")
            assert r["result"] == [{"name": "Alpha", "dir": str(tmp_path), "colors": {"accent": "#7aa2f7"},
                                    "backgrounds": [str(tmp_path / "1.png")], "preview": None,
                                    "icon_palette": ["#7aa2f7"], "icon_plate": None}]
        finally:
            await svc.stop()
    run(go())


def test_undo_and_snapshots(tmp_path):
    svc, ops, _ = make(tmp_path)

    async def go():
        await svc.start()
        try:
            await until(lambda: svc.phones[fp_of("A")].connected)
            c = await connect(svc)
            r = await c.call("undo", phone="A")
            assert r["result"] == {"text": "Restored snap-1", "notes": [], "data": "snap-1"}
            r = await c.call("snapshots", phone="A")
            assert r["result"]["data"] == [{"name": "snap-1", "device": "link:a", "kind": "manual",
                                            "title": "Saved by hand: snap-1", "contents": "other", "time": None}]
            r = await c.call("snapshots", phone="B")
            assert [x["name"] for x in r["result"]["data"]] == ["snap-2"]
        finally:
            await svc.stop()
    run(go())


# -- follower ----------------------------------------------------------------------------------
def test_follower_applies_to_follow_phones_only(tmp_path):
    svc, ops, cur = make(tmp_path, phones=("A", "B", "C"),
                         looks={"A": FOLLOW, "B": {"source": "chosen", "theme": "Beta"}, "C": {"source": "off"}})

    async def go():
        await svc.start()
        try:
            await until(lambda: all(p.connected for p in svc.phones.values()))
            await asyncio.sleep(0.15)
            assert ops.applied("A") == [] and ops.applied("B") == []  # no change yet: nothing applied
            cur.value = ("Beta", Path("/bg/beta-1.png"))
            await until(lambda: ops.applied("A"))
            await asyncio.sleep(0.15)
            assert len(ops.applied("A")) == 1
            assert ops.applied("B") == [] and ops.applied("C") == []
            st = svc.state()
            assert st["omarchy"] == {"theme": "Beta", "background": "/bg/beta-1.png"}
            assert phone(st, "A")["applied"] == {"theme": "Beta", "background": "/bg/beta-1.png", "at": 1000.0}
        finally:
            await svc.stop()
    run(go())


def test_offline_follow_phone_pending_then_applied_on_reconnect(tmp_path):
    svc, ops, cur = make(tmp_path, looks={"A": FOLLOW})
    ops.online["A"] = False

    async def go():
        await svc.start()
        try:
            await until(lambda: ("status", "A") in ops.calls)
            assert not svc.phones[fp_of("A")].connected
            cur.value = ("Beta", Path("/bg/beta-1.png"))
            await until(lambda: svc.phones[fp_of("A")].pending)
            assert ops.applied("A") == []
            ops.online["A"] = True
            await until(lambda: ops.applied("A"))
            await until(lambda: not svc.phones[fp_of("A")].pending)
            assert svc.phones[fp_of("A")].connected and svc.phones[fp_of("A")].error is None
        finally:
            await svc.stop()
    run(go())


def test_backoff_grows_while_offline(tmp_path):
    svc, ops, _ = make(tmp_path, phones=("A",), backoff_min=0.02, backoff_max=0.08, check_interval=5)
    ops.online["A"] = False
    assert [svc.backoff(i) for i in range(5)] == [0.02, 0.04, 0.08, 0.08, 0.08]
    d = daemon.DEFAULTS
    assert (d["check_interval"], d["follow_interval"], d["backoff_min"], d["backoff_max"]) == (10, 2, 2, 60)


def test_slow_apply_does_not_block_other_phone(tmp_path):
    svc, ops, cur = make(tmp_path, looks={"A": FOLLOW})
    gate = threading.Event()
    ops.gate["A"] = gate

    async def go():
        await svc.start()
        try:
            await until(lambda: svc.phones[fp_of("A")].connected)
            cur.value = ("Beta", Path("/bg/beta-1.png"))
            await until(lambda: ops.applied("A"))          # A's apply is now stuck in its thread
            c = await connect(svc)
            r = await asyncio.wait_for(c.call("look.get", phone="B"), 1.0)
            assert r["result"]["source"] == "off"
            assert svc.state()["busy"] == [{"phone": fp_of("A"), "name": "A", "what": "look"}]
            gate.set()
            await until(lambda: svc.state()["busy"] == [])
        finally:
            gate.set()
            await svc.stop()
    run(go())


def test_op_failure_becomes_op_failed_and_phone_error(tmp_path):
    svc, ops, cur = make(tmp_path, looks={"A": FOLLOW})
    ops.fail["A"] = OpError("Cannot save the phone's current wallpaper first")

    async def go():
        await svc.start()
        try:
            await until(lambda: svc.phones[fp_of("A")].connected)
            c = await connect(svc)
            r = await c.call("look.apply", phone="A")
            assert r["error"] == {"code": "op_failed", "message": "Cannot save the phone's current wallpaper first."}
            assert phone(svc.state(), "A")["error"] == "Cannot save the phone's current wallpaper first."
            assert phone(svc.state(), "A")["applied"]["at"] == 1.0          # the previous look is kept
            n = len(ops.applied("A"))
            await asyncio.sleep(0.2)                       # no retry loop
            assert len(ops.applied("A")) == n
            del ops.fail["A"]
            r = await c.call("look.apply", phone="A")      # the next trigger tries again
            assert r["result"]["text"] == "Look applied on A."
            assert phone(svc.state(), "A")["error"] is None
        finally:
            await svc.stop()
    run(go())


def test_look_apply_retries_a_failed_follow_apply_with_the_current_omarchy_look(tmp_path):
    """The window's Retry: a follow phone whose apply failed (no photo access yet) gets the look
    Omarchy shows now, and its error clears."""
    svc, ops, cur = make(tmp_path, looks={"A": FOLLOW})
    ops.fail["A"] = OpError("The current wallpaper cannot be saved first")

    async def go():
        await svc.start()
        try:
            await until(lambda: svc.phones[fp_of("A")].connected)
            cur.value = ("Beta", Path("/bg/beta-2.png"))              # Omarchy changes; the follower fails
            await until(lambda: svc.phones[fp_of("A")].error is not None)
            assert svc.phones[fp_of("A")].applied["theme"] == "Alpha"
            del ops.fail["A"]                                         # photo access allowed on the phone
            c = await connect(svc)
            r = await c.call("look.apply", phone="A")
            assert r["result"]["data"]["theme"] == "Beta"
            st = phone(svc.state(), "A")
            assert st["error"] is None and not st["pending"]
            assert (st["applied"]["theme"], st["applied"]["background"]) == ("Beta", "/bg/beta-2.png")
            assert svc.phones[fp_of("A")].seen == {"theme": "Beta", "background": "/bg/beta-2.png"}
        finally:
            await svc.stop()
    run(go())


def test_plain_exception_from_apply_is_op_failed_too(tmp_path):
    svc, ops, _ = make(tmp_path, looks={"A": FOLLOW})
    ops.fail["A"] = ValueError("Background picture is not an image")

    async def go():
        await svc.start()
        try:
            await until(lambda: svc.phones[fp_of("A")].connected)
            c = await connect(svc)
            r = await c.call("look.apply", phone="A")
            assert r["error"] == {"code": "op_failed", "message": "Background picture is not an image."}
        finally:
            await svc.stop()
    run(go())


def test_transport_failure_drops_link_and_keeps_pending(tmp_path):
    svc, ops, cur = make(tmp_path, looks={"A": FOLLOW}, check_interval=5)
    ops.fail["A"] = OpError("link failed: reset", transport=True)

    async def go():
        await svc.start()
        try:
            await until(lambda: svc.phones[fp_of("A")].connected)
            cur.value = ("Beta", Path("/bg/beta-1.png"))
            await until(lambda: ops.applied("A"))
            await until(lambda: not svc.phones[fp_of("A")].connected)
            assert svc.phones[fp_of("A")].pending
            assert svc.phones[fp_of("A")].session.resets >= 1
            del ops.fail["A"]
            await until(lambda: not svc.phones[fp_of("A")].pending)   # the reconnect applied it
            assert svc.phones[fp_of("A")].connected and svc.phones[fp_of("A")].error is None
        finally:
            await svc.stop()
    run(go())


def test_look_apply_offline_phone(tmp_path):
    svc, ops, _ = make(tmp_path, looks={"A": FOLLOW})
    ops.online["A"] = False

    async def go():
        await svc.start()
        try:
            await until(lambda: ("status", "A") in ops.calls)
            c = await connect(svc)
            r = await c.call("look.apply", phone="A")
            assert r["error"]["code"] == "offline"
            r = await c.call("look.set", phone="A", settings={"source": "chosen", "theme": "Beta"})
            assert r["result"]["text"] is None and svc.phones[fp_of("A")].pending
        finally:
            await svc.stop()
    run(go())


# -- the command -------------------------------------------------------------------------------
def test_serve_command_runs_the_service(monkeypatch):
    from typer.testing import CliRunner
    from homalawn import cli
    seen = []
    monkeypatch.setattr(daemon, "run", lambda: seen.append("run"))
    res = CliRunner().invoke(cli.app, ["serve"])
    assert res.exit_code == 0, res.output
    assert seen == ["run"]


def test_serve_command_already_running(monkeypatch):
    from typer.testing import CliRunner
    from homalawn import cli

    def boom():
        raise daemon.AlreadyRunning("The Homa Lawn service is already running (/x).")
    monkeypatch.setattr(daemon, "run", boom)
    res = CliRunner().invoke(cli.app, ["serve"])
    assert res.exit_code == 1
    assert "already running" in res.output


# -- fix round 1 -------------------------------------------------------------------------------
def test_peers_reread_new_phone_appears_removed_one_goes(tmp_path):
    phones = ["A"]
    svc, ops, _ = make(tmp_path, phones=phones)

    async def go():
        await svc.start()
        try:
            c = await connect(svc)
            await c.call("subscribe")
            await c.event()
            phones.append("B")
            st = await c.event(lambda s: [p["name"] for p in s["phones"]] == ["A", "B"])
            await until(lambda: svc.phones[fp_of("B")].connected)
            old = svc.phones[fp_of("A")].session
            phones.remove("A")
            st = await c.event(lambda s: [p["name"] for p in s["phones"]] == ["B"])
            assert fp_of("A") not in svc.phones and old.resets >= 1
            n = len([x for x in ops.calls if x == ("status", "A")])
            await asyncio.sleep(0.2)
            assert len([x for x in ops.calls if x == ("status", "A")]) == n   # its watcher stopped
        finally:
            await svc.stop()
    run(go())


def test_peers_read_failure_keeps_the_known_phones(tmp_path):
    calls = {"n": 0}
    svc, ops, _ = make(tmp_path, phones=("A",))
    good = svc._peers

    def flaky():
        calls["n"] += 1
        if calls["n"] > 2:                 # the first read is start()'s, the second the first sync
            raise OSError("peers.json unreadable")
        return good()
    svc._peers = flaky

    async def go():
        await svc.start()
        try:
            await until(lambda: calls["n"] >= 3)
            assert list(svc.phones) == [fp_of("A")]
        finally:
            await svc.stop()
    run(go())


def test_applied_is_persisted_and_restored(tmp_path):
    svc, ops, cur = make(tmp_path, looks={"A": FOLLOW})

    async def go():
        await svc.start()
        try:
            await until(lambda: svc.phones[fp_of("A")].connected)
            cur.value = ("Beta", Path("/bg/beta-1.png"))
            await until(lambda: (svc.phones[fp_of("A")].applied or {}).get("theme") == "Beta")
        finally:
            await svc.stop()
    run(go())
    saved = json.loads((tmp_path / "state" / "applied.json").read_text())
    assert saved[fp_of("A")] == {"theme": "Beta", "background": "/bg/beta-1.png", "at": 1000.0,
                          "seen": {"theme": "Beta", "background": "/bg/beta-1.png"}}
    svc2, _, _ = make(tmp_path, looks={"A": FOLLOW}, applied=None)

    async def again():
        await svc2.start()
        try:
            assert svc2.state()["phones"][0]["applied"]["theme"] == "Beta"
        finally:
            await svc2.stop()
    run(again())


def test_catch_up_at_start_when_omarchy_changed_meanwhile(tmp_path):
    old = {"A": {"theme": "Old", "background": "/bg/old.png", "at": 1.0},
           "B": {"theme": "Old", "background": "/bg/old.png", "at": 1.0}}
    svc, ops, _ = make(tmp_path, looks={"A": FOLLOW, "B": {"source": "chosen", "theme": "Beta"}}, applied=old)

    async def go():
        await svc.start()
        try:
            await until(lambda: ops.applied("A"))
            await asyncio.sleep(0.15)
            assert len(ops.applied("A")) == 1 and ops.applied("B") == []
            assert svc.phones[fp_of("A")].applied["theme"] == "Alpha"
        finally:
            await svc.stop()
    run(go())


def test_catch_up_matches_theme_by_slug_and_ignores_background_when_wallpaper_off(tmp_path):
    looks = {"A": {"source": "follow", "sync": {"wallpaper": False}}, "B": FOLLOW}
    applied = {"A": {"theme": "alpha", "background": None, "at": 1.0},
               "B": {"theme": "ALPHA", "background": "/bg/alpha-1.png", "at": 1.0}}
    svc, ops, _ = make(tmp_path, looks=looks, applied=applied)

    async def go():
        await svc.start()
        try:
            await until(lambda: all(p.connected for p in svc.phones.values()))
            await asyncio.sleep(0.15)
            assert ops.applied("A") == [] and ops.applied("B") == []
        finally:
            await svc.stop()
    run(go())


def test_catch_up_on_reconnect(tmp_path):
    svc, ops, cur = make(tmp_path, looks={"A": FOLLOW}, check_interval=0.05)

    async def go():
        await svc.start()
        try:
            await until(lambda: svc.phones[fp_of("A")].connected)
            ops.online["A"] = False
            await until(lambda: not svc.phones[fp_of("A")].connected)
            cur.value = ("Beta", Path("/bg/beta-1.png"))
            await until(lambda: svc.phones[fp_of("A")].pending)
            ops.online["A"] = True
            await until(lambda: ops.applied("A"))
            await until(lambda: not svc.phones[fp_of("A")].pending)
        finally:
            await svc.stop()
    run(go())


def test_busy_is_per_phone(tmp_path):
    svc, ops, cur = make(tmp_path, looks={"A": FOLLOW, "B": FOLLOW})
    ga, gb = threading.Event(), threading.Event()
    ops.gate.update(A=ga, B=gb)

    async def go():
        await svc.start()
        try:
            await until(lambda: all(p.connected for p in svc.phones.values()))
            cur.value = ("Beta", Path("/bg/beta-1.png"))
            await until(lambda: len(svc.state()["busy"]) == 2)
            assert sorted(b["name"] for b in svc.state()["busy"]) == ["A", "B"]
            ga.set()
            await until(lambda: svc.state()["busy"] == [{"phone": fp_of("B"), "name": "B", "what": "look"}])
            gb.set()
            await until(lambda: svc.state()["busy"] == [])
        finally:
            ga.set(); gb.set()
            await svc.stop()
    run(go())


def test_watch_and_follow_survive_unexpected_errors(tmp_path, caplog):
    svc, ops, cur = make(tmp_path, looks={"A": FOLLOW})
    real_check, real_read = svc._check, svc._read_current
    boom = {"check": 1, "read": 2}   # read: once at start, once in the follower loop

    async def check(ph):
        if boom["check"]:
            boom["check"] -= 1
            raise RuntimeError("check exploded")
        return await real_check(ph)

    def read():
        if boom["read"]:
            boom["read"] -= 1
            raise RuntimeError("read exploded")
        return real_read()
    svc._check, svc._read_current = check, read

    async def go():
        await svc.start()
        try:
            await until(lambda: svc.phones[fp_of("A")].connected)     # the watcher went on after its error
            cur.value = ("Beta", Path("/bg/beta-1.png"))
            await until(lambda: ops.applied("A"))              # the follower too
        finally:
            await svc.stop()
    run(go())
    text = caplog.text
    assert "check exploded" in text and "read exploded" in text


def test_socket_dir_unusable_is_cannot_start(tmp_path):
    svc, _, _ = make(tmp_path)
    (tmp_path / "file").write_text("x")
    svc.socket_path = tmp_path / "file" / "homalawn" / "homalawnd.sock"

    async def go():
        with pytest.raises(daemon.CannotStart, match="Cannot open the Homa Lawn socket"):
            await svc.start()
        await svc.stop()
    run(go())


def test_existing_open_socket_folder_is_refused_not_changed(tmp_path):
    """HOMALAWN_SOCKET=~/x.sock must not chmod the home folder: a folder that is already there
    is used only when it is ours and 0700."""
    svc, _, _ = make(tmp_path)
    folder = tmp_path / "shared"
    folder.mkdir(mode=0o755)
    folder.chmod(0o755)
    svc.socket_path = folder / "x.sock"

    async def go():
        with pytest.raises(daemon.CannotStart, match="only you may use"):
            await svc.start()
        await svc.stop()
    run(go())
    assert stat.S_IMODE(folder.stat().st_mode) == 0o755
    assert not (folder / "x.sock").exists()


def test_existing_socket_folder_of_another_user_is_refused(tmp_path, monkeypatch):
    svc, _, _ = make(tmp_path)
    folder = tmp_path / "theirs"
    folder.mkdir(mode=0o700)
    svc.socket_path = folder / "x.sock"
    real = os.getuid()
    monkeypatch.setattr(daemon.os, "getuid", lambda: real + 1)

    async def go():
        with pytest.raises(daemon.CannotStart, match="belongs to another user"):
            await svc.start()
        await svc.stop()
    run(go())


def test_existing_private_socket_folder_is_used(tmp_path):
    svc, _, _ = make(tmp_path)
    folder = tmp_path / "mine"
    folder.mkdir(mode=0o700)
    folder.chmod(0o700)
    svc.socket_path = folder / "x.sock"

    async def go():
        await svc.start()
        try:
            assert stat.S_ISSOCK(svc.socket_path.stat().st_mode)
        finally:
            await svc.stop()
    run(go())


def test_state_dir_env_order(monkeypatch, tmp_path):
    monkeypatch.setenv("HOMALAWN_STATE_DIR", "/s/h")
    assert daemon.applied_path() == Path("/s/h/applied.json")
    monkeypatch.delenv("HOMALAWN_STATE_DIR")
    monkeypatch.setenv("XDG_STATE_HOME", "/xs")
    assert daemon.applied_path() == Path("/xs/homalawn/applied.json")
    monkeypatch.delenv("XDG_STATE_HOME")
    monkeypatch.setenv("HOME", str(tmp_path))
    assert daemon.applied_path() == tmp_path / ".local/state/homalawn/applied.json"


# -- fix round 2 -------------------------------------------------------------------------------
async def _reconnect(svc, ops, name):
    ops.online[name] = False
    await until(lambda: not svc.phones[fp_of(name)].connected)
    ops.online[name] = True
    await until(lambda: svc.phones[fp_of(name)].connected)


def test_no_reapply_on_reconnect_when_omarchy_unchanged_and_no_wallpaper_was_sent(tmp_path):
    svc, ops, cur = make(tmp_path, looks={"A": FOLLOW})
    ops.no_wallpaper.add("A")

    async def go():
        await svc.start()
        try:
            await until(lambda: svc.phones[fp_of("A")].connected)
            cur.value = ("Beta", Path("/bg/beta-1.png"))
            await until(lambda: ops.applied("A"))
            await until(lambda: (svc.phones[fp_of("A")].applied or {}).get("theme") == "Beta")
            assert svc.phones[fp_of("A")].applied["background"] is None
            await _reconnect(svc, ops, "A")
            await _reconnect(svc, ops, "A")
            await asyncio.sleep(0.1)
            assert len(ops.applied("A")) == 1          # nothing changed in Omarchy: no new snapshots
        finally:
            await svc.stop()
    run(go())


def test_reconnect_after_omarchy_background_change_applies(tmp_path):
    svc, ops, cur = make(tmp_path, looks={"A": FOLLOW}, check_interval=5)

    async def go():
        await svc.start()
        try:
            await until(lambda: svc.phones[fp_of("A")].connected)
            ops.online["A"] = False
            svc.phones[fp_of("A")].wake.set()
            await until(lambda: not svc.phones[fp_of("A")].connected)
            cur.value = ("Alpha", Path("/bg/alpha-2.png"))
            await until(lambda: svc.phones[fp_of("A")].pending)
            svc.phones[fp_of("A")].pending = False             # only the seen-vs-current check is left
            ops.online["A"] = True
            await until(lambda: ops.applied("A"))
        finally:
            await svc.stop()
    run(go())


def test_bad_peer_does_not_stop_the_others(tmp_path):
    svc, ops, _ = make(tmp_path, phones=("A", "Bad", "B"))
    real = svc._session_factory

    def factory(fp, name):
        if name == "Bad":
            raise RuntimeError("cannot make a session")
        return real(fp, name)
    svc._session_factory = factory

    async def go():
        await svc.start()
        try:
            await until(lambda: fp_of("A") in svc.phones and fp_of("B") in svc.phones)
            assert fp_of("Bad") not in svc.phones
            await until(lambda: svc.phones[fp_of("A")].connected and svc.phones[fp_of("B")].connected)
        finally:
            await svc.stop()
    run(go())


# -- a locked phone, and a Lawnchair that did not reload --------------------------------------
NOT_LOADED = OpError("link failed: Lawnchair did not finish loading its home screen within 60s Partly applied; "
                     "`homalawn undo` puts back the previous look (look-1).", code="not_loaded")


def test_locked_phone_gets_the_look_at_once(tmp_path):
    """No waiting for the unlock (owner, 2026-10-17): the look goes out at once and the service never
    even asks whether the phone is locked."""
    svc, ops, cur = make(tmp_path, phones=("A",), looks={"A": FOLLOW})
    ops.locked_["A"] = True

    async def go():
        await svc.start()
        try:
            await until(lambda: svc.phones[fp_of("A")].connected)
            c = await connect(svc)
            await c.call("look.set", phone="A", settings={"source": "follow", "sync": {"icons": True}})
            assert len(ops.applied("A")) == 1
            p = svc.phones[fp_of("A")]
            assert not p.pending and p.waiting is None and p.error is None
            cur.value = ("Beta", Path("/bg/beta-1.png"))                  # a follow change while locked
            await until(lambda: len(ops.applied("A")) == 2)
            assert svc.phones[fp_of("A")].applied["theme"] == "Beta"
            assert ("locked", "A") not in ops.calls
        finally:
            await svc.stop()
    run(go())


def test_build_that_does_not_say_locked_applies_as_before(tmp_path):
    svc, ops, cur = make(tmp_path, phones=("A",), looks={"A": FOLLOW})   # locked() answers None

    async def go():
        await svc.start()
        try:
            await until(lambda: svc.phones[fp_of("A")].connected)
            cur.value = ("Beta", Path("/bg/beta-1.png"))
            await until(lambda: ops.applied("A"))
            await until(lambda: not svc.phones[fp_of("A")].pending)
            assert svc.phones[fp_of("A")].waiting is None
        finally:
            await svc.stop()
    run(go())


def test_reload_timeout_stays_pending_and_is_retried_once_a_minute(tmp_path):
    now = [1000.0]
    svc, ops, cur = make(tmp_path, phones=("A",), looks={"A": FOLLOW}, clock=lambda: now[0])
    ops.fail["A"] = NOT_LOADED

    async def go():
        await svc.start()
        try:
            await until(lambda: svc.phones[fp_of("A")].connected)
            cur.value = ("Beta", Path("/bg/beta-1.png"))
            await until(lambda: svc.phones[fp_of("A")].waiting == "reload")
            p = svc.phones[fp_of("A")]
            assert p.pending and p.connected and "Partly applied" in p.error   # the link is kept
            await asyncio.sleep(0.3)                                   # many check cycles, same minute
            assert len(ops.applied("A")) == 1                          # no retry, no extra snapshot
            now[0] += 60
            await until(lambda: len(ops.applied("A")) == 2)            # the next cycle tries again
            await asyncio.sleep(0.3)
            assert len(ops.applied("A")) == 2
            del ops.fail["A"]
            now[0] += 60
            await until(lambda: svc.phones[fp_of("A")].waiting is None)
            assert len(ops.applied("A")) == 3
            assert not p.pending and p.error is None and p.applied["theme"] == "Beta"
            await asyncio.sleep(0.2)
            assert len(ops.applied("A")) == 3
        finally:
            await svc.stop()
    run(go())


def test_reload_retries_stop_before_they_push_out_the_snapshot(tmp_path):
    now = [1000.0]
    svc, ops, cur = make(tmp_path, phones=("A",), looks={"A": FOLLOW}, clock=lambda: now[0])
    ops.fail["A"] = NOT_LOADED

    async def go():
        await svc.start()
        try:
            await until(lambda: svc.phones[fp_of("A")].connected)
            cur.value = ("Beta", Path("/bg/beta-1.png"))
            for i in range(1, daemon.RETRY_MAX + 1):
                await until(lambda: len(ops.applied("A")) == i)
                await until(lambda: svc.phones[fp_of("A")].tries == i)
                now[0] += 60
            await asyncio.sleep(0.3)
            assert len(ops.applied("A")) == daemon.RETRY_MAX           # stopped trying by itself
            assert svc.phones[fp_of("A")].pending and svc.phones[fp_of("A")].waiting == "reload"
            cur.value = ("Gamma", Path("/bg/gamma-1.png"))            # a new Omarchy change tries again
            await until(lambda: len(ops.applied("A")) == daemon.RETRY_MAX + 1)
            assert svc.phones[fp_of("A")].tries == 1
        finally:
            await svc.stop()
    run(go())


# -- phone info, history restore, app update, service info ---------------------------------------
def make_info(tmp_path, **kw):
    peers = {fp_of("A"): {"paired": 1790000000.0, "hosts": ["100.9.9.9:47100"]}}
    return make(tmp_path, peer_info=lambda fp: peers.get(fp, {}), **kw)


def test_phone_info_of_a_connected_phone_asks_the_phone(tmp_path):
    svc, ops, _ = make_info(tmp_path)

    async def go():
        await svc.start()
        try:
            await until(lambda: svc.phones[fp_of("A")].connected)
            c = await connect(svc)
            r = (await c.call("phone.info", phone="A"))["result"]
            assert r["name"] == "A" and r["connected"] is True
            assert r["paired"] == 1790000000.0
            assert r["address"] == "100.1.2.3"                 # the address that answers, not the config's
            assert r["app"]["versionName"] == "15.0-homalawn"
            assert r["permissions"] == {"default_home": True, "screen_control": False, "wallpaper_backup": None}
            assert ("phone_info", "A") in ops.calls
        finally:
            await svc.stop()
    run(go())


def test_phone_info_of_an_offline_phone_does_not_dial_it(tmp_path):
    svc, ops, _ = make_info(tmp_path)
    ops.online["A"] = False

    async def go():
        await svc.start()
        try:
            await until(lambda: ("status", "A") in ops.calls)
            c = await connect(svc)
            r = (await c.call("phone.info", phone="A"))["result"]
            assert r["connected"] is False and r["address"] == "100.9.9.9"     # the link's own port left out
            assert r["app"] is None and r["permissions"] is None
            assert ("phone_info", "A") not in ops.calls
        finally:
            await svc.stop()
    run(go())


def test_phone_info_while_a_write_runs_answers_without_waiting(tmp_path):
    svc, ops, cur = make_info(tmp_path, looks={"A": FOLLOW})
    gate = threading.Event()
    ops.gate["A"] = gate

    async def go():
        await svc.start()
        try:
            await until(lambda: svc.phones[fp_of("A")].connected)
            cur.value = ("Beta", Path("/bg/beta-1.png"))
            await until(lambda: ops.applied("A"))
            c = await connect(svc)
            r = (await asyncio.wait_for(c.call("phone.info", phone="A"), 1.0))["result"]
            assert r["busy"] == "look" and r["app"] is None
        finally:
            gate.set()
            await svc.stop()
    run(go())


def test_snapshots_say_kind_and_time(tmp_path):
    svc, ops, _ = make(tmp_path)
    ops.extra_snaps = [{"name": "look-20261014-101500", "device": "link:a"},
                       {"name": "prefs-20261013-090000", "device": "link:a"},
                       {"name": "auto-20261012-080000", "device": "link:a"},
                       {"name": "wallpaper-20261011-070000", "device": "link:a"}]

    async def go():
        await svc.start()
        try:
            c = await connect(svc)
            rows = (await c.call("snapshots", phone="A"))["result"]["data"]
            kinds = {r["name"]: (r["kind"], r["title"], r["contents"]) for r in rows}
            assert kinds == {"snap-1": ("manual", "Saved by hand: snap-1", "other"),
                             "look-20261014-101500": ("style", "Wallpaper & style", "look"),
                             "prefs-20261013-090000": ("other", "Settings", "prefs"),
                             "auto-20261012-080000": ("home", "Home screen", "layout"),
                             "wallpaper-20261011-070000": ("wallpaper", "Wallpaper", "wallpaper")}
            look = next(r for r in rows if r["name"].startswith("look-"))
            import time as _t
            assert look["time"] == _t.mktime((2026, 10, 14, 10, 15, 0, 0, 0, -1))
            assert next(r for r in rows if r["name"] == "snap-1")["time"] is None
        finally:
            await svc.stop()
    run(go())


def test_snapshot_restore_only_this_phones_snapshots(tmp_path):
    svc, ops, _ = make(tmp_path)

    async def go():
        await svc.start()
        try:
            await until(lambda: svc.phones[fp_of("A")].connected)
            c = await connect(svc)
            r = await c.call("snapshot.restore", phone="A", name="snap-1")
            assert r["result"]["text"] == "Restored snap-1"
            assert ("restore", "A", "snap-1", False) in ops.calls
            for other in ("snap-2", "snap-3", "nope"):          # B's, unknown phone's, missing
                r = await c.call("snapshot.restore", phone="A", name=other)
                assert r["error"]["code"] == "not_this_phone", other
            assert [x for x in ops.calls if x[0] == "restore"] == [("restore", "A", "snap-1", False)]
            r = await c.call("snapshot.restore", phone="A")
            assert r["error"]["code"] == "invalid"
        finally:
            await svc.stop()
    run(go())


def test_app_update_runs_as_a_write_and_never_beside_another(tmp_path):
    svc, ops, cur = make(tmp_path, looks={"A": FOLLOW})

    async def go():
        await svc.start()
        try:
            await until(lambda: svc.phones[fp_of("A")].connected)
            c = await connect(svc)
            r = await c.call("app.update", phone="A")
            assert r["result"] == {"text": "Updated.", "notes": ["Sending x.apk (1.0 MB) to A over the link."],
                                   "data": {"outcome": "installed"}}
            gate = threading.Event()
            ops.gate["A"] = gate                                    # a look apply is now stuck
            cur.value = ("Beta", Path("/bg/beta-1.png"))
            await until(lambda: svc.state()["busy"] == [{"phone": fp_of("A"), "name": "A", "what": "look"}])
            r = await c.call("app.update", phone="A")
            assert r["error"]["code"] == "busy"
            gate.set()
            await until(lambda: svc.state()["busy"] == [])
            ug = threading.Event()
            ops.gate[("update", "A")] = ug
            n = await c.send("app.update", phone="A")
            await until(lambda: svc.state()["busy"] == [{"phone": fp_of("A"), "name": "A", "what": "update"}])
            r = await c.call("undo", phone="A")                     # no write beside the update either
            assert r["error"]["code"] == "busy"
            ug.set()
            assert "result" in await c.reply(n)
            assert len([x for x in ops.calls if x[0] == "app_update"]) == 2
        finally:
            for g in ops.gate.values():
                g.set()
            await svc.stop()
    run(go())


def test_app_update_offline(tmp_path):
    svc, ops, _ = make(tmp_path)
    ops.online["A"] = False

    async def go():
        await svc.start()
        try:
            await until(lambda: ("status", "A") in ops.calls)
            c = await connect(svc)
            r = await c.call("app.update", phone="A")
            assert r["error"]["code"] == "offline"
        finally:
            await svc.stop()
    run(go())


def test_service_info(tmp_path):
    svc, ops, _ = make(tmp_path, version="1.2+abc")

    async def go():
        await svc.start()
        try:
            c = await connect(svc)
            r = (await c.call("service.info"))["result"]
            assert r["version"] == "1.2+abc" and r["socket"] == str(svc.socket_path)
            assert r["pid"] == os.getpid() and isinstance(r["started"], float)
        finally:
            await svc.stop()
    run(go())


def test_phone_info_waits_for_a_short_connection_check(tmp_path):
    svc, ops, _ = make_info(tmp_path, check_interval=5)

    async def go():
        await svc.start()
        try:
            await until(lambda: svc.phones[fp_of("A")].connected)
            ph = svc.phones[fp_of("A")]
            await ph.lock.acquire()                       # a connection check in progress
            asyncio.get_running_loop().call_later(0.2, ph.lock.release)
            c = await connect(svc)
            r = (await c.call("phone.info", phone="A"))["result"]
            assert r["app"]["versionName"] == "15.0-homalawn" and r["busy"] is None
        finally:
            await svc.stop()
    run(go())


# -- a wallpaper that cannot be saved first ----------------------------------------------------
def test_look_set_with_an_unsaveable_wallpaper_says_so_with_its_own_code(tmp_path):
    svc, ops, cur = make(tmp_path)
    ops.unsaved.add("A")

    async def go():
        await svc.start()
        try:
            await until(lambda: svc.phones[fp_of("A")].connected)
            c = await connect(svc)
            r = await c.call("look.set", phone="A", settings={"source": "chosen", "theme": "Alpha"})
            assert r["error"]["code"] == "wallpaper_unsaved"
            st = phone(svc.state(), "A")
            assert st["error_code"] == "wallpaper_unsaved" and st["look"]["source"] == "chosen"  # saved
            assert ops.overwrites == []
            r = await c.call("look.apply", phone="A", overwrite_wallpaper=True)   # the user said Overwrite
            assert r["result"]["text"] == "Look applied on A."
            assert ops.overwrites == ["A"]
            st = phone(svc.state(), "A")
            assert st["error"] is None and st["error_code"] is None
        finally:
            await svc.stop()
    run(go())


def test_look_set_can_carry_the_overwrite_consent(tmp_path):
    svc, ops, cur = make(tmp_path)
    ops.unsaved.add("A")

    async def go():
        await svc.start()
        try:
            await until(lambda: svc.phones[fp_of("A")].connected)
            c = await connect(svc)
            r = await c.call("look.set", phone="A", settings={"source": "chosen", "theme": "Alpha"},
                             overwrite_wallpaper=True)
            assert "result" in r and ops.overwrites == ["A"]
        finally:
            await svc.stop()
    run(go())


def test_a_follow_apply_never_overwrites_an_unsaveable_wallpaper_by_itself(tmp_path):
    svc, ops, cur = make(tmp_path, looks={"A": FOLLOW})
    ops.unsaved.add("A")

    async def go():
        await svc.start()
        try:
            await until(lambda: svc.phones[fp_of("A")].connected)
            cur.value = ("Beta", Path("/bg/beta-1.png"))             # Omarchy changes
            await until(lambda: svc.phones[fp_of("A")].error_code is not None)
            st = phone(svc.state(), "A")
            assert st["error_code"] == "wallpaper_unsaved" and st["applied"]["theme"] == "Alpha"
            assert ops.overwrites == []
        finally:
            await svc.stop()
    run(go())


# -- the home-screen editor ----------------------------------------------------------------------
LAYOUT = {"grid": {"cols": 4, "rows": 5}, "screens": [{"index": 0, "items": []}], "hotseat": []}


def test_home_reads_go_to_the_phone_with_the_size(tmp_path):
    svc, ops, _ = make(tmp_path, phones=("A",))

    async def go():
        await svc.start()
        try:
            await until(lambda: svc.phones[fp_of("A")].connected)
            c = await connect(svc)
            r = await c.call("home.get", phone="A", size=96)
            assert r["result"]["icons"] == {"com.a/com.a.A": "/cache/a.png"} and r["result"]["grid"] == [4, 5]
            assert ("home_get", "A", 96) in ops.calls
            assert (await c.call("home.apps"))["result"][0]["component"] == "com.a/com.a.A"
            assert (await c.call("home.widgets"))["result"][0]["cells"] == [2, 1]
            r = await c.call("home.get", size=9)
            assert r["error"]["code"] == "invalid"
            svc.phones[fp_of("A")].connected = False
            assert (await c.call("home.get"))["error"]["code"] == "offline"
        finally:
            await svc.stop()
    run(go())


def test_home_apply_passes_the_draft_and_answers(tmp_path):
    svc, ops, _ = make(tmp_path, phones=("A",))

    async def go():
        await svc.start()
        try:
            await until(lambda: svc.phones[fp_of("A")].connected)
            c = await connect(svc)
            r = await c.call("home.apply", phone="A", layout=LAYOUT, grid=[4, 5], hotseat_count=5,
                             look_prefs={"label_gap_dp": -4})
            assert r["result"] == {"text": "Home screen applied.", "notes": [], "data": {"snapshot": "home-1"}}
            assert ops.homes("A") == [("home_apply", "A", LAYOUT, [4, 5], 5, {"label_gap_dp": -4})]
            for bad in ({"layout": []}, {"layout": LAYOUT, "grid": [4]}, {"layout": LAYOUT, "hotseat_count": "5"},
                        {"layout": LAYOUT, "look_prefs": [1]}):
                assert (await c.call("home.apply", **bad))["error"]["code"] == "invalid"
            assert len(ops.homes("A")) == 1
        finally:
            await svc.stop()
    run(go())


def test_home_apply_invalid_layout_returns_located_errors(tmp_path):
    svc, ops, _ = make(tmp_path, phones=("A",))
    ops.home_invalid = [{"message": "screen 0: com.a/com.a.A overlaps another item at (1,1)", "where": "screen",
                         "screen": 0, "x": 1, "y": 1, "id": 7}]

    async def go():
        await svc.start()
        try:
            await until(lambda: svc.phones[fp_of("A")].connected)
            c = await connect(svc)
            r = await c.call("home.apply", layout=LAYOUT)
            assert r["error"]["code"] == "invalid"
            assert r["error"]["errors"] == ops.home_invalid
            assert "overlaps" in r["error"]["message"]
        finally:
            await svc.stop()
    run(go())


def test_home_apply_on_a_locked_phone_is_written_at_once(tmp_path):
    svc, ops, _ = make(tmp_path, phones=("A",))
    ops.locked_["A"] = True

    async def go():
        await svc.start()
        try:
            await until(lambda: svc.phones[fp_of("A")].connected)
            c = await connect(svc)
            r = await c.call("home.apply", layout=LAYOUT, look_prefs={"icon_scale": 1.2})
            assert r["result"]["text"] == "Home screen applied."
            assert [h[2] for h in ops.homes("A")] == [LAYOUT]
            assert (await c.call("home.cancel"))["error"]["code"] == "unknown_method"   # nothing is ever held
            assert ("locked", "A") not in ops.calls
        finally:
            await svc.stop()
    run(go())


def test_search_get_and_apply_pass_the_draft_and_answer(tmp_path):
    svc, ops, _ = make(tmp_path, phones=("A",))

    async def go():
        await svc.start()
        try:
            await until(lambda: svc.phones[fp_of("A")].connected)
            c = await connect(svc)
            r = await c.call("search.get", phone="A")
            assert r["result"]["gestures"]["swipe_down"] == {"type": "openNotificationdata"}
            r = await c.call("search.apply", phone="A", gestures={"swipe_down": "homarSearch"},
                             search={"calculator": True})
            assert r["result"] == {"text": "Search & gestures applied.", "notes": [],
                                   "data": {"snapshot": "prefs-1", "changed": ["k"]}}
            assert ops.searches("A") == [("search_apply", "A", {"swipe_down": "homarSearch"}, {"calculator": True})]
            for bad in ({}, {"gestures": []}, {"search": "x"}, {"gestures": {}, "search": {}}):
                assert (await c.call("search.apply", **bad))["error"]["code"] == "invalid"
            assert len(ops.searches("A")) == 1
            svc.phones[fp_of("A")].connected = False
            assert (await c.call("search.get"))["error"]["code"] == "offline"
        finally:
            await svc.stop()
    run(go())


def test_search_apply_invalid_returns_located_errors(tmp_path):
    svc, ops, _ = make(tmp_path, phones=("A",))
    ops.search_invalid = [{"message": "Swipe down: 'fly' cannot be set here", "where": "gesture",
                           "setting": "swipe_down"}]

    async def go():
        await svc.start()
        try:
            await until(lambda: svc.phones[fp_of("A")].connected)
            c = await connect(svc)
            r = await c.call("search.apply", gestures={"swipe_down": "fly"})
            assert r["error"]["code"] == "invalid" and r["error"]["errors"] == ops.search_invalid
        finally:
            await svc.stop()
    run(go())


def test_search_apply_on_a_locked_phone_is_written_at_once(tmp_path):
    svc, ops, _ = make(tmp_path, phones=("A",))
    ops.locked_["A"] = True

    async def go():
        await svc.start()
        try:
            await until(lambda: svc.phones[fp_of("A")].connected)
            c = await connect(svc)
            r = await c.call("search.apply", gestures={"swipe_down": "homarSearch"})
            assert r["result"]["text"] == "Search & gestures applied."
            assert [x[2:] for x in ops.searches("A")] == [({"swipe_down": "homarSearch"}, {})]
        finally:
            await svc.stop()
    run(go())


class FakeAgents:
    """Stands in for homalawn.agents: nothing is read from or written to a real agent's config."""
    COMMAND = ["homalawn", "mcp"]
    REGISTRY = {"claude": None, "hermes": None}

    class AgentError(Exception):
        pass

    def __init__(self):
        self.rows = [{"id": "claude", "name": "Claude Code", "installed": True, "config": "/tmp/x/.claude.json",
                      "registered": False, "enabled": None, "command": None, "error": None}]
        self.connected = []
        self.syncs = []
        self.default_file = None

    def omarchy_default_file(self):
        from pathlib import Path
        return self.default_file or Path("/nonexistent/homalawn-test/agent")

    def omarchy_default(self):
        return "claude"

    def sync(self, quiet=False):
        self.syncs.append(quiet)
        return ["Codex can now use your phone: Homa Lawn was added to its settings."]

    def status(self):
        return [dict(r) for r in self.rows]

    def connect(self, agent):
        if agent != "claude":
            raise self.AgentError(f"unknown agent {agent!r}")
        self.connected.append(agent)
        self.rows[0].update(registered=True, enabled=True, command="homalawn mcp")
        return "Homa Lawn is registered in Claude Code."


def test_agents_status_and_connect_go_through_the_agents_module(tmp_path):
    fake = FakeAgents()
    svc, ops, _ = make(tmp_path, phones=("A",), agents=fake)

    async def go():
        await svc.start()
        try:
            c = await connect(svc)
            r = (await c.call("agents.status"))["result"]
            assert r["command"] == "homalawn mcp" and r["agents"][0]["registered"] is False
            assert r["default"] == "claude"
            r = (await c.call("agents.connect", agent="claude"))["result"]
            assert r["text"] == "Homa Lawn is registered in Claude Code." and r["agents"][0]["registered"] is True
            assert fake.connected == ["claude"]
            e = (await c.call("agents.connect", agent="cursor"))["error"]
            assert e["code"] == "op_failed" and "unknown agent" in e["message"].lower()
            assert (await c.call("agents.connect"))["error"]["code"] == "invalid"
        finally:
            await svc.stop()
    run(go())


def test_the_service_registers_agents_at_start_when_the_default_changes_and_periodically(tmp_path):
    fake = FakeAgents()
    fake.default_file = tmp_path / "agent"
    svc, ops, _ = make(tmp_path, phones=(), agents=fake, agents_check_interval=0.02, agents_sync_interval=3600)

    async def go():
        await svc.start()
        try:
            await until(lambda: len(fake.syncs) == 1)
            assert fake.syncs == [True]                     # quiet: only changes are logged
            await asyncio.sleep(0.1)
            assert len(fake.syncs) == 1                     # nothing changed, the hour is not up
            fake.default_file.write_text("codex\n")       # Omarchy's default agent was chosen
            await until(lambda: len(fake.syncs) == 2)
            svc.agents_sync_interval = 0.05                 # the periodic check
            await until(lambda: len(fake.syncs) >= 3)
        finally:
            await svc.stop()
    run(go())


def test_recipes_list_answers_the_recipes(tmp_path, monkeypatch):
    d = tmp_path / "recipes"
    d.mkdir()
    (d / "a.md").write_text("---\nname: alarm\ndescription: Set an alarm\nneeds: screen control\n---\n# Alarm\n")
    (d / "README.md").write_text("# not a recipe\n")
    monkeypatch.setenv("HOMALAWN_RECIPES", str(d))
    svc, ops, _ = make(tmp_path, phones=("A",), agents=FakeAgents())

    async def go():
        await svc.start()
        try:
            c = await connect(svc)
            assert (await c.call("recipes.list"))["result"] == [
                {"name": "alarm", "title": "Alarm", "description": "Set an alarm", "needs": "screen control",
                 "verified": False}]
        finally:
            await svc.stop()
    run(go())


def test_agents_changes_are_the_agents_snapshots_of_the_paired_phones(tmp_path):
    svc, ops, _ = make(tmp_path, phones=("A", "B"), agents=FakeAgents())
    ops.extra_snaps = [{"name": "prefs-20261015-100000", "device": "link:a", "by": "agent"},
                       {"name": "home-20261015-110000", "device": "link:b", "by": "agent"},
                       {"name": "look-20261015-120000", "device": "link:a", "by": None},       # the window's
                       {"name": "auto-20261015-130000", "device": "link:zz", "by": "agent"}]   # not paired

    async def go():
        await svc.start()
        try:
            await until(lambda: svc.phones[fp_of("A")].device and svc.phones[fp_of("B")].device)
            c = await connect(svc)
            rows = (await c.call("agents.changes"))["result"]
            assert [(r["phone"], r["name"], r["kind"], r["title"]) for r in rows] == [
                ("B", "home-20261015-110000", "home", "Home screen"), ("A", "prefs-20261015-100000", "other", "Settings")]
            assert rows[0]["contents"] == "layout"
            assert rows[0]["time"] > rows[1]["time"]
            assert len((await c.call("agents.changes", limit=1))["result"]) == 1
            assert (await c.call("agents.changes", limit=0))["error"]["code"] == "invalid"
            # Restore goes through snapshot.restore, which takes only that phone's snapshots
            r = await c.call("snapshot.restore", phone="B", name="home-20261015-110000")
            assert r["result"]["text"] == "Restored home-20261015-110000"
        finally:
            await svc.stop()
    run(go())


# -- the Agents page's chat ---------------------------------------------------------------------------
async def chat_event(c, pred, timeout=5.0):
    """The next `chat` event whose chat matches; other lines are dropped."""
    while c.lines:
        m = c.lines.pop(0)
        if m.get("event") == "chat" and pred(m["chat"]):
            return m["chat"]
    loop = asyncio.get_running_loop()
    end = loop.time() + timeout
    while True:
        m = await c.line(max(0.01, end - loop.time()))
        if m.get("event") == "chat" and pred(m["chat"]):
            return m["chat"]


def test_chat_send_streams_to_subscribers_continues_stops_and_starts_over(tmp_path, monkeypatch):
    import test_chat
    bin_dir = tmp_path / "bin"
    monkeypatch.setenv("PATH", f"{bin_dir}{os.pathsep}{os.environ['PATH']}")
    log = test_chat.fake_agent(bin_dir, "claude", test_chat.CLAUDE)   # FakeAgents: the default is claude
    svc, ops, _ = make(tmp_path, phones=("A", "B"))

    async def go():
        await svc.start()
        try:
            c = await connect(svc)
            await c.call("subscribe")
            h = (await c.call("chat.history"))["result"]
            assert h["agent"]["name"] == "Claude Code" and h["agent"]["supported"] and h["messages"] == []
            e = (await c.call("chat.send", text="hi"))["error"]        # two phones: say which
            assert e["code"] == "ambiguous"
            assert (await c.call("chat.send", text="hi", phone="Z"))["error"]["code"] == "unknown_phone"
            r = (await c.call("chat.send", text="what is in the dock?", phone="B"))["result"]
            assert r["running"] is True and r["messages"][-1]["text"] == "what is in the dock?"
            done = await chat_event(c, lambda ch: not ch["running"])
            assert [m["role"] for m in done["messages"]] == ["user", "agent", "step", "agent"]
            assert done["messages"][-1]["text"] == "Your dock has Phone and Chrome." and done["continues"]
            assert test_chat.calls(log)[0]["phone"] == fp_of("B")
            first = test_chat.calls(log)[0]
            assert "[Selected phone: B]" in (first["stdin"] or first["argv"][-1]) and done["phone"] == "B"

            await c.call("chat.send", text="and the folders?", phone="B")
            await chat_event(c, lambda ch: not ch["running"])
            assert "--resume" in test_chat.calls(log)[1]["argv"]

            test_chat.fake_agent(bin_dir, "claude", test_chat.CLAUDE[:5], wait=30)
            await c.call("chat.send", text="slow one", phone="B")
            await chat_event(c, lambda ch: ch["messages"][-1]["role"] == "agent")
            assert (await c.call("chat.cancel"))["result"] == {"cancelled": True}
            h = (await c.call("chat.history"))["result"]
            assert h["running"] is False and h["messages"][-1]["text"] == "Stopped."

            fresh = (await c.call("chat.new"))["result"]
            assert fresh["messages"] == [] and fresh["continues"] is False
        finally:
            await svc.stop()
    run(go())


def test_chat_with_an_unsupported_default_agent_is_refused_plainly(tmp_path):
    class Hermes(FakeAgents):
        def omarchy_default(self):
            return "hermes"
    svc, ops, _ = make(tmp_path, phones=("A",), agents=Hermes())

    async def go():
        await svc.start()
        try:
            c = await connect(svc)
            h = (await c.call("chat.history"))["result"]
            assert h["agent"] == {**h["agent"], "id": "hermes", "name": "Hermes", "supported": False}
            assert "terminal" in h["agent"]["why"]
            e = (await c.call("chat.send", text="hi"))["error"]
            assert e["code"] == "unsupported" and e["message"] == h["agent"]["why"]
        finally:
            await svc.stop()
    run(go())


def test_themes_without_their_folders_are_logged_as_an_error(tmp_path, caplog):
    from homalawn.look import ThemeDirMissing

    def broken():
        raise ThemeDirMissing("`omarchy theme dir tokyo-night` gives /themes/tokyo-night")
    svc, _, _ = make(tmp_path, list_themes=broken)
    with caplog.at_level("ERROR", logger="homalawn.daemon"):
        assert run(svc._themes()) == []
    rec, = [r for r in caplog.records if r.levelname == "ERROR"]
    assert "cannot list the Omarchy themes" in rec.getMessage() and "/themes/tokyo-night" in rec.getMessage()
    assert rec.exc_info is None          # one clear line, not a traceback


def test_two_phones_with_one_name_are_two_phones(tmp_path):
    svc, ops, cur = make(tmp_path, phones=())
    svc._peers = lambda: {fp_of("1"): {"name": "Pixel 8", "device": "link:1"},
                          fp_of("2"): {"name": "Pixel 8", "device": "link:2"}}

    async def go():
        await svc._sync_peers()
        try:
            assert sorted(svc.phones) == sorted([fp_of("1"), fp_of("2")])
            st = svc.state()["phones"]
            assert {p["id"] for p in st} == {fp_of("1"), fp_of("2")} and {p["name"] for p in st} == {"Pixel 8"}
            with pytest.raises(daemon.Fail) as e:
                svc._phone({"phone": "Pixel 8"})
            assert e.value.code == "ambiguous"
            assert svc._phone({"phone": fp_of("2")}).fp == fp_of("2")
        finally:
            await svc.stop()
    run(go())


def test_name_keyed_files_move_to_fingerprints(tmp_path):
    svc, ops, cur = make(tmp_path, phones=("A",), looks={"A": {"source": "follow"}})

    async def go():
        await svc.start()
        try:
            raw = json.loads((tmp_path / "look.json").read_text())
            assert list(raw) == [fp_of("A")]
            assert (tmp_path / "look.json.names").exists()
            assert list(json.loads(svc.applied_path.read_text())) == [fp_of("A")]
            assert (svc.applied_path.parent / "applied.json.names").exists()
        finally:
            await svc.stop()
    run(go())


def test_start_fails_when_the_paired_phones_cannot_be_read(tmp_path):
    svc, ops, cur = make(tmp_path, phones=("A",))

    def broken():
        raise OSError("peers.json unreadable")
    svc._peers = broken

    async def go():
        with pytest.raises(daemon.CannotStart, match="Cannot read the paired phones: peers.json unreadable"):
            await svc.start()
    run(go())


def test_not_paired_shows_as_error_code_and_clears_when_the_phone_trusts_again(tmp_path):
    async def go():
        from homalawn.link.client import NotPaired
        svc, ops, cur = make(tmp_path, phones=("A",))
        real = ops.status
        def refuse(s):
            raise NotPaired("A no longer trusts this computer")
        ops.status = refuse
        await svc._sync_peers()
        ph = svc.phones[fp_of("A")]
        await svc._check(ph)
        assert svc.state()["phones"][0]["error_code"] == "not_paired"
        ops.status = real
        await svc._check(ph)
        assert svc.state()["phones"][0]["error_code"] is None
        ph.error_code = "wallpaper_unsaved"
        await svc._check(ph)
        assert ph.error_code == "wallpaper_unsaved"
        await svc.stop()
    run(go())


# -- the look book on the service side (Task 15) -----------------------------------------------
def test_changed_at_is_kept_while_the_key_is_the_same(tmp_path):
    async def go():
        now = [1000.0]
        svc, ops, cur = make(tmp_path, phones=("A",), looks={"A": {"source": "follow"}}, applied={},
                             clock=lambda: now[0])
        ops.book = True
        await svc._sync_peers()
        ph = svc.phones[fp_of("A")]
        ph.connected = True
        for key in ("k1", "k1", "k2"):
            ops.next_key = key
            await svc._apply(ph)
            now[0] += 10
        assert ops.stamps[0] == ops.stamps[1] < ops.stamps[2]
        rec = json.loads(svc.applied_path.read_text())[fp_of("A")]
        assert rec["key"] == "k2" and rec["changed_at"] == ops.stamps[2]
    run(go())


def test_a_kept_look_is_marked_kept_and_a_shown_one_is_not(tmp_path):
    async def go():
        svc, ops, cur = make(tmp_path, phones=("A",), looks={"A": {"source": "follow"}}, applied={})
        ops.book = True
        await svc._sync_peers()
        ph = svc.phones[fp_of("A")]
        ph.task.cancel()      # the service is not started: no watcher may apply on its own meanwhile
        ph.connected = True
        ops.shown, ops.mine = False, False
        await svc._apply(ph)
        assert svc.state()["phones"][0]["kept"] is True
        ops.shown, ops.mine = False, True        # "Already on": the screen has it
        await svc._apply(ph)
        assert svc.state()["phones"][0]["kept"] is False
    run(go())


def test_look_state_reaches_the_window(tmp_path):
    async def go():
        svc, ops, cur = make(tmp_path, phones=("A",))
        ops.state["A"] = {"source": {"fp": "z", "name": "Laptop"}, "scope": "latest",
                          "you": {"key": "k1", "shown": False}, "applied": None}
        await svc._sync_peers()
        ph = svc.phones[fp_of("A")]
        await svc._check(ph)
        assert svc.state()["phones"][0]["look_source"] == {"self": False, "name": "Laptop", "scope": "latest"}
        ops.state["A"] = {"source": {"fp": fp_of("THIS COMPUTER"), "name": "Me"}, "scope": "chosen",
                          "you": {"key": "k1", "shown": True}, "applied": None}
        await svc._check(ph)
        assert svc.state()["phones"][0]["look_source"] == {"self": True, "name": "Me", "scope": "chosen"}
    run(go())


def test_a_look_state_that_cannot_be_read_leaves_no_source(tmp_path):
    async def go():
        svc, ops, cur = make(tmp_path, phones=("A",))
        await svc._sync_peers()
        ph = svc.phones[fp_of("A")]
        ph.look_source = {"self": False, "name": "Old", "scope": "latest"}
        await svc._check(ph)                      # the fake has no state for A: unknown_method
        assert svc.state()["phones"][0]["look_source"] is None
    run(go())


def test_an_unreadable_look_state_is_logged_once_and_clears_kept(tmp_path, caplog):
    async def go():
        svc, ops, cur = make(tmp_path, phones=("A",))
        await svc._sync_peers()
        ph = svc.phones[fp_of("A")]
        ph.kept = True
        caplog.set_level("INFO", logger=daemon.log.name)
        for _ in range(4):
            await svc._check(ph)
        assert len([r for r in caplog.records if "look state not read" in r.message]) == 1
        assert ph.kept is False
        ops.state["A"] = {"source": None, "scope": "latest", "you": {"key": None, "shown": False}, "applied": None}
        await svc._check(ph)                      # a read resets the reason
        del ops.state["A"]
        await svc._check(ph)
        assert len([r for r in caplog.records if "look state not read" in r.message]) == 2
    run(go())


def _book_phone(tmp_path, you, applied_key=None, source="follow"):
    """Phone A on a check where its look book answers `you`; applied.json holds the current look
    (with `applied_key`, or without a key as a pre-book service wrote it)."""
    rec = {"theme": "Alpha", "background": "/bg/alpha-1.png", "at": 1.0}
    if applied_key is not None:
        rec |= {"key": applied_key, "changed_at": 5}
    look = {"source": "follow"} if source == "follow" else {"source": source, "theme": "Alpha"}
    svc, ops, cur = make(tmp_path, phones=("A",))
    svc.looks = {fp_of("A"): lookconf.validate(look)}       # as start() loads them
    svc._applied = {fp_of("A"): rec}
    svc.omarchy = {"theme": "Alpha", "background": "/bg/alpha-1.png"}   # nothing behind Omarchy
    ops.book = True
    ops.state["A"] = {"source": None, "scope": "latest", "you": you, "applied": None}
    return svc, ops


def test_an_empty_book_after_an_upgrade_gets_the_look_once(tmp_path):
    async def go():
        svc, ops = _book_phone(tmp_path, {"key": None, "shown": False})
        await svc._sync_peers()
        ph = svc.phones[fp_of("A")]
        ph.task.cancel()
        await svc._check(ph)                   # applied.json says current, but the book lacks it
        assert len(ops.applied("A")) == 1
        await svc._check(ph)                   # once per connection, even if the book still lacks it
        assert len(ops.applied("A")) == 1
        ops.state["A"]["you"] = {"key": "k1", "shown": True}
        await svc._check(ph)
        assert len(ops.applied("A")) == 1
    run(go())


def test_a_book_holding_another_key_gets_the_look(tmp_path):
    async def go():
        svc, ops = _book_phone(tmp_path, {"key": "k0", "shown": True}, applied_key="k1")
        await svc._sync_peers()
        ph = svc.phones[fp_of("A")]
        ph.task.cancel()
        await svc._check(ph)
        assert len(ops.applied("A")) == 1
    run(go())


def test_a_book_holding_the_applied_key_gets_nothing(tmp_path):
    async def go():
        svc, ops = _book_phone(tmp_path, {"key": "k1", "shown": True}, applied_key="k1")
        await svc._sync_peers()
        ph = svc.phones[fp_of("A")]
        ph.task.cancel()
        await svc._check(ph)
        await svc._check(ph)
        assert ops.applied("A") == []
    run(go())


def test_a_look_not_shown_with_the_applied_key_is_left_alone(tmp_path):
    async def go():   # the person's undo (or another computer's look): never undone by a check
        svc, ops = _book_phone(tmp_path, {"key": "k1", "shown": False}, applied_key="k1")
        await svc._sync_peers()
        ph = svc.phones[fp_of("A")]
        ph.task.cancel()
        await svc._check(ph)
        assert ops.applied("A") == []
    run(go())


def test_an_empty_book_is_not_filled_when_the_look_is_off(tmp_path):
    async def go():
        svc, ops = _book_phone(tmp_path, {"key": None, "shown": False}, source="off")
        await svc._sync_peers()
        ph = svc.phones[fp_of("A")]
        ph.task.cancel()
        await svc._check(ph)
        assert ops.applied("A") == []
    run(go())


def test_an_empty_book_of_a_chosen_look_gets_it(tmp_path):
    async def go():
        svc, ops = _book_phone(tmp_path, {"key": None, "shown": False}, source="chosen")
        await svc._sync_peers()
        ph = svc.phones[fp_of("A")]
        ph.task.cancel()
        await svc._check(ph)
        assert len(ops.applied("A")) == 1
    run(go())


def test_a_failed_apply_clears_kept(tmp_path):
    async def go():
        svc, ops, cur = make(tmp_path, phones=("A",), looks={"A": {"source": "follow"}}, applied={})
        await svc._sync_peers()
        ph = svc.phones[fp_of("A")]
        ph.connected, ph.kept = True, True
        ops.fail["A"] = OpError("boom")
        with pytest.raises(daemon.Fail):
            await svc._apply(ph)
        assert ph.kept is False
    run(go())


def test_changed_at_survives_a_service_restart(tmp_path):
    async def go():
        svc, ops, cur = make(tmp_path, phones=("A",), looks={"A": {"source": "follow"}}, applied={},
                             clock=lambda: 1000.0)
        ops.book = True
        await svc._sync_peers()
        ph = svc.phones[fp_of("A")]
        ph.connected = True
        await svc._apply(ph)
        first = ops.stamps[0]
        svc2, ops2, _ = make(tmp_path, phones=("A",), looks={"A": {"source": "follow"}}, applied=None,
                             clock=lambda: 5000.0)
        ops2.book = True
        svc2._applied = svc2._load_applied()
        await svc2._sync_peers()
        ph2 = svc2.phones[fp_of("A")]
        ph2.connected = True
        await svc2._apply(ph2)
        assert ops2.stamps == [first] and first == 1000000
    run(go())


def _fake_pair(monkeypatch, fail=None, hold=None):
    from homalawn.link import identity, pairing

    def fake_pair(host, port, *, confirm, say, show_words, stop, on_conn, **kw):
        say("On the phone: unlock it")
        show_words(["apple", "brick", "cloud", "delta"])
        say("Confirm on the phone: Accept there only if it shows the same four words.")
        if hold is not None:
            hold.wait(5)
            if stop.is_set():
                raise pairing.PairError("Stopped. Nothing was paired.")
        if fail:
            raise pairing.PairError(fail)
        return identity.Peer(fp="d" * 64, name="New")
    monkeypatch.setattr(pairing, "pair", fake_pair)


async def pair_event(c, stage, timeout=3.0):
    loop = asyncio.get_running_loop()
    end = loop.time() + timeout
    while True:
        for m in list(c.lines):
            if m.get("event") == "pair" and m["pair"]["stage"] == stage:
                c.lines.remove(m)
                return m["pair"]
        c.lines.append(await c.line(max(0.01, end - loop.time())))
        m = c.lines[-1]
        if m.get("event") != "pair":
            c.lines.pop()


def test_pair_start_streams_words_then_done_after_the_phone_is_in_state(tmp_path, monkeypatch):
    phones = []
    svc, ops, cur = make(tmp_path, phones=phones)
    _fake_pair(monkeypatch)
    real = svc._sync_peers
    seen = []

    async def sync():
        phones.append("New")
        await real()
        seen.append("sync")
    svc._sync_peers = sync

    async def go():
        await svc.start()
        try:
            c = await connect(svc)
            await c.call("subscribe")
            before = len(seen)
            r = await c.call("pair.start", host="10.0.0.9")
            assert r["result"] == {"started": True}
            words = await pair_event(c, "words")
            assert words["words"] == ["apple", "brick", "cloud", "delta"]
            waiting = await pair_event(c, "waiting")
            assert waiting["words"] == ["apple", "brick", "cloud", "delta"] and "Confirm" in waiting["message"]
            done = await pair_event(c, "done")
            assert done["phone"] == "d" * 64
            assert len(seen) > before and any(p["name"] == "New" for p in svc.state()["phones"])
        finally:
            await svc.stop()
    run(go())


def test_pair_start_is_busy_while_one_runs_and_cancel_ends_it(tmp_path, monkeypatch):
    svc, ops, cur = make(tmp_path, phones=())
    hold = threading.Event()
    _fake_pair(monkeypatch, hold=hold)

    async def go():
        await svc.start()
        try:
            c = await connect(svc)
            await c.call("subscribe")
            await c.call("pair.start", host="10.0.0.9")
            await pair_event(c, "words")
            r = await c.call("pair.start", host="10.0.0.9")
            assert r["error"]["code"] == "busy"
            assert (await c.call("pair.cancel"))["result"] == {"cancelled": True}
            hold.set()
            assert (await pair_event(c, "cancelled"))["message"] == "Cancelled. Nothing was paired."
            await until(lambda: svc._pairing is None)
            assert (await c.call("pair.cancel"))["result"] == {"cancelled": False}
        finally:
            hold.set()
            await svc.stop()
    run(go())


def test_pair_start_reports_a_failed_pairing(tmp_path, monkeypatch):
    svc, ops, cur = make(tmp_path, phones=())
    _fake_pair(monkeypatch, fail="Not paired: Reject was tapped on the phone. Nothing was saved on either side.")

    async def go():
        await svc.start()
        try:
            c = await connect(svc)
            await c.call("subscribe")
            await c.call("pair.start", host="10.0.0.9")
            assert "Reject" in (await pair_event(c, "failed"))["message"]
            assert (await c.call("pair.start"))["error"]["code"] == "invalid"
        finally:
            await svc.stop()
    run(go())


def test_cancel_while_waiting_for_the_tap_returns_promptly_over_a_real_connection(tmp_path, monkeypatch):
    from test_pairing import FakePhone
    monkeypatch.setenv("HOMALAWN_CONFIG", str(tmp_path / "cfg" / "config.toml"))
    fake = FakePhone(tmp_path, outcome="hang")
    svc, ops, cur = make(tmp_path, phones=())

    async def go():
        await svc.start()
        try:
            c = await connect(svc)
            await c.call("subscribe")
            await c.call("pair.start", host=f"127.0.0.1:{fake.port}")
            await pair_event(c, "words")
            await pair_event(c, "waiting")
            loop = asyncio.get_running_loop()
            t0 = loop.time()
            assert (await c.call("pair.cancel"))["result"] == {"cancelled": True}
            assert (await c.call("service.info"))["result"]["pid"]     # the loop is not stuck
            # the cancel broke the connection, but the person cancelled: no connection-broke text
            assert (await pair_event(c, "cancelled"))["message"] == "Cancelled. Nothing was paired."
            assert loop.time() - t0 < 1.0
        finally:
            await svc.stop()
    run(go())


def test_service_stop_during_a_pairing_ends_it_and_says_so(tmp_path, monkeypatch):
    from test_pairing import FakePhone
    monkeypatch.setenv("HOMALAWN_CONFIG", str(tmp_path / "cfg" / "config.toml"))
    fake = FakePhone(tmp_path, outcome="hang")
    svc, ops, cur = make(tmp_path, phones=())

    async def go():
        await svc.start()
        c = await connect(svc)
        await c.call("subscribe")
        await c.call("pair.start", host=f"127.0.0.1:{fake.port}")
        await pair_event(c, "waiting")
        loop = asyncio.get_running_loop()
        t0 = loop.time()
        await svc.stop()
        assert loop.time() - t0 < 3.0 and svc._pairing is None
    run(go())


def test_cancel_after_the_pairing_ended_says_nothing_was_cancelled(tmp_path):
    svc, ops, cur = make(tmp_path, phones=())
    svc._pairing = (threading.Event(), [], threading.Event())
    svc._pairing[2].set()

    async def go():
        assert await svc.m_pair_cancel({}, None) == {"cancelled": False}
    run(go())


def test_pair_find_lists_phones_by_host_name(tmp_path, monkeypatch):
    from homalawn.link import discovery
    svc, ops, cur = make(tmp_path, phones=())
    hit = discovery.Hit(name="Pixel", address="10.0.0.9", port=47100, fp_prefix="", hostname="pixel-7")
    monkeypatch.setattr(discovery, "browse", lambda: [hit])
    monkeypatch.setattr(discovery, "missing", lambda: False)

    async def go():
        await svc.start()
        try:
            c = await connect(svc)
            r = await c.call("pair.find")
            assert r["result"] == [{"host": hit.host, "name": "pixel-7"}]
        finally:
            await svc.stop()
    run(go())


def test_pair_find_without_avahi_says_so_instead_of_finding_nothing(tmp_path, monkeypatch):
    from homalawn.link import discovery
    svc, ops, cur = make(tmp_path, phones=())
    monkeypatch.setattr(discovery, "missing", lambda: True)
    monkeypatch.setattr(discovery, "browse", lambda: pytest.fail("browsed without avahi-browse"))

    async def go():
        await svc.start()
        try:
            c = await connect(svc)
            r = await c.call("pair.find")
            assert r["error"]["code"] == "no_avahi"
            assert r["error"]["message"].startswith("This computer cannot look for phones on the network: "
                                                    "avahi-browse is not installed")
        finally:
            await svc.stop()
    run(go())


def _fake_unpair(monkeypatch, phones, answer):
    from homalawn.link import identity, pairing
    told = []
    monkeypatch.setattr(identity, "load_peers", lambda: [identity.Peer(fp=fp_of(n), name=n) for n in phones])

    def fake(peer, **kw):
        told.append(peer.fp)
        phones.remove(next(n for n in phones if fp_of(n) == peer.fp))
        return answer
    monkeypatch.setattr(pairing, "unpair", fake)
    return told


def test_unpair_removes_the_phone_through_the_socket(tmp_path, monkeypatch):
    phones = ["A", "B"]
    svc, ops, cur = make(tmp_path, phones=phones)
    told = _fake_unpair(monkeypatch, phones, "10.0.0.2:47100")

    async def go():
        await svc.start()
        try:
            c = await connect(svc)
            r = await c.call("phone.unpair", phone="A")
            assert r["result"]["told"] == "10.0.0.2:47100" and r["result"]["name"] == "A"
            assert "both" in r["result"]["message"] and fp_of("A") not in r["result"]["message"]
            assert told == [fp_of("A")]
            assert [p["name"] for p in svc.state()["phones"]] == ["B"]
        finally:
            await svc.stop()
    run(go())


def test_unpair_says_when_the_phone_was_not_reachable(tmp_path, monkeypatch):
    phones = ["A"]
    svc, ops, cur = make(tmp_path, phones=phones)
    _fake_unpair(monkeypatch, phones, None)

    async def go():
        await svc.start()
        try:
            c = await connect(svc)
            r = (await c.call("phone.unpair", phone="A"))["result"]
            assert r["told"] is None and "not reachable" in r["message"] and "later" not in r["message"]
            assert svc.state()["phones"] == []
        finally:
            await svc.stop()
    run(go())


def test_unpair_refuses_a_busy_phone(tmp_path, monkeypatch):
    phones = ["A"]
    svc, ops, cur = make(tmp_path, phones=phones)
    told = _fake_unpair(monkeypatch, phones, "x")

    async def go():
        await svc.start()
        try:
            svc.busy[fp_of("A")] = "look"
            c = await connect(svc)
            r = await c.call("phone.unpair", phone="A")
            assert r["error"]["code"] == "busy" and told == []
        finally:
            await svc.stop()
    run(go())


def test_backoff_slows_after_fast_dials(tmp_path):
    svc, ops, cur = make(tmp_path, phones=(), backoff_min=2, backoff_max=60, fast_dials=20, slow_dial=120)
    assert svc.backoff(0) == 2 and svc.backoff(5) == 60 and svc.backoff(19) == 60 and svc.backoff(20) == 120


def test_network_change_dials_an_absent_phone_at_once(tmp_path):
    key = ["a"]
    svc, ops, cur = make(tmp_path, phones=("A",), local_key=lambda: key[0], net_interval=0.05,
                         backoff_min=30, backoff_max=30)
    ops.online["A"] = False

    async def go():
        await svc.start()
        try:
            ph = svc.phones[fp_of("A")]
            await until(lambda: ph.failures > 0)
            await asyncio.sleep(0.2)                       # the key stays: it keeps waiting
            asked = ops.calls.count(("status", "A"))
            key[0] = "b"                                   # this computer joined another network
            await until(lambda: ops.calls.count(("status", "A")) > asked, timeout=1.0)
            await until(lambda: ph.failures == 1)          # zeroed, then this one failed again
        finally:
            await svc.stop()
    run(go())


def test_an_absent_phone_is_logged_once_until_it_returns(tmp_path, caplog):
    svc, ops, cur = make(tmp_path, phones=("A",))
    ops.online["A"] = False

    async def go():
        await svc.start()
        try:
            ph = svc.phones[fp_of("A")]
            await until(lambda: ph.failures >= 3)
            ops.online["A"] = True
            await until(lambda: ph.connected)
        finally:
            await svc.stop()
    with caplog.at_level(logging.INFO, logger="homalawn.daemon"):
        run(go())
    assert caplog.text.count("A: not reachable (") == 1
    assert caplog.text.count("reachable again") == 1


def test_a_command_to_an_absent_phone_dials_it_at_once(tmp_path):
    svc, ops, cur = make(tmp_path, phones=("A",), backoff_min=30, backoff_max=30)
    ops.online["A"] = False

    async def go():
        await svc.start()
        try:
            ph = svc.phones[fp_of("A")]
            await until(lambda: ph.failures > 0)
            asked = ops.calls.count(("status", "A"))
            c = await connect(svc)
            r = await c.call("undo", phone="A")
            assert r["error"]["code"] == "offline"
            await until(lambda: ops.calls.count(("status", "A")) > asked, timeout=1.0)
        finally:
            await svc.stop()
    run(go())


def test_the_network_loop_survives_a_failing_local_key(tmp_path, caplog):
    n = [0]

    def key():
        n[0] += 1
        if n[0] == 2:
            raise RuntimeError("boom")
        return "a"
    svc, ops, cur = make(tmp_path, phones=(), local_key=key, net_interval=0.02)

    async def go():
        await svc.start()
        try:
            await until(lambda: n[0] >= 5)
        finally:
            await svc.stop()
    run(go())
    assert caplog.text.count("cannot read this computer's addresses") == 1


def test_addresses_set_validates_and_saves(tmp_path):
    from homalawn.link import identity
    svc, ops, cur = make(tmp_path, phones=("A",))
    identity.save_peer(identity.Peer(fp=fp_of("A"), name="A", hosts=["10.0.0.5:47100"],
                                     learned=["100.70.1.9"], last_ok="old.example"))

    async def go():
        await svc.start()
        c = await connect(svc)
        await svc._sync_peers()
        r = (await c.call("phone.addresses_set", phone=fp_of("A"), addresses=["Pixel.ts.net", "100.70.1.2", "old.example"]))["result"]
        added = sorted((x for x in r["addresses"] if x["removable"]), key=lambda x: x["saved"])
        assert [x["address"] for x in added] == ["pixel.ts.net", "100.70.1.2", "old.example"]
        assert identity.load_peers()[0].addresses == ["pixel.ts.net", "100.70.1.2", "old.example"]
        got = (await c.call("phone.addresses", phone="A"))["result"]
        kinds = {x["address"]: x["kind"] for x in got["addresses"]}
        assert kinds["100.70.1.9"] == "learned (Tailscale)" and kinds["pixel.ts.net"] == "added"
        assert kinds["10.0.0.5"] == "paired through"
        too_many = [f"h{i}.example" for i in range(6)]
        for bad, text in ((["a/b"], None), (too_many, "A has 5 addresses; remove one first.")):
            e = (await c.call("phone.addresses_set", phone="A", addresses=bad))["error"]
            assert e["code"] == "invalid" and (text is None or e["message"] == text)
        assert identity.load_peers()[0].addresses == ["pixel.ts.net", "100.70.1.2", "old.example"]   # a bad list saved nothing
        await c.call("phone.addresses_set", phone="A", addresses=[])
        assert identity.load_peers()[0].addresses == [] and identity.load_peers()[0].last_ok is None
        await svc.stop()

    run(go())


# -- permissions, app_latest, the app for a new phone, errors as sentences (2026-11-12) -------------
PERMS = {"home": True, "background": False, "notifications": True, "screen_control": False, "install_updates": True}


class StatusOps(FakeOps):
    """status answers like ops.status: the app the phone runs, where it answered and (from a new
    build) its permissions."""

    def __init__(self, cur=None):
        super().__init__(cur)
        self.perms = {}       # name -> permissions dict (absent: an older build)
        self.version = "16.0-dev.abc+homalawn.325"

    def status(self, s):
        self.calls.append(("status", s.name))
        if not self.online.get(s.name, True):
            raise OpError("link failed: phone not reachable", transport=True)
        data = {"via": "link", "phone": s.name, "version": self.version, "package": "app.homalawn",
                "address": "192.168.1.20:47100"}
        if s.name in self.perms:
            data["permissions"] = self.perms[s.name]
        return Result("ok", data=data)


def make_status(tmp_path, latest=None, **kw):
    svc, ops, cur = make(tmp_path, latest=latest or (lambda pkg, v: None), **kw)
    new = StatusOps(cur)
    svc.ops = new
    return svc, new, cur


def test_permissions_are_passed_through_when_the_phone_reports_them(tmp_path):
    svc, ops, _ = make_status(tmp_path)
    ops.perms["A"] = PERMS

    async def go():
        await svc.start()
        try:
            await until(lambda: svc.phones[fp_of("A")].connected and svc.phones[fp_of("B")].connected)
            st = svc.state()
            assert phone(st, "A")["permissions"] == PERMS
            assert phone(st, "B")["permissions"] is None                 # an older build: not reported
            c = await connect(svc)
            await c.call("subscribe")
            ops.perms["A"] = PERMS | {"home": False}                    # the person changed the home app
            st = await c.event(lambda st: phone(st, "A")["permissions"]["home"] is False)
            assert phone(st, "A")["permissions"]["background"] is False
        finally:
            await svc.stop()
    run(go())


def test_ops_permissions_of_keeps_only_the_five_true_false_answers():
    assert real_ops.permissions_of({"permissions": PERMS | {"extra": True}}) == PERMS
    assert real_ops.permissions_of({"permissions": {"home": "yes"}}) == {k: None for k in PERMS} | {"home": None}
    assert real_ops.permissions_of({"default_home": True}) is None


def test_app_latest_is_worked_out_off_the_loop_and_again_when_the_version_changes(tmp_path):
    asked = []

    def latest(pkg, version):
        asked.append((pkg, version, threading.current_thread() is threading.main_thread()))
        return {"version": "16.0-dev.abc+homalawn.330", "newer": version.endswith(".325"), "source": "release"}
    svc, ops, _ = make_status(tmp_path, latest=latest, phones=("A",))

    async def go():
        await svc.start()
        try:
            await until(lambda: phone(svc.state(), "A")["app_latest"] is not None)
            assert phone(svc.state(), "A")["app_latest"] == {
                "version": "16.0-dev.abc+homalawn.330", "newer": True, "source": "release"}
            await asyncio.sleep(0.3)                                  # many checks: asked once
            assert asked == [("app.homalawn", "16.0-dev.abc+homalawn.325", False)]
            ops.version = "16.0-dev.abc+homalawn.330"                 # the phone was updated
            await until(lambda: phone(svc.state(), "A")["app_latest"]["newer"] is False)
            assert len(asked) == 2
        finally:
            await svc.stop()
    run(go())


def test_app_latest_failing_leaves_nothing_offered(tmp_path):
    def latest(pkg, version):
        raise RuntimeError("boom")
    svc, ops, _ = make_status(tmp_path, latest=latest, phones=("A",))

    async def go():
        await svc.start()
        try:
            await until(lambda: svc.phones[fp_of("A")].latest_key is not None)
            assert phone(svc.state(), "A")["app_latest"] is None
        finally:
            await svc.stop()
    run(go())


def test_phone_info_adds_the_reported_permissions(tmp_path):
    svc, ops, _ = make_info(tmp_path)
    plain = ops.phone_info

    def info(s):
        r = plain(s)
        r.data["permissions"] |= PERMS
        return r
    ops.phone_info = info

    async def go():
        await svc.start()
        try:
            await until(lambda: svc.phones[fp_of("A")].connected)
            c = await connect(svc)
            r = (await c.call("phone.info", phone="A"))["result"]
            assert r["permissions"] == {"default_home": True, "screen_control": False, "wallpaper_backup": None} | PERMS
        finally:
            await svc.stop()
    run(go())


def _local_app(apk):
    """What update.new_phone_app answers for a build made on this computer."""
    return {"source": "checkout", "apk": apk, "version": "1+homalawn.9", "url": None, "file": apk.name}


GITHUB_URL = ("https://github.com/g00rek/homalawn/releases/download/build-330/"
              "HomaLawn-16.0-dev.abc1234+homalawn.330-release.apk")


def test_app_serve_of_a_release_gives_the_github_address_and_serves_nothing(tmp_path):
    """A release reaches a new phone straight from GitHub over HTTPS: no server on this computer,
    no time limit; serve_stop takes the code away."""
    from homalawn import update
    app = {"source": "release", "apk": tmp_path / "x.apk", "version": "16.0-dev.abc1234+homalawn.330",
           "url": GITHUB_URL, "file": "HomaLawn-16.0-dev.abc1234+homalawn.330-release.apk"}
    svc, ops, _ = make(tmp_path, phones=(), serve_apk=lambda: app,
                       serve_hosts=lambda: pytest.fail("no address is needed for GitHub"), serve_port=0)

    async def go():
        await svc.start()
        try:
            c = await connect(svc)
            await c.call("subscribe")
            r = (await c.call("app.serve"))["result"]
            assert (r["url"], r["urls"], r["expires_at"]) == (GITHUB_URL, [GITHUB_URL], None)
            assert (r["source"], r["version"], r["file"]) == ("release", app["version"], app["file"])
            assert len(r["qr"]) >= 29
            st = await c.event(lambda st: st["serving"] is not None)
            assert st["serving"] == {"url": GITHUB_URL, "urls": [GITHUB_URL], "expires_at": None,
                                     "version": app["version"], "source": "release",
                                     "message": "The phone downloads Homa Lawn from GitHub."}
            assert update.FROM_GITHUB == "The phone downloads Homa Lawn from GitHub."
            assert (await c.call("app.serve"))["result"] == r
            assert (await c.call("app.serve_stop"))["result"] == {"stopped": True}
            assert svc.state()["serving"] is None
        finally:
            await svc.stop()
    run(go())


def test_app_serve_serves_the_app_with_a_qr_code_until_stopped(tmp_path):
    import urllib.request
    apk = tmp_path / "HomaLawn-1+homalawn.9-release.apk"
    apk.write_bytes(b"the app" * 100)
    svc, ops, _ = make(tmp_path, phones=(), serve_apk=lambda: _local_app(apk),
                       serve_hosts=lambda: ["127.0.0.1"], serve_port=0, serve_seconds=30)

    async def go():
        await svc.start()
        try:
            c = await connect(svc)
            await c.call("subscribe")
            r = (await c.call("app.serve"))["result"]
            assert r["url"].startswith("http://127.0.0.1:") and r["url"].endswith(".apk") and r["urls"] == [r["url"]]
            assert (r["source"], r["version"], r["expires_at"]) == ("checkout", "1+homalawn.9", 1030.0)
            assert len(r["qr"]) == len(r["qr"][0]) >= 29 and {v for row in r["qr"] for v in row} == {0, 1}
            assert r["qr"][0] == [0] * len(r["qr"])                   # the quiet border
            st = await c.event(lambda st: st["serving"] is not None)
            assert st["serving"]["url"] == r["url"] and "qr" not in st["serving"]
            assert (await c.call("app.serve"))["result"] == r         # asked again: the same address
            body = await asyncio.to_thread(lambda: urllib.request.urlopen(r["url"], timeout=5).read())
            assert body == apk.read_bytes()
            st = await c.event(lambda st: (st["serving"] or {}).get("message", "").startswith("Downloaded by"))
            assert (await c.call("app.serve_stop"))["result"] == {"stopped": True}
            assert svc.state()["serving"] is None
            assert (await c.call("app.serve_stop"))["result"] == {"stopped": False}
            with pytest.raises(OSError):
                await asyncio.to_thread(lambda: urllib.request.urlopen(r["url"], timeout=2).read())
        finally:
            await svc.stop()
    run(go())


def test_app_serve_stops_by_itself_after_a_completed_download(tmp_path, monkeypatch):
    import urllib.request
    from homalawn import update
    monkeypatch.setattr(update, "DONE_GRACE_S", 0.3)
    apk = tmp_path / "HomaLawn-1+homalawn.9-release.apk"
    apk.write_bytes(b"the app" * 100)
    svc, ops, _ = make(tmp_path, phones=(), serve_apk=lambda: _local_app(apk),
                       serve_hosts=lambda: ["127.0.0.1"], serve_port=0, serve_seconds=60)

    async def go():
        await svc.start()
        try:
            c = await connect(svc)
            await c.call("subscribe")
            r = (await c.call("app.serve"))["result"]
            await asyncio.to_thread(lambda: urllib.request.urlopen(r["url"], timeout=5).read())
            await until(lambda: svc.state()["serving"] is None)
        finally:
            await svc.stop()
    run(go())


def test_app_serve_errors_are_sentences(tmp_path):
    from homalawn import update
    apk = tmp_path / "a.apk"
    apk.write_bytes(b"x")

    def none():
        raise update.UpdateError("There is no Homa Lawn release on GitHub yet (g00rek/homalawn), so there is "
                                 "nothing to download.")
    svc, ops, _ = make(tmp_path, phones=(), serve_apk=none, serve_hosts=lambda: ["127.0.0.1"], serve_port=0)

    async def go():
        await svc.start()
        try:
            c = await connect(svc)
            e = (await c.call("app.serve"))["error"]
            assert e == {"code": "no_app", "message": "There is no Homa Lawn release on GitHub yet (g00rek/homalawn), "
                                                      "so there is nothing to download."}
            svc._serve_apk, svc._serve_hosts = (lambda: _local_app(apk)), (lambda: [])
            e = (await c.call("app.serve"))["error"]
            assert e["code"] == "no_network" and "no Wi-Fi/LAN or Tailscale address" in e["message"]
        finally:
            await svc.stop()
    run(go())


def test_an_unreachable_phone_is_one_sentence_with_its_address(tmp_path):
    from homalawn.link.client import LinkDown, NotPaired
    svc, ops, _ = make_status(tmp_path, looks={"A": FOLLOW}, phones=("A",))

    def down(msg):
        def boom(*a, **k):
            try:
                raise LinkDown(msg)
            except LinkDown as e:
                raise OpError(f"link failed on A: {e}", transport=True) from e
        return boom

    def untrusted(*a, **k):
        try:
            raise NotPaired("A forgot this computer, so it was removed here. Pair it again with `homalawn pair`.")
        except NotPaired as e:
            raise OpError(f"link failed on A: {e}", transport=True) from e
    working = ops.look_apply
    cases = [(down("A did not answer over the link: 192.168.1.20:47100 (ConnectionRefusedError: "
                   "[Errno 111] Connection refused)"), "A did not answer at 192.168.1.20."),
             (down("the link to A (192.168.1.20:47100) broke during launcher.look_put: "
                   "ConnectionResetError: reset. It may or may not have been carried out."),
              "The connection to A broke during the change; it may or may not have been carried out."),
             (untrusted, "A forgot this computer, so it was removed here. Pair it again with `homalawn pair`.")]

    async def go():
        await svc.start()
        try:
            c = await connect(svc)
            for fn, want in cases:
                await until(lambda: svc.phones[fp_of("A")].connected)
                ops.look_apply = fn
                e = (await c.call("look.apply", phone="A"))["error"]
                ops.look_apply = working
                assert e == {"code": "op_failed", "message": want}
        finally:
            await svc.stop()
    run(go())


def test_sentence_drops_errno_class_names_and_prefixes():
    assert daemon.sentence("link failed: [Errno 113] No route to host") == "No route to host."
    assert daemon.sentence("shot_failed: the screenshot could not be taken") == "The screenshot could not be taken."
    assert daemon.sentence("cannot read x (PermissionError: [Errno 13] Permission denied)") == \
        "Cannot read x (Permission denied)."
    assert daemon.sentence("Error in layout") == "Error in layout."
    assert daemon.plain_error(ValueError()) == daemon.INTERNAL
