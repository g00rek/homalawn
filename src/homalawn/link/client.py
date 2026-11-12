"""`LinkTransport`: the `Transport` over the paired TLS link (spec D3, D6, D9).

Dials the paired phone's addresses in one order (spec section 8: the address that answered last,
the last LAN address, the addresses the user added, the Tailscale addresses the phone reported when
this computer is on Tailscale too, the address it paired through, then every mDNS hint), each with
a 1.5 s connect timeout and 3 s for TLS plus `link.hello`. The phone's certificate must hash to the
pin. At an address this computer was told or remembered, any other
certificate is a hard error (`pin_mismatch`), never a reason to quietly try the next address; an
mDNS hint carries no identity (any Homa Lawn phone on the network answers it), so another
certificate there only means "not this phone" and the next hint is tried. One connection serves the
whole CLI run: a small keep-alive sends `link.ping` while it sits idle (the phone drops a
connection after 60 s of silence), and a connection the phone has hung up is re-dialled before
the next request. Files travel as blob frames: the link has no generic file access and no shell.
"""
from __future__ import annotations

import contextlib
import ssl
import threading
import time
from typing import Any, Callable

from ..screen import SHOT_RETRY_S
from ..transport import NotSupported, ProviderError, TransportError
from . import addresses, discovery, identity
from .identity import Identity, Peer
from .wire import MAX_BLOB, PORT, Conn, LinkError, fmt_host, parse_host

CONNECT_S = 1.5
HELLO_S = 3.0
CALL_S = 150.0        # the phone may wait up to 90 s for its write lock, then run the change
KEEPALIVE_S = 20.0    # well under the phone's 60 s idle timeout
SQLITE_MAGIC = b"SQLite format 3\x00"

# Answers that are safe to ask again on a fresh connection when the old one broke mid-request.
_REPEATABLE = frozenset({
    "link.hello", "link.ping", "launcher.ping", "launcher.apps_list", "launcher.prefs_list",
    "launcher.wallpaper_info", "launcher.widgets_list", "launcher.widget_status", "launcher.layout_export",
    "launcher.wallpaper_backup", "launcher.ensure_loaded", "launcher.app_info", "launcher.device_profile", "screen.status", "screen.tree", "screen.shot",
})


class LinkDown(TransportError):
    """No address of the paired phone answered, or the connection broke."""


class PinMismatch(TransportError):
    """Something answered at a phone address with a certificate other than the pinned one."""
    code = "pin_mismatch"


class OtherPhone(TransportError):
    """The address answered as a different phone this computer is also paired with."""


class BlobTooLarge(TransportError):
    """A file is bigger than one blob frame may be (spec D6: 32 MiB); nothing was sent."""


class NotPaired(TransportError):
    """The phone no longer trusts this computer. `forgotten`: the phone forgot it on purpose (it was
    removed from peers.json here); otherwise the phone lost its pairings."""
    code = "not_paired"

    def __init__(self, message: str, forgotten: bool = False):
        super().__init__(message)
        self.forgotten = forgotten


def _addr_text(host: str, port: int) -> str:
    return host if port == PORT and ":" not in host else fmt_host(host, port)


class LinkTransport:
    name = "link"
    has_shell = False

    def __init__(self, peer: Peer, hosts: list[str] | None = None, *, ident: Identity | None = None,
                 discover: Callable[[], list[discovery.Hit]] | None = discovery.browse,
                 sweep: Callable[[], list[str]] | None = lambda: addresses.lan_sweep(),
                 connect_s: float = CONNECT_S, hello_s: float = HELLO_S, call_s: float = CALL_S,
                 keepalive_s: float | None = KEEPALIVE_S, only_hosts: bool = False,
                 others: dict[str, str] | None = None):
        """`only_hosts`: dial exactly `hosts` (none of the phone's own addresses, no mDNS), for
        `homalawn connect HOST`; `hosts` is for that only. `others`: fingerprint -> name of the
        other paired phones (default: read from peers.json); a certificate of one of them at an
        address is skipped as "that is <name>", any other unknown certificate is a hard pin_mismatch."""
        if hosts and not only_hosts:
            raise ValueError("hosts are dialled only with only_hosts; a phone's own addresses live in peers.json")
        self.peer = peer
        self.label = peer.name
        self._hosts = list(hosts or [])
        self._only_hosts = only_hosts
        self._others = others
        self._ident = ident
        self._discover = discover
        self._sweep = sweep
        self._connect_s, self._hello_s, self._call_s = connect_s, hello_s, call_s
        self._keepalive_s = keepalive_s
        self._conn: Conn | None = None
        self._lock = threading.RLock()
        self._last = 0.0
        self._stop = threading.Event()
        self._ka: threading.Thread | None = None
        self.address: str | None = None   # the address that answered, for "via link (...)"
        self.tried: list[str] = []        # every address dialled, with why it failed
        self.other_seen = False           # some address answered as another paired phone

    # -- addresses ------------------------------------------------------------
    def candidates(self) -> list[str]:
        """`last_ok`, `last_lan`, `addresses`, `learned` (only when this computer has a Tailscale
        address itself), the address paired through; each once. mDNS comes after, only when all fail."""
        if self._only_hosts:
            return list(dict.fromkeys(h for h in self._hosts if h))
        from . import addresses
        p = self.peer
        learned = p.learned if p.learned and addresses.local_tailscale() else []
        out = [p.last_ok, p.last_lan] + [fmt_host(*parse_host(a)) for a in p.addresses + learned] + list(p.hosts)
        return list(dict.fromkeys(a for a in out if a))

    @property
    def fingerprint(self) -> str:
        """THIS computer's certificate fingerprint (the phone's is `peer.fp`): how the phone names
        this computer, e.g. as a look source."""
        return self._identity().fingerprint

    # -- connecting -----------------------------------------------------------
    def _identity(self) -> Identity:
        if self._ident is None:
            self._ident = identity.ensure()
        return self._ident

    def _not_paired(self, forgotten: bool) -> NotPaired:
        if forgotten:
            identity.remove_peer(self.peer.fp)
            return NotPaired(f"{self.peer.name} forgot this computer, so it was removed here. Pair it again "
                             "with `homalawn pair`.", forgotten=True)
        return NotPaired(f"{self.peer.name} no longer trusts this computer; run `homalawn unpair`, then "
                         "`homalawn pair HOST`.")

    def _dial(self, addr: str) -> Conn:
        host, port = parse_host(addr)
        ident = self._identity()
        c = Conn(host, port, str(ident.cert), str(ident.key), timeout=self._connect_s,
                 handshake_timeout=self._hello_s)
        try:
            if c.peer_fp != self.peer.fp:
                other = self._other_peers().get(c.peer_fp)
                if other is not None:
                    raise OtherPhone(f"that address is {other}, another paired phone")
                raise PinMismatch(
                    f"Something other than {self.peer.name} answers at {_addr_text(host, port)} (its certificate "
                    f"{c.peer_fp[:16]}… is not the paired one, {self.peer.fp[:16]}…), so nothing was sent to it. If "
                    "the phone was reset or Homa Lawn reinstalled, run `homalawn unpair` and `homalawn pair HOST` "
                    "again; until then use `--via adb`. (pin_mismatch)")
            hello = c.call("link.hello", timeout=self._hello_s)
            if not (isinstance(hello, dict) and hello.get("paired")):
                raise self._not_paired(bool(isinstance(hello, dict) and hello.get("forgotten")))
            self._learn(hello.get("addresses"))
            c.sock.settimeout(self._call_s)
            self.address = _addr_text(host, port)
            return c
        except BaseException:
            c.close()
            raise

    def _learn(self, reported) -> None:
        """The phone's Tailscale addresses (spec 8) replace `learned`; the rest it reports is not
        kept. Only that field is written, and only when it changed."""
        if not isinstance(reported, list):
            return
        from . import addresses
        found: list[str] = []
        for a in reported:
            try:
                n = addresses.normalize(a) if isinstance(a, str) else None
            except ValueError:
                continue
            if n and addresses.is_tailscale(n) and n not in found:
                found.append(n)
        found = found[:addresses.MAX_LEARNED]
        if found == self.peer.learned:
            return
        self.peer.learned = found
        try:
            identity.update_peer(self.peer.fp, **{"learned": found})
        except OSError:
            pass

    def _other_peers(self) -> dict[str, str]:
        if self._others is None:
            try:
                self._others = {p.fp: p.name for p in identity.load_peers() if p.fp != self.peer.fp}
            except (OSError, ValueError):
                self._others = {}
        return self._others

    def _remember(self, addr: str) -> None:
        """The address that answered goes first next time (`last_ok`), and a private-LAN one is also
        kept as `last_lan`. Only these fields are written: the user may edit `addresses` meanwhile."""
        host, port = parse_host(addr)
        changed = {"last_ok": addr} if addr != self.peer.last_ok else {}
        lan = identity.lan_address(host, port)
        if lan and lan != self.peer.last_lan:
            changed["last_lan"] = lan
        if not changed:
            return
        for k, v in changed.items():
            setattr(self.peer, k, v)
        try:
            identity.update_peer(self.peer.fp, **changed)
        except OSError:
            pass

    def connect(self) -> None:
        """Dial until one address answers as the paired phone. PinMismatch and NotPaired stop at once."""
        with self._lock:
            if self._conn is not None:
                return
            self.tried = []
            tried_addrs: set[str] = set()

            def attempt(addr: str, hint: bool = False) -> bool:
                tried_addrs.add(addr)
                try:
                    self._conn = self._dial(addr)
                except PinMismatch:
                    if not hint:
                        raise
                    self.tried.append(f"{addr} (mDNS: another phone)")
                    return False
                except NotPaired:
                    raise
                except OtherPhone as e:
                    self.other_seen = True
                    self.tried.append(f"{addr} ({e})")
                    return False
                except (OSError, ssl.SSLError, ConnectionError, LinkError, ValueError) as e:
                    self.tried.append(f"{addr} ({type(e).__name__}: {e})" if str(e) else f"{addr} ({type(e).__name__})")
                    return False
                self._last = time.monotonic()
                self._remember(addr)
                self._start_keepalive()
                return True

            for addr in self.candidates():
                if attempt(addr):
                    return
            use_mdns = self._discover is not None and not self._only_hosts
            hits = discovery.for_peer(self.peer.fp, self._discover() if use_mdns else [])
            for h in hits:
                if h.host not in tried_addrs and attempt(h.host, hint=True):
                    return
            # the router gave the phone another home address and mDNS is silent (an Android phone
            # often stops answering it): search this computer's home networks for the link's port
            for addr in (self._sweep() if self._sweep is not None and not self._only_hosts else []):
                if addr not in tried_addrs and attempt(addr, hint=True):
                    return
            where = "; ".join(self.tried) or "no address known (run `homalawn connect HOST`, or pair again)"
            raise LinkDown(f"{self.peer.name} did not answer over the link: {where}")

    @contextlib.contextmanager
    def capped(self, seconds: float):
        """Within this block no dial, handshake or call waits longer than `seconds` (and mDNS is
        not browsed): for a caller with its own deadline, e.g. waiting for the phone to restart."""
        cap = max(0.1, seconds)
        with self._lock:
            saved = (self._connect_s, self._hello_s, self._call_s, self._discover, self._sweep)
            self._connect_s, self._hello_s, self._call_s = (min(v, cap) for v in saved[:3])
            self._discover = self._sweep = None
            if self._conn is not None:
                self._conn.sock.settimeout(self._call_s)
        try:
            yield self
        finally:
            with self._lock:
                self._connect_s, self._hello_s, self._call_s, self._discover, self._sweep = saved
                if self._conn is not None:
                    try:
                        self._conn.sock.settimeout(self._call_s)
                    except OSError:
                        pass

    def close(self) -> None:
        self._stop.set()
        with self._lock:
            if self._conn is not None:
                self._conn.close()
                self._conn = None

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()

    # -- keep-alive -----------------------------------------------------------
    def _start_keepalive(self) -> None:
        if not self._keepalive_s or (self._ka and self._ka.is_alive() and not self._stop.is_set()):
            return
        self._stop = stop = threading.Event()  # a fresh one: an old thread stopped by close() stays stopped
        self._ka = threading.Thread(target=self._keepalive, args=(stop,), name="homalawn-link-keepalive", daemon=True)
        self._ka.start()

    def _keepalive(self, stop: threading.Event) -> None:
        step = min(1.0, self._keepalive_s / 4)
        while not stop.wait(step):
            if time.monotonic() - self._last < self._keepalive_s:
                continue
            if not self._lock.acquire(blocking=False):
                continue  # a request is running: the connection is not idle
            try:
                if self._conn is None:
                    continue
                try:
                    self._conn.call("link.ping", timeout=self._hello_s)
                    self._conn.sock.settimeout(self._call_s)
                    self._last = time.monotonic()
                except (OSError, ConnectionError, LinkError, ValueError):
                    self._drop()
            finally:
                self._lock.release()

    # -- requests -------------------------------------------------------------
    def _drop(self) -> None:
        if self._conn is not None:
            self._conn.close()
            self._conn = None

    def _request(self, method: str, params: dict | None = None, blob: bytes | None = None
                 ) -> tuple[Any, bytes | None]:
        if method.startswith("link.pair") or method == "link.unpair":
            raise NotSupported("pairing runs through `homalawn pair` / `homalawn unpair`, not through a command")
        if blob is not None and len(blob) > MAX_BLOB:
            raise BlobTooLarge(f"{len(blob) / (1 << 20):.1f} MiB is more than the link carries in one piece "
                               f"({MAX_BLOB >> 20} MiB); nothing was sent")
        with self._lock:
            for attempt in (1, 2):
                if self._conn is not None and self._conn.readable_eof():
                    self._drop()  # the phone hung up while we were idle: dial again before sending
                self.connect()
                try:
                    r = self._conn.request(method, params, blob=blob)
                    self._last = time.monotonic()
                    return r
                except LinkError as e:
                    self._last = time.monotonic()
                    if e.code == "not_paired":
                        self._drop()
                        raise self._not_paired(bool((e.detail or {}).get("forgotten")))
                    msg = e.message if e.message.startswith(e.code + ":") else f"{e.code}: {e.message}"
                    raise ProviderError(msg, code=e.code, data=e.detail)
                except (OSError, ConnectionError, ValueError) as e:
                    self._drop()
                    if attempt == 1 and method in _REPEATABLE:
                        continue
                    what = "" if method in _REPEATABLE else " It may or may not have been carried out."
                    raise LinkDown(f"the link to {self.peer.name} ({self.address}) broke during {method}: "
                                   f"{type(e).__name__}: {e}.{what}")
        raise AssertionError("unreachable")

    def call(self, method: str, params: dict | None = None) -> Any:
        return self._request(method, params)[0]

    def request(self, method: str, params: dict | None = None, blob: bytes | None = None
                ) -> tuple[Any, bytes | None]:
        """One call that may carry a blob each way: (result, the phone's blob or None)."""
        return self._request(method, params, blob)

    def hello(self) -> dict:
        return self.call("link.hello")

    # -- files ----------------------------------------------------------------
    def export_layout(self) -> tuple[str, bytes]:
        meta, data = self._request("launcher.layout_export")
        if data is None or not data.startswith(SQLITE_MAGIC):
            raise LinkDown("the launcher's layout export came back damaged")
        return (meta or {})["db"], data

    def import_layout(self, data: bytes, db: str | None = None) -> dict:
        return self._request("launcher.layout_import", {"db": db} if db else {}, blob=data)[0]

    def nova_convert(self, backup: bytes, params: dict) -> tuple[dict, bytes]:
        """A Nova backup in, the launcher DB Lawnchair's converter makes of it out (patch 0012)."""
        try:
            meta, data = self._request("launcher.nova_convert", params, blob=backup)
        except ProviderError as e:
            if e.code == "unknown_method":
                raise NotSupported("The Homa Lawn build on the phone cannot import a Nova backup yet; update it "
                                   "(`homalawn update`)") from e
            raise
        if data is None or not data.startswith(SQLITE_MAGIC):
            raise LinkDown("the converted layout came back damaged")
        return meta or {}, data

    def write_private(self, name: str, data: bytes) -> str:
        return self._request("launcher.stage_file", {"name": name}, blob=data)[0]["path"]

    def wallpaper_backup(self, which: str = "home") -> bytes:
        _, data = self._request("launcher.wallpaper_backup", {"which": which} if which != "home" else None)
        return data or b""

    def screenshot(self) -> bytes:
        """PNG through the phone's accessibility service (`screen.shot`); needs screen control on."""
        try:
            try:
                _, data = self._request("screen.shot")
            except ProviderError as e:
                if e.code != "rate_limited":
                    raise
                time.sleep(SHOT_RETRY_S)  # the phone allows one screenshot every 340 ms: wait once
                _, data = self._request("screen.shot")
        except ProviderError as e:
            if e.code == "screen_off":
                raise NotSupported("Screenshots over the link need screen control: on the phone, turn it on in Homa Lawn's "
                                   "settings › Permissions › Screen control, or use `homalawn --via adb screenshot`")
            if e.code == "unknown_method":
                raise NotSupported("The Homa Lawn build on the phone has no screen control yet; update it, or use "
                                   "`homalawn --via adb screenshot`")
            raise
        if not data or not data.startswith(b"\x89PNG"):
            raise LinkDown("the screenshot came back damaged")
        return data

    def shell(self, *args: str) -> str:
        raise NotSupported("The link cannot run commands on the phone's shell; that needs `--via adb`.")
