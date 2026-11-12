"""`homalawn update`: a new Lawnchair build to the phone, the way Flux does it (spec U2, U3).

Over the link (U2): the phone reports what it runs (`launcher.app_info`); the same build is not
sent again. Otherwise the APK goes in chunks of at most 8 MiB (`launcher.update_offer`, each
carrying the APK's name, sha256, size and the chunk's offset), in order from 0. The phone appends
them to one file and, after the last byte, checks it (our package, our signing key, not older, the
sha256). A phone allowed to "Install unknown apps" for Homa Lawn (patch 0007) then installs it by
itself and answers `silent: true`: its home screen restarts, and this waits until the link is back
and `launcher.app_info` reports the new sha256. A locked phone keeps the build and installs it at the
next unlock (`deferred_until_unlock: true`); this then says so and does not wait. When the new build
does not come back in time, `launcher.update_status` says what happened on the phone. Otherwise (no `silent`, an older phone build, or
Android wants the person) it posts a notification; the person taps it, then Update. A chunk the phone did not take
where it was sent is answered `bad_offset` with where the file ends (`expected`), and the sending
continues from there, so a dropped connection costs one chunk.

By download (U3), for a phone whose Homa Lawn cannot update itself yet (or a new phone): a QR code
and an address the phone opens (new_phone_app picks what it gets).
- The newest GitHub release: the address is the release file's own HTTPS download address on GitHub
  (`browser_download_url`); the phone downloads straight from GitHub and nothing is served here.
  This computer first downloads and checks that exact file (release.py), so it names only a file
  whose checksum and signing certificate it has checked.
- A build from this checkout, or `--apk FILE` (the developer path): served over plain HTTP on this
  computer's LAN and Tailscale addresses, at one path with a random token, for 10 minutes; the QR
  code (terminal, plus a picture opened on the desktop) carries the address.

The link method names live here only (`APP_INFO`, `UPDATE_OFFER`, `UPDATE_STATUS`).
"""
from __future__ import annotations

import hashlib
import http.server
import ipaddress
import os
import secrets
import signal
import subprocess
import threading
import time
from pathlib import Path
from typing import Callable

from . import adbpair, appid

APP_INFO = "launcher.app_info"
UPDATE_OFFER = "launcher.update_offer"
UPDATE_STATUS = "launcher.update_status"
STATUS_WAIT_S = 10      # the one status question after a timeout
CHUNK = 8 << 20
SILENT_WAIT_S = 60      # how long a silent update may take to come back with the new build
MAX_STALLS = 3          # bad_offset / dropped-connection retries in a row without the phone moving forward
DOWNLOAD_S = 600
DONE_GRACE_S = 15   # after a download completes, the server stays up this long for a retry, then stops
DOWNLOAD_PORT = 47101   # fixed, so one firewall rule covers it
CONNECTION_TIMEOUT_S = 30   # a connection to the download server that stays silent this long is dropped
UFW_RULES = Path("/etc/ufw/user.rules")
APK_TYPE = "application/vnd.android.package-archive"
APK_DIR = appid.apk_dir()  # build-lawnchair.sh's release build; the test build's folder for a phone running it
QR_FILE = "homalawn-update-qr.png"

TAP = "On the phone: tap the Homa Lawn update notification, then Update."
SILENT = "Updating {phone} in the background; it restarts its home screen in a few seconds."
UPDATED = "Updated."
LOCKED = "{phone} is locked; the update installs by itself the next time it is unlocked."
# after the wait ran out: what launcher.update_status says happened (the "failed" one adds the reason)
OUTCOME = {
    "installing": ("{phone} has not finished installing it after {s} seconds; Android may still be checking it "
                   "(Google Play Protect). Run `homalawn update` again in a minute to check."),
    "pending_user_action": "{phone} needs a tap to install it: tap the Homa Lawn update notification, then Update.",
    "deferred": LOCKED,
    "failed": "{phone} did not install it ({reason}). Run `homalawn update` again.",
    # the phone says installed, but app_info reports another build (e.g. an adb install in between)
    "installed": ("{phone} reports the update as installed, but it does not run this build now; run "
                  "`homalawn update` again."),
}
NOT_BACK = ("{phone} did not come back with the new build within {s} seconds; Android may still be checking it "
            "(Google Play Protect). If the Homa Lawn update notification shows up on the phone, tap it, then Update; "
            "then run `homalawn update` again to check.")
OLD_PHONE = ("The Homa Lawn on {phone} cannot update itself yet: update it once with `homalawn update --download` "
             "or `homalawn setup`.")
# what send() did, so a caller (setup) knows whether anything is left to wait for
SAME = "same"                          # the phone already runs this build; nothing sent
INSTALLED = "installed"                # the phone runs the new build now
DEFERRED = "deferred_until_unlock"     # locked: installs by itself at the next unlock; no notification
NOTIFIED = "notified"                  # the update notification is up; it waits for the person's tap
OLD_APP = ("{phone} runs the old Homa Lawn app ({old}), which is no longer supported: remove it from the phone, "
           "then run `homalawn setup` to install the new app ({new}). Its home screen and settings are not carried "
           "over.")
OTHER_APP = ("This build is {apk_pkg}, but {phone} runs {phone_pkg}; pass the build for that app with --apk{both}.")
REFUSED = {
    "not_ours": "The phone refused it: this file is not the Homa Lawn app the phone runs.",
    "wrong_key": ("The phone refused it: this build is signed with another key than Homa Lawn on the phone "
                  "(installing it would need an uninstall, which wipes Homa Lawn's data — not done)."),
    "older": "The phone refused it: Homa Lawn on the phone is newer than this build.",
    "bad_hash": "The build arrived damaged (its checksum did not match); run `homalawn update` again.",
}
PLAY_PROTECT_LINK = ("If Google Play Protect shows a dialog on the phone, read it before tapping: it may ask to scan "
                     "the app or to install it anyway.")
PLAY_PROTECT = ("If Google Play Protect warns about the app, choose to install it anyway; if it does not let you, "
                "run `homalawn setup --new`. On a Samsung phone, if the install is blocked, turn off Auto Blocker "
                "for the install: Settings › Security and privacy › Auto Blocker.")


class UpdateError(Exception):
    """The update stopped; the message is the one line saying what to do."""


class CannotUpdate(UpdateError):
    """The phone's Homa Lawn has no updater yet (an older build)."""


class OldApp(UpdateError):
    """The phone runs the old app (appid.OLD), which is no longer updated."""


def _mb(n: int) -> str:
    return f"{n / 1e6:.1f} MB"


TEST_NO_BUILD = ("{phone} runs Homa Lawn's test build, which comes only from a build on this computer: {build}, or pass "
                 "--apk FILE.")


def _build_how() -> str:
    """How to build the phone app here: the script when this is the source checkout, else where
    the source is (an installed copy of the tool has no build script)."""
    if appid.in_checkout():
        return "build it with scripts/build-lawnchair.sh"
    return "build it from Homa Lawn's source (https://github.com/g00rek/homalawn, scripts/build-lawnchair.sh)"


def local_build(package: str | None = None, apk_dir: Path | None = None) -> Path | None:
    """The newest build in this checkout for a phone running `package` (None: a new phone, which
    gets the release build), when it is that app: the release folder for the release app (and for
    a new or old-app phone), the test build's folder for the test app. `apk_dir` names the folder
    instead (then a checkout is not required). None outside a checkout or without such a build."""
    want = package if package and not appid.is_old(package) else appid.RELEASE
    if apk_dir is None:
        if not appid.in_checkout():
            return None
        apk_dir = APK_DIR if want == appid.RELEASE else appid.apk_dir(want)
    apk = appid.newest_apk(apk_dir)
    if apk is None:
        return None
    got = appid.apk_package(apk)
    return apk if got is None or got == want else None


def pick_apk(apk: Path | None, apk_dir: Path | None = None, package: str | None = None, *,
             say: Callable[[str], None] | None = None, phone: str = "This phone") -> Path:
    """The APK for a phone running `package` (None: a new phone), in this order: `apk` (--apk
    FILE); else the build in this checkout for that app (local_build); else, for the release app
    or a new phone only, the newest GitHub release, downloaded and checked (release.py). A phone on
    the test build never gets the release: it is signed with another key, and Android would refuse
    it. UpdateError (one sentence) when there is nothing to offer."""
    if apk is not None:
        if not apk.is_file():
            raise UpdateError(f"{apk} is not a file.")
        return apk
    local = local_build(package, apk_dir)
    if local is not None:
        return local
    if package == appid.TEST:
        raise UpdateError(TEST_NO_BUILD.format(phone=phone, build=_build_how()))
    from . import release
    try:
        return release.newest_apk(say=say)[1]
    except release.ReleaseError as e:
        raise UpdateError(str(e)) from e


FROM_GITHUB = "The phone downloads Homa Lawn from GitHub."
LAN_ONLY = "This address is plain HTTP on your own network, meant for your own builds."


def new_phone_app(apk: Path | None = None, apk_dir: Path | None = None, *,
                  say: Callable[[str], None] | None = None) -> dict:
    """What a new phone (or one that cannot update itself) downloads: {source, apk, version, url,
    file}. source "file" (`apk`, --apk FILE) or "checkout" (this checkout's release build): `url`
    None, it is served here (DownloadServer). source "release": the newest GitHub release, downloaded
    and checked here first; `url` is its HTTPS download address on GitHub, which the phone opens
    itself. UpdateError (one sentence) when there is nothing to offer."""
    if apk is not None:
        if not apk.is_file():
            raise UpdateError(f"{apk} is not a file.")
        return {"source": "file", "apk": apk, "version": appid.apk_version(apk), "url": None, "file": apk.name}
    local = local_build(None, apk_dir)
    if local is not None:
        return {"source": "checkout", "apk": local, "version": appid.apk_version(local), "url": None,
                "file": local.name}
    from . import release
    try:
        rel, path = release.newest_apk(say=say)
    except release.ReleaseError as e:
        raise UpdateError(str(e)) from e
    return {"source": "release", "apk": path, "version": rel.version, "url": rel.url, "file": rel.asset}


def from_github(say: Callable[[str], None], app: dict) -> None:
    """`homalawn update --download` for a release: the QR code and the GitHub address; nothing is served."""
    say(f"{FROM_GITHUB} Homa Lawn {app['version']} ({app['file']}), checked on this computer first.")
    say("On the phone: scan this with the camera, or open the address in the browser, download, then open the file.")
    say(adbpair.render_qr(app["url"]))
    say(f"Address: {app['url']}")
    say(PLAY_PROTECT)


def offer(package: str | None, version: str | None, *, latest: Callable | None = None) -> dict | None:
    """What the window offers a phone running `package` at `version`: {version, newer, source}
    with source "checkout" (the build in this checkout for that app) or "release" (the newest
    GitHub release, for the release app only; nothing is downloaded here); None when nothing is
    offered (the old app, a test-build phone without a build here, no release or no network).
    `newer` compares the build numbers (…+homalawn.<N>)."""
    from . import release
    if not package or appid.is_old(package):
        return None
    have = release.build_number(version)
    local = local_build(package)
    if local is not None:
        v = appid.apk_version(local)
        source = "checkout"
    elif package == appid.RELEASE:
        try:
            v = (latest or release.latest)().version
        except release.ReleaseError:
            return None
        source = "release"
    else:
        return None
    if not v:
        return None
    n = release.build_number(v)
    return {"version": v, "newer": n is not None and have is not None and n > have, "source": source}


# -- over the link ---------------------------------------------------------------------------------
def app_info(link) -> dict:
    """What the phone runs; CannotUpdate when its build has no updater."""
    from .transport import ProviderError, TransportError
    try:
        info = link.call(APP_INFO, {})
    except ProviderError as e:
        if e.code == "unknown_method":
            raise CannotUpdate(OLD_PHONE.format(phone=link.label))
        raise UpdateError(f"The phone could not say which build it runs ({e}).")
    except TransportError as e:  # the link dropped, or the phone no longer trusts this computer
        raise UpdateError(str(e))
    return info if isinstance(info, dict) else {}


def _expected(e) -> int | None:
    v = (getattr(e, "data", None) or {}).get("expected")
    return v if isinstance(v, int) and not isinstance(v, bool) else None


def wait_for_build(link, sha: str, *, seconds: float = SILENT_WAIT_S, sleep: Callable[[float], None] = time.sleep,
                   clock: Callable[[], float] = time.monotonic, step: float = 1.0) -> bool:
    """True once the phone answers `launcher.app_info` with `sha`, within `seconds`. The link drops
    while the phone replaces its app; every failure in between is just "not yet"."""
    from .link.client import NotPaired, PinMismatch
    from .transport import TransportError, ProviderError
    end = clock() + seconds
    while True:
        left = end - clock()
        if left <= 0:
            return False
        try:
            # every dial and call inside is bounded by the time left (a LinkTransport can cap them)
            cap = getattr(link, "capped", None)
            if cap is not None:
                with cap(left):
                    info = link.call(APP_INFO, {})
            else:
                info = link.call(APP_INFO, {})
            if isinstance(info, dict) and str(info.get("sha256", "")).lower() == sha:
                return True
        except (NotPaired, PinMismatch) as e:
            raise UpdateError(str(e))  # trust is gone, not a restart: waiting changes nothing
        except (TransportError, ProviderError):
            pass
        if clock() >= end:
            return False
        sleep(min(step, max(0.0, end - clock())))


def update_status(link, sha: str) -> dict | None:
    """The phone's last update outcome for the build `sha`, or None (an older phone build, the link
    down, or an outcome about another build)."""
    from .transport import ProviderError, TransportError
    try:
        cap = getattr(link, "capped", None)
        if cap is not None:
            with cap(STATUS_WAIT_S):
                st = link.call(UPDATE_STATUS, {})
        else:
            st = link.call(UPDATE_STATUS, {})
    except (ProviderError, TransportError):
        return None
    if not isinstance(st, dict) or str(st.get("sha256", "")).lower() != sha:
        return None
    return st


def _runs(link, sha: str) -> bool:
    """One `launcher.app_info` question (bounded): does the phone run the build `sha` now?"""
    from .transport import ProviderError, TransportError
    try:
        cap = getattr(link, "capped", None)
        if cap is not None:
            with cap(STATUS_WAIT_S):
                info = link.call(APP_INFO, {})
        else:
            info = link.call(APP_INFO, {})
    except (ProviderError, TransportError):
        return False
    return isinstance(info, dict) and str(info.get("sha256", "")).lower() == sha


def after_timeout(say: Callable[[str], None], link, sha: str, seconds: float) -> str:
    """The new build did not come back within `seconds`: one plain line saying what the phone reports.
    INSTALLED (only once app_info confirms the build), NOTIFIED or DEFERRED; UpdateError otherwise."""
    st = update_status(link, sha)
    outcome = str(st.get("outcome", "")) if st else ""
    phone = link.label
    if outcome == "installed":
        if _runs(link, sha):
            say(UPDATED)
            return INSTALLED
        raise UpdateError(OUTCOME["installed"].format(phone=phone))
    if outcome == "pending_user_action":
        say(OUTCOME[outcome].format(phone=phone))
        return NOTIFIED
    if outcome == "deferred":
        say(OUTCOME[outcome].format(phone=phone))
        return DEFERRED
    if outcome in OUTCOME:
        reason = str(st.get("reason") or "no reason given").rstrip(".")
        raise UpdateError(OUTCOME[outcome].format(phone=phone, s=int(seconds), reason=reason))
    raise UpdateError(NOT_BACK.format(phone=phone, s=int(seconds)))


def check_app(info: dict, apk: Path, phone: str) -> None:
    """Refuse before sending when `apk` is another app than the one the phone runs: the phone
    would refuse it anyway (`not_ours`), after the whole upload."""
    phone_pkg, apk_pkg = info.get("package"), appid.apk_package(apk)
    if not phone_pkg or not apk_pkg or phone_pkg == apk_pkg:
        return
    if appid.is_old(phone_pkg):
        raise OldApp(OLD_APP.format(phone=phone, old=phone_pkg, new=apk_pkg))
    raise UpdateError(OTHER_APP.format(apk_pkg=apk_pkg, phone=phone, phone_pkg=phone_pkg,
                                       both=" (scripts/build-lawnchair.sh makes both)" if appid.in_checkout() else ""))


def send(say: Callable[[str], None], link, apk: Path | None, *, chunk: int = CHUNK, wait_s: float = SILENT_WAIT_S,
         sleep: Callable[[float], None] = time.sleep, clock: Callable[[], float] = time.monotonic) -> str:
    """Offer `apk` (None: the newest build here of the app the phone runs) to the phone over
    `link`. Returns what happened: SAME (the phone already runs this exact file), INSTALLED,
    DEFERRED (locked; installs at the next unlock) or NOTIFIED (waits for a tap)."""
    from .link.client import NotPaired, PinMismatch
    from .transport import ProviderError, TransportError
    info = app_info(link)
    if apk is None:
        if appid.is_old(info.get("package")):
            raise OldApp(OLD_APP.format(phone=link.label, old=info.get("package"), new=appid.RELEASE))
        apk = pick_apk(None, package=info.get("package"), say=say, phone=link.label)
    check_app(info, apk, link.label)
    data = apk.read_bytes()
    sha = hashlib.sha256(data).hexdigest()
    if str(info.get("sha256", "")).lower() == sha:
        say(f"Homa Lawn on {link.label} is already this build ({apk.name}); nothing sent.")
        return SAME
    size = len(data)
    say(f"Sending {apk.name} ({_mb(size)}) to {link.label} over the link.")
    offset, best, stalls = 0, 0, 0
    while True:
        part = data[offset:offset + chunk]
        params = {"name": apk.name, "sha256": sha, "size": size, "offset": offset}
        try:
            result, _ = link.request(UPDATE_OFFER, params, part)
        except ProviderError as e:
            if e.code == "unknown_method":
                raise CannotUpdate(OLD_PHONE.format(phone=link.label))
            if e.code in REFUSED:
                raise UpdateError(REFUSED[e.code])
            if e.code != "bad_offset":
                raise UpdateError(f"The phone refused the update ({e}).")
            exp = _expected(e)
            if exp is None or not 0 <= exp <= size:
                raise UpdateError(f"The phone lost track of the update ({e}); run `homalawn update` again.")
            stalls = stalls + 1 if exp <= best else 0
            if stalls > MAX_STALLS:
                raise UpdateError("The phone keeps losing the update in the middle; run `homalawn update` again.")
            if exp != offset and exp > 0 and offset == 0:
                say(f"The phone already has {_mb(exp)} of it; continuing from there.")
            best = max(best, exp)
            offset = exp
            continue
        except (NotPaired, PinMismatch) as e:
            raise UpdateError(str(e))  # not a dropped connection: trying again changes nothing
        except TransportError as e:
            stalls += 1
            if stalls > MAX_STALLS:
                raise UpdateError(f"The link to {link.label} broke while sending ({e}); run `homalawn update` again.")
            continue  # the same chunk again; the phone says bad_offset if it had it already
        result = result if isinstance(result, dict) else {}
        offset += len(part)
        got = result.get("received")
        if isinstance(got, int) and not isinstance(got, bool) and got != offset and not result.get("offered"):
            if not 0 <= got <= size:
                raise UpdateError("The phone lost track of the update; run `homalawn update` again.")
            stalls = stalls + 1 if got <= best else 0  # it kept less than was sent: continue where it is
            if stalls > MAX_STALLS:
                raise UpdateError("The phone keeps losing the update in the middle; run `homalawn update` again.")
            offset = got
            continue
        best, stalls = max(best, offset), 0
        if offset >= size:
            if not result.get("offered"):
                raise UpdateError("The phone took the whole build but did not offer it; run `homalawn update` again.")
            if result.get("silent") is True and result.get("deferred_until_unlock") is True:
                say(LOCKED.format(phone=link.label))
                return DEFERRED
            if result.get("silent") is True:
                say(SILENT.format(phone=link.label))
                if not wait_for_build(link, sha, seconds=wait_s, sleep=sleep, clock=clock):
                    return after_timeout(say, link, sha, wait_s)
                say(UPDATED)
                return INSTALLED
            say(f"{link.label} has the build and shows the update notification.")
            say(TAP)
            say(PLAY_PROTECT_LINK)
            return NOTIFIED
        say(f"Sent {_mb(offset)} of {_mb(size)}.")


# -- by download -----------------------------------------------------------------------------------
_SKIP_IFACES = ("lo", "docker", "br-", "veth", "virbr", "vmnet", "podman", "cni", "flannel")


def addresses(run=subprocess.run) -> list[str]:
    """This computer's LAN (private, not link-local) and Tailscale (the tailscale* interface)
    IPv4 addresses, LAN first."""
    try:
        out = run(["ip", "-o", "-4", "addr", "show"], capture_output=True, text=True, timeout=10).stdout
    except (OSError, subprocess.TimeoutExpired):
        return []
    lan, ts = [], []
    for line in out.splitlines():
        parts = line.split()
        if len(parts) < 4 or parts[2] != "inet":
            continue
        iface, addr = parts[1], parts[3].split("/")[0]
        try:
            ip = ipaddress.ip_address(addr)
        except ValueError:
            continue
        if iface.startswith("tailscale"):
            ts.append(addr)
        elif ip.is_private and not ip.is_loopback and not ip.is_link_local and not iface.startswith(_SKIP_IFACES):
            lan.append(addr)
    return list(dict.fromkeys(lan + ts))


def _network_of(addr: str, run=subprocess.run) -> str:
    """The subnet `addr` sits in on its interface (from its prefix), e.g. 192.168.1.0/24."""
    if ipaddress.ip_address(addr) in ipaddress.ip_network("100.64.0.0/10"):
        return "100.64.0.0/10"  # Tailscale
    try:
        out = run(["ip", "-o", "-4", "addr", "show"], capture_output=True, text=True, timeout=10).stdout
    except (OSError, subprocess.TimeoutExpired):
        out = ""
    for line in out.splitlines():
        parts = line.split()
        if len(parts) >= 4 and parts[2] == "inet" and parts[3].split("/")[0] == addr:
            try:
                return str(ipaddress.ip_interface(parts[3]).network)
            except ValueError:
                break
    return str(ipaddress.ip_network(f"{addr}/24", strict=False))


def firewall_hint(host: str, port: int = DOWNLOAD_PORT, run=subprocess.run,
                  rules: Path = UFW_RULES) -> str | None:
    """One line saying how to open `port` when ufw is active and has no rule allowing tcp `port`;
    None when ufw is off, the rule is there, or the rules cannot be read (no root)."""
    try:
        active = run(["systemctl", "is-active", "ufw"], capture_output=True, text=True, timeout=10).stdout.strip()
        if active != "active":
            return None
        text = rules.read_text()
    except (OSError, subprocess.TimeoutExpired):
        return None
    for line in text.splitlines():
        t = line.split()
        if (t[:2] == ["-A", "ufw-user-input"] and "ACCEPT" in t[-2:] and "-p" in t and t[t.index("-p") + 1] == "tcp"
                and "--dport" in t and t[t.index("--dport") + 1] == str(port)):
            return None
    net = _network_of(host, run)
    return ("The firewall blocks the phone from reaching this computer. Allow it once: "
            f"sudo ufw allow from {net} to any port {port} proto tcp comment homalawn-download")


class DownloadServer:
    """One file at one secret path, on the given addresses; nothing else is served. The file is
    opened once, here, and served from that open file until the server closes: replacing or
    removing it on disk meanwhile (the release cache does) changes nothing for a download. Plain
    HTTP on this computer's own network, meant for builds made here."""

    def __init__(self, apk: Path, hosts: list[str], say: Callable[[str], None], port: int = 0,
                 grace: float | None = None):
        self.apk = apk
        self.grace = DONE_GRACE_S if grace is None else grace
        self.downloaded = False
        self._timer: threading.Timer | None = None
        self._lock = threading.Lock()
        self._fd: int | None = os.open(apk, os.O_RDONLY)
        try:
            self.size = os.fstat(self._fd).st_size
            h, off = hashlib.sha256(), 0
            while block := os.pread(self._fd, 1 << 20, off):
                h.update(block)
                off += len(block)
        except BaseException:
            self._close_file()
            raise
        sha = h.hexdigest()
        self.filename = f"homalawn-{sha[:8]}.apk"
        self.path = f"/{secrets.token_urlsafe(18)}/{self.filename}"
        self._say = say
        self._stop = threading.Event()
        self._servers: list[http.server.ThreadingHTTPServer] = []
        handler = self._handler()
        try:
            for h in hosts:
                try:
                    srv = http.server.ThreadingHTTPServer((h, port), handler)
                except OSError as e:
                    if port == 0:
                        raise
                    raise UpdateError(f"Port {port} is already in use on this computer; pick another with "
                                      f"`--port N`.") from e
                srv.daemon_threads = True
                port = port or srv.server_address[1]
                self._servers.append(srv)
        except BaseException:
            self._close()
            raise
        self.urls = [f"http://{s.server_address[0]}:{s.server_address[1]}{self.path}" for s in self._servers]

    def _reader(self) -> int | None:
        """A file descriptor of its own on the served file for one download (None once closed)."""
        with self._lock:
            return None if self._fd is None else os.dup(self._fd)

    def _handler(self):
        server = self

        class Handler(http.server.BaseHTTPRequestHandler):
            timeout = CONNECTION_TIMEOUT_S      # per connection: a silent device cannot hold a thread

            def do_GET(self):
                if self.path != server.path:
                    self.send_error(404)
                    return
                fd = server._reader()            # the file first, then the 200
                if fd is None:
                    self.send_error(503)
                    return
                who = self.client_address[0]
                sent = 0
                try:
                    server._say(f"Download started by {who}.")
                    self.send_response(200)
                    self.send_header("Content-Type", APK_TYPE)
                    self.send_header("Content-Length", str(server.size))
                    self.send_header("Content-Disposition", f'attachment; filename="{server.filename}"')
                    self.end_headers()
                    while sent < server.size and (block := os.pread(fd, min(1 << 16, server.size - sent), sent)):
                        self.wfile.write(block)
                        sent += len(block)
                except OSError:
                    server._say(f"Download by {who} stopped after {_mb(sent)}.")
                    return
                finally:
                    os.close(fd)
                server._say(f"Downloaded by {who} ({_mb(sent)}).")
                server._downloaded()

            def log_message(self, *args):  # the downloads are said above; no per-request noise
                pass

        return Handler

    def serve(self, seconds: float = DOWNLOAD_S) -> None:
        """Serve until `seconds` pass, `stop()` or Ctrl+C; then close every address."""
        threads = [threading.Thread(target=s.serve_forever, kwargs={"poll_interval": 0.2}, daemon=True)
                   for s in self._servers]
        for t in threads:
            t.start()
        try:
            self._stop.wait(seconds)
        finally:
            for s in self._servers:
                s.shutdown()
            self._close()

    def _downloaded(self) -> None:
        """A download completed: keep serving `grace` seconds for a retry, then stop."""
        self.downloaded = True
        if self._timer is not None:
            self._timer.cancel()
        self._timer = threading.Timer(self.grace, self._stop.set)
        self._timer.daemon = True
        self._timer.start()

    def stop(self) -> None:
        if self._timer is not None:
            self._timer.cancel()
        self._stop.set()

    def _close(self) -> None:
        for s in self._servers:
            s.server_close()
        self._close_file()

    def _close_file(self) -> None:
        with self._lock:
            fd, self._fd = self._fd, None
        if fd is not None:
            os.close(fd)


def _qr_png_path() -> Path:
    return adbpair.qr_png_path().with_name(QR_FILE)


def download(say: Callable[[str], None], apk: Path, *, hosts: list[str] | None = None,
             seconds: float = DOWNLOAD_S, port: int = DOWNLOAD_PORT, run=subprocess.run,
             ufw_rules: Path = UFW_RULES, qr_file: Path | None = None,
             open_qr: Callable[[Path], bool] = adbpair.open_on_desktop,
             serve: Callable[[DownloadServer, float], None] | None = None) -> None:
    hosts = addresses() if hosts is None else hosts
    if not hosts:
        raise UpdateError("This computer has no Wi-Fi/LAN or Tailscale address to serve the build on: connect it "
                          "to the phone's network, then run this again.")
    srv = DownloadServer(apk, hosts, say, port=port)
    qr_file = qr_file or _qr_png_path()
    say(f"Serving {apk.name} ({_mb(srv.size)}) for {int(seconds // 60)} minutes (Ctrl+C stops it).")
    say(LAN_ONLY)
    say("On the phone: scan this with the camera, or open the address in the browser, download, then open the file.")
    say(adbpair.render_qr(srv.urls[0]))
    for u in srv.urls:
        say(f"Address: {u}")
    if hint := firewall_hint(hosts[0], port, run, ufw_rules):
        say(hint)  # still serving: it can be fixed while this runs
    restore = _stop_on_signals()
    try:
        adbpair.save_qr_png(srv.urls[0], qr_file)
        opened = bool(open_qr(qr_file))
        say(f"QR code also saved to {qr_file}" + (" and opened on your screen." if opened else "."))
        say(PLAY_PROTECT)
        (serve or (lambda s, sec: s.serve(sec)))(srv, seconds)
    finally:
        restore()
        srv.stop()
        srv._close()
        qr_file.unlink(missing_ok=True)
    say("Downloaded. Stopped serving." if srv.downloaded else "Stopped serving the build.")


_STOP_SIGNALS = tuple(getattr(signal, n) for n in ("SIGTERM", "SIGHUP") if hasattr(signal, n))


def _stop_on_signals() -> Callable[[], None]:
    """While serving, SIGTERM and SIGHUP (the terminal closed, `kill`) stop it like Ctrl+C does, so
    the QR picture is removed and every address closed. Returns what puts the old handlers back."""
    if threading.current_thread() is not threading.main_thread():
        return lambda: None

    def stop(signum, frame):
        raise KeyboardInterrupt

    old = {}
    for sig in _STOP_SIGNALS:
        try:
            old[sig] = signal.signal(sig, stop)
        except (OSError, ValueError):
            pass

    def restore() -> None:
        for sig, handler in old.items():
            signal.signal(sig, handler)
    return restore
