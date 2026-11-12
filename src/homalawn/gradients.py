"""Gradient wallpapers made from an Omarchy theme's colours (its colors.toml palette), for the phone's
"Wallpaper from Omarchy" screen. Pure and deterministic: the same colours, style and size give the
same picture, byte for byte, so a gradient's key can name its thumbnail.

Each style is a smooth function of the position, computed on a small grid and scaled up (smooth
functions lose nothing that way), then a fixed, seeded grain of +-2 levels is added so that dark,
slow ramps do not band on a phone screen."""
from __future__ import annotations

import colorsys
import functools
import hashlib
import io
import math
import random

from PIL import Image, ImageChops

# (style, name shown on the phone), in the order the phone shows them
STYLES = (("fade", "Fade"), ("diagonal", "Diagonal"), ("glow", "Glow"),
          ("duo", "Duo"), ("mesh", "Mesh"), ("vignette", "Vignette"))
NAMES = dict(STYLES)
GRID = 12          # the grid is the picture's size divided by this (at least 24 px across)
GRAIN = 2          # +- levels of the fixed grain
_SECOND = ("magenta", "red", "cyan", "green", "yellow", "orange", "blue",
           "bright_magenta", "bright_red", "bright_cyan", "bright_green", "bright_yellow", "bright_blue")

RGB = tuple[float, float, float]


def _rgb(v) -> RGB | None:
    if not isinstance(v, str):
        return None
    h = v.strip().lstrip("#")
    if len(h) == 8:
        h = h[2:]
    if len(h) != 6:
        return None
    try:
        return tuple(int(h[i:i + 2], 16) / 255 for i in (0, 2, 4))   # type: ignore[return-value]
    except ValueError:
        return None


def _mix(a: RGB, b: RGB, t: float) -> RGB:
    t = min(1.0, max(0.0, t))
    return (a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t, a[2] + (b[2] - a[2]) * t)


def _smooth(t: float) -> float:
    t = min(1.0, max(0.0, t))
    return t * t * (3 - 2 * t)


def _hue_gap(a: RGB, b: RGB) -> float:
    ha = colorsys.rgb_to_hsv(*a)[0]
    hb = colorsys.rgb_to_hsv(*b)[0]
    d = abs(ha - hb)
    return min(d, 1 - d)


def _sat(c: RGB) -> float:
    return colorsys.rgb_to_hsv(*c)[1]


@functools.lru_cache(maxsize=64)
def _palette(items: tuple) -> dict | None:
    colors = dict(items)
    bg, accent = _rgb(colors.get("background")), _rgb(colors.get("accent"))
    if bg is None and accent is None:
        return None
    if accent is None:
        accent = _rgb(colors.get("blue")) or _rgb(colors.get("foreground")) or _mix(bg, (1, 1, 1), 0.5)
    if bg is None:
        bg = _mix(accent, (0, 0, 0), 0.85)
    light = colors.get("mode") == "light"
    deep = _rgb(colors.get("darker_background")) or _rgb(colors.get("dark_background")) or \
        _mix(bg, (1, 1, 1) if light else (0, 0, 0), 0.4 if light else 0.35)
    soft = _rgb(colors.get("lighter_background")) or _mix(bg, (0, 0, 0) if light else (1, 1, 1), 0.08)
    cands = [c for c in (_rgb(colors.get(k)) for k in _SECOND) if c is not None and _sat(c) > 0.25]
    # the second colour sits near the accent on the colour wheel (blends stay clean, never grey);
    # the third is the one furthest from both, for the mesh
    second = min(cands, key=lambda c: abs(_hue_gap(c, accent) - 0.2), default=None)
    if second is None or _hue_gap(second, accent) < 0.06:
        h, s, v = colorsys.rgb_to_hsv(*accent)
        second = colorsys.hsv_to_rgb((h + 0.18) % 1, max(s, 0.45), max(v, 0.6))
    third = max(cands, key=lambda c: min(_hue_gap(c, accent), _hue_gap(c, second)), default=None)
    if third is None or min(_hue_gap(third, accent), _hue_gap(third, second)) < 0.06:
        h, s, v = colorsys.rgb_to_hsv(*accent)
        third = colorsys.hsv_to_rgb((h - 0.15) % 1, max(s, 0.45), max(v, 0.6))
    return {"bg": bg, "accent": accent, "second": second, "third": third, "deep": deep, "soft": soft,
            "light": light}


def palette(colors: dict) -> dict | None:
    """The colours the gradients use, or None when the theme has neither a background nor an accent."""
    return _palette(tuple(sorted((k, v) for k, v in (colors or {}).items() if isinstance(v, str))))


def available(colors: dict) -> bool:
    return palette(colors) is not None


def key(colors: dict, style: str) -> str:
    """The gradient's identity (16 hex): its style and the colours it is made of."""
    p = palette(colors)
    used = "" if p is None else "|".join(
        "%.4f,%.4f,%.4f" % p[k] for k in ("bg", "accent", "second", "third", "deep", "soft"))
    return hashlib.sha1(f"gradient|{style}|{used}".encode()).hexdigest()[:16]


def _pixel(style: str, p: dict, u: float, v: float, r: float) -> RGB:
    """The colour at (u, v), both 0..1 across and down; r is the height over the width."""
    bg, accent, second, third, deep, soft = (p[k] for k in ("bg", "accent", "second", "third", "deep", "soft"))
    y = v * r
    if style == "fade":
        lit = _mix(bg, accent, 0.62)
        t = v ** 1.3
        return _mix(_mix(deep, bg, _smooth(t * 1.8)), lit, _smooth((t - 0.3) / 0.7))
    if style == "diagonal":
        t = (u + y) / (1 + r)
        a = _mix(second, bg, 0.25)
        m = _mix(bg, accent, 0.55)
        return _mix(a, m, _smooth(t / 0.5)) if t < 0.5 else _mix(m, deep, _smooth((t - 0.5) / 0.5))
    if style == "glow":
        d2 = (u - 0.5) ** 2 + (y - 0.8 * r) ** 2
        w = math.exp(-d2 / (0.42 ** 2 * (1 + 0.15 * r)))
        halo = math.exp(-d2 / (0.9 ** 2 * (1 + 0.15 * r)))
        return _mix(_mix(deep, bg, halo), accent, 0.9 * w)
    if style == "duo":
        t = ((1 - u) + y) / (1 + r)
        a = _mix(accent, deep, 0.18)
        b = _mix(second, deep, 0.22)
        return _mix(a, b, _smooth(t))
    if style == "mesh":
        blobs = ((accent, 0.18, 0.22 * r, 0.55), (second, 0.92, 0.5 * r, 0.6), (third, 0.25, 0.92 * r, 0.6))
        out, rest = (0.0, 0.0, 0.0), 1.0
        ws = []
        for c, bx, by, s in blobs:
            ws.append((c, 0.85 * math.exp(-((u - bx) ** 2 + (y - by) ** 2) / (s * s))))
        total = sum(w for _, w in ws)
        scale = 1 / total if total > 1 else 1.0
        for c, w in ws:
            w *= scale
            out = (out[0] + c[0] * w, out[1] + c[1] * w, out[2] + c[2] * w)
            rest -= w
        return (out[0] + bg[0] * rest, out[1] + bg[1] * rest, out[2] + bg[2] * rest)
    if style == "vignette":
        d = math.sqrt(((u - 0.5) / 0.5) ** 2 + ((v - 0.42) / 0.6) ** 2)
        centre = _mix(soft, accent, 0.3)
        return _mix(centre, deep, _smooth((d - 0.15) / 1.0))
    raise ValueError(f"unknown gradient style {style!r}")


@functools.lru_cache(maxsize=8)
def _grain(seed: int, size: int = 128) -> Image.Image:
    rnd = random.Random(seed)
    tile = Image.new("L", (size, size))
    tile.putdata([128 + rnd.randint(-GRAIN, GRAIN) for _ in range(size * size)])
    return tile


def image(colors: dict, style: str, size: tuple[int, int]) -> Image.Image:
    """The gradient as an RGB picture of exactly `size`."""
    if style not in NAMES:
        raise ValueError(f"unknown gradient style {style!r}")
    p = palette(colors)
    if p is None:
        raise ValueError("this theme has no colours to make a gradient from")
    w, h = max(1, int(size[0])), max(1, int(size[1]))
    gw = max(24, round(w / GRID))
    gh = max(2, round(gw * h / w))
    r = h / w
    data = []
    for j in range(gh):
        v = j / (gh - 1)
        for i in range(gw):
            c = _pixel(style, p, i / (gw - 1), v, r)
            data.append(tuple(max(0, min(255, round(x * 255))) for x in c))
    small = Image.new("RGB", (gw, gh))
    small.putdata(data)
    img = small.resize((w, h), Image.BICUBIC)
    tile = _grain(int(key(colors, style)[:8], 16))
    noise = Image.new("L", (w, h))
    for x in range(0, w, tile.width):
        for y in range(0, h, tile.height):
            noise.paste(tile, (x, y))
    noise = Image.merge("RGB", (noise, noise, noise))
    return ImageChops.add(img, noise, 1.0, -128)


def render(colors: dict, style: str, size: tuple[int, int]) -> bytes:
    """The gradient as PNG bytes of exactly `size`."""
    out = io.BytesIO()
    image(colors, style, size).save(out, "PNG")
    return out.getvalue()
