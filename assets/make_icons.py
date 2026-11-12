"""Build Homa Lawn's icon set from homalawn-icon-source.png.

    uv run --with numpy --with scipy python assets/make_icons.py [--lawnchair lawnchair]

The source is a full-bleed square picture: a sunset sky with a pale sun, a navy hill, and in the
middle a dark phone (a rounded rectangle with a white outline and one pink "app" square).

Writes, next to this file:
  homalawn-scene.png   the picture with the phone taken out (sky, sun and hill filled in under it)
  homalawn-mark.png    the bar mark at 256 px, white: only the phone's outline and app square
the Omarchy plugin's copies in src/homalawn/shell/HomaLawn/assets/ (the mark as two white masks,
outline and app square, at 16 to 256 px, and the whole picture as a small rounded square for the
window header), and, when given a Lawnchair checkout (--lawnchair DIR), the launcher icon there.
"""

import argparse
from dataclasses import dataclass
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw
from scipy import ndimage

HERE = Path(__file__).resolve().parent
SHELL = HERE.parent / "src/homalawn/shell/HomaLawn/assets"


@dataclass
class Phone:
    """The phone's shape in the source, in source pixels (edges are pixel boundaries)."""
    x0: float
    y0: float
    x1: float
    y1: float
    stroke: float  # the white outline's width
    radius: float  # the outline's outer corner radius
    app: tuple[float, float, float, float]  # the pink square, x0, y0, x1, y1
    app_radius: float


class Source:
    """The picture split into the phone and the scene behind it."""

    def __init__(self, img: Image.Image):
        self.rgb = rgb = np.asarray(img.convert("RGB"), dtype=np.float64)
        lo, hi = rgb.min(axis=2), rgb.max(axis=2)
        white = (lo > 200) & (hi - lo < 40)
        lab, n = ndimage.label(white)
        sizes = ndimage.sum(white, lab, index=np.arange(1, n + 1))
        self.outline = lab == 1 + int(np.argmax(sizes))  # the phone's outline: the one big white ring
        self.core = ndimage.binary_fill_holes(self.outline)  # the whole phone, outline included
        self.white = np.median(rgb[ndimage.binary_erosion(self.outline, iterations=2)], axis=0)
        ys, xs = np.nonzero(self.outline)
        x0, x1, y0, y1 = xs.min(), xs.max() + 1, ys.min(), ys.max() + 1
        mid = (y0 + y1) // 2
        stroke = float(self.outline[mid, x0:x0 + 60].sum())
        # The corner radius: how far in the outline starts 4 rows below its top (a circle's chord).
        dx = float(np.nonzero(self.outline[y0 + 4, x0:])[0][0])
        radius = dx + 4 + np.sqrt(8 * dx)  # the r with (r - 4)^2 + (r - dx)^2 = r^2 and r > dx
        r, g, b = rgb[..., 0], rgb[..., 1], rgb[..., 2]
        inside = ndimage.binary_erosion(self.core, iterations=int(stroke) + 4)
        pink = inside & (r > 180) & (g < 110) & (b > 60) & (b < 180)
        ays, axs = np.nonzero(pink)
        app = (float(axs.min()), float(ays.min()), float(axs.max() + 1), float(ays.max() + 1))
        # The app square's corner radius, measured the same way along its top row.
        top = pink[int(app[1]) + 2, int(app[0]):int(app[2])]
        adx = float(np.nonzero(top)[0][0]) if top.any() else 0.0
        app_radius = adx + 2 + np.sqrt(4 * adx)
        self.phone = Phone(float(x0), float(y0), float(x1), float(y1), stroke,
                           min(radius, (x1 - x0) / 2), app, min(app_radius, (app[2] - app[0]) / 2))
        self.scene = self._scene()

    def _scene(self) -> np.ndarray:
        """The picture with the phone taken out: under it, each row's sky, sun and hill are carried
        across from the parts of the same row that show, and the sun's disc and the hill's ridge are
        continued from the shapes around the phone."""
        rgb = self.rgb
        h, w, _ = rgb.shape
        r, g = rgb[..., 0], rgb[..., 1]
        yy = np.mgrid[:h, :w][0]
        hole = ndimage.binary_dilation(self.core, iterations=4)
        near = ndimage.binary_dilation(hole, iterations=3)
        sun = (g > 110) & (yy < h * 0.45) & ~near
        hill = (r < 70) & (yy > h * 0.45) & ~near
        # The sun's disc, fitted (least squares) to the part of its rim that shows.
        rim = sun & ~ndimage.binary_erosion(sun) & ~ndimage.binary_dilation(near, iterations=2)
        ry, rx = np.nonzero(rim)
        A = np.column_stack([rx, ry, np.ones(len(rx))])
        (ca, cb, cc), *_ = np.linalg.lstsq(A, rx ** 2 + ry ** 2, rcond=None)
        scx, scy = ca / 2 + 0.5, cb / 2 + 0.5
        sr = np.sqrt(cc + scx ** 2 + scy ** 2 - scx - scy + 0.5)
        # The hill's ridge: where each visible column near the phone turns navy, fitted with a parabola.
        hx = np.nonzero(hole.any(axis=0))[0]
        cols = [x for x in range(max(0, hx.min() - 250), min(w, hx.max() + 251))
                if not near[:, x].any() and hill[:, x].any()]
        ridge = [np.argmax(hill[:, x]) for x in cols]
        fit = np.polyfit(cols, ridge, 2)
        top = np.polyval(fit, np.arange(w))
        sky = ~ndimage.binary_dilation(sun | hill, iterations=3) & ~near
        out = rgb.copy()
        rows = np.nonzero(hole.any(axis=1))[0]
        any_row = {name: np.nonzero(m.any(axis=1))[0] for name, m in (("sky", sky), ("sun", sun), ("hill", hill))}
        masks = {"sky": sky, "sun": sun, "hill": hill}
        for y in rows:
            xs = np.nonzero(hole[y])[0]

            def carried(name: str, y: int = y, xs: np.ndarray = xs) -> np.ndarray:
                """The colour `name` has across this row's hole, carried over from where it shows on
                the row (or on the nearest row where it shows at all: the hill's ridge rises inside
                the hole above every row the hill reaches outside it)."""
                have = any_row[name]
                yn = have[np.argmin(abs(have - y))]
                seen = np.nonzero(masks[name][yn])[0]
                return np.stack([np.interp(xs, seen, rgb[yn, seen, c]) for c in range(3)], axis=1)

            colour = carried("sky")
            t_sun = np.clip(sr - np.hypot(xs + 0.5 - scx, y + 0.5 - scy) + 0.5, 0, 1)[:, None]
            if t_sun.any():
                colour = colour * (1 - t_sun) + carried("sun") * t_sun
            t_hill = np.clip(y + 0.5 - top[xs], 0, 1)[:, None]
            if t_hill.any():
                colour = colour * (1 - t_hill) + carried("hill") * t_hill
            out[y, xs] = colour
        # Soften the seams the row-by-row filling leaves inside the hole.
        soft = ndimage.gaussian_filter(out, sigma=(1.5, 1.5, 0))
        out[hole] = soft[hole]
        return out

    def scene_image(self) -> Image.Image:
        return Image.fromarray(self.scene.round().astype(np.uint8), "RGB")

    def phone_image(self) -> Image.Image:
        """The phone alone on transparency, in the source's frame. Inside it the pixels are kept as
        they are; on its outer edge each pixel is white with the opacity that takes the filled-in
        scene to the pixel's colour, so the edge is smooth and carries no sky."""
        rgb, scene, white = self.rgb, self.scene, self.white
        solid = ndimage.binary_erosion(self.core, iterations=2)
        band = ndimage.binary_dilation(self.core, iterations=3) & ~solid
        v = white - scene
        alpha = np.clip(((rgb - scene) * v).sum(axis=2) / np.maximum((v * v).sum(axis=2), 1.0), 0, 1)
        alpha[~band] = 0.0
        alpha[solid] = 1.0
        colour = np.where(solid[..., None], rgb, white)
        return Image.fromarray(np.dstack([colour, alpha * 255]).round().astype(np.uint8), "RGBA")


# ── The phone drawn as a mark ──────────────────────────────────────────────────────────────

SS = 8  # supersampling of the drawn masks


@dataclass
class Shape:
    """A phone outline and its app square in some frame (edges are pixel boundaries)."""
    outer: tuple[float, float, float, float]
    radius: float
    stroke: float
    app: tuple[float, float, float, float]
    app_radius: float


def shape(p: Phone, x0: float, y0: float, height: float, stroke: float, snap: bool) -> Shape:
    """p's outline and app square, `height` tall with its top left corner at (x0, y0) and an outline
    `stroke` wide. The app square keeps its size and its place in the top right corner, but stays at
    least one stroke clear of the outline. With `snap` every edge lands on a whole pixel."""
    rnd = round if snap else (lambda v: v)
    k = height / (p.y1 - p.y0)
    width = (p.x1 - p.x0) * k
    if snap:
        width = 2 * round(width / 2)
    x1, y1 = x0 + width, y0 + height
    side = (p.app[2] - p.app[0]) * k
    gap_right = max((p.x1 - p.app[2]) * k - stroke, stroke)
    gap_top = max((p.app[1] - p.y0) * k - stroke, stroke)
    ax1 = rnd(x1 - stroke - gap_right)
    ax0 = rnd(ax1 - side)
    ay0 = rnd(y0 + stroke + gap_top)
    ay1 = ay0 + (ax1 - ax0)
    return Shape((x0, y0, x1, y1), p.radius * k, stroke, (ax0, ay0, ax1, ay1),
                 p.app_radius / (p.app[2] - p.app[0]) * (ax1 - ax0))


def draw(sh: Shape, size: int) -> tuple[Image.Image, Image.Image]:
    """The outline and the app square as two greyscale masks, size x size."""
    big = size * SS
    s = lambda b: (b[0] * SS, b[1] * SS, b[2] * SS - 1, b[3] * SS - 1)  # PIL boxes include their last pixel
    outline = Image.new("L", (big, big), 0)
    d = ImageDraw.Draw(outline)
    x0, y0, x1, y1 = sh.outer
    d.rounded_rectangle(s((x0, y0, x1, y1)), radius=sh.radius * SS, fill=255)
    t = sh.stroke
    d.rounded_rectangle(s((x0 + t, y0 + t, x1 - t, y1 - t)), radius=max(0.0, sh.radius - t) * SS, fill=0)
    app = Image.new("L", (big, big), 0)
    ImageDraw.Draw(app).rounded_rectangle(s(sh.app), radius=sh.app_radius * SS, fill=255)
    # Pixel-boundary edges: shrinking by whole SS blocks averages exactly the covered area.
    return outline.resize((size, size), Image.BOX), app.resize((size, size), Image.BOX)


STROKE = 1 / 16  # the mark's outline is 1/16 of its square: 1 px at 16 px, 2 px at 32 px


def mark_masks(p: Phone, size: int) -> tuple[Image.Image, Image.Image]:
    """The bar mark at size x size: the phone upright, 14/16 of the square tall, centred, with the
    outline 1/16 of the square wide (the source's own outline, 1/64 of the phone's height, would
    vanish at bar size), every edge on a whole pixel."""
    height = size * 14 / 16
    k = height / (p.y1 - p.y0)
    width = 2 * round((p.x1 - p.x0) * k / 2)
    sh = shape(p, (size - width) / 2, size / 16, height, size * STROKE, snap=True)
    return draw(sh, size)


def white(mask: Image.Image) -> Image.Image:
    """White on transparency, with mask as the opacity."""
    img = Image.new("RGBA", mask.size, (255, 255, 255, 0))
    img.putalpha(mask)
    return img


def union(a: Image.Image, b: Image.Image) -> Image.Image:
    total = np.asarray(a, dtype=np.uint16) + np.asarray(b, dtype=np.uint16)
    return Image.fromarray(np.clip(total, 0, 255).astype(np.uint8), "L")


def rounded(img: Image.Image, corner: float, inset: float = 0.0) -> Image.Image:
    """img (square, RGB or RGBA) cut to a rounded square: corner is the radius as a share of the
    side, inset the empty margin in pixels."""
    side = img.width
    big = side * 4
    shape_ = Image.new("L", (big, big), 0)
    i = round(inset * 4)
    ImageDraw.Draw(shape_).rounded_rectangle((i, i, big - i - 1, big - i - 1),
                                             radius=corner * (big - 2 * i), fill=255)
    out = img.convert("RGBA")
    out.putalpha(shape_.resize((side, side), Image.LANCZOS))
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--lawnchair", type=Path, help="write the launcher icon into this checkout")
    args = ap.parse_args()
    picture = Image.open(HERE / "homalawn-icon-source.png").convert("RGB")
    src = Source(picture)
    p = src.phone
    print(f"phone {p.x0:.0f},{p.y0:.0f}-{p.x1:.0f},{p.y1:.0f} stroke {p.stroke:.0f} radius {p.radius:.0f}"
          f" app {tuple(round(v) for v in p.app)} radius {p.app_radius:.0f}")
    src.scene_image().save(HERE / "homalawn-scene.png", optimize=True)
    white(union(*mark_masks(p, 256))).save(HERE / "homalawn-mark.png", optimize=True)
    SHELL.mkdir(parents=True, exist_ok=True)
    for pattern in ("homar-*.png", "homalawn-*.png"):
        for old in SHELL.glob(pattern):
            old.unlink()
    # One mask pair per power of two from 16 px: the bar draws the smallest one at least as large as
    # it needs, so at bar size it lands pixel for pixel.
    for side in (16, 32, 64, 128, 256):
        outline, app = mark_masks(p, side)
        white(outline).save(SHELL / f"homalawn-outline-{side}.png", optimize=True)
        white(app).save(SHELL / f"homalawn-app-{side}.png", optimize=True)
    # The window header: the whole picture as a small rounded square.
    rounded(picture.resize((128, 128), Image.LANCZOS), 0.22).save(SHELL / "homalawn-icon.png", optimize=True)
    if args.lawnchair:
        android(args.lawnchair, picture, src)


# ── The phone's launcher icon ──────────────────────────────────────────────────────────────

DENSITIES = {"mdpi": 1, "hdpi": 1.5, "xhdpi": 2, "xxhdpi": 3, "xxxhdpi": 4}


def android(root: Path, picture: Image.Image, src: Source) -> None:
    """Lawnchair's launcher icon (ic_launcher_home): an adaptive icon that looks like the picture.
    The picture fills the middle 72 dp of the 108 dp canvas (what a launcher's mask shows); the
    background layer is the scene without the phone, its edges stretched out to the canvas's edge;
    the foreground is the phone, where the picture has it, which keeps it inside the 66 dp safe
    circle; the monochrome layer (themed icons) is the phone's outline and app square in white. The
    legacy square and round PNGs are the whole picture."""
    res = root / "res"
    p = src.phone
    n = picture.width
    scene = src.scene_image()
    phone = src.phone_image()
    # How far the phone reaches from the canvas's centre, in dp (the picture is 72 dp wide).
    a = np.asarray(phone)[..., 3] > 8
    ys, xs = np.nonzero(a)
    reach = float(np.hypot((xs + 0.5) / n - 0.5, (ys + 0.5) / n - 0.5).max()) * 72
    assert reach <= 33, f"the phone reaches {reach:.1f} dp from the centre, past the 33 dp safe circle"
    print(f"phone reaches {reach:.1f} dp of the 33 dp safe circle")
    for name, k in DENSITIES.items():
        d = res / f"mipmap-{name}"
        canvas = round(108 * k)
        view = round(72 * k)
        margin = (canvas - view) // 2
        bg = np.asarray(scene.resize((view, view), Image.LANCZOS))
        bg = np.pad(bg, ((margin, canvas - view - margin), (margin, canvas - view - margin), (0, 0)), mode="edge")
        background = Image.fromarray(bg, "RGB")
        background.save(d / "ic_launcher_home_background.png", optimize=True)
        front = Image.new("RGBA", (canvas, canvas), (0, 0, 0, 0))
        front.alpha_composite(phone.resize((view, view), Image.LANCZOS), (margin, margin))
        front.save(d / "ic_launcher_home_foreground.png", optimize=True)
        # The monochrome phone: the picture's phone, its outline 1/14 of its height (as the bar mark's).
        s = view / n
        height = (p.y1 - p.y0) * s
        sh = shape(p, margin + p.x0 * s, margin + p.y0 * s, height, height / 14, snap=False)
        outline, app = draw(sh, canvas)
        white(union(outline, app)).save(d / "ic_launcher_home_monochrome.png", optimize=True)
        # Legacy icons: the whole picture in 48 dp, on a shape with a 2 dp margin.
        side = round(48 * k)
        whole = picture.resize((side, side), Image.LANCZOS)
        for suffix, corner in (("", 0.2), ("_round", 0.5)):
            rounded(whole, corner, inset=2 * k).save(d / f"ic_launcher_home{suffix}.png", optimize=True)
    old = res / "values/homar_icon.xml"
    if old.exists():
        old.unlink()


if __name__ == "__main__":
    main()
