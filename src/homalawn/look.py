"""Omarchy look engine: the desktop's themes, a wallpaper cut and tinted for the phone, and the
theme palette turned into launcher colour settings. Pure functions; nothing here talks to a phone."""
from __future__ import annotations

import io
import os
import subprocess
import tomllib
from dataclasses import dataclass
from pathlib import Path

from PIL import Image

from . import env as _env
from .wallcrop import crop_cover

IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".webp"}

# launcher setting -> (palette key, alpha byte)
DEFAULT_MAPPING = {
    "accent_color": ("accent", 0xFF),
    "folder_color": ("lighter_background", 0xFF),
    "hotseat_bg_color": ("background", 0x99),
    "app_drawer_bg_color": ("background", 0xFF),
}


PARALLAX_PREF = "pref_wallpaperScrolling"


def parallax_size(screen: tuple[int, int]) -> tuple[int, int]:
    """The home picture's size when the launcher scrolls the wallpaper: twice the screen's width, its
    height (Android lets a picture be up to 100 % wider than the screen; off, send exactly the screen)."""
    return (2 * screen[0], screen[1])


def slug(name: str) -> str:
    """Omarchy's name for a theme's folder: `omarchy theme list` prints "Tokyo Night", while
    `omarchy theme dir` and `theme.name` use "tokyo-night"."""
    return "-".join(name.strip().lower().split())


@dataclass
class Theme:
    name: str                 # display name, as `omarchy theme list` prints it
    dir: Path
    colors: dict
    backgrounds: list[Path]
    preview: Path | None
    slug: str = ""            # folder name; derived from the name when not given

    def __post_init__(self):
        if not self.slug:
            self.slug = slug(self.name)


def _images(d: Path) -> list[Path]:
    if not d.is_dir():
        return []
    return [p for p in d.iterdir() if p.is_file() and p.suffix.lower() in IMAGE_EXTS]


def _build(name: str, d: Path, home: Path) -> Theme | None:
    colors_file = d / "colors.toml"
    if not d.is_dir():
        return None
    colors: dict = {}      # older themes have no colors.toml: their wallpapers still sync, colours do not
    if colors_file.is_file():
        try:
            colors = tomllib.loads(colors_file.read_text())
        except (OSError, tomllib.TOMLDecodeError):
            return None
    bgs = _images(d / "backgrounds") + _images(home / ".config" / "omarchy" / "backgrounds" / d.name)
    preview = d / "preview.png"
    return Theme(name, d, colors, sorted(bgs, key=lambda p: (p.name, str(p))), preview if preview.is_file() else None)


class ThemeDirMissing(Exception):
    """`omarchy theme list` named a theme whose `omarchy theme dir` is no folder: Omarchy itself is
    misconfigured for this process (seen when the service started without the session's
    OMARCHY_PATH: the dir came back as "/themes/<name>"), so every theme would silently vanish."""


def list_themes(run=subprocess.run, home: Path = Path.home()) -> list[Theme]:
    """Omarchy's themes; none on a computer without the `omarchy` command."""
    try:
        r = run(["omarchy", "theme", "list"], capture_output=True, text=True)
    except FileNotFoundError:
        return []
    if r.returncode != 0:
        return []
    out = []
    for name in (n.strip() for n in r.stdout.splitlines()):
        if not name:
            continue
        d = run(["omarchy", "theme", "dir", slug(name)], capture_output=True, text=True)
        if d.returncode != 0 or not d.stdout.strip():
            continue
        path = Path(d.stdout.strip())
        if not path.is_dir():
            raise ThemeDirMissing(
                f"`omarchy theme list` lists {name!r} but `omarchy theme dir {slug(name)}` gives {path}, "
                f"which is not a folder (OMARCHY_PATH={os.environ.get('OMARCHY_PATH', '<unset>')}); "
                "is this process running outside the desktop session's environment?")
        theme = _build(name, path, home)
        if theme:
            out.append(theme)
    return out


def theme_named(name: str, run=subprocess.run, home: Path = Path.home()) -> Theme | None:
    want = name.strip().lower()
    for t in list_themes(run=run, home=home):
        if want in (t.name.lower(), t.slug, t.dir.name.lower()) or slug(want) == t.slug:
            return t
    return None


def current(home: Path = Path.home()) -> tuple[str | None, Path | None]:
    given = _env.get("OMARCHY_STATE")
    cur = Path(given) if given else home / ".local" / "state" / "omarchy" / "current"
    try:
        name = (cur / "theme.name").read_text().strip() or None
    except OSError:
        name = None
    bg = cur / "background"
    try:
        path = bg.resolve(strict=True)
    except OSError:
        path = None
    return name, path


def layer(img: Image.Image, amount: int) -> Image.Image:
    """Darken (amount > 0, towards black) or lighten (< 0, towards white) by |amount| percent."""
    amount = max(-100, min(100, int(amount)))
    if amount == 0:
        return img
    base = img.convert("RGBA") if img.mode == "RGBA" else img.convert("RGB")
    tint = (0, 0, 0) if amount > 0 else (255, 255, 255)
    over = Image.new(base.mode, base.size, tint + ((255,) if base.mode == "RGBA" else ()))
    out = Image.blend(base, over, abs(amount) / 100)
    if base.mode == "RGBA":
        out.putalpha(base.getchannel("A"))
    return out


def render(data: bytes, size: tuple[int, int], offset: float, amount: int) -> bytes:
    """PNG of exactly `size`: the picture cut to cover it (framed by offset), then tinted."""
    cropped = crop_cover(data, size[0], size[1], offset)
    if int(amount) == 0:
        return cropped
    img = layer(Image.open(io.BytesIO(cropped)), amount)
    out = io.BytesIO()
    img.save(out, "PNG")
    return out.getvalue()


def _hex(rgb: str, alpha: int = 0xFF) -> str | None:
    h = rgb.strip().lstrip("#").lower()
    if len(h) == 6:
        h = f"{alpha:02x}{h}"
    if len(h) != 8 or any(c not in "0123456789abcdef" for c in h):
        return None
    return f"custom|#{h}"


def palette_prefs(colors: dict, mapping: dict | None = None) -> list[tuple[str, str]]:
    """Launcher colour settings from a palette. `mapping` overrides {setting: palette key | #rrggbb | #aarrggbb}.
    A palette key the theme lacks is skipped, never invented."""
    out = []
    for pref, (key, alpha) in DEFAULT_MAPPING.items():
        src = (mapping or {}).get(pref)
        if src is None:
            value = colors.get(key)
            val = _hex(value, alpha) if isinstance(value, str) else None
        elif src.startswith("#"):
            val = _hex(src)
        else:
            value = colors.get(src)
            val = _hex(value, alpha) if isinstance(value, str) else None
        if val:
            out.append((pref, val))
    return out


def mode_pref(colors: dict) -> tuple[str, str] | None:
    mode = colors.get("mode")
    return ("pref_launcherTheme", mode) if mode in ("light", "dark") else None


# -- monochrome icons in one theme colour (the phone's patch 0023 reads these two settings) ------
ICON_COLOR_PREF = "homalawn_icon_color"        # the symbol, "#rrggbb"
ICON_BG_PREF = "homalawn_icon_bg_color"        # the plate behind it, "#rrggbb"
# the palette keys offered for the symbol, in the order the window shows them: the accent first
# (the default), then the text colours, the named hues, the terminal's colours, the rest
ICON_KEYS = (("accent", "foreground", "bright_foreground", "light_foreground", "dark_foreground",
              "red", "orange", "yellow", "green", "cyan", "blue", "magenta", "brown",
              "bright_red", "bright_yellow", "bright_green", "bright_cyan", "bright_blue", "bright_magenta")
             + tuple(f"color{i}" for i in (*range(1, 8), *range(9, 16), 8, 0))
             + ("cursor", "muted", "selection_foreground"))
# a symbol fainter than this against its plate cannot be read; such colours are not offered
ICON_MIN_CONTRAST = 1.5
# colours this close (largest difference of one channel, 0..255) to one already offered look the
# same as a swatch: only the first is offered
ICON_NEAR = 16


def rgb6(value) -> str | None:
    """"#rrggbb" in lower case, or None when `value` is not a six-digit hex colour."""
    if not isinstance(value, str):
        return None
    h = value.strip().lstrip("#").lower()
    if len(h) != 6 or any(c not in "0123456789abcdef" for c in h):
        return None
    return "#" + h


def _luminance(hex6: str) -> float:
    def ch(i):
        c = int(hex6[i:i + 2], 16) / 255
        return c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4
    return 0.2126 * ch(1) + 0.7152 * ch(3) + 0.0722 * ch(5)


def contrast(a: str, b: str) -> float:
    la, lb = sorted((_luminance(a), _luminance(b)), reverse=True)
    return (la + 0.05) / (lb + 0.05)


def _near(a: str, b: str) -> bool:
    return max(abs(int(a[i:i + 2], 16) - int(b[i:i + 2], 16)) for i in (1, 3, 5)) <= ICON_NEAR


def icon_plate(colors: dict) -> str | None:
    """The plate behind a monochrome icon's symbol, from the theme: a dark theme its
    lighter_background (a step up from the wallpaper's dark, as Android's own dark plates), else its
    background; a light theme the lighter of background and lighter_background (a near-white plate,
    as Android's light ones). A theme without `mode` is light when its background is."""
    bg, lighter = rgb6(colors.get("background")), rgb6(colors.get("lighter_background"))
    mode = colors.get("mode")
    if mode not in ("light", "dark"):
        mode = "light" if bg and _luminance(bg) > 0.5 else "dark"
    if mode == "dark":
        return lighter or bg
    have = [c for c in (bg, lighter) if c]
    return max(have, key=_luminance) if have else None


def icon_palette(colors: dict) -> list[str]:
    """The theme's colours a monochrome icon's symbol can take, "#rrggbb", accent first, each colour
    once (one that looks the same as an earlier one is left out); colours too faint against the
    plate are left out too (the accent never)."""
    plate = icon_plate(colors)
    out: list[str] = []
    for k in ICON_KEYS:
        c = rgb6(colors.get(k))
        if not c or any(_near(c, o) for o in out):
            continue
        if k != "accent" and plate and contrast(c, plate) < ICON_MIN_CONTRAST:
            continue
        out.append(c)
    return out


def icon_prefs(colors: dict, chosen: str | None = None) -> list[tuple[str, str]]:
    """The two icon colour settings: the symbol (`chosen`, else the theme's accent, else its first
    offered colour) and the plate. [] when the theme has no colours to make them from."""
    offered = icon_palette(colors)
    color = rgb6(chosen) or (offered[0] if offered else None)
    plate = icon_plate(colors)
    if not color or not plate:
        return []
    return [(ICON_COLOR_PREF, color), (ICON_BG_PREF, plate)]
