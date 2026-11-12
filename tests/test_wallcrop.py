import io

import pytest
from PIL import Image

from homalawn.wallcrop import cover_box, crop_center, crop_cover


def _png(w, h, mode="RGB", fill=(0, 0, 0)):
    out = io.BytesIO()
    Image.new(mode, (w, h), fill).save(out, "PNG")
    return out.getvalue()


def test_landscape_picture_keeps_the_full_height_and_the_middle():
    left, top, right, bottom = cover_box(3840, 2160, 1080, 2340)
    assert (top, bottom) == (0, 2160)  # the height fills the screen
    assert right - left == pytest.approx(2160 * 1080 / 2340)  # the screen's proportions
    assert left == pytest.approx(3840 - right)  # centred


def test_portrait_picture_taller_than_the_screen_keeps_the_full_width():
    left, top, right, bottom = cover_box(1000, 3000, 1080, 2340)
    assert (left, right) == pytest.approx((0, 1000))
    assert bottom - top == pytest.approx(1000 * 2340 / 1080)
    assert top == pytest.approx(3000 - bottom)


def test_picture_of_the_screen_size_is_left_whole():
    assert cover_box(1080, 2340, 1080, 2340) == (0, 0, 1080, 2340)


def test_small_picture_is_scaled_up_to_cover():
    left, top, right, bottom = cover_box(540, 540, 1080, 2340)
    assert (top, bottom) == (0, 540) and right - left == pytest.approx(540 * 1080 / 2340)


def test_crop_output_is_exactly_the_screen_and_shows_the_middle():
    img = Image.new("RGB", (3840, 2160), (255, 0, 0))  # red sides, a green band in the middle
    img.paste((0, 255, 0), (1800, 0, 2040, 2160))
    out = io.BytesIO()
    img.save(out, "JPEG", quality=95)
    got = Image.open(io.BytesIO(crop_center(out.getvalue(), 1080, 2340)))
    assert got.format == "PNG" and got.size == (1080, 2340)
    r, g, b = got.convert("RGB").getpixel((540, 1170))
    assert g > 200 and r < 60  # the centre of the picture is the centre of the screen
    r, g, b = got.convert("RGB").getpixel((5, 1170))
    assert r > 200 and g < 60


def test_exact_size_picture_is_sent_unchanged_in_size():
    got = Image.open(io.BytesIO(crop_center(_png(1080, 2340), 1080, 2340)))
    assert got.size == (1080, 2340)


def test_palette_picture_becomes_plain_colour():
    got = Image.open(io.BytesIO(crop_center(_png(200, 100, "P"), 1080, 2340)))
    assert got.size == (1080, 2340) and got.mode in ("RGB", "RGBA")


def test_not_a_picture_is_a_plain_error():
    with pytest.raises(ValueError, match="cannot read the picture"):
        crop_center(b"not an image", 1080, 2340)


def test_offset_moves_the_frame_along_the_slack_axis():
    l0, _, r0, _ = cover_box(3840, 2160, 1080, 2340, 0.0)
    assert l0 == 0
    l1, _, r1, _ = cover_box(3840, 2160, 1080, 2340, 1.0)
    assert r1 == pytest.approx(3840)
    l5, _, r5, _ = cover_box(3840, 2160, 1080, 2340, 0.5)
    assert l5 == pytest.approx(3840 - r5)
    assert r0 - l0 == pytest.approx(r1 - l1)


def test_offset_is_clamped():
    assert cover_box(3840, 2160, 1080, 2340, -3) == cover_box(3840, 2160, 1080, 2340, 0.0)
    assert cover_box(3840, 2160, 1080, 2340, 7) == cover_box(3840, 2160, 1080, 2340, 1.0)


def test_offset_applies_vertically_for_tall_pictures():
    _, top, _, bottom = cover_box(1000, 3000, 1080, 2340, 1.0)
    assert bottom == pytest.approx(3000)


def test_crop_cover_offset_picks_the_side():
    img = Image.new("RGB", (40, 10), (255, 0, 0))
    img.paste((0, 255, 0), (30, 0, 40, 10))
    out = io.BytesIO()
    img.save(out, "PNG")
    got = Image.open(io.BytesIO(crop_cover(out.getvalue(), 10, 10, 1.0))).convert("RGB")
    assert got.getpixel((9, 5))[1] > 200
    got = Image.open(io.BytesIO(crop_cover(out.getvalue(), 10, 10, 0.0))).convert("RGB")
    assert got.getpixel((9, 5))[0] > 200
