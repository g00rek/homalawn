"""Applying an Omarchy look to a phone: one snapshot, one preference commit, the right pictures, one undo."""
import io
import json
from pathlib import Path

import pytest
from PIL import Image

from homalawn import look, lookconf, ops, snapshots
from homalawn.look import Theme
from homalawn.session import LOCK_WALLPAPER_FILE, PREFS_FILE, WALLPAPER_FILE, Launcher
from test_transport import linked  # noqa: F401 (fixture)


def png(color, size=(400, 200)) -> bytes:
    out = io.BytesIO()
    Image.new("RGB", size, color).save(out, "PNG")
    return out.getvalue()


@pytest.fixture
def look_phone(linked, monkeypatch):  # noqa: F811
    """The linked fake phone, with a preference store behind prefs_list / prefs_set_many."""
    t, dev, phone = linked
    monkeypatch.setattr("time.sleep", lambda s: None)
    store = {"datastore": {"accent_color": {"type": "string", "value": "system"}},
             "shared": {"pref_launcherTheme": {"type": "string", "value": "system"},
                        "themed_icons": {"type": "bool", "value": False}}}
    commits = []

    def many(p):
        commits.append(p["writes"])
        for w in p["writes"]:
            if w.get("remove"):
                store[w["store"]].pop(w["key"], None)
                continue
            store[w["store"]][w["key"]] = {"type": w["type"], "value": w["value"] if w["type"] != "bool"
                                           else w["value"] == "true"}
        return {}, None
    phone.handlers["launcher.prefs_list"] = lambda p: (json.loads(json.dumps(store)), None)
    phone.handlers["launcher.prefs_set_many"] = many
    dev.size = (1080, 2400)
    return t, dev, phone, store, commits


def val(store, key):
    return (store["datastore"].get(key) or store["shared"][key])["value"]


def settings_theme(tmp_path, mode="dark"):
    bg = tmp_path / "bg.png"
    bg.write_bytes(png((200, 40, 40), (800, 400)))
    colors = {"mode": mode, "accent": "#7aa2f7", "background": "#1a1b26", "lighter_background": "#24283b"}
    return Theme("tokyo-night", tmp_path, colors, [bg], None), bg


def wp(source="chosen", theme="tokyo-night", parallax=False, home=None, lock=None, **kw):
    """Look settings in the new shape; `home` / `lock` are Screen dicts (defaults: wallpaper, no layer)."""
    scr = {"kind": "wallpaper", "style": None, "layer": 0}
    d = {"source": source, "theme": theme, "wallpaper": {"parallax": parallax,
         "home": {**scr, **(home or {})}, "lock": {**scr, **(lock or {})}}}
    return lookconf.validate(d | kw)


def fake_size(monkeypatch, size=(100, 200)):
    monkeypatch.setattr(Launcher, "screen_size", lambda self: size)


def test_one_snapshot_one_commit_and_undo_puts_everything_back(look_phone):
    t, dev, phone, store, commits = look_phone
    la = Launcher(t)
    old_home, old_lock = dev.wallpaper_now, dev.lock_now
    snap = la.apply_look({"home": png("red"), "lock": png("blue")},
                         [("accent_color", "custom|#ff7aa2f7"), ("pref_launcherTheme", "dark")])
    assert snap.startswith("look-")
    assert len([n for n in snapshots.list_undoable(la.device_key()) if n.startswith("look-")]) == 1
    files = snapshots.load(snap)
    assert set(files) == {PREFS_FILE, WALLPAPER_FILE, LOCK_WALLPAPER_FILE}
    assert len(commits) == 1 and {w["key"] for w in commits[0]} == {"accent_color", "pref_launcherTheme"}
    assert val(store, "accent_color") == "custom|#ff7aa2f7"
    assert dev.wallpaper_now == png("red") and dev.lock_now == png("blue")
    name, _ = la.undo()
    assert name == snap
    assert val(store, "accent_color") == "system" and val(store, "pref_launcherTheme") == "system"
    assert dev.wallpaper_now == files[WALLPAPER_FILE] and dev.lock_now == files[LOCK_WALLPAPER_FILE]


def test_both_key_sends_one_picture_to_both_screens(look_phone):
    t, dev, phone, store, commits = look_phone
    la = Launcher(t)
    snap = la.apply_look({"both": png("green")}, [])
    sets = [p.get("which", "home") for _, m, p, _ in phone.log if m == "launcher.wallpaper_set"]
    assert sets == ["both"] and commits == []
    assert PREFS_FILE not in snapshots.load(snap)


def test_nothing_to_change_takes_no_snapshot(look_phone):
    t, dev, phone, store, commits = look_phone
    la = Launcher(t)
    assert la.apply_look({}, []) == ""
    assert snapshots.list_undoable(la.device_key()) == [] and commits == []


def test_colours_only_leaves_wallpapers_out_of_the_snapshot(look_phone):
    t, dev, phone, store, commits = look_phone
    la = Launcher(t)
    snap = la.apply_look({}, [("accent_color", "custom|#ff112233")])
    assert set(snapshots.load(snap)) == {PREFS_FILE}
    assert "launcher.wallpaper_set" not in [m for _, m, _, _ in phone.log]


def test_a_bad_pref_stops_before_anything_changes(look_phone):
    t, dev, phone, store, commits = look_phone
    la = Launcher(t)
    with pytest.raises(ValueError):
        la.apply_look({"home": png("red")}, [("no_such_setting_zz", "x")])
    assert snapshots.list_undoable(la.device_key()) == [] and commits == []
    assert "launcher.wallpaper_set" not in [m for _, m, _, _ in phone.log]


def sets_of(phone):
    """The pictures that went on screen, as the computer sent them: each wallpaper_set's `which`,
    and for each look_show the pictures of the look_put it shows (home, lock, both)."""
    out, put = [], {}
    for _, m, p, _ in phone.log:
        if m == "launcher.wallpaper_set":
            out.append(p.get("which", "home"))
        elif m == "launcher.look_put":
            put[p["key"]] = [w for w in ("home", "lock", "both") if w in p.get("pictures", {})]
        elif m == "launcher.look_show":
            out += put.get(p["key"], [])
    return out


def test_ops_same_crop_and_layer_sends_one_both_picture(look_phone, tmp_path, monkeypatch):
    t, dev, phone, store, commits = look_phone
    fake_size(monkeypatch)
    theme, bg = settings_theme(tmp_path)
    s = ops.Session(); s._t = t
    r = ops.look_apply(s, wp(), theme=theme, background=bg)
    assert sets_of(phone) == ["both"]
    assert r.text.startswith("Wallpaper & style applied (tokyo-night, bg.png: home and lock). The previous one is saved as look-")
    assert "`homalawn undo` puts it back." in r.text
    assert val(store, "accent_color") == "custom|#ff7aa2f7" and val(store, "pref_launcherTheme") == "dark"
    assert Image.open(io.BytesIO(dev.wallpaper_now)).size == (100, 200)
    assert r.data["theme"] == "tokyo-night" and r.data["background"] == str(bg)   # what it used
    assert r.data["snapshot"].startswith("look-")
    assert snapshots.describe(r.data["snapshot"]) == {"kind": "style", "title": "Wallpaper & style: tokyo-night"}


def test_ops_different_layers_send_two_pictures_home_first(look_phone, tmp_path, monkeypatch):
    t, dev, phone, store, commits = look_phone
    fake_size(monkeypatch)
    theme, bg = settings_theme(tmp_path)
    s = ops.Session(); s._t = t
    st = wp(lock={"layer": 50})
    ops.look_apply(s, st, theme=theme, background=bg)
    assert sets_of(phone) == ["home", "lock"]
    assert dev.wallpaper_now != dev.lock_now


def test_ops_different_crops_send_two_pictures(look_phone, tmp_path, monkeypatch):
    t, dev, phone, store, commits = look_phone
    fake_size(monkeypatch)
    theme, bg = settings_theme(tmp_path)
    s = ops.Session(); s._t = t
    st = wp(crops={str(bg): {"home": 0.0, "lock": 1.0}})
    ops.look_apply(s, st, theme=theme, background=bg)
    assert sets_of(phone) == ["home", "lock"]


def test_ops_target_home_only_touches_home(look_phone, tmp_path, monkeypatch):
    t, dev, phone, store, commits = look_phone
    fake_size(monkeypatch)
    theme, bg = settings_theme(tmp_path)
    s = ops.Session(); s._t = t
    r = ops.look_apply(s, wp(lock={"kind": "keep"}), theme=theme, background=bg)
    assert sets_of(phone) == ["home"]
    assert "bg.png: home)" in r.text


def test_ops_both_screens_kept_leaves_wallpapers_alone(look_phone, tmp_path, monkeypatch):
    t, dev, phone, store, commits = look_phone
    theme, bg = settings_theme(tmp_path)
    s = ops.Session(); s._t = t
    ops.look_apply(s, wp(home={"kind": "keep"}, lock={"kind": "keep"}), theme=theme, background=bg)
    assert sets_of(phone) == []
    (snap,) = snapshots.list_undoable(Launcher(t).device_key())
    assert set(snapshots.load(snap)) == {PREFS_FILE}


def test_ops_colours_switch_off_sends_no_colour_prefs(look_phone, tmp_path, monkeypatch):
    t, dev, phone, store, commits = look_phone
    fake_size(monkeypatch)
    theme, bg = settings_theme(tmp_path)
    s = ops.Session(); s._t = t
    st = wp(sync={"colors": False, "mode": False, "icons": False})
    ops.look_apply(s, st, theme=theme, background=bg)
    # no colour or mode settings: only the icons switch (off: themed icons off, colours cleared)
    # and the scrolling setting that goes with the wallpaper
    assert [[w["key"] for w in c] for c in commits] == [["themed_icons", "homalawn_icon_color",
                                                         "homalawn_icon_bg_color", look.PARALLAX_PREF]]
    assert sets_of(phone) == ["both"]


def test_ops_icons_switch_turns_themed_icons_on(look_phone, tmp_path, monkeypatch):
    t, dev, phone, store, commits = look_phone
    theme, bg = settings_theme(tmp_path)
    s = ops.Session(); s._t = t
    st = wp(home={"kind": "keep"}, sync={"colors": False, "mode": False, "icons": True})
    ops.look_apply(s, st, theme=theme, background=bg)
    assert val(store, "themed_icons") is True


def test_ops_icons_send_the_accent_on_the_theme_plate_by_default(look_phone, tmp_path, monkeypatch):
    t, dev, phone, store, commits = look_phone
    theme, bg = settings_theme(tmp_path)
    s = ops.Session(); s._t = t
    ops.look_apply(s, wp(home={"kind": "keep"}, lock={"kind": "keep"},
                         sync={"colors": False, "mode": False, "icons": True}), theme=theme, background=bg)
    sent = {w["key"]: (w["store"], w["type"], w["value"]) for c in commits for w in c}
    assert sent["homalawn_icon_color"] == ("shared", "string", "#7aa2f7")
    assert sent["homalawn_icon_bg_color"] == ("shared", "string", "#24283b")     # dark: lighter_background


def test_ops_icons_send_the_colour_chosen_for_this_theme(look_phone, tmp_path, monkeypatch):
    t, dev, phone, store, commits = look_phone
    theme, bg = settings_theme(tmp_path)
    s = ops.Session(); s._t = t
    st = wp(home={"kind": "keep"}, lock={"kind": "keep"}, sync={"colors": False, "mode": False, "icons": True},
            icon_colors={"tokyo-night": "#9ece6a", "nord": "#88c0d0"})
    ops.look_apply(s, st, theme=theme, background=bg)
    assert val(store, "homalawn_icon_color") == "#9ece6a" and val(store, "homalawn_icon_bg_color") == "#24283b"


def test_ops_icons_off_turns_themed_icons_off_and_clears_the_colours(look_phone, tmp_path, monkeypatch):
    # the switch is the truth: off undoes an earlier on (themed icons and both colours, which
    # patch 0023 reads as "empty = the accent's colour")
    t, dev, phone, store, commits = look_phone
    store["shared"] |= {"themed_icons": {"type": "bool", "value": True},
                        "homalawn_icon_color": {"type": "string", "value": "#9ece6a"},
                        "homalawn_icon_bg_color": {"type": "string", "value": "#24283b"}}
    theme, bg = settings_theme(tmp_path)
    s = ops.Session(); s._t = t
    st = wp(home={"kind": "keep"}, lock={"kind": "keep"}, sync={"colors": True, "mode": True, "icons": False},
            icon_colors={"tokyo-night": "#9ece6a"})
    ops.look_apply(s, st, theme=theme, background=bg)
    sent = {w["key"]: (w["store"], w["type"], w["value"]) for c in commits for w in c}
    assert sent["themed_icons"] == ("shared", "bool", "false")
    assert sent["homalawn_icon_color"] == ("shared", "string", "")
    assert sent["homalawn_icon_bg_color"] == ("shared", "string", "")
    assert val(store, "themed_icons") is False
    assert val(store, "homalawn_icon_color") == "" and val(store, "homalawn_icon_bg_color") == ""


def test_ops_icons_off_then_on_again_restores_the_colours(look_phone, tmp_path, monkeypatch):
    t, dev, phone, store, commits = look_phone
    theme, bg = settings_theme(tmp_path)
    s = ops.Session(); s._t = t
    keep = {"home": {"kind": "keep"}, "lock": {"kind": "keep"}}
    ops.look_apply(s, wp(**keep, sync={"colors": False, "mode": False, "icons": False}), theme=theme, background=bg)
    assert val(store, "themed_icons") is False
    ops.look_apply(s, wp(**keep, sync={"colors": False, "mode": False, "icons": True}), theme=theme, background=bg)
    assert val(store, "themed_icons") is True
    assert val(store, "homalawn_icon_color") == "#7aa2f7" and val(store, "homalawn_icon_bg_color") == "#24283b"


def test_ops_light_mode_theme_sets_light(look_phone, tmp_path, monkeypatch):
    t, dev, phone, store, commits = look_phone
    theme, bg = settings_theme(tmp_path, mode="light")
    s = ops.Session(); s._t = t
    ops.look_apply(s, wp(home={"kind": "keep"}, sync={"colors": False, "mode": True, "icons": False}),
                   theme=theme, background=bg)
    assert val(store, "pref_launcherTheme") == "light"


def test_ops_follow_without_a_current_background_says_so(look_phone, tmp_path, monkeypatch):
    t, dev, phone, store, commits = look_phone
    monkeypatch.setenv("HOMALAWN_OMARCHY_STATE", str(tmp_path / "state"))
    (tmp_path / "state").mkdir()
    (tmp_path / "state" / "theme.name").write_text("tokyo-night")
    s = ops.Session(); s._t = t
    with pytest.raises(ops.OpError, match="Omarchy has no current background"):
        ops.look_apply(s, wp("follow", None))
    assert snapshots.list_undoable(Launcher(t).device_key()) == []


def test_ops_follow_uses_the_current_theme_and_background(look_phone, tmp_path, monkeypatch):
    t, dev, phone, store, commits = look_phone
    fake_size(monkeypatch)
    theme, bg = settings_theme(tmp_path)
    state = tmp_path / "state"
    state.mkdir()
    (state / "theme.name").write_text("tokyo-night")
    (state / "background").symlink_to(bg)
    monkeypatch.setenv("HOMALAWN_OMARCHY_STATE", str(state))
    monkeypatch.setattr(look, "theme_named", lambda name, **k: theme if name == "tokyo-night" else None)
    s = ops.Session(); s._t = t
    r = ops.look_apply(s, wp("follow", None))
    assert "tokyo-night, bg.png" in r.text and sets_of(phone) == ["both"]
    assert (r.data["theme"], r.data["background"]) == ("tokyo-night", str(bg))


def test_ops_follow_uses_the_wallpaper_picked_for_the_theme(look_phone, tmp_path, monkeypatch):
    """A pick for the followed theme wins over Omarchy's current background; another theme's does not."""
    t, dev, phone, store, commits = look_phone
    fake_size(monkeypatch)
    theme, bg = settings_theme(tmp_path)
    other = tmp_path / "other.png"
    other.write_bytes(png((10, 200, 10), (800, 400)))
    state = tmp_path / "state"
    state.mkdir()
    (state / "theme.name").write_text("tokyo-night")
    (state / "background").symlink_to(bg)
    monkeypatch.setenv("HOMALAWN_OMARCHY_STATE", str(state))
    monkeypatch.setattr(look, "theme_named", lambda name, **k: theme if name == "tokyo-night" else None)
    s = ops.Session(); s._t = t
    st = wp("follow", None, picks={
        "tokyo-night": {"home": str(other), "by": "phone"},
        "nord": {"home": str(bg), "by": "phone"}})
    r = ops.look_apply(s, st)
    assert "tokyo-night, other.png" in r.text and r.data["background"] == str(other)
    other.unlink()                     # a pick whose picture is gone falls back to Omarchy's
    r = ops.look_apply(s, st)
    assert r.data["background"] == str(bg)


def test_ops_chosen_uses_the_pick_unless_a_background_is_named(look_phone, tmp_path, monkeypatch):
    t, dev, phone, store, commits = look_phone
    fake_size(monkeypatch)
    theme, bg = settings_theme(tmp_path)
    other = tmp_path / "other.png"
    other.write_bytes(png((10, 200, 10), (800, 400)))
    theme.backgrounds.append(other)
    monkeypatch.setattr(look, "theme_named", lambda name, **k: theme)
    s = ops.Session(); s._t = t
    picks = {"tokyo-night": {"home": str(other), "by": "phone"}}
    r = ops.look_apply(s, wp(theme="Tokyo Night", picks=picks))
    assert r.data["background"] == str(other)
    r = ops.look_apply(s, wp(theme="Tokyo Night", picks=picks), background=bg)
    assert r.data["background"] == str(bg)


def test_ops_off_is_refused(look_phone):
    t, dev, phone, store, commits = look_phone
    s = ops.Session(); s._t = t
    with pytest.raises(ops.OpError, match="off"):
        ops.look_apply(s, lookconf.LookSettings())


def test_unsaveable_wallpaper_message_has_no_no_backup_option(look_phone):
    from homalawn.session import MSG_MEDIA_NOT_GRANTABLE
    t, dev, phone, store, commits = look_phone
    la = Launcher(t)
    la._media_msg = MSG_MEDIA_NOT_GRANTABLE
    la._backup_files = lambda parts, notify=None: (_ for _ in ()).throw(
        __import__("homalawn.transport", fromlist=["NotSupported"]).NotSupported(MSG_MEDIA_NOT_GRANTABLE))
    with pytest.raises(ValueError) as e:
        la.apply_look({"home": png("red")}, [])
    msg = str(e.value)
    assert "--no-backup" not in msg and "photo access" in msg and "switch Wallpaper off in Wallpaper & style" in msg
    # `--via adb look apply` reads the settings under the serial and records the picture under the
    # adb key, so it cannot unblock the link: it is not advised
    assert "--via adb" not in msg and "try again" in msg
    assert snapshots.list_undoable(la.device_key()) == []


def test_a_failed_write_after_the_snapshot_points_at_undo(look_phone, monkeypatch):
    t, dev, phone, store, commits = look_phone
    la = Launcher(t)
    monkeypatch.setattr(la, "_put_wallpaper", lambda *a, **k: (_ for _ in ()).throw(ValueError("disk full")))
    with pytest.raises(ValueError) as e:
        la.apply_look({"home": png("red")}, [("accent_color", "custom|#ff112233")])
    (snap,) = snapshots.list_undoable(la.device_key())
    assert str(e.value).endswith(f"Partly applied; `homalawn undo` puts back the previous wallpaper & style ({snap}).")
    assert str(e.value).startswith("disk full")


def test_a_look_apply_keeps_only_the_newest_20_look_snapshots(look_phone):
    t, dev, phone, store, commits = look_phone
    la = Launcher(t)
    for i in range(20):
        snapshots.save(f"look-20200101-{i:06d}", {"f": b"x"}, device=la.device_key())
    snap = la.apply_look({}, [("accent_color", "custom|#ff112233")])
    looks = [n for n in snapshots.list_() if n.startswith("look-")]
    assert snap in looks and len(looks) == 20 and "look-20200101-000000" not in looks


def _no_backup(la, msg=None):
    """The phone cannot hand over its current wallpaper (no photo access over the link)."""
    from homalawn.session import MSG_MEDIA_NOT_GRANTABLE
    from homalawn.transport import NotSupported
    tried = []

    def refuse(parts, notify=None):
        tried.append(tuple(parts))
        raise NotSupported(msg or MSG_MEDIA_NOT_GRANTABLE)
    la._backup_files = refuse
    return tried


def test_an_unsaveable_wallpaper_is_its_own_error_and_changes_nothing(look_phone):
    from homalawn.session import WallpaperUnsaved
    t, dev, phone, store, commits = look_phone
    la = Launcher(t)
    _no_backup(la)
    with pytest.raises(WallpaperUnsaved) as e:
        la.apply_look({"home": png("red")}, [("accent_color", "custom|#ff112233")])
    assert e.value.code == "wallpaper_unsaved"
    assert snapshots.list_undoable(la.device_key()) == [] and commits == []
    assert sets_of(phone) == []


def test_overwrite_wallpaper_replaces_it_unsaved_and_undo_still_puts_the_colours_back(look_phone):
    t, dev, phone, store, commits = look_phone
    la = Launcher(t)
    tried = _no_backup(la)
    snap = la.apply_look({"home": png("red")}, [("accent_color", "custom|#ff112233"),
                                                ("pref_launcherTheme", "dark")], overwrite_wallpaper=True)
    assert snap.startswith("look-")
    assert set(snapshots.load(snap)) == {PREFS_FILE}            # no wallpaper backup in it
    assert tried and all(len(p) == 1 for p in tried[1:])        # each part tried once on its own
    assert dev.wallpaper_now == png("red") and val(store, "accent_color") == "custom|#ff112233"
    name, _ = la.undo()
    assert name == snap
    assert val(store, "accent_color") == "system" and val(store, "pref_launcherTheme") == "system"
    assert dev.wallpaper_now == png("red")                      # nothing to put back for it


def test_overwrite_wallpaper_keeps_the_part_that_could_be_saved(look_phone):
    from homalawn.transport import NotSupported
    from homalawn.session import MSG_MEDIA_NOT_GRANTABLE
    t, dev, phone, store, commits = look_phone
    la = Launcher(t)

    def partly(parts, notify=None):
        if tuple(parts) == ("lock",):
            return {LOCK_WALLPAPER_FILE: png("blue")}
        raise NotSupported(MSG_MEDIA_NOT_GRANTABLE)
    la._backup_files = partly
    snap = la.apply_look({"home": png("red"), "lock": png("green")}, [], overwrite_wallpaper=True)
    assert set(snapshots.load(snap)) == {LOCK_WALLPAPER_FILE}


def test_ops_unsaveable_wallpaper_has_the_code_and_overwrite_goes_through(look_phone, tmp_path, monkeypatch):
    t, dev, phone, store, commits = look_phone
    fake_size(monkeypatch)
    theme, bg = settings_theme(tmp_path)
    monkeypatch.setattr(look, "theme_named", lambda name, **k: theme)
    from homalawn.transport import NotSupported
    from homalawn.session import MSG_MEDIA_NOT_GRANTABLE
    monkeypatch.setattr(Launcher, "_backup_files",
                        lambda self, parts, notify=None: (_ for _ in ()).throw(NotSupported(MSG_MEDIA_NOT_GRANTABLE)))
    s = ops.Session(); s._t = t
    settings = wp()
    with pytest.raises(ops.OpError) as e:
        ops.look_apply(s, settings)
    assert e.value.code == "wallpaper_unsaved"
    assert sets_of(phone) == []
    r = ops.look_apply(s, settings, overwrite_wallpaper=True)
    assert sets_of(phone) == ["both"]
    assert set(snapshots.load(r.data["snapshot"])) == {PREFS_FILE}


# -- per screen: wallpaper, gradient or keep (the phone's "Wallpaper from Omarchy" screen) --------
def test_ops_gradient_on_the_lock_screen_only_home_kept(look_phone, tmp_path, monkeypatch):
    from homalawn import gradients
    t, dev, phone, store, commits = look_phone
    fake_size(monkeypatch)
    theme, bg = settings_theme(tmp_path)
    monkeypatch.setattr(look, "theme_named", lambda name, **k: theme)
    s = ops.Session(); s._t = t
    old_home = dev.wallpaper_now
    st = wp(home={"kind": "keep"}, lock={"kind": "gradient", "style": "glow", "layer": 40})
    r = ops.look_apply(s, st)
    assert sets_of(phone) == ["lock"] and dev.wallpaper_now == old_home
    want = look.render(gradients.render(theme.colors, "glow", (100, 200)), (100, 200), 0.5, 40)
    assert dev.lock_now == want                          # the gradient at the phone's size, darkened 40 %
    assert "Glow gradient: lock" in r.text and r.data["background"] is None
    assert len([n for n in snapshots.list_undoable(Launcher(t).device_key()) if n.startswith("look-")]) == 1


def test_ops_lock_pick_and_parts(look_phone, tmp_path, monkeypatch):
    t, dev, phone, store, commits = look_phone
    fake_size(monkeypatch)
    theme, bg = settings_theme(tmp_path)
    other = tmp_path / "other.png"
    other.write_bytes(png((10, 200, 10), (800, 400)))
    monkeypatch.setattr(look, "theme_named", lambda name, **k: theme)
    s = ops.Session(); s._t = t
    st = wp(picks={"tokyo-night": {"lock": str(other), "by": "phone"}})
    r = ops.look_apply(s, st)
    assert sets_of(phone) == ["home", "lock"]
    assert "bg.png: home, other.png: lock" in r.text
    phone.log.clear()
    ops.look_apply(s, st, parts=("lock",))               # a choice that changed only the lock screen
    assert sets_of(phone) == ["lock"]


def test_ops_both_screens_kept_sends_colours_only_and_needs_no_background(look_phone, tmp_path, monkeypatch):
    t, dev, phone, store, commits = look_phone
    fake_size(monkeypatch)
    theme, _ = settings_theme(tmp_path)
    theme.backgrounds.clear()
    monkeypatch.setattr(look, "theme_named", lambda name, **k: theme)
    s = ops.Session(); s._t = t
    st = wp(home={"kind": "keep"}, lock={"kind": "keep"})
    r = ops.look_apply(s, st)
    assert sets_of(phone) == [] and "colors only" in r.text


def test_ops_gradient_without_theme_colours_is_refused_before_anything_changes(look_phone, tmp_path, monkeypatch):
    t, dev, phone, store, commits = look_phone
    fake_size(monkeypatch)
    theme, bg = settings_theme(tmp_path)
    theme.colors.clear()
    monkeypatch.setattr(look, "theme_named", lambda name, **k: theme)
    s = ops.Session(); s._t = t
    st = wp(home={"kind": "gradient", "style": "fade"}, lock={"kind": "keep"})
    with pytest.raises(ops.OpError, match="no colours"):
        ops.look_apply(s, st)
    assert snapshots.list_undoable(Launcher(t).device_key()) == []


# -- parallax: a wider home picture and the launcher's scrolling setting --------------------------
def test_ops_parallax_on_sends_a_wide_home_picture_and_scrolling_true(look_phone, tmp_path, monkeypatch):
    t, dev, phone, store, commits = look_phone
    fake_size(monkeypatch)
    theme, bg = settings_theme(tmp_path)
    s = ops.Session(); s._t = t
    ops.look_apply(s, wp(parallax=True), theme=theme, background=bg)
    assert sets_of(phone) == ["home", "lock"]                      # never "both": the sizes differ
    assert Image.open(io.BytesIO(dev.wallpaper_now)).size == look.parallax_size((100, 200)) == (200, 200)
    assert Image.open(io.BytesIO(dev.lock_now)).size == (100, 200)
    assert val(store, look.PARALLAX_PREF) is True


def test_ops_parallax_off_sends_both_and_scrolling_false(look_phone, tmp_path, monkeypatch):
    t, dev, phone, store, commits = look_phone
    fake_size(monkeypatch)
    theme, bg = settings_theme(tmp_path)
    s = ops.Session(); s._t = t
    ops.look_apply(s, wp(parallax=False), theme=theme, background=bg)
    assert sets_of(phone) == ["both"]
    assert Image.open(io.BytesIO(dev.wallpaper_now)).size == (100, 200)
    assert val(store, look.PARALLAX_PREF) is False


def test_ops_home_kept_does_not_write_the_scrolling_setting(look_phone, tmp_path, monkeypatch):
    t, dev, phone, store, commits = look_phone
    fake_size(monkeypatch)
    theme, bg = settings_theme(tmp_path)
    s = ops.Session(); s._t = t
    ops.look_apply(s, wp(parallax=True, home={"kind": "keep"}), theme=theme, background=bg)
    assert sets_of(phone) == ["lock"]
    assert look.PARALLAX_PREF not in store["shared"] or val(store, look.PARALLAX_PREF) is not True
    assert all(w["key"] != look.PARALLAX_PREF for c in commits for w in c)


def test_ops_parallax_with_a_gradient_is_off_screen_size_and_scrolling_false(look_phone, tmp_path, monkeypatch):
    t, dev, phone, store, commits = look_phone
    fake_size(monkeypatch)
    theme, bg = settings_theme(tmp_path)
    s = ops.Session(); s._t = t
    st = wp(parallax=True, home={"kind": "gradient", "style": "glow"})
    assert st.wallpaper["parallax"] is True and not st.parallax_on()
    ops.look_apply(s, st, theme=theme)
    assert Image.open(io.BytesIO(dev.wallpaper_now)).size == (100, 200)    # never the wide picture
    assert Image.open(io.BytesIO(dev.lock_now)).size == (100, 200)
    assert val(store, look.PARALLAX_PREF) is False


def test_ops_separate_lock_pick_and_crop_and_layer_per_screen(look_phone, tmp_path, monkeypatch):
    from homalawn import gradients  # noqa: F401
    t, dev, phone, store, commits = look_phone
    fake_size(monkeypatch)
    theme, bg = settings_theme(tmp_path)
    other = tmp_path / "other.png"
    img = Image.new("RGB", (800, 400), (0, 0, 255))
    img.paste((255, 0, 0), (0, 0, 400, 400))
    buf = io.BytesIO(); img.save(buf, "PNG"); other.write_bytes(buf.getvalue())
    s = ops.Session(); s._t = t
    st = wp(lock={"layer": 30},
            picks={"tokyo-night": {"lock": str(other), "by": "computer"}},
            crops={str(other): {"home": 0.5, "lock": 0.0}})
    ops.look_apply(s, st, theme=theme, background=None)
    assert dev.lock_now == look.render(other.read_bytes(), (100, 200), 0.0, 30)
    assert dev.wallpaper_now == look.render(bg.read_bytes(), (100, 200), 0.5, 0)


# -- over the link: the look goes into the phone's look book (look_put), then on screen (look_show) --
@pytest.fixture
def link_phone_session(look_phone, tmp_path, monkeypatch):
    """The linked fake phone and a Session on it; Omarchy follows tokyo-night with bg.png."""
    t, dev, phone, store, commits = look_phone
    fake_size(monkeypatch)
    theme, bg = settings_theme(tmp_path)
    state = tmp_path / "state"
    state.mkdir()
    (state / "theme.name").write_text("tokyo-night")
    (state / "background").symlink_to(bg)
    monkeypatch.setenv("HOMALAWN_OMARCHY_STATE", str(state))
    monkeypatch.setattr(look, "theme_named", lambda name, **k: theme if name == "tokyo-night" else None)
    s = ops.Session(); s._t = t
    return phone, s


def settings_follow():
    return wp("follow", None)


def test_link_look_is_put_then_shown(link_phone_session):
    phone, s = link_phone_session
    calls = []
    phone.handlers["launcher.look_put"] = lambda p: (calls.append(("put", p)) or
        {"stored": True, "show": True, "source": {"fp": "x", "name": "me"}, "scope": "latest"}, None)
    phone.handlers["launcher.look_show"] = lambda p: (calls.append(("show", p)) or {"shown": True}, None)
    r = ops.look_apply(s, settings_follow(), stamp=lambda key: 1234)
    put = calls[0][1]
    assert put["changed_at"] == 1234 and len(put["key"]) == 16 and calls[1] == ("show", {"key": put["key"]})
    assert r.data["shown"] is True and r.data["snapshot"].startswith("look-")
    assert "launcher.wallpaper_set" not in phone.methods()          # one way: the book


def test_link_look_kept_takes_no_snapshot(link_phone_session):
    phone, s = link_phone_session
    phone.handlers["launcher.look_put"] = lambda p: ({"stored": True, "show": False,
        "source": {"fp": "y", "name": "Laptop"}, "scope": "chosen"}, None)
    r = ops.look_apply(s, settings_follow())
    assert r.data["shown"] is False and not r.data["snapshot"]
    assert "Kept on" in r.text and "Laptop" in r.text
    assert "launcher.look_show" not in phone.methods()


def test_old_phone_build_says_update(link_phone_session):
    phone, s = link_phone_session
    phone.look_book = False                  # a build before patch 0031: unknown_method
    with pytest.raises(ops.OpError) as e:
        ops.look_apply(s, settings_follow())
    assert "homalawn update" in str(e.value) and "Homa Lawn on Pixel is too old" in str(e.value)
    assert "launcher.wallpaper_set" not in phone.methods()          # no second way
    assert snapshots.list_undoable(Launcher(s.transport()).device_key()) == []


def test_link_look_key_is_16_hex_and_follows_the_look(link_phone_session):
    phone, s = link_phone_session
    stamps = []
    r1 = ops.look_apply(s, settings_follow(), stamp=lambda key: stamps.append(key) or 5)
    r2 = ops.look_apply(s, wp("follow", None, sync={"colors": False, "mode": True, "icons": False}))
    assert stamps == [r1.data["key"]] and r1.data["changed_at"] == 5
    assert len(r1.data["key"]) == 16 and all(c in "0123456789abcdef" for c in r1.data["key"])
    assert r1.data["key"] != r2.data["key"]
    assert ops.look_key("a", [("k", "v")], {"home": b"1"}) == ops.look_key("a", [("k", "v")], {"home": b"1"})
    assert ops.look_key("a", [("k", "v")], {"home": b"1"}) != ops.look_key("a", [("k", "v")], {"home": b"2"})


def test_link_look_sends_staged_pictures_and_resolved_prefs(link_phone_session):
    phone, s = link_phone_session
    ops.look_apply(s, settings_follow())
    (put,) = [p for _, m, p, _ in phone.log if m == "launcher.look_put"]
    assert set(put["pictures"]) == {"both"}
    assert put["pictures"]["both"].endswith(f"/files/omalawn/look-{put['key']}-both.img")
    assert put["theme"] == "tokyo-night"
    assert {"store": "shared", "key": "pref_launcherTheme", "type": "string", "value": "dark"} in put["prefs"]


def test_link_same_look_again_is_already_on_the_screen(link_phone_session):
    phone, s = link_phone_session
    ops.look_apply(s, settings_follow())
    phone.log.clear()
    r = ops.look_apply(s, settings_follow())
    assert r.data["shown"] is False and not r.data["snapshot"] and r.data["mine"] is True
    assert r.text == "Already on Pixel: its screen shows this wallpaper & style."
    assert "launcher.look_show" not in phone.methods()
    assert len([n for n in snapshots.list_undoable(Launcher(s.transport()).device_key()) if n.startswith("look-")]) == 1


def test_link_parts_put_only_that_screen(link_phone_session):
    phone, s = link_phone_session
    st = wp("follow", None, lock={"layer": 40})
    ops.look_apply(s, st)
    phone.log.clear()
    ops.look_apply(s, st, parts=("lock",))
    (put,) = [p for _, m, p, _ in phone.log if m == "launcher.look_put"]
    assert set(put["pictures"]) == {"lock"} and sets_of(phone) == ["lock"]


def test_link_kept_look_says_who_and_why(link_phone_session):
    phone, s = link_phone_session
    phone.handlers["launcher.look_put"] = lambda p: ({"stored": True, "show": False,
        "source": {"fp": "y", "name": "Laptop"}, "scope": "latest"}, None)
    r = ops.look_apply(s, settings_follow())
    assert r.text == "Kept on Pixel: it shows Laptop's wallpaper & style (it changed last)."
    assert r.data["source"] == "Laptop" and r.data["mine"] is False


def test_link_home_only_look_keeps_the_moved_picture_for_the_lock_screen(link_phone_session):
    """The lock screen showed the home picture: Android moves the old home picture there when the
    look's home picture goes on; homalawn still holds it, so a later backup needs no phone read."""
    phone, s = link_phone_session
    t = s.transport()
    dev = phone.dev
    la = Launcher(t)
    old = png("blue")
    t.call("launcher.wallpaper_set", {"path": t.write_private("old.png", old)})
    t.call("launcher.wallpaper_lock_reset")
    la._remember_wallpaper({"home": old})
    assert dev.lock_id < 0
    phone.log.clear()
    ops.look_apply(s, wp("follow", None, lock={"kind": "keep"}))
    assert "launcher.look_show" in phone.methods() and "launcher.wallpaper_set" not in phone.methods()
    assert dev.lock_now == old
    assert Launcher(t)._held_wallpaper("lock") == old


def test_link_undo_then_the_same_look_is_shown_again(link_phone_session):
    """undo writes the screen with wallpaper_set / prefs_set_many: the phone's `applied` goes, so
    the same look is put on the screen again, not reported as already there."""
    phone, s = link_phone_session
    r1 = ops.look_apply(s, settings_follow())
    ops.undo(s)
    assert phone.look_applied is None
    phone.log.clear()
    r2 = ops.look_apply(s, settings_follow())
    assert r2.data["key"] == r1.data["key"] and r2.data["shown"] is True
    assert r2.text.startswith("Wallpaper & style applied") and sets_of(phone) == ["both"]


@pytest.mark.parametrize("method,params", [
    ("launcher.wallpaper_lock_reset", {}),
    ("launcher.pref_set", {"store": "datastore", "key": "x", "type": "string", "value": "1"})])
def test_link_any_other_screen_write_clears_applied(link_phone_session, method, params):
    phone, s = link_phone_session
    phone.handlers["launcher.pref_set"] = lambda p: ({}, None)
    ops.look_apply(s, settings_follow())
    assert phone.look_applied is not None
    s.transport().call(method, params)
    assert phone.look_applied is None


def test_link_another_source_between_put_and_show_keeps_no_snapshot(link_phone_session):
    phone, s = link_phone_session

    def put_then_lose(p):
        r = phone._look("launcher.look_put", p, s.transport().fingerprint)
        phone.look_source = "f" * 64        # another computer became the source meanwhile
        return r, None
    phone.handlers["launcher.look_put"] = put_then_lose
    r = ops.look_apply(s, settings_follow())
    assert r.data["shown"] is False and not r.data["snapshot"] and r.data["mine"] is False
    assert r.text == "Kept on Pixel: it shows another computer's wallpaper & style (it changed last)."
    assert snapshots.list_undoable(Launcher(s.transport()).device_key()) == []
    assert "launcher.look_show" in phone.methods() and sets_of(phone) == ["both"]   # asked, refused
    assert phone.look_applied is None


def test_fake_book_keeps_changed_at_for_the_same_key_and_checks_like_the_phone(link_phone_session):
    from homalawn.transport import ProviderError
    phone, s = link_phone_session
    t = s.transport()
    k = "0123456789abcdef"
    t.call("launcher.look_put", {"key": k, "changed_at": 5, "theme": "a", "prefs": [], "pictures": {}})
    t.call("launcher.look_put", {"key": k, "changed_at": 9, "theme": "a", "prefs": [], "pictures": {}})
    assert phone.looks[t.fingerprint]["changed_at"] == 5
    for bad in ({"pictures": {"side": t.write_private(f"look-{k}-side.img", b"x")}},
                {"prefs": [{"store": "datastore", "key": "x", "type": "colour", "value": "1"}]},
                {"prefs": [{"store": "elsewhere", "key": "x", "type": "string", "value": "1"}]},
                {"prefs": [{"store": "shared", "key": "x", "type": "int", "value": "one"}]}):
        with pytest.raises(ProviderError) as e:
            t.call("launcher.look_put", {"key": k, "changed_at": 1, "theme": "a", "prefs": [], "pictures": {}} | bad)
        assert e.value.code == "bad_params"
