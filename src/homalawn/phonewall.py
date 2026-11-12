"""Wallpapers picked on the phone: the phone's "Wallpaper from Omarchy" screen (patch 0017) shows,
for its lock screen and its home screen, what each shows now and what it could show: the
backgrounds of the theme its look follows, or gradients made from that theme's colours (gradients.py).

The computer pushes the list as small JPEG thumbnails (`launcher.theme_wallpapers_set`): the
pictures cut like the home screen but without the colour layer (the phone lays `layer` over its
previews itself), and the gradients. The phone answers with the thumbnails it already holds, so
only new ones travel, in batches that keep every message well under the link's 1 MiB line. The
list carries what each screen shows now (`screens`: keep, or a wallpaper or gradient by id).
Choices are a draft on the phone; its Apply leaves one pending choice `{at, screens}` (one choice
per screen), which the service reads (`launcher.theme_wallpaper_choice`, which also says
`needs_list` when the phone wants the whole list again) and answers
(`launcher.theme_wallpaper_ack`: received, then applied, waiting or failed; failed with code
wallpaper_unsaved makes the phone ask whether to replace the wallpaper without a backup, and its
Replace sends the same choice again with `overwrite`). A build from before
per-screen choices gets the older list (`target`, `current_id`, no gradients) and answers with
`{at, id?, target?}`; one older still gets no `target`.

Format 3 (the whole look model, as in the window: the two screens set separately, parallax, a
colour layer per screen): the list adds `format: 3` and `wallpaper: {same, parallax}`, and every
wallpaper a `wide` thumbnail (the home picture as it is sent with parallax on, twice the width;
gradients have none: they never scroll, and `parallax` says true only while the home screen shows a
theme wallpaper) and, when that picture's lock-screen crop differs from its home one, a `lock` thumbnail. A phone that knows it
answers `{at, format: 3, screens, layer: {home, lock}, wallpaper: {same, parallax}, overwrite?}`.
`same` is left from the phone's "Same on both", dropped from the model on 2026-10-17: the list
always says false (the phone shows its two panels); a choice may still say true (the phone's own
switch, until its screen drops it, or nothing), and the service stores that as two equal screens.
A build from before refuses the two list fields (its params are strict) and gets the per-screen
list; a service from before never sends them, so the phone offers only what that list can carry
and its choice still reads as one per screen (the extra fields are ignored there).
Nothing here applies anything; the service does that through ops.look_apply."""
from __future__ import annotations

import base64
import functools
import hashlib
import io
from dataclasses import dataclass, field
from pathlib import Path

from PIL import Image

from . import gradients
from . import look as look_mod
from .lookconf import KINDS, SCREENS, TARGETS, LookSettings

THUMB_W = 240              # px: a third of a phone's width at about 2x, for the 3-column grid
JPEG_QUALITY = 80
BATCH_BYTES = 600_000      # base64 thumbnails per message: the link's lines are capped at 1 MiB

SET = "launcher.theme_wallpapers_set"
CHOICE = "launcher.theme_wallpaper_choice"
ACK = "launcher.theme_wallpaper_ack"
ACK_STATES = ("received", "applied", "waiting", "failed")
NEW_FIELDS = ("gradients", "screens", "layer")    # what a build from before per-screen choices refuses
FORMAT = 3                                        # the list and choice with the whole look model
MODEL_FIELDS = ("format", "wallpaper")            # what a build from before format 3 refuses


@dataclass(frozen=True)
class Item:
    id: str        # the picture: its path, size and change time; a gradient: its style and colours
    name: str
    path: str      # a gradient has none ("")
    thumb: str     # the thumbnail: the picture plus how it is cut (and its size)
    style: str | None = None    # a gradient's style
    wide: str | None = None     # the home picture with parallax on (twice as wide), its thumbnail key
    lock: str | None = None     # the lock screen's own cut, when its crop differs from the home one


@dataclass
class View:
    """What the phone's screen shows. `enabled`: this phone's look is on (not `off`)."""
    enabled: bool
    theme: str | None = None
    slug: str | None = None
    items: list[Item] = field(default_factory=list)
    current: str | None = None             # id of the background the home screen uses now
    message: str | None = None             # why there is nothing to show, for the phone to say
    size: tuple[int, int] = (THUMB_W, THUMB_W * 2)
    cut: dict = field(default_factory=dict)   # path -> crop offset
    target: str = "both"                   # the look's target (for older builds)
    gradients: list[Item] = field(default_factory=list)
    screens: dict = field(default_factory=lambda: {p: {"kind": "keep"} for p in SCREENS})
    layer: dict = field(default_factory=lambda: {"home": 0, "lock": 0})
    colors: dict = field(default_factory=dict)
    default: str | None = None             # the picture a wallpaper screen without a pick shows
    parallax: bool = False                 # the home screen's wallpaper scrolls with its pages
    cut_lock: dict = field(default_factory=dict)   # path -> the lock crop, where it differs

    def fingerprint(self) -> tuple:
        return (self.enabled, self.theme, self.current, self.message, self.target, self.parallax,
                tuple((i.id, i.name, i.thumb, i.wide, i.lock) for i in self.items),
                tuple((i.id, i.name, i.thumb, i.wide) for i in self.gradients),
                tuple(sorted((p, tuple(sorted(c.items()))) for p, c in self.screens.items())),
                tuple(sorted(self.layer.items())))

    def find(self, item_id: str) -> Item | None:
        """A wallpaper of the list by id."""
        return next((i for i in self.items if i.id == item_id), None)

    def find_gradient(self, item_id: str) -> Item | None:
        return next((i for i in self.gradients if i.id == item_id), None)


def picture_id(path: str) -> str:
    try:
        st = Path(path).stat()
        stamp = f"{st.st_size}:{st.st_mtime_ns}"
    except OSError:
        stamp = "missing"
    return hashlib.sha1(f"{path}|{stamp}".encode()).hexdigest()[:16]


def thumb_size(screen: tuple[int, int] | None) -> tuple[int, int]:
    w, h = screen if screen and screen[0] > 0 and screen[1] > 0 else (1080, 2400)
    return THUMB_W, max(1, round(THUMB_W * h / w))


def _key(*parts) -> str:
    return hashlib.sha1("|".join(str(p) for p in parts).encode()).hexdigest()[:16]


def _theme_named(themes, name: str | None):
    if not name:
        return None
    want = look_mod.slug(name)
    return next((t for t in themes if want in (t.slug, look_mod.slug(t.name), t.dir.name.lower())), None)


def view(settings: LookSettings, omarchy: dict, themes, screen: tuple[int, int] | None) -> View:
    """The screen's contents for one phone: the theme its look follows (follow: Omarchy's current
    one; chosen: the chosen one), every background of it, its gradients, what each screen shows
    now, and the thumbnails' keys (a picture's changes when its crop does)."""
    layer = {p: settings.layer_of(p) for p in SCREENS}
    target = settings.target() or "both"       # older builds know home / lock / both only
    model = {"parallax": settings.parallax_on()}     # off unless the home screen shows a wallpaper
    if settings.source == "off":
        return View(False, target=target, layer=layer, **model)
    follow = settings.source == "follow"
    name = omarchy.get("theme") if follow else settings.theme
    theme = _theme_named(themes, name)
    if theme is None:
        return View(True, theme=name, target=target, layer=layer, **model,
                    message=f"Omarchy theme {name} was not found on the computer."
                    if name else "Omarchy reports no current theme.")
    paths = [str(p) for p in theme.backgrounds]
    omarchy_bg = omarchy.get("background") if follow else None
    if omarchy_bg and not any(Path(p).name == Path(omarchy_bg).name for p in paths):
        paths.insert(0, omarchy_bg)       # your own picture as the desktop background: shown first
    picks = {p: settings.pick_for(theme.name, p) for p in SCREENS}
    for extra in (picks["lock"], picks["home"]):
        if extra and extra not in paths:
            paths.insert(0, extra)
    default = omarchy_bg if follow else (str(theme.backgrounds[0]) if theme.backgrounds else None)   # as ops

    def shown(pick):
        """The picture a wallpaper screen shows: its pick, else the theme's default."""
        cur = pick or default
        if cur and cur not in paths:      # the same picture under another path (a link)
            cur = next((p for p in paths if Path(p).name == Path(cur).name), cur)
        return cur
    size = thumb_size(screen)
    wide = look_mod.parallax_size(size)
    items, cut, cut_lock = [], {}, {}
    for p in paths:
        pid = picture_id(p)
        c = round(settings.crop_for(p, "home"), 4)
        cut[p] = c
        lc = round(settings.crop_for(p, "lock"), 4)
        if lc != c:
            cut_lock[p] = lc
        items.append(Item(pid, Path(p).stem, p, _key(pid, size, c), wide=_key(pid, wide, c),
                          lock=_key(pid, size, lc) if lc != c else None))
    grads = []
    if gradients.available(theme.colors):
        for style, label in gradients.STYLES:
            gid = gradients.key(theme.colors, style)
            grads.append(Item(gid, label, "", _key(gid, size), style))   # never wide: no parallax
    ids = {p: next((i.id for i in items if i.path == shown(picks[p])), None) for p in SCREENS}
    screens = {}
    for p, c in settings.screens_of().items():
        if c["kind"] == "wallpaper":
            screens[p] = {"kind": "wallpaper", "id": ids[p]}
        elif c["kind"] == "gradient":
            g = next((i for i in grads if i.style == c["style"]), None)
            screens[p] = {"kind": "gradient", "id": g.id if g else None}
        else:
            screens[p] = {"kind": "keep"}
    return View(True, theme=theme.name, slug=theme.slug, items=items, current=ids["home"], size=size, cut=cut,
                target=target, gradients=grads, screens=screens, layer=layer, colors=dict(theme.colors),
                default=shown(None), cut_lock=cut_lock, **model)


def _jpeg(img: Image.Image) -> bytes:
    out = io.BytesIO()
    img.convert("RGB").save(out, "JPEG", quality=JPEG_QUALITY, optimize=True)
    return out.getvalue()


@functools.lru_cache(maxsize=64)
def _thumb(path: str, pid: str, w: int, h: int, offset: float) -> bytes:
    data = Path(path).read_bytes()
    return _jpeg(Image.open(io.BytesIO(look_mod.render(data, (w, h), offset, 0))))


@functools.lru_cache(maxsize=32)
def _gradient_thumb(gid: str, colors: tuple, style: str, w: int, h: int) -> bytes:
    return _jpeg(gradients.image(dict(colors), style, (w, h)))


def thumbnail(v: View, item: Item, variant: str = "thumb") -> bytes | None:
    """An item's thumbnail: `thumb` (the screen's shape, home crop), `wide` (the home picture with
    parallax on) or `lock` (the screen's shape, the lock screen's own crop)."""
    w, h = (v.size[0] * 2, v.size[1]) if variant == "wide" else v.size
    try:
        if item.style:
            colors = tuple(sorted((k, c) for k, c in v.colors.items() if isinstance(c, str)))
            return _gradient_thumb(item.id, colors, item.style, w, h)
        cut = v.cut_lock.get(item.path, v.cut.get(item.path, 0.5)) if variant == "lock" else v.cut.get(item.path, 0.5)
        return _thumb(item.path, item.id, w, h, cut)
    except (OSError, ValueError):
        return None


def _rows(items: list[Item], model: bool = False) -> list[dict]:
    """The list's items; with the whole model (format 3) also their `wide` and `lock` thumbnail keys."""
    out = []
    for i in items:
        row = {"id": i.id, "name": i.name, "thumb": i.thumb}
        if model:
            row.update({k: getattr(i, k) for k in ("wide", "lock") if getattr(i, k)})
        out.append(row)
    return out


def _base(v: View, level: int = FORMAT) -> dict:
    """The list as a build of `level` reads it: 3 the whole model, 2 one choice per screen (no
    gradients before that), 1 with the target, 0 without it."""
    model = level >= FORMAT
    base = {"enabled": v.enabled, "theme": v.theme, "current_id": v.current, "message": v.message,
            "items": _rows(v.items, model)}
    if level >= 1:
        base["target"] = v.target
    if level >= 2:
        base.update({"gradients": _rows(v.gradients, model), "screens": v.screens, "layer": v.layer})
    if model:
        # same: always false, so the phone's builds from before 2026-10-17 show both screens' panels
        base.update({"format": FORMAT, "wallpaper": {"same": False, "parallax": v.parallax}})
    return base


def _pictures(v: View, level: int) -> list[tuple[str, Item, str]]:
    """(thumbnail key, item, variant) of every thumbnail a build of `level` shows."""
    out = [(i.thumb, i, "thumb") for i in v.items + (v.gradients if level >= 2 else [])]
    if level >= FORMAT:
        out += [(i.wide, i, "wide") for i in v.items if i.wide]
        out += [(i.lock, i, "lock") for i in v.items if i.lock]
    return out


def _refused_names(e: Exception) -> set[str]:
    """The params a build refused (bad_params: "unknown params for <method>: a, b"), else none."""
    if getattr(e, "code", None) != "bad_params" and not str(e).startswith("bad_params"):
        return set()
    text = str(e)
    if "unknown params" not in text:
        return set()
    return {n.strip() for n in text.rsplit(": ", 1)[-1].split(",") if n.strip()}


def _refused(e: Exception, names) -> bool:
    """A build from before a field refuses it (its params are strict): bad_params naming it."""
    return bool(_refused_names(e) & set(names))


# the list's fields by the level that brought them (see _base)
_LEVEL_FIELDS = {FORMAT: MODEL_FIELDS, 2: NEW_FIELDS, 1: ("target",)}


def push(t, v: View) -> int:
    """Send the screen's contents; then the thumbnails the phone lacks, in batches. Returns how many
    thumbnails were sent. The first call carries no pictures: its answer says which it has. A build
    refusing a field gets the list of the level before that field (_base): one from before format 3
    without the model and the wide and lock thumbnails, one from before per-screen choices without
    gradients, screens and layer, one from before the target choice also without `target`."""
    level = FORMAT
    while True:
        base = _base(v, level)
        try:
            r = t.call(SET, {**base, "thumbs": []})
            break
        except Exception as e:
            named = _refused_names(e)
            lower = [lv for lv, fields in _LEVEL_FIELDS.items() if lv <= level and named & set(fields)]
            if not lower:
                raise
            level = min(lower) - 1
    have = set(r.get("have") or []) if isinstance(r, dict) else set()
    batch, size, sent = [], 0, 0
    for key, item, variant in _pictures(v, level):
        if key in have:
            continue
        jpeg = thumbnail(v, item, variant)
        if jpeg is None:
            continue
        b64 = base64.b64encode(jpeg).decode()
        if batch and size + len(b64) > BATCH_BYTES:
            t.call(SET, {**base, "thumbs": batch})
            sent += len(batch)
            batch, size = [], 0
        batch.append({"key": key, "jpeg": b64})
        size += len(b64)
        have.add(key)
    if batch:
        t.call(SET, {**base, "thumbs": batch})
        sent += len(batch)
    return sent


def _screen_choice(c) -> dict | None:
    if not isinstance(c, dict) or c.get("kind") not in KINDS:
        return None
    if c["kind"] == "keep":
        return {"kind": "keep"}
    cid = c.get("id")
    return {"kind": c["kind"], "id": cid} if isinstance(cid, str) and cid else None


def poll(t) -> tuple[dict | None, bool]:
    """One choice read: (the pending choice as `choice` returns it, needs_list). needs_list: the
    phone holds no list from its current process, or one in an older format (a build updated after
    the last push keeps the old build's list on disk), so it wants the whole list again. Builds
    from before needs_list never send it (False)."""
    r = t.call(CHOICE, {})
    return _choice(r), isinstance(r, dict) and r.get("needs_list") is True


def choice(t) -> dict | None:
    """The phone's pending choice not yet received, else None: {"at", "screens", "id", "target"}.
    `screens`: {home, lock} each {"kind": "keep"} or {"kind": "wallpaper" | "gradient", "id"}.
    An older build sends `id` (the wallpaper tapped) and/or `target` (home, lock or both) instead,
    and `screens` is None."""
    return poll(t)[0]


def _choice(r) -> dict | None:
    c = r.get("choice") if isinstance(r, dict) else None
    if not isinstance(c, dict) or not isinstance(c.get("at"), (int, float)) or isinstance(c.get("at"), bool):
        return None
    raw = c.get("screens")
    if isinstance(raw, dict):
        screens = {p: _screen_choice(raw.get(p)) for p in SCREENS}
        if any(v is None for v in screens.values()):
            return None
        out = {"at": c["at"], "screens": screens, "id": None, "target": None}
        if c.get("overwrite") is True:      # Replace in the phone's overwrite question
            out["overwrite"] = True
        fmt = c.get("format")
        if isinstance(fmt, int) and not isinstance(fmt, bool) and fmt >= FORMAT:
            model = _model_choice(c)
            if model is None:
                return None
            out.update(model)
        return out
    pid = c.get("id") if isinstance(c.get("id"), str) else None
    target = c.get("target") if c.get("target") in TARGETS else None
    if pid is None and target is None:
        return None
    return {"id": pid, "target": target, "at": c["at"], "screens": None}


def _model_choice(c: dict) -> dict | None:
    """The whole-model part of a format-3 choice: {"format", "wallpaper": {same, parallax},
    "layer": {home, lock}}; None when any of it is missing or out of range. `same` may be left out
    (false); true is an older phone build's "Same on both" (the service stores two equal screens)."""
    w, layer = c.get("wallpaper"), c.get("layer")
    if not isinstance(w, dict) or not isinstance(w.get("parallax"), bool) \
            or not isinstance(w.get("same", False), bool):
        return None
    if not isinstance(layer, dict):
        return None
    lay = {}
    for p in SCREENS:
        x = layer.get(p)
        if isinstance(x, bool) or not isinstance(x, int) or not -100 <= x <= 100:
            return None
        lay[p] = x
    return {"format": FORMAT, "wallpaper": {"same": w.get("same", False), "parallax": w["parallax"]}, "layer": lay}


def ack(t, at, state: str, current_id: str | None = None, message: str | None = None,
        code: str | None = None) -> None:
    """`code` (with failed): why, for the phone to act on (wallpaper_unsaved: it asks whether to
    replace the wallpaper without a backup). A build without codes refuses the field; it gets the
    message alone."""
    if state not in ACK_STATES:
        raise ValueError(f"unknown ack state {state!r}")
    params = {"at": at, "state": state}
    if current_id is not None:
        params["current_id"] = current_id
    if message is not None:
        params["message"] = message
    if code is not None:
        try:
            return t.call(ACK, params | {"code": code})
        except Exception as e:
            if not _refused(e, ("code",)):
                raise
    t.call(ACK, params)
