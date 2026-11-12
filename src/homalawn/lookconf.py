"""Per-phone Omarchy look settings, stored in look.json next to config.toml."""
from __future__ import annotations

import copy
import json
import logging
import os
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

from . import config, phonekeys

log = logging.getLogger("homalawn.lookconf")

SOURCES = ("follow", "chosen", "off")
SCREENS = ("home", "lock")
# what a screen shows: keep (what it has now; the look leaves it alone), wallpaper (the theme's
# background: the pick, else the default) or gradient (made from the theme's colours, `style`)
KINDS = ("keep", "wallpaper", "gradient")
PICKED_BY = ("phone", "computer")
TARGETS = ("home", "lock", "both")          # only for the phone's older builds
PARTS = {"home": ("home",), "lock": ("lock",), "both": ("home", "lock")}
_OLD_KEYS = ("target", "screens", "separate_lock_crop", "background", "layer")


def theme_key(name: str) -> str:
    """A theme's key in `picks`: Omarchy's folder name ("Tokyo Night" -> "tokyo-night"), as look.slug."""
    return "-".join(str(name).strip().lower().split())


def screen_default() -> dict:
    return {"kind": "wallpaper", "style": None, "layer": 0}


def wallpaper_default() -> dict:
    return {"parallax": False, "home": screen_default(), "lock": screen_default()}


def _sync() -> dict:
    return {"colors": True, "mode": True, "icons": False}


def _old_pick(v) -> bool:
    return isinstance(v, dict) and "background" in v


def _is_old(d: dict) -> bool:
    """Anything of the older shape in `d`: no `wallpaper`, an old key, sync.wallpaper, an old pick
    or `wallpaper.same` (dropped 2026-10-17: the two screens are always set separately)."""
    sync, picks, wall = d.get("sync"), d.get("picks"), d.get("wallpaper")
    return ("wallpaper" not in d or any(k in d for k in _OLD_KEYS)
            or (isinstance(sync, dict) and "wallpaper" in sync)
            or (isinstance(picks, dict) and any(_old_pick(v) for v in picks.values()))
            or (isinstance(wall, dict) and "same" in wall))


def as_two_screens(d: dict) -> None:
    """A look of the older "Same on both" (`wallpaper.same` true) as two equal screens, in place:
    the lock screen gets the home screen's Screen, every theme's lock pick its home pick, and every
    crop the home one (lock None); a theme's lock-only pick goes. It showed exactly that, so
    nothing it showed changes. The `same` key goes either way."""
    wp = d.get("wallpaper")
    if not isinstance(wp, dict) or wp.pop("same", None) is not True:
        return
    wp["lock"] = copy.deepcopy(wp.get("home"))
    picks = d.get("picks") if isinstance(d.get("picks"), dict) else {}
    for k, v in list(picks.items()):
        if isinstance(v, dict):
            v["lock"] = v.get("home")
            if not v["home"]:                 # a lock-only pick: Same on both never showed it
                del picks[k]
    for c in (d.get("crops") or {}).values():
        if isinstance(c, dict):
            c["lock"] = None


def _old_picks(picks) -> dict:
    """Old picks ({background, lock, by}) as new ones ({home, lock, by}). Before, a lock screen
    without a pick of its own showed the home pick: that is now written out."""
    if picks is not None and not isinstance(picks, dict):
        raise ValueError("The picks setting must be an object.")
    out: dict = {}
    for k, v in (picks or {}).items():
        if _old_pick(v):
            v = {"home": v.get("background"), "lock": v.get("lock"), "by": v.get("by")}
            if v["lock"] is None:
                v["lock"] = v["home"]
        out[k] = copy.deepcopy(v)
    return out


def migrate(d: dict) -> tuple[dict, bool]:
    """`d` in the current shape, and whether it had to be converted from the older one
    (target / screens / background / layer / separate_lock_crop, or `wallpaper.same`; see the
    spec). An entry that already has a `wallpaper` object (written by both versions) keeps it: only
    the old keys go, its old picks get the new shape and a "Same on both" becomes two equal screens."""
    if not _is_old(d):
        return copy.deepcopy(d), False
    if isinstance(d.get("wallpaper"), dict):
        out = {k: copy.deepcopy(v) for k, v in d.items() if k not in _OLD_KEYS}
        if isinstance(out.get("sync"), dict):
            out["sync"].pop("wallpaper", None)
        out["picks"] = _old_picks(d.get("picks"))
        as_two_screens(out)
        return out, True
    for k in ("layer", "crops", "sync", "picks", "mapping"):
        if d.get(k) is not None and not isinstance(d[k], dict):
            raise ValueError(f"The {k} setting must be an object.")
    if d.get("background") is not None and not isinstance(d["background"], str):
        raise ValueError("The background must be text.")
    old_sync = d.get("sync") or {}
    target = d.get("target", "both")
    if d.get("screens") is not None:
        screens = d["screens"]
        if not isinstance(screens, dict):
            raise ValueError("The screens setting must be an object.")
    elif target in PARTS:
        screens = {p: {"kind": "wallpaper" if p in PARTS[target] else "keep"} for p in SCREENS}
    else:
        raise ValueError(f"Unknown target {target!r}: use home, lock or both.")
    if old_sync.get("wallpaper") is False:
        screens = {p: {"kind": "keep"} for p in SCREENS}
    layer = d.get("layer") or {}
    wp: dict = {"parallax": False}
    for p in SCREENS:
        c = screens.get(p)
        c = c if isinstance(c, dict) else {"kind": c}
        lay = layer.get(p) or 0
        if isinstance(lay, bool) or not isinstance(lay, (int, float)) or not -100 <= lay <= 100:
            raise ValueError(f"The {p} colour layer must be between -100 and 100.")
        wp[p] = {"kind": c.get("kind"), "style": c.get("style") if c.get("kind") == "gradient" else None,
                 "layer": int(lay)}
    picks: dict = {}
    for k, v in (d.get("picks") or {}).items():
        picks[k] = ({"home": v.get("background"), "lock": v.get("lock"), "by": v.get("by")}
                    if isinstance(v, dict) and (_old_pick(v) or "home" not in v) else copy.deepcopy(v))
    if d.get("source") == "chosen" and d.get("background") and d.get("theme"):
        key = theme_key(d["theme"])
        cur = picks.get(key) if isinstance(picks.get(key), dict) else None
        picks[key] = {"home": d["background"], "lock": cur["lock"] if cur else None,
                      "by": cur["by"] if cur else "computer"}
    crops = copy.deepcopy(d.get("crops") or {})
    separate = d.get("separate_lock_crop") is True
    for c in crops.values():
        if isinstance(c, dict) and not separate:
            c["lock"] = None
    for v in picks.values():
        if isinstance(v, dict) and v.get("lock") is None:   # before, a lock without its own pick
            v["lock"] = v.get("home")                       # showed the home pick
    out = {"source": d.get("source", "off"), "theme": d.get("theme"), "wallpaper": wp, "crops": crops,
           "picks": picks, "sync": {**_sync(), **{k: v for k, v in old_sync.items() if k in _sync()}},
           "mapping": copy.deepcopy(d.get("mapping") or {})}
    return out, True


@dataclass
class LookSettings:
    source: str = "off"
    theme: str | None = None
    # what each screen shows, set separately for the home and the lock screen, and parallax
    wallpaper: dict = field(default_factory=wallpaper_default)
    crops: dict = field(default_factory=dict)        # path -> {"home": float, "lock": float | None}
    # the picture chosen for a theme, on the phone or in the window:
    # theme key -> {"home": path | None, "lock": path | None, "by": "phone" | "computer"}
    picks: dict = field(default_factory=dict)
    sync: dict = field(default_factory=_sync)
    mapping: dict = field(default_factory=dict)
    # the monochrome icons' symbol colour chosen for a theme: theme key -> "#rrggbb" (none: the
    # theme's accent). Files written before it existed load with none.
    icon_colors: dict = field(default_factory=dict)

    def screen(self, p: str) -> dict:
        """The Screen `p` shows."""
        return copy.deepcopy(self.wallpaper[p])

    def screens_of(self) -> dict:
        return {p: self.screen(p) for p in SCREENS}

    def pick_for(self, theme: str | None, p: str = "home") -> str | None:
        """The picture chosen for this theme on screen `p`, while the file is still there."""
        e = self.picks.get(theme_key(theme)) if theme else None
        v = e.get(p) if isinstance(e, dict) else None
        return v if isinstance(v, str) and v and Path(v).is_file() else None

    def crop_for(self, path: str, p: str) -> float:
        c = self.crops.get(path) or {}
        home = c.get("home")
        if p == "lock" and c.get("lock") is not None:       # None: the lock screen's is the home one
            return float(c["lock"])
        return float(home) if home is not None else 0.5

    def parallax_on(self) -> bool:
        """The home screen's wallpaper scrolls with its pages: parallax is on and the home screen
        shows one of the theme's wallpapers. A gradient never scrolls (it goes at the screen's size)
        and a kept wallpaper is left alone, whatever `parallax` holds (it is remembered for when the
        home screen shows a theme wallpaper again)."""
        return bool(self.wallpaper["parallax"]) and self.wallpaper["home"]["kind"] == "wallpaper"

    def icon_color_for(self, theme: str | None) -> str | None:
        """The symbol colour chosen for this theme's monochrome icons, else None (its accent)."""
        v = self.icon_colors.get(theme_key(theme)) if theme else None
        return v if isinstance(v, str) else None

    def layer_of(self, p: str) -> int:
        return self.screen(p)["layer"]

    def follows_background(self, theme: str | None) -> bool:
        """A wallpaper screen has no picture of its own: Omarchy's background change matters."""
        return any(self.screen(p)["kind"] == "wallpaper" and not self.pick_for(theme, p) for p in SCREENS)

    def target(self) -> str | None:
        """"home", "lock" or "both": the screens the look changes; None when both are kept."""
        on = tuple(p for p in SCREENS if self.screen(p)["kind"] != "keep")
        return {("home",): "home", ("lock",): "lock", ("home", "lock"): "both"}.get(on)

    def to_dict(self) -> dict:
        return copy.deepcopy({
            "source": self.source, "theme": self.theme, "wallpaper": self.wallpaper,
            "crops": self.crops, "picks": self.picks, "sync": self.sync, "mapping": self.mapping,
            "icon_colors": self.icon_colors,
        })

    @classmethod
    def from_dict(cls, d: dict) -> "LookSettings":
        d, _ = migrate(d)
        base = cls()
        known = base.to_dict().keys()
        kw = {k: copy.deepcopy(v) for k, v in d.items() if k in known}
        if isinstance(kw.get("sync"), dict):        # keep missing sub-keys at their defaults
            kw["sync"] = {**base.sync, **kw["sync"]}
        return cls(**kw)


def _check_screen(p: str, c) -> dict:
    from . import gradients
    if not isinstance(c, dict) or c.get("kind") not in KINDS:
        raise ValueError(f"The {p} screen must be keep, wallpaper or gradient.")
    if c["kind"] == "gradient" and c.get("style") not in gradients.NAMES:
        raise ValueError(f"Unknown gradient for the {p} screen: {c.get('style')!r}.")
    lay = c.get("layer", 0)
    if isinstance(lay, bool) or not isinstance(lay, (int, float)) or not -100 <= lay <= 100:
        raise ValueError(f"The {p} colour layer must be between -100 and 100.")
    return {"kind": c["kind"], "style": c["style"] if c["kind"] == "gradient" else None, "layer": int(lay)}


def _rgb6(v) -> str | None:
    """"#rrggbb" in lower case, or None when `v` is not one."""
    if not isinstance(v, str) or len(v) != 7 or v[0] != "#":
        return None
    h = v[1:].lower()
    return "#" + h if all(c in "0123456789abcdef" for c in h) else None


def validate(d: dict) -> LookSettings:
    s = LookSettings.from_dict(d)
    if s.source not in SOURCES:
        raise ValueError(f"Unknown source {s.source!r}: use follow, chosen or off.")
    if s.theme is not None and not isinstance(s.theme, str):
        raise ValueError("The theme must be text.")
    for k in ("crops", "sync", "mapping", "picks", "icon_colors"):
        if not isinstance(getattr(s, k), dict):
            raise ValueError(f"The {k} setting must be an object.")
    if s.source == "chosen" and not s.theme:
        raise ValueError("Choosing a theme needs a theme name.")
    w = s.wallpaper
    if not isinstance(w, dict) or set(w) != {"parallax", "home", "lock"} or not isinstance(w["parallax"], bool):
        raise ValueError("The wallpaper setting must be {parallax, home, lock}.")
    for p in SCREENS:
        w[p] = _check_screen(p, w[p])
    for k, v in s.picks.items():
        ok = isinstance(v, dict) and v.get("by") in PICKED_BY and (v.get("home") or v.get("lock"))
        if ok:
            ok = all(v.get(f) is None or (isinstance(v[f], str) and v[f]) for f in ("home", "lock"))
        if not ok:
            raise ValueError(f"The wallpaper picked for {k} must be {{home and/or lock, by: phone or computer}}.")
        s.picks[k] = {"home": v.get("home"), "lock": v.get("lock"), "by": v["by"]}
    for k, v in s.icon_colors.items():
        c = _rgb6(v)
        if not isinstance(k, str) or not k or c is None:
            raise ValueError(f"The icon colour for {k} must be #rrggbb.")
        s.icon_colors[k] = c
    for bg, c in s.crops.items():
        if not isinstance(c, dict):
            raise ValueError(f"The crop for {bg} must be an object.")
        for k in ("home", "lock"):
            v = c.get(k)
            if k == "lock" and v is None:
                continue
            if isinstance(v, bool) or not isinstance(v, (int, float)) or not 0 <= v <= 1:
                raise ValueError(f"The {k} crop for {bg} must be between 0 and 1.")
    return s


def default_path() -> Path:
    return config.path().parent / "look.json"


def load(path: Path | None = None, peers: list[tuple[str, str]] | None = None) -> dict[str, LookSettings]:
    p = path or default_path()
    if not p.exists():
        return {}
    try:
        raw = json.loads(p.read_text())
        if not isinstance(raw, dict):
            raise ValueError("top level is not an object")
    except (OSError, ValueError) as e:
        log.warning("look settings %s unreadable (%s); using defaults", p, e)
        return {}
    if peers:                                 # name keys move to fingerprints, once
        new, moved = phonekeys.rekey(raw, peers)
        if moved:
            phonekeys.keep_once(p, ".names")
            _write(new, p)
            raw = new
    out: dict[str, LookSettings] = {}
    migrated: dict[str, dict] = {}
    for name, d in raw.items():
        try:
            if not isinstance(d, dict):
                raise ValueError("entry is not an object")
            out[name] = validate(d)
            if _is_old(d):
                migrated[name] = out[name].to_dict()
        except (ValueError, TypeError, AttributeError, OverflowError) as e:
            log.warning("look settings for phone %r are invalid (%s); using defaults", name, e)
            out[name] = LookSettings()
    if migrated:                              # once: the file now holds the current shape
        try:
            _backup(p)                           # the older file, kept once beside it
            _write({**raw, **migrated}, p)       # entries that did not validate stay as they were
        except OSError as e:
            log.warning("look settings %s could not be rewritten in the new shape (%s)", p, e)
    return out


def save(all: dict[str, LookSettings], path: Path | None = None) -> None:
    _write({n: s.to_dict() for n, s in all.items()}, path or default_path())


def _backup(p: Path) -> None:
    """`look.json.v1` beside `p`: the file as it was before its one-time rewrite to the current
    shape. Written only when it does not exist yet (never replaced), readable by the owner only."""
    phonekeys.keep_once(p, ".v1")


def _write(raw: dict, p: Path) -> None:
    p.parent.mkdir(parents=True, exist_ok=True)
    data = json.dumps(raw, indent=2)
    fd, tmp = tempfile.mkstemp(dir=p.parent, prefix=".look-", suffix=".tmp")
    try:
        with os.fdopen(fd, "w") as f:
            f.write(data)
        os.chmod(tmp, 0o600)
        os.replace(tmp, p)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise
