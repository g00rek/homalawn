"""Gradient wallpapers from a theme's colours: deterministic, sized exactly, different per style
and per palette, and refused when the theme has no colours."""
import io

import pytest
from PIL import Image, ImageStat

from homalawn import gradients

TOKYO = {"mode": "dark", "accent": "#7aa2f7", "background": "#1a1b26", "darker_background": "#0e0e14",
         "lighter_background": "#24283b", "magenta": "#ad8ee6", "yellow": "#e0af68", "green": "#9ece6a",
         "cyan": "#449dab", "red": "#f7768e"}
LATTE = {"mode": "light", "accent": "#1e66f5", "background": "#eff1f5", "magenta": "#ea76cb",
         "yellow": "#df8e1d", "green": "#40a02b"}


def test_every_style_is_the_exact_size_and_the_same_bytes_every_time():
    for style, _ in gradients.STYLES:
        a = gradients.render(TOKYO, style, (240, 533))
        assert a == gradients.render(TOKYO, style, (240, 533))
        img = Image.open(io.BytesIO(a))
        assert img.format == "PNG" and img.size == (240, 533) and img.mode == "RGB"
    assert Image.open(io.BytesIO(gradients.render(TOKYO, "glow", (1080, 2400)))).size == (1080, 2400)


def test_styles_differ_and_follow_the_palette():
    pics = {s: gradients.image(TOKYO, s, (60, 133)).tobytes() for s, _ in gradients.STYLES}
    assert len(set(pics.values())) == len(gradients.STYLES)
    keys = {gradients.key(TOKYO, s) for s, _ in gradients.STYLES}
    assert len(keys) == len(gradients.STYLES) and all(len(k) == 16 for k in keys)
    assert gradients.key(TOKYO, "fade") != gradients.key(LATTE, "fade")
    dark = ImageStat.Stat(gradients.image(TOKYO, "vignette", (60, 133)).convert("L")).mean[0]
    light = ImageStat.Stat(gradients.image(LATTE, "vignette", (60, 133)).convert("L")).mean[0]
    assert dark < 80 < 150 < light


def test_the_glow_is_the_accent_near_the_bottom_and_dark_at_the_top():
    img = gradients.image(TOKYO, "glow", (100, 222))
    top, glow = img.getpixel((50, 5)), img.getpixel((50, 178))
    assert sum(top) < 90
    assert glow[2] > glow[0] and glow[2] > 180          # the blue accent


def test_a_theme_without_colours_has_no_gradients():
    assert not gradients.available({})
    assert gradients.available({"accent": "#ff0000"}) and gradients.available({"background": "#000000"})
    with pytest.raises(ValueError, match="no colours"):
        gradients.render({}, "fade", (10, 20))
    with pytest.raises(ValueError, match="unknown gradient"):
        gradients.render(TOKYO, "plaid", (10, 20))
