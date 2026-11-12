"""`homalawn setup`: a phone with developer options on, to "an agent can drive it" (spec N1-N7).

Steps, each saying in one line what it did (a failure is a `SetupError` whose message says what
to do): find the phone over adb (named, else the one non-emulator device connected, else adb
pairing by QR, with the code fallback only when adb's mDNS is off; `--new` goes straight to QR
pairing whatever is connected) → install the newest build (`adb install -r`, no taps; only when the
phone has dropped off adb, over the Homa Lawn link — the person taps the update notification, then
Update; never an uninstall)
→ notifications allowed for Lawnchair (Android 13+) → default home → kept off battery optimisation
("Keep connected") → screen control (Lawnchair's accessibility
service added to the ones already on) → link pairing (home screen brought up first, the phone
takes the request there) → a proof run over the link (status, layout, screen control, a
screenshot) → a reminder that Wireless debugging is no longer needed.

Running it is the person's consent to set the default home and switch screen control on (N4); it
asks no questions on the normal path. Re-running it on a set-up phone updates or skips the
install, re-checks, skips pairing and repeats the proof.
"""
from __future__ import annotations

import hashlib
import re
import socket
import subprocess
import sys
import time
from pathlib import Path
from typing import Callable

from . import adbpair, appid, ops, update
from .adb import AdbError, AdbTransport, subprocess_runner
from .link import identity, pairing
from .link.wire import PORT, fmt_host
from .transport import TransportError

ROOT = Path(__file__).resolve().parents[2]
INSTALL_TIMEOUT_S = 180
CMD_TIMEOUT_S = 30
CONNECT_WAIT_S = 20
SCREEN_WAIT_S = 6  # the service binds within ~1 s of the setting (tests/device/NOTES.md)

QR_HOW = "On the phone: Settings › Developer options › Wireless debugging › Pair device with QR code, then scan this."
ACCEPT = "On the phone: check the four words and tap Accept."
AUTO_BLOCKER = ("Samsung Auto Blocker is on: Settings › Security and privacy › Auto Blocker, off for the install, "
                "then run setup again.")
OTHER_KEY = ("Homa Lawn on the phone is signed with another key: an install would need an uninstall, which "
             "wipes Homa Lawn's data — not done.")
SLOW_INSTALL = ("The install did not finish in 3 minutes: check the phone for a Play Protect or install prompt "
                "(tap Install anyway), else a slow Wi-Fi install, or Samsung Auto Blocker is on "
                "(Settings › Security and privacy › Auto Blocker, off for the install). Run setup again.")
NOT_ALLOWED = ("On the phone, tap Allow for this computer (USB/Wireless debugging prompt), then run setup again.")
NO_ADB_DEBUG = "Wireless debugging can be turned off; Homa Lawn does not need it."
READY = "Ready: {phone} is set up and paired."
ALMOST = "Almost ready: {phone} is set up and paired; switch on what is listed above."
SCREEN_ON = ("Screen control switched on (running setup allows it; --no-screen-control skips this step). Turn it off "
             "any time: on the phone, Homa Lawn's settings › Permissions › Screen control.")
SCREEN_LEFT_OFF = ("Screen control left off, as asked (--no-screen-control): agents can change the home screen but "
                   "not operate other apps.")
NEW_HINT = "(To set up a different phone that this computer has never seen, run `homalawn setup --new`.)"
TYPE_IN_TERMINAL = ("This computer cannot find the phone by itself (adb's network discovery is off), so connecting "
                    "it over Wireless debugging needs typing: run `homalawn setup --new` in your own terminal.")
LINK_OLD_BUILD = ("The Homa Lawn on {phone} cannot update itself yet; keeping the build it has (update it once with "
                  "`homalawn update --download`).")
SETTINGS_WAY = ("Homa Lawn's settings open from the home screen (long-press it › Home settings) or from the "
                "Homa Lawn icon in the app list.")
LINK_NOTIFY = ("Notifications for Homa Lawn cannot be checked over the link; the update notification needs them "
               "(on the phone: Homa Lawn's settings › Permissions › Notifications). " + SETTINGS_WAY)
HOME_BUTTON = ("on the phone, tap Set as home in Homa Lawn's notification, or open Homa Lawn's settings › "
               "Permissions › Home app")
LINK_HOME_LOST = f"Homa Lawn is not the home app: {HOME_BUTTON}."
LINK_SCREEN_OFF = "Screen control is off: on the phone, Homa Lawn's settings › Permissions › Screen control."
UPDATE_NOT_TAPPED = ("The update was not installed within 3 minutes: on the phone, tap the Homa Lawn update "
                     "notification, then Update, and run setup again.")
OLD_APP = ("{phone} runs the old Homa Lawn app ({old}), which is no longer supported: remove it from the phone, "
           "then run setup again to install the new app ({new}). Its home screen and settings are not carried over.")
NOTIFY_OFF = ("The phone cannot show the pairing request because notifications for Homa Lawn are off: on the phone, "
              "Homa Lawn's settings › Permissions › Notifications, allow them, then run setup again. " + SETTINGS_WAY)


class SetupError(Exception):
    """Setup stopped; the message is the one line saying what to do."""


def _adb(run, *args: str, timeout: float = CMD_TIMEOUT_S) -> subprocess.CompletedProcess:
    try:
        return run(["adb", *args], capture_output=True, text=True, timeout=timeout)
    except FileNotFoundError:
        raise SetupError("adb is not installed or not on PATH (install Android platform-tools).")


def _out(p: subprocess.CompletedProcess) -> str:
    return (p.stdout or "") + (p.stderr or "")


def _devices(run) -> dict[str, str]:
    """serial -> state, from `adb devices`."""
    found = {}
    for line in _out(_adb(run, "devices")).splitlines()[1:]:
        parts = line.split()
        if len(parts) >= 2:
            found[parts[0]] = parts[1]
    return found


# -- 1. the phone ----------------------------------------------------------------------------------
_MDNS_NAME = re.compile(r"adb-(.+)-[^-]+\._adb-tls-connect\._tcp\.?$")
_HOST_PORT = re.compile(r"(\[[0-9A-Fa-f:.]+\]|\d{1,3}(?:\.\d{1,3}){3}):(\d+)")


def serial_host(serial: str) -> str | None:
    """The address in an `ip:port` adb serial (a Wireless debugging connection), else None."""
    m = _HOST_PORT.fullmatch(serial)
    return m.group(1).strip("[]") if m else None


def _phone_id(run, serial: str) -> str:
    """Who the device behind a serial is: its ro.serialno (one phone on Wireless debugging is often
    listed twice, as `adb-<serialno>-<id>._adb-tls-connect._tcp` and as `ip:port`)."""
    try:
        p = _adb(run, "-s", serial, "shell", "getprop", "ro.serialno")
        v = (p.stdout or "").strip()
        if p.returncode == 0 and v and " " not in v:
            return v
    except subprocess.TimeoutExpired:
        pass
    m = _MDNS_NAME.match(serial)
    return m.group(1) if m else serial


def _phones(run, devs: dict[str, str]) -> dict[str, str]:
    """phone id -> the serial to use for it, over the ready non-emulator devices. An `ip:port`
    serial is preferred: it is the address that answers, and the link is tried there first."""
    groups: dict[str, list[str]] = {}
    for s, st in devs.items():
        if st == "device" and not s.startswith("emulator-"):
            groups.setdefault(_phone_id(run, s), []).append(s)
    return {pid: sorted(ss, key=lambda s: (serial_host(s) is None, s))[0] for pid, ss in groups.items()}


def find_phone(say, serial: str | None, *, run, pair_qr, ask, new: bool = False, interactive: bool = True) -> str:
    """`new`: a phone this computer has never seen — straight to QR pairing, even with other phones
    on adb (a paired phone with Wireless debugging on reconnects by itself), and only the serial
    that pairing returns is used."""
    devs = _devices(run)
    if new:
        return _pair_new(say, devs, run=run, pair_qr=pair_qr, ask=ask, interactive=interactive)
    if serial:
        state = devs.get(serial)
        if state == "unauthorized":
            raise SetupError(NOT_ALLOWED)
        if state == "offline":
            raise SetupError(f"{serial} is offline: reconnect it (plug the cable in again, or turn Wireless debugging "
                             "off and on), then run setup again.")
        if state != "device":
            raise SetupError(f"{serial} is not connected over adb (see `adb devices`); connect it, or leave out "
                             "--serial to pair a phone over Wireless debugging.")
        say(f"Phone: {serial} (connected over adb).")
        return serial
    phones = _phones(run, devs)
    if len(phones) > 1:
        raise SetupError("Several phones are connected over adb (" + ", ".join(sorted(phones.values())) + "); name "
                         "one with --serial SERIAL, or run `homalawn setup --new` for a phone never set up here.")
    if phones:
        found = next(iter(phones.values()))
        say(f"Phone: {found} (connected over adb). {NEW_HINT}")
        return found
    if any(st == "unauthorized" for s, st in devs.items() if not s.startswith("emulator-")):
        raise SetupError(NOT_ALLOWED)
    return _pair_new(say, devs, run=run, pair_qr=pair_qr, ask=ask, interactive=interactive)


def _pair_new(say, before: dict[str, str], *, run, pair_qr, ask, interactive: bool) -> str:
    """Pair by QR (or by typed code); the phone is then only ever the serial pairing returned, or
    another listing of that same phone that appeared with it — never a device connected before."""
    try:
        if adbpair.mdns_available(run=run):
            say(QR_HOW)
            found = pair_qr(say, run=run)
        elif not interactive:  # the fallback reads typed answers; nobody can type into a background run
            raise SetupError(TYPE_IN_TERMINAL)
        else:
            found = _pair_by_code(say, run=run, ask=ask)
    except adbpair.AdbPairError as e:
        raise SetupError(str(e))
    # adb's auto-connect and pair_by_qr's own `adb connect` can both land: one phone, two serials.
    # Candidates: serials that were not on adb before pairing, and of those only the paired phone's.
    pid = _phone_id(run, found)
    fresh = {s: st for s, st in _devices(run).items() if s == found or before.get(s) != "device"}
    found = _phones(run, fresh).get(pid, found)
    say(f"Phone: {found} (connected over Wireless debugging).")
    return found


def _pair_by_code(say, *, run, ask) -> str:
    """adb cannot browse the network here: the only path that asks anything."""
    say("This computer cannot find the phone by itself (adb's network discovery is off), so type what the phone "
        "shows. On the phone: Settings › Developer options › Wireless debugging › Pair device with pairing code.")
    adbpair.pair_by_code(ask("Address shown in that window (IP:port)").strip(), ask("Six-digit code").strip(), run=run)
    addr = ask("Back on the Wireless debugging screen: the address under \"IP address & Port\"").strip()
    deadline = time.monotonic() + CONNECT_WAIT_S
    while True:
        out = _out(_adb(run, "connect", addr))
        if _devices(run).get(addr) == "device":
            return addr
        if time.monotonic() >= deadline:
            raise SetupError(f"The phone accepted the code but did not connect at {addr} ({out.strip()}). Keep "
                             "Wireless debugging on and run setup again.")
        time.sleep(1)


# -- 2. install ------------------------------------------------------------------------------------
def choose_app(t: AdbTransport, apk: Path | None, apk_dir: Path | None, phone: str,
               say: Callable[[str], None] | None = None) -> Path | None:
    """The build to install, in update.pick_apk's order (`apk`; else this checkout's build for the
    app on the phone, from `apk_dir` when given; else, for the release app or a phone without Homa
    Lawn, the newest release), and point `t` at the app it installs. None when there is nothing to
    install but the phone has Homa Lawn (the reason is said); SetupError when it has none. A phone
    with only the old app is not set up next to it: the old app is no longer supported."""
    installed = t.device.installed_packages()
    if apk is None:
        on_phone = next((p for p in appid.PACKAGES if p in installed and not appid.is_old(p)), None)
        try:
            apk = update.pick_apk(None, apk_dir, package=on_phone, say=say, phone=phone)
        except update.UpdateError as e:
            if on_phone is None:
                raise SetupError(f"Homa Lawn is not on the phone and there is nothing to install: {_lower(str(e))}")
            if say is not None:
                say(str(e))
            apk = None
    pkg = (appid.apk_package(apk) if apk is not None else None) \
        or next((p for p in appid.PACKAGES if p in installed), appid.RELEASE)
    if not appid.is_old(pkg) and pkg not in installed and installed & set(appid.OLD):
        raise SetupError(OLD_APP.format(phone=phone, old=next(iter(installed & set(appid.OLD))), new=pkg))
    t.device.package = pkg
    return apk


def _lower(sentence: str) -> str:
    """A sentence to go after a colon: its first letter small unless it starts a name."""
    first = sentence.split(" ", 1)[0]
    return sentence if first in ("Homa", "GitHub", "Android") or first.isupper() else sentence[:1].lower() + sentence[1:]


def _installed_sha(t: AdbTransport) -> str | None:
    try:
        path = next((ln.split(":", 1)[1].strip() for ln in t.shell("pm", "path", t.device.package).splitlines()
                     if ln.startswith("package:") and ln.strip().endswith("/base.apk")), None)
    except AdbError:  # not installed: pm exits 1
        return None
    if path is None:
        return None
    try:
        return t.shell("sha256sum", path).split()[0]
    except (AdbError, IndexError):
        return "?"


LINK_NO_UPDATER = ("The phone is not connected over adb, and its Homa Lawn cannot update itself yet: reconnect it "
                   "over adb (or run `homalawn update --download`), then run setup again.")


def _update_over_link(say, apk: Path, link, link_for, sleep, old_build: str | None = None) -> None:
    """Offer the build over the link. Only when the phone waits for the person's tap on the update
    notification, wait (asking the phone over the link again, since Lawnchair restarts when the
    update lands) until the phone runs it. A phone build without the
    updater stops setup, unless `old_build` is given: then that line is said and setup goes on."""
    try:
        try:
            done = update.send(say, link, apk)
            if done != update.NOTIFIED:
                return  # same build, installed, or locked (installs at the next unlock): send() said so
        except update.CannotUpdate:
            if old_build is not None:
                say(old_build)
                return
            raise SetupError(LINK_NO_UPDATER)
        except update.UpdateError as e:
            raise SetupError(str(e))
    finally:
        if hasattr(link, "close"):
            link.close()
    sha = hashlib.sha256(apk.read_bytes()).hexdigest()
    say("Waiting for the update to be installed (up to 3 minutes).")
    for _ in range(INSTALL_TIMEOUT_S // 2):
        sleep(2)
        again = link_for()
        if again is None:
            continue
        try:
            if str(update.app_info(again).get("sha256", "")).lower() == sha:
                say(f"Homa Lawn updated over the link from {apk.name}.")
                return
        except (update.UpdateError, TransportError):
            pass
        finally:
            if hasattr(again, "close"):
                again.close()
    raise SetupError(UPDATE_NOT_TAPPED)


def install(say, t: AdbTransport, apk: Path | None, *, run, link_for=None,
            sleep: Callable[[float], None] = time.sleep) -> None:
    """Over adb (`adb install -r`, no taps) whenever the phone is connected over adb. Only when it
    is not, and `link_for()` gives a link to it, the build is offered over the Homa Lawn link (the
    person taps the notification, then Update)."""
    if apk is not None and link_for is not None and _devices(run).get(t.serial) != "device":
        link = link_for()
        if link is not None:
            say(f"{t.serial} is not connected over adb; updating over the Homa Lawn link.")
            _update_over_link(say, apk, link, link_for, sleep)
            return
    installed = _installed_sha(t)
    if apk is None:
        if installed is None:
            raise SetupError("Homa Lawn is not on the phone and there is nothing to install: pass --apk FILE.")
        say("Keeping the Homa Lawn the phone has.")
        return
    # the debug build keeps one versionCode across builds, so "same build" is the same file
    if installed == hashlib.sha256(apk.read_bytes()).hexdigest():
        say(f"Homa Lawn on the phone is already this build ({apk.name}); not reinstalled.")
        return
    try:
        p = _adb(run, "-s", t.serial, "install", "-r", str(apk), timeout=INSTALL_TIMEOUT_S)
    except subprocess.TimeoutExpired:  # Samsung's Auto Blocker leaves "Performing Streamed Install" hanging
        raise SetupError(SLOW_INSTALL)
    out = _out(p)
    if "INSTALL_FAILED_UPDATE_INCOMPATIBLE" in out:
        raise SetupError(OTHER_KEY)
    if "INSTALL_FAILED_USER_RESTRICTED" in out or "auto blocker" in out.lower():
        raise SetupError(AUTO_BLOCKER)
    if "Success" not in out:
        last = out.strip().splitlines()[-1] if out.strip() else "no answer"
        raise SetupError(f"Installing Homa Lawn failed ({last}).")
    say(f"Homa Lawn {'updated' if installed else 'installed'} over adb from {apk.name}.")


def _pair_qr_on_screen(say, *, run) -> str:
    """QR pairing with the QR also saved as a picture and opened on the desktop, so it reaches the
    person when an agent runs setup in the background."""
    return adbpair.pair_by_qr(say, run=run, qr_file=adbpair.qr_png_path(), open_qr=adbpair.open_on_desktop)


# -- 2b. notifications -----------------------------------------------------------------------------
NOTIFY_PERMISSION = "android.permission.POST_NOTIFICATIONS"


def _sdk(t: AdbTransport) -> int:
    try:
        return int(t.shell("getprop", "ro.build.version.sdk").strip())
    except (AdbError, ValueError):
        return 0


def notifications(say, t: AdbTransport) -> None:
    """Allow Lawnchair's notifications up front (Android 13+ asks at the first home screen, and that
    dialog would pause the launcher and hold up link pairing). Part of the same consent as N4.
    Before Android 13 there is no such permission: nothing to do, nothing said."""
    if _sdk(t) < 33:
        return
    try:
        t.shell("pm", "grant", t.device.package, NOTIFY_PERMISSION)
    except AdbError:
        say("Could not allow notifications for Homa Lawn; if the phone asks, tap Allow.")
        return
    say("Notifications allowed for Homa Lawn (its pairing request needs them).")


# -- 3. default home -------------------------------------------------------------------------------
def _default_home(t: AdbTransport) -> bool:
    try:
        return bool(t.call("launcher.ping").get("default_home"))
    except TransportError as e:
        raise SetupError(f"Homa Lawn does not answer on the phone ({e}).")


def default_home(say, t: AdbTransport) -> None:
    if _default_home(t):
        say("Homa Lawn is already the home app.")
        return
    try:
        t.set_home()
    except AdbError as e:
        raise SetupError(f"Could not make Homa Lawn the home app ({e}): {HOME_BUTTON}, then run setup again.")
    if not _default_home(t):
        raise SetupError(f"Homa Lawn did not become the home app: {HOME_BUTTON}, then run setup again.")
    say("Homa Lawn is now the home app.")


# -- 3b. keep connected ----------------------------------------------------------------------------
KEEP_OFF = ("Could not let Homa Lawn run in the background; on the phone, tap Run in background in Homa Lawn's "
            "notification or open Homa Lawn's settings › Permissions › Run in background, else the link stops answering while another app is in front or the phone sleeps.")


def keep_connected(say, t: AdbTransport) -> None:
    """Take Homa Lawn off battery optimisation (what "Keep connected" on the phone does). The link
    runs without a foreground service, so this is what gives it network behind other apps and in
    Doze on Android 15+. Part of the same consent as N4."""
    try:
        out = t.shell("dumpsys", "deviceidle", "whitelist", f"+{t.device.package}")
    except AdbError:
        out = ""
    if "Added" not in out and "Already" not in out:
        say(KEEP_OFF)
        return
    say("Homa Lawn may run in the background (the link answers while the phone sleeps).")


# -- 4. screen control -----------------------------------------------------------------------------
def _full(component: str) -> str:
    pkg, _, cls = component.partition("/")
    return f"{pkg}/{pkg}{cls}" if cls.startswith(".") else component


def _setting(t: AdbTransport, key: str) -> str:
    v = t.shell("settings", "get", "secure", key).strip()
    return "" if v == "null" else v


def screen_control(say, t: AdbTransport, switch: bool = True) -> bool:
    """Switch Homa Lawn's screen control on (unless `switch` is False: then only say how it is).
    True when it is on afterwards."""
    service = appid.a11y_service(t.device.package)
    services = [s for s in _setting(t, "enabled_accessibility_services").split(":") if s]
    on = _setting(t, "accessibility_enabled") == "1"
    has = any(_full(s) == service for s in services)
    if has and on:
        say("Screen control is already on.")
        return True
    if not switch:
        say(SCREEN_LEFT_OFF)
        return False
    if not has:
        t.shell("settings", "put", "secure", "enabled_accessibility_services", ":".join(services + [service]))
    if not on:
        t.shell("settings", "put", "secure", "accessibility_enabled", "1")
    say(SCREEN_ON)
    return True


# -- 5. link pairing -------------------------------------------------------------------------------
NO_WIFI = "The phone has no Wi-Fi address: connect it to the same Wi-Fi as this computer, then run setup again."


def wifi_address(t: AdbTransport) -> str | None:
    try:
        out = t.shell("ip", "-f", "inet", "addr", "show", "wlan0")
    except AdbError:
        out = ""
    m = re.search(r"\binet (\d+\.\d+\.\d+\.\d+)/", out)
    return m.group(1) if m else None


def link_hosts(serial: str, t: AdbTransport) -> list[str]:
    """Where to reach the phone's link: the host of an `ip:port` adb serial first (this computer
    already reaches the phone there), then the phone's Wi-Fi address."""
    hosts = [h for h in (serial_host(serial), wifi_address(t)) if h]
    if not hosts:
        raise SetupError(NO_WIFI)
    return list(dict.fromkeys(hosts))


def _reachable(host: str, port: int) -> bool:
    try:
        socket.create_connection((host, port), timeout=1.5).close()
        return True
    except OSError:
        return False


def _peer_at(peers, hosts: list[str]):
    """The one paired phone whose known addresses include one of `hosts`, else None."""
    from .link.client import LinkTransport
    from .link.wire import parse_host
    hit = [p for p in peers if {parse_host(h)[0] for h in LinkTransport(p).candidates()} & set(hosts)]
    return hit[0] if len(hit) == 1 else None


def _connect_link(peers, addr: str):
    """A paired phone that answers at exactly `addr` over the link, or None."""
    if not peers:
        return None
    t, _ = ops._link_to(peers, only_hosts=[addr])
    return t


def link_pair(say, t: AdbTransport, hosts: list[str], port: int, *, pair, connect_link, reachable, sleep):
    peers = identity.load_peers()
    for h in hosts:
        link = connect_link(peers, fmt_host(h, port))
        if link is not None:
            say(f"Already paired with {link.label} over the link.")
            return link
    t.shell("input", "keyevent", "KEYCODE_WAKEUP")
    t.shell("input", "keyevent", "KEYCODE_HOME")  # the phone takes a pair request while its home screen shows
    host = None
    for attempt in range(3):  # Lawnchair may still be starting after an install
        host = next((h for h in hosts if reachable(h, port)), None)
        if host is not None:
            break
        sleep(1)
    if host is None:
        raise SetupError("The phone's Homa Lawn link does not answer at " + " or ".join(fmt_host(h, port) for h in hosts)
                         + ": keep the phone on the same network as this computer with Homa Lawn running, then run "
                           "setup again.")
    addr = fmt_host(host, port)

    def show_words(words):
        say("Words: " + " ".join(words))
        say(ACCEPT)

    def quiet(msg: str) -> None:  # the pairing's own Accept line would say ACCEPT a second time
        if not msg.startswith(("Confirm on the phone", "Pairing with the phone at")):
            say(msg)

    try:
        peer = pair(host, port, confirm=lambda words: True, say=quiet, show_words=show_words)
    except pairing.PairError as e:
        msg = str(e)
        if msg == pairing._PHONE_SAYS["notifications_off"]:
            raise SetupError(NOTIFY_OFF)
        raise SetupError(msg)
    except identity.IdentityError as e:
        raise SetupError(str(e))
    except (OSError, ConnectionError, RuntimeError) as e:
        raise SetupError(f"Pairing failed: {e}")
    say(f"Paired with {peer.name}.")
    link = connect_link([peer], addr)
    if link is None:
        raise SetupError(f"Paired, but {peer.name} does not answer over the link at {addr}; run setup again.")
    return link


# -- 6. proof run ----------------------------------------------------------------------------------
def proof(say, link, pictures: Path, *, sleep, screen_on: bool = True) -> None:
    """Check the phone over the link and say what was found. `screen_on=False` (screen control
    off, already said): no wait, no screenshot. The caller says the last line (READY or ALMOST)."""
    s = ops.Session()
    s._t = link
    if not screen_on:
        try:
            st = ops.status(s).data
            lay = ops.layout_get(s).data
        except ops.OpError as e:
            raise SetupError(f"The check over the link failed: {e}")
        items = sum(len(sc.get("items", [])) for sc in lay.get("screens", [])) + len(lay.get("hotseat", []))
        grid = "x".join(map(str, st["grid"])) if st.get("grid") else "unknown"
        say(f"Checked over the link: Homa Lawn {st.get('version')}, grid {grid}, {items} items on the home screen, "
            "screen control off.")
        return
    try:
        st = ops.status(s).data
        lay = ops.layout_get(s).data
        enabled = False
        deadline = time.monotonic() + SCREEN_WAIT_S
        while True:
            enabled = bool(ops._screen(s, "screen.status").get("enabled"))
            if enabled or time.monotonic() >= deadline:
                break
            sleep(0.5)
        if not enabled:
            raise SetupError("Screen control did not come on: on the phone, Homa Lawn's settings › Permissions › Screen "
                             "control, then run setup again. If Android says Restricted setting: Settings › Apps › "
                             "Homa Lawn › ⋮ › Allow restricted settings.")
        shot = ops.screenshot(s).data
    except ops.OpError as e:
        raise SetupError(f"The check over the link failed: {e}")
    items = sum(len(sc.get("items", [])) for sc in lay.get("screens", [])) + len(lay.get("hotseat", []))
    grid = "x".join(map(str, st["grid"])) if st.get("grid") else "unknown"
    say(f"Checked over the link: Homa Lawn {st.get('version')}, grid {grid}, {items} items on the home screen, "
        "screen control on.")
    pictures.mkdir(parents=True, exist_ok=True)
    out = pictures / f"homalawn-setup-{time.strftime('%Y%m%d-%H%M%S')}.png"
    out.write_bytes(shot)
    say(f"Screenshot: {out}")


# -- the phone over the link only (not on adb) -----------------------------------------------------
def _link_to_peer(peer):
    """That paired phone over the link at the addresses it is known by (as every command dials it),
    or None when it does not answer."""
    try:
        t, _ = ops._link_to([peer])
    except ops.OpError as e:  # another device answers at its address
        raise SetupError(str(e))
    return t


def _pick_peer(name: str | None, *, forced: bool):
    """The paired phone setup works on over the link. Forced (`--via link`): it must be clear
    which; otherwise None when nothing or several (unnamed) are paired."""
    try:
        if name or forced:
            return ops._peer_named(name)
    except ops.OpError as e:
        if forced:
            raise SetupError(f"{e}." if not str(e).endswith(".") else str(e))
        return None
    peers = identity.load_peers()
    return peers[0] if len(peers) == 1 else None


def _link_sequence(say, peer, link, apk, apk_dir, link_to_peer, pictures, sleep, *, forced: bool = False,
                   switch_screen: bool = True) -> None:
    """A set-up phone reached over the link only: update over the link, then check each step over
    the link and name what the person switches on, instead of stopping; then the proof run."""
    from .transport import ProviderError, TransportError
    # forced (`--via link`): adb was not asked, so whether the phone is on it is not known
    say(f"Phone: {peer.name} (over the Homa Lawn link, as asked)." if forced
        else f"Phone: {peer.name} (over the Homa Lawn link; not connected over adb).")
    try:
        on_phone = link.call("launcher.ping").get("package")
        if appid.is_old(on_phone) and (apk is None or appid.apk_package(apk) != on_phone):
            raise SetupError(OLD_APP.format(phone=peer.name, old=on_phone, new=appid.RELEASE))
        if apk is None:
            try:
                apk = update.pick_apk(None, apk_dir, package=on_phone, say=say, phone=peer.name)
            except update.UpdateError as e:
                say(str(e))
        if apk is None:
            say("Keeping the Homa Lawn the phone has.")
        else:
            _update_over_link(say, apk, link, lambda: link_to_peer(peer), sleep,
                              old_build=LINK_OLD_BUILD.format(phone=peer.name))
            link = link_to_peer(peer)  # the offer's connection is closed; Lawnchair may have restarted
            if link is None:
                raise SetupError(f"{peer.name} stopped answering over the link after the update; run setup again.")
        missing = False
        say(LINK_NOTIFY)
        try:
            home = bool(link.call("launcher.ping").get("default_home"))
            try:
                screen = bool(link.call("screen.status").get("enabled"))
            except ProviderError as e:
                if e.code != "unknown_method":
                    raise
                screen = False  # a build without screen control
        except TransportError as e:
            raise SetupError(f"The check over the link failed: {e}")
        if home:
            say("Homa Lawn is already the home app.")
        else:
            say(LINK_HOME_LOST)
            missing = True
        if screen:
            say("Screen control is already on.")
        elif not switch_screen:
            say(SCREEN_LEFT_OFF)
        else:
            say(LINK_SCREEN_OFF)
            missing = True
        proof(say, link, pictures, sleep=sleep, screen_on=screen)
    finally:
        if link is not None and hasattr(link, "close"):
            link.close()
    say((ALMOST if missing else READY).format(phone=peer.name))


# -- the whole sequence ----------------------------------------------------------------------------
def run(say: Callable[[str], None], *, serial: str | None = None, apk: Path | None = None,
        apk_dir: Path | None = None, ask: Callable[[str], str] | None = None, debug_forward: bool = False,
        new: bool = False, interactive: bool | None = None,
        run=subprocess.run, runner=subprocess_runner, pair=pairing.pair, pair_qr=_pair_qr_on_screen,
        connect_link=_connect_link, link_port: int = PORT, pictures: Path | None = None,
        sleep: Callable[[float], None] = time.sleep, reachable: Callable[[str, int], bool] = _reachable,
        via: str | None = None, phone_name: str | None = None, link_to_peer=_link_to_peer,
        screen_control: bool = True) -> None:
    """`debug_forward` (hidden, for the emulator): reach the link through `adb forward` on this
    computer's loopback, with the debug build's local-pairing hook on while pairing.
    `new`: pair a never-seen phone by QR even when others are on adb. `interactive` (default: stdin
    is a terminal): whether the typed fallback may ask questions. `via="link"`: a set-up phone over
    the Homa Lawn link only (`phone_name` picks it when several are paired); without `via`, that path
    is also taken when no phone is on adb and the paired phone answers over the link.
    `screen_control=False` (--no-screen-control): screen control is left as it is."""
    if interactive is None:
        interactive = sys.stdin is not None and sys.stdin.isatty()
    if ask is None:
        ask = lambda q: input(q + ": ")
    pictures = pictures or Path.home() / "Pictures"
    try:
        if apk is not None and not apk.is_file():
            raise SetupError(f"{apk} is not a file.")
        if via == "link":
            if new or serial or debug_forward:
                raise SetupError("--via link sets up a phone already paired over the Homa Lawn link; leave out --new "
                                 "and --serial with it.")
            peer = _pick_peer(phone_name, forced=True)
            link = link_to_peer(peer)
            if link is None:
                raise SetupError(f"{peer.name} does not answer over the Homa Lawn link: keep it on the same network as "
                                 "this computer with Homa Lawn running (or connect it over adb), then run setup again.")
            _link_sequence(say, peer, link, apk, apk_dir, link_to_peer, pictures, sleep, forced=True,
                           switch_screen=screen_control)
            return
        if via is None and not (new or serial or debug_forward) and identity.load_peers() \
                and not _phones(run, _devices(run)):
            peer = _pick_peer(phone_name, forced=False)
            link = link_to_peer(peer) if peer is not None else None
            if link is not None:
                _link_sequence(say, peer, link, apk, apk_dir, link_to_peer, pictures, sleep,
                               switch_screen=screen_control)
                return
        _sequence(say, serial, apk, apk_dir, ask, debug_forward, run, runner, pair, pair_qr, connect_link,
                  link_port, pictures, sleep, reachable, new, interactive, screen_control)
    except AdbError as e:
        raise SetupError(f"adb failed: {e}. Check the phone is still connected (adb devices), then run setup again.")


def _sequence(say, serial, apk, apk_dir, ask, debug_forward, run, runner, pair, pair_qr, connect_link, link_port,
              pictures, sleep, reachable, new, interactive, switch_screen=True) -> None:
    if new and serial:
        raise SetupError("--new connects a phone that is not on adb yet; leave out --serial with it.")
    phone = find_phone(say, serial, run=run, pair_qr=pair_qr, ask=ask, new=new, interactive=interactive)
    t = AdbTransport(phone, runner)

    def link_for():
        """THIS phone over the link: the one paired phone known at this phone's address (the
        host of its adb serial or its Wi-Fi address), dialled there and accepted only with that
        phone's certificate. None when that is not certain (not on the emulator's forwarded
        loopback either, which is set up later)."""
        if debug_forward:
            return None
        try:
            hosts = link_hosts(phone, t)
        except SetupError:
            return None
        peer = _peer_at(identity.load_peers(), hosts)
        if peer is None:
            return None
        return next((ln for ln in (connect_link([peer], fmt_host(h, link_port)) for h in hosts) if ln is not None),
                    None)

    install(say, t, choose_app(t, apk, apk_dir, phone, say=say), run=run, link_for=link_for, sleep=sleep)
    notifications(say, t)
    default_home(say, t)
    keep_connected(say, t)
    screen_on = screen_control(say, t, switch=switch_screen)
    link = None
    added_forward = False
    try:
        if debug_forward:
            spec = f"tcp:{link_port}"
            had = any(ln.split()[:3] == [phone, spec, spec]
                      for ln in _out(_adb(run, "-s", phone, "forward", "--list")).splitlines())
            if not had:
                _adb(run, "-s", phone, "forward", spec, spec)
                added_forward = True
            t.device.call("debug_allow_local_pairing", "on")
            try:
                link = link_pair(say, t, ["127.0.0.1"], link_port, pair=pair, connect_link=connect_link,
                                 reachable=lambda h, p: True, sleep=sleep)
            finally:
                t.device.call("debug_allow_local_pairing", "off")
        else:
            link = link_pair(say, t, link_hosts(phone, t), link_port, pair=pair, connect_link=connect_link,
                             reachable=reachable, sleep=sleep)
        proof(say, link, pictures, sleep=sleep, screen_on=screen_on)
        name = link.label
    finally:
        if link is not None and hasattr(link, "close"):
            link.close()
        if added_forward:  # a forward that was there before setup stays
            _adb(run, "-s", phone, "forward", "--remove", f"tcp:{link_port}")
    say(NO_ADB_DEBUG)
    say(READY.format(phone=name))
