"""The Homa Lawn service (`homalawn serve`): one link per paired phone, the Omarchy look follower, and a
unix socket the Omarchy shell plugin talks to.

Protocol: one JSON object per line. Request `{"id": n, "method": str, "params": {}}`; reply
`{"id": n, "result": ...}` or `{"id": n, "error": {"code": str, "message": str}}`. After
`subscribe`, every change sends `{"event": "state", "state": {...}}`, and the Agents page's chat
sends `{"event": "chat", "chat": {...}}` as the agent answers. Lines over 8 MiB are dropped.
Only the user running the service may connect (SO_PEERCRED).

Phone work (connection checks, applies, undo) runs in threads; one lock per phone keeps its link
to one call at a time, so one slow phone never holds up another.

Errors: `code` is for the window's logic and never changes meaning; `message` is one sentence a
person can read (no errno text, no exception class names), e.g. "Pixel did not answer at
192.168.1.20." (`plain_error`).

State (`phones` and the `state` event), per phone, besides the connection and look fields:
- `permissions`: what the phone has allowed Homa Lawn, as its build reports it in launcher.ping:
  {"home", "background", "notifications", "screen_control", "install_updates"}, each true/false
  (null when the phone did not say); null from a build that does not report them (then
  `phone.info`'s permissions {default_home, screen_control, wallpaper_backup} are all there is).
- `app_latest`: the Homa Lawn app on offer for this phone, {"version", "newer": bool, "source":
  "release" | "checkout"} (update.offer: this checkout's build for the app the phone runs, else
  for the release app the newest GitHub release); null when nothing is offered (a test build
  without a build here, the old app, no release yet, no network). Worked out in a thread when the
  phone's app or version changes, and at most hourly otherwise (GitHub is asked at most hourly).
And for the whole service, `serving`: null, or {"url", "urls", "expires_at", "version", "source",
"message"} while the app is offered to a new phone (`app.serve`): for a GitHub release (`source`
"release") `url` is the release file's HTTPS address on GitHub, `expires_at` null (no time limit)
and `message` always "The phone downloads Homa Lawn from GitHub."; for a build served from this
computer `expires_at` is unix time and `message` the server's latest line (null at first).
`app.serve_stop` clears it.

`snapshots` rows: {name, device, by, time, kind, title, contents}: `kind` is the window section
(home, search, style, wallpaper, manual, before_restore, other), `title` the plain line History
shows ("Home screen: 3 changes", "Wallpaper & style: Tokyo Night", "Saved by hand: <name>",
"Before a restore", ...), `contents` what the snapshot holds (look, layout, prefs, wallpaper,
manual, before restore, other; the `kind` of before 2026-11-12).
"""
from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import os
import re
import socket
import struct
import tempfile
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from . import env, gradients, icons, look as look_mod, lookconf, phonekeys, phonewall, snapshots as snapshots_mod
from .link import addresses
from .link.client import LinkDown

log = logging.getLogger("homalawn.daemon")


def _default_local_key() -> str:
    return ",".join(sorted(addresses.local_network_addresses()))

MAX_LINE = 8 * 1024 * 1024
DEFAULTS = {"check_interval": 10, "follow_interval": 2, "backoff_min": 2, "backoff_max": 60, "retry_interval": 60,
            # an absent phone: the first fast_dials failures back off 2 s doubling to 60 s, then one dial per slow_dial
            "fast_dials": 20, "slow_dial": 120,
            # agents installed later get Homa Lawn: Omarchy's default-agent file is looked at every
            # minute, and every supported agent once an hour (agents.sync)
            "agents_check_interval": 60, "agents_sync_interval": 3600}
RELOAD = "reload"     # Phone.waiting: Lawnchair did not reload in time; tried again after retry_interval
NOT_LOADED = "not_loaded"   # ops.NOT_LOADED
INVALID = "invalid"         # ops.INVALID: a home.apply the phone would not take (errors located)
WALLPAPER_UNSAVED = "wallpaper_unsaved"   # session.WALLPAPER_UNSAVED: a look needs consent to replace it
# RELOAD retries in a row before the service stops trying by itself (the next Omarchy change or look
# change tries again): each try saves a look- snapshot and only the newest
# snapshots.KEEP_LOOK are kept, so endless retries would push out the one taken before the change
RETRY_MAX = 5
# The phone's "Wallpaper from Omarchy" screen (phonewall): the ops it needs (a fake ops without them
# leaves the feature off), and how long the theme list is reused before `omarchy` is asked again
# (it starts one process per theme; a background added to a theme shows on the phone within this).
WALL_OPS = ("walls_push", "walls_poll", "walls_ack")
THEMES_TTL = 60
WALLS_REPROBE = 300   # a build without the wallpaper screen is asked again this often (it may be updated meanwhile)
WALLS_RELIST = 10     # s: a phone asking for the whole list again (needs_list) is answered at most this often
MSG_PICK_GONE = "That wallpaper is no longer in the theme on the computer."
MSG_GRADIENT_GONE = "That gradient is no longer offered: the theme's colours changed on the computer."
MSG_PICK_OFF = "Wallpaper sync is off for this phone."
# a phone choice whose wallpaper cannot be saved first: a build with the overwrite question shows
# its own dialog (ack code wallpaper_unsaved); an older one shows this line
MSG_PICK_UNSAVED = "The current wallpaper can't be saved first, so it was not replaced."
GRADIENT_MIN, GRADIENT_MAX = 16, 4096    # px: the sizes look.gradients draws


def gradient_dir() -> Path:
    """Where look.gradients keeps its pictures: beside the icon cache ($XDG_CACHE_HOME/homalawn)."""
    return icons.cache_root().parent / "gradients"


def gradient_files(colors: dict, w: int, h: int) -> list[dict]:
    """The six gradients of `colors` as PNG files of w x h, each drawn only when its file is
    missing (the name holds the colours' key and the size), written whole (temp file + rename)."""
    folder = gradient_dir()
    folder.mkdir(parents=True, exist_ok=True)
    out = []
    for style, label in gradients.STYLES:
        path = folder / f"{gradients.key(colors, style)}-{w}x{h}.png"
        if not path.is_file():
            fd, tmp = tempfile.mkstemp(dir=folder, prefix=f".{path.stem}-", suffix=".tmp")
            try:
                with os.fdopen(fd, "wb") as f:
                    f.write(gradients.render(colors, style, (w, h)))
                os.replace(tmp, path)
            except BaseException:
                with contextlib.suppress(OSError):
                    os.unlink(tmp)
                raise
        out.append({"style": style, "name": label, "path": str(path)})
    return out


class CannotStart(RuntimeError):
    """The service cannot open its socket; the message is the line `homalawn serve` prints."""


class AlreadyRunning(CannotStart):
    pass


class Fail(Exception):
    """A request error: the reply's `{"code", "message"}`, plus `data`'s fields (home.apply's
    `errors`)."""

    def __init__(self, code: str, message: str, data: dict | None = None):
        super().__init__(message)
        self.code = code
        self.data = data


def socket_path() -> Path:
    given = env.get("SOCKET")  # HOMALAWN_SOCKET, else HOMAR_SOCKET from before the rename
    if given:
        return Path(given)
    run = os.environ.get("XDG_RUNTIME_DIR") or f"/run/user/{os.getuid()}"
    return Path(run) / "homalawn" / "homalawnd.sock"


def peer_uid(sock: socket.socket) -> int:
    creds = sock.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, struct.calcsize("3i"))
    return struct.unpack("3i", creds)[1]


def applied_path() -> Path:
    """Where the service remembers what it last applied to each phone (for catching up)."""
    return env.state_dir() / "applied.json"


def _default_peers() -> dict[str, dict]:
    """Paired phones: fingerprint -> {name, device (as snapshots record it)}."""
    from .link import identity
    return {p.fp: {"name": p.name, "device": f"link:{p.fp}"} for p in identity.load_peers()}


def _default_peer_info(fp: str) -> dict:
    """What this computer knows of a paired phone without asking it: when it was paired and
    where it is dialled, in dial order ({"paired": unix time or None, "hosts": [HOST:PORT, ...]})."""
    from .link import identity
    from .link.client import LinkTransport
    peer = next((p for p in identity.load_peers() if p.fp == fp), None)
    if peer is None:
        return {}
    return {"paired": peer.paired or None, "hosts": LinkTransport(peer).candidates()}


def _default_version() -> str:
    from . import desktop
    return desktop.installed_version()


def _default_session(fp: str, name: str):
    from . import ops
    return ops.Session(via="link", phone=fp)


def _default_own_fp() -> str:
    from .link import identity
    return identity.ensure().fingerprint


def _default_screen_size(s) -> tuple[int, int]:
    return s.launcher().screen_size()


@dataclass
class Phone:
    fp: str
    name: str
    session: Any
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    connected: bool = False
    screen: tuple[int, int] | None = None
    applied: dict | None = None
    pending: bool = False
    error: str | None = None
    error_code: str | None = None  # WALLPAPER_UNSAVED: the window asks whether to overwrite it
    wake: asyncio.Event = field(default_factory=asyncio.Event)   # check the connection now
    device: str | None = None                                    # snapshot device key
    task: asyncio.Task | None = None                             # its connection watcher
    seen: dict | None = None     # Omarchy's {theme, background} when its look was last applied
    waiting: str | None = None   # why a pending look is held back: RELOAD, else None
    retry_at: float = 0.0        # RELOAD: not tried again before this clock time
    tries: int = 0               # RELOAD failures in a row
    walls_sent: tuple | None = None    # phonewall.View.fingerprint() of the last push to its screen
    walls_on: bool = False             # that push showed the wallpapers (sync on): the phone is asked for taps
    walls_unsupported: bool = False    # its Homa Lawn build has no such screen (until it reconnects,
                                       # is updated through the service, or WALLS_REPROBE passes)
    walls_unsupported_at: float = 0.0  # clock time it said so
    walls_polling: bool = False        # a choice read is on its way
    app_version: str | None = None     # Homa Lawn's version as the last connection check read it
    app_build: str | None = None       # which build (ops._build: install time or APK sha256), likewise:
                                       # our builds share one version, so only this tells them apart
    walls_relist_at: float | None = None   # clock time the phone's needs_list was last answered
    choice_seen: Any = None            # `at` of the last choice taken from the phone
    choice: dict | None = None         # a choice taken but not applied yet: {at, current_id, parts, before,
                                       # overwrite (the phone's Replace: no backup of the wallpaper)}
    pick_undo: dict | None = None      # the newest apply of a phone choice: {snapshot, before (look.json entry)}
    look_source: dict | None = None    # whose look the phone shows: {self, name, scope}; None = none / unread
    failures: int = 0                  # connection checks failed in a row (sets the back-off)
    absent: bool = False               # logged as not reachable (until it answers again)
    look_err: str | None = None        # why the look state was last not read (logged once per reason)
    kept: bool = False                 # the last apply was kept in the phone's look book, not shown
    book_key: Any = False              # this computer's key in the phone's look book as the check read
                                       # it (None = no entry); False = not read
    book_sent: bool = False            # this connection already re-sent a look the book lacked
    app_package: str | None = None     # the app the phone runs (launcher.ping's package)
    address: str | None = None         # where the last connection check reached it (HOST, or HOST:PORT)
    permissions: dict | None = None    # launcher.ping's permissions (ops.permissions_of); None: not reported
    app_latest: dict | None = None     # update.offer for its app and version; None: nothing offered
    latest_key: tuple | None = None    # the (package, version) app_latest was worked out for
    latest_at: float = 0.0             # clock time it was worked out
    latest_busy: bool = False          # being worked out now


@dataclass
class Reply:
    text: str
    notes: list = field(default_factory=list)
    data: Any = None


_socket_path = socket_path
_applied_path = applied_path


class Service:
    def __init__(self, socket_path: Path | None = None, peers: Callable[[], dict[str, dict]] = _default_peers,
                 session_factory: Callable[[str, str], Any] = _default_session, ops=None,
                 screen_size: Callable[[Any], tuple[int, int]] = _default_screen_size,
                 current: Callable[[], tuple] = look_mod.current,
                 list_themes: Callable[[], list] = look_mod.list_themes,
                 look_path: Path | None = None, applied_path: Path | None = None,
                 check_interval: float = DEFAULTS["check_interval"],
                 follow_interval: float = DEFAULTS["follow_interval"],
                 backoff_min: float = DEFAULTS["backoff_min"], backoff_max: float = DEFAULTS["backoff_max"],
                 retry_interval: float = DEFAULTS["retry_interval"],
                 fast_dials: int = DEFAULTS["fast_dials"], slow_dial: float = DEFAULTS["slow_dial"],
                 local_key: Callable[[], str] | None = None, net_interval: float | None = None,
                 clock: Callable[[], float] | None = None,
                 peer_info: Callable[[str], dict] = _default_peer_info, version: str | None = None,
                 agents=None, agents_check_interval: float = DEFAULTS["agents_check_interval"],
                 agents_sync_interval: float = DEFAULTS["agents_sync_interval"], chat=None,
                 own_fp: Callable[[], str] | None = None,
                 latest: Callable[[str | None, str | None], dict | None] | None = None,
                 serve_apk: Callable[[], tuple] | None = None, serve_hosts: Callable[[], list] | None = None,
                 serve_port: int | None = None, serve_seconds: float | None = None):
        if ops is None:
            from . import ops as ops_mod
            ops = ops_mod
        import time
        self.socket_path = Path(socket_path) if socket_path else _socket_path()
        self._peers = peers
        self._session_factory = session_factory
        self.ops = ops
        self._screen_size = screen_size
        self._current = current
        self._list_themes = list_themes
        self.look_path = look_path or lookconf.default_path()
        self.applied_path = applied_path or _applied_path()
        self.check_interval, self.follow_interval = check_interval, follow_interval
        self.backoff_min, self.backoff_max = backoff_min, backoff_max
        self.retry_interval = retry_interval
        self.fast_dials, self.slow_dial = fast_dials, slow_dial
        self._local_key = local_key or _default_local_key
        self.net_interval = check_interval if net_interval is None else net_interval
        self.clock = clock or time.time
        self._own_fp_of = own_fp or _default_own_fp
        self._own_fp: str | None = None            # this computer's fingerprint (read once, in a thread)
        self._peer_info = peer_info
        self._version = version
        if agents is None:
            from . import agents
        self.agents = agents
        self._agents_lock = asyncio.Lock()          # one registration at a time
        self.agents_check_interval, self.agents_sync_interval = agents_check_interval, agents_sync_interval
        if chat is None:
            from . import chat as chat_mod
            chat = chat_mod.Chat(default=lambda: self.agents.omarchy_default())
        self.chat = chat
        self.chat.on_change = self._chat_changed
        self.started = time.time()
        self.phones: dict[str, Phone] = {}
        self.looks: dict[str, lookconf.LookSettings] = {}
        self._looks_lock = asyncio.Lock()          # one read-modify-write of look.json at a time
        self.omarchy: dict = {"theme": None, "background": None}
        self._applied: dict = {}                   # phone fingerprint -> what was last applied
        self.busy: dict[str, str] = {}             # phone fingerprint -> what it is doing
        self._subscribers: set[asyncio.StreamWriter] = set()
        self._clients: set[asyncio.StreamWriter] = set()
        self._server: asyncio.base_events.Server | None = None
        self._tasks: set[asyncio.Task] = set()
        self._pairing: tuple[threading.Event, list, threading.Event] | None = None  # (stop flag, connections, thread ended)
        self._pair_task: asyncio.Task | None = None
        self._bound = False
        self._walls = all(hasattr(ops, n) for n in WALL_OPS)
        self._themes_cache: tuple[float, list] | None = None   # (monotonic time, themes)
        self._latest = latest or _default_latest
        self._serve_apk = serve_apk or _default_serve_apk
        self._serve_hosts = serve_hosts or _default_serve_hosts
        self._serve_port = _download_port() if serve_port is None else serve_port
        self._serve_seconds = _download_seconds() if serve_seconds is None else serve_seconds
        self._serving: dict | None = None          # app.serve: {server, thread, reply}
        self._serve_lock = asyncio.Lock()

    # -- lifecycle ---------------------------------------------------------------------------
    async def start(self) -> None:
        try:
            await asyncio.to_thread(addresses.migrate_config_hosts)
        except Exception as e:
            raise CannotStart(f"Cannot move the phone addresses out of config.toml: {plain_error(e, capital=False)}") from e
        try:
            pairs = [(fp, r["name"]) for fp, r in (await asyncio.to_thread(self._peers)).items()]
        except Exception as e:
            raise CannotStart(f"Cannot read the paired phones: {plain_error(e, capital=False)}") from e
        self.looks = lookconf.load(self.look_path, peers=pairs)
        self._applied = self._load_applied()
        new, moved = phonekeys.rekey(self._applied, pairs)
        if moved:
            phonekeys.keep_once(self.applied_path, ".names")
            self._applied = new
            self._save_applied(new)
        try:
            self.omarchy = self._read_current()
        except Exception:
            log.exception("cannot read the current Omarchy look")
        await self._listen()
        await self._sync_peers()
        self._spawn(self._peers_loop())
        self._spawn(self._net_loop())
        self._spawn(self._follow())
        self._spawn(self._agents_loop())

    async def _listen(self) -> None:
        p = self.socket_path
        try:
            self._private_folder(p.parent)
        except OSError as e:
            raise CannotStart(f"Cannot open the Homa Lawn socket {p}: {e.strerror or e}") from e
        if p.exists() or p.is_symlink():
            if await self._answers(p):
                raise AlreadyRunning(f"The Homa Lawn service is already running ({p}).")
            p.unlink()
        old = os.umask(0o177)
        try:
            self._server = await asyncio.start_unix_server(self._client, path=str(p))
        except OSError as e:
            raise CannotStart(f"Cannot open the Homa Lawn socket {p}: {e.strerror or e}") from e
        finally:
            os.umask(old)
        self._bound = True
        os.chmod(p, 0o600)

    @staticmethod
    def _private_folder(d: Path) -> None:
        """Make the socket's folder (0700), or check that an existing one is ours and 0700.
        A folder that is already there is never changed: HOMALAWN_SOCKET=~/x.sock must not
        chmod the home folder."""
        d.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        try:
            d.mkdir(mode=0o700)
            os.chmod(d, 0o700)   # made by us just now: the umask cannot widen it, only narrow it
            return
        except FileExistsError:
            pass
        st = d.stat()
        if st.st_uid != os.getuid():
            raise CannotStart(f"The folder for the Homa Lawn socket, {d}, belongs to another user. "
                              "Choose another place with HOMALAWN_SOCKET.")
        if (st.st_mode & 0o777) != 0o700:
            raise CannotStart(f"The folder for the Homa Lawn socket, {d}, can be read by others (mode "
                              f"{st.st_mode & 0o777:o}); only you may use it. Run `chmod 700 {d}` or "
                              "choose another place with HOMALAWN_SOCKET.")

    @staticmethod
    async def _answers(p: Path) -> bool:
        try:
            _, w = await asyncio.wait_for(asyncio.open_unix_connection(str(p)), 1)
        except (OSError, asyncio.TimeoutError):
            return False
        w.close()
        return True

    async def stop(self) -> None:
        if self._stop_pairing() and self._pair_task is not None:
            # the shutdown wakes the pairing thread; let it say how it really ended before the tasks go
            await asyncio.wait({self._pair_task}, timeout=3)
        with contextlib.suppress(Exception):
            await self.chat.close()
        with contextlib.suppress(Exception):
            await self._stop_serving()
        for t in list(self._tasks):
            t.cancel()
        for t in list(self._tasks):
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await t
        for w in list(self._clients):
            w.close()
        if self._server is not None:
            self._server.close()
            with contextlib.suppress(Exception):
                await asyncio.wait_for(self._server.wait_closed(), 1)
            self._server = None
        if self._bound:                       # never remove another service's socket
            with contextlib.suppress(OSError):
                self.socket_path.unlink()
            self._bound = False
        for ph in self.phones.values():
            with contextlib.suppress(Exception):
                ph.session.reset()

    async def serve_forever(self) -> None:
        """Run until SIGTERM or SIGINT (systemd stop, Ctrl+C); the socket file is removed on the way out."""
        import signal
        done = asyncio.Event()
        loop = asyncio.get_running_loop()
        for sig in (signal.SIGTERM, signal.SIGINT):
            loop.add_signal_handler(sig, done.set)
        await self.start()
        try:
            await done.wait()
        finally:
            await self.stop()

    def _spawn(self, coro) -> asyncio.Task:
        t = asyncio.get_running_loop().create_task(coro)
        self._tasks.add(t)
        t.add_done_callback(self._tasks.discard)
        return t

    # -- state -------------------------------------------------------------------------------
    def look_of(self, fp: str) -> lookconf.LookSettings:
        return self.looks.get(fp) or lookconf.LookSettings()

    def _phone_state(self, ph: Phone) -> dict:
        return {"id": ph.fp, "name": ph.name, "connected": ph.connected,
                "screen": {"w": ph.screen[0], "h": ph.screen[1]} if ph.screen else None,
                "look": self.look_of(ph.fp).to_dict(), "applied": ph.applied,
                "pending": ph.pending, "waiting": ph.waiting, "error": ph.error,
                "error_code": ph.error_code, "look_source": ph.look_source, "kept": ph.kept,
                "permissions": ph.permissions, "app_latest": ph.app_latest}

    def state(self) -> dict:
        return {"phones": [self._phone_state(p) for p in self.phones.values()],
                "omarchy": dict(self.omarchy), "serving": self._serving_state(),
                "busy": [{"phone": fp, "name": self.phones[fp].name, "what": self.busy[fp]}
                         for fp in self.phones if fp in self.busy]}

    # -- what was applied (survives restarts) -------------------------------------------------
    def _load_applied(self) -> dict:
        try:
            raw = json.loads(self.applied_path.read_text())
        except FileNotFoundError:
            return {}
        except (OSError, ValueError) as e:
            log.warning("%s unreadable (%s); starting without it", self.applied_path, e)
            return {}
        return {k: v for k, v in raw.items() if isinstance(v, dict)} if isinstance(raw, dict) else {}

    def _save_applied(self, data: dict) -> None:
        import tempfile
        p = self.applied_path
        p.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        fd, tmp = tempfile.mkstemp(dir=p.parent, prefix=".applied-", suffix=".tmp")
        try:
            with os.fdopen(fd, "w") as f:
                json.dump(data, f, indent=2)
            os.chmod(tmp, 0o600)
            os.replace(tmp, p)
        except BaseException:
            with contextlib.suppress(OSError):
                os.unlink(tmp)
            raise

    def _behind(self, ph: Phone) -> bool:
        """A follow phone whose last applied look is not Omarchy's current one."""
        settings = self.look_of(ph.fp)
        cur = self.omarchy
        if settings.source != "follow" or not cur.get("theme"):
            return False
        # compared with what Omarchy showed at the last apply, not with what was sent (with the
        # wallpaper switch off no picture is sent): every apply is a snapshot, so no repeats
        seen = ph.seen or ph.applied
        if not seen or look_mod.slug(str(seen.get("theme") or "")) != look_mod.slug(cur["theme"]):
            return True
        if not settings.follows_background(cur["theme"]):   # no screen shows Omarchy's background
            return False
        return seen.get("background") != cur.get("background")

    # -- paired phones (re-read every check cycle) ---------------------------------------------
    async def _sync_peers(self) -> None:
        try:
            peers = dict(await asyncio.to_thread(self._peers))
        except Exception:
            log.exception("cannot read the paired phones; keeping the known ones")
            return
        changed = False
        for fp in [f for f in self.phones if f not in peers]:
            ph = self.phones.pop(fp)
            if ph.task is not None:
                ph.task.cancel()
            with contextlib.suppress(Exception):
                await asyncio.to_thread(ph.session.reset)
            self.busy.pop(fp, None)
            changed = True
        for fp, peer in peers.items():
            if fp in self.phones:
                ph = self.phones[fp]
                if ph.name != peer["name"]:
                    ph.name, changed = peer["name"], True
                ph.device = peer["device"]
                continue
            try:
                session = self._session_factory(fp, peer["name"])
            except Exception:
                log.exception("%s: cannot set up its link; trying again next cycle", peer["name"])
                continue
            rec = dict(self._applied.get(fp) or {}) or None
            seen = rec.pop("seen", None) if rec else None
            ph = Phone(fp, peer["name"], session, device=peer["device"], applied=rec,
                       seen=seen if isinstance(seen, dict) else None)
            self.phones[fp] = ph
            ph.task = self._spawn(self._watch(ph))
            changed = True
        if changed:
            self._changed()

    async def _peers_loop(self) -> None:
        while True:
            await asyncio.sleep(self.check_interval)
            await self._sync_peers()

    async def _net_loop(self) -> None:
        """This computer's own addresses changed (it joined another network): dial every phone that is
        not connected at once, with its back-off starting over."""
        last, err = None, None
        while True:
            try:
                key = await asyncio.to_thread(self._local_key)
                err = None
            except Exception as e:
                if err != str(e):
                    err = str(e)
                    log.exception("cannot read this computer's addresses")
                key = last
            if last is None:
                last = key
            if key != last:
                last = key
                self._redial_absent()
            await asyncio.sleep(self.net_interval)

    def _redial_absent(self) -> None:
        for ph in self.phones.values():
            if not ph.connected:
                self._redial(ph)

    @staticmethod
    def _redial(ph: Phone) -> None:
        ph.failures = 0
        ph.wake.set()

    def _changed(self) -> None:
        line = _encode({"event": "state", "state": self.state()})
        for w in list(self._subscribers):
            if w.is_closing():
                self._subscribers.discard(w)
                continue
            w.write(line)

    def _chat_changed(self) -> None:
        line = _encode({"event": "chat", "chat": self.chat.snapshot()})
        for w in list(self._subscribers):
            if w.is_closing():
                self._subscribers.discard(w)
                continue
            w.write(line)

    def _read_current(self) -> dict:
        try:
            name, bg = self._current()
        except Exception as e:  # a broken Omarchy state dir must not stop the service
            log.warning("cannot read the current Omarchy theme: %s", e)
            name, bg = None, None
        return {"theme": name, "background": str(bg) if bg else None}

    def backoff(self, failures: int) -> float:
        """Wait before the next connection attempt after `failures` failed ones in a row (0-based)."""
        if failures >= self.fast_dials:
            return self.slow_dial
        return min(self.backoff_min * (2 ** failures), self.backoff_max)

    # -- phones ------------------------------------------------------------------------------
    async def _watch(self, ph: Phone) -> None:
        while True:
            try:
                ok = await self._check(ph) and ph.connected   # a pending apply may have dropped it again
            except Exception:
                log.exception("%s: connection check failed unexpectedly", ph.name)
                ok = False
            if ok:
                ph.failures = 0
                wait = self.check_interval
            else:
                wait = self.backoff(ph.failures)
                ph.failures += 1
            ph.wake.clear()
            with contextlib.suppress(asyncio.TimeoutError):
                await asyncio.wait_for(ph.wake.wait(), wait)

    async def _check(self, ph: Phone) -> bool:
        was = ph.connected
        version = build = None
        data = None
        async with ph.lock:
            try:
                st = await asyncio.to_thread(self.ops.status, ph.session)
                data = getattr(st, "data", None)
                version = data.get("version") if isinstance(data, dict) else None
                build = data.get("build") if isinstance(data, dict) else None
                if not was or ph.screen is None:
                    ph.screen = tuple(await asyncio.to_thread(self._screen_size, ph.session))
                ok = True
                if ph.error_code == "not_paired":
                    ph.error_code = None
                    self._changed()
            except Exception as e:
                if not ph.absent:
                    ph.absent = True
                    log.info("%s: not reachable (%s)", ph.name, e)
                if getattr(e, "code", None) == "not_paired" and not getattr(e, "forgotten", False):
                    if ph.error_code != "not_paired":
                        ph.error_code = "not_paired"
                        self._changed()
                await asyncio.to_thread(ph.session.reset)
                ok = False
        if ok != was:
            ph.connected = ok
            self._changed()
        if ok:
            if ph.absent:
                ph.absent = False
                log.info("%s: reachable again", ph.name)
            if not was:
                ph.book_sent = False
            await self._read_look_state(ph)
        if ok and self._due(ph, was):
            await self._apply_quietly(ph, retry=ph.waiting == RELOAD)
        elif ok and self._book_lacks(ph):
            ph.book_sent = True
            log.info("%s: its look book lacks this computer's look; sending it again", ph.name)
            await self._apply_quietly(ph)
        if self._walls:
            if not ok or not was:          # a new connection: its screen is told everything again
                ph.walls_sent, ph.walls_unsupported = None, False
            elif ph.walls_unsupported and self.clock() - ph.walls_unsupported_at >= WALLS_REPROBE:
                ph.walls_sent, ph.walls_unsupported = None, False   # updated outside the service? ask again
            elif self._another_build(ph, version, build):
                # another build (an update installed between two checks, the link never seen down):
                # it may hold a list the old build got in the old build's format; tell it everything
                log.info("%s: Homa Lawn build changed (%s -> %s): sending the wallpaper list again",
                         ph.name, ph.app_build or ph.app_version, build or version)
                ph.walls_sent, ph.walls_unsupported = None, False
            if ok and ph.connected:
                await self._push_walls(ph)
        if ok and version is not None:
            ph.app_version = version
        if ok and build is not None:
            ph.app_build = build
        if ok and isinstance(data, dict):
            self._took_status(ph, data)
        return ok

    def _took_status(self, ph: Phone, data: dict) -> None:
        """What a connection check's status says beyond the version: the app the phone runs, where
        it answered, and what it has allowed (a build that does not report permissions leaves them
        null); then app_latest is brought up to date when due."""
        if data.get("package"):
            ph.app_package = data["package"]
        if data.get("address"):
            ph.address = _plain_address(str(data["address"]))
        perms = data.get("permissions")
        perms = perms if isinstance(perms, dict) else None
        if perms != ph.permissions:
            ph.permissions = perms
            self._changed()
        self._latest_due(ph)

    def _latest_due(self, ph: Phone) -> None:
        key = (ph.app_package, ph.app_version)
        if ph.latest_busy or (key == ph.latest_key and self.clock() - ph.latest_at < LATEST_TTL):
            return
        ph.latest_busy = True
        self._spawn(self._refresh_latest(ph, key))

    async def _refresh_latest(self, ph: Phone, key: tuple) -> None:
        """app_latest for the app and version the phone runs, in a thread (update.offer may ask
        GitHub; at most hourly, and never on the event loop)."""
        try:
            new = await asyncio.to_thread(self._latest, *key)
        except Exception:
            log.exception("%s: cannot work out which Homa Lawn app to offer", ph.name)
            new = None
        finally:
            ph.latest_busy = False
        ph.latest_key, ph.latest_at = key, self.clock()
        if new != ph.app_latest:
            ph.app_latest = new
            self._changed()

    async def _read_look_state(self, ph: Phone) -> None:
        """Whose look the phone shows, as the connection check sees it."""
        new = None
        ph.book_key = False
        try:
            if self._own_fp is None:
                self._own_fp = await asyncio.to_thread(self._own_fp_of)
            async with ph.lock:
                ls = await asyncio.to_thread(self.ops.look_state, ph.session)
            src = ls.get("source") if isinstance(ls, dict) else None
            if src:
                new = {"self": src.get("fp") == self._own_fp, "name": src.get("name"),
                       "scope": ls.get("scope", "latest")}
            you = ls.get("you") if isinstance(ls, dict) else None
            if isinstance(you, dict):
                ph.book_key = you.get("key")
            ph.look_err = None
        except Exception as e:
            ph.kept = False
            if ph.look_err != str(e):
                ph.look_err = str(e)
                log.info("%s: look state not read (%s)", ph.name, e)
        if new != ph.look_source:
            ph.look_source = new
            self._changed()

    @staticmethod
    def _another_build(ph: Phone, version, build) -> bool:
        """The connection check read another Homa Lawn build than the one before (an unknown on
        either side is no change)."""
        return ((version is not None and ph.app_version is not None and version != ph.app_version)
                or (build is not None and ph.app_build is not None and build != ph.app_build))

    def _book_lacks(self, ph: Phone) -> bool:
        """Does the phone's look book lack the look this computer last sent? Its entry is missing
        (a build from before the book, a re-pair, the phone forgot this computer) or holds another
        key than applied.json. Sent again at most once per connection, and never for a phone whose
        look is off. A look the book holds but does not show (`you.shown` false: the person's undo,
        or another computer's look on screen) is left alone."""
        if ph.book_key is False or ph.book_sent or self.look_of(ph.fp).source == "off":
            return False
        return ph.book_key is None or ph.book_key != (ph.applied or {}).get("key")

    def _due(self, ph: Phone, was: bool) -> bool:
        """Should this connection check apply the look? On a reconnect when it is pending or
        behind Omarchy; one whose Lawnchair did not reload at most once per retry_interval."""
        if ph.waiting == RELOAD and self.clock() < ph.retry_at:
            return False
        if ph.waiting is not None:
            return True
        return not was and (ph.pending or self._behind(ph))

    async def _drop(self, ph: Phone) -> None:
        with contextlib.suppress(Exception):
            await asyncio.to_thread(ph.session.reset)
        ph.connected = False
        ph.wake.set()

    def _phone(self, params: dict) -> Phone:
        key = params.get("phone")
        if key is not None and not isinstance(key, str):
            raise Fail("unknown_phone", "phone must be a name or fingerprint.")
        try:
            fp = phonekeys.find(key, [(p.fp, p.name) for p in self.phones.values()])
        except phonekeys.Ambiguous as e:
            raise Fail("ambiguous", str(e))
        except phonekeys.NotFound as e:
            raise Fail("unknown_phone", str(e))
        return self.phones[fp]

    async def _write(self, ph: Phone, what: str, fn, *args, alone: bool = False):
        """Run a phone write in a thread under the phone's lock, showing it as busy. Failures
        become `op_failed`; a broken link is dropped (the watcher reconnects). Nothing waits behind
        an app update (the phone restarts its home screen in it); `alone` (the update itself) is
        refused while any other write runs."""
        if not ph.connected:
            self._redial(ph)       # a user action never waits out the back-off
            raise Fail("offline", f"{ph.name} is not connected.")
        doing = self.busy.get(ph.fp)
        if doing == "update" or (alone and doing):
            raise Fail("busy", f"{ph.name} is busy ({doing}); try again when it has finished.")
        async with ph.lock:
            doing = self.busy.get(ph.fp)
            if doing == "update" or (alone and doing):
                raise Fail("busy", f"{ph.name} is busy ({doing}); try again when it has finished.")
            self.busy[ph.fp] = what
            self._changed()
            try:
                return await asyncio.to_thread(fn, ph.session, *args)
            except Exception as e:
                if getattr(e, "transport", False):
                    await self._drop(ph)
                f = Fail("op_failed", plain_error(e, ph.name, ph.address))
                f.cause_code = getattr(e, "code", None)
                raise f from e
            finally:
                self.busy.pop(ph.fp, None)
                self._changed()

    def _stamp(self, ph: Phone):
        """`changed_at` for a look's key: the stored one while the key is the one last applied, else now."""
        rec = self._applied.get(ph.fp) or {}

        def stamp(key: str) -> int:
            if rec.get("key") == key and isinstance(rec.get("changed_at"), int):
                return rec["changed_at"]
            return int(self.clock() * 1000)
        return stamp

    async def _apply(self, ph: Phone, retry: bool = False, overwrite_wallpaper: bool = False) -> Any:
        """Apply this phone's look now. Success clears pending and error; a failure keeps the
        previous look, records the message, and (only when the link broke) leaves it pending for
        the reconnect. A locked phone gets everything at once (wallpapers and colours apply at once;
        a home-screen reload Lawnchair leaves for the unlock is reported in the notes, never as a
        failure: session.Launcher.wait_loaded). When Lawnchair did not reload in time it stays pending and is
        tried again after retry_interval (each try may save one more snapshot). A wallpaper that
        cannot be saved first fails with `wallpaper_unsaved` (also recorded as the phone's
        error_code) unless `overwrite_wallpaper`: the user's consent, never assumed here."""
        settings = self.look_of(ph.fp)
        omarchy = self._read_current() if settings.source == "follow" else None
        # a choice from the phone changes only the screens it names
        parts = ph.choice.get("parts") if ph.choice is not None else None
        overwrite_wallpaper = overwrite_wallpaper or (ph.choice is not None and ph.choice.get("overwrite") is True)
        kw = ({"overwrite_wallpaper": True} if overwrite_wallpaper else {}) | (
            {"parts": parts} if parts is not None else {}) | {"stamp": self._stamp(ph)}

        def apply(session, st):
            return self.ops.look_apply(session, st, **kw)
        try:
            r = await self._write(ph, "look", apply, settings)
        except Fail as e:
            ph.kept = False
            ph.waiting = None
            ph.error_code = None
            if e.code in ("offline", "busy"):
                ph.pending = True
            elif getattr(e, "cause_code", None) == NOT_LOADED:
                ph.error = str(e)
                ph.pending, ph.waiting = True, RELOAD
                ph.tries = ph.tries + 1 if retry else 1
                ph.retry_at = self.clock() + self.retry_interval if ph.tries < RETRY_MAX else float("inf")
            else:
                ph.error = str(e)
                ph.pending = not ph.connected
                if getattr(e, "cause_code", None) == WALLPAPER_UNSAVED:
                    ph.error_code = WALLPAPER_UNSAVED
                    self._changed()
                    raise Fail(WALLPAPER_UNSAVED, str(e)) from e
            self._changed()
            raise
        used = r.data if isinstance(r.data, dict) and "theme" in r.data else (
            omarchy if settings.source == "follow"
            else {"theme": settings.theme, "background": settings.pick_for(settings.theme)})
        ph.applied = {"theme": used.get("theme"), "background": used.get("background"), "at": self.clock()}
        if isinstance(r.data, dict) and r.data.get("key") is not None:
            ph.applied |= {"key": r.data["key"], "changed_at": r.data.get("changed_at")}
        ph.kept = isinstance(r.data, dict) and r.data.get("shown") is False and r.data.get("mine") is not True
        ph.seen = dict(omarchy) if omarchy else None
        self._applied[ph.fp] = {**ph.applied, "seen": ph.seen}
        try:
            await asyncio.to_thread(self._save_applied, dict(self._applied))
        except OSError as e:
            log.warning("cannot record the applied look in %s: %s", self.applied_path, e)
        ph.pending = False
        ph.waiting, ph.tries = None, 0
        ph.error, ph.error_code = None, None
        self._changed()
        if self._walls:
            if ph.choice is not None:
                self._spawn(self._choice_applied(ph, ph.choice, r))
                ph.choice = None
            else:
                self._spawn(self._push_walls(ph))
        return r

    async def _apply_quietly(self, ph: Phone, retry: bool = False) -> None:
        with contextlib.suppress(Fail):
            await self._apply(ph, retry)

    # -- wallpapers picked on the phone (phonewall) -----------------------------------------------
    async def _themes(self) -> list:
        import time
        c = self._themes_cache
        if c is None or time.monotonic() - c[0] > THEMES_TTL:
            try:
                themes = list(await asyncio.to_thread(self._list_themes))
            except look_mod.ThemeDirMissing as e:
                log.error("cannot list the Omarchy themes: %s", e)
                themes = c[1] if c else []
            except Exception:
                log.exception("cannot list the Omarchy themes")
                themes = c[1] if c else []
            self._themes_cache = (time.monotonic(), themes)
            return themes
        return c[1]

    async def _walls_view(self, ph: Phone) -> phonewall.View:
        themes = await self._themes()
        return await asyncio.to_thread(phonewall.view, self.look_of(ph.fp), dict(self.omarchy), themes, ph.screen)

    async def _push_walls(self, ph: Phone) -> None:
        """Tell the phone's screen what to show, when that changed since the last push (each new
        connection gets everything again). A build without the screen is left alone until it
        reconnects, is updated through the service, or WALLS_REPROBE passes (`homalawn update` from
        the command line swaps the build without the link ever dropping)."""
        if not ph.connected or ph.walls_unsupported or self.busy.get(ph.fp):
            return
        try:
            v = await self._walls_view(ph)
            if v.fingerprint() == ph.walls_sent:
                return
            async with ph.lock:
                if not ph.connected:
                    return
                sent = await asyncio.to_thread(self.ops.walls_push, ph.session, v)
            ph.walls_sent, ph.walls_on = v.fingerprint(), v.enabled
            log.info("%s: wallpaper list sent (%s, %d pictures, %d new)", ph.name, v.theme, len(v.items), sent)
        except Exception as e:
            self._walls_failed(ph, e, "sending the wallpaper list")

    def _walls_failed(self, ph: Phone, e: Exception, doing: str) -> None:
        if getattr(e, "code", None) == "unknown_method":
            if not ph.walls_unsupported:
                ph.walls_unsupported_at = self.clock()
            ph.walls_unsupported = True
            log.info("%s: its Homa Lawn build has no wallpaper screen yet", ph.name)
        elif getattr(e, "transport", False) or isinstance(e, (LinkDown, OSError)):
            # The link broke under this call, often because the phone's home screen restarted (an
            # app update, a crash) between two connection checks that both found it up. The new
            # process knows nothing of what was sent: the next check tells it everything again.
            ph.walls_sent, ph.walls_unsupported = None, False
            ph.wake.set()
            log.info("%s: %s failed (%s)", ph.name, doing, e)
        elif e.__class__.__name__ == "OpError":
            log.info("%s: %s failed (%s)", ph.name, doing, e)
        else:
            log.warning("%s: %s failed: %s", ph.name, doing, e)

    def _poll_choices(self) -> None:
        """Every follower tick: ask each idle phone whose screen shows wallpapers whether one was tapped."""
        for ph in self.phones.values():
            if (ph.connected and ph.walls_on and not ph.walls_unsupported and not ph.walls_polling
                    and not ph.lock.locked() and not self.busy.get(ph.fp)):
                ph.walls_polling = True
                self._spawn(self._poll_choice(ph))

    async def _poll_choice(self, ph: Phone) -> None:
        try:
            async with ph.lock:
                c, needs_list = await asyncio.to_thread(self.ops.walls_poll, ph.session)
            if needs_list and (ph.walls_relist_at is None or self.clock() - ph.walls_relist_at >= WALLS_RELIST):
                # its home screen restarted (often an app update) or it holds an older build's list
                ph.walls_relist_at = self.clock()
                ph.walls_sent = None
                log.info("%s: the phone asks for the wallpaper list again", ph.name)
                await self._push_walls(ph)
            if c is not None and c["at"] != ph.choice_seen:
                await self._take_choice(ph, c)
        except Exception as e:
            self._walls_failed(ph, e, "reading the wallpaper tapped on the phone")
        finally:
            ph.walls_polling = False

    async def _walls_ack(self, ph: Phone, at, state: str, current_id: str | None = None,
                         message: str | None = None, code: str | None = None) -> None:
        args = (at, state, current_id, message) + ((code,) if code is not None else ())
        try:
            async with ph.lock:
                await asyncio.to_thread(self.ops.walls_ack, ph.session, *args)
        except Exception as e:
            self._walls_failed(ph, e, f"telling the phone its wallpaper was {state}")

    @staticmethod
    def _legacy_screens(c: dict, v: phonewall.View, target: str) -> dict:
        """An older build's choice ({id, target}) as one choice per screen: the screens of the
        target show the wallpaper (the tapped one, else the one they show now), the other keeps."""
        t = c.get("target") or target
        pid = c.get("id") or v.current
        return {p: {"kind": "wallpaper", "id": pid} if p in lookconf.PARTS[t] else {"kind": "keep"}
                for p in lookconf.SCREENS}

    @staticmethod
    def _choice_into(st: lookconf.LookSettings, v: phonewall.View, want: dict, picked: dict,
                     changed: list) -> dict:
        """The look with the phone's choice for the `changed` screens written into it (look.json's
        shape); the other screen goes on showing what it showed."""
        d = st.to_dict()
        wp, picks = d["wallpaper"], d["picks"]
        old = picks.get(v.slug)
        entry = dict(old) if isinstance(old, dict) else {"home": None, "lock": None, "by": "phone"}
        chose = False
        for p in changed:
            w, item, lay = want[p], picked.get(p), wp[p]["layer"]
            if w["kind"] == "keep":
                wp[p] = {"kind": "keep", "style": None, "layer": lay}
            elif w["kind"] == "gradient":
                wp[p] = {"kind": "gradient", "style": item.style, "layer": lay}
            else:
                wp[p] = {"kind": "wallpaper", "style": None, "layer": lay}
                entry[p], chose = item.path, True
        if chose:
            entry["by"] = "phone"
        if entry.get("home") or entry.get("lock"):
            picks[v.slug] = entry
        else:
            picks.pop(v.slug, None)
        return d

    @staticmethod
    def _model_into(st: lookconf.LookSettings, v: phonewall.View, c: dict, picked: dict) -> dict:
        """The look with a whole-model choice (format 3) written into it, as the window writes it:
        parallax, and per screen its source, picture or gradient and colour layer. A screen whose
        source and picture did not change keeps its pick (one showing the theme's current
        background goes on following it). The phone's builds from before 2026-10-17 still offer
        "Same on both" (`wallpaper.same` true): that is stored as two equal screens, the lock
        screen getting the home screen's choice and layer, and every theme's pick and every crop
        the home one (as an older look.json with `same` is read)."""
        d = st.to_dict()
        wp, picks = d["wallpaper"], d["picks"]
        same, want, layer = c["wallpaper"].get("same") is True, c["screens"], c["layer"]
        old = picks.get(v.slug)
        entry = dict(old) if isinstance(old, dict) else {"home": None, "lock": None, "by": "phone"}
        for p in (("home",) if same else lookconf.SCREENS):     # with `same` the lock follows below
            w, item = want[p], picked.get(p)
            if w != v.screens[p]:
                if w["kind"] == "keep":
                    wp[p] = {"kind": "keep", "style": None, "layer": 0}
                elif w["kind"] == "gradient":
                    wp[p] = {"kind": "gradient", "style": item.style, "layer": 0}
                else:
                    wp[p] = {"kind": "wallpaper", "style": None, "layer": 0}
                    entry[p], entry["by"] = item.path, "phone"
            wp[p]["layer"] = layer[p]
        wp["parallax"] = c["wallpaper"]["parallax"]
        if entry.get("home") or entry.get("lock"):
            picks[v.slug] = entry
        else:
            picks.pop(v.slug, None)
        if same:                    # what Same on both showed: the lock screen as the home one, for
            wp["same"] = True       # every theme's pick and every crop (lookconf's own migration)
            lookconf.as_two_screens(d)
        return d

    @staticmethod
    def _shows(st: lookconf.LookSettings, v: phonewall.View) -> dict:
        """What each screen gets from a look, as far as the phone's pictures go: kind, gradient,
        picture, crop and layer, and for the home screen whether it scrolls."""
        out = {}
        for p in lookconf.SCREENS:
            c = st.screen(p)
            pic = (st.pick_for(v.theme, p) or v.default) if c["kind"] == "wallpaper" else None
            out[p] = (c["kind"], c["style"], pic, st.crop_for(pic, p) if pic else None, c["layer"],
                      st.parallax_on() if p == "home" and c["kind"] != "keep" else None)
        return out

    async def _take_choice(self, ph: Phone, c: dict) -> None:
        """Apply on the phone's wallpaper screen: one choice per screen (keep what it has, a
        wallpaper of the theme, or a gradient). The screens that differ from what they show now
        are kept in look.json (`wallpaper`; a wallpaper as the theme's pick for that screen), then
        the look is applied to those screens only (one snapshot); the phone hears each step. A screen set to keep is left as it is."""
        ph.choice_seen = c["at"]
        await self._walls_ack(ph, c["at"], "received")
        v = await self._walls_view(ph)

        async def fail(message):
            await self._walls_ack(ph, c["at"], "failed", message=message)
            ph.walls_sent = None
            await self._push_walls(ph)
        if not v.enabled:
            return await fail(MSG_PICK_OFF)
        want = c.get("screens") or self._legacy_screens(c, v, self.look_of(ph.fp).target() or "both")
        picked = {}
        for p in lookconf.SCREENS:
            w = want[p]
            if w["kind"] == "wallpaper":
                picked[p] = v.find(w["id"])
                if picked[p] is None:
                    return await fail(MSG_PICK_GONE)
            elif w["kind"] == "gradient":
                picked[p] = v.find_gradient(w["id"])
                if picked[p] is None:
                    return await fail(MSG_GRADIENT_GONE)
        model = c.get("format") is not None and "wallpaper" in c
        if model:
            st = self.look_of(ph.fp)
            try:
                new = lookconf.validate(self._model_into(st, v, c, picked))
            except ValueError as e:
                return await fail(str(e))
            if new.to_dict() == st.to_dict():
                await self._walls_ack(ph, c["at"], "applied", current_id=v.current)
                return
            was, now = self._shows(st, v), self._shows(new, v)
            changed = [p for p in lookconf.SCREENS if was[p] != now[p]]
        else:
            changed = [p for p in lookconf.SCREENS if want[p] != v.screens[p]]
            if not changed:
                await self._walls_ack(ph, c["at"], "applied", current_id=v.current)
                return
        async with self._looks_lock:
            st = self.look_of(ph.fp)
            before = st.to_dict()
            d = self._model_into(st, v, c, picked) if model else self._choice_into(st, v, want, picked, changed)
            self.looks[ph.fp] = lookconf.validate(d)
            await asyncio.to_thread(lookconf.save, dict(self.looks), self.look_path)
            screens = self.looks[ph.fp].screens_of()
        log.info("%s: wallpaper set on the phone (%s)", ph.name,
                 ", ".join(f"{p}: {screens[p]['kind']}" + (f" {picked[p].name}" if picked.get(p) else "")
                           for p in changed) or "nothing the phone shows changed")
        parts = tuple(p for p in changed if screens[p]["kind"] != "keep")
        home = want["home"]
        current_id = home["id"] if home["kind"] == "wallpaper" else None
        if not parts:                         # only screens set to keep: nothing to put on the phone
            self._changed()                   # the window shows the new setting
            ph.pick_undo = None
            await self._walls_ack(ph, c["at"], "applied", current_id=current_id)
            await self._push_walls(ph)
            return
        ph.choice = {"at": c["at"], "current_id": current_id, "parts": parts, "before": before,
                     "overwrite": c.get("overwrite") is True}
        ph.error, ph.error_code = None, None
        self._changed()
        try:
            await self._apply(ph)
        except Fail as e:
            if e.code in ("offline", "busy"):
                return                        # still pending: applied (and acknowledged) at the reconnect
            ph.choice = None
            if e.code == WALLPAPER_UNSAVED:
                # the phone asks whether to replace it anyway (its Replace sends the same choice with
                # overwrite); until then nothing changed: the settings go back, the window asks nothing
                async with self._looks_lock:
                    self.looks[ph.fp] = lookconf.validate(before)
                    await asyncio.to_thread(lookconf.save, dict(self.looks), self.look_path)
                ph.error, ph.error_code, ph.pending = None, None, False
                self._changed()
                log.info("%s: the wallpaper cannot be saved first; the phone asks whether to replace it", ph.name)
                await self._walls_ack(ph, c["at"], "failed", message=MSG_PICK_UNSAVED, code=WALLPAPER_UNSAVED)
                await self._push_walls(ph)
                return
            await self._walls_ack(ph, c["at"], "failed", message=str(e))
            await self._push_walls(ph)        # the saved setting stands; the screen shows it
            return

    async def _choice_applied(self, ph: Phone, choice: dict, r) -> None:
        snap = r.data.get("snapshot") if isinstance(r.data, dict) else None
        ph.pick_undo = {"snapshot": snap, "before": choice["before"]}
        await self._walls_ack(ph, choice["at"], "applied", current_id=choice["current_id"])
        await self._push_walls(ph)

    # -- the home-screen editor ----------------------------------------------------------------
    async def _read(self, ph: Phone, fn, *args):
        """A phone read for the window, in a thread under the phone's lock (one link call at a
        time). Failures become `op_failed`; a broken link is dropped (the watcher reconnects)."""
        if not ph.connected:
            self._redial(ph)       # a user action never waits out the back-off
            raise Fail("offline", f"{ph.name} is not connected.")
        if self.busy.get(ph.fp) == "update":
            raise Fail("busy", f"{ph.name} is busy (update); try again when it has finished.")
        async with ph.lock:
            try:
                return await asyncio.to_thread(fn, ph.session, *args)
            except Exception as e:
                if getattr(e, "transport", False):
                    await self._drop(ph)
                raise Fail("op_failed", plain_error(e, ph.name, ph.address)) from e

    async def _apply_draft(self, ph: Phone, kind: str, draft: dict) -> dict:
        """Write a window draft (`kind`: home or search, see DRAFTS) now, locked phone or not: the
        phone takes it at once and Lawnchair shows it (at the latest when it is unlocked). A draft
        the phone would not take fails with `invalid` and `errors` (each located); nothing changed
        then."""
        fn, args = DRAFTS[kind](self.ops, draft)
        try:
            r = await self._write(ph, kind, fn, *args)
        except Fail as e:
            if getattr(e, "cause_code", None) == INVALID:
                cause = e.__cause__
                raise Fail(INVALID, str(e), {"errors": list(getattr(cause, "problems", []) or [])}) from e
            raise
        return {"text": r.text, "notes": list(r.notes), "data": r.data}

    @staticmethod
    def _size(params: dict) -> int | None:
        size = params.get("size")
        if size is None:
            return None
        if isinstance(size, bool) or not isinstance(size, int) or not 16 <= size <= 512:
            raise Fail("invalid", "size must be a whole number of pixels from 16 to 512.")
        return size

    # -- follower ----------------------------------------------------------------------------
    async def _follow(self) -> None:
        while True:
            await asyncio.sleep(self.follow_interval)
            try:
                await self._follow_once()
            except Exception:
                log.exception("following the Omarchy look failed; trying again")
            if self._walls:
                self._poll_choices()

    async def _follow_once(self) -> None:
        now = await asyncio.to_thread(self._read_current)
        if now == self.omarchy:
            return
        if look_mod.slug(str(now.get("theme") or "")) != look_mod.slug(str(self.omarchy.get("theme") or "")):
            self._themes_cache = None          # another theme: its backgrounds are read afresh
        self.omarchy = now
        for ph in self.phones.values():
            settings = self.look_of(ph.fp)
            if settings.source != "follow":
                continue
            if self._walls and ph.connected:
                self._spawn(self._push_walls(ph))
            seen = ph.seen or ph.applied or {}
            if not settings.follows_background(now.get("theme")) and \
                    look_mod.slug(str(seen.get("theme") or "")) == look_mod.slug(str(now.get("theme") or "")):
                continue                       # only Omarchy's background moved; no screen shows it
            if ph.connected:
                self._spawn(self._apply_quietly(ph))
            else:
                ph.pending = True
        self._changed()

    # -- requests ----------------------------------------------------------------------------
    async def _client(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        try:
            sock = writer.get_extra_info("socket")
            if sock is None or peer_uid(sock) != os.getuid():
                log.warning("refused a connection from another user")
                writer.close()
                return
            self._clients.add(writer)
            buf = b""
            dropping = False
            while True:
                chunk = await reader.read(65536)
                if not chunk:
                    break
                buf += chunk
                while True:
                    i = buf.find(b"\n")
                    if i < 0:
                        if len(buf) > MAX_LINE:
                            buf, dropping = b"", True
                        break
                    line, buf = buf[:i], buf[i + 1:]
                    if dropping:
                        dropping = False
                        continue
                    if len(line) > MAX_LINE or not line.strip():
                        continue
                    self._spawn(self._handle(line, writer))
        except (ConnectionError, OSError):
            pass
        finally:
            self._subscribers.discard(writer)
            self._clients.discard(writer)
            writer.close()

    async def _handle(self, line: bytes, writer: asyncio.StreamWriter) -> None:
        rid = None
        try:
            try:
                req = json.loads(line)
                if not isinstance(req, dict) or not isinstance(req.get("method"), str):
                    raise ValueError
            except ValueError:
                raise Fail("bad_request", "Each line must be a JSON object with a method.")
            rid = req.get("id")
            params = req.get("params") or {}
            if not isinstance(params, dict):
                raise Fail("bad_request", "params must be an object.")
            fn = self.METHODS.get(req["method"])
            if fn is None:
                raise Fail("unknown_method", f"Unknown method {req['method']!r}.")
            result = await fn(self, params, writer)
            msg = {"id": rid, "result": result}
        except Fail as e:
            msg = {"id": rid, "error": {**(e.data or {}), "code": e.code, "message": str(e)}}
        except Exception:
            log.exception("request failed")
            msg = {"id": rid, "error": {"code": "internal", "message": INTERNAL}}
        if not writer.is_closing():
            writer.write(_encode(msg))
            with contextlib.suppress(ConnectionError):
                await writer.drain()

    async def m_subscribe(self, params, writer):
        self._subscribers.add(writer)
        # the reply goes out after this returns; the first state follows it
        asyncio.get_running_loop().call_soon(
            lambda: writer.is_closing() or writer.write(_encode({"event": "state", "state": self.state()})))
        return {"subscribed": True}

    async def m_phones(self, params, writer):
        return self.state()["phones"]

    async def m_themes_list(self, params, writer):
        themes = await asyncio.to_thread(self._list_themes)
        return [{"name": t.name, "dir": str(t.dir), "colors": t.colors,
                 "backgrounds": [str(b) for b in t.backgrounds],
                 "preview": str(t.preview) if t.preview else None,
                 # monochrome icons: the colours offered for the symbol (accent first) and its plate
                 "icon_palette": look_mod.icon_palette(t.colors), "icon_plate": look_mod.icon_plate(t.colors)}
                for t in themes]

    async def m_look_get(self, params, writer):
        return self.look_of(self._phone(params).fp).to_dict()

    async def m_look_gradients(self, params, writer):
        """{phone?, theme?: name, w, h} -> [{style, name, path}]: the six gradients of a theme
        (the given one, else Omarchy's current one) as PNG files of w x h, made once into the
        cache (a file's name changes with the colours). [] when the theme is unknown or has no
        colours to make them from. `phone` is accepted and not needed."""
        w, h = params.get("w"), params.get("h")
        for k, n in (("w", w), ("h", h)):
            if isinstance(n, bool) or not isinstance(n, int) or not GRADIENT_MIN <= n <= GRADIENT_MAX:
                raise Fail("invalid", f"{k} must be a whole number from {GRADIENT_MIN} to {GRADIENT_MAX}.")
        name = params.get("theme")
        if name is not None and not isinstance(name, str):
            raise Fail("invalid", "theme must be a theme name.")
        theme = phonewall._theme_named(await self._themes(), name or self.omarchy.get("theme"))
        if theme is None or not gradients.available(theme.colors):
            return []
        return await asyncio.to_thread(gradient_files, dict(theme.colors), w, h)

    async def m_look_set(self, params, writer):
        ph = self._phone(params)
        raw = params.get("settings")
        if not isinstance(raw, dict):
            raise Fail("invalid", "settings must be an object.")
        try:
            settings = lookconf.validate(raw)      # an older-shape draft is read into the current one
        except (ValueError, TypeError) as e:
            raise Fail("invalid", str(e))
        async with self._looks_lock:         # saves queue: an older copy never lands last
            self.looks[ph.fp] = settings
            await asyncio.to_thread(lookconf.save, dict(self.looks), self.look_path)
        ph.error, ph.error_code = None, None
        ph.pick_undo = None                  # undo after this no longer means "take the phone's pick back"
        if self._walls and ph.connected:
            self._spawn(self._push_walls(ph))
        out = {"look": settings.to_dict(), "text": None, "notes": []}
        if settings.source == "off":
            ph.pending, ph.waiting = False, None
            self._changed()
            return out
        if not ph.connected:
            ph.pending = True
            self._changed()
            return out
        self._changed()
        r = await self._apply(ph, overwrite_wallpaper=params.get("overwrite_wallpaper") is True)
        return {**out, "text": r.text, "notes": list(r.notes)}

    async def m_look_apply(self, params, writer):
        r = await self._apply(self._phone(params), overwrite_wallpaper=params.get("overwrite_wallpaper") is True)
        return {"text": r.text, "notes": list(r.notes), "data": r.data}

    async def m_undo(self, params, writer):
        ph = self._phone(params)
        r = await self._write(ph, "undo", self.ops.undo)
        u = ph.pick_undo
        if u is not None and u.get("snapshot") and r.data == u["snapshot"]:
            # the look undone was a choice made on the phone: its settings go back with it
            ph.pick_undo = None
            async with self._looks_lock:
                self.looks[ph.fp] = lookconf.validate(u["before"])
                await asyncio.to_thread(lookconf.save, dict(self.looks), self.look_path)
            self._changed()
            if self._walls:
                self._spawn(self._push_walls(ph))
        return {"text": r.text, "notes": list(r.notes), "data": r.data}

    async def m_snapshots(self, params, writer):
        ph = self._phone(params)
        try:
            r = await asyncio.to_thread(self.ops.snapshot_list, ph.session)
        except Exception as e:
            raise Fail("op_failed", plain_error(e))
        rows = [_history_row(row) for row in r.data or [] if ph.device and row.get("device") == ph.device]
        return {"text": "\n".join(row["name"] for row in rows), "data": rows}

    async def m_snapshot_restore(self, params, writer):
        """Put one of this phone's own snapshots back (a pre-restore snapshot is taken first)."""
        ph = self._phone(params)
        name = params.get("name")
        if not isinstance(name, str) or not name:
            raise Fail("invalid", "name must be a snapshot name.")
        mine = (await self.m_snapshots({"phone": ph.fp}, writer))["data"]
        if name not in {row["name"] for row in mine}:
            raise Fail("not_this_phone", f"{name} is not a snapshot of {ph.name}.")
        r = await self._write(ph, "restore", self.ops.snapshot_restore, name)
        return {"text": r.text, "notes": list(r.notes), "data": r.data}

    async def m_app_update(self, params, writer):
        """Send the Homa Lawn app on offer (update.pick_apk: this checkout's build, else the newest
        release) to the phone over the link (homalawn update)."""
        ph = self._phone(params)
        try:
            r = await self._write(ph, "update", self.ops.app_update, alone=True)
        finally:
            ph.walls_sent, ph.walls_unsupported = None, False   # a new build may have the wallpaper screen
            ph.latest_key = None   # app_latest is worked out again at the next check
            ph.wake.set()          # its home screen may have restarted: check the link now
        return {"text": r.text, "notes": list(r.notes), "data": r.data}

    async def m_phone_info(self, params, writer):
        """What is known of one phone: from this computer (paired date, address) and, while it is
        connected and idle, from the phone itself (app version, what it has allowed)."""
        ph = self._phone(params)
        try:
            known = dict(await asyncio.to_thread(self._peer_info, ph.fp) or {})
        except Exception:
            log.exception("%s: cannot read its pairing record", ph.name)
            known = {}
        hosts = known.get("hosts") or []
        out = {"id": ph.fp, "name": ph.name, "connected": ph.connected, "device": ph.device,
               "paired": known.get("paired"), "address": hosts[0] if hosts else None,
               "applied": ph.applied, "busy": self.busy.get(ph.fp), "app": None, "permissions": None,
               "error": None}
        if not ph.connected or out["busy"]:
            out["address"] = _plain_address(out["address"])
            return out
        try:   # a connection check holds the lock briefly; a write that starts meanwhile, longer
            await asyncio.wait_for(ph.lock.acquire(), INFO_WAIT)
        except asyncio.TimeoutError:
            out["busy"] = self.busy.get(ph.fp) or "busy"
            out["address"] = _plain_address(out["address"])
            return out
        try:
            r = await asyncio.to_thread(self.ops.phone_info, ph.session)
        except Exception as e:
            if getattr(e, "transport", False):
                await self._drop(ph)
            out["error"] = plain_error(e, ph.name, ph.address or _plain_address(out["address"]))
            out["address"] = _plain_address(out["address"])
            return out
        finally:
            ph.lock.release()
        data = r.data or {}
        out["address"] = _plain_address(data.get("address") or out["address"])
        out["app"], out["permissions"] = data.get("app"), data.get("permissions")
        return out

    async def m_home_get(self, params, writer):
        """The home screen for the editor (ops.home_get's data)."""
        ph = self._phone(params)
        r = await self._read(ph, self.ops.home_get, self._size(params))
        return r.data

    async def m_home_apps(self, params, writer):
        ph = self._phone(params)
        r = await self._read(ph, self.ops.home_apps, self._size(params))
        return r.data

    async def m_home_widgets(self, params, writer):
        ph = self._phone(params)
        r = await self._read(ph, self.ops.home_widgets, self._size(params))
        return r.data

    async def m_home_apply(self, params, writer):
        """{layout, grid?: [cols, rows], hotseat_count?, look_prefs?: {icon_scale, label_scale,
        label_gap_dp, show_labels}}: the editor's draft, applied in one undo step."""
        ph = self._phone(params)
        layout = params.get("layout")
        if not isinstance(layout, dict) or not isinstance(layout.get("grid"), dict) \
                or not isinstance(layout.get("screens"), list) or not isinstance(layout.get("hotseat", []), list):
            raise Fail(INVALID, "layout must be a home.get layout: {grid: {cols, rows}, screens: [...], hotseat: [...]}.")
        grid = params.get("grid")
        if grid is not None and not (isinstance(grid, list) and len(grid) == 2
                                     and all(isinstance(v, int) and not isinstance(v, bool) for v in grid)):
            raise Fail(INVALID, "grid must be [columns, rows].")
        hot = params.get("hotseat_count")
        if hot is not None and (isinstance(hot, bool) or not isinstance(hot, int)):
            raise Fail(INVALID, "hotseat_count must be a whole number.")
        look = params.get("look_prefs")
        if look is not None and not isinstance(look, dict):
            raise Fail(INVALID, "look_prefs must be an object.")
        return await self._apply_draft(ph, "home", {"layout": layout, "grid": grid, "hotseat_count": hot, "look_prefs": look})

    async def m_search_get(self, params, writer):
        """Search & gestures for the window (ops.search_get's data)."""
        ph = self._phone(params)
        r = await self._read(ph, self.ops.search_get)
        return r.data

    async def m_search_apply(self, params, writer):
        """{gestures?: {name: handler type}, search?: {name: value}}: the Search & gestures page's
        draft, in one undo step."""
        ph = self._phone(params)
        gestures, search = params.get("gestures"), params.get("search")
        for name, v in (("gestures", gestures), ("search", search)):
            if v is not None and not isinstance(v, dict):
                raise Fail(INVALID, f"{name} must be an object.")
        if not gestures and not search:
            raise Fail(INVALID, "Nothing to apply: give gestures or search.")
        return await self._apply_draft(ph, "search", {"gestures": gestures or {}, "search": search or {}})

    # -- agents installed later -----------------------------------------------------------------
    def _default_agent_stamp(self):
        try:
            st = self.agents.omarchy_default_file().stat()
            return (st.st_mtime_ns, st.st_size)
        except OSError:
            return None

    async def _agents_loop(self) -> None:
        """Register `homalawn mcp` with agents installed after `desktop install` (agents.sync: never
        one the user removed it from): at start, when Omarchy's default agent changes, and hourly."""
        import time
        last, stamp = None, None
        while True:
            now_stamp = await asyncio.to_thread(self._default_agent_stamp)
            if last is None or now_stamp != stamp or time.monotonic() - last >= self.agents_sync_interval:
                last, stamp = time.monotonic(), now_stamp
                try:
                    async with self._agents_lock:
                        lines = await asyncio.to_thread(self.agents.sync, quiet=True)
                    for line in lines:
                        log.info("%s", line)
                except Exception:
                    log.exception("registering Homa Lawn with the coding agents failed; trying again later")
            await asyncio.sleep(self.agents_check_interval)

    # -- the Agents page -------------------------------------------------------------------------
    async def m_agents_status(self, params, writer):
        """The supported agents installed here and whether `homalawn mcp` is in their settings
        (agents.status), plus Omarchy's default agent."""
        return {"agents": await asyncio.to_thread(self.agents.status), "command": " ".join(self.agents.COMMAND),
                "default": await asyncio.to_thread(self.agents.omarchy_default)}

    async def m_agents_connect(self, params, writer):
        """{agent}: (re)register `homalawn mcp` in that agent with its own command."""
        agent = params.get("agent")
        if not isinstance(agent, str) or not agent:
            raise Fail("invalid", "agent must be an agent id (" + ", ".join(self.agents.REGISTRY) + ").")
        if self._agents_lock.locked():
            raise Fail("busy", "Another agent is being connected; try again when it has finished.")
        async with self._agents_lock:
            try:
                text = await asyncio.to_thread(self.agents.connect, agent)
            except self.agents.AgentError as e:
                raise Fail("op_failed", plain_error(e))
            return {"text": text, "agents": await asyncio.to_thread(self.agents.status)}

    async def m_recipes_list(self, params, writer):
        """The recipes agents follow for jobs inside other apps: [{name, title, description, needs, verified}]
        (the window hides unverified ones)."""
        from . import recipes
        try:
            return await asyncio.to_thread(recipes.list_)
        except OSError as e:
            raise Fail("op_failed", f"The recipes could not be read: {plain_error(e, capital=False)}")

    async def m_agents_changes(self, params, writer):
        """What agents changed: the snapshots the agent tool server took before its changes, of the
        paired phones, newest first ({phone, name, kind, time}); at most `limit` (default 30)."""
        limit = params.get("limit", 30)
        if isinstance(limit, bool) or not isinstance(limit, int) or limit < 1:
            raise Fail("invalid", "limit must be a whole number from 1.")
        if not self.phones:
            return []
        first = next(iter(self.phones.values()))
        try:
            r = await asyncio.to_thread(self.ops.snapshot_list, first.session)
        except Exception as e:
            raise Fail("op_failed", plain_error(e))
        owner = {ph.device: ph.name for ph in self.phones.values() if ph.device}
        rows = [{"phone": owner[row["device"]],
                 **{k: v for k, v in _history_row(row).items() if k in ("name", "kind", "title", "contents", "time")}}
                for row in r.data or [] if row.get("by") == "agent" and row.get("device") in owner]
        rows.sort(key=lambda x: x["time"] or 0, reverse=True)
        return rows[:limit]

    # -- the Agents page's chat ------------------------------------------------------------------
    async def m_chat_history(self, params, writer):
        """The conversation, who answers it ({agent: {id, name, supported, installed, why}}) and
        whether an answer is being written (`running`)."""
        await asyncio.to_thread(self.chat.agent)
        return self.chat.snapshot()

    async def m_chat_send(self, params, writer):
        """{text, phone?}: ask Omarchy's default agent about that phone (the only paired one when
        not given). The answer streams as `chat` events; the reply comes at once."""
        from .chat import ChatError
        text = params.get("text")
        if not isinstance(text, str):
            raise Fail("invalid", "text must be the message to send.")
        name = params.get("phone")
        if name is None and not self.phones:
            phone = None
        else:
            phone = self._phone({"phone": name})
        who = await asyncio.to_thread(self.chat.agent)
        try:
            return await self.chat.send(text, phone and phone.fp, who, phone and phone.name)
        except ChatError as e:
            raise Fail(e.code, str(e))

    async def m_chat_cancel(self, params, writer):
        """Stop the answer being written (the agent's process group is ended)."""
        return {"cancelled": await self.chat.cancel()}

    async def m_chat_new(self, params, writer):
        """Start over: a running answer is stopped and the conversation forgotten."""
        await self.chat.cancel()
        self.chat.new()
        await asyncio.to_thread(self.chat.agent)
        return self.chat.snapshot()

    async def m_service_info(self, params, writer):
        if self._version is None:
            self._version = await asyncio.to_thread(_default_version)
        return {"version": self._version, "socket": str(self.socket_path), "pid": os.getpid(),
                "started": self.started}

    async def m_pair_find(self, params, writer):
        from .link import discovery
        if discovery.missing():
            raise Fail("no_avahi", discovery.MISSING + " Or type the phone's address.")
        hits = await asyncio.to_thread(lambda: discovery.phones(discovery.browse()))
        return [{"host": h.host, "name": h.hostname or h.name} for h in hits]

    async def m_pair_start(self, params, writer):
        host = params.get("host")
        if not isinstance(host, str) or not host:
            raise Fail("invalid", "host is required.")
        if self._pairing is not None:
            raise Fail("busy", "A pairing is already running.")
        from .link import identity, pairing, wire
        try:
            h, port = wire.parse_host(host)
        except ValueError:
            raise Fail("invalid", f"{host} is not a phone address.")
        stop, conns, ended = threading.Event(), [], threading.Event()
        self._pairing = (stop, conns, ended)
        loop = asyncio.get_running_loop()
        emit = lambda **p: loop.call_soon_threadsafe(self._pair_event, p)
        words: list[str] = []

        def show(w) -> None:
            words[:] = w
            emit(stage="words", words=list(words))

        def say(m: str) -> None:
            # once the words are up, the next message keeps them (the window shows the latest stage)
            emit(stage="waiting", words=list(words), message=m) if words else emit(stage="opening", message=m)

        def work() -> dict:
            try:
                peer = pairing.pair(h, port, confirm=lambda w: True, say=say, show_words=show,
                                    stop=stop, on_conn=conns.append)
                return {"stage": "done", "phone": peer.fp}
            except Exception as e:
                if stop.is_set():   # the cancel shut the connection: whatever broke, the person cancelled
                    return {"stage": "cancelled", "message": "Cancelled. Nothing was paired."}
                if isinstance(e, (pairing.PairError, identity.IdentityError)):
                    return {"stage": "failed", "message": str(e)}
                log.exception("pairing failed")
                return {"stage": "failed", "message": f"Pairing failed: {plain_error(e, capital=False)}"}
            finally:
                ended.set()

        async def go():
            outcome = {"stage": "failed", "message": "Pairing did not finish."}
            try:
                outcome = await asyncio.to_thread(work)
            finally:
                self._pairing = None
                with contextlib.suppress(Exception):
                    await self._sync_peers()   # the new phone is in the state before the window hears "done"
                self._pair_event(outcome)
        self._pair_task = self._spawn(go())
        return {"started": True}

    async def m_phone_unpair(self, params, writer):
        """{phone} -> {told: HOST:PORT | null, name, message}: ask the phone to forget this
        computer, then forget it here. The phone leaves the list at once."""
        ph = self._phone(params)
        from .link import identity, pairing
        peer = next((p for p in await asyncio.to_thread(identity.load_peers) if p.fp == ph.fp), None)
        if peer is None:
            raise Fail("unknown_phone", f"{ph.name} is not paired any more.")
        if self.busy.get(ph.fp):
            raise Fail("busy", f"{ph.name} is busy ({self.busy[ph.fp]}); try again when it has finished.")
        name = ph.name
        async with ph.lock:
            await asyncio.to_thread(ph.session.reset)
            told = await asyncio.to_thread(pairing.unpair, peer)
        await self._sync_peers()
        if told:
            message = f"Forgot {name} on both sides."
        else:
            message = (f"Forgot {name} here. The phone was not reachable; forget this computer "
                       "there in Homa Lawn's settings \u203a Computers.")
        return {"told": told, "name": name, "message": message}

    async def m_phone_addresses(self, params, writer):
        """{phone} -> {addresses: [{address, kind, removable, answered}]}: the phone's addresses in
        the order they are dialled (ops.addresses_list)."""
        ph = self._phone(params)
        r = await self._address_op(ph, self.ops.addresses_list)
        return {"addresses": r.data}

    async def m_phone_addresses_set(self, params, writer):
        """{phone, addresses: [HOST[:PORT]]} -> {addresses: rows, message}: the added addresses
        replaced by the list (ops.addresses_set); a bad one is `invalid` with the sentence, nothing
        is saved. The link redials from the new list."""
        ph = self._phone(params)
        raw = params.get("addresses")
        if not isinstance(raw, list) or not all(isinstance(a, str) for a in raw):
            raise Fail("invalid", "addresses must be a list of text.")
        r = await self._address_op(ph, self.ops.addresses_set, raw)
        await asyncio.to_thread(ph.session.reset)      # the next dial reads the new list
        ph.wake.set()
        rows = await self._address_op(ph, self.ops.addresses_list)
        return {"addresses": rows.data, "message": r.text}

    async def _address_op(self, ph, fn, *args):
        try:
            return await asyncio.to_thread(fn, ph.fp, *args)
        except Exception as e:
            if e.__class__.__name__ == "OpError":
                raise Fail("invalid", str(e))
            raise

    # -- the app for a new phone (Add phone › New phone) -------------------------------------------
    def _serving_state(self) -> dict | None:
        sv = self._serving
        if sv is None:
            return None
        r = sv["reply"]
        return {k: r[k] for k in ("url", "urls", "expires_at", "version", "source")} | {"message": sv["message"]}

    async def m_app_serve(self, params, writer):
        """{} -> {url, urls, expires_at, qr, version, source, file}: the Homa Lawn app for a new
        phone to download, as `homalawn update --download` gives it (update.new_phone_app).
        source "release" (no build here): the newest GitHub release, downloaded and checked here
        first; `url` (and the only entry of `urls`) is its HTTPS download address on GitHub, the
        phone downloads straight from there, nothing is served on this computer, `expires_at` is
        null (no time limit) and `serving.message` is update.FROM_GITHUB until app.serve_stop.
        source "checkout" (this checkout's release build): served over plain HTTP at one
        random-token address on this computer's LAN and Tailscale addresses, port 47101, for 10
        minutes; `url` is the first address (LAN first), `expires_at` unix time, `serving.message`
        the server's latest line. `qr` is `url`'s QR code as rows of 0/1 (1 = dark, with the
        4-module quiet border). Asked again while it is offered: the same answer. Errors (one
        sentence each): `no_app` (no build here and no usable release), `no_network` (no LAN or
        Tailscale address), `port_busy`."""
        async with self._serve_lock:
            if self._serving is not None:
                return self._serving["reply"]
            from . import adbpair, update
            try:
                app = await asyncio.to_thread(self._serve_apk)
            except update.UpdateError as e:
                raise Fail("no_app", str(e))
            apk, source, version = app["apk"], app["source"], app["version"]
            if source == "release":
                url = app["url"]
                self._serving = {"message": update.FROM_GITHUB, "reply": {
                    "url": url, "urls": [url], "expires_at": None,
                    "qr": [[1 if v else 0 for v in row] for row in adbpair.qr_matrix(url)],
                    "version": version, "source": source, "file": app["file"]}}
                log.info("offering %s from GitHub at %s", app["file"], url)
                self._changed()
                return self._serving["reply"]
            hosts = await asyncio.to_thread(self._serve_hosts)
            if not hosts:
                raise Fail("no_network", "This computer has no Wi-Fi/LAN or Tailscale address to serve the app on: "
                                         "connect it to the phone's network, then try again.")
            loop = asyncio.get_running_loop()
            entry: dict = {"message": None}

            def said(m: str) -> None:       # the server's own lines (download started, finished)
                log.info("serving the app: %s", m)

                def note():
                    if self._serving is entry:
                        entry["message"] = m
                        self._changed()
                loop.call_soon_threadsafe(note)
            try:
                srv = await asyncio.to_thread(update.DownloadServer, apk, hosts, said, self._serve_port)
            except update.UpdateError as e:
                raise Fail("port_busy", f"Port {self._serve_port} is already in use on this computer, so the app "
                                        "cannot be served now.") from e
            except OSError as e:
                raise Fail("port_busy", f"The app cannot be served on this computer: {plain_error(e, capital=False)}") from e
            url = srv.urls[0]
            entry.update(server=srv, reply={
                "url": url, "urls": list(srv.urls), "expires_at": self.clock() + self._serve_seconds,
                "qr": [[1 if v else 0 for v in row] for row in adbpair.qr_matrix(url)],
                "version": version, "source": source, "file": srv.filename})

            def serve() -> None:
                try:
                    srv.serve(self._serve_seconds)
                finally:
                    loop.call_soon_threadsafe(self._served, entry)
            entry["thread"] = threading.Thread(target=serve, name="homalawn-app-serve", daemon=True)
            self._serving = entry
            entry["thread"].start()
            log.info("serving %s at %s", apk.name, ", ".join(srv.urls))
            self._changed()
            return entry["reply"]

    def _served(self, entry: dict) -> None:
        """The server stopped (its time ran out, or app.serve_stop)."""
        if self._serving is entry:
            self._serving = None
            log.info("stopped serving the app")
            self._changed()

    async def m_app_serve_stop(self, params, writer):
        """{} -> {stopped: bool}: stop serving the app (false: it was not being served)."""
        return {"stopped": await self._stop_serving()}

    async def _stop_serving(self) -> bool:
        entry = self._serving
        if entry is None:
            return False
        if "server" in entry:               # a build served here (a release is only an address)
            entry["server"].stop()
            await asyncio.to_thread(entry["thread"].join, 5)
        self._served(entry)
        return True

    async def m_pair_cancel(self, params, writer):
        return {"cancelled": self._stop_pairing()}

    def _stop_pairing(self) -> bool:
        """Ends the running pairing: shut its connections down so a read blocked in the pairing
        thread returns (closing would wait for that read); the thread closes them itself. False when
        none runs or it has already ended."""
        if self._pairing is None or self._pairing[2].is_set():
            return False
        stop, conns, _ = self._pairing
        stop.set()
        for c in list(conns):
            with contextlib.suppress(Exception):
                c.sock.shutdown(socket.SHUT_RDWR)
        return True

    def _pair_event(self, p: dict) -> None:
        line = _encode({"event": "pair", "pair": p})
        for w in list(self._subscribers):
            if not w.is_closing():
                w.write(line)

    METHODS = {"subscribe": m_subscribe, "phones": m_phones, "themes.list": m_themes_list,
               "look.get": m_look_get, "look.gradients": m_look_gradients, "look.set": m_look_set,
               "look.apply": m_look_apply,
               "undo": m_undo, "snapshots": m_snapshots, "snapshot.restore": m_snapshot_restore,
               "app.update": m_app_update, "phone.info": m_phone_info, "service.info": m_service_info,
               "home.get": m_home_get, "home.apps": m_home_apps, "home.widgets": m_home_widgets,
               "home.apply": m_home_apply,
               "search.get": m_search_get, "search.apply": m_search_apply,
               "agents.status": m_agents_status, "agents.connect": m_agents_connect,
               "agents.changes": m_agents_changes, "recipes.list": m_recipes_list,
               "chat.history": m_chat_history, "chat.send": m_chat_send, "chat.cancel": m_chat_cancel,
               "chat.new": m_chat_new, "pair.find": m_pair_find, "pair.start": m_pair_start,
               "pair.cancel": m_pair_cancel, "phone.unpair": m_phone_unpair,
               "phone.addresses": m_phone_addresses, "phone.addresses_set": m_phone_addresses_set,
               "app.serve": m_app_serve, "app.serve_stop": m_app_serve_stop}


# The window's drafts: kind -> (ops -> (the op that writes it, its arguments)); the busy entry
# while one is written says the kind.
DRAFTS = {
    "home": lambda ops, d: (ops.home_apply, (d["layout"], d.get("grid"), d.get("hotseat_count"), d.get("look_prefs"))),
    "search": lambda ops, d: (ops.search_apply, (d.get("gestures"), d.get("search"))),
}

INFO_WAIT = 5   # phone.info waits this long for the phone's link before answering without it
LATEST_TTL = 60     # s: app_latest is worked out again this often, so a build made here shows within a minute; GitHub is still asked at most once an hour (release.TTL_S keeps its answer)
INTERNAL = "Something went wrong in the Homa Lawn service; its log has the details (journalctl --user -u homalawnd)."


def _default_latest(package: str | None, version: str | None) -> dict | None:
    from . import update
    return update.offer(package, version)


def _default_serve_apk() -> dict:
    """What a new phone downloads: this checkout's release build, else the newest release
    (update.new_phone_app)."""
    from . import update
    return update.new_phone_app()


def _default_serve_hosts() -> list[str]:
    from . import update
    return update.addresses()


def _download_port() -> int:
    from . import update
    return update.DOWNLOAD_PORT


def _download_seconds() -> float:
    from . import update
    return update.DOWNLOAD_S


def _history_row(row: dict) -> dict:
    """A snapshot row for History: kind (the window section) and title, from the row when ops gave
    them, else from the snapshot's own record; contents (what it holds) and time from its name."""
    name = row["name"]
    desc = {"kind": row["kind"], "title": row["title"]} if "title" in row and "kind" in row \
        else snapshots_mod.describe(name)
    return {**row, **desc, "contents": row.get("contents") or snapshots_mod.kind_of(name),
            "time": snapshots_mod.time_of(name)}


# -- errors a person reads ---------------------------------------------------------------------------
_ERRNO = re.compile(r"\s*\[Errno -?\d+\]\s*")
_CLASS = re.compile(r"\b(?:[A-Z][A-Za-z]*(?:Error|Exception))\b:?\s*")
_VIA = re.compile(r"^(?:link|adb|connection) failed(?: on [^:]+)?:\s*")
_CODE = re.compile(r"^[a-z]+(?:_[a-z]+)+:\s*")      # a phone's error code before its message (shot_failed: ...)
_TRUST = ("not_paired", "pin_mismatch")


def _chain(e: BaseException):
    seen = set()
    while e is not None and id(e) not in seen:
        seen.add(id(e))
        yield e
        e = e.__cause__ or e.__context__


def sentence(text: str, capital: bool = True) -> str:
    """Error text as one sentence for a person: no errno numbers, no exception class names, no
    "link failed:" or error-code prefix; a capital first letter (unless `capital` is false: the
    text goes after a colon and keeps its own first letter) and a full stop."""
    t = _ERRNO.sub(" ", str(text or ""))
    t = _VIA.sub("", t.strip())
    t = _CODE.sub("", t)
    t = _CLASS.sub("", t)
    t = re.sub(r"\(\s*\)", "", t)
    t = re.sub(r"\s+", " ", t).strip(" :;,-")
    if not t:
        return ""
    if capital:
        t = t[0].upper() + t[1:]
    return t if t[-1] in ".!?" else t + "."


def plain_error(e: BaseException, phone: str | None = None, address: str | None = None,
                capital: bool = True) -> str:
    """The one sentence a person sees for a failure. A phone that could not be reached: "<phone>
    did not answer at <address>." (or "… broke during the change; …" when a write was cut off); a
    phone that no longer trusts this computer keeps its own explanation; anything else, its text
    made a sentence (INTERNAL when it has none)."""
    from .link.client import LinkDown
    for x in _chain(e):
        if getattr(x, "code", None) in _TRUST and x is not e and str(x):
            return sentence(str(x))
    if getattr(e, "code", None) not in _TRUST:
        down = next((x for x in _chain(e) if isinstance(x, LinkDown)), None)
        if down is not None or getattr(e, "transport", False):
            who = phone or "The phone"
            if down is not None and "may or may not" in str(down):
                return f"The connection to {who} broke during the change; it may or may not have been carried out."
            if down is not None and "broke during" in str(down):
                return f"The connection to {who} broke; try again."
            return f"{who} did not answer at {address}." if address else f"{who} did not answer."
    return sentence(str(e), capital) or INTERNAL


def _plain_address(addr: str | None) -> str | None:
    """HOST:PORT as people read it: the link's own port (47100) left out."""
    if isinstance(addr, str) and addr.endswith(":47100") and addr.count(":") == 1:
        return addr[:-len(":47100")]
    return addr


def _encode(msg: dict) -> bytes:
    return (json.dumps(msg, default=str) + "\n").encode()


def run() -> None:
    """`homalawn serve`: run until stopped."""
    logging.basicConfig(level=logging.INFO, format="homalawn serve: %(message)s")
    svc = Service()
    try:
        asyncio.run(svc.serve_forever())
    except KeyboardInterrupt:
        pass
