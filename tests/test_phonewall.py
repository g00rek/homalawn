"""Wallpapers picked on the phone: the list and thumbnails pushed to its screen, the tap read back,
kept as that theme's pick in look.json, applied like any look, acknowledged, and undone.

The service runs as in test_daemon (real asyncio server, fake sessions and ops); the phone's side
of the three link commands is faked in WallOps. phonewall.push runs against a fake transport."""
import asyncio
import base64
import io
import json
from pathlib import Path

import pytest
from PIL import Image

from homalawn import daemon, lookconf, phonewall
from homalawn.link.client import LinkDown
from homalawn.look import Theme
from homalawn.ops import OpError, Result
from homalawn.transport import ProviderError
from test_daemon import FakeOps, connect, fp_of, make, run, until


def picture(path: Path, color=(200, 40, 40), size=(800, 400)) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", size, color).save(path, "PNG")
    return path


def look(source="follow", home=None, lock=None, **kw) -> lookconf.LookSettings:
    """A look in the current shape. `home`/`lock`: that screen's Screen fields (kind, style,
    layer); without `lock` the lock screen gets the home screen's (two equal screens)."""
    wp = lookconf.wallpaper_default()
    wp["home"].update(home or {})
    if lock is not None:
        wp["lock"].update(lock)
    else:
        wp["lock"] = dict(wp["home"])
    return lookconf.validate({"source": source, "wallpaper": wp, **kw})


ALPHA_COLORS = {"accent": "#7aa2f7", "background": "#1a1b26", "magenta": "#ad8ee6"}


def themes(tmp_path):
    a = [picture(tmp_path / "alpha" / "backgrounds" / f"{i}-a.png", (40 * i, 10, 10)) for i in (1, 2, 3)]
    b = [picture(tmp_path / "beta" / "backgrounds" / f"{i}-b.png", (10, 40 * i, 10)) for i in (1, 2)]
    return [Theme("Alpha", tmp_path / "alpha", dict(ALPHA_COLORS), a, None, slug="alpha"),
            Theme("Beta", tmp_path / "beta", {}, b, None, slug="beta")]


# -- the list -----------------------------------------------------------------------------------
def test_view_follow_lists_the_theme_and_marks_omarchys_background(tmp_path):
    ts = themes(tmp_path)
    v = phonewall.view(lookconf.LookSettings(source="follow"), {"theme": "Alpha", "background": str(ts[0].backgrounds[1])},
                       ts, (1080, 2400))
    assert v.enabled and v.theme == "Alpha" and v.slug == "alpha"
    assert [i.name for i in v.items] == ["1-a", "2-a", "3-a"]
    assert v.current == v.items[1].id
    assert v.size == (240, 533)


def test_view_follow_with_a_pick_marks_the_pick_and_shows_your_own_picture_first(tmp_path):
    ts = themes(tmp_path)
    own = picture(tmp_path / "mine" / "holiday.png")
    st = look(picks={"alpha": {"home": str(ts[0].backgrounds[2]), "by": "phone"}})
    v = phonewall.view(st, {"theme": "Alpha", "background": str(own)}, ts, (1080, 2400))
    assert [i.name for i in v.items] == ["holiday", "1-a", "2-a", "3-a"]
    assert v.current == v.find(v.items[3].id).id
    # another theme followed: its own default, the pick for Alpha is not used there
    v2 = phonewall.view(st, {"theme": "Beta", "background": str(ts[1].backgrounds[0])}, ts, (1080, 2400))
    assert v2.current == v2.items[0].id and v2.theme == "Beta"


def test_view_chosen_off_and_missing_theme(tmp_path):
    ts = themes(tmp_path)
    v = phonewall.view(lookconf.validate({"source": "chosen", "theme": "beta"}), {"theme": "Alpha"}, ts, None)
    assert v.theme == "Beta" and v.current == v.items[0].id
    assert not phonewall.view(lookconf.LookSettings(source="off"), {}, ts, None).enabled
    # both screens kept (the old "wallpaper" sync switch off): the screen is on and says so
    kept = phonewall.view(look(home={"kind": "keep"}), {"theme": "Alpha"}, ts, None)
    assert kept.enabled and kept.screens == {"home": {"kind": "keep"}, "lock": {"kind": "keep"}}
    gone = phonewall.view(lookconf.validate({"source": "chosen", "theme": "Nope"}), {}, ts, None)
    assert gone.enabled and gone.items == [] and "Nope was not found" in gone.message


def test_thumbnail_key_follows_crop_and_layer_but_the_id_does_not(tmp_path):
    ts = themes(tmp_path)
    bg = str(ts[0].backgrounds[0])
    cur = {"theme": "Alpha", "background": bg}
    a = phonewall.view(lookconf.LookSettings(source="follow"), cur, ts, (1080, 2400))
    b = phonewall.view(look(crops={bg: {"home": 0.1}}), cur, ts, (1080, 2400))
    assert a.items[0].id == b.items[0].id and a.items[0].thumb != b.items[0].thumb
    assert a.items[1].thumb == b.items[1].thumb            # only that picture's crop moved
    c = phonewall.view(look(home={"layer": 30}), cur, ts, (1080, 2400))
    assert [x.thumb for x in a.items] == [y.thumb for y in c.items]   # the phone lays the colour layer itself
    assert c.layer == {"home": 30, "lock": 30} and a.fingerprint() != c.fingerprint()   # two equal screens


# -- the link commands ---------------------------------------------------------------------------
class FakeT:
    def __init__(self, have=()):
        self.calls = []
        self.have = set(have)
        self.answers = {}

    def call(self, method, params=None):
        line = json.dumps({"id": 1, "method": method, "params": params})
        assert len(line.encode()) < 1024 * 1024, "over the link's line limit"
        self.calls.append((method, params))
        if method == phonewall.SET:
            self.have |= {t["key"] for t in params["thumbs"]}
            listed = {i[k] for i in params["items"] + params.get("gradients", [])
                      for k in ("thumb", "wide", "lock") if k in i}
            return {"have": sorted(self.have & listed)}
        return self.answers.get(method, {})


def test_push_sends_only_missing_thumbnails_in_batches_under_the_line_limit(tmp_path, monkeypatch):
    ts = themes(tmp_path)
    v = phonewall.view(lookconf.LookSettings(source="follow"), {"theme": "Alpha", "background": str(ts[0].backgrounds[0])},
                       ts, (1080, 2400))
    t = FakeT(have={v.items[0].thumb})
    jpeg_len = len(base64.b64encode(phonewall.thumbnail(v, v.items[1])))
    monkeypatch.setattr(phonewall, "BATCH_BYTES", jpeg_len + 10)   # one thumbnail per message
    # two wallpapers, six gradients; then every wallpaper's wide picture for parallax (a gradient
    # never scrolls, so it has none)
    assert phonewall.push(t, v) == 2 + 6 + 3
    sets = [p for m, p in t.calls if m == phonewall.SET]
    assert [len(p["thumbs"]) for p in sets] == [0] + [1] * 11
    assert all(p["theme"] == "Alpha" and p["current_id"] == v.items[0].id and len(p["items"]) == 3 for p in sets)
    img = Image.open(io.BytesIO(base64.b64decode(sets[1]["thumbs"][0]["jpeg"])))
    assert img.format == "JPEG" and img.size == (240, 533)
    t.calls.clear()
    assert phonewall.push(t, v) == 0 and len(t.calls) == 1      # the phone has them all now


def test_choice_and_ack():
    t = FakeT()
    assert phonewall.choice(t) is None
    t.answers[phonewall.CHOICE] = {"choice": {"id": "abc", "at": 1760000000000}}
    assert phonewall.choice(t) == {"id": "abc", "target": None, "at": 1760000000000, "screens": None}
    t.answers[phonewall.CHOICE] = {"choice": {"target": "lock", "at": 2}}
    assert phonewall.choice(t) == {"id": None, "target": "lock", "at": 2, "screens": None}
    t.answers[phonewall.CHOICE] = {"choice": {"id": "abc", "target": "both", "at": 3}}
    assert phonewall.choice(t) == {"id": "abc", "target": "both", "at": 3, "screens": None}
    for bad in ({"id": 5}, {"id": 5, "at": 1}, {"target": "desk", "at": 1}, {"at": 1}, {"target": "home", "at": True}):
        t.answers[phonewall.CHOICE] = {"choice": bad}
        assert phonewall.choice(t) is None
    phonewall.ack(t, 7, "applied", current_id="abc")
    phonewall.ack(t, 8, "failed", message="no")
    assert t.calls[-2:] == [(phonewall.ACK, {"at": 7, "state": "applied", "current_id": "abc"}),
                            (phonewall.ACK, {"at": 8, "state": "failed", "message": "no"})]
    with pytest.raises(ValueError):
        phonewall.ack(t, 1, "maybe")


# -- the service ---------------------------------------------------------------------------------
class WallOps(FakeOps):
    """FakeOps plus the phone's side of the wallpaper screen; look_apply honours picks like ops."""

    def __init__(self, cur):
        super().__init__(cur)
        self.pushes = {}      # name -> [View]
        self.pending = {}     # name -> the choice the phone holds, until received
        self.acks = []        # (name, at, state, current_id, message)
        self.unsupported = set()
        self.targets = []     # settings.target() of every look applied
        self.parts = []       # the parts of every look applied (None: every screen it shows)
        self.needs_list = set()   # names whose choice reads ask for the whole list again
        self.codes = []           # the code of every ack, in order (None: none)
        self.overwrote = []       # overwrite_wallpaper of every look applied

    def walls_push(self, s, v):
        if s.name in self.unsupported:
            raise ProviderError("unknown method", code="unknown_method")
        self.pushes.setdefault(s.name, []).append(v)
        return len(v.items)

    def walls_choice(self, s):
        return self.pending.get(s.name)

    def walls_poll(self, s):
        return self.walls_choice(s), s.name in self.needs_list

    def walls_ack(self, s, at, state, current_id=None, message=None, code=None):
        self.acks.append((s.name, at, state, current_id, message))
        self.codes.append(code)
        if state in ("applied", "failed") and (self.pending.get(s.name) or {}).get("at") == at:
            del self.pending[s.name]      # the phone drops a choice once it is settled

    def look_apply(self, s, settings, theme=None, background=None, overwrite_wallpaper=False, parts=None, stamp=None):
        self.calls.append(("look_apply", s.name))
        if s.name in self.fail:
            raise self.fail[s.name]
        if s.name in self.unsaved and not overwrite_wallpaper:
            raise OpError("The current wallpaper cannot be saved first, so the look was not applied. Allow "
                          "Homa Lawn photo access on the phone, or apply again to replace it.",
                          code="wallpaper_unsaved")
        self.overwrote.append(overwrite_wallpaper)
        self.targets.append(settings.target())
        self.parts.append(parts)
        name, bg = self.cur()
        pick = settings.pick_for(name if settings.source == "follow" else settings.theme)
        used = pick or (str(bg) if settings.source == "follow" else None)
        self.n = getattr(self, "n", 0) + 1
        return Result("Look applied.", [], data={"snapshot": f"look-{self.n}", "theme": name, "background": used})

    def undo(self, s, no_backup=False):
        self.calls.append(("undo", s.name))
        return Result(f"Restored look-{self.n}", [], data=f"look-{self.n}")

    def last(self, name):
        return self.pushes[name][-1]


def service(tmp_path, look=None, **kw):
    ts = themes(tmp_path)
    looks = {"A": look or {"source": "follow"}}
    kw.setdefault("check_interval", 0.37)   # not in step with the follower's 0.05 s: a tick that finds the phone's lock held skips its poll
    svc, _, cur = make(tmp_path, phones=["A"], looks=looks, list_themes=lambda: ts,
                       applied={"A": {"theme": "Alpha", "background": str(ts[0].backgrounds[0]), "at": 1.0,
                                      "seen": {"theme": "Alpha", "background": str(ts[0].backgrounds[0])}}}, **kw)
    cur.value = ("Alpha", ts[0].backgrounds[0])
    ops = WallOps(cur)
    svc.ops, svc._walls = ops, True
    return svc, ops, cur, ts


def looks_on_disk(svc):
    """The stored looks by phone name (the file itself is keyed by fingerprint)."""
    raw = json.loads(svc.look_path.read_text())
    return {n: raw[fp_of(n)] for n in ("A", "B") if fp_of(n) in raw}


def test_connect_pushes_the_list_and_a_tap_is_kept_applied_and_acknowledged(tmp_path):
    async def go():
        svc, ops, cur, ts = service(tmp_path)
        await svc.start()
        try:
            await until(lambda: ops.pushes.get("A"))
            v = ops.last("A")
            assert v.theme == "Alpha" and v.current == v.items[0].id
            ops.pending["A"] = {"id": v.items[2].id, "at": 111}
            await until(lambda: any(a[2] == "applied" for a in ops.acks))
            states = [a[2] for a in ops.acks if a[1] == 111]
            assert states == ["received", "applied"]
            assert ops.acks[-1][3] == v.items[2].id
            bg = str(ts[0].backgrounds[2])
            assert looks_on_disk(svc)["A"]["picks"] == {"alpha": {"home": bg, "lock": bg, "by": "phone"}}
            assert ops.applied("A") == [("look_apply", "A")]
            await until(lambda: ops.last("A").current == v.items[2].id)
            # the same tap is never taken twice, while the phone still holds it
            ops.pending["A"] = {"id": v.items[2].id, "at": 111}
            await asyncio.sleep(0.3)
            assert len(ops.applied("A")) == 1
        finally:
            await svc.stop()
    run(go())


def test_undo_takes_the_pick_back_and_the_phone_hears_it(tmp_path):
    async def go():
        svc, ops, cur, ts = service(tmp_path)
        await svc.start()
        try:
            await until(lambda: ops.pushes.get("A"))
            v = ops.last("A")
            ops.pending["A"] = {"id": v.items[1].id, "at": 5}
            await until(lambda: ops.last("A").current == v.items[1].id)
            c = await connect(svc)
            r = await c.call("undo", phone="A")
            assert r["result"]["data"] == "look-1"
            assert looks_on_disk(svc)["A"]["picks"] == {}
            await until(lambda: ops.last("A").current == v.items[0].id)   # Omarchy's own again
        finally:
            await svc.stop()
    run(go())


def test_a_choice_on_a_locked_phone_is_applied_at_once(tmp_path):
    async def go():
        svc, ops, cur, ts = service(tmp_path)
        await svc.start()
        try:
            await until(lambda: ops.pushes.get("A"))
            v = ops.last("A")
            ops.locked_["A"] = True
            ops.pending["A"] = {"id": v.items[1].id, "at": 9}
            await until(lambda: any(a[2] == "applied" for a in ops.acks), timeout=5)
            assert not any(a[2] == "waiting" for a in ops.acks)
            assert ops.acks[-1][3] == v.items[1].id and len(ops.applied("A")) == 1
        finally:
            await svc.stop()
    run(go())


def test_a_wallpaper_no_longer_there_fails_with_a_line_for_the_phone(tmp_path):
    async def go():
        svc, ops, cur, ts = service(tmp_path)
        await svc.start()
        try:
            await until(lambda: ops.pushes.get("A"))
            ops.pending["A"] = {"id": "0000000000000000", "at": 3}
            await until(lambda: any(a[2] == "failed" for a in ops.acks))
            assert ops.acks[-1][4] == "That wallpaper is no longer in the theme on the computer."
            assert ops.applied("A") == [] and not looks_on_disk(svc)["A"].get("picks")
        finally:
            await svc.stop()
    run(go())


def test_theme_change_keeps_the_old_pick_and_uses_the_new_themes_default(tmp_path):
    async def go():
        pick = str(themes(tmp_path)[0].backgrounds[2])
        svc, ops, cur, ts = service(tmp_path, look=look(
            picks={"alpha": {"home": pick, "lock": pick, "by": "phone"}}).to_dict())
        await svc.start()
        try:
            await until(lambda: ops.pushes.get("A"))
            assert ops.last("A").current == ops.last("A").items[2].id
            # only Omarchy's background moves within Alpha: the phone keeps its pick, nothing applied
            cur.value = ("Alpha", ts[0].backgrounds[1])
            await asyncio.sleep(0.3)
            assert ops.applied("A") == []
            # another theme: its own default (Omarchy's current background), the Alpha pick is kept
            cur.value = ("Beta", ts[1].backgrounds[1])
            await until(lambda: ops.applied("A"))
            await until(lambda: ops.last("A").theme == "Beta")
            v = ops.last("A")
            assert v.current == v.items[1].id
            assert looks_on_disk(svc)["A"]["picks"]["alpha"]["home"] == str(ts[0].backgrounds[2])
        finally:
            await svc.stop()
    run(go())


def test_sync_off_tells_the_phone_and_stops_asking(tmp_path):
    async def go():
        svc, ops, cur, ts = service(tmp_path)
        await svc.start()
        try:
            await until(lambda: ops.pushes.get("A"))
            c = await connect(svc)
            await c.call("look.set", phone="A", settings={"source": "off"})
            await until(lambda: not ops.last("A").enabled)
            ops.pending["A"] = {"id": "x", "at": 1}
            await asyncio.sleep(0.3)
            assert ops.acks == []
        finally:
            await svc.stop()
    run(go())


def test_an_older_build_without_the_screen_is_left_alone(tmp_path):
    async def go():
        svc, ops, cur, ts = service(tmp_path)
        ops.unsupported.add("A")
        await svc.start()
        try:
            await until(lambda: svc.phones.get(fp_of("A")) and svc.phones[fp_of("A")].walls_unsupported)
            await asyncio.sleep(0.2)
            assert ops.acks == [] and not ops.pushes
        finally:
            await svc.stop()
    run(go())


def test_an_older_build_updated_behind_the_services_back_is_asked_again(tmp_path):
    """The phone was updated with `homalawn update` from the command line: the link never dropped,
    so only the periodic re-ask finds the new build's wallpaper screen."""
    async def go():
        now = [1000.0]
        svc, ops, cur, ts = service(tmp_path, clock=lambda: now[0])
        ops.unsupported.add("A")
        await svc.start()
        try:
            await until(lambda: svc.phones.get(fp_of("A")) and svc.phones[fp_of("A")].walls_unsupported)
            ops.unsupported.discard("A")             # the new build is on, the link stayed up
            now[0] += daemon.WALLS_REPROBE - 1
            await asyncio.sleep(0.3)
            assert not ops.pushes and svc.phones[fp_of("A")].walls_unsupported
            now[0] += 1
            await until(lambda: ops.pushes.get("A"))
            assert not svc.phones[fp_of("A")].walls_unsupported
        finally:
            await svc.stop()
    run(go())


def test_an_app_update_through_the_service_asks_the_new_build_at_once(tmp_path):
    async def go():
        svc, ops, cur, ts = service(tmp_path)
        ops.unsupported.add("A")
        await svc.start()
        try:
            await until(lambda: svc.phones.get(fp_of("A")) and svc.phones[fp_of("A")].walls_unsupported)
            ops.unsupported.discard("A")
            c = await connect(svc)
            await c.call("app.update", phone="A")
            await until(lambda: ops.pushes.get("A"))
        finally:
            await svc.stop()
    run(go())


def test_a_home_screen_restart_between_checks_gets_the_whole_list_again(tmp_path):
    """Seen on a real phone: the list went out, then an app update restarted the phone's home screen
    between two connection checks that both found it up. The next tap read broke on the dead link;
    the new process showed its own stale list until the service restarted. A broken read must make
    the next check send everything again."""
    async def go():
        svc, ops, cur, ts = service(tmp_path)
        await svc.start()
        try:
            await until(lambda: ops.pushes.get("A"))
            n = len(ops.pushes["A"])
            real = ops.walls_choice
            broke = []

            def refused(s):
                if not broke:
                    broke.append(1)
                    raise LinkDown("the link to A broke during launcher.theme_wallpaper_choice: "
                                   "[Errno 111] Connection refused")
                return real(s)
            ops.walls_choice = refused
            await until(lambda: broke)
            await until(lambda: len(ops.pushes["A"]) > n)
            assert svc.phones[fp_of("A")].connected
            assert ops.last("A").fingerprint() == ops.pushes["A"][n - 1].fingerprint()
        finally:
            await svc.stop()
    run(go())


def test_another_build_reported_by_the_connection_check_gets_the_whole_list_again(tmp_path):
    async def go():
        svc, ops, cur, ts = service(tmp_path)
        version = ["1.0"]
        status = ops.status

        def versioned(s):
            r = status(s)
            r.data["version"] = version[0]
            return r
        ops.status = versioned
        await svc.start()
        try:
            await until(lambda: ops.pushes.get("A"))
            await until(lambda: svc.phones[fp_of("A")].app_version == "1.0")
            await asyncio.sleep(0.3)
            n = len(ops.pushes["A"])
            version[0] = "1.1"
            await until(lambda: len(ops.pushes["A"]) > n)
        finally:
            await svc.stop()
    run(go())


# -- where the wallpaper goes (home, lock or both), chosen on the phone --------------------------
def test_the_list_carries_the_looks_target(tmp_path):
    ts = themes(tmp_path)
    om = {"theme": "Alpha", "background": str(ts[0].backgrounds[0])}
    v = phonewall.view(look(home={"kind": "keep"}, lock={"kind": "wallpaper"}), om, ts, (1080, 2400))
    assert v.target == "lock"
    assert phonewall.view(look("off", home={"kind": "wallpaper"}, lock={"kind": "keep"}), om, ts, None).target == "home"
    assert phonewall.view(look(home={"kind": "keep"}), om, ts, None).target == "both"   # nothing changed: as before
    w = phonewall.view(look(home={"kind": "wallpaper"}, lock={"kind": "keep"}), om, ts, (1080, 2400))
    assert v.fingerprint() != w.fingerprint()
    t = FakeT()
    phonewall.push(t, v)
    assert all(p["target"] == "lock" for m, p in t.calls if m == phonewall.SET)


def test_a_build_from_before_the_target_choice_gets_the_list_without_it(tmp_path):
    ts = themes(tmp_path)
    v = phonewall.view(lookconf.LookSettings(source="follow"), {"theme": "Alpha", "background": str(ts[0].backgrounds[0])},
                       ts, (1080, 2400))

    class Older(FakeT):
        def call(self, method, params=None):
            if method == phonewall.SET and "target" in params:
                raise ProviderError("bad_params: unknown params for launcher.theme_wallpapers_set: target")
            return super().call(method, params)

    t = Older()
    assert phonewall.push(t, v) == 3
    assert [m for m, _ in t.calls] == [phonewall.SET] * 2 and "target" not in t.calls[-1][1]




# -- one choice per screen: keep, a wallpaper or a gradient ---------------------------------------
def scr(home, lock):
    def one(x):
        if x == "keep":
            return {"kind": "keep"}
        kind, i = x
        return {"kind": kind, "id": i}
    return {"home": one(home), "lock": one(lock)}


def test_the_list_offers_gradients_and_says_what_each_screen_shows(tmp_path):
    ts = themes(tmp_path)
    om = {"theme": "Alpha", "background": str(ts[0].backgrounds[0])}
    v = phonewall.view(lookconf.LookSettings(source="follow"), om, ts, (1080, 2400))
    assert [g.name for g in v.gradients] == ["Fade", "Diagonal", "Glow", "Duo", "Mesh", "Vignette"]
    assert v.screens == scr(("wallpaper", v.items[0].id), ("wallpaper", v.items[0].id))
    st = look(home={"kind": "keep", "layer": 10}, lock={"kind": "gradient", "style": "glow", "layer": 60})
    w = phonewall.view(st, om, ts, (1080, 2400))
    assert w.screens == scr("keep", ("gradient", w.gradients[2].id)) and w.layer == {"home": 10, "lock": 60}
    jpeg = phonewall.thumbnail(w, w.gradients[2])
    assert Image.open(io.BytesIO(jpeg)).size == (240, 533)
    # a theme without colours offers no gradients
    b = phonewall.view(lookconf.LookSettings(source="follow"), {"theme": "Beta"}, ts, (1080, 2400))
    assert b.gradients == []
    t = FakeT()
    assert phonewall.push(t, w) == 3 * 2 + 6         # each wallpaper also wide (parallax); gradients never
    first = t.calls[0][1]
    assert first["screens"] == w.screens and first["layer"] == {"home": 10, "lock": 60}
    assert [g["name"] for g in first["gradients"]] == ["Fade", "Diagonal", "Glow", "Duo", "Mesh", "Vignette"]


def test_a_build_from_before_per_screen_choices_gets_the_older_list(tmp_path):
    ts = themes(tmp_path)
    v = phonewall.view(lookconf.LookSettings(source="follow"), {"theme": "Alpha", "background": str(ts[0].backgrounds[0])},
                       ts, (1080, 2400))

    class Older(FakeT):
        def call(self, method, params=None):
            extra = sorted(k for k in ("gradients", "layer", "screens") if k in (params or {}))
            if method == phonewall.SET and extra:
                raise ProviderError("bad_params: unknown params for launcher.theme_wallpapers_set: " + ", ".join(extra))
            return super().call(method, params)

    t = Older()
    assert phonewall.push(t, v) == 3                     # the wallpapers only, no gradient thumbnails
    assert all("target" in p and "gradients" not in p for m, p in t.calls if m == phonewall.SET)


def test_choice_per_screen_is_read_and_checked():
    t = FakeT()
    t.answers[phonewall.CHOICE] = {"choice": {"at": 4, "screens": {"home": {"kind": "keep"},
                                                                   "lock": {"kind": "gradient", "id": "g1"}}}}
    assert phonewall.choice(t) == {"at": 4, "screens": scr("keep", ("gradient", "g1")), "id": None, "target": None}
    for bad in ({"home": {"kind": "keep"}}, {"home": {"kind": "keep"}, "lock": {"kind": "wallpaper"}},
                {"home": {"kind": "photo"}, "lock": {"kind": "keep"}}):
        t.answers[phonewall.CHOICE] = {"choice": {"at": 5, "screens": bad}}
        assert phonewall.choice(t) is None


def test_lock_only_gradient_is_one_apply_of_the_lock_screen_and_undo_takes_it_back(tmp_path):
    async def go():
        svc, ops, cur, ts = service(tmp_path)
        await svc.start()
        try:
            await until(lambda: ops.pushes.get("A"))
            v = ops.last("A")
            home = ("wallpaper", v.items[0].id)
            ops.pending["A"] = {"at": 61, "screens": scr(home, ("gradient", v.gradients[1].id)), "id": None, "target": None}
            await until(lambda: [a[2] for a in ops.acks if a[1] == 61] == ["received", "applied"])
            assert ops.applied("A") == [("look_apply", "A")] and ops.parts == [("lock",)]
            d = looks_on_disk(svc)["A"]
            assert d["wallpaper"] == {"parallax": False,
                                      "home": {"kind": "wallpaper", "style": None, "layer": 0},
                                      "lock": {"kind": "gradient", "style": "diagonal", "layer": 0}}
            assert lookconf.validate(d).target() == "both" and not d["picks"]
            await until(lambda: ops.last("A").screens["lock"] == {"kind": "gradient", "id": v.gradients[1].id})
            c = await connect(svc)
            r = await c.call("undo", phone="A")
            assert r["result"]["data"] == "look-1"
            assert looks_on_disk(svc)["A"]["wallpaper"] == lookconf.wallpaper_default()
            await until(lambda: ops.last("A").screens == v.screens)
        finally:
            await svc.stop()
    run(go())


def test_both_screens_to_one_wallpaper_and_home_alone_keeps_the_lock_screens_picture(tmp_path):
    async def go():
        svc, ops, cur, ts = service(tmp_path)
        await svc.start()
        try:
            await until(lambda: ops.pushes.get("A"))
            v = ops.last("A")
            pic = ("wallpaper", v.items[2].id)
            ops.pending["A"] = {"at": 71, "screens": scr(pic, pic), "id": None, "target": None}
            await until(lambda: any(a[1] == 71 and a[2] == "applied" for a in ops.acks))
            assert ops.parts == [("home", "lock")] and ops.acks[-1][3] == v.items[2].id
            d = looks_on_disk(svc)["A"]
            bg = str(ts[0].backgrounds[2])
            assert d["picks"] == {"alpha": {"home": bg, "lock": bg, "by": "phone"}}
            assert d["wallpaper"]["home"] == d["wallpaper"]["lock"]     # two equal screens
            await until(lambda: ops.last("A").screens == scr(pic, pic))
            # now the home screen alone: the lock screen keeps the picture it shows
            ops.pending["A"] = {"at": 72, "screens": scr(("wallpaper", v.items[1].id), pic), "id": None, "target": None}
            await until(lambda: any(a[1] == 72 and a[2] == "applied" for a in ops.acks))
            assert ops.parts[-1] == ("home",)
            d = looks_on_disk(svc)["A"]
            assert d["picks"]["alpha"] == {"home": str(ts[0].backgrounds[1]), "lock": str(ts[0].backgrounds[2]),
                                           "by": "phone"}
            await until(lambda: ops.last("A").screens == scr(("wallpaper", v.items[1].id), pic))
        finally:
            await svc.stop()
    run(go())


def test_keep_only_saves_and_applies_nothing_and_nothing_changed_is_just_acknowledged(tmp_path):
    async def go():
        svc, ops, cur, ts = service(tmp_path)
        await svc.start()
        try:
            await until(lambda: ops.pushes.get("A"))
            v = ops.last("A")
            ops.pending["A"] = {"at": 81, "screens": v.screens, "id": None, "target": None}
            await until(lambda: [a[2] for a in ops.acks if a[1] == 81] == ["received", "applied"])
            assert ops.applied("A") == [] and looks_on_disk(svc)["A"]["wallpaper"] == lookconf.wallpaper_default()
            ops.pending["A"] = {"at": 82, "screens": scr(v.screens["home"] and ("wallpaper", v.items[0].id), "keep"),
                                "id": None, "target": None}
            await until(lambda: [a[2] for a in ops.acks if a[1] == 82] == ["received", "applied"])
            assert ops.applied("A") == []
            d = looks_on_disk(svc)["A"]
            assert d["wallpaper"]["lock"]["kind"] == "keep" and lookconf.validate(d).target() == "home"
        finally:
            await svc.stop()
    run(go())


def test_a_per_screen_choice_on_a_locked_phone_applies_only_its_screens_at_once(tmp_path):
    async def go():
        svc, ops, cur, ts = service(tmp_path)
        await svc.start()
        try:
            await until(lambda: ops.pushes.get("A"))
            v = ops.last("A")
            ops.locked_["A"] = True
            ops.pending["A"] = {"at": 91, "screens": scr(("wallpaper", v.items[0].id), ("gradient", v.gradients[4].id)),
                                "id": None, "target": None}
            await until(lambda: any(a[1] == 91 and a[2] == "applied" for a in ops.acks), timeout=5)
            assert not any(a[2] == "waiting" for a in ops.acks)
            assert ops.parts == [("lock",)]
        finally:
            await svc.stop()
    run(go())


def test_a_gradient_no_longer_offered_fails_with_a_line_for_the_phone(tmp_path):
    async def go():
        svc, ops, cur, ts = service(tmp_path)
        await svc.start()
        try:
            await until(lambda: ops.pushes.get("A"))
            ops.pending["A"] = {"at": 99, "screens": scr("keep", ("gradient", "0000000000000000")), "id": None, "target": None}
            await until(lambda: any(a[2] == "failed" for a in ops.acks))
            assert ops.acks[-1][4] == daemon.MSG_GRADIENT_GONE and ops.applied("A") == []
        finally:
            await svc.stop()
    run(go())


def test_an_older_builds_target_choice_becomes_one_choice_per_screen(tmp_path):
    async def go():
        svc, ops, cur, ts = service(tmp_path, look=look(home={"kind": "keep"}, lock={"kind": "wallpaper"}).to_dict())
        await svc.start()
        try:
            await until(lambda: ops.pushes.get("A"))
            v = ops.last("A")
            ops.pending["A"] = {"id": v.items[1].id, "target": "both", "at": 31, "screens": None}
            await until(lambda: any(a[2] == "applied" for a in ops.acks))
            assert ops.parts == [("home", "lock")] and ops.targets == ["both"]
            d = looks_on_disk(svc)["A"]
            assert lookconf.validate(d).target() == "both" and d["picks"]["alpha"]["home"] == str(ts[0].backgrounds[1])
        finally:
            await svc.stop()
    run(go())


def test_a_look_set_in_the_window_shows_its_screens_on_the_phone(tmp_path):
    async def go():
        svc, ops, cur, ts = service(tmp_path)
        await svc.start()
        try:
            await until(lambda: ops.pushes.get("A"))
            c = await connect(svc)
            draft = look(home={"kind": "gradient", "style": "mesh", "layer": 20}, lock={"kind": "keep"}).to_dict()
            r = await c.call("look.set", phone="A", settings=draft)
            assert r["result"]["look"]["wallpaper"] == draft["wallpaper"]
            await until(lambda: ops.last("A").screens["home"]["kind"] == "gradient")
            v = ops.last("A")
            assert v.screens == scr(("gradient", v.gradients[4].id), "keep")
            assert v.target == "home" and v.layer == {"home": 20, "lock": 0}
        finally:
            await svc.stop()
    run(go())


def test_the_list_from_a_separate_look_says_each_screens_own_picture(tmp_path):
    ts = themes(tmp_path)
    om = {"theme": "Alpha", "background": str(ts[0].backgrounds[0])}
    st = look(home={"kind": "gradient", "style": "glow", "layer": -30}, lock={"kind": "wallpaper", "layer": 50},
              picks={"alpha": {"home": None, "lock": str(ts[0].backgrounds[2]), "by": "computer"}})
    v = phonewall.view(st, om, ts, (1080, 2400))
    g = next(i for i in v.gradients if i.style == "glow")
    lock = next(i for i in v.items if i.path == str(ts[0].backgrounds[2]))
    assert v.screens == {"home": {"kind": "gradient", "id": g.id}, "lock": {"kind": "wallpaper", "id": lock.id}}
    assert v.target == "both" and v.layer == {"home": -30, "lock": 50}


def test_one_screen_changed_on_two_equal_screens_leaves_the_other_as_it_was(tmp_path):
    async def go():
        glow_look = look(home={"kind": "gradient", "style": "glow", "layer": 15}).to_dict()
        svc, ops, cur, ts = service(tmp_path, look=glow_look)
        await svc.start()
        try:
            await until(lambda: ops.pushes.get("A"))
            v = ops.last("A")
            glow = ("gradient", v.gradients[2].id)
            assert v.screens == scr(glow, glow)
            ops.pending["A"] = {"at": 41, "screens": scr(("wallpaper", v.items[1].id), glow),
                                "id": None, "target": None}
            await until(lambda: any(a[1] == 41 and a[2] == "applied" for a in ops.acks))
            assert ops.parts == [("home",)]
            d = looks_on_disk(svc)["A"]
            assert d["wallpaper"] == {"parallax": False,
                                      "home": {"kind": "wallpaper", "style": None, "layer": 15},
                                      "lock": {"kind": "gradient", "style": "glow", "layer": 15}}
            assert d["picks"]["alpha"]["home"] == str(ts[0].backgrounds[1])
            await until(lambda: ops.last("A").screens == scr(("wallpaper", v.items[1].id), glow))
            # the lock screen to the same picture: two equal screens again
            ops.pending["A"] = {"at": 42, "screens": scr(("wallpaper", v.items[1].id), ("wallpaper", v.items[1].id)),
                                "id": None, "target": None}
            await until(lambda: any(a[1] == 42 and a[2] == "applied" for a in ops.acks))
            d = looks_on_disk(svc)["A"]
            assert d["wallpaper"]["lock"]["kind"] == d["wallpaper"]["home"]["kind"] == "wallpaper"
            bg = str(ts[0].backgrounds[1])
            assert d["picks"]["alpha"] == {"home": bg, "lock": bg, "by": "phone"}
        finally:
            await svc.stop()
    run(go())


# -- a build swapped under the service (same version, another APK) -------------------------------
def test_poll_reads_needs_list_and_older_builds_never_send_it():
    t = FakeT()
    assert phonewall.poll(t) == (None, False)
    t.answers[phonewall.CHOICE] = {"choice": None, "needs_list": True}
    assert phonewall.poll(t) == (None, True)
    t.answers[phonewall.CHOICE] = {"choice": {"at": 4, "screens": {"home": {"kind": "keep"}, "lock": {"kind": "keep"}}},
                                   "needs_list": "yes"}
    assert phonewall.poll(t) == ({"at": 4, "screens": scr("keep", "keep"), "id": None, "target": None}, False)


class SwappedPhone(FakeT):
    """One phone whose Homa Lawn is first a build from before per-screen choices (it refuses the
    new list fields), then our newer build with the same versionName but another APK."""

    def __init__(self):
        super().__init__()
        self.old = True

    def call(self, method, params=None):
        extra = sorted(k for k in phonewall.NEW_FIELDS if k in (params or {}))
        if method == phonewall.SET and self.old and extra:
            raise ProviderError("bad_params: unknown params for launcher.theme_wallpapers_set: " + ", ".join(extra))
        return super().call(method, params)

    def lists(self):
        return [p for m, p in self.calls if m == phonewall.SET]


def test_a_build_swapped_with_the_same_version_gets_the_full_list(tmp_path):
    """Seen on a real phone: the list went to the old build in its older format, then an update
    (pending until the unlock) restarted the home screen, with no transport error seen and the same
    versionName/versionCode. Only the build key (install time or APK sha256) changed; the new build
    must get the whole list in the new format."""
    async def go():
        svc, ops, cur, ts = service(tmp_path)
        phone = SwappedPhone()
        build = ["sha256:" + "a" * 64]
        ops.walls_push = lambda s, v: phonewall.push(phone, v)
        status = ops.status

        def versioned(s):
            r = status(s)
            r.data |= {"version": "15.Dev.(505dbc4)", "build": build[0]}
            return r
        ops.status = versioned
        await svc.start()
        try:
            await until(lambda: phone.lists() and svc.phones[fp_of("A")].app_build == build[0])
            assert "screens" not in phone.lists()[-1]           # the older build got the older list
            await asyncio.sleep(0.3)
            n = len(phone.lists())
            assert n and all("screens" not in p for p in phone.lists())
            phone.old = False
            build[0] = "installed:1760000000000"                # the new build reports its install time
            await until(lambda: len(phone.lists()) > n)
            assert "screens" in phone.lists()[-1] and "gradients" in phone.lists()[-1]
            assert svc.phones[fp_of("A")].app_version == "15.Dev.(505dbc4)"
        finally:
            await svc.stop()
    run(go())


def test_the_phone_asking_for_the_list_gets_it_at_once_and_not_on_every_read(tmp_path):
    async def go():
        now = [1000.0]
        # connection checks seldom, so they do not keep the phone busy for the 2 s choice reads
        svc, ops, cur, ts = service(tmp_path, clock=lambda: now[0], check_interval=5)
        await svc.start()
        try:
            await until(lambda: ops.pushes.get("A"))
            await asyncio.sleep(0.3)
            n = len(ops.pushes["A"])
            ops.needs_list.add("A")                   # its home screen restarted on another build
            await until(lambda: len(ops.pushes["A"]) == n + 1)
            await asyncio.sleep(0.5)                  # it still asks (say its list never arrived)
            assert len(ops.pushes["A"]) == n + 1      # answered at most every WALLS_RELIST
            now[0] += daemon.WALLS_RELIST
            await until(lambda: len(ops.pushes["A"]) == n + 2)
            ops.needs_list.discard("A")
            now[0] += daemon.WALLS_RELIST
            await asyncio.sleep(0.5)
            assert len(ops.pushes["A"]) == n + 2
        finally:
            await svc.stop()
    run(go())


# -- a wallpaper that cannot be saved first: the phone asks, Replace overwrites ---------------------
def test_ack_with_a_code_and_an_older_build_gets_the_message_alone():
    t = FakeT()
    phonewall.ack(t, 7, "failed", message="no", code="wallpaper_unsaved")
    assert t.calls == [(phonewall.ACK, {"at": 7, "state": "failed", "message": "no", "code": "wallpaper_unsaved"})]

    class Older(FakeT):
        def call(self, method, params=None):
            if method == phonewall.ACK and "code" in params:
                raise ProviderError("bad_params: unknown params for launcher.theme_wallpaper_ack: code",
                                    code="bad_params")
            return super().call(method, params)
    o = Older()
    phonewall.ack(o, 7, "failed", message="no", code="wallpaper_unsaved")
    assert o.calls == [(phonewall.ACK, {"at": 7, "state": "failed", "message": "no"})]


def test_choice_reads_the_overwrite_of_replace():
    t = FakeT()
    keep = {"home": {"kind": "keep"}, "lock": {"kind": "keep"}}
    t.answers[phonewall.CHOICE] = {"choice": {"at": 4, "screens": keep, "overwrite": True}}
    assert phonewall.choice(t)["overwrite"] is True
    t.answers[phonewall.CHOICE] = {"choice": {"at": 4, "screens": keep, "overwrite": "yes"}}
    assert "overwrite" not in phonewall.choice(t)


def test_an_unsaved_wallpaper_asks_on_the_phone_and_replace_overwrites_it(tmp_path):
    """The current wallpaper cannot be backed up: the phone gets no long message but the code for
    its overwrite question; nothing is kept on the computer (the window asks nothing). Replace sends
    the same choice again with overwrite, which applies without the backup."""
    async def go():
        svc, ops, cur, ts = service(tmp_path)
        ops.unsaved.add("A")
        await svc.start()
        try:
            await until(lambda: ops.pushes.get("A"))
            v = ops.last("A")
            before = looks_on_disk(svc)["A"]
            want = scr(("wallpaper", v.items[2].id), ("wallpaper", v.items[2].id))
            ops.pending["A"] = {"at": 50, "screens": want, "id": None, "target": None}
            await until(lambda: any(a[1] == 50 and a[2] == "failed" for a in ops.acks))
            i = next(k for k, a in enumerate(ops.acks) if a[1] == 50 and a[2] == "failed")
            assert ops.codes[i] == daemon.WALLPAPER_UNSAVED and ops.acks[i][4] == daemon.MSG_PICK_UNSAVED
            assert looks_on_disk(svc)["A"] == before
            ph = svc.phones[fp_of("A")]
            assert ph.error is None and ph.error_code is None and not ph.pending
            assert ops.overwrote == []
            ops.pending["A"] = {"at": 51, "screens": want, "id": None, "target": None, "overwrite": True}
            await until(lambda: any(a[1] == 51 and a[2] == "applied" for a in ops.acks))
            assert ops.overwrote == [True]
            assert looks_on_disk(svc)["A"]["picks"]["alpha"]["home"] == str(ts[0].backgrounds[2])
        finally:
            await svc.stop()
    run(go())


# -- a choice from the phone written into the look: the other screens and themes stay as they were --
def _choice(st, ts, want, picked, changed):
    v = phonewall.View(enabled=True, theme="Alpha", slug="alpha", default=str(ts[0].backgrounds[0]))
    return lookconf.validate(daemon.Service._choice_into(st, v, want, picked, changed))


def _wall(path):
    return phonewall.Item(id="w", name=Path(path).stem, path=str(path), thumb="t")


def test_another_themes_own_lock_pick_and_a_lock_crop_stay(tmp_path):
    ts = themes(tmp_path)
    a1, b1, b2 = (str(p) for p in (ts[0].backgrounds[1], *ts[1].backgrounds))
    st = look(lock={}, picks={"beta": {"home": b1, "lock": b2, "by": "phone"}},
              crops={a1: {"home": 0.2, "lock": 0.7}})
    want = {p: {"kind": "wallpaper"} for p in lookconf.SCREENS}
    s = _choice(st, ts, want, {"home": _wall(a1), "lock": _wall(a1)}, ["home", "lock"])
    assert "same" not in s.wallpaper
    assert s.picks["beta"] == {"home": b1, "lock": b2, "by": "phone"}
    assert s.picks["alpha"] == {"home": a1, "lock": a1, "by": "phone"}
    assert s.crops[a1] == {"home": 0.2, "lock": 0.7}


def test_one_screen_changed_keeps_the_other_screens_picks(tmp_path):
    ts = themes(tmp_path)
    a1, b1 = str(ts[0].backgrounds[1]), str(ts[1].backgrounds[0])
    st = look(picks={"alpha": {"home": a1, "lock": a1, "by": "computer"}, "beta": {"home": b1, "lock": b1, "by": "phone"}})
    glow = phonewall.Item(id="g", name="glow", path="", thumb="t", style="glow")
    s = _choice(st, ts, {"home": {"kind": "gradient"}}, {"home": glow}, ["home"])
    assert s.wallpaper["home"]["kind"] == "gradient" and s.wallpaper["lock"]["kind"] == "wallpaper"
    assert s.picks["alpha"] == {"home": a1, "lock": a1, "by": "computer"}
    assert s.picks["beta"] == {"home": b1, "lock": b1, "by": "phone"}


# -- format 3: the whole look model on the phone (two screens, parallax, layers) ------------------
def test_the_list_carries_the_model_and_the_wide_and_lock_pictures(tmp_path):
    ts = themes(tmp_path)
    a0, a1 = str(ts[0].backgrounds[0]), str(ts[0].backgrounds[1])
    Image.linear_gradient("L").rotate(90).resize((800, 400)).convert("RGB").save(a1)   # a crop shows
    om = {"theme": "Alpha", "background": a0}
    st = look(lock={"kind": "wallpaper", "layer": 25}, crops={a1: {"home": 0.1, "lock": 0.9}})
    st.wallpaper["parallax"] = True
    v = phonewall.view(st, om, ts, (1080, 2400))
    assert v.parallax is True
    one = v.find(next(i.id for i in v.items if i.path == a1))
    zero = next(i for i in v.items if i.path == a0)
    assert one.lock and one.lock != one.thumb and zero.lock is None     # only where the lock crop differs
    assert all(i.wide and i.wide != i.thumb for i in v.items)
    assert all(i.wide is None for i in v.gradients)                    # a gradient never scrolls
    size = lambda b: Image.open(io.BytesIO(b)).size
    assert size(phonewall.thumbnail(v, one, "wide")) == (480, 533)
    assert phonewall.thumbnail(v, one, "lock") != phonewall.thumbnail(v, one)
    # the lock screen's crop as the home one (lock None): no lock picture
    eq = phonewall.view(look(crops={a1: {"home": 0.1}}), om, ts, (1080, 2400))
    assert all(i.lock is None for i in eq.items)
    assert eq.fingerprint() != phonewall.view(look(), om, ts, (1080, 2400)).fingerprint()
    t = FakeT()
    n = phonewall.push(t, v)
    assert n == 3 * 2 + 6 + 1
    first = t.calls[0][1]
    assert first["format"] == phonewall.FORMAT and first["wallpaper"] == {"same": False, "parallax": True}
    row = next(r for r in first["items"] if r["id"] == one.id)
    assert row == {"id": one.id, "name": one.name, "thumb": one.thumb, "wide": one.wide, "lock": one.lock}
    sent = {x["key"] for _, p in t.calls[1:] for x in p["thumbs"]}
    assert one.lock in sent and all(i.wide in sent for i in v.items)


def test_parallax_is_sent_on_only_while_the_home_screen_shows_a_theme_wallpaper(tmp_path):
    ts = themes(tmp_path)
    om = {"theme": "Alpha", "background": str(ts[0].backgrounds[0])}
    for home, on in (({"kind": "wallpaper"}, True), ({"kind": "gradient", "style": "glow"}, False),
                     ({"kind": "keep"}, False)):
        st = look(home=home, lock={})
        st.wallpaper["parallax"] = True
        v = phonewall.view(st, om, ts, (1080, 2400))
        assert v.parallax is on, home
        t = FakeT()
        phonewall.push(t, v)
        assert t.calls[0][1]["wallpaper"] == {"same": False, "parallax": on}


class StrictPhone(FakeT):
    """A build that refuses every list field it does not know, as LinkServer's strict params do."""

    def __init__(self, known):
        super().__init__()
        self.known = set(known)

    def call(self, method, params=None):
        extra = sorted(k for k in (params or {}) if k not in self.known)
        if method == phonewall.SET and extra:
            raise ProviderError("bad_params: unknown params for launcher.theme_wallpapers_set: " + ", ".join(extra))
        return super().call(method, params)


V2 = {"enabled", "theme", "current_id", "message", "target", "items", "gradients", "screens", "layer", "thumbs"}


def test_a_build_from_before_format_3_gets_the_per_screen_list_without_wide_pictures(tmp_path):
    ts = themes(tmp_path)
    v = phonewall.view(lookconf.LookSettings(source="follow"), {"theme": "Alpha", "background": str(ts[0].backgrounds[0])},
                       ts, (1080, 2400))
    t = StrictPhone(V2)
    assert phonewall.push(t, v) == 3 + 6                 # no wide thumbnails
    sets = [p for m, p in t.calls if m == phonewall.SET]
    assert all("format" not in p and "wallpaper" not in p and "screens" in p for p in sets)
    assert all(set(r) == {"id", "name", "thumb"} for p in sets for r in p["items"] + p["gradients"])
    # older still: from before per-screen choices, and from before the target
    t = StrictPhone(V2 - {"gradients", "screens", "layer"})
    assert phonewall.push(t, v) == 3 and "target" in t.calls[-1][1]
    t = StrictPhone(V2 - {"gradients", "screens", "layer", "target"})
    assert phonewall.push(t, v) == 3 and "target" not in t.calls[-1][1]


def test_a_refusal_naming_no_list_field_is_not_taken_for_an_older_build(tmp_path):
    """The method's own name holds "wallpaper": only the refused names count."""
    ts = themes(tmp_path)
    v = phonewall.view(lookconf.LookSettings(source="follow"), {"theme": "Alpha"}, ts, (1080, 2400))

    class Broken(FakeT):
        def call(self, method, params=None):
            raise ProviderError("bad_params: bad_params: thumbnail key")
    with pytest.raises(ProviderError):
        phonewall.push(Broken(), v)
    assert phonewall._refused_names(ProviderError("bad_params: unknown params for launcher.theme_wallpapers_set: "
                                                  "format, wallpaper")) == {"format", "wallpaper"}


def model_choice(at, screens, same=False, parallax=False, layer=(0, 0), **kw):
    """A format-3 choice; `same` true is an older phone build's "Same on both"."""
    return {"at": at, "screens": screens, "id": None, "target": None, "format": phonewall.FORMAT,
            "wallpaper": {"same": same, "parallax": parallax}, "layer": {"home": layer[0], "lock": layer[1]}, **kw}


def test_a_format_3_choice_is_read_and_checked():
    t = FakeT()
    keep = {"home": {"kind": "keep"}, "lock": {"kind": "gradient", "id": "g1"}}
    good = {"at": 4, "format": 3, "screens": keep, "wallpaper": {"same": False, "parallax": True},
            "layer": {"home": 10, "lock": -40}}
    t.answers[phonewall.CHOICE] = {"choice": good}
    assert phonewall.choice(t) == model_choice(4, scr("keep", ("gradient", "g1")), False, True, (10, -40))
    for bad in ({"wallpaper": None}, {"wallpaper": {"same": "yes", "parallax": False}},
                {"layer": {"home": 101, "lock": 0}}, {"layer": {"home": True, "lock": 0}}, {"layer": {"home": 0}}):
        t.answers[phonewall.CHOICE] = {"choice": {**good, **bad}}
        assert phonewall.choice(t) is None, bad
    # a choice without the format is the per-screen one, whatever else it carries
    t.answers[phonewall.CHOICE] = {"choice": {**good, "format": None}}
    assert phonewall.choice(t) == {"at": 4, "screens": scr("keep", ("gradient", "g1")), "id": None, "target": None}
    # a later phone build may leave `same` out: two screens
    t.answers[phonewall.CHOICE] = {"choice": {**good, "wallpaper": {"parallax": True}}}
    assert phonewall.choice(t) == model_choice(4, scr("keep", ("gradient", "g1")), False, True, (10, -40))


def test_separate_on_the_phone_with_a_lock_gradient_and_layer_applies_the_lock_screen_only(tmp_path):
    async def go():
        svc, ops, cur, ts = service(tmp_path)
        await svc.start()
        try:
            await until(lambda: ops.pushes.get("A"))
            v = ops.last("A")
            home = ("wallpaper", v.items[0].id)
            ops.pending["A"] = model_choice(301, scr(home, ("gradient", v.gradients[2].id)), False, layer=(0, 40))
            await until(lambda: [a[2] for a in ops.acks if a[1] == 301] == ["received", "applied"])
            assert ops.parts == [("lock",)]
            d = looks_on_disk(svc)["A"]
            assert d["wallpaper"] == {"parallax": False,
                                      "home": {"kind": "wallpaper", "style": None, "layer": 0},
                                      "lock": {"kind": "gradient", "style": "glow", "layer": 40}}
            assert d["picks"] == {}             # the home screen goes on following Omarchy's background
            await until(lambda: ops.last("A").layer == {"home": 0, "lock": 40})
            # the window reads the same setting
            c = await connect(svc)
            r = await c.call("look.get", phone="A")
            assert r["result"]["wallpaper"] == d["wallpaper"]
        finally:
            await svc.stop()
    run(go())


def test_parallax_switched_on_the_phone_applies_the_home_screen(tmp_path):
    async def go():
        svc, ops, cur, ts = service(tmp_path)
        await svc.start()
        try:
            await until(lambda: ops.pushes.get("A"))
            v = ops.last("A")
            ops.pending["A"] = model_choice(302, v.screens, parallax=True)
            await until(lambda: [a[2] for a in ops.acks if a[1] == 302] == ["received", "applied"])
            assert ops.parts == [("home",)]
            d = looks_on_disk(svc)["A"]
            assert d["wallpaper"]["parallax"] is True and "same" not in d["wallpaper"]
            await until(lambda: ops.last("A").parallax is True)
            # the darken layer of both screens at once
            ops.pending["A"] = model_choice(303, v.screens, parallax=True, layer=(30, 30))
            await until(lambda: [a[2] for a in ops.acks if a[1] == 303] == ["received", "applied"])
            assert ops.parts[-1] == ("home", "lock")
            assert looks_on_disk(svc)["A"]["wallpaper"]["lock"]["layer"] == 30
            # the same again: just acknowledged
            n = len(ops.parts)
            await until(lambda: ops.last("A").layer == {"home": 30, "lock": 30})
            ops.pending["A"] = model_choice(304, ops.last("A").screens, parallax=True, layer=(30, 30))
            await until(lambda: [a[2] for a in ops.acks if a[1] == 304] == ["received", "applied"])
            assert len(ops.parts) == n
        finally:
            await svc.stop()
    run(go())


def test_same_on_both_from_an_older_phone_build_is_stored_as_two_equal_screens(tmp_path):
    async def go():
        ts0 = themes(tmp_path)
        sep = look(home={"kind": "gradient", "style": "mesh"}, lock={"kind": "wallpaper", "layer": 50},
                   picks={"alpha": {"home": None, "lock": str(ts0[0].backgrounds[2]), "by": "computer"}},
                   crops={str(ts0[0].backgrounds[2]): {"home": 0.3, "lock": 0.8}}).to_dict()
        svc, ops, cur, ts = service(tmp_path, look=sep)
        await svc.start()
        try:
            await until(lambda: ops.pushes.get("A"))
            v = ops.last("A")
            mesh = ("gradient", v.gradients[4].id)
            assert v.screens["home"] == scr(mesh, "keep")["home"]
            ops.pending["A"] = model_choice(311, scr(mesh, mesh), True)
            await until(lambda: [a[2] for a in ops.acks if a[1] == 311] == ["received", "applied"])
            assert ops.parts == [("lock",)]                   # the home screen shows what it showed
            d = looks_on_disk(svc)["A"]
            assert "same" not in d["wallpaper"] and d["wallpaper"]["lock"] == d["wallpaper"]["home"]
            assert "alpha" not in d["picks"] and d["crops"][str(ts[0].backgrounds[2])]["lock"] is None
            await until(lambda: ops.last("A").screens == scr(mesh, mesh))
        finally:
            await svc.stop()
    run(go())


def test_a_choice_with_nothing_visible_changed_saves_without_applying(tmp_path):
    async def go():
        svc, ops, cur, ts = service(tmp_path)
        await svc.start()
        try:
            await until(lambda: ops.pushes.get("A"))
            v = ops.last("A")
            ops.pending["A"] = model_choice(321, v.screens, False)
            await until(lambda: [a[2] for a in ops.acks if a[1] == 321] == ["received", "applied"])
            assert ops.applied("A") == []
            assert "same" not in looks_on_disk(svc)["A"]["wallpaper"]
        finally:
            await svc.stop()
    run(go())


def test_a_wallpaper_picked_for_the_home_screen_alone_on_a_separate_look(tmp_path):
    async def go():
        svc, ops, cur, ts = service(tmp_path, look=look(lock={}).to_dict())
        await svc.start()
        try:
            await until(lambda: ops.pushes.get("A"))
            v = ops.last("A")
            ops.pending["A"] = model_choice(331, scr(("wallpaper", v.items[2].id), v.screens["lock"] and
                                                     ("wallpaper", v.items[0].id)), False, layer=(-20, 0))
            await until(lambda: [a[2] for a in ops.acks if a[1] == 331] == ["received", "applied"])
            assert ops.parts == [("home",)] and ops.acks[-1][3] == v.items[2].id
            d = looks_on_disk(svc)["A"]
            assert d["picks"]["alpha"] == {"home": str(ts[0].backgrounds[2]), "lock": None, "by": "phone"}
            assert d["wallpaper"]["home"]["layer"] == -20
        finally:
            await svc.stop()
    run(go())


def test_an_unsaved_wallpaper_on_a_format_3_choice_puts_the_look_back_and_replace_applies_it(tmp_path):
    async def go():
        svc, ops, cur, ts = service(tmp_path)
        ops.unsaved.add("A")
        await svc.start()
        try:
            await until(lambda: ops.pushes.get("A"))
            v = ops.last("A")
            before = looks_on_disk(svc)["A"]
            want = scr(("wallpaper", v.items[1].id), ("wallpaper", v.items[1].id))
            ops.pending["A"] = model_choice(341, want, True, parallax=True)
            await until(lambda: any(a[1] == 341 and a[2] == "failed" for a in ops.acks))
            assert ops.codes[-1] == daemon.WALLPAPER_UNSAVED and looks_on_disk(svc)["A"] == before
            ops.pending["A"] = model_choice(342, want, True, parallax=True, overwrite=True)
            await until(lambda: any(a[1] == 342 and a[2] == "applied" for a in ops.acks))
            assert ops.overwrote == [True] and looks_on_disk(svc)["A"]["wallpaper"]["parallax"] is True
        finally:
            await svc.stop()
    run(go())
