"""App updates over the link (patch 0005, spec U1) on the test AVD (HOMALAWN_SERIAL=emulator-5560).

The computer sends Lawnchair's own APK with `launcher.update_offer {name, sha256, size, offset}` in
chunks of at most 8 MiB, one blob each; the phone checks the whole file (sha256, own package,
installed signing key, versionCode not lower) and posts "Homa Lawn update from <computer>", whose
tap opens Android's installer, or first the "Install unknown apps" page. These tests talk the raw
wire through the session's paired LinkTransport so they see the exact answers (the error's extra
fields included). The APK offered is the freshly built one (`scripts/build-lawnchair.sh` output), which
is what the AVD runs; "older" is not driven here (it needs a build with a lower versionCode).
Silent updates (patch 0007): with "Install unknown apps" allowed, the phone installs the offered
build itself through a PackageInstaller session and answers `silent: true`; Lawnchair restarts and
the link comes back with the new sha256. The build offered for that is ours with one extra file,
re-signed with the debug key (a different sha256, same package, key and versionCode); the original
is then offered back the same way. The notification-path tests below take "Install unknown apps"
away first, since with it allowed an offer now installs.
Run on its own:
  HOMALAWN_SERIAL=emulator-5560 uv run pytest tests/test_device_update.py -q -m device
"""
import hashlib
import json
import os
import re
import shutil
import subprocess
import time
from pathlib import Path

import pytest

pytestmark = pytest.mark.device
SERIAL = os.environ.get("HOMALAWN_SERIAL")
if not SERIAL:
    pytest.skip("HOMALAWN_SERIAL not set", allow_module_level=True)

from conftest import DEVICE_PACKAGE as PKG  # noqa: E402
from test_device_seam import _go_home, _top, _ui  # noqa: E402  (avd_hygiene, link_transport: conftest)

from homalawn import appid  # noqa: E402

APK_DIR = appid.apk_dir(PKG)  # the test build's folder (scripts/build-lawnchair.sh debug)
BUILD_TOOLS = Path.home() / "Android" / "Sdk" / "build-tools" / "36.1.0"
CHUNK = 8 << 20
SMALL = ["com.android.bookmarkprovider", "com.android.providers.userdictionary", "com.android.dreams.basic"]
INSTALLERS = ("packageinstaller",)


@pytest.fixture(autouse=True)
def _hygiene(avd_hygiene):
    """Every test here runs inside the one session-wide hygiene check (tests/conftest.py)."""


@pytest.fixture(scope="module")
def d(avd_hygiene):
    return avd_hygiene.transport.device


@pytest.fixture(scope="module")
def apk():
    found = sorted(APK_DIR.glob("*.apk"), key=lambda p: p.stat().st_mtime)
    if not found:
        pytest.skip("no built Lawnchair APK; run scripts/build-lawnchair.sh")
    return found[-1].read_bytes()


DEBUG_KEYSTORE = next((p for p in (Path.home() / ".config" / ".android" / "debug.keystore",
                                    Path.home() / ".android" / "debug.keystore") if p.exists()), None)


@pytest.fixture(scope="module")
def variant_apk(apk, tmp_path_factory):
    """Our own APK with one extra asset, aligned and re-signed with the debug key the build used."""
    import zipfile
    apksigner, zipalign = BUILD_TOOLS / "apksigner", BUILD_TOOLS / "zipalign"
    if not apksigner.exists() or not zipalign.exists() or DEBUG_KEYSTORE is None:
        pytest.skip(f"needs {apksigner}, {zipalign} and the debug keystore")
    tmp = tmp_path_factory.mktemp("variant")
    src, aligned, out = tmp / "in.apk", tmp / "aligned.apk", tmp / "out.apk"
    src.write_bytes(apk)
    with zipfile.ZipFile(src, "a") as z:
        z.writestr("assets/omalawn-test-variant.txt", f"variant {time.time()}\n")
    subprocess.run([str(zipalign), "-f", "-p", "4", str(src), str(aligned)], check=True, capture_output=True)
    subprocess.run([str(apksigner), "sign", "--ks", str(DEBUG_KEYSTORE), "--ks-pass", "pass:android",
                    "--ks-key-alias", "androiddebugkey", "--key-pass", "pass:android", "--out", str(out),
                    str(aligned)], check=True, capture_output=True)
    data = out.read_bytes()
    assert hashlib.sha256(data).digest() != hashlib.sha256(apk).digest()
    return data


@pytest.fixture(scope="module")
def other_apk(d, tmp_path_factory):
    """A small APK of another package, pulled from the AVD's system image."""
    for pkg in SMALL:
        path = d.shell("pm", "path", pkg).strip().splitlines()
        if path and path[0].startswith("package:"):
            data = subprocess.run(["adb", "-s", SERIAL, "exec-out", "cat", path[0][len("package:"):]],
                                  check=True, capture_output=True).stdout
            if data[:2] == b"PK":
                return data
    pytest.skip("no small system APK on the AVD")


@pytest.fixture(scope="module")
def foreign_key_apk(apk, tmp_path_factory):
    """Our own APK re-signed with a throwaway key."""
    apksigner = BUILD_TOOLS / "apksigner"
    keytool = shutil.which("keytool")
    if not apksigner.exists() or keytool is None:
        pytest.skip(f"needs {apksigner} and keytool")
    tmp = tmp_path_factory.mktemp("wrongkey")
    ks = tmp / "throwaway.jks"
    subprocess.run([keytool, "-genkeypair", "-keystore", str(ks), "-storepass", "throwaway", "-keypass", "throwaway",
                    "-alias", "t", "-keyalg", "RSA", "-keysize", "2048", "-validity", "1", "-dname", "CN=throwaway"],
                   check=True, capture_output=True)
    src, out = tmp / "in.apk", tmp / "out.apk"
    src.write_bytes(apk)
    subprocess.run([str(apksigner), "sign", "--ks", str(ks), "--ks-pass", "pass:throwaway", "--out", str(out), str(src)],
                   check=True, capture_output=True)
    return out.read_bytes()


def raw(t, method, params, blob=None):
    """One request on the paired link, answered as the raw JSON object (result or error)."""
    with t._lock:
        t.connect()
        c = t._conn
        c._n += 1
        out = json.dumps({"id": c._n, "method": method, "params": params}).encode() + b"\n"
        if blob is not None:
            out += json.dumps({"id": c._n, "blob": len(blob)}).encode() + b"\n" + blob
        c.sock.settimeout(120)
        c.sock.sendall(out)
        msg = json.loads(c._f.readline())
        assert msg["id"] == c._n, msg
        return msg


def chunk(t, data, offset, sha=None, size=None, n=CHUNK, name="test.apk"):
    sha = sha or hashlib.sha256(data).hexdigest()
    return raw(t, "launcher.update_offer",
               {"name": name, "sha256": sha, "size": len(data) if size is None else size, "offset": offset},
               data[offset:offset + n])


def offer(t, data, sha=None, start=0):
    """Sends `data` from `start` to the end in CHUNK pieces; returns every answer."""
    answers = []
    off = start
    while off < len(data):
        a = chunk(t, data, off, sha)
        answers.append(a)
        if "error" in a:
            return answers
        off += min(CHUNK, len(data) - off)
    return answers


def update_notification(d):
    out = d.shell("dumpsys", "notification", "--noredact")
    return [b for b in out.split("NotificationRecord(")[1:]
            if f"pkg={PKG}" in b and "id=47110" in b and "Homa Lawn update from" in b]


def update_text(d):
    """The update notification's text, or "" when there is none."""
    recs = update_notification(d)
    m = re.search(r"android\.text=\S+ \((.*?)\)\n", recs[0]) if recs else None
    return m.group(1) if m else ""


def link_notification(d):
    out = d.shell("dumpsys", "notification", "--noredact")
    return [b for b in out.split("NotificationRecord(")[1:] if f"pkg={PKG}" in b and "id=47100 " in b]


def update_files(d):
    try:
        return d.shell("run-as", PKG, "ls", "files/omalawn-update").split()
    except Exception:  # no such directory yet
        return []


@pytest.fixture(scope="module", autouse=True)
def _no_update_left(d):
    """Starts and ends with no offered update on the AVD (debug hook, as an install clears it)."""
    d.call("debug_update_reset")
    yield
    d.call("debug_update_reset")
    assert update_files(d) == [] and update_notification(d) == []


def test_app_info_reports_the_installed_apk(link_transport, d):
    r = raw(link_transport, "launcher.app_info", {})["result"]
    path = d.shell("pm", "path", PKG).strip().splitlines()[0][len("package:"):]
    base = subprocess.run(["adb", "-s", SERIAL, "exec-out", "cat", path], check=True, capture_output=True).stdout
    dump = d.shell("dumpsys", "package", PKG)
    assert r == {
        "package": PKG,
        "versionCode": int(re.search(r"versionCode=(\d+)", dump).group(1)),
        "versionName": re.search(r"versionName=(\S+)", dump).group(1),
        "sha256": hashlib.sha256(base).hexdigest(),
    }


def _not_allowed(d):
    """Takes "Install unknown apps" away (it kills Lawnchair: the link is dialled again)."""
    if "allow" in d.shell("appops", "get", PKG, "REQUEST_INSTALL_PACKAGES"):
        d.shell("appops", "set", PKG, "REQUEST_INSTALL_PACKAGES", "default")
        time.sleep(1)


def _relink(t, timeout=60):
    """The link again after Lawnchair restarted (killed, or replaced by an update)."""
    t.close()
    end = time.time() + timeout
    while True:
        try:
            return raw(t, "launcher.ping", {})
        except Exception:
            t.close()
            if time.time() > end:
                raise
            time.sleep(1)


def test_offer_in_chunks_posts_the_notification(link_transport, d, apk, install_sources):
    assert len(apk) > CHUNK, "the APK must need several chunks"
    _not_allowed(d)
    _relink(link_transport)
    answers = offer(link_transport, apk)
    assert [a.get("result") for a in answers[:-1]] == [{"received": CHUNK * (i + 1)} for i in range(len(answers) - 1)]
    last = answers[-1]["result"]
    assert last["received"] == len(apk) and last["offered"] is True
    assert last["package"] == PKG and isinstance(last["versionCode"], int) and last["versionName"]
    assert last["notified"] is True and last["can_install"] is False and last["silent"] is False
    assert update_notification(d), "no Homa Lawn update notification"
    assert update_files(d) == [f"update-{hashlib.sha256(apk).hexdigest()}.apk"]


def test_bad_offset_tells_where_to_resume(link_transport, d, apk, install_sources):
    _not_allowed(d)
    _relink(link_transport)
    first = chunk(link_transport, apk, 0)
    assert first["result"] == {"received": CHUNK}
    gap = chunk(link_transport, apk, 2 * CHUNK)
    assert gap["error"]["code"] == "bad_offset" and gap["error"]["expected"] == CHUNK
    overlap = chunk(link_transport, apk, 0)
    assert overlap["error"]["code"] == "bad_offset" and overlap["error"]["expected"] == CHUNK
    answers = offer(link_transport, apk, start=gap["error"]["expected"])
    assert answers[-1]["result"]["offered"] is True


def test_another_transfer_discards_the_partial_one(link_transport, apk, other_apk):
    assert chunk(link_transport, apk, 0)["result"] == {"received": CHUNK}
    # the small APK is sent whole, with a wrong hash: it starts (and ends) another transfer
    assert chunk(link_transport, other_apk, 0, sha="0" * 64)["error"]["code"] == "bad_hash"
    again = chunk(link_transport, apk, CHUNK)
    assert again["error"]["code"] == "bad_offset" and again["error"]["expected"] == 0


def test_another_package_is_refused(link_transport, d, other_apk):
    a = offer(link_transport, other_apk)[-1]
    assert a["error"]["code"] == "not_ours", a
    assert not [f for f in update_files(d) if f.endswith(".part") or hashlib.sha256(other_apk).hexdigest() in f]


def test_another_signing_key_is_refused(link_transport, foreign_key_apk):
    a = offer(link_transport, foreign_key_apk)[-1]
    assert a["error"]["code"] == "wrong_key", a


def test_bad_hash_is_refused(link_transport, apk):
    a = offer(link_transport, apk, sha="f" * 64)[-1]
    assert a["error"]["code"] == "bad_hash", a


def test_sizes_and_chunks_out_of_bounds_are_refused(link_transport, other_apk):
    big = chunk(link_transport, other_apk, 0, size=(256 << 20) + 1)
    assert big["error"]["code"] == "too_large"
    past = chunk(link_transport, other_apk, 0, size=10)
    assert past["error"]["code"] == "bad_params"
    bad_sha = raw(link_transport, "launcher.update_offer",
                  {"name": "x.apk", "sha256": "nope", "size": len(other_apk), "offset": 0}, other_apk)
    assert bad_sha["error"]["code"] == "bad_params"


def _tap_update_notification(d, timeout=20):
    end = time.time() + timeout
    text = ""
    while time.time() < end:
        d.shell("cmd", "statusbar", "expand-notifications")
        time.sleep(1)
        nodes = _ui(d)
        text = " | ".join(t for t, _, _ in nodes if t)
        hit = [c for t, _, c in nodes if t.startswith("Homa Lawn update from")]
        if hit:
            d.shell("input", "tap", str(hit[0][0]), str(hit[0][1]))
            return
        time.sleep(1)
    d.shell("cmd", "statusbar", "collapse")
    raise AssertionError(f"no Homa Lawn update notification in the shade: {text}")


def _wait_top(d, cond, timeout=15):
    end = time.time() + timeout
    while time.time() < end:
        top = _top(d)
        if cond(top):
            return top
        time.sleep(0.5)
    raise AssertionError("not reached; in front: " + _top(d))


@pytest.fixture
def install_sources(d, link_transport):
    """Puts Lawnchair's "Install unknown apps" app-op back as it was. Taking it away kills
    Lawnchair, so the link is then dialled again before the next test."""
    def now():
        m = re.search(r"REQUEST_INSTALL_PACKAGES: (\w+)", d.shell("appops", "get", PKG, "REQUEST_INSTALL_PACKAGES"))
        return m.group(1) if m else "default"
    before = now()
    yield
    changed = now() != before
    d.shell("appops", "set", PKG, "REQUEST_INSTALL_PACKAGES", before)
    d.shell("cmd", "statusbar", "collapse")
    if changed:
        link_transport.close()
        _go_home(d)
        end = time.time() + 30
        while True:
            try:
                raw(link_transport, "launcher.ping", {})
                break
            except Exception:
                link_transport.close()
                if time.time() > end:
                    raise
                time.sleep(1)
    d.call("debug_update_reset")
    _go_home(d)


def test_tap_first_asks_for_install_unknown_apps(link_transport, d, apk, install_sources):
    _not_allowed(d)  # taking it away kills Lawnchair: dial the link again before offering
    _relink(link_transport)
    d.shell("input", "keyevent", "KEYCODE_WAKEUP")
    d.shell("wm", "dismiss-keyguard")
    last = offer(link_transport, apk)[-1]["result"]
    assert last["can_install"] is False
    _tap_update_notification(d)
    _wait_top(d, lambda top: "settings" in top.lower())
    assert any("unknown" in t.lower() or "allow from this source" in t.lower() for t, _, _ in _ui(d))


def test_update_notification_stands_alone(link_transport, d, apk, install_sources):
    """In its own group, never folded with Homa Lawn's other notifications (Android's auto-group,
    Samsung One UI), where a tap on the folded group opens the launcher instead of the installer.
    The link itself posts nothing any more (2026-10-17)."""
    _not_allowed(d)
    _relink(link_transport)
    _go_home(d)
    assert link_notification(d) == [], "the link must not post a status notification"
    offer(link_transport, apk)
    rec = update_notification(d)[0]
    own_group = re.search(r"\n\s+groupKey=(\S+)", rec).group(1)  # this record's own line, not later sections
    assert own_group == f"0|{PKG}|g:app.lawnchair.omalawn.update", own_group


def test_text_asks_to_allow_installing_only_while_not_allowed(link_transport, d, apk, install_sources):
    _not_allowed(d)  # taking it away kills Lawnchair: dial the link again before offering
    _relink(link_transport)
    d.shell("input", "keyevent", "KEYCODE_WAKEUP")
    d.shell("wm", "dismiss-keyguard")
    assert offer(link_transport, apk)[-1]["result"]["can_install"] is False
    assert "allow installing apps" in update_text(d)
    d.shell("appops", "set", PKG, "REQUEST_INSTALL_PACKAGES", "allow")  # allowed meanwhile
    _tap_update_notification(d)
    _wait_top(d, lambda top: any(i in top.lower() for i in INSTALLERS))  # one tap, straight to the installer
    end = time.time() + 10
    while "allow installing apps" in update_text(d) and time.time() < end:
        time.sleep(0.5)
    text = update_text(d)
    assert text.endswith("Tap, then Update.") and "allow installing apps" not in text, text
    d.shell("input", "keyevent", "KEYCODE_BACK")


def test_fallback_tap_opens_androids_confirmation(link_transport, d, apk, install_sources):
    """Android wants the person (forced here with the debug hook; on a phone: an old targetSdk, a
    vendor policy): STATUS_PENDING_USER_ACTION brings up the notification, and its one tap opens
    Android's confirmation for that install session."""
    d.shell("appops", "set", PKG, "REQUEST_INSTALL_PACKAGES", "allow")
    _relink(link_transport)
    d.call("debug_update_require_user_action", "1")
    try:
        d.shell("input", "keyevent", "KEYCODE_WAKEUP")
        d.shell("wm", "dismiss-keyguard")
        last = offer(link_transport, apk)[-1]["result"]
        assert last["can_install"] is True and last["silent"] is False and last["notified"] is False
        end = time.time() + 15
        while not update_notification(d) and time.time() < end:  # posted when Android answers the commit
            time.sleep(0.5)
        assert update_notification(d), "no update notification after STATUS_PENDING_USER_ACTION"
        assert update_text(d).endswith("Tap, then Update.")
        _tap_update_notification(d)
        _wait_top(d, lambda top: any(i in top.lower() for i in INSTALLERS))
        assert any(t.lower() == "update" for t, _, _ in _ui(d)), "the confirmation does not offer Update"
        d.shell("input", "keyevent", "KEYCODE_BACK")  # the AVD declines; the update tap is a person's
    finally:
        d.call("debug_update_require_user_action", "0")


def _awake(d):
    d.shell("input", "keyevent", "KEYCODE_WAKEUP")
    d.shell("wm", "dismiss-keyguard")
    time.sleep(1)


def _interactive(d):
    return "mWakefulness=Awake" in d.shell("dumpsys", "power")


def _installed_sha(d):
    path = d.shell("pm", "path", PKG).strip().splitlines()[0][len("package:"):]
    return hashlib.sha256(subprocess.run(["adb", "-s", SERIAL, "exec-out", "cat", path], check=True,
                                         capture_output=True).stdout).hexdigest()


def _silent_install(t, d, data, timeout=60):
    """Offers `data` with "Install unknown apps" allowed; returns the last answer once the link is
    back with that build. No notification is ever asked for."""
    sha = hashlib.sha256(data).hexdigest()
    _awake(d)  # a locked phone keeps the build until the unlock (test_locked_phone_installs_at_the_unlock)
    last = offer(t, data)[-1]["result"]
    assert last["offered"] is True and last["silent"] is True and last["notified"] is False, last
    assert last["deferred_until_unlock"] is False, last
    end = time.time() + timeout
    while True:
        try:
            _relink(t, timeout=5)
            if raw(t, "launcher.app_info", {})["result"]["sha256"] == sha:
                return last
        except Exception:
            pass
        assert not update_notification(d), "a silent update asked for a tap"
        if time.time() > end:
            raise AssertionError(f"the phone did not come back with {sha[:12]} within {timeout} s")
        time.sleep(1)


SILENT_THROTTLE_S = 31  # Android lets one installer update a package silently at most every 30 s


@pytest.fixture
def play_protect(d):
    """Sets Play Protect's "Scan apps" (package_verifier_user_consent: 1 on, -1 off) for one test and
    puts it back. On this AVD the scan of a silent install of our debug build never decides (verdict
    "ask the user", no screen to ask on); it rejects only when Lawnchair is killed."""
    before = d.shell("settings", "get", "global", "package_verifier_user_consent").strip()

    def set_(on: bool):
        d.shell("settings", "put", "global", "package_verifier_user_consent", "1" if on else "-1")
    yield set_
    if before in ("", "null"):
        d.shell("settings", "delete", "global", "package_verifier_user_consent")
    else:
        d.shell("settings", "put", "global", "package_verifier_user_consent", before)


def test_silent_update_installs_without_a_tap(link_transport, d, apk, variant_apk, install_sources, play_protect):
    """True OTA: the offer installs, Lawnchair restarts, the link comes back with the new build; then
    the original build goes back the same way (after Android's 30 s limit on silent updates)."""
    play_protect(False)
    d.shell("appops", "set", PKG, "REQUEST_INSTALL_PACKAGES", "allow")
    _relink(link_transport)
    before = raw(link_transport, "launcher.app_info", {})["result"]
    pid0 = d.shell("pidof", PKG).strip()
    _silent_install(link_transport, d, variant_apk)
    assert _installed_sha(d) == hashlib.sha256(variant_apk).hexdigest()
    assert d.shell("pidof", PKG).strip() not in ("", pid0), "Lawnchair did not restart"
    assert d.call("ping")["default_home"] is True
    assert update_notification(d) == [] and update_files(d) == []
    end = time.time() + 15  # the status arrives at the receiver in the new process
    while (st := raw(link_transport, "launcher.update_status", {})["result"])["outcome"] != "installed" \
            and time.time() < end:
        time.sleep(1)
    assert st["outcome"] == "installed" and st["sha256"] == hashlib.sha256(variant_apk).hexdigest(), st
    time.sleep(SILENT_THROTTLE_S)
    _silent_install(link_transport, d, apk)
    after = raw(link_transport, "launcher.app_info", {})["result"]
    assert after == before and _installed_sha(d) == hashlib.sha256(apk).hexdigest()


def test_not_allowed_still_notifies(link_transport, d, apk, install_sources):
    """Without "Install unknown apps" nothing installs: the notification, as before."""
    _not_allowed(d)
    _relink(link_transport)
    last = offer(link_transport, apk)[-1]["result"]
    assert last["can_install"] is False and last["silent"] is False and last["notified"] is True
    assert update_notification(d)
    assert "allow installing apps" in update_text(d)


def test_play_protect_rejection_comes_back_as_the_notification(link_transport, d, variant_apk, install_sources,
                                                               play_protect):
    """With Play Protect scanning on, the silent install of our debug build hangs in its scan: after
    45 s the phone abandons the session, the build is not installed and the notification says so;
    its tap opens the installer the old way (where Play Protect asks)."""
    play_protect(True)
    d.shell("appops", "set", PKG, "REQUEST_INSTALL_PACKAGES", "allow")
    _relink(link_transport)
    time.sleep(SILENT_THROTTLE_S)  # not a throttled one: this is about the scan
    before = _installed_sha(d)
    _awake(d)
    last = offer(link_transport, variant_apk)[-1]["result"]
    assert last["silent"] is True and last["deferred_until_unlock"] is False
    assert raw(link_transport, "launcher.update_status", {})["result"]["outcome"] == "installing"
    end = time.time() + 90  # the phone gives the session up after 45 s
    while "did not install by itself" not in update_text(d) and time.time() < end:
        time.sleep(2)
    text = update_text(d)
    assert "did not install by itself" in text and "Play Protect" in text and text.endswith("Tap, then Update."), text
    assert _installed_sha(d) == before
    st = raw(link_transport, "launcher.update_status", {})["result"]
    assert st["outcome"] == "failed" and "Play Protect" in st["reason"], st


def test_homalawn_update_over_the_link_is_silent(link_transport, d, apk, variant_apk, install_sources, play_protect,
                                                tmp_path):
    """The desktop's own `homalawn update` code (update.send) against the AVD: it says the phone
    updates in the background, waits for the link to come back with the new build and says Updated."""
    from homalawn import update
    play_protect(False)
    d.shell("appops", "set", PKG, "REQUEST_INSTALL_PACKAGES", "allow")
    _relink(link_transport)
    time.sleep(SILENT_THROTTLE_S)
    for data, name in ((variant_apk, "variant.apk"), (apk, "original.apk")):
        f = tmp_path / name
        f.write_bytes(data)
        out: list[str] = []
        assert update.send(out.append, link_transport, f) == update.INSTALLED, out
        assert out[-2:] == [update.SILENT.format(phone=link_transport.label), update.UPDATED], out
        assert _installed_sha(d) == hashlib.sha256(data).hexdigest()
        assert update_notification(d) == []
        if name == "variant.apk":
            time.sleep(SILENT_THROTTLE_S)


def test_confirmation_after_the_45_s_limit_still_installs(link_transport, d, apk, variant_apk, install_sources,
                                                          play_protect):
    """A session Android answered with STATUS_PENDING_USER_ACTION waits for the person: the 45 s limit
    for undecided sessions leaves it alone, so a tap on the notification well after 45 s still opens
    Android's confirmation and Update installs the build. The original build goes back silently."""
    play_protect(False)
    d.shell("appops", "set", PKG, "REQUEST_INSTALL_PACKAGES", "allow")
    _relink(link_transport)
    time.sleep(SILENT_THROTTLE_S)
    d.call("debug_update_require_user_action", "1")
    try:
        d.shell("logcat", "-c")
        last = offer(link_transport, variant_apk)[-1]["result"]
        assert last["silent"] is False
        time.sleep(55)  # past the 45 s limit
        assert update_text(d).endswith("Tap, then Update."), update_text(d)
        assert "did not install by itself" not in update_text(d)
        log = d.shell("logcat", "-d", "-s", "OmalawnUpdate")
        assert "outcome=pending_user_action" in log and "predicted_silent=false" in log, log
        assert "abandoning" not in log, log
        d.shell("input", "keyevent", "KEYCODE_WAKEUP")
        d.shell("wm", "dismiss-keyguard")
        _tap_update_notification(d)
        _wait_top(d, lambda top: any(i in top.lower() for i in INSTALLERS))
        hit = [c for t, _, c in _ui(d) if t.lower() == "update"]
        assert hit, "the confirmation does not offer Update"
        d.shell("input", "tap", str(hit[0][0]), str(hit[0][1]))  # the AVD's tap; on a phone it is the person's
        end = time.time() + 90
        while True:
            try:
                _relink(link_transport, timeout=5)
                if raw(link_transport, "launcher.app_info", {})["result"]["sha256"] == \
                        hashlib.sha256(variant_apk).hexdigest():
                    break
            except Exception:
                pass
            if time.time() > end:
                raise AssertionError("the confirmed update did not install")
            time.sleep(1)
    finally:
        d.call("debug_update_require_user_action", "0")
    _go_home(d)
    time.sleep(SILENT_THROTTLE_S)
    _silent_install(link_transport, d, apk)
    assert _installed_sha(d) == hashlib.sha256(apk).hexdigest()


PIN = "1234"


@pytest.fixture
def screen_lock(d):
    """A PIN lock for one test (the AVD has none: its lock screen is switched off), then no lock again
    and the screen on and unlocked."""
    before = d.shell("locksettings", "get-disabled").strip()
    d.shell("locksettings", "set-disabled", "false")
    d.shell("locksettings", "set-pin", PIN)
    yield
    _unlock(d)
    d.shell("locksettings", "clear", "--old", PIN)
    d.shell("locksettings", "set-disabled", before if before in ("true", "false") else "true")
    _awake(d)
    assert "true" in d.shell("locksettings", "get-disabled") or before == "false"


def _lock(d):
    """Screen off; with the PIN set the keyguard is up when it comes back on."""
    if _interactive(d):
        d.shell("input", "keyevent", "KEYCODE_POWER")
    end = time.time() + 10
    while _interactive(d) and time.time() < end:
        time.sleep(0.5)
    assert not _interactive(d), "the AVD screen did not go off"


def _unlock(d):
    """Wake, bring up the PIN pad and type the PIN (what the person does)."""
    d.shell("input", "keyevent", "KEYCODE_WAKEUP")
    time.sleep(1)
    d.shell("wm", "dismiss-keyguard")
    time.sleep(1.5)
    d.shell("input", "text", PIN)
    d.shell("input", "keyevent", "KEYCODE_ENTER")
    end = time.time() + 10
    while "isKeyguardShowing=true" in d.shell("dumpsys", "window") and time.time() < end:
        time.sleep(0.5)


@pytest.mark.parametrize("restart", [False, True], ids=["same-process", "after-restart"])
def test_locked_phone_installs_at_the_unlock(link_transport, d, apk, variant_apk, install_sources, play_protect,
                                             screen_lock, tmp_path, restart):
    """Locked (PIN, screen off) when the build arrives: the phone keeps it, answers
    `deferred_until_unlock` (the desktop says so and does not wait), installs nothing while locked,
    and installs silently right after the unlock; `launcher.update_status` then says installed.
    `after-restart`: Lawnchair is stopped while locked, so the session held in memory is gone and the
    unlock installs the kept, checked APK through a new session (patch 0010: under the update lock)."""
    from homalawn import update
    play_protect(False)
    d.shell("appops", "set", PKG, "REQUEST_INSTALL_PACKAGES", "allow")
    _relink(link_transport)
    time.sleep(SILENT_THROTTLE_S)
    d.call("debug_update_reset")
    before = _installed_sha(d)
    _lock(d)
    f = tmp_path / "variant.apk"
    f.write_bytes(variant_apk)
    out: list[str] = []
    t0 = time.time()
    assert update.send(out.append, link_transport, f) == update.DEFERRED, out
    assert out[-1] == update.LOCKED.format(phone=link_transport.label), out
    assert time.time() - t0 < 50, "the desktop waited for a locked phone"
    st = raw(link_transport, "launcher.update_status", {})["result"]
    assert st["outcome"] == "deferred" and st["sha256"] == hashlib.sha256(variant_apk).hexdigest(), st
    assert st["locked"] is True
    time.sleep(10)
    assert _installed_sha(d) == before, "installed while locked"
    assert update_notification(d) == []
    if restart:
        d.shell("am", "force-stop", PKG)
        time.sleep(2)
    _unlock(d)
    if restart:
        d.shell("input", "keyevent", "KEYCODE_HOME")  # the default home comes back, and with it the link
    sha = hashlib.sha256(variant_apk).hexdigest()
    end = time.time() + 60
    while True:
        try:
            _relink(link_transport, timeout=5)
            if raw(link_transport, "launcher.app_info", {})["result"]["sha256"] == sha:
                break
        except Exception:
            pass
        assert not update_notification(d), "the deferred update asked for a tap"
        if time.time() > end:
            raise AssertionError("the deferred update did not install after the unlock")
        time.sleep(1)
    end = time.time() + 15
    while (st := raw(link_transport, "launcher.update_status", {})["result"])["outcome"] != "installed" \
            and time.time() < end:
        time.sleep(1)
    assert st["outcome"] == "installed" and st["sha256"] == sha and st["locked"] is False, st
    assert d.call("ping")["default_home"] is True
    _go_home(d)
    time.sleep(SILENT_THROTTLE_S)
    _silent_install(link_transport, d, apk)
    assert _installed_sha(d) == hashlib.sha256(apk).hexdigest()


def test_update_status_with_nothing_offered(link_transport, d, install_sources):
    """With nothing offered, `launcher.update_status` says none."""
    d.call("debug_update_reset")
    st = raw(link_transport, "launcher.update_status", {})["result"]
    assert st["outcome"] == "none" and st["sha256"] == "" and "reason" not in st, st
