"""Durability of the link on the test AVD (HOMALAWN_SERIAL=emulator-5560): reboot, crash, a
double crash, Doze, a long time behind another app, a kill behind another app, an app update, and
an idle connection held open past the phone's 60 s idle timeout.

The link has no foreground service and posts nothing that stays in the shade (2026-10-17). What
keeps it reachable is Homa Lawn being the home app plus "Keep connected" (off battery
optimisation), which `homalawn setup` grants and the phone asks for. The module grants it for its
run (and puts the AVD's previous state back); one case proves that without it Android 15 cuts the
link behind another app and the phone asks for it.

One desktop identity is paired the real way for the whole module (CLI `pair` + tapping Accept in
the shade) and unpaired at the end. Every case ends with `link.ping` answered over the link within
30 s and an empty crash buffer (crashes a case causes on purpose are cleared by that case).

The "behind another app" and Doze cases dial through the emulator's NAT redirect (127.0.0.1:47101
-> the phone's real interface), where Android's per-app network firewall applies; the others dial
through `adb forward` (loopback). Every case prints a `DURABILITY ...` line with its timings.

The module takes about 15 minutes: run it on its own, e.g.
  HOMALAWN_SERIAL=emulator-5560 uv run pytest tests/test_device_durability.py -s
"""
import glob
import os
import re
import subprocess
import time
from pathlib import Path

import pytest

pytestmark = [pytest.mark.device, pytest.mark.slow]
SERIAL = os.environ.get("HOMALAWN_SERIAL")
if not SERIAL:
    pytest.skip("HOMALAWN_SERIAL not set", allow_module_level=True)

from conftest import DEVICE_PACKAGE as PKG
from homalawn import appid
from homalawn.adb import AdbTransport

LAUNCHER = appid.launcher(PKG)
from test_device_seam import _start_pair, _tap_pair_action, pairing_gate  # the existing pairing helpers

LINK = 47100
NAT = 47101  # `emu redir`: through the emulator NAT onto the phone's interface (firewall applies)
ROOT = Path(__file__).parent.parent
PING_S = 30
QUIET_S = 70  # Android's MIN_CRASH_INTERVAL is 60 s: each crash case starts from a clean crash history
_last_crash = [0.0]


def adb(*args, check=True, timeout=120):
    r = subprocess.run(["adb", "-s", SERIAL, *args], capture_output=True, text=True, timeout=timeout)
    if check and r.returncode:
        raise RuntimeError(f"adb {' '.join(args)}: {r.stderr or r.stdout}")
    return r.stdout


def sh(*args, **kw):
    return adb("shell", *args, **kw)


def crash_log():
    return sh("logcat", "-b", "crash", "-d").strip()


def pid():
    return sh("pidof", PKG, check=False).strip()


def note(msg):
    print(f"\nDURABILITY {msg}", flush=True)


def wait_for(cond, timeout, step=0.5):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        try:
            if cond():
                return True
        except Exception:
            pass
        time.sleep(step)
    return False


def forward():
    adb("forward", f"tcp:{LINK}", f"tcp:{LINK}")


def ping_over_link(peer, port=LINK, timeout=PING_S):
    """`link.ping` on a fresh connection, retried until `timeout`; returns seconds it took."""
    from homalawn.link.client import LinkTransport
    t0 = time.monotonic()
    last = None
    while time.monotonic() - t0 < timeout:
        t = LinkTransport(peer, [f"127.0.0.1:{port}"], only_hosts=True, discover=None, keepalive_s=None)
        try:
            r = t.call("link.ping")
            assert abs(r["t"] / 1000 - time.time()) < 120, r
            return time.monotonic() - t0
        except Exception as e:  # noqa: BLE001 - down while restarting: try again
            last = e
            time.sleep(1)
        finally:
            t.close()
    raise AssertionError(f"link.ping not answered over 127.0.0.1:{port} within {timeout}s: {last}")


def finish(link, name, port=LINK):
    took = ping_over_link(link["peer"], port)
    assert crash_log() == "", f"crash logged during {name}"
    note(f"{name}: final link.ping answered in {took:.1f}s")
    return took


def go_home():
    sh("am", "start", "-n", LAUNCHER, "-a", "android.intent.action.MAIN", "-c", "android.intent.category.HOME")


def lawnchair_in_front():
    return f"{PKG}/" in sh("dumpsys", "activity", "activities").split("topResumedActivity=", 1)[-1][:200]


def ams_log(since):
    """ActivityManager lines about our process/service since `since` (logcat -T time)."""
    out = adb("logcat", "-d", "-T", since, "-s", "ActivityManager:*", check=False)
    return [ln for ln in out.splitlines()
            if PKG in ln and re.search(r"Start proc|has died|restart|FGS|Killing|crash", ln)]


def phone_time():
    return sh("date '+%m-%d %H:%M:%S.000'").strip()


def service_running():
    """The link service is among the running services (a "* ServiceRecord{…}" entry). Not any
    ServiceRecord line: after an ANR, dumpsys keeps printing the stopped service under "Last ANR
    service:" until the phone reboots, which made a stopped service look running."""
    out = sh("dumpsys", "activity", "services", f"{PKG}/app.lawnchair.omalawn.link.LinkService")
    return re.search(r"^\s*\* ServiceRecord\{", out, re.M) is not None


def keep_connected(on):
    sh("dumpsys", "deviceidle", "whitelist", ("+" if on else "-") + PKG)


def exempt():
    return PKG in sh("dumpsys", "deviceidle", "whitelist")


def ongoing_notifications():
    """Homa Lawn's notifications that would stay in the shade, as `dumpsys notification` lists them."""
    dump = sh("dumpsys", "notification", "--noredact")
    recs = re.findall(r"NotificationRecord\([^)]*pkg=" + re.escape(PKG) + r" user=\S+ (id=\d+) .*?flags=([A-Z_|]*)", dump)
    return [r for r in recs if re.search(r"ONGOING_EVENT|FOREGROUND_SERVICE|NO_CLEAR", r[1])]


def net_state():
    """(process state, what blocks the app's network) as Android's network policy says, e.g.
    ("HOME", "APP_BACKGROUND"); "NONE" when nothing blocks it."""
    m = re.search(r"UID=" + uid() + r" state=\{procState=([A-Z_]+)\s*,[^}]*\} blocked_state=\{[^}]*effective=([A-Z_|]+)\}",
                  sh("dumpsys", "netpolicy"))
    return m.groups() if m else None


def uid():
    return re.search(r"uid:(\d+)", sh("pm", "list", "packages", "-U", PKG)).group(1)


@pytest.fixture(scope="module")
def link(tmp_path_factory):
    from homalawn.link import identity, pairing
    forward()
    adb("emu", "redir", "add", f"tcp:{NAT}:{LINK}", check=False)
    d = AdbTransport(SERIAL).device
    _last_crash[0] = time.monotonic()  # crashes just before this module may still count on the phone
    assert d.call("ping")["default_home"], "AVD must start with Lawnchair as the default home"
    assert crash_log() == "", "crash buffer not empty before the durability tests"
    old = os.environ.get("HOMALAWN_CONFIG")
    os.environ["HOMALAWN_CONFIG"] = str(tmp_path_factory.mktemp("dur-cfg") / "config.toml")
    peer = None
    was_exempt = exempt()
    keep_connected(True)
    try:
        d.call("debug_pair_reset")
        pairing_gate(d, True)
        p, _, lines = _start_pair()
        _tap_pair_action(d, "Accept")
        out = p.communicate(timeout=30)[0]
        assert p.returncode == 0, "".join(lines) + out
        peer, = identity.load_peers()
        yield {"peer": peer, "d": d}
    finally:
        forward()
        if peer is not None and not pairing.unpair(peer):
            d.call("debug_unpin", identity.ensure().fingerprint)
        pairing_gate(d, False)
        d.call("debug_pair_reset")
        sh("cmd", "statusbar", "collapse")
        keep_connected(was_exempt)
        adb("emu", "redir", "del", f"tcp:{NAT}", check=False)
        if old is None:
            os.environ.pop("HOMALAWN_CONFIG", None)
        else:
            os.environ["HOMALAWN_CONFIG"] = old
    assert d.call("debug_unpin", "0" * 64)["pins"] == 0, "computers left pinned on the AVD"
    assert d.call("ping")["default_home"] is True
    assert crash_log() == ""


def test_idle_connection_survives_90s_on_keepalive(link):
    """Task 6's keep-alive, on the real phone: one LinkTransport sits idle for 90 s, then a call goes
    over the SAME connection. Control: a connection without keep-alive is hung up by the phone."""
    from homalawn.link.client import LinkTransport
    peer = link["peer"]
    kept = LinkTransport(peer, [f"127.0.0.1:{LINK}"], only_hosts=True, discover=None)
    bare = LinkTransport(peer, [f"127.0.0.1:{LINK}"], only_hosts=True, discover=None, keepalive_s=None)
    try:
        kept.connect()
        bare.connect()
        conn = kept._conn
        t0 = time.monotonic()
        time.sleep(90)
        assert bare._conn.readable_eof(), "the phone kept an idle connection without keep-alive open for 90 s"
        assert not conn.readable_eof()
        r = kept.call("link.ping")
        assert kept._conn is conn, "the call needed a new connection"
        assert abs(r["t"] / 1000 - time.time()) < 120
        note(f"keepalive: idle {time.monotonic() - t0:.0f}s, same connection answered; "
             "control connection without keep-alive was hung up by the phone")
    finally:
        kept.close()
        bare.close()
    finish(link, "keepalive")


def test_behind_settings_for_5_minutes(link):
    """Settings in front for 5 minutes, link.ping every minute through the firewall path."""
    sh("am", "start", "-W", "-a", "android.settings.SETTINGS")
    assert wait_for(lambda: not lawnchair_in_front(), 10)
    took = []
    try:
        start = time.monotonic()
        for i in range(6):  # t = 0, 1, ..., 5 min
            while time.monotonic() < start + 60 * i:
                time.sleep(1)
            took.append(ping_over_link(link["peer"], NAT))
            assert not lawnchair_in_front()
        state = re.search(r"curProcState=(\d+)", sh("dumpsys", "activity", "processes", PKG))
        note(f"settings 5 min: pings at 0..5 min answered in {[round(t, 2) for t in took]} s; "
             f"Lawnchair curProcState={state and state.group(1)}")
    finally:
        go_home()
    finish(link, "settings 5 min", NAT)


def test_doze_screen_off(link):
    """Screen off, forced Doze: the phone still answers through the firewall path."""
    sh("input", "keyevent", "KEYCODE_SLEEP")
    try:
        assert wait_for(lambda: "mWakefulness=Asleep" in sh("dumpsys", "power"), 10)
        sh("dumpsys", "deviceidle", "force-idle")
        deep = sh("dumpsys", "deviceidle", "get", "deep").strip()
        assert deep == "IDLE", deep
        took = [ping_over_link(link["peer"], NAT) for _ in range(3)]
        assert sh("dumpsys", "deviceidle", "get", "deep").strip() == "IDLE"
        note(f"doze: deep={deep}, 3 pings answered in {[round(t, 2) for t in took]} s")
    finally:
        sh("dumpsys", "deviceidle", "unforce")
        sh("input", "keyevent", "KEYCODE_WAKEUP")
        sh("wm", "dismiss-keyguard", check=False)
        go_home()
    finish(link, "doze", NAT)


def _restore_home():
    d = AdbTransport(SERIAL).device
    if not wait_for(lambda: d.call("ping")["default_home"], 3):
        d.set_home()
    if not lawnchair_in_front():
        go_home()
    wait_for(lambda: d.call("ping")["model_loaded"], 30)


def _crash(quiet=True):
    if quiet:
        while time.monotonic() < _last_crash[0] + QUIET_S:
            time.sleep(1)
    sh("am", "crash", PKG)
    _last_crash[0] = time.monotonic()


def _quiet_since_last_crash(seconds):
    """Wait until Android logged no crash of Homa Lawn (am_crash, any cause) for `seconds`."""
    while True:
        ours = [ln for ln in adb("logcat", "-d", "-b", "events", check=False).splitlines()
                if "am_crash" in ln and PKG in ln]
        if not ours:
            return
        now = time.mktime(time.strptime(time.strftime("%Y-") + phone_time().strip(), "%Y-%m-%d %H:%M:%S.000"))
        last = time.mktime(time.strptime(time.strftime("%Y-") + ours[-1][:18], "%Y-%m-%d %H:%M:%S.%f"))
        if now - last >= seconds:
            return
        time.sleep(min(10, seconds - (now - last) + 1))


def _reset_restart_budget():
    """Lawnchair arms at most 3 crash-restart alarms per 10 min; earlier cases (and earlier runs)
    must not use up this case's budget (debug-only provider hook, shell uid)."""
    AdbTransport(SERIAL).device.call("debug_crash_budget_reset")


def _crash_and_wait_restart(old):
    _crash()
    assert wait_for(lambda: pid() not in ("", old), 30), "Lawnchair did not come back after the crash"
    return pid()


def _export_over_link(peer, timeout):
    """launcher.layout_export over the link, retried (down or busy while it recovers) until timeout.
    Returns (seconds, data, last error before it worked)."""
    from homalawn.link.client import LinkTransport
    t0 = time.monotonic()
    err = None
    while time.monotonic() - t0 < timeout:
        t = LinkTransport(peer, [f"127.0.0.1:{LINK}"], only_hosts=True, discover=None, keepalive_s=None)
        try:
            _, data = t.export_layout()
            return time.monotonic() - t0, data, err
        except Exception as e:  # noqa: BLE001
            err = e
            time.sleep(2)
        finally:
            t.close()
    raise AssertionError(f"layout export over the link did not recover within {timeout}s: {err}")


def _double_crash():
    """Crash, wait for the restart, crash again a few seconds later. Returns (pids, monotonic time
    of the 2nd crash)."""
    _reset_restart_budget()
    p0 = pid()
    p1 = _crash_and_wait_restart(p0)
    time.sleep(2)
    t0 = time.monotonic()
    _crash(quiet=False)
    time.sleep(4)
    return (p0, p1), t0


def test_double_crash_behind_another_app_link_back_by_itself(link):
    """Two quick crashes while Settings is in front: Android postpones the service restart by 30 min
    (crash within 60 s of the last restart) and no home activity restarts the app. Lawnchair's own
    alarm brings the link back."""
    sh("am", "start", "-W", "-a", "android.settings.SETTINGS")
    assert wait_for(lambda: not lawnchair_in_front(), 10)
    exempt = PKG in sh("dumpsys", "deviceidle", "whitelist")
    since = phone_time()
    try:
        (p0, p1), t0 = _double_crash()
        _, data, err = _export_over_link(link["peer"], 90)
        p2 = pid()
        assert data.startswith(b"SQLite format 3\x00")
        assert p2 not in ("", p1)
        svc = sh("dumpsys", "activity", "services", f"{PKG}/app.lawnchair.omalawn.link.LinkService")
        crashes = re.search(r"crashCount=\d+", svc)
        lines = [ln for ln in adb("logcat", "-d", "-T", since, check=False).splitlines()
                 if re.search(r"OmalawnCrashExit|link start from|Scheduling restart of crashed service.*LinkService"
                              r"|Start proc .*app\.lawnchair\.debug|crash dialog|crashed too many", ln)]
        assert any("link start from app.lawnchair.omalawn.link.RESTART" in ln for ln in lines), lines
        note(f"double crash behind Settings (battery exemption: {exempt}): pids {p0} -> {p1} -> {p2}; "
             f"layout export over the link ok {time.monotonic() - t0:.1f}s after the 2nd crash "
             f"(last error before: {err}); {crashes and crashes.group(0)}; log: {lines}")
    finally:
        sh("logcat", "-b", "crash", "-c")  # our own deliberate crashes
        _restore_home()
    finish(link, "double crash behind Settings")


def test_double_crash_layout_export_recovers_by_itself(link):
    """Two crashes in quick succession with Lawnchair in front: Android shows "keeps stopping" and
    its crash call blocks the crashed main thread until the dialog goes (5 min). Before the fix the
    process lingered half-alive: ping answered, every layout call `busy`, until a force-stop. Now the
    process ends itself, comes back, and a layout export over the link works with no action on the
    phone (no adb repair until the export has worked).

    Whether Android shows the dialog (rather than killing at once) depends on its own crash history
    (AppErrors: repeated vs. "crashed quickly"), so the double crash is repeated, a quiet minute
    apart, until one attempt showed the dialog (at most 3); every attempt must recover."""
    attempts = []
    try:
        for _ in range(3):
            _restore_home()
            assert wait_for(lawnchair_in_front, 10)
            since = phone_time()
            (p0, p1), t0 = _double_crash()
            _, data, err = _export_over_link(link["peer"], 90)
            took = time.monotonic() - t0
            ams = ams_log(since)
            # the dialog can come with either crash of the pair; while it shows, that process is stuck
            dialog = any("Showing crash dialog" in ln for ln in ams)
            p2 = pid()
            assert data.startswith(b"SQLite format 3\x00")
            assert p2 not in ("", p1), "the second crash left the same process alive"
            attempts.append(dialog)
            note(f"double crash in front: pids {p0} -> {p1} -> {p2}; crash dialog shown={dialog}; layout export "
                 f"over the link ok {took:.1f}s after the 2nd crash (last error before: {err}); AMS: {ams}")
            sh("logcat", "-b", "crash", "-c")
            if dialog:
                break
        assert any(attempts), f"Android never showed 'keeps stopping' in {len(attempts)} attempts: stuck state not reproduced"
    finally:
        sh("logcat", "-b", "crash", "-c")  # our own deliberate crashes
        _restore_home()
    finish(link, "double crash")


def test_crash_loop_is_bounded(link):
    """A crash on every link service start (debug hook: the next 8 starts throw), Settings in front:
    Lawnchair's restart alarm must not feed the loop. At most 3 alarms (the hard cap; nothing is armed
    within 60 s of our own alarm firing), and the crashing stops by itself well before the 8 are
    used up."""
    d = AdbTransport(SERIAL).device
    # Android's own crash history must be cold: the double-crash cases before this one end with our
    # restart alarm starting a process that the previous crash still counts against, and a crash
    # then is "crashed too many times, killing! Reason: crashed quickly" (no restart at all, so no
    # loop to bound; seen 2026-11-12). QUIET_S after our last `am crash` is not enough for that.
    _quiet_since_last_crash(2 * QUIET_S)
    sh("am", "start", "-W", "-a", "android.settings.SETTINGS")
    assert wait_for(lambda: not lawnchair_in_front(), 10)
    _reset_restart_budget()
    since = phone_time()
    try:
        d.call("debug_crash_link_start", "8")
        _crash()  # the restart after this one crashes in LinkService.onCreate, and so on
        time.sleep(150)
        log = adb("logcat", "-d", "-T", since, check=False).splitlines()
        alarms = [ln for ln in log if "OmalawnCrashExit: link restart alarm set" in ln]
        crashes = [ln for ln in adb("logcat", "-d", "-b", "events", "-T", since, check=False).splitlines()
                   if "am_crash" in ln and PKG in ln]
        assert len(alarms) <= 3, alarms
        assert 2 <= len(crashes) <= 8, crashes
        last = crashes[-1][:18]
        quiet_for = (time.time() - time.mktime(time.strptime(time.strftime("%Y-") + last, "%Y-%m-%d %H:%M:%S.%f"))
                     if last else 0)
        assert quiet_for > 60, f"still crashing: last crash {last}"
        # read the hook's file, not the provider: a provider call would start (and crash) the app
        xml = sh("run-as", PKG, "cat", "shared_prefs/omalawn_debug.xml", check=False)
        m = re.search(r'name="crash_link_start" value="(\d+)"', xml)
        left = int(m.group(1)) if m else None
        assert left, f"the loop ran through all 8 deliberate crashes: {xml}"
        note(f"crash loop: {len(crashes)} crashes, {len(alarms)} restart alarm(s), quiet for {quiet_for:.0f}s at "
             f"the end, {left} deliberate crashes never used; alarms: {alarms}")
    finally:
        d.call("debug_crash_link_start", "0")
        _reset_restart_budget()
        sh("logcat", "-b", "crash", "-c")  # our own deliberate crashes
        _restore_home()
    finish(link, "crash loop")


def test_crash_restarts_the_link(link):
    since = phone_time()
    p0 = pid()
    try:
        _reset_restart_budget()
        _crash_and_wait_restart(p0)  # waits the quiet minute first, then crashes
        t0 = _last_crash[0]
        p1 = pid()
        ping_over_link(link["peer"])
        note(f"crash: pid {p0} -> {p1}, link.ping answered {time.monotonic() - t0:.1f}s after the crash; "
             f"AMS: {ams_log(since)}")
    finally:
        sh("logcat", "-b", "crash", "-c")  # our own deliberate crash
        _restore_home()
    finish(link, "crash")


def test_ping_is_pure_and_ensure_loaded_loads(link):
    """A process started without the launcher activity (here: by a provider call after a force-stop,
    Settings in front) does not load the home screen; launcher.ping must not change that, and
    launcher.ensure_loaded must load it."""
    d = AdbTransport(SERIAL).device
    sh("am", "start", "-W", "-a", "android.settings.SETTINGS")
    try:
        sh("am", "force-stop", PKG)
        pings = []
        for _ in range(4):
            pings.append(d.call("ping")["model_loaded"])
            time.sleep(2)
        assert pings == [False] * 4, pings
        t0 = time.monotonic()
        r = d.call("ensure_loaded")
        if not r["loaded"]:  # bounded wait on the phone (10 s); a second call finishes it
            r = d.call("ensure_loaded")
        assert r["loaded"] is True and r["db"], r
        assert d.call("ping")["model_loaded"] is True
        note(f"ping pure: model_loaded {pings} over 8 s; ensure_loaded -> {r} in {time.monotonic() - t0:.1f}s")
    finally:
        _restore_home()
    finish(link, "ensure_loaded")


def _apk():
    apks = glob.glob(str(appid.apk_dir(PKG) / "*.apk"))
    if not apks:
        pytest.skip("no built APK (scripts/build-lawnchair.sh)")
    return max(apks, key=os.path.getmtime)


def test_app_update_brings_the_link_back(link):
    """`adb install -r` of the same APK with Settings in front (so no home activity restarts the
    app): MY_PACKAGE_REPLACED starts the process, and with it the link."""
    apk = _apk()
    sh("am", "start", "-W", "-a", "android.settings.SETTINGS")
    assert wait_for(lambda: not lawnchair_in_front(), 10)
    since = phone_time()
    try:
        t0 = time.monotonic()
        out = adb("install", "-r", apk, timeout=300)
        assert "Success" in out, out
        took = ping_over_link(link["peer"])
        log = ams_log(since)
        note(f"update: link.ping {time.monotonic() - t0:.1f}s after install started ({took:.1f}s after it "
             f"returned); link service running={service_running()}; AMS: {log}")
        receiver = [ln for ln in adb("logcat", "-d", "-T", since, "-s", "OmalawnLink:*", check=False).splitlines()
                    if "link start from android.intent.action.MY_PACKAGE_REPLACED" in ln]
        assert any("Start proc" in ln and "for broadcast" in ln and "BootReceiver" in ln for ln in log), log
        assert receiver, "BootReceiver did not handle MY_PACKAGE_REPLACED"
    finally:
        go_home()
    finish(link, "update")


def test_nothing_stays_in_the_shade(link):
    """The owner's rule (2026-10-17): no notification that stays. With the link in use and Homa Lawn
    on the home screen, `dumpsys notification` lists nothing ongoing for Homa Lawn, the old status
    notification (id 47100) is gone, and the link service is not a foreground service."""
    go_home()
    ping_over_link(link["peer"], NAT)
    ongoing = ongoing_notifications()
    services = sh("dumpsys", "activity", "services", PKG)
    assert ongoing == [], ongoing
    assert "id=47100 " not in sh("dumpsys", "notification", "--noredact")
    assert "isForeground=true" not in services
    note(f"shade: ongoing Homa Lawn notifications {ongoing}; link service running={service_running()}, "
         f"foreground={'isForeground=true' in services}")
    finish(link, "nothing in the shade", NAT)


def test_killed_behind_another_app_comes_back_by_itself_and_on_home(link):
    """The process is killed (as a low-memory kill would) while Settings is in front: Android
    restarts the sticky link service, the process starts the link, and computers reach it again
    without anyone touching the phone; pressing Home afterwards keeps it answering."""
    sh("am", "start", "-W", "-a", "android.settings.SETTINGS")
    assert wait_for(lambda: not lawnchair_in_front(), 10)
    since = phone_time()
    try:
        p0 = pid()
        sh("run-as", PKG, "kill", "-9", p0)
        t0 = time.monotonic()
        assert wait_for(lambda: pid() not in ("", p0), 60), "the process did not come back"
        back = ping_over_link(link["peer"], NAT, timeout=60)
        p1 = pid()
        state = net_state()
        assert not lawnchair_in_front()
        go_home()
        assert wait_for(lawnchair_in_front, 10)
        home = ping_over_link(link["peer"], NAT)
        note(f"kill behind Settings: pid {p0} -> {p1}; link.ping answered {time.monotonic() - t0:.1f}s after the "
             f"kill ({back:.1f}s of retrying) with Settings still in front, net {state}; after Home in {home:.1f}s; "
             f"AMS: {ams_log(since)}")
    finally:
        sh("logcat", "-b", "crash", "-c")
        go_home()
    finish(link, "kill behind Settings", NAT)


def test_without_keep_connected_the_link_is_cut_behind_another_app_and_the_phone_asks(link):
    """Why "Keep connected" matters now: without it Android 15 stops the link service a minute
    after another app comes to the front, and its background network rule then cuts the home
    app's network. The phone asks for it once (a "Setup reminders" notification with Run in
    background, removed once granted)."""
    d = link["d"]
    keep_connected(False)
    try:
        go_home()
        assert wait_for(lambda: "keep_connected" in d.call("debug_link_state")["asks"], 5)
        assert wait_for(lambda: re.search(r"pkg=" + re.escape(PKG) + r" user=\S+ id=47103 ",
                                          sh("dumpsys", "notification", "--noredact")), 10), "no Keep connected ask"
        ask = sh("dumpsys", "notification", "--noredact").split("id=47103 ", 1)[1][:3000]
        # the "Setup reminders" ask (patch 0033 words: "Let Homa Lawn run in the background")
        assert "run in the background" in ask and "ONGOING_EVENT" not in ask.split("NotificationRecord(", 1)[0]
        assert ongoing_notifications() == []
        sh("am", "start", "-W", "-a", "android.settings.SETTINGS")
        assert wait_for(lambda: not lawnchair_in_front(), 10)
        t0 = time.monotonic()
        assert wait_for(lambda: not service_running(), 150, step=5), "Android did not stop the idle app's service"
        stopped = time.monotonic() - t0
        assert wait_for(lambda: (net_state() or ("", ""))[1] != "NONE", 30), net_state()
        state = net_state()
        with pytest.raises(AssertionError):
            ping_over_link(link["peer"], NAT, timeout=15)
        note(f"no Keep connected: service stopped {stopped:.0f}s after Settings came up, net {state}, "
             f"link.ping not answered behind Settings; the phone asked (notification 47103, not ongoing)")
    finally:
        keep_connected(True)
        go_home()
    assert wait_for(lambda: "id=47103 " not in sh("dumpsys", "notification", "--noredact"), 10), "ask left behind"
    finish(link, "no Keep connected", NAT)


def test_reboot_brings_the_link_back_without_touching_the_app(link):
    d = AdbTransport(SERIAL).device
    d.call("debug_allow_local_pairing", "on")  # must not survive the reboot
    since = phone_time()
    t0 = time.monotonic()
    adb("reboot")
    adb("wait-for-device", timeout=300)
    assert wait_for(lambda: sh("getprop", "sys.boot_completed", check=False).strip() == "1", 300, step=2)
    booted = time.monotonic()
    forward()
    took = ping_over_link(link["peer"])
    starts = [ln for ln in adb("logcat", "-d", "-s", "ActivityManager:*", check=False).splitlines()
              if PKG in ln and re.search(r"Start proc", ln)]
    note(f"reboot: boot {booted - t0:.0f}s, link.ping {took:.1f}s after boot_completed; "
         f"link service running={service_running()}; AMS since boot: {starts[:6]} (before: {since})")
    state = d.call("debug_link_state")
    assert state["local_pairing"] is False and state["pairing_open_ms"] == 0, state
    finish(link, "reboot")
