"""Requests that arrive while Homa Lawn is starting never freeze it (HOMALAWN_SERIAL=emulator-5560,
the test build).

Until patch 0036 a request handler could be the first to build Launcher3's app objects off the
main thread: `launcher.ping` (InvariantDeviceProfile) took Dagger's locks for the device profile and
the theme manager and, inside ThemeProvider's constructor, waited for the main thread
(WallpaperManagerCompat is created on the main thread only); the main thread, starting the home
screen (Launcher.onCreate -> LauncherAppState -> the icon provider -> the theme manager), waited
for the theme manager's lock. Both stopped for good: "Homa Lawn Test isn't responding", every later
call hung.

Each round here stops the app, then starts the home screen while several adb provider calls and
link calls race into the starting process, and requires a ping to be answered within 30 s. A
freeze is kept as evidence: the system's ANR record (dumpsys dropbox) goes to
tests/build/startup-anr-<round>.txt. HOMALAWN_STARTUP_ROUNDS sets the number of rounds (default 15).
"""
import os
import subprocess
import threading
import time
from pathlib import Path

import pytest

pytestmark = pytest.mark.device
SERIAL = os.environ.get("HOMALAWN_SERIAL")
if not SERIAL:
    pytest.skip("HOMALAWN_SERIAL not set", allow_module_level=True)

from conftest import DEVICE_PACKAGE as PKG  # noqa: E402
from homalawn import appid  # noqa: E402
from test_device_seam import LINK, crash_log  # noqa: E402,F401  (avd_hygiene, link_transport: conftest)

ROUNDS = int(os.environ.get("HOMALAWN_STARTUP_ROUNDS") or 15)
BUILD = Path(__file__).parent / "build"
ADB_CALLS = 6   # provider calls fired at once into the starting process
LINK_CALLERS = 3


def adb(*args, timeout=60) -> subprocess.CompletedProcess:
    return subprocess.run(["adb", "-s", SERIAL, *args], capture_output=True, text=True, timeout=timeout)


def ping_answers(timeout) -> bool:
    try:
        out = adb("shell", "content", "call", "--uri", f"content://{appid.authority(PKG)}", "--method", "ping",
                  timeout=timeout).stdout
    except subprocess.TimeoutExpired:
        return False
    return "ok=true" in out


def anr_count() -> int:
    out = adb("logcat", "-d", "-b", "events").stdout
    return sum(1 for ln in out.splitlines() if "am_anr" in ln and PKG in ln)


def link_caller(ident, stop: threading.Event, answered: list):
    """Dials the link over and over while the app starts (connection refused until it listens)."""
    from homalawn.link.wire import Conn
    while not stop.is_set():
        try:
            with Conn("127.0.0.1", LINK, str(ident.cert), str(ident.key), timeout=5) as c:
                c.call("launcher.ping")
                answered.append(time.monotonic())
        except Exception:
            time.sleep(0.05)


def test_requests_during_startup_never_freeze_the_app(link_transport, d):
    from homalawn.link import identity
    ident = identity.ensure()  # the session's paired identity (link_transport set HOMALAWN_CONFIG)
    adb("forward", f"tcp:{LINK}", f"tcp:{LINK}")
    anr0 = anr_count()
    starts = []
    try:
        for r in range(ROUNDS):
            print(f"STARTUP round {r + 1}", flush=True)
            adb("shell", "am", "force-stop", PKG)
            stop = threading.Event()
            answered: list = []
            callers = [threading.Thread(target=link_caller, args=(ident, stop, answered), daemon=True)
                       for _ in range(LINK_CALLERS)]
            for t in callers:
                t.start()
            # on the phone itself: provider calls and the home screen's start, all at once
            fire = " ".join(f"content call --uri content://{appid.authority(PKG)} --method ping >/dev/null 2>&1 &"
                            for _ in range(ADB_CALLS))
            # not waited for: a frozen process never answers them
            adb("shell", f"{fire} am start -n {appid.launcher(PKG)} -a android.intent.action.MAIN "
                         f"-c android.intent.category.HOME >/dev/null 2>&1")
            t0 = time.monotonic()
            alive = ping_answers(30)
            end = time.monotonic() + 15
            while alive and not answered and time.monotonic() < end:
                time.sleep(0.2)
            stop.set()
            for t in callers:
                t.join(10)
            if not alive:
                # the evidence: a key and a touch for the stuck home screen make Android record the ANR with
                # every thread's stack (input is not handled within 5 s); a stuck service start
                # would take 200 s
                end = time.monotonic() + 240
                while anr_count() == anr0 and time.monotonic() < end:
                    adb("shell", "input", "keyevent", "KEYCODE_DPAD_DOWN")
                    adb("shell", "input", "tap", "540", "1200")
                    time.sleep(5)
                BUILD.mkdir(exist_ok=True)
                (BUILD / f"startup-anr-{r}.txt").write_text(adb("shell", "dumpsys", "dropbox", "--print",
                                                                "data_app_anr", timeout=120).stdout)
                pytest.fail(f"round {r + 1} of {ROUNDS}: Homa Lawn froze while starting (no ping answer in 30 s; "
                            f"ANR recorded: {anr_count() > anr0}; traces in tests/build/startup-anr-{r}.txt)")
            assert answered, f"round {r + 1}: the link never answered after the start"
            starts.append(round(time.monotonic() - t0, 1))
    finally:
        if not ping_answers(10):  # a frozen process: stop it so the session can go on
            adb("shell", "am", "force-stop", PKG)
        adb("shell", "input", "keyevent", "KEYCODE_HOME")
        end = time.monotonic() + 30
        while not ping_answers(10) and time.monotonic() < end:
            time.sleep(1)
    print(f"STARTUP {ROUNDS} rounds, ping answered after {starts} s")
    assert anr_count() == anr0, "an ANR was recorded during the rounds"
    assert crash_log(d) == ""
