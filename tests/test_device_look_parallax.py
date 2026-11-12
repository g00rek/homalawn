"""Parallax on the test AVD (HOMALAWN_SERIAL=emulator-5560): READY, NOT YET RUN on a device.

Two tests prove what the design
could not check from source, plus the settings round trip:

1. Parallax on (first test): the launcher preference `pref_wallpaperScrolling` reads back true, and
   the home picture the phone stored is exactly `look.parallax_size(screen)` (twice the screen's
   width, its height). `dumpsys wallpaper` must show a crop or size of that wide shape (twice the
   screen's aspect, within 6 %); the test skips only when no crop size can be read from the dump at
   all (format unknown: the dump is saved for evidence), never when a size is read and is wrong.
2. Also in the first test: page i of N shows the slice that starts at i / (max(N, 4) - 1) of the picture's extra width: page 0
   is the LEFT screen-width, and with three pages the last one is at 2/3. Checked by comparing each
   screenshot's wallpaper (the median colour of every column, so icons do not matter) with the
   matching slice of the stored picture, which is a left-to-right ramp; the best match among the
   slices at 0, 1/3, 2/3 and 1 must be the expected one.
3. Parallax off (second test): the preference reads back false, the stored picture is exactly the screen's size, and
   page 0 and the next page show the same wallpaper (nothing scrolls).

The window is not needed: the test drives the service (`look.set`) the way the window's Apply does.
Run it only when the emulator is free:
    HOMALAWN_SERIAL=emulator-5560 .venv/bin/pytest tests/test_device_look_parallax.py -q -s
Screenshots go to HOMALAWN_APPLY_SHOTS (default /tmp/claude-1000/homalawn-apply-shots)."""
import io
import os
import time

import pytest
from PIL import Image

from homalawn import look, lookconf, prefs as prefs_mod
from homalawn.session import WALLPAPER_FILE
from test_device_shell_apply import SHOTS, Window, phone  # noqa: F401  (phone: the fixture)

pytestmark = pytest.mark.device
SERIAL = os.environ.get("HOMALAWN_SERIAL")
if not SERIAL:
    pytest.skip("HOMALAWN_SERIAL not set", allow_module_level=True)

CANDIDATES = (0, 1 / 3, 2 / 3, 1)      # where a slice can start, as a share of the extra width
COLUMNS = 24


def ramp(path, size):
    """A picture of exactly `size`: red grows from 0 at the left edge to 255 at the right."""
    w, h = size
    img = Image.new("RGB", size)
    img.putdata([(int(255 * x / (w - 1)), 90, int(255 * y / (h - 1))) for y in range(h) for x in range(w)])
    img.save(path, "PNG")
    return path


def profile(img):
    """The median colour of each of COLUMNS columns, left to right (robust against icons)."""
    small = img.convert("RGB").resize((COLUMNS, 48), Image.BOX)
    px = small.load()
    out = []
    for x in range(COLUMNS):
        col = [px[x, y] for y in range(48)]
        out.append(tuple(sorted(c[i] for c in col)[24] for i in range(3)))
    return out


def distance(a, b):
    return sum(abs(p - q) for u, v in zip(a, b) for p, q in zip(u, v)) / (3 * COLUMNS)


def slice_profile(stored, start_share, screen_w):
    x = round(start_share * (stored.width - screen_w))
    return profile(stored.crop((x, 0, x + screen_w, stored.height)))


def expected_share(page, pages):
    """Lawnchair's WallpaperOffsetInterpolator: page i of N sits at i / (max(N, 4) - 1)."""
    return page / (max(pages, 4) - 1)


def check_dumpsys_crops(rig):
    """`dumpsys wallpaper` must show a crop or size whose shape is the wide picture (twice the screen's
    aspect, within 6 %). The dump's format is not known yet: when nothing in it can be read that way,
    the lines go to a file and this part of the test skips (evidence gathering)."""
    import re
    out = rig.d.shell("dumpsys", "wallpaper")
    SHOTS.mkdir(parents=True, exist_ok=True)
    (SHOTS / "dumpsys-wallpaper.txt").write_text(out)
    want = 2 * rig.screen[0] / rig.screen[1]
    shapes = []
    for line in out.splitlines():
        if "crop" not in line.lower():
            continue
        for a, b in re.findall(r"(\d+)\s*x\s*(\d+)", line):
            if int(b):
                shapes.append(int(a) / int(b))
        for l, t, r, b in re.findall(r"(-?\d+)\s*,\s*(-?\d+)\s*-\s*(-?\d+)\s*,\s*(-?\d+)", line):
            if int(b) > int(t):
                shapes.append((int(r) - int(l)) / (int(b) - int(t)))
    if not shapes:
        return ("evidence gathering - format unknown: no readable crop size in `dumpsys wallpaper`; "
                f"the dump is in {SHOTS / 'dumpsys-wallpaper.txt'}")
    assert any(abs(a - want) / want < 0.06 for a in shapes), \
        f"no crop in the dump has the wide shape {want:.3f}: found {shapes}"
    return None


class Rig:
    def __init__(self, d, la, link_transport, tmp_path):
        self.d, self.la, self.t = d, la, link_transport
        self.w = Window(tmp_path, link_transport.label)
        self.screen = la.screen_size()
        self.picture = ramp(tmp_path / "ramp.png", look.parallax_size(self.screen))
        self.w.wait_connected()

    def apply(self, parallax):
        wall = lookconf.wallpaper_default()
        wall["parallax"] = parallax
        st = lookconf.LookSettings(
            source="chosen", theme="Catppuccin", wallpaper=wall,
            crops={str(self.picture): {"home": 0.5, "lock": None}},
            picks={"catppuccin": {"home": str(self.picture), "lock": None, "by": "computer"}})
        r = self.w.ask("look.set", phone=self.w.name, settings=st.to_dict())
        assert r["text"].startswith("Wallpaper & style applied"), r
        time.sleep(3)

    def pref(self):
        v = prefs_mod.current(self.t)["shared"][look.PARALLAX_PREF]["value"]
        return str(v).lower() == "true"

    def stored(self):
        return Image.open(io.BytesIO(self.la._backup_files(("home",))[WALLPAPER_FILE]))

    def pages(self):
        return len(self.w.ask("home.get", phone=self.w.name)["screens"])

    def home(self):
        self.d.shell("input", "keyevent", "KEYCODE_HOME")
        time.sleep(1.5)
        self.d.shell("input", "keyevent", "KEYCODE_HOME")      # a second press goes to page 0
        time.sleep(2)

    def next_page(self):
        w, h = self.screen
        self.d.shell("input", "swipe", str(int(w * 0.85)), str(int(h * 0.4)), str(int(w * 0.15)),
                     str(int(h * 0.4)), "250")
        time.sleep(2.5)

    def shot(self, name):
        data = self.d.screencap()
        SHOTS.mkdir(parents=True, exist_ok=True)
        (SHOTS / name).write_bytes(data)
        return Image.open(io.BytesIO(data))

    def close(self):
        self.w.close()


@pytest.fixture
def rig(phone, link_transport, tmp_path):   # noqa: F811
    d, la = phone
    r = Rig(d, la, link_transport, tmp_path)
    try:
        yield r
    finally:
        r.home()
        r.close()


def test_parallax_on_stores_a_wide_picture_and_pages_show_the_right_slices(rig):
    rig.apply(True)
    assert rig.pref() is True, "pref_wallpaperScrolling did not read back true"
    stored = rig.stored()
    assert stored.size == look.parallax_size(rig.screen), (stored.size, rig.screen)

    unknown_dump = check_dumpsys_crops(rig)
    pages = rig.pages()
    rig.home()
    seen = []
    for page in range(min(pages, 3)):
        if page:
            rig.next_page()
        got = profile(rig.shot(f"parallax-page{page}.png"))
        scores = [distance(got, slice_profile(stored, c, rig.screen[0])) for c in CANDIDATES]
        best = CANDIDATES[scores.index(min(scores))]
        want = expected_share(page, pages)
        label = f"page {page} of {pages}: best slice at {best:.2f}, expected {want:.2f}, errors {scores}"
        print(label)
        assert abs(best - want) < 0.01 and min(scores) < 60, label
        seen.append(got)
    assert pages >= 2, "the home screen has one page: cannot prove scrolling (add a page and rerun)"
    assert distance(seen[0], seen[1]) > 8, "page 0 and page 1 show the same wallpaper"
    notes = ([unknown_dump] if unknown_dump else []) + (
        ["only %d pages: the last-page-at-2/3 check needs three" % pages] if pages < 3 else [])
    if notes:                                   # everything else above passed; say what is unproven
        pytest.skip("; ".join(notes))


def test_parallax_off_stores_a_screen_sized_picture_and_nothing_scrolls(rig):
    rig.apply(False)
    assert rig.pref() is False, "pref_wallpaperScrolling did not read back false"
    stored = rig.stored()
    assert stored.size == tuple(rig.screen), (stored.size, rig.screen)
    if rig.pages() < 2:
        pytest.fail("the home screen has one page: cannot prove nothing scrolls (add a page and rerun)")
    rig.home()
    first = profile(rig.shot("parallax-off-page0.png"))
    # off = the LEFT edge: page 0 shows the whole screen-sized picture
    own = distance(first, slice_profile(stored, 0, rig.screen[0]))
    assert own < 60, f"page 0 does not show the stored picture (distance {own})"
    rig.next_page()
    second = profile(rig.shot("parallax-off-page1.png"))
    assert distance(first, second) < 8, "the wallpaper moved with parallax off"
