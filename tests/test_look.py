import io
from pathlib import Path

import pytest
from PIL import Image

from homalawn import look

FIX = Path(__file__).parent / "fixtures" / "omarchy"
PALETTE = {"mode": "dark", "accent": "#7aa2f7", "background": "#1a1b26", "lighter_background": "#24283b"}


def _png(color, size=(8, 6)):
    out = io.BytesIO()
    Image.new("RGB", size, color).save(out, "PNG")
    return out.getvalue()


class _Run:
    def __init__(self, listed="Alpha\nBeta\n", dirs=None):
        self.calls = []
        self.listed = listed
        self.dirs = dirs or {"alpha": FIX / "themes" / "alpha", "beta": FIX / "themes" / "beta"}

    def __call__(self, cmd, **kw):
        self.calls.append(cmd)

        class R:
            returncode = 0
            stderr = ""
            stdout = ""

        r = R()
        if cmd[:3] == ["omarchy", "theme", "list"]:
            r.stdout = self.listed
        elif cmd[:3] == ["omarchy", "theme", "dir"]:
            # like the real command: it wants the slug; any other name gives a path that does not exist
            r.stdout = f"{self.dirs.get(cmd[3], FIX / 'themes' / cmd[3])}\n"
        return r


@pytest.fixture
def home(tmp_path):
    h = tmp_path / "home"
    (h / ".config/omarchy/backgrounds/alpha").mkdir(parents=True)
    src = FIX / "user-backgrounds/alpha/3.png"
    (h / ".config/omarchy/backgrounds/alpha/3.png").write_bytes(src.read_bytes())
    return h


def test_list_themes_merges_backgrounds(home):
    run = _Run()
    themes = look.list_themes(run=run, home=home)
    assert [t.name for t in themes] == ["Alpha", "Beta"]
    a, b = themes
    assert [p.name for p in a.backgrounds] == ["1.png", "2.jpg", "3.png"]
    assert a.preview == FIX / "themes/alpha/preview.png"
    assert a.colors["accent"] == "#7aa2f7"
    assert b.backgrounds == [] and b.preview is None and b.colors["mode"] == "light"
    assert ["omarchy", "theme", "list"] in run.calls


def test_a_listed_theme_without_a_folder_is_an_error_not_an_empty_list(home, monkeypatch):
    # what the service saw when it started before the session's OMARCHY_PATH reached systemd:
    # `omarchy theme dir tokyo-night` printed "/themes/tokyo-night"
    monkeypatch.delenv("OMARCHY_PATH", raising=False)
    run = _Run(listed="Alpha\nTokyo Night\n", dirs={"alpha": FIX / "themes" / "alpha",
                                                     "tokyo-night": Path("/themes/tokyo-night")})
    with pytest.raises(look.ThemeDirMissing, match=r"tokyo-night` gives /themes/tokyo-night.*OMARCHY_PATH=<unset>"):
        look.list_themes(run=run, home=home)


def test_theme_named_matches_display_name_or_dir_case_insensitive(home):
    assert look.theme_named("ALPHA", run=_Run(), home=home).name == "Alpha"
    assert look.theme_named("beta", run=_Run(), home=home).dir.name == "beta"
    assert look.theme_named("nope", run=_Run(), home=home) is None


def test_list_themes_asks_for_the_dir_by_slug(tmp_path, home):
    d = tmp_path / "themes" / "tokyo-night"
    d.mkdir(parents=True)
    (d / "colors.toml").write_text('accent = "#7aa2f7"\n')
    run = _Run(listed="Tokyo Night\n", dirs={"tokyo-night": d})
    t, = look.list_themes(run=run, home=home)
    assert (t.name, t.slug, t.dir) == ("Tokyo Night", "tokyo-night", d)
    assert ["omarchy", "theme", "dir", "tokyo-night"] in run.calls


def test_theme_named_matches_the_slug_from_theme_name_file(tmp_path, home):
    d = tmp_path / "themes" / "tokyo-night"
    d.mkdir(parents=True)
    (d / "colors.toml").write_text('accent = "#7aa2f7"\n')
    run = lambda: _Run(listed="Tokyo Night\n", dirs={"tokyo-night": d})
    assert look.theme_named("tokyo-night", run=run(), home=home).name == "Tokyo Night"
    assert look.theme_named("TOKYO NIGHT", run=run(), home=home).slug == "tokyo-night"


def test_theme_slug_defaults_from_the_name(tmp_path):
    assert look.Theme("Arc Blueberry", tmp_path, {}, [], None).slug == "arc-blueberry"


def test_current_follows_the_background_symlink(tmp_path, monkeypatch):
    monkeypatch.delenv("HOMALAWN_OMARCHY_STATE", raising=False)
    cur = tmp_path / ".local/state/omarchy/current"
    cur.mkdir(parents=True)
    (cur / "theme.name").write_text("alpha\n")
    real = tmp_path / "pic.png"
    real.write_bytes(_png((1, 2, 3)))
    (cur / "background").symlink_to(real)
    assert look.current(tmp_path) == ("alpha", real.resolve())
    assert look.current(tmp_path / "empty") == (None, None)


def test_current_honours_the_state_env(tmp_path, monkeypatch):
    cur = tmp_path / "state"
    cur.mkdir()
    (cur / "theme.name").write_text("beta")
    monkeypatch.setenv("HOMALAWN_OMARCHY_STATE", str(cur))
    assert look.current(tmp_path / "elsewhere") == ("beta", None)


def test_layer_darkens_and_lightens():
    white = Image.new("RGB", (2, 2), (255, 255, 255))
    assert look.layer(white, 50).getpixel((0, 0)) == pytest.approx((128, 128, 128), abs=1)
    black = Image.new("RGB", (2, 2), (0, 0, 0))
    assert look.layer(black, -50).getpixel((0, 0)) == pytest.approx((128, 128, 128), abs=1)


def test_layer_zero_returns_identical_bytes():
    data = _png((10, 20, 30))
    assert look.render(data, (8, 6), 0.5, 0) == look.render(data, (8, 6), 0.5, 0)
    img = Image.open(io.BytesIO(data))
    assert look.layer(img, 0).tobytes() == img.tobytes()


def test_render_crops_then_layers():
    got = Image.open(io.BytesIO(look.render(_png((255, 255, 255), (16, 6)), (4, 6), 0.0, 100)))
    assert got.size == (4, 6) and got.convert("RGB").getpixel((0, 0)) == (0, 0, 0)


def test_palette_prefs_exact():
    assert look.palette_prefs(PALETTE) == [
        ("accent_color", "custom|#ff7aa2f7"),
        ("folder_color", "custom|#ff24283b"),
        ("hotseat_bg_color", "custom|#991a1b26"),
        ("app_drawer_bg_color", "custom|#ff1a1b26"),
    ]


def test_palette_prefs_skip_missing_keys():
    got = look.palette_prefs({"accent": "#112233"})
    assert got == [("accent_color", "custom|#ff112233")]


def test_palette_prefs_mapping_override():
    got = dict(look.palette_prefs(PALETTE, {"accent_color": "#00ff00", "folder_color": "background",
                                            "hotseat_bg_color": "#80102030"}))
    assert got["accent_color"] == "custom|#ff00ff00"
    assert got["folder_color"] == "custom|#ff1a1b26"
    assert got["hotseat_bg_color"] == "custom|#80102030"
    assert got["app_drawer_bg_color"] == "custom|#ff1a1b26"


def test_mode_pref():
    assert look.mode_pref(PALETTE) == ("pref_launcherTheme", "dark")
    assert look.mode_pref({"mode": "light"}) == ("pref_launcherTheme", "light")
    assert look.mode_pref({}) is None


def test_theme_without_colors_toml_is_listed_with_no_colours(tmp_path, home):
    d = tmp_path / "themes" / "archwave"
    (d / "backgrounds").mkdir(parents=True)
    (d / "backgrounds" / "1.png").write_bytes(_png((1, 2, 3)))
    (d / "alacritty.toml").write_text("[colors]\n")
    run = _Run(listed="Archwave\n", dirs={"archwave": d})
    t, = look.list_themes(run=run, home=home)
    assert (t.name, t.colors, [p.name for p in t.backgrounds]) == ("Archwave", {}, ["1.png"])
    assert look.palette_prefs(t.colors) == [] and look.mode_pref(t.colors) is None


def test_parallax_size_is_twice_the_screen_width_and_the_same_height():
    assert look.parallax_size((1080, 2400)) == (2160, 2400)
    assert look.PARALLAX_PREF == "pref_wallpaperScrolling"


# -- monochrome icon colours -----------------------------------------------------------------
TOKYO = {"mode": "dark", "accent": "#7aa2f7", "background": "#1a1b26", "lighter_background": "#24283b",
         "foreground": "#a9b1d6", "dark_foreground": "#565f89", "blue": "#7aa2f7", "red": "#f7768e",
         "color0": "#24283b", "color1": "#F7768E", "cursor": "rgb(1 2 3)"}


def test_icon_plate_dark_is_the_lighter_background_else_the_background():
    assert look.icon_plate(TOKYO) == "#24283b"
    assert look.icon_plate({"mode": "dark", "background": "#1C1E26"}) == "#1c1e26"


def test_icon_plate_light_is_the_lighter_of_background_and_lighter_background():
    latte = {"mode": "light", "background": "#eff1f5", "lighter_background": "#dce0e8"}
    assert look.icon_plate(latte) == "#eff1f5"
    assert look.icon_plate({"mode": "light", "lighter_background": "#ffffff", "background": "#f0f0f0"}) == "#ffffff"
    assert look.icon_plate({"background": "#fafafa"}) == "#fafafa"          # no mode: light by its background
    assert look.icon_plate({}) is None


def test_icon_palette_accent_first_each_value_once_faint_ones_left_out():
    # blue repeats the accent, color1 repeats red (case aside), color0 is the plate itself, cursor is
    # not a hex colour
    assert look.icon_palette(TOKYO) == ["#7aa2f7", "#a9b1d6", "#565f89", "#f7768e"]
    assert look.icon_palette({}) == []
    # near-white shades look alike as swatches: the first stands for them
    assert look.icon_palette({"mode": "dark", "background": "#000000", "accent": "#fcee0a", "foreground": "#e8ecf2",
                              "bright_foreground": "#ffffff", "light_foreground": "#f0f4f9"}) \
        == ["#fcee0a", "#e8ecf2", "#ffffff"]


def test_icon_prefs_symbol_is_the_choice_else_the_accent_and_the_plate_goes_with_it():
    assert look.icon_prefs(TOKYO) == [("homalawn_icon_color", "#7aa2f7"), ("homalawn_icon_bg_color", "#24283b")]
    assert look.icon_prefs(TOKYO, "#F7768E")[0] == ("homalawn_icon_color", "#f7768e")
    assert look.icon_prefs({"accent": "#7aa2f7"}) == []         # no background to make a plate from


def test_no_omarchy_command_means_no_themes():
    from homalawn import look

    def run(argv, **kw):
        raise FileNotFoundError(argv[0])
    assert look.list_themes(run=run) == []
