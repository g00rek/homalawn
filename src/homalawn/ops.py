"""The operations behind every homalawn command, shared by the CLI and the agent tool server.

Nothing here prints: each op returns a `Result` (the success line, the notes to show before it,
and the structured value) or raises `OpError` with the exact line the CLI prints on failure.
Messages that must reach the person while an op runs (which phone a write goes to, why the link
was not used) go to `Session.notify`.
"""
from __future__ import annotations

import hashlib
import json
import subprocess
import time
from pathlib import Path
from dataclasses import dataclass, field
from typing import Any, Callable

from . import apps as apps_mod, config, env, icons as icons_mod, look as look_mod, prefs as prefs_mod, snapshots, \
    widgets as widgets_mod
from .adb import AdbError, AdbTransport
from .layout import _component
from .session import DEFERRED_NOTE, HomeError, Launcher, LayoutInvalid, NotLoaded, WallpaperUnsaved, parse_spec
from .transport import NotSupported, ProviderError, Transport, TransportError


class OpError(Exception):
    """An op failed; the message is the line the CLI prints. `notes` are lines produced before the
    failure (default-home repair, ...) that still have to be shown, before the message.
    `transport` is True when the cause was the connection itself (not an answer from the phone),
    so a long-lived caller should drop the transport and choose again on the next call.
    `code` is the phone's error code (`shot_failed`, ...) when the phone answered with one, or
    INVALID for a layout the phone would not take; `problems` then locates each thing wrong with it
    (layout.problems)."""

    def __init__(self, message: str, notes: list[str] | None = None, transport: bool = False,
                 code: str | None = None, problems: list[dict] | None = None):
        super().__init__(message)
        self.notes = list(notes or [])
        self.transport = transport
        self.code = code
        self.problems = list(problems or [])


@dataclass
class Result:
    text: str
    notes: list[str] = field(default_factory=list)
    data: Any = None


@dataclass
class Session:
    """Which phone, and how: chosen once from these options (and HOMALAWN_SERIAL / HOMALAWN_PHONE),
    then kept until `reset`."""
    via: str | None = None
    serial: str | None = None
    phone: str | None = None
    notify: Callable[[str], None] = lambda m: None
    _t: Transport | None = field(default=None, init=False, repr=False)

    def transport(self) -> Transport:
        if self._t is None:
            self._t = _choose(self)
        return self._t

    def launcher(self) -> Launcher:
        return Launcher(self.transport())

    def reset(self) -> None:
        t, self._t = self._t, None
        if t is not None and hasattr(t, "close"):
            t.close()

    def target(self) -> str:
        """Which phone a change goes to: "via link (Pixel 8)" / "via adb (emulator-5560)"."""
        t = self.transport()
        return f"via {t.name} ({t.label})"


# -- which phone, and how ------------------------------------------------------------------------
def _via(t: Transport) -> str:
    return f"via {t.name} ({getattr(t, 'address', None) or t.label})" if t.name == "link" else f"via adb ({t.label})"


def _ping(t: Transport) -> dict:
    try:
        return t.call("launcher.ping")
    except ProviderError as e:
        raise OpError(f"Homa Lawn answered with an error on {t.label}: {e}")
    except AdbError as e:
        msg = str(e)
        pkg = getattr(getattr(t, "device", None), "package", None)
        if "provider" in msg.lower() or (pkg and pkg in msg):
            raise OpError(f"Homa Lawn ({pkg}) is not installed on {t.label}")
        raise OpError(f"adb failed on {t.label}: {msg}", transport=True)
    except TransportError as e:
        raise OpError(f"{t.name} failed on {t.label}: {e}", transport=True)


def _adb_devices() -> list[str]:
    try:
        out = subprocess.run(["adb", "devices"], capture_output=True, text=True, timeout=30).stdout
    except FileNotFoundError:
        raise OpError("adb is not installed or not on PATH (install Android platform-tools)")
    except subprocess.TimeoutExpired:
        raise OpError("adb did not answer within 30s")
    return [l.split()[0] for l in out.splitlines()[1:] if l.strip().endswith("device")]


def _single_device() -> str:
    devs = _adb_devices()
    if len(devs) != 1:
        raise OpError(f"Expected exactly one phone connected over adb, found {len(devs)}; name one with "
                      "`--serial SERIAL` (or HOMALAWN_SERIAL).")
    return devs[0]


def _link_to(peers, only_hosts: list[str] | None = None):
    """The first paired phone that answers over the link at its own addresses, or (None, why not).

    `only_hosts` (for `connect HOST`): dial exactly those addresses for each phone; an unknown
    certificate there is just "not a paired phone" (the caller then tries adb), not a hard error."""
    from .link.client import LinkDown, LinkTransport, NotPaired, PinMismatch
    from .link.identity import IdentityError
    reasons = []
    for peer in peers:
        t = LinkTransport(peer, only_hosts, only_hosts=only_hosts is not None)
        try:
            t.connect()
            return t, None
        except IdentityError as e:   # no openssl: this computer has no key to dial any phone with
            raise OpError(str(e))
        except PinMismatch as e:
            if not only_hosts:
                raise OpError(str(e))
            return None, str(e).rstrip(".")
        except (LinkDown, NotPaired) as e:
            if getattr(e, "forgotten", False):
                raise OpError(str(e))
            reasons.append(str(e).rstrip("."))
            if only_hosts and isinstance(e, LinkDown) and not t.other_seen:
                break  # the address itself does not answer; asking again for the next phone won't help
    return None, "; ".join(reasons)


def _explicit_serial(s: Session) -> str | None:
    """An adb device the user named for this run (--serial, else HOMALAWN_SERIAL). The serial saved
    in the config by `connect` is only a fallback, not a choice."""
    return s.serial or env.get("SERIAL") or None


def _named_phone(s: Session) -> str | None:
    return s.phone or env.get("PHONE") or None


def _peer_named(name: str | None):
    from . import phonekeys
    from .link import identity
    peers = identity.load_peers()
    try:
        fp = phonekeys.find(name, [(p.fp, p.name) for p in peers])
    except (phonekeys.NotFound, phonekeys.Ambiguous) as e:
        raise OpError(str(e))
    return next(p for p in peers if p.fp == fp)


def _address_input(raw: str) -> str:
    from .link import addresses
    try:
        return addresses.normalize(raw)
    except ValueError as e:
        raise OpError(str(e))


def _norm_or_raw(a: str) -> str:
    from .link import addresses
    try:
        return addresses.normalize(a)
    except ValueError:
        return a


def _address_rows(p) -> list[dict]:
    """The phone's addresses in the order they are dialled (last answered, last LAN, added, learned,
    paired through), each `{address, kind, removable, answered, saved}`: only "added" ones are removable,
    `answered` marks the one that answered last, `saved` is an added one's place in the saved list
    (the dial order puts the one that answered last first; the saved order is what is edited)."""
    saved = {_norm_or_raw(a): i for i, a in enumerate(p.addresses)}
    kinds = {}
    for kind, items in (("paired through", p.hosts), ("last LAN", [p.last_lan] if p.last_lan else []),
                        ("learned (Tailscale)", p.learned), ("added", p.addresses)):
        for a in items:
            kinds[_norm_or_raw(a)] = kind          # later kinds win: added beats learned beats LAN beats paired
    order = [_norm_or_raw(a) for a in [p.last_ok, p.last_lan] + p.addresses + p.learned + p.hosts if a]
    ok = _norm_or_raw(p.last_ok) if p.last_ok else None
    return [{"address": a, "kind": kinds.get(a, "last answered"), "removable": kinds.get(a) == "added",
             "answered": a == ok, "saved": saved.get(a)} for a in dict.fromkeys(order)]


def addresses_list(phone: str | None) -> Result:
    """The phone's addresses with their kind, in the order they are dialled; the one that answered
    last is marked. `data` has the rows (`_address_rows`)."""
    p = _peer_named(phone)
    rows = _address_rows(p)
    lines = [f"{r['address']}  {r['kind']}" + ("  answered last" if r["answered"] else "") for r in rows]
    if not lines:
        return Result(f"{p.name} has no addresses.", data=rows)
    if not all(r["removable"] for r in rows):
        lines.append("Only added addresses can be removed; the others are kept up to date by the phone and by connecting.")
    return Result(f"Addresses of {p.name}:\n" + _lines(lines), data=rows)


def addresses_add(phone: str | None, raw: str, first: bool = False) -> Result:
    """Add an address to the phone (`first`: dial it before the others). At the limit of 5 it is an
    error, nothing is dropped."""
    from .link import addresses, identity
    n = _address_input(raw)
    p = _peer_named(phone)

    def change(peer):
        if n not in peer.addresses and len(peer.addresses) >= addresses.MAX_ADDRESSES:
            raise OpError(f"{peer.name} has {addresses.MAX_ADDRESSES} addresses; remove one first.")
        rest = [a for a in peer.addresses if a != n]
        peer.addresses = [n] + rest if first else (peer.addresses if n in peer.addresses else rest + [n])
    if not identity.change_peer(p.fp, change):
        raise OpError(f"{p.name} is no longer paired.")
    return Result(f"Address {n} added for {p.name}.")


def addresses_remove(phone: str | None, raw: str) -> Result:
    from .link import identity
    n = _address_input(raw)
    p = _peer_named(phone)

    def change(peer):
        if n not in peer.addresses:
            raise OpError(f"{peer.name} has no address {raw.strip()}.")
        peer.addresses = [a for a in peer.addresses if a != n]
        if peer.last_ok and _norm_or_raw(peer.last_ok) == n:   # it must not stay dialled first
            peer.last_ok = None
        if peer.last_lan and _norm_or_raw(peer.last_lan) == n:
            peer.last_lan = None
    if not identity.change_peer(p.fp, change):
        raise OpError(f"{p.name} is no longer paired.")
    return Result(f"Address {n} removed from {p.name}.")


def addresses_set(phone: str | None, raw: list[str]) -> Result:
    """Replace the phone's added addresses by `raw`, in that order (the window's Apply). At most 5;
    one bad address changes nothing. An address no longer added stops being dialled first."""
    from .link import addresses, identity
    new = list(dict.fromkeys(_address_input(r) for r in raw))
    p = _peer_named(phone)
    if len(new) > addresses.MAX_ADDRESSES:
        raise OpError(f"{p.name} has {addresses.MAX_ADDRESSES} addresses; remove one first.")

    def change(peer):
        gone = {a for a in peer.addresses if a not in new}
        peer.addresses = new
        if peer.last_ok and _norm_or_raw(peer.last_ok) in gone:
            peer.last_ok = None
        if peer.last_lan and _norm_or_raw(peer.last_lan) in gone:
            peer.last_lan = None
    if not identity.change_peer(p.fp, change):
        raise OpError(f"{p.name} is no longer paired.")
    return Result(f"Addresses of {p.name} saved.")


SEVERAL = "several_phones"   # OpError.code: several phones are paired and none was named
SEVERAL_HOW = "name one with `--phone NAME` (or HOMALAWN_PHONE), or use `--serial SERIAL` for adb"
SEVERAL_PHONES = "Several phones are paired ({names}); " + SEVERAL_HOW + "."


def _choose(s: Session) -> Transport:
    """Which phone, and how:
    - a named adb device (--serial / HOMALAWN_SERIAL) without --via: adb to it, never the link;
    - otherwise the paired link (the phone named by --phone / HOMALAWN_PHONE; with several paired
      phones one must be named), else adb (the configured serial or the one attached device);
    - `--via` forces either. Falling back from a paired link says why, through `notify`; a phone
      named with --phone never falls back to adb (adb might be another device)."""
    from .link import identity
    via = s.via
    explicit = _explicit_serial(s)
    if via is None and explicit:
        return AdbTransport(explicit)
    cfg = config.load()
    why = None
    if via != "adb":
        peers = identity.load_peers()
        named = _named_phone(s)
        if not peers and via == "link":
            raise OpError("No phone is paired; run `homalawn pair` first.")
        if named:
            peers = [_peer_named(named)]
        elif len(peers) > 1:
            raise OpError(SEVERAL_PHONES.format(names=", ".join(p.name for p in peers)), code=SEVERAL)
        if peers:
            t, why = _link_to(peers)
            if t is not None:
                return t
            if via == "link" or named:
                raise OpError(why)
    serial = explicit or cfg.serial
    if not serial:
        devs = _adb_devices()
        if len(devs) != 1:
            raise OpError((f"{why}. " if why else "") + "No phone could be reached: pair one with `homalawn pair`, "
                          "or connect one over adb with `homalawn connect [HOST]`.")
        serial = devs[0]
    if why:
        s.notify(f"{why}. Using adb ({serial}) instead.")
    return AdbTransport(serial)


# -- shared write machinery ----------------------------------------------------------------------
def _writer(s: Session) -> Launcher:
    """The Launcher for a write, after saying (through `notify`) which phone it is about to change."""
    s.notify(s.target())
    return s.launcher()


def _known(la: Launcher) -> set[str] | None:
    """Components the phone can launch (over adb from the package manager, over the link from
    Lawnchair's own app list)."""
    out = set()
    for a in apps_mod.list_apps(la.transport):
        act = a["activity"]
        out.add(f"{a['package']}/{a['package'] + act if act.startswith('.') else act}")  # layout.validate expands '.X'
    return out


def _resolve(la: Launcher, spec: str) -> str:
    """A bare package becomes package/activity when the phone has exactly one launcher activity for it."""
    if "/" in spec:
        return spec
    try:
        acts = [a for a in apps_mod.list_apps(la.transport) if a["package"] == spec]
    except NotSupported as e:
        raise OpError(str(e))
    if not acts:
        raise OpError(f"{spec} has no launchable activity on this phone")
    if len(acts) > 1:
        raise OpError(f"{spec} has several launcher activities; pick one: "
                      + ", ".join(f"{a['package']}/{a['activity']}" for a in acts))
    return f"{spec}/{acts[0]['activity']}"


def _run(s: Session, fn, notes: list[str] | None = None):
    try:
        return fn()
    except WallpaperUnsaved as e:
        raise OpError(str(e), notes, code=WallpaperUnsaved.code)
    except LayoutInvalid as e:
        raise OpError(str(e), notes, code=INVALID, problems=e.problems)
    except (ValueError, LookupError, FileNotFoundError, HomeError) as e:
        raise OpError(str(e).strip("'\""), notes)
    except NotSupported as e:
        raise OpError(str(e), notes)
    except NotLoaded as e:   # the phone answered; only its home screen did not reload in time
        t = s._t
        raise OpError(f"{t.name if t is not None else 'connection'} failed: {e}", notes, code=NOT_LOADED)
    except TransportError as e:
        t = s._t
        raise OpError(f"{t.name if t is not None else 'connection'} failed: {e}", notes,
                      transport=not isinstance(e, ProviderError), code=getattr(e, "code", None))


def _write(s: Session, la: Launcher, fn, notes: list[str], require_home: bool = True):
    """Run a write: refuse if Lawnchair is not the default home, repair it if the write lost it
    (saying so in `notes`); a reload a locked phone leaves for its unlock is noted too."""
    def guarded():
        with la.home_guard(require=require_home, notify=notes.append):
            return fn()
    out = _run(s, guarded, notes)
    if la.deferred:   # written; a locked phone's home screen shows it at the unlock
        notes.append(DEFERRED_NOTE)
    return out


def _lines(lines) -> str:
    return "\n".join(lines)


# -- reads ---------------------------------------------------------------------------------------
NOT_LOADED = "not_loaded"   # OpError.code: Lawnchair did not reload its home screen in time
INVALID = "invalid"         # OpError.code: a layout or setting the phone would not take (nothing changed)


def _selected_fp(s: Session, peers) -> str | None:
    """The paired phone this session works with: the named one, else the one it is connected to,
    else the only one paired; None when several are paired and none was chosen (or adb was)."""
    named = _named_phone(s)
    if named:
        try:
            return _peer_named(named).fp
        except OpError:
            return None
    t = s._t
    if t is not None and getattr(t, "peer", None) is not None:
        return t.peer.fp
    if s.via == "adb" or _explicit_serial(s):
        return None
    return peers[0].fp if len(peers) == 1 else None


def _answers(peer) -> bool:
    try:
        t, _ = _link_to([peer])
    except OpError:   # something else answers at its address
        return False
    if t is None:
        return False
    t.close()
    return True


def phone_list(s: Session) -> Result:
    """Every paired phone: its name, whether it answers over the link now (all are dialled at
    once), and which one this session works with. data: [{name, id, connected, selected}]."""
    from concurrent.futures import ThreadPoolExecutor
    from .link import identity
    peers = identity.load_peers()
    if not peers:
        return Result("No phone is paired yet: pairing is done on the computer (`homalawn pair`, or Add phone in "
                      "the Homa Lawn window), with a tap on the phone.", data=[])
    sel = _selected_fp(s, peers)
    held = s._t.peer.fp if s._t is not None and getattr(s._t, "peer", None) is not None else None
    others = [p for p in peers if p.fp != held]
    with ThreadPoolExecutor(max(1, len(others))) as pool:
        up = dict(zip((p.fp for p in others), pool.map(_answers, others)))
    up[held] = True
    rows = [{"name": p.name, "id": p.fp[:16], "connected": up[p.fp], "selected": p.fp == sel} for p in peers]
    lines = [f"{r['name']}: {'connected' if r['connected'] else 'offline'}" + (", selected" if r["selected"] else "")
             for r in rows]
    if sel is None:
        t = s._t
        lines.append(f"None is selected: working with the phone on adb ({t.label}); phone_select picks a paired one."
                     if t is not None and t.name == "adb" else
                     "None is selected: call phone_select with the name of the phone the user means.")
    return Result(_lines(lines), data=rows)


def phone_select(s: Session, name: str) -> Result:
    """Work with this paired phone from now on (over the link), whatever was chosen before."""
    peer = _peer_named(name)
    s.reset()
    s.phone, s.via, s.serial = peer.fp, "link", None
    try:
        s.transport()
    except OpError as e:
        return Result(f"Selected {peer.name}, but it does not answer now: {str(e).rstrip('.')}. Tools for the "
                      "phone fail until it is back.")
    return Result(f"Selected {peer.name}; it is connected.")


def status(s: Session) -> Result:
    t = s.transport()
    info = _ping(t)
    la = Launcher(t)
    c = r = h = None
    try:
        c, r = la.grid()
        h = la.hotseat()
        grid_txt = f"grid {c}x{r} (columns x rows), dock {h}"
    except TransportError as e:
        grid_txt = f"grid unknown ({e})"
    db = info.get("db")
    how = f"over the link ({getattr(t, 'address', None) or t.label})" if t.name == "link" else f"over adb ({t.label})"
    text = _lines([f"Phone: {t.label}, {how}", f"Homa Lawn: {info['version']}",
                   f"Home app: {'yes' if info['default_home'] else 'no'}", grid_txt[0].upper() + grid_txt[1:]])
    return Result(text, data={"via": t.name, "phone": t.label, "version": info["version"],
                              "build": _build(t, info), "package": info.get("package"),
                              "address": getattr(t, "address", None),
                              "grid": [c, r] if h is not None else None, "dock": h, "db": db,
                              "default_home": info["default_home"], "permissions": permissions_of(info)})


PERMISSIONS = ("home", "background", "notifications", "screen_control", "install_updates")


def permissions_of(ping: dict) -> dict | None:
    """What the phone has allowed Homa Lawn, as launcher.ping reports it on builds that do
    ({home, background, notifications, screen_control, install_updates}: true/false); None from a
    build that does not. A key the phone leaves out (or does not report as true/false) is None."""
    p = ping.get("permissions") if isinstance(ping, dict) else None
    if not isinstance(p, dict):
        return None
    return {k: p[k] if isinstance(p.get(k), bool) else None for k in PERMISSIONS}


def _build(t: Transport, ping: dict) -> str | None:
    """Which Homa Lawn build the phone runs, as a key that changes with every install. Our own
    builds all carry the same versionName and versionCode, so the version cannot tell them apart.
    A build with patch 0017's `installed_at` in launcher.ping (the package's lastUpdateTime) says it
    at no cost; an older one is asked for its APK's sha256 (`launcher.app_info`, hashed once per
    phone process). None when neither answers."""
    at = ping.get("installed_at")
    if isinstance(at, int) and not isinstance(at, bool) and at > 0:
        return f"installed:{at}"
    try:
        info = _optional(t, "launcher.app_info")
    except TransportError:
        return None
    sha = info.get("sha256") if info else None
    return f"sha256:{sha.lower()}" if isinstance(sha, str) and sha else None


def _optional(t: Transport, method: str):
    """One read the phone's build may not have: its answer, or None."""
    try:
        r = t.call(method, {})
    except (ProviderError, NotSupported):
        return None
    return r if isinstance(r, dict) else None


def phone_info(s: Session) -> Result:
    """What the phone says about itself: the address it answers on, the Homa Lawn app it runs, and
    what it has allowed Homa Lawn (default home app, screen control). Read-only: whether Homa Lawn may
    read the wallpaper (for backups) is not asked, since asking posts a notification on the
    phone; it is None (unknown)."""
    from . import update
    t = s.transport()
    ping = _ping(t)
    try:
        app = update.app_info(t)
    except update.UpdateError:
        app = None
    app = {k: app[k] for k in ("versionName", "versionCode", "sha256") if k in app} if app else {
        "versionName": ping.get("version")}
    try:
        st = _optional(t, "screen.status")
    except TransportError as e:
        raise OpError(f"{t.name} failed on {t.label}: {e}", transport=True)
    screen = st.get("enabled") if st else None
    perms = {"default_home": ping.get("default_home"),
             "screen_control": screen if isinstance(screen, bool) else None, "wallpaper_backup": None}
    reported = permissions_of(ping)
    if reported is not None:       # a build that reports them: its five answers join the ones above
        perms |= reported
    address = getattr(t, "address", None) or t.label
    return Result(_lines([f"{t.label} at {address}: Homa Lawn {app.get('versionName') or 'unknown'}"]),
                  data={"address": address, "app": app, "permissions": perms})


def apps(s: Session) -> Result:
    la = s.launcher()
    found = _run(s, lambda: apps_mod.list_apps(la.transport))
    return Result(_lines(f"{a['package']}/{a['activity']}" for a in found), data=found)


def widgets(s: Session) -> Result:
    la = s.launcher()
    found = _run(s, lambda: widgets_mod.list_providers(la.transport))
    lines = []
    for w in found:
        c = w.get("cells") or [1, 1]
        lines.append(f"{max(1, c[0])}x{max(1, c[1])}  {w['provider']}  {w.get('label', '')}")
    return Result(_lines(lines), data=found)


def _label(it: dict) -> str:
    if it["type"] == "app":
        return it["package"] + (f"/{it['activity']}" if it.get("activity") else "")
    if it["type"] == "widget":
        state = it.get("state")
        return f"widget {it['provider']} {it['w']}x{it['h']}" + (f" [{state}]" if state and state != "ready" else "")
    return it.get("title") or it["type"]


def _table(layout: dict) -> list[str]:
    lines = ["screen row col  name"]
    for sc in layout["screens"]:
        if not sc["items"]:
            lines.append(f"{sc['index']:>6} {'':>3} {'':>3}  (empty page)")
        for it in sc["items"]:
            lines.append(f"{sc['index']:>6} {it.get('y', 0):>3} {it.get('x', 0):>3}  {_label(it)}"
                         + (f" [folder, {len(it.get('items', []))} items]" if it["type"] == "folder" else ""))
            for kid in it.get("items", []) if it["type"] == "folder" else []:
                lines.append(f"{'':>6} {'':>3} {'':>3}    - {_label(kid)}")
    for it in layout["hotseat"]:
        lines.append(f"{'dock':>6} {'':>3} {it.get('x', 0):>3}  {_label(it)}")
    return lines


def layout_get(s: Session) -> Result:
    lay = _run(s, s.launcher().get_layout)
    return Result(_lines(_table(lay)), data=lay)


def _fmt(v) -> str:
    return "(default)" if v is None else json.dumps(v) if isinstance(v, list) else prefs_mod.value_text(v)


def _pref_line(key: str, e: dict) -> str:
    return f"{key}  [{e['store']}/{e['type']}]  {_fmt(e['value'])}"


def prefs_list(s: Session, filter_: str | None) -> Result:
    la = s.launcher()
    hits = {k: e for k, e in _run(s, lambda: prefs_mod.get_all(la.transport)).items()
            if not filter_ or filter_.lower() in k.lower()}
    return Result(_lines(_pref_line(k, e) for k, e in hits.items()), data=hits)


def prefs_get(s: Session, key: str) -> Result:
    la = s.launcher()
    e = _run(s, lambda: prefs_mod.get_all(la.transport)).get(key)
    if e is None:
        raise OpError(f"unknown preference {key!r}")
    return Result(_pref_line(key, e), data={"key": key, **e})


def screenshot(s: Session) -> Result:
    la = s.launcher()
    return Result("", data=_run(s, la.transport.screenshot))


def snapshot_list(s: Session) -> Result:
    """Every snapshot, newest first: {name, device, by, kind (the window section: home, search,
    style, wallpaper, manual, before_restore, other), title (what History shows), contents (what
    it holds, from its name: look, layout, prefs, wallpaper, manual, before restore, other)}."""
    names = _paired_names()
    rows = [{"name": n, "device": snapshots.device_of(n), "by": snapshots.by_of(n), **snapshots.describe(n),
             "contents": snapshots.kind_of(n)} for n in snapshots.list_()]
    for r in rows:
        r["phone"] = _phone_label(r["device"], names)
    return Result(_lines(f"{r['name']}  {r['phone']}  {r['title']}" for r in rows), data=rows)


def _paired_names() -> dict[str, str]:
    """{certificate fingerprint: the paired phone's name}."""
    from .link import identity
    try:
        return {p.fp: p.name for p in identity.load_peers()}
    except (OSError, ValueError):
        return {}


def _phone_label(device: str | None, names: dict[str, str]) -> str:
    """A snapshot's phone in words: the paired phone's name; an unknown one keeps a short id."""
    if not device:
        return "(unknown phone)"
    kind, _, key = device.partition(":")
    if kind == "link" and key:
        return names.get(key) or key[:8]
    return device


def _plural(n: int, one: str, many: str) -> str:
    return f"{n} {one if n == 1 else many}"


def _snapshot_words(name: str) -> str:
    """"Home screen: 3 changes (home-2026...)": a snapshot's History title, then its name."""
    return f"{snapshots.describe(name)['title']} ({name})"


def snapshot_save(s: Session, name: str | None = None) -> Result:
    la = s.launcher()
    name = name or snapshots.auto_name().replace("auto-", "manual-")
    files = _run(s, la._read_all)
    snapshots.save(name, files, device=la.device_key(), meta={"section": "manual"})
    return Result(f"Saved snapshot {name}", data=name)


# -- writes --------------------------------------------------------------------------------------
def layout_apply(s: Session, layout: dict) -> Result:
    la = _writer(s)
    notes: list[str] = []
    notes += _write(s, la, lambda: la.apply_layout(layout, _known(la)), notes) or []
    return Result("Layout applied. `homalawn undo` reverts it.", notes)


def place(s: Session, app: str, screen: int, x: int, y: int) -> Result:
    la = _writer(s)
    spec = _resolve(la, app)
    notes: list[str] = []
    notes += _write(s, la, lambda: la.place(spec, screen, x, y, _known(la)), notes) or []
    return Result(f"Placed {spec} on screen {screen} at ({x},{y})", notes, data=spec)


def remove(s: Session, target: str) -> Result:
    la = _writer(s)
    notes: list[str] = []
    notes += _write(s, la, lambda: la.remove(target), notes) or []
    return Result(f"Removed {target}", notes)


def folder_create(s: Session, name: str, apps: list[str], screen: int, x: int, y: int) -> Result:
    la = _writer(s)
    specs = [parse_spec(_resolve(la, a)) for a in apps]
    notes: list[str] = []
    notes += _write(s, la, lambda: la.folder_create(name, screen, x, y, specs, _known(la)), notes) or []
    return Result(f"Folder {name!r} created with {len(specs)} apps", notes)


# What an agent is told when `widget_add` returns before a person has finished the widget.
PENDING = {
    "needs setup": "it needs its setup screen finished: tap it (screen_tree shows it) and complete the setup, "
                   "then check with phone_layout.",
    "needs permission": "Android needs the user to allow widgets: ask them to tap Create (or Allow) on the "
                        "phone's dialog; do not tap it yourself (recipe widget-permission), then check with phone_layout.",
    "loading": "Homa Lawn has not loaded it yet: check with phone_layout in a moment.",
}


def widget_add(s: Session, provider: str, screen: int, x: int, y: int, w: int | None, h: int | None,
               wait: int = 120, progress: Callable[[str], None] | None = None,
               wait_for_person: bool = True) -> Result:
    """`progress` gets the lines the person must act on while the widget is waited for (Allow on
    the phone, finish its setup screen); `notify` when not given.

    `wait_for_person=False` (the agent tool server) never waits for a person: it returns as soon
    as the widget is ready, needs its setup screen, or needs Allow, and says which; data is then
    {"appWidgetId", "state"}."""
    la = _writer(s)
    notes: list[str] = []
    try:
        if wait_for_person:
            wid = _write(s, la, lambda: widgets_mod.add(la, provider, screen, x, y, w, h, wait_s=wait,
                                                        notify=progress or s.notify), notes)
        else:
            wid, state = _write(s, la, lambda: widgets_mod.place(la, provider, screen, x, y, w, h), notes)
    except (TimeoutError, RuntimeError) as e:
        raise OpError(str(e), notes)
    done = f"Widget added on screen {screen} at ({x},{y}), id {wid}. `homalawn undo` removes it."
    if wait_for_person:
        return Result(done, notes, data=wid)
    text = done if state == "ready" else f"Placed on screen {screen} at ({x},{y}); {PENDING[state]}"
    return Result(text, notes, data={"appWidgetId": wid, "state": state})


def grid(s: Session, cols: int, rows: int) -> Result:
    la = _writer(s)
    notes: list[str] = []
    snap = _write(s, la, lambda: la.set_grid(cols, rows), notes)
    c, r = _run(s, la.grid, notes)
    if snap:
        snapshots.annotate(snap, section="home", detail=f"grid {c}x{r}")
    return Result(f"Grid is {c}x{r}" + (f". Previous grid and layout saved as {snap}; `homalawn undo` puts them back."
                                       if snap else " (unchanged)."), notes, data={"grid": [c, r], "snapshot": snap})


def nova_import(s: Session, path: str, dry_run: bool = False, glance_row: bool | None = None) -> Result:
    """Move a Nova Launcher backup onto the phone (Lawnchair's own converter on the phone, applied
    with a snapshot first). `dry_run` changes nothing and says what would be placed and left out.
    `glance_row`: the "Add extra row to show At a Glance" switch of Lawnchair's restore screen (None keeps
    the phone's At a Glance setting). data: nova.as_data."""
    from . import nova
    try:
        backup = Path(path).read_bytes()
    except OSError as e:
        raise OpError(f"cannot read {path}: {e.strerror or e}")
    try:
        backup = nova.slim_backup(backup)  # checked here, once: a bad file never reaches the phone
    except nova.NovaError as e:
        raise OpError(f"{path}: {e}")
    notes: list[str] = []
    if dry_run:
        la = s.launcher()
        plan, snap, _ = _run(s, lambda: nova.run(la, backup, glance_row, dry_run=True), notes)
    else:
        la = _writer(s)
        plan, snap, extra = _write(s, la, lambda: nova.run(la, backup, glance_row, notify=s.notify), notes)
        notes += extra
        if snap:
            snapshots.annotate(snap, section="home", detail="imported from Nova")
    head = "Dry run, nothing changed on the phone." if dry_run else "Nova layout imported."
    return Result(_lines([head] + nova.summary(plan, dry_run, snap)), notes, data=nova.as_data(plan, snap, dry_run))


def pref_set(s: Session, key: str, value: str, store: str | None = None, type_: str | None = None) -> Result:
    la = _writer(s)
    notes: list[str] = []
    snap = _write(s, la, lambda: la.set_pref(key, value, store, type_), notes)
    snapshots.annotate(snap, section="home" if str(snap).startswith("grid-") else "other", detail=key)
    return Result(f"{key} = {value}. Previous values saved as {snap}; `homalawn undo` puts them back.", notes,
                  data=snap)


WALLPAPER_DONE = {"home": "Wallpaper set", "lock": "Lock screen wallpaper set",
                  "both": "Home and lock screen wallpaper set"}


def wallpaper(s: Session, path: str, no_backup: bool = False, target: str = "home", crop: str = "none") -> Result:
    """`target`: home, lock or both. `crop` "center": scale the picture to cover the phone's
    screen and cut out the middle, so the phone shows exactly that."""
    if target not in WALLPAPER_DONE:
        raise OpError(f"target must be home, lock or both, not {target!r}")
    if crop not in ("none", "center"):
        raise OpError(f"crop must be none or center, not {crop!r}")
    la = _writer(s)
    notes: list[str] = []

    def go():
        size = la.screen_size() if crop == "center" else None
        return size, la.set_wallpaper(str(path), backup=not no_backup, notify=notes.append, target=target, size=size)
    size, snap = _write(s, la, go, notes)
    cut = f" (cropped to the screen, {size[0]}x{size[1]})" if size else ""
    prev = "Previous ones" if target == "both" else "Previous one"
    return Result(f"{WALLPAPER_DONE[target]}{cut}." + (f" {prev} saved as {snap}; `homalawn undo` puts "
                                                       f"{'them' if target == 'both' else 'it'} back." if snap else ""),
                  notes, data=snap)


def look_key(theme_name: str, prefs, images: dict[str, bytes]) -> str:
    """A look's key in the phone's look book (spec 6.1): 16 lowercase hex of SHA-256 over the theme's
    name, the settings it writes and the digests of its rendered pictures."""
    h = hashlib.sha256()
    h.update(json.dumps({"theme": theme_name, "prefs": sorted(prefs),
                         "images": {p: hashlib.sha256(b).hexdigest() for p, b in sorted(images.items())}},
                        sort_keys=True).encode())
    return h.hexdigest()[:16]


def look_apply(s: Session, settings, theme=None, background: Path | None = None,
               overwrite_wallpaper: bool = False, parts: tuple | None = None,
               stamp: Callable[[str], int] | None = None) -> Result:
    """Put an Omarchy look on the phone in one undo step: the wallpapers (cut to the screen and
    tinted), the launcher colours, light or dark mode, and themed icons, as the settings allow.
    Per screen: `settings.wallpaper` holds one Screen each for home and lock ({kind, style, layer});
    `settings.screens_of()` gives them.
    A Screen shows the theme's wallpaper, a gradient made from the theme's colours (`style`), or
    nothing new (keep); `layer` tints it with the theme's colour. The picture for a wallpaper
    screen: the theme's pick for that screen (settings.picks[theme] home / lock), else the default (follow: Omarchy's current background; chosen: the theme's first),
    cut at that screen's crop (settings.crops). `theme` / `background` override what `settings`
    would pick. Parallax (`settings.parallax_on()`: on, and the home screen shows a theme wallpaper):
    the home picture is made twice the screen's width (look.parallax_size) so the launcher can
    scroll it, and the launcher preference pref_wallpaperScrolling is set to match whenever the
    home screen gets a new picture; otherwise the home picture is the screen's size (a gradient
    always: the preference goes off with it). A kept home screen leaves the preference alone. The
    lock screen never scrolls. `parts`: only these screens get a new
    picture (a choice made on the phone changes only the screens it names). A wallpaper that
    cannot be saved first fails with code `wallpaper_unsaved`, unless `overwrite_wallpaper` (the
    user agreed to replace it without that backup).
    Over the link the look goes into the phone's look book (session.Launcher.put_look) under its
    key (look_key), with `changed_at` = `stamp(key)` (default: now, epoch ms); the phone puts it on
    the screen only when this computer is its look source. `data` then also holds `key`,
    `changed_at`, `shown`, `source` (the source's name) and `mine` (this computer is the source)."""
    from . import gradients
    from .lookconf import LookSettings  # noqa: F401 (type of `settings`)
    if settings.source == "off" and theme is None:
        raise OpError("Wallpaper & style is off for this phone: choose its Source in the Homa Lawn window (Wallpaper & "
                      "style), then try again.")
    sync = {"colors": True, "mode": True, "icons": False} | settings.sync
    screens = settings.screens_of()
    shown = [p for p in ("home", "lock") if screens[p]["kind"] != "keep" and (parts is None or p in parts)]
    needs_bg = any(screens[p]["kind"] == "wallpaper" for p in shown)
    default_bg: Path | None = None       # the picture a screen without a pick of its own gets
    if theme is None:
        if settings.source == "follow":
            name, default_bg = look_mod.current()
            theme = look_mod.theme_named(name) if name else None
            key = name
            if theme is None and needs_bg and background is None and default_bg is None \
                    and any(not settings.pick_for(name, p) for p in shown if screens[p]["kind"] == "wallpaper"):
                raise OpError("Omarchy has no current background")
            if theme is None:
                raise OpError("Omarchy has no current theme" if not name else f"Omarchy theme {name!r} was not found")
        else:
            theme = look_mod.theme_named(settings.theme or "")
            if theme is None:
                raise OpError(f"Omarchy theme {settings.theme!r} was not found")
            key = theme.name
            default_bg = theme.backgrounds[0] if theme.backgrounds else None
    else:
        key = theme.name
        default_bg = (look_mod.current()[1] if settings.source == "follow" else
                      (theme.backgrounds[0] if theme.backgrounds else None))

    def picture_of(p) -> Path | None:
        """The picture screen `p` shows: the named one, else the pick for this theme on that screen,
        else the default."""
        if background is not None:
            return background
        pick = settings.pick_for(key, p)
        return Path(pick) if pick else default_bg
    paths = {p: picture_of(p) for p in shown if screens[p]["kind"] == "wallpaper"}
    if any(v is None for v in paths.values()):
        raise OpError("Omarchy has no current background" if settings.source == "follow"
                      else f"Theme {theme.name} has no background picture")
    if any(screens[p]["kind"] == "gradient" for p in shown) and not gradients.available(theme.colors):
        raise OpError(f"Theme {theme.name} has no colours to make a gradient from")
    parallax = settings.parallax_on()
    prefs: list[tuple[str, str]] = []
    if sync["colors"]:
        prefs += look_mod.palette_prefs(theme.colors, settings.mapping)
    mode = look_mod.mode_pref(theme.colors) if sync["mode"] else None
    if mode:
        prefs.append(mode)
    # the icons switch is the truth either way: on, monochrome icons with the symbol in the colour
    # chosen for this theme on its plate; off, themed icons off and both colours back to empty
    # (patch 0023: empty = Lawnchair's accent), so turning it off really undoes turning it on
    if sync["icons"]:
        prefs.append(("themed_icons", "true"))
        prefs += look_mod.icon_prefs(theme.colors, settings.icon_color_for(key))
    else:
        prefs += [("themed_icons", "false"), (look_mod.ICON_COLOR_PREF, ""), (look_mod.ICON_BG_PREF, "")]
    if "home" in shown:       # the lock screen never scrolls; a kept home screen leaves it alone
        prefs.append((look_mod.PARALLAX_PREF, "true" if parallax else "false"))
    la = _writer(s)
    notes: list[str] = []

    def source(p, size):
        c = screens[p]
        if p == "home" and parallax:
            size = look_mod.parallax_size(size)
        if c["kind"] == "gradient":
            return ("gradient", c["style"], 0.5, settings.layer_of(p), size)
        bg = str(paths[p])
        return ("wallpaper", bg, round(settings.crop_for(bg, p), 4), settings.layer_of(p), size)

    def go():
        images: dict[str, bytes] = {}
        if shown:
            screen = la.screen_size()
            want = {p: source(p, screen) for p in shown}
            datas: dict[str, bytes] = {}

            def picture(kind, what, offset, amount, size):
                if kind == "gradient":
                    return look_mod.render(gradients.render(theme.colors, what, size), size, 0.5, amount)
                if what not in datas:
                    try:
                        datas[what] = Path(what).read_bytes()
                    except OSError as e:
                        raise OpError(f"Cannot read the background {what}: {e}")
                return look_mod.render(datas[what], size, offset, amount)
            if len(want) == 2 and want["home"] == want["lock"] and not parallax:
                images["both"] = picture(*want["home"])
            else:
                for p, w in want.items():
                    images[p] = picture(*w)
        if link:
            k = look_key(theme.name, prefs, images)   # over what is sent, so after the pictures exist
            changed = stamp(k) if stamp is not None else int(time.time() * 1000)
            lp = la.put_look(images, prefs, k, changed, theme.name, notify=notes.append,
                             overwrite_wallpaper=overwrite_wallpaper)
            put.update(key=k, changed_at=changed, shown=lp.shown, source=lp.source, mine=lp.mine, scope=lp.scope)
            return lp.snapshot, list(images)
        if overwrite_wallpaper:
            return la.apply_look(images, prefs, notify=notes.append, overwrite_wallpaper=True), list(images)
        return la.apply_look(images, prefs, notify=notes.append), list(images)
    link = la.transport.name == "link"
    put: dict = {}
    snap, sent = _write(s, la, go, notes)
    used_bg = next((paths[p] for p in shown if p in paths), None)   # home's picture, else lock's
    # data: what was actually used, so a long-running caller can tell when the phone is behind
    used = {"snapshot": snap, "theme": theme.name, "background": str(used_bg) if sent and used_bg else None}
    if link:
        scope = put.pop("scope")
        used |= put
        if not put["shown"]:   # nothing on the screen changed: no snapshot was taken
            if put["mine"]:
                return Result(f"Already on {la.transport.label}: its screen shows this wallpaper & style.", notes,
                              data=used)
            return Result(f"Kept on {la.transport.label}: it shows {put['source'] or 'another computer'}'s wallpaper & style"
                          + (" (chosen on the phone)." if scope == "chosen" else " (it changed last)."),
                          notes, data=used)
    if not snap:
        return Result("Nothing to apply: wallpaper & style sends nothing to this phone (Wallpaper and Colors & icons "
                      "are both off).", notes, data=used)
    snapshots.annotate(snap, section="style", theme=theme.name)

    def said(p):
        c = screens[p]
        if c["kind"] == "gradient":
            return f"{gradients.NAMES[c['style']]} gradient"
        return paths[p].name
    if not sent:
        what = "colors only"
    elif len(shown) == 2 and said("home") == said("lock"):
        what = f"{said('home')}: home and lock"
    else:
        what = ", ".join(f"{said(p)}: {p}" for p in shown)
    return Result(f"Wallpaper & style applied ({theme.name}, {what}). The previous one is saved as {snap}; "
                  "`homalawn undo` puts it back.",
                  notes, data=used)


def look_state(s: Session) -> dict:
    """The phone's look book as it tells it: {source: {fp, name}, scope, you: {key, shown}, applied}."""
    return s.transport().call("launcher.look_state")


# -- wallpapers picked on the phone (phonewall, patch 0017) ------------------------------------
def walls_push(s: Session, view) -> int:
    """The phone's "Wallpaper from Omarchy" screen: the theme's backgrounds and the thumbnails it lacks."""
    from . import phonewall
    return phonewall.push(s.transport(), view)


def walls_choice(s: Session) -> dict | None:
    """A wallpaper tapped on the phone and not yet received: {"id", "at"}, else None."""
    from . import phonewall
    return phonewall.choice(s.transport())


def walls_poll(s: Session) -> tuple[dict | None, bool]:
    """The phone's pending choice (as walls_choice) and whether it asks for the whole list again."""
    from . import phonewall
    return phonewall.poll(s.transport())


def walls_ack(s: Session, at, state: str, current_id: str | None = None, message: str | None = None,
              code: str | None = None) -> None:
    from . import phonewall
    phonewall.ack(s.transport(), at, state, current_id, message, code)


def app_update(s: Session, apk: Path | None = None) -> Result:
    """`homalawn update` for one phone over the link: `apk`, else the app update.pick_apk offers it
    (this checkout's build for the app it runs, else the newest release for the release app).
    text: the last line of what happened, notes: the lines before it; data {"outcome"}."""
    from . import update
    t = s.transport()
    if t.name != "link":
        raise OpError("Updates go over the Homa Lawn link; for adb use `homalawn setup`.")
    said: list[str] = []
    try:
        outcome = update.send(said.append, t, update.pick_apk(apk) if apk is not None else None)
    except update.UpdateError as e:
        raise OpError(str(e), said)
    except TransportError as e:
        raise OpError(f"{t.name} failed: {e}", said, transport=True)
    return Result(said[-1] if said else "Done.", said[:-1], data={"outcome": outcome})


def snapshot_restore(s: Session, name: str, force: bool = False, no_backup: bool = False) -> Result:
    la = _writer(s)
    notes: list[str] = []
    notes += _write(s, la, lambda: la.restore(name, force=force, no_backup=no_backup), notes,
                    require_home=False) or []
    return Result(f"Restored: {_snapshot_words(name)}", notes, data=name)


def undo(s: Session, no_backup: bool = False) -> Result:
    la = _writer(s)
    notes: list[str] = []
    name, dropped = _write(s, la, lambda: la.undo(no_backup=no_backup), notes, require_home=False)
    notes += dropped or []
    return Result(f"Undid: {_snapshot_words(name)}", notes, data=name)


# -- home-screen editor --------------------------------------------------------------------------
# The editor's icon and label settings: name in home.get / home.apply -> (Lawnchair key, default,
# lowest, highest). The ranges are Lawnchair's own sliders; the label gap is ours (patches 0014,
# 0025): the visible distance from the icon's shape to the label's capitals, 0 = touching. The phone
# holds it at the most its cell has room for and reports both (geometry icon.label_gap).
HOME_LOOK = {"icon_scale": ("home_icon_size_factor", 1.0, 0.5, 1.5),
             "label_scale": ("home_icon_label_size_factor", 1.0, 0.5, 1.5),
             "label_gap_dp": ("homar_label_gap_dp", 0.0, 0.0, 32.0)}
HOME_SHOW_LABELS = ("show_labels", "show_icon_labels_on_home_screen", True)


def _layout_components(layout: dict) -> list[str]:
    """Every app on the home screen, folders included, as package/full.Activity."""
    out: list[str] = []

    def walk(items):
        for i in items:
            if i.get("type") == "app" and i.get("package") and i.get("activity"):
                out.append(_component(i))
            if i.get("type") == "folder":
                walk(i.get("items", []))
    walk(layout.get("hotseat", []))
    for sc in layout.get("screens", []):
        walk(sc.get("items", []))
    return list(dict.fromkeys(out))


def _pref_value(cur: dict, key: str):
    for store in ("datastore", "shared"):
        e = cur.get(store, {}).get(key)
        if e is not None:
            return e.get("value")
    return None


def _cache(la: Launcher, size: int | None) -> icons_mod.IconCache:
    return icons_mod.IconCache(la.device_key(), size=size or icons_mod.SIZE)


def home_get(s: Session, size: int | None = None) -> Result:
    """The home screen as the editor draws it: grid, dock width, every screen's items, the icon and
    label settings, a cached picture for every app on it (component -> file path), and the phone's
    own home-screen geometry (`geometry`, Launcher.geometry)."""
    la = s.launcher()

    def go():
        cur = prefs_mod.current(la.transport)
        lay = la.get_layout(cur)
        c, r, h = la._grid3()
        pics = _cache(la, size).apps(la.transport, _layout_components(lay))
        look = {}
        for name, (key, default, _, _) in HOME_LOOK.items():
            v = _pref_value(cur, key)
            look[name] = float(v) if isinstance(v, (int, float)) and not isinstance(v, bool) else default
        geometry = la.geometry()
        # the label gap the phone draws (the setting held at what its cell has room for)
        drawn = ((geometry or {}).get("icon") or {}).get("label_gap")
        if drawn:
            look["label_gap_dp"] = float(drawn["dp"])
        shown = _pref_value(cur, HOME_SHOW_LABELS[1])
        return {"grid": [c, r], "hotseat_count": h, "screens": lay["screens"], "hotseat": lay["hotseat"],
                "top_row_reserved": Launcher.top_row_reserved_in(cur), **look,
                "show_labels": shown if isinstance(shown, bool) else HOME_SHOW_LABELS[2],
                "icons": {comp: p["path"] for comp, p in pics.items() if p.get("path")},
                # where the phone draws (its own pixels); the window's picture is drawn from it
                "geometry": geometry}
    data = _run(s, go)
    n = sum(len(sc["items"]) for sc in data["screens"]) + len(data["hotseat"])
    return Result(f"{n} items on {len(data['screens'])} screens, grid {data['grid'][0]}x{data['grid'][1]}, "
                  f"dock {data['hotseat_count']}", data=data)


def home_apps(s: Session, size: int | None = None) -> Result:
    """Launcher apps for the editor's side list, by label: {component, package, activity, label,
    icon (file path or None)}."""
    la = s.launcher()

    def go():
        found = apps_mod.list_apps(la.transport)
        pics = _cache(la, size).apps(la.transport)
        out = []
        for a in found:
            comp = _component(a)
            p = pics.get(comp, {})
            out.append({"component": comp, "package": a["package"], "activity": a["activity"],
                        "label": p.get("label") or a.get("label") or a["package"], "icon": p.get("path")})
        return sorted(out, key=lambda a: (a["label"].casefold(), a["component"]))
    data = _run(s, go)
    return Result(_lines(f"{a['label']}  {a['component']}" for a in data), data=data)


def home_widgets(s: Session, size: int | None = None) -> Result:
    """Widget providers for the editor's side list, by label: {provider, label, cells [w, h],
    minW, minH (dp), preview (file path or None)}."""
    la = s.launcher()

    def go():
        found = widgets_mod.list_providers(la.transport)
        pics = {widgets_mod.normalize(k): v for k, v in _cache(la, size).widgets(la.transport).items()}
        out = []
        for w in found:
            c = w.get("cells") or [1, 1]
            p = pics.get(widgets_mod.normalize(w["provider"]), {})
            out.append({"provider": w["provider"], "label": w.get("label") or p.get("label") or w["provider"],
                        "cells": [max(1, int(c[0])), max(1, int(c[1]))], "minW": w.get("minW"),
                        "minH": w.get("minH"), "preview": p.get("path")})
        return sorted(out, key=lambda w: (str(w["label"]).casefold(), w["provider"]))
    data = _run(s, go)
    return Result(_lines(f"{w['cells'][0]}x{w['cells'][1]}  {w['provider']}  {w['label']}" for w in data), data=data)


def _home_prefs(look: dict | None) -> list[tuple[str, str]]:
    """The editor's icon and label settings as (Lawnchair key, value); a bad one raises OpError
    INVALID before anything is sent."""
    out: list[tuple[str, str]] = []
    bad: list[dict] = []
    for name, v in (look or {}).items():
        if name == HOME_SHOW_LABELS[0]:
            if not isinstance(v, bool):
                bad.append({"message": f"{name} must be true or false", "where": "setting", "setting": name})
                continue
            out.append((HOME_SHOW_LABELS[1], "true" if v else "false"))
            continue
        spec = HOME_LOOK.get(name)
        if spec is None:
            bad.append({"message": f"unknown setting {name!r} (one of {', '.join([*HOME_LOOK, HOME_SHOW_LABELS[0]])})",
                        "where": "setting", "setting": name})
            continue
        key, _, lo, hi = spec
        if isinstance(v, bool) or not isinstance(v, (int, float)) or not lo <= v <= hi:
            bad.append({"message": f"{name} must be a number from {lo:g} to {hi:g}", "where": "setting",
                        "setting": name})
            continue
        out.append((key, repr(float(v))))
    if bad:
        raise OpError("; ".join(b["message"] for b in bad), code=INVALID, problems=bad)
    return out


def home_apply(s: Session, layout: dict, grid: list[int] | None = None, hotseat: int | None = None,
               look: dict | None = None) -> Result:
    """The home-screen editor's draft in one undo step (Launcher.apply_home): the grid, the dock's
    width, the icon and label settings (HOME_LOOK names, plus show_labels) and the whole layout.
    A layout or setting the phone would not take fails with code INVALID and `problems`, before
    anything changed. data: {"snapshot"}."""
    prefs = _home_prefs(look)
    la = _writer(s)
    notes: list[str] = []
    snap, dropped = _write(s, la, lambda: la.apply_home(layout, tuple(grid) if grid else None, hotseat, prefs,
                                                        _known(la)), notes)
    notes += dropped or []
    return Result(f"Home screen applied. Previous one saved as {snap}; `homalawn undo` puts it back.", notes,
                  data={"snapshot": snap})


# -- search & gestures ---------------------------------------------------------------------------
# Everything below was read from the Lawnchair source in lawnchair/ (2026-10-15); labels are the
# ones Lawnchair's own settings screens show (lawnchair/res/values/strings.xml).
#
# Gestures: name -> (Lawnchair key, label, default type). Keys and defaults: PreferenceManager2.kt
# (doubleTapGestureHandler ... backPressGestureHandler); labels: GesturePreferences.kt's
# gesture_double_tap / gesture_swipe_up / gesture_swipe_down / gesture_home_tap / gesture_back_tap.
# The value is kotlinx's encoding of GestureHandlerConfig, {"type": <SerialName>}; a value
# Lawnchair cannot decode means the default (serializablePreference's getOrDefault).
SEARCH_GESTURES = {
    "double_tap": ("double_tap_gesture_handler", "Double tap", "sleep"),
    "swipe_up": ("swipe_up_gesture_handler", "Swipe up", "openAppDrawer"),
    "swipe_down": ("swipe_down_gesture_handler", "Swipe down", "openNotificationdata"),
    "home_press": ("home_press_gesture_handler", "Home button", "noOp"),
    "back_press": ("back_press_gesture_handler", "Back button", "noOp"),
}
# What a gesture can do, in the order of Lawnchair's picker (GestureHandlerPreference.kt `options`),
# SerialNames from GestureHandlerConfig.kt, labels gesture_handler_* (homar_gesture_search is ours,
# patch 0015). "Open app" (openApp) needs an app picked on the phone, so it is shown, never set here.
GESTURE_HANDLERS = (("noOp", "Do nothing"), ("sleep", "Sleep"), ("recents", "Open Recents screen"),
                    ("openNotificationdata", "Open notification panel"), ("openAppDrawer", "Open app drawer"),
                    ("openAppSearch", "Open app search"), ("homarSearch", "Search (instant)"),
                    ("openSearch", "Open search"), ("openAssistant", "Open assistant"))
GESTURE_OPEN_APP = "openApp"
# Lawnchair's picker leaves these out while the "Lawn deck" layout (enable_lawn_deck) is on: no drawer.
DECK_KEY = "enable_lawn_deck"
DECK_HIDDEN = frozenset({"openAppDrawer", "openAppSearch", "homarSearch"})
# Search switches: name -> (Lawnchair key, label, default, inverted). The search result sources are
# PreferenceManager.kt's BoolPrefs (shared store), the others PreferenceManager2.kt (datastore);
# defaults from there and from res/values/config.xml. Labels: DrawerSearchPreferences.kt. The
# search bar switch stores the opposite (hide_app_drawer_search_bar).
SEARCH_SWITCHES = {
    "search_bar": ("hide_app_drawer_search_bar", "Show search bar", False, True),
    "auto_keyboard": ("auto_show_keyboard_in_drawer", "Automatically show keyboard", False, False),
    "apps": ("pref_searchResultApps", "Apps and shortcuts", True, False),
    "web": ("pref_searchResultStartPageSuggestion", "Web suggestions", True, False),
    "people": ("pref_searchResultPeople", "People", False, False),
    "files": ("pref_searchResultFiles", "Files", False, False),
    "settings": ("pref_searchResultSettingsEntry", "Android settings", False, False),
    "history": ("pref_searchResultRecentSuggestion", "Search history", False, False),
    "calculator": ("pref_searchResultCalculator", "Calculator", False, False),
}
# Search choices: name -> (Lawnchair key, label, default, [(value, label)], [(value, label) shown only
# when already set]). web_suggestion_provider: WebSearchProvider.fromString / the providers' ids and
# search_provider_* labels ("custom" needs its URLs, entered on the phone); search_algorithm:
# LawnchairSearchAlgorithm's constants, labels search_algorithm_* ("via ASI" exists only on some phones).
SEARCH_CHOICES = {
    "algorithm": ("search_algorithm", "Search algorithm", "localSearch",
                  [("appSearch", "App Search"), ("localSearch", "Global search (on-device)")],
                  [("globalSearch", "Global search (via ASI)")]),
    "web_provider": ("web_suggestion_provider", "Web suggestion provider", "google",
                     [("google", "Google"), ("duckduckgo", "DuckDuckGo"), ("startpage", "Startpage"),
                      ("kagi", "Kagi")],
                     [("custom", "Custom")]),
}


def _gesture_type(raw) -> str | None:
    if not isinstance(raw, str):
        return None
    try:
        t = json.loads(raw).get("type")
    except (ValueError, AttributeError):
        return None
    return t if t in prefs_mod.GESTURE_TYPES else None


def _search_state(cur: dict) -> dict:
    """The page's view of the phone's settings: {gestures: {name: {type, app?}}, search: {name: value}}."""
    gestures = {}
    for name, (key, _, default) in SEARCH_GESTURES.items():
        raw = _pref_value(cur, key)
        t = _gesture_type(raw) or default
        g = {"type": t}
        if t == GESTURE_OPEN_APP:
            g["app"] = str((json.loads(raw) or {}).get("appName") or "")
        gestures[name] = g
    search = {}
    for name, (key, _, default, inverted) in SEARCH_SWITCHES.items():
        v = _pref_value(cur, key)
        v = v if isinstance(v, bool) else default
        search[name] = (not v) if inverted else v
    for name, (key, _, default, choices, rare) in SEARCH_CHOICES.items():
        v = _pref_value(cur, key)
        known = {c for c, _ in choices + rare}
        search[name] = v if v in known else default
    return {"gestures": gestures, "search": search}


def search_get(s: Session) -> Result:
    """Search & gestures as the page shows them: the phone's values (search_state), and what each
    can be set to with its label (gesture handlers already left out when the Lawn deck has no drawer)."""
    la = s.launcher()
    cur = _run(s, lambda: prefs_mod.current(la.transport))
    state = _search_state(cur)
    deck = _pref_value(cur, DECK_KEY) is True
    handlers = [{"type": t, "label": label} for t, label in GESTURE_HANDLERS if not (deck and t in DECK_HIDDEN)]
    fields = {
        "gestures": [{"name": n, "label": label} for n, (_, label, _) in SEARCH_GESTURES.items()],
        "switches": [{"name": n, "label": label} for n, (_, label, _, _) in SEARCH_SWITCHES.items()],
        "choices": [{"name": n, "label": label,
                     "options": [{"value": v, "label": lb} for v, lb in choices
                                 + [r for r in rare if r[0] == state["search"][n]]]}
                    for n, (_, label, _, choices, rare) in SEARCH_CHOICES.items()],
    }
    data = {**state, "handlers": handlers, "deck": deck, "fields": fields}
    lines = [f"{SEARCH_GESTURES[n][1]}: {dict(GESTURE_HANDLERS).get(g['type'], 'Open ' + g.get('app', 'app'))}"
             for n, g in state["gestures"].items()]
    lines += [f"{SEARCH_SWITCHES[n][1] if n in SEARCH_SWITCHES else SEARCH_CHOICES[n][1]}: {prefs_mod.value_text(v)}"
              for n, v in state["search"].items()]
    return Result(_lines(lines), data=data)


def _search_writes(cur: dict, gestures: dict | None, search: dict | None) -> list[tuple[str, str]]:
    """The page's changes as (Lawnchair key, value), only those that differ from the phone; every
    bad name or value raises OpError INVALID (with `problems`) before anything is sent."""
    bad: list[dict] = []
    out: list[tuple[str, str]] = []
    state = _search_state(cur)
    deck = _pref_value(cur, DECK_KEY) is True
    allowed = {t for t, _ in GESTURE_HANDLERS}
    for name, t in (gestures or {}).items():
        spec = SEARCH_GESTURES.get(name)
        if spec is None:
            bad.append({"message": f"unknown gesture {name!r} (one of {', '.join(SEARCH_GESTURES)})",
                        "where": "gesture", "setting": name})
        elif t not in allowed:
            why = "an app for it is picked on the phone" if t == GESTURE_OPEN_APP else \
                f"one of {', '.join(sorted(allowed))}"
            bad.append({"message": f"{spec[1]}: {t!r} cannot be set here ({why})", "where": "gesture", "setting": name})
        elif deck and t in DECK_HIDDEN:
            bad.append({"message": f"{spec[1]}: {dict(GESTURE_HANDLERS)[t]} needs the app drawer, which the "
                                   "Lawn deck layout turns off", "where": "gesture", "setting": name})
        elif state["gestures"][name]["type"] != t:
            out.append((spec[0], json.dumps({"type": t}, separators=(",", ":"))))
    for name, v in (search or {}).items():
        if name in SEARCH_SWITCHES:
            key, label, _, inverted = SEARCH_SWITCHES[name]
            if not isinstance(v, bool):
                bad.append({"message": f"{label} must be true or false", "where": "search", "setting": name})
            elif state["search"][name] != v:
                out.append((key, "true" if (not v if inverted else v) else "false"))
        elif name in SEARCH_CHOICES:
            key, label, _, choices, rare = SEARCH_CHOICES[name]
            ok = {c for c, _ in choices} | ({state["search"][name]} & {c for c, _ in rare})
            if v not in ok:
                bad.append({"message": f"{label}: {v!r} is not one of {', '.join(sorted(ok))}",
                            "where": "search", "setting": name})
            elif state["search"][name] != v:
                out.append((key, v))
        else:
            bad.append({"message": f"unknown search setting {name!r} "
                                   f"(one of {', '.join([*SEARCH_SWITCHES, *SEARCH_CHOICES])})",
                        "where": "search", "setting": name})
    if bad:
        raise OpError("; ".join(b["message"] for b in bad), code=INVALID, problems=bad)
    return out


def search_apply(s: Session, gestures: dict | None = None, search: dict | None = None) -> Result:
    """The Search & gestures page's draft in one undo step: `gestures` {name: handler type},
    `search` {name: true/false or a choice}. Only what differs from the phone is written, in one
    `prefs-` snapshot and one commit; nothing differing is a no-op without a snapshot. A bad name
    or value fails with code INVALID and `problems`, before anything changed. data: {"snapshot",
    "changed": [Lawnchair keys]}."""
    la = s.launcher()
    cur = _run(s, lambda: prefs_mod.current(la.transport))
    writes = _search_writes(cur, gestures, search)
    if not writes:
        return Result("Nothing to change: the phone already has these settings.", data={"snapshot": None, "changed": []})
    la = _writer(s)
    notes: list[str] = []
    snap = _write(s, la, lambda: la.set_prefs(writes), notes)
    snapshots.annotate(snap, section="search", changes=len(writes))
    return Result(f"Search & gestures applied. Previous settings saved as {snap}; `homalawn undo` puts them back.",
                  notes, data={"snapshot": snap, "changed": [k for k, _ in writes]})


# -- screen control ------------------------------------------------------------------------------
_SCREEN_WRITES = frozenset({"screen.tap", "screen.long_press", "screen.swipe", "screen.type", "screen.key",
                            "screen.launch"})


def _screen(s: Session, method: str, params: dict | None = None):
    """One screen.* call; 'screen control is off' and bad arguments become one line. An action
    (tap, type, key, ...) first says through `notify` which phone it acts on."""
    from . import screen as screen_mod
    t = s.transport()
    if method in _SCREEN_WRITES:
        s.notify(s.target())
    try:
        return t.call(method, params or {})
    except ProviderError as e:
        if e.code == screen_mod.SCREEN_OFF:
            try:
                st = t.call("screen.status")
            except TransportError:
                st = None
            raise OpError(screen_mod.off_message(st))
        raise OpError(str(e))
    except NotSupported as e:
        raise OpError(str(e))
    except TransportError as e:
        raise OpError(f"{t.name} failed: {e}", transport=True)


def _screen_args(fn, *args):
    from . import screen as screen_mod
    try:
        return fn(*args)
    except screen_mod.ScreenArgError as e:
        raise OpError(str(e))


def screen_status(s: Session) -> Result:
    from . import screen as screen_mod
    st = _screen(s, "screen.status")
    first = "Screen control is on." if st.get("enabled") else screen_mod.off_message(st)
    r = st.get("restricted")
    return Result(_lines([first, "Restricted settings: " + {
        True: "yes (blocked until allowed in App info)", False: "no",
        None: "unknown (Android decides from where Homa Lawn was installed)"}[r]]), data=st)


def screen_tree(s: Session) -> Result:
    from . import screen as screen_mod
    tree = _screen(s, "screen.tree")
    return Result(_lines(screen_mod.format_tree(tree)), data=tree)


def _where(p: dict) -> str:
    """A tap target in words: the item id (n12) or the point (540,1200)."""
    return p["node"] if "node" in p else f"{p['x']},{p['y']}"


def screen_tap(s: Session, point: dict) -> Result:
    return Result(f"Tapped {_where(point)}.", data=_screen(s, "screen.tap", dict(point)))


def screen_long_press(s: Session, point: dict, ms: int | None = None) -> Result:
    p = dict(point)
    if ms is not None:
        p["ms"] = ms
    return Result(f"Long-pressed {_where(point)}.", data=_screen(s, "screen.long_press", p))


def screen_swipe(s: Session, params: dict) -> Result:
    data = _screen(s, "screen.swipe", dict(params))
    return Result(f"Swiped from {params['x1']},{params['y1']} to {params['x2']},{params['y2']}.", data=data)


def screen_type(s: Session, text: str, node: str | None = None) -> Result:
    data = _screen(s, "screen.type", {"text": text, **({"node": node} if node else {})})
    return Result(f"Typed {_plural(len(text), 'character', 'characters')}.", data=data)


_KEY_WORDS = {"quick_settings": "Quick settings"}


def screen_key(s: Session, name: str) -> Result:
    from . import screen as screen_mod
    key = _screen_args(screen_mod.parse_key, name)
    return Result(f"Pressed {_KEY_WORDS.get(key, key.capitalize())}.", data=_screen(s, "screen.key", {"name": key}))


def screen_launch(s: Session, package: str) -> Result:
    r = _screen(s, "screen.launch", {"package": package})
    return Result(f"Opened {r.get('package', package)}.", data=r)


def screen_shot(s: Session) -> Result:
    from . import screen as screen_mod
    t = s.transport()
    try:
        data = screen_mod.shot(t)
    except NotSupported as e:
        raise OpError(str(e))
    except ProviderError as e:
        raise OpError(str(e), code=e.code)
    except TransportError as e:
        raise OpError(f"{t.name} failed: {e}", transport=True)
    return Result("", data=data)
