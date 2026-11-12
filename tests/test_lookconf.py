import json
import logging
import os
import stat

import pytest

from homalawn import lookconf
from homalawn.lookconf import LookSettings


def wp(home=None, lock=None, parallax=False):
    h = {"kind": "wallpaper", "style": None, "layer": 0} | (home or {})
    l = {"kind": "wallpaper", "style": None, "layer": 0} | (lock or {})
    return {"parallax": parallax, "home": h, "lock": l}


def png(tmp_path, name):
    f = tmp_path / name
    f.write_bytes(b"x")
    return str(f)


def test_defaults():
    s = LookSettings()
    assert s.source == "off" and s.theme is None and s.crops == {} and s.picks == {}
    assert s.wallpaper == {"parallax": False,
                           "home": {"kind": "wallpaper", "style": None, "layer": 0},
                           "lock": {"kind": "wallpaper", "style": None, "layer": 0}}
    assert s.sync == {"colors": True, "mode": True, "icons": False}
    assert s.mapping == {}
    a, b = LookSettings(), LookSettings()
    a.wallpaper["home"]["layer"] = 5
    assert b.wallpaper["home"]["layer"] == 0
    assert lookconf.screen_default() == {"kind": "wallpaper", "style": None, "layer": 0}


def test_round_trip(tmp_path):
    p = tmp_path / "look.json"
    s = LookSettings(source="chosen", theme="Nord",
                     wallpaper=wp({"layer": 10}, {"kind": "gradient", "style": "glow", "layer": -20},
                                  parallax=True),
                     crops={"/b/1.png": {"home": 0.2, "lock": None}},
                     picks={"nord": {"home": "/b/1.png", "lock": None, "by": "computer"}},
                     mapping={"accent_color": "accent"})
    lookconf.save({"Pixel": s}, p)
    assert lookconf.load(p) == {"Pixel": s}
    assert LookSettings.from_dict(s.to_dict()) == s
    assert lookconf.validate(s.to_dict()) == s


def test_missing_file(tmp_path):
    assert lookconf.load(tmp_path / "nope.json") == {}


def test_default_path_follows_homalawn_config(tmp_path, monkeypatch):
    monkeypatch.setenv("HOMALAWN_CONFIG", str(tmp_path / "config.toml"))
    lookconf.save({"P": LookSettings(source="follow")})
    assert (tmp_path / "look.json").exists()
    assert lookconf.load()["P"].source == "follow"


def test_screens_are_separate():
    s = LookSettings(wallpaper=wp({"kind": "gradient", "style": "glow", "layer": 7}, {"kind": "keep"}))
    assert s.screen("home") == {"kind": "gradient", "style": "glow", "layer": 7}
    s.screen("home")["layer"] = 1                      # a copy
    assert s.layer_of("home") == 7
    assert s.screen("lock")["kind"] == "keep" and s.layer_of("lock") == 0
    assert s.screens_of() == {"home": s.screen("home"), "lock": s.screen("lock")}


def test_pick_for(tmp_path):
    a, b = png(tmp_path, "a.png"), png(tmp_path, "b.png")
    s = LookSettings(picks={"nord": {"home": a, "lock": b, "by": "phone"},
                            "gone": {"home": str(tmp_path / "no.png"), "lock": None, "by": "phone"}})
    assert s.pick_for("Gone") is None and s.pick_for("Other") is None and s.pick_for(None) is None
    assert s.pick_for("Nord", "lock") == b and s.pick_for("Nord") == a
    s.picks["nord"]["lock"] = None
    assert s.pick_for("Nord", "lock") is None


def test_crop_for():
    s = LookSettings(crops={"/a": {"home": 0.2, "lock": 0.9}, "/b": {"home": 0.3, "lock": None}})
    assert s.crop_for("/zzz", "home") == 0.5 and s.crop_for("/zzz", "lock") == 0.5
    assert s.crop_for("/a", "home") == 0.2
    assert s.crop_for("/a", "lock") == 0.9
    assert s.crop_for("/b", "lock") == 0.3          # lock None -> home


def test_target():
    assert LookSettings().target() == "both"
    s = LookSettings(wallpaper=wp(lock={"kind": "keep"}))
    assert s.target() == "home"
    s.wallpaper["home"]["kind"] = "keep"
    assert s.target() is None
    s.wallpaper["lock"]["kind"] = "gradient"
    assert s.target() == "lock"


def test_follows_background(tmp_path):
    a = png(tmp_path, "a.png")
    s = LookSettings(picks={"nord": {"home": a, "lock": a, "by": "phone"}})
    assert not s.follows_background("Nord") and s.follows_background("Other")
    s.picks["nord"]["lock"] = None
    assert s.follows_background("Nord")             # the lock screen has no pick of its own
    s.wallpaper = wp({"kind": "gradient", "style": "glow"}, {"kind": "keep"})
    assert not s.follows_background("Other")
    s.wallpaper["lock"]["kind"] = "wallpaper"
    assert s.follows_background("Nord")             # lock has no pick of its own


def test_validation_messages():
    with pytest.raises(ValueError, match="source"):
        lookconf.validate({"source": "bogus", "wallpaper": wp()})
    with pytest.raises(ValueError, match="theme"):
        lookconf.validate({"source": "chosen", "wallpaper": wp()})
    assert lookconf.validate({"source": "chosen", "theme": "Nord", "wallpaper": wp()}).theme == "Nord"
    with pytest.raises(ValueError, match="keep, wallpaper or gradient"):
        lookconf.validate({"wallpaper": wp({"kind": "photo"})})
    with pytest.raises(ValueError, match="Unknown gradient"):
        lookconf.validate({"wallpaper": wp({"kind": "gradient", "style": "plaid"})})
    with pytest.raises(ValueError, match="lock colour layer must be between -100 and 100"):
        lookconf.validate({"wallpaper": wp(lock={"layer": 101})})
    with pytest.raises(ValueError, match="-100"):
        lookconf.validate({"wallpaper": wp({"layer": True})})
    with pytest.raises(ValueError, match="parallax, home, lock"):
        lookconf.validate({"wallpaper": {"parallax": False, "home": lookconf.screen_default()}})
    with pytest.raises(ValueError, match="parallax, home, lock"):
        lookconf.validate({"wallpaper": wp() | {"parallax": "yes"}})
    with pytest.raises(ValueError, match="parallax, home, lock"):
        lookconf.validate({"wallpaper": wp() | {"extra": 1}})
    with pytest.raises(ValueError, match="0 and 1"):
        lookconf.validate({"wallpaper": wp(), "crops": {"/a": {"home": 1.5, "lock": None}}})
    with pytest.raises(ValueError, match="0 and 1"):
        lookconf.validate({"wallpaper": wp(), "crops": {"/a": {"home": 0.5, "lock": -0.1}}})
    with pytest.raises(ValueError, match="home and/or lock"):
        lookconf.validate({"wallpaper": wp(), "picks": {"nord": {"by": "phone"}}})
    with pytest.raises(ValueError, match="home and/or lock"):
        lookconf.validate({"wallpaper": wp(), "picks": {"nord": {"home": "/a", "lock": 5, "by": "phone"}}})
    with pytest.raises(ValueError, match="home and/or lock"):
        lookconf.validate({"wallpaper": wp(), "picks": {"nord": {"home": "/a", "by": "me"}}})


def test_validate_normalises():
    s = lookconf.validate({"wallpaper": wp({"kind": "wallpaper", "style": "glow", "layer": 5.0},
                                           {"kind": "keep"})})
    assert s.wallpaper["home"] == {"kind": "wallpaper", "style": None, "layer": 5}
    assert s.wallpaper["lock"]["kind"] == "keep"


def test_atomic_save_no_temp_and_mode(tmp_path):
    p = tmp_path / "look.json"
    lookconf.save({"P": LookSettings()}, p)
    lookconf.save({"P": LookSettings(source="follow")}, p)
    assert os.listdir(tmp_path) == ["look.json"]
    assert stat.S_IMODE(p.stat().st_mode) == 0o600


def test_unknown_keys_ignored():
    s = LookSettings.from_dict({"source": "follow", "future": 1})
    assert s.source == "follow"
    assert lookconf.validate({"source": "off", "future": 1}).source == "off"


def test_bad_entry_gets_defaults_and_warning(tmp_path, caplog):
    p = tmp_path / "look.json"
    p.write_text(json.dumps({"Bad": {"source": "bogus"}, "Good": {"source": "follow"}}))
    with caplog.at_level(logging.WARNING, logger="homalawn.lookconf"):
        out = lookconf.load(p)
    assert out["Bad"] == LookSettings() and out["Good"].source == "follow"
    assert any("Bad" in r.message for r in caplog.records)


def test_corrupt_file_does_not_crash(tmp_path, caplog):
    p = tmp_path / "look.json"
    p.write_text("{nope")
    with caplog.at_level(logging.WARNING, logger="homalawn.lookconf"):
        assert lookconf.load(p) == {}
    assert caplog.records


@pytest.mark.parametrize("bad", [
    {"layer": 5}, {"crops": [1]}, {"layer": "x"}, {"sync": "x"},
    {"crops": {"/a": 3}}, {"theme": 7}, {"background": ["x"]},
    {"wallpaper": 5}, {"wallpaper": wp(), "picks": []},
])
def test_wrong_types_raise_value_error_and_load_survives(tmp_path, caplog, bad):
    with pytest.raises(ValueError):
        lookconf.validate(bad)
    p = tmp_path / "look.json"
    p.write_text(json.dumps({"Bad": bad, "Good": {"source": "follow"}}))
    with caplog.at_level(logging.WARNING, logger="homalawn.lookconf"):
        out = lookconf.load(p)
    assert out["Bad"] == LookSettings() and out["Good"].source == "follow"
    assert any("Bad" in r.message for r in caplog.records)


# -- migration from the older shape ---------------------------------------------------------------
def test_migrate_new_shape_is_untouched():
    d = LookSettings().to_dict()
    out, was_old = lookconf.migrate(d)
    assert out == d and not was_old


def test_migrate_target_both_with_layer_is_two_equal_screens():
    s = lookconf.validate({"source": "follow", "target": "both", "layer": {"home": 10, "lock": 10}})
    assert s.wallpaper == wp({"layer": 10}, {"layer": 10}) and s.source == "follow"
    assert "target" not in s.to_dict() and "layer" not in s.to_dict()


def test_migrate_target_lock_is_separate():
    s = lookconf.validate({"target": "lock"})
    assert s.wallpaper["home"]["kind"] == "keep" and s.wallpaper["lock"]["kind"] == "wallpaper"
    assert s.target() == "lock" and s.wallpaper["parallax"] is False


def test_migrate_screens_with_gradient_is_separate():
    s = lookconf.validate({"screens": {"home": {"kind": "gradient", "style": "glow"},
                                       "lock": {"kind": "wallpaper"}}, "layer": {"home": 0, "lock": 30}})
    assert s.wallpaper["home"] == {"kind": "gradient", "style": "glow", "layer": 0}
    assert s.wallpaper["lock"] == {"kind": "wallpaper", "style": None, "layer": 30}


def test_migrate_sync_wallpaper_off_keeps_both_screens():
    s = lookconf.validate({"target": "home", "sync": {"wallpaper": False, "colors": False}})
    assert s.wallpaper["home"]["kind"] == s.wallpaper["lock"]["kind"] == "keep"
    assert s.sync == {"colors": False, "mode": True, "icons": False}


def test_migrate_chosen_background_becomes_a_pick():
    s = lookconf.validate({"source": "chosen", "theme": "Nord", "background": "/b/2.png"})
    assert s.picks["nord"] == {"home": "/b/2.png", "lock": "/b/2.png", "by": "computer"}
    s = lookconf.validate({"source": "chosen", "theme": "Nord", "background": "/b/2.png",
                           "picks": {"nord": {"background": "/b/1.png", "by": "phone"}}})
    assert s.picks["nord"] == {"home": "/b/2.png", "lock": "/b/2.png", "by": "phone"}   # lock showed home


def test_migrate_picks_keep_their_own_lock():
    s = lookconf.validate({"picks": {"nord": {"background": "/a", "lock": "/b", "by": "phone"},
                                     "x": {"lock": "/z", "by": "phone"}}})
    assert s.picks["nord"] == {"home": "/a", "lock": "/b", "by": "phone"}
    assert s.picks["x"] == {"home": None, "lock": "/z", "by": "phone"}


def test_migrate_lock_crop_dropped_unless_separate():
    crops = {"/a": {"home": 0.2, "lock": 0.7}}
    s = lookconf.validate({"separate_lock_crop": False, "crops": crops})
    assert s.crops["/a"] == {"home": 0.2, "lock": None} and s.crop_for("/a", "lock") == 0.2
    s = lookconf.validate({"separate_lock_crop": True, "crops": crops})
    assert s.crops["/a"] == {"home": 0.2, "lock": 0.7}


def test_load_rewrites_old_entries_once(tmp_path):
    p = tmp_path / "look.json"
    p.write_text(json.dumps({"Old": {"source": "follow", "target": "lock", "layer": {"home": 0, "lock": 5}},
                             "Bad": {"source": "bogus"}}))
    out = lookconf.load(p)
    raw = json.loads(p.read_text())
    assert "target" not in raw["Old"] and "same" not in raw["Old"]["wallpaper"]
    assert raw["Bad"] == {"source": "bogus"}                    # an invalid entry is left as it was
    assert out["Old"] == lookconf.validate(raw["Old"])
    os.utime(p, (1_000_000, 1_000_000))
    lookconf.load(p)                                            # new shape now: not written again
    assert p.stat().st_mtime == 1_000_000


def test_load_new_shape_file_is_untouched(tmp_path):
    p = tmp_path / "look.json"
    lookconf.save({"P": LookSettings(source="follow")}, p)
    os.utime(p, (1_000_000, 1_000_000))
    lookconf.load(p)
    assert p.stat().st_mtime == 1_000_000


def test_failed_write_back_only_warns(tmp_path, caplog, monkeypatch):
    p = tmp_path / "look.json"
    p.write_text(json.dumps({"Old": {"source": "follow", "target": "home"}}))
    monkeypatch.setattr(lookconf, "_write", lambda *a, **k: (_ for _ in ()).throw(OSError("read-only")))
    with caplog.at_level(logging.WARNING, logger="homalawn.lookconf"):
        out = lookconf.load(p)
    assert out["Old"].wallpaper["lock"]["kind"] == "keep"
    assert any("rewritten" in r.message for r in caplog.records)


# -- final review: mixed versions, migration keeps the lock picture, backup -----------------------
def test_a_new_shape_wallpaper_next_to_old_keys_is_kept():
    wall = wp({"kind": "gradient", "style": "glow", "layer": 44}, {"kind": "wallpaper"})
    s = lookconf.validate({"source": "chosen", "theme": "Ethereal", "wallpaper": wall,
                           "layer": {"home": 0, "lock": 0}, "target": "both", "separate_lock_crop": False,
                           "background": "/b/x.png", "screens": None, "sync": {"wallpaper": True}})
    assert s.wallpaper == wall
    d = s.to_dict()
    assert not any(k in d for k in ("target", "layer", "screens", "background", "separate_lock_crop"))
    assert "wallpaper" not in d["sync"]


def test_a_new_shape_wallpaper_with_old_picks_gets_the_new_picks():
    wall = wp({"layer": 30})
    s = lookconf.validate({"wallpaper": wall, "target": "both",
                           "picks": {"nord": {"background": "/a", "lock": "/b", "by": "phone"},
                                     "x": {"background": "/c", "by": "computer"}}})
    assert s.wallpaper == wall
    assert s.picks["nord"] == {"home": "/a", "lock": "/b", "by": "phone"}
    assert s.picks["x"] == {"home": "/c", "lock": "/c", "by": "computer"}   # old rule: lock = home


def test_load_rewrites_a_mixed_entry_and_keeps_its_wallpaper(tmp_path):
    p = tmp_path / "look.json"
    wall = wp({"kind": "gradient", "style": "glow", "layer": 44})
    p.write_text(json.dumps({"M": {"source": "follow", "wallpaper": wall, "target": "home"}}))
    out = lookconf.load(p)
    raw = json.loads(p.read_text())
    assert "target" not in raw["M"] and raw["M"]["wallpaper"] == wall and out["M"].wallpaper == wall


def test_migrate_separate_lock_without_its_own_pick_shows_the_home_pick(tmp_path):
    meadow = png(tmp_path, "2-meadow.jpg")
    s = lookconf.validate({"source": "chosen", "theme": "Ethereal", "target": "lock",
                           "picks": {"ethereal": {"background": meadow, "by": "computer"}}})
    assert s.pick_for("Ethereal", "lock") == meadow
    s = lookconf.validate({"source": "chosen", "theme": "Ethereal", "target": "both",
                           "layer": {"home": 30, "lock": 0},
                           "picks": {"ethereal": {"background": meadow, "by": "computer"}}})
    assert s.pick_for("Ethereal", "home") == s.pick_for("Ethereal", "lock") == meadow
    s = lookconf.validate({"source": "chosen", "theme": "Ethereal", "target": "lock",
                           "background": meadow})                  # the chosen background, too
    assert s.pick_for("Ethereal", "lock") == meadow


def test_load_keeps_one_backup_of_the_old_file(tmp_path):
    p = tmp_path / "look.json"
    old = json.dumps({"Old": {"source": "follow", "target": "lock"}})
    p.write_text(old)
    lookconf.load(p)
    b = tmp_path / "look.json.v1"
    assert b.read_text() == old and stat.S_IMODE(b.stat().st_mode) == 0o600
    p.write_text(json.dumps({"Old": {"source": "follow", "target": "home"}}))
    lookconf.load(p)
    assert b.read_text() == old                                 # written once, never replaced


def test_load_new_shape_file_makes_no_backup(tmp_path):
    p = tmp_path / "look.json"
    lookconf.save({"P": LookSettings(source="follow")}, p)
    lookconf.load(p)
    assert not (tmp_path / "look.json.v1").exists()


def test_an_infinite_layer_gets_defaults_not_a_crash(tmp_path, caplog):
    p = tmp_path / "look.json"
    p.write_text('{"P": {"source": "follow", "target": "both", "layer": {"home": Infinity, "lock": 0}},'
                 ' "Q": {"source": "follow", "wallpaper": {"same": true, "parallax": false,'
                 ' "home": {"kind": "wallpaper", "layer": Infinity}, "lock": {"kind": "wallpaper"}}}}')
    with caplog.at_level(logging.WARNING, logger="homalawn.lookconf"):
        out = lookconf.load(p)
    assert out["P"] == LookSettings() and out["Q"] == LookSettings()
    with pytest.raises(ValueError):
        lookconf.validate({"target": "both", "layer": {"home": float("inf")}})


# -- 2026-10-17: "Same on both" dropped; the two screens are always set separately ----------------
def test_old_same_on_both_loads_as_two_equal_screens(tmp_path):
    a = png(tmp_path, "a.png")
    old = {"source": "follow", "wallpaper": {"same": True, "parallax": True,
                                             "home": {"kind": "gradient", "style": "glow", "layer": 30},
                                             "lock": {"kind": "keep", "style": None, "layer": 0}},
           "crops": {a: {"home": 0.2, "lock": 0.7}},
           "picks": {"nord": {"home": a, "lock": None, "by": "phone"},
                     "x": {"home": None, "lock": "/z", "by": "phone"}}}
    s = lookconf.validate(old)
    assert s.wallpaper == {"parallax": True, "home": {"kind": "gradient", "style": "glow", "layer": 30},
                           "lock": {"kind": "gradient", "style": "glow", "layer": 30}}
    assert s.picks["nord"] == {"home": a, "lock": a, "by": "phone"}
    assert s.pick_for("Nord", "lock") == s.pick_for("Nord", "home") == a
    assert s.crops[a] == {"home": 0.2, "lock": None} and s.crop_for(a, "lock") == 0.2
    assert "x" not in s.picks                    # it showed nothing of its own: the lock used home's


def test_old_separate_just_loses_the_key():
    wall = wp({"layer": 5}, {"kind": "keep"})
    s = lookconf.validate({"wallpaper": {"same": False, **wall}, "crops": {"/a": {"home": 0.2, "lock": 0.7}},
                           "picks": {"nord": {"home": "/a", "lock": "/b", "by": "phone"}}})
    assert s.wallpaper == wall and s.crops["/a"]["lock"] == 0.7 and s.picks["nord"]["lock"] == "/b"


def test_load_rewrites_an_old_same_entry_once(tmp_path):
    p = tmp_path / "look.json"
    wall = wp({"layer": 10}, {"kind": "keep"})
    p.write_text(json.dumps({"S": {"source": "follow", "wallpaper": {"same": True, **wall}}}))
    out = lookconf.load(p)
    raw = json.loads(p.read_text())
    assert raw["S"]["wallpaper"] == wp({"layer": 10}, {"layer": 10})
    assert out["S"].wallpaper == raw["S"]["wallpaper"]
    os.utime(p, (1_000_000, 1_000_000))
    lookconf.load(p)                                            # current shape now: not written again
    assert p.stat().st_mtime == 1_000_000


def test_parallax_only_with_a_theme_wallpaper_on_the_home_screen():
    s = LookSettings(wallpaper=wp(parallax=True))
    assert s.parallax_on()
    s.wallpaper["home"] = {"kind": "gradient", "style": "glow", "layer": 0}
    assert not s.parallax_on() and s.wallpaper["parallax"] is True   # remembered, not used
    s.wallpaper["home"]["kind"] = "keep"
    assert not s.parallax_on()
    assert not LookSettings(wallpaper=wp(parallax=False)).parallax_on()


# -- monochrome icon colour per theme (2026-10-17) ----------------------------------------------
def test_icon_colors_default_to_none_and_round_trip(tmp_path):
    assert LookSettings().icon_colors == {} and LookSettings().icon_color_for("Nord") is None
    p = tmp_path / "look.json"
    s = lookconf.validate({"source": "follow", "wallpaper": wp(), "icon_colors": {"tokyo-night": "#9ECE6A"}})
    assert s.icon_colors == {"tokyo-night": "#9ece6a"}            # stored in lower case
    assert s.icon_color_for("Tokyo Night") == "#9ece6a" and s.icon_color_for("Nord") is None
    lookconf.save({"P": s}, p)
    assert lookconf.load(p)["P"].icon_colors == {"tokyo-night": "#9ece6a"}


def test_a_file_from_before_icon_colors_loads_and_is_not_rewritten(tmp_path):
    p = tmp_path / "look.json"
    d = LookSettings(source="follow").to_dict()
    del d["icon_colors"]
    p.write_text(json.dumps({"P": d}))
    os.utime(p, (1_000_000, 1_000_000))
    assert lookconf.load(p)["P"].icon_colors == {}
    assert p.stat().st_mtime == 1_000_000 and not (tmp_path / "look.json.v1").exists()


@pytest.mark.parametrize("bad", [{"nord": "red"}, {"nord": "#12345"}, {"nord": None}, {"": "#ffffff"},
                                 {"nord": "#gggggg"}])
def test_a_bad_icon_colour_is_refused(bad):
    with pytest.raises(ValueError, match="icon colour"):
        lookconf.validate({"wallpaper": wp(), "icon_colors": bad})
    with pytest.raises(ValueError, match="icon_colors"):
        lookconf.validate({"wallpaper": wp(), "icon_colors": ["#ffffff"]})
