"""Local snapshots of the launcher database (and its journal files) for undo.

Every snapshot homalawn takes records which phone it came from in `meta.json` (`{"device":
"link:<phone certificate fingerprint>"}` or `"adb:<serial>"`). `undo` only walks the current
phone's snapshots; a snapshot without that record (taken by an older homalawn) is "unknown": never
undone automatically, restorable by name with --force only."""
from __future__ import annotations

import json
import re
import shutil
import time
from pathlib import Path

from . import env

KEEP_AUTO = 30
KEEP_LOOK = 20  # look- snapshots kept per phone (the service takes one on every Omarchy change)
META = "meta.json"  # which device the snapshot belongs to (and who made it); not one of its files
# Who makes the snapshots this process saves, recorded as meta.json's "by": "agent" in the agent
# tool server (`homalawn mcp` sets it), None (not recorded) elsewhere.
ORIGIN: str | None = None


def root() -> Path:
    p = env.get("DATA")
    base = Path(p) if p else env.data_dir()
    return base / "snapshots"


def _dir(name: str) -> Path:
    if not re.fullmatch(r"[A-Za-z0-9._-]+", name) or name in (".", ".."):
        raise ValueError(f"invalid snapshot name {name!r}")
    return root() / name


def auto_name() -> str:
    return "auto-" + time.strftime("%Y%m%d-%H%M%S")


def pre_restore_name() -> str:
    return "pre-restore-" + time.strftime("%Y%m%d-%H%M%S")


def save(name: str, files: dict[str, bytes], device: str | None = None, meta: dict | None = None) -> Path:
    """`device`: the key of the phone the files came from (see the module doc). `meta`: what the
    History list says about it (see describe): section, changes, theme, detail."""
    if META in files:
        raise ValueError(f"{META} is reserved")
    d = _dir(name)
    if d.exists():
        shutil.rmtree(d)
    d.mkdir(parents=True)
    for fname, data in files.items():
        (d / fname).write_bytes(data)
    record = {k: v for k, v in (("device", device), ("by", ORIGIN)) if v is not None} | _clean(meta or {})
    if record:
        (d / META).write_text(json.dumps(record))
    for prefix in ("auto-", "pre-restore-"):
        if name.startswith(prefix):
            for old in _with_prefix(prefix)[KEEP_AUTO:]:
                delete(old)
    if name.startswith("look-") and device is not None:
        _prune_looks(device, keep=name)
    return d


def _prune_looks(device: str, keep: str) -> None:
    """Keep the newest KEEP_LOOK look- snapshots of `device` (and always `keep`, the one just made)."""
    mine = [n for n in _with_prefix("look-") if n != keep and device_of(n) == device]
    for old in mine[KEEP_LOOK - 1:]:
        delete(old)


def list_() -> list[str]:
    """All snapshot names, newest first."""
    r = root()
    if not r.exists():
        return []
    dirs = [p for p in r.iterdir() if p.is_dir()]
    dirs.sort(key=lambda p: (p.stat().st_mtime_ns, p.name), reverse=True)
    return [p.name for p in dirs]


def _with_prefix(prefix: str) -> list[str]:
    return sorted((n for n in list_() if n.startswith(prefix)), reverse=True)


def list_auto() -> list[str]:
    """Automatic snapshots, newest first (the timestamp is in the name)."""
    return _with_prefix("auto-")


UNDO_PREFIXES = ("auto-", "prefs-", "grid-", "wallpaper-", "nova-", "look-", "home-")
_STAMP = re.compile(r"(\d{8}-\d{6})$")


def _meta(name: str) -> dict:
    try:
        v = json.loads((_dir(name) / META).read_text())
    except (OSError, ValueError):
        return {}
    return v if isinstance(v, dict) else {}


DESCRIBE = ("section", "changes", "theme", "detail")


def _clean(fields: dict) -> dict:
    out = {k: v for k, v in fields.items() if k in DESCRIBE and v is not None}
    if "section" in out and out["section"] not in SECTIONS:
        raise ValueError(f"unknown section {out['section']!r}")
    return out


def annotate(name: str, **fields) -> None:
    """Record what the History list says about a snapshot already saved (section, changes, theme,
    detail) in its meta.json, next to the device it came from."""
    if not isinstance(name, str) or not re.fullmatch(r"[A-Za-z0-9._-]+", name):
        return
    d = _dir(name)
    if not d.is_dir():
        return
    record = _meta(name) | _clean(fields)
    tmp = d / (META + ".tmp")
    tmp.write_text(json.dumps(record))
    tmp.replace(d / META)


def device_of(name: str) -> str | None:
    """The device key recorded for a snapshot, None when it has none (unknown)."""
    try:
        v = json.loads((_dir(name) / META).read_text()).get("device")
    except (OSError, ValueError, AttributeError):
        return None
    return v if isinstance(v, str) and v else None


def by_of(name: str) -> str | None:
    """Who made a snapshot ("agent": the agent tool server), None when not recorded."""
    try:
        v = json.loads((_dir(name) / META).read_text()).get("by")
    except (OSError, ValueError, AttributeError):
        return None
    return v if isinstance(v, str) and v else None


def list_undoable(device: str | None) -> list[str]:
    """Snapshots homalawn took of `device` before its own changes (layout, preferences, grid,
    wallpaper), newest first, as one chronological list. Manual and pre-restore snapshots, and
    snapshots of other or unknown devices, are not in it."""
    r = root()
    if device is None:
        return []
    names = [n for n in list_() if n.startswith(UNDO_PREFIXES) and device_of(n) == device]

    def key(n: str):
        m = _STAMP.search(n)
        return (m.group(1) if m else "", (r / n).stat().st_mtime_ns)
    return sorted(names, key=key, reverse=True)


KINDS = (("look-", "look"), ("prefs-", "prefs"), ("grid-", "layout"), ("auto-", "layout"), ("home-", "layout"),
         ("nova-", "layout"), ("wallpaper-", "wallpaper"), ("manual-", "manual"),
         ("pre-restore-", "before restore"))


def kind_of(name: str) -> str:
    """What a snapshot holds, from its name: look, layout, prefs, wallpaper, manual, before
    restore; "other" for names homalawn did not make."""
    return next((k for prefix, k in KINDS if name.startswith(prefix)), "other")


# The window's sections, as History names them (kind -> plain title). A snapshot records its
# section when it is made (meta.json "section"); an older one is placed by its name's prefix.
SECTIONS = {"home": "Home screen", "search": "Search & gestures", "style": "Wallpaper & style",
            "wallpaper": "Wallpaper", "manual": "Saved by hand", "before_restore": "Before a restore",
            "other": "Settings"}
_PREFIX_SECTION = (("pre-restore-", "before_restore"), ("look-", "style"), ("wallpaper-", "wallpaper"),
                   ("home-", "home"), ("auto-", "home"), ("grid-", "home"), ("nova-", "home"),
                   ("manual-", "manual"), ("prefs-", "other"))
_MADE = tuple(p for p, _ in _PREFIX_SECTION)


def section_of(name: str, meta: dict | None = None) -> str:
    """The window section a snapshot belongs to: the one recorded with it, else from its name; a
    name homalawn did not make is one saved by hand (`snapshot save NAME`)."""
    meta = _meta(name) if meta is None else meta
    if meta.get("section") in SECTIONS:
        return meta["section"]
    return next((sec for prefix, sec in _PREFIX_SECTION if name.startswith(prefix)), "manual")


def _plural(n: int, one: str, many: str) -> str:
    return f"{n} {one if n == 1 else many}"


def title_of(name: str, meta: dict | None = None) -> str:
    """The plain title History shows: "Home screen: 3 changes", "Wallpaper & style: Tokyo Night",
    "Search & gestures: 2 settings", "Saved by hand: <name>", "Before a restore", ... What was not
    recorded when the snapshot was made (older snapshots) leaves just the section's name."""
    meta = _meta(name) if meta is None else meta
    sec = section_of(name, meta)
    base = SECTIONS[sec]
    n = meta.get("changes")
    n = n if isinstance(n, int) and not isinstance(n, bool) and n >= 0 else None
    detail = meta.get("detail") if isinstance(meta.get("detail"), str) and meta.get("detail") else None
    if sec == "home":
        if n is not None:
            return f"{base}: {_plural(n, 'change', 'changes')}"
        return f"{base}: {detail}" if detail else base
    if sec == "search":
        return f"{base}: {_plural(n, 'setting', 'settings')}" if n is not None else base
    if sec == "style":
        theme = meta.get("theme")
        return f"{base}: {theme}" if isinstance(theme, str) and theme else base
    if sec == "manual":
        mine = not name.startswith(_MADE)    # manual-<time> is the name given when none was
        return f"{base}: {name}" if mine else base
    if sec == "other":
        return f"Setting: {detail}" if detail else base
    return base


def describe(name: str) -> dict:
    """{kind, title} of a snapshot for History (kind: a SECTIONS key)."""
    meta = _meta(name)
    return {"kind": section_of(name, meta), "title": title_of(name, meta)}


def time_of(name: str) -> float | None:
    """When a snapshot was taken (unix time, from the stamp in its name), None without one."""
    m = _STAMP.search(name)
    if not m:
        return None
    try:
        return time.mktime(time.strptime(m.group(1), "%Y%m%d-%H%M%S"))
    except ValueError:
        return None


def load(name: str) -> dict[str, bytes]:
    d = _dir(name)
    if not d.is_dir():
        raise FileNotFoundError(f"no snapshot named {name!r}")
    return {p.name: p.read_bytes() for p in sorted(d.iterdir()) if p.is_file() and p.name != META}


def delete(name: str) -> None:
    shutil.rmtree(_dir(name), ignore_errors=True)
