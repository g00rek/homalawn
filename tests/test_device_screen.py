"""Screen control (patch 0004) on the test AVD (HOMALAWN_SERIAL=emulator-5560), on the Settings
app, over the paired link.

The AVD hygiene fixture and the one-pairing-per-session link fixture come from
test_device_seam. A module fixture turns Lawnchair's accessibility service on through the secure
setting (the UI path was checked by hand, see tests/device/NOTES.md) and puts the previous value
back at the end. Run on its own:
  HOMALAWN_SERIAL=emulator-5560 uv run pytest tests/test_device_screen.py -v
"""
import os
import re
import struct
import time

import pytest

pytestmark = pytest.mark.device
SERIAL = os.environ.get("HOMALAWN_SERIAL")
if not SERIAL:
    pytest.skip("HOMALAWN_SERIAL not set", allow_module_level=True)

from conftest import DEVICE_PACKAGE as PKG  # noqa: E402
from homalawn.transport import ProviderError  # noqa: E402
from test_device_seam import LINK, _ui, crash_log  # noqa: E402,F401  (avd_hygiene, link_transport: conftest)

SERVICE = f"{PKG}/app.lawnchair.LawnchairAccessibilityService"
SETTINGS = "com.android.settings"
SEARCH = "com.google.android.settings.intelligence"  # Settings' search page runs in its own app


def _get(d, key):
    v = d.shell("settings", "get", "secure", key).strip()
    return None if v in ("", "null") else v


def _put(d, key, value):
    if value is None:
        d.shell("settings", "delete", "secure", key)
    else:
        d.shell("settings", "put", "secure", key, value)


def _status(d):
    return d.call("screen_status")


def _wait_enabled(d, want, timeout=15):
    end = time.time() + timeout
    while time.time() < end:
        if _status(d)["enabled"] is want:
            return True
        time.sleep(0.3)
    return False


def _without_us(services):
    return ":".join(s for s in (services or "").split(":") if s and s != SERVICE) or None


@pytest.fixture(scope="module")
def d(avd_hygiene):
    return avd_hygiene.transport.device


@pytest.fixture(scope="module")
def screen_on(d):
    before = (_get(d, "enabled_accessibility_services"), _get(d, "accessibility_enabled"))
    services = before[0] or ""
    if SERVICE not in services.split(":"):
        _put(d, "enabled_accessibility_services", ":".join(s for s in (services, SERVICE) if s))
    _put(d, "accessibility_enabled", "1")
    assert _wait_enabled(d, True), "Lawnchair's accessibility service did not bind"
    yield
    _put(d, "enabled_accessibility_services", before[0])
    _put(d, "accessibility_enabled", before[1])
    d.shell("input", "keyevent", "HOME")
    if SERVICE not in (before[0] or "").split(":"):
        assert _wait_enabled(d, False)


@pytest.fixture
def lt(link_transport, screen_on, d):
    d.shell("input", "keyevent", "WAKEUP")
    d.shell("wm", "dismiss-keyguard")
    return link_transport


def tree(t):
    """screen.tree; while an app is still opening there may be no window for a moment."""
    for _ in range(10):
        try:
            return t.call("screen.tree")
        except ProviderError as e:
            if e.code != "no_window":
                raise
            time.sleep(0.3)
    return t.call("screen.tree")


def texts(tr):
    return [n.get("text") or n.get("desc") or "" for n in tr["nodes"]]


def find(tr, label):
    return next((n for n in tr["nodes"] if label in (n.get("text"), n.get("desc"))), None)


def wait_tree(t, cond, timeout=10):
    end = time.time() + timeout
    tr = tree(t)
    while not cond(tr) and time.time() < end:
        time.sleep(0.4)
        tr = tree(t)
    assert cond(tr), texts(tr)
    return tr


def settings_home(t):
    """Settings opened fresh at its top page (a launch resumes where it was, so stop it first)."""
    import subprocess
    for pkg in (SETTINGS, SEARCH):
        subprocess.run(["adb", "-s", SERIAL, "shell", "am", "force-stop", pkg], check=True, capture_output=True)
    t.call("screen.launch", {"package": SETTINGS})
    return wait_tree(t, lambda tr: tr["package"] == SETTINGS and find(tr, "Network & internet") is not None)


def png_size(data):
    assert data[:8] == b"\x89PNG\r\n\x1a\n"
    return struct.unpack(">II", data[16:24])


def screen_size(d):
    m = re.search(r"(\d+)x(\d+)", d.shell("wm", "size").splitlines()[-1])
    return int(m.group(1)), int(m.group(2))


def test_status_over_link_and_provider(lt, d):
    st = lt.call("screen.status")
    assert st["enabled"] is True and st == _status(d)
    assert "restricted" in st
    # the system built the service from the XML: every capability is there (161 = window
    # content 1 + gestures 32 + screenshots 128), the flags were not dropped on connect
    dump = d.shell("dumpsys", "accessibility")
    assert re.search(r"Bound services:\{Service\[label=Homa Lawn[^\n]*capabilities=161", dump), dump[:2000]


def test_settings_tree_tap_back(lt, d):
    tr = settings_home(lt)
    row = find(tr, "Network & internet")
    assert row["bounds"][2] > row["bounds"][0] and "depth" in row
    assert {"id", "class", "bounds", "click", "focus", "edit", "scroll"} <= set(row)
    lt.call("screen.tap", {"node": row["id"]})
    inner = wait_tree(lt, lambda t2: find(t2, "Internet") is not None)  # the Network & internet page
    assert texts(inner) != texts(tr)
    lt.call("screen.key", {"name": "back"})
    wait_tree(lt, lambda t2: find(t2, "Network & internet") is not None)


def test_tap_by_coordinates_and_stale_node(lt, d):
    tr = settings_home(lt)
    row = find(tr, "Network & internet")
    l, t, r, b = row["bounds"]
    lt.call("screen.tap", {"x": (l + r) // 2, "y": (t + b) // 2})
    wait_tree(lt, lambda t2: find(t2, "Internet") is not None)
    with pytest.raises(ProviderError) as e:
        lt.call("screen.tap", {"node": "n99999"})
    assert e.value.code == "stale_node"
    with pytest.raises(ProviderError) as e:
        lt.call("screen.tap", {"x": -5, "y": 10})
    assert e.value.code == "bad_params"
    lt.call("screen.key", {"name": "back"})


def test_swipe_scrolls_settings(lt, d):
    tr = settings_home(lt)
    w, h = screen_size(d)
    lt.call("screen.swipe", {"x1": w // 2, "y1": int(h * 0.8), "x2": w // 2, "y2": int(h * 0.3), "ms": 400})
    after = wait_tree(lt, lambda t2: texts(t2) != texts(tr))
    assert find(after, "Network & internet") is None or texts(after) != texts(tr)


def test_shot_png_and_rate_limit(lt, d):
    settings_home(lt)
    time.sleep(0.4)
    data = lt.screenshot()
    assert png_size(data) == screen_size(d)
    with pytest.raises(ProviderError) as e:  # straight after: refused by our 340 ms guard
        lt.call("screen.shot")
    assert e.value.code == "rate_limited"
    time.sleep(0.4)
    t0 = time.time()
    shots = 0
    while time.time() - t0 < 2.0:
        try:
            lt.screenshot()
            shots += 1
        except ProviderError as err:
            assert err.code == "rate_limited"
        time.sleep(0.35)
    print(f"SCREEN shots in 2 s at 0.35 s spacing: {shots}")
    assert 3 <= shots <= 6


def test_type_into_settings_search(lt, d):
    tr = settings_home(lt)
    search = next(n for n in tr["nodes"] if "Search" in (n.get("text") or n.get("desc") or ""))
    lt.call("screen.tap", {"node": search["id"]})
    field = wait_tree(lt, lambda t2: any(n["edit"] and n["focus"] for n in t2["nodes"]))
    lt.call("screen.type", {"text": "homalawn wifi"})
    typed = wait_tree(lt, lambda t2: any(n["edit"] and "homalawn wifi" in (n.get("text") or "") for n in t2["nodes"]))
    assert typed["package"] != PKG
    # an explicit node works too, and replaces the text
    edit = next(n for n in typed["nodes"] if n["edit"])
    lt.call("screen.type", {"text": "display", "node": edit["id"]})
    wait_tree(lt, lambda t2: any(n["edit"] and (n.get("text") or "") == "display" for n in t2["nodes"]))
    with pytest.raises(ProviderError) as e:
        lt.call("screen.type", {"text": "x", "node": next(n["id"] for n in field["nodes"] if not n["edit"])})
    assert e.value.code in ("not_editable", "stale_node")
    lt.call("screen.key", {"name": "back"})
    lt.call("screen.key", {"name": "back"})


def test_keys_home_recents_notifications(lt, d):
    settings_home(lt)
    lt.call("screen.key", {"name": "home"})
    wait_tree(lt, lambda t2: t2["package"] == PKG)
    lt.call("screen.key", {"name": "notifications"})
    time.sleep(1.5)
    assert "mExpandedVisible=true" in d.shell("dumpsys", "statusbar") or \
        "systemui" in str(tree(lt)["package"])
    d.shell("cmd", "statusbar", "collapse")
    lt.call("screen.key", {"name": "quick_settings"})
    time.sleep(1.5)
    d.shell("cmd", "statusbar", "collapse")
    with pytest.raises(ProviderError) as e:
        lt.call("screen.key", {"name": "power"})
    assert e.value.code == "bad_params"
    lt.call("screen.key", {"name": "recents"})
    time.sleep(1.5)
    lt.call("screen.key", {"name": "home"})


def test_long_press_on_home_opens_lawnchair_menu(lt, d):
    x, y = _empty_home_point(lt, d)
    time.sleep(1)  # let the home animation settle; a touch during it is swallowed
    menu_up = lambda t2: any(s in " ".join(texts(t2)) for s in ("Wallpaper", "Home settings", "Widgets"))  # noqa: E731
    for attempt in range(2):
        lt.call("screen.long_press", {"x": x, "y": y})
        end = time.time() + 5
        tr = tree(lt)
        while not menu_up(tr) and time.time() < end:
            time.sleep(0.4)
            tr = tree(lt)
        if menu_up(tr):
            break
        print(f"SCREEN long press attempt {attempt + 1} at {x},{y}: no menu")
    assert menu_up(tr) and tr["package"] == PKG, texts(tr)
    lt.call("screen.key", {"name": "back"})


def test_cli_screen_commands_over_link(lt, d, tmp_path):
    """The desktop CLI end to end: launch, the indented tree, a tap by node id, a screenshot."""
    import subprocess
    from test_device_seam import _cli
    for pkg in (SETTINGS, SEARCH):
        subprocess.run(["adb", "-s", SERIAL, "shell", "am", "force-stop", pkg], check=True, capture_output=True)
    r = _cli("--via", "link", "screen", "launch", SETTINGS)
    assert r.returncode == 0 and "Opened com.android.settings" in r.stdout, r.stdout + r.stderr
    wait_tree(lt, lambda t2: find(t2, "Network & internet") is not None)
    r = _cli("--via", "link", "screen", "tree")
    assert r.returncode == 0, r.stderr
    print("SCREEN cli tree (first lines):\n" + "\n".join(r.stdout.splitlines()[:8]))
    line = next(l for l in r.stdout.splitlines() if "Network & internet" in l)
    assert line.startswith("  ") and "[click]" in line and re.search(r"@\d+,\d+$", line), line
    nid = line.split()[0]
    r = _cli("--via", "link", "screen", "tap", nid)
    assert r.returncode == 0, r.stderr
    wait_tree(lt, lambda t2: find(t2, "Internet") is not None)
    time.sleep(0.4)
    out = tmp_path / "s.png"
    r = _cli("--via", "link", "screen", "shot", str(out))
    assert r.returncode == 0, r.stderr
    assert png_size(out.read_bytes()) == screen_size(d)
    r = _cli("--via", "link", "screen", "status")
    assert r.returncode == 0 and "Screen control is on." in r.stdout
    r = _cli("--via", "adb", "screen", "status")
    assert r.returncode == 0 and "Screen control is on." in r.stdout
    _cli("--via", "link", "screen", "key", "home")


def test_launch_unknown_package(lt):
    with pytest.raises(ProviderError) as e:
        lt.call("screen.launch", {"package": "com.example.nothing.here"})
    assert e.value.code == "not_found"
    with pytest.raises(ProviderError) as e:
        lt.call("screen.launch", {"package": "not a package"})
    assert e.value.code == "bad_params"


def test_unpaired_computer_cannot_reach_screen(lt, tmp_path, monkeypatch):
    from homalawn.link import identity
    from homalawn.link.wire import Conn, LinkError
    monkeypatch.setenv("HOMALAWN_CONFIG", str(tmp_path / "stranger" / "config.toml"))
    stranger = identity.ensure()
    for method in ("screen.tree", "screen.status", "screen.nonexistent"):
        with Conn("127.0.0.1", LINK, str(stranger.cert), str(stranger.key), timeout=10) as c:
            assert c.call("link.hello")["paired"] is False
            with pytest.raises(LinkError) as e:
                c.call(method)
            assert e.value.code == "not_paired"


def test_provider_offers_status_only(d, screen_on):
    from homalawn.adb import ProviderError as PE
    for m in ("screen_tree", "screen_tap", "screen_shot", "screen.tree"):
        with pytest.raises(PE) as e:
            d.call(m)
        assert "unknown method" in str(e.value)


def _empty_home_point(lt, d):
    lt.call("screen.key", {"name": "home"})
    home = wait_tree(lt, lambda t2: t2["package"] == PKG)
    w, h = screen_size(d)
    taken = [n["bounds"] for n in home["nodes"] if n["click"] and (n["bounds"][2] - n["bounds"][0]) < w * 0.9]
    x, y = w // 2, int(h * 0.45)
    for _ in range(12):
        if not any(l <= x <= r and t <= y <= b for l, t, r, b in taken):
            break
        y += h // 30
    return x, y


def test_lawnchair_double_tap_sleep_still_works(lt, d):
    """Lawnchair's own use of the service (double tap locks the phone, its default) survives."""
    import subprocess
    from homalawn import prefs
    v = prefs.current(lt).get("datastore", {}).get("double_tap_gesture_handler")
    if v is not None and "sleep" not in str(v.get("value")):
        pytest.skip(f"double tap is set to {v} on this AVD")
    x, y = _empty_home_point(lt, d)
    # two `input` processes started together: their taps land ~100 ms apart
    subprocess.run(["adb", "-s", SERIAL, "shell", f"input tap {x} {y} & sleep 0.1; input tap {x} {y}; wait"],
                   check=True, capture_output=True, timeout=30)
    try:
        end = time.time() + 6
        while "mWakefulness=Awake" in d.shell("dumpsys", "power") and time.time() < end:
            time.sleep(0.3)
        assert "mWakefulness=Awake" not in d.shell("dumpsys", "power"), "double tap did not lock the phone"
    finally:
        d.shell("input", "keyevent", "WAKEUP")
        time.sleep(1)
        d.shell("wm", "dismiss-keyguard")
        time.sleep(1)


NOTIF_MEDIA = r"pkg=" + re.escape(PKG) + r" user=\S+ id=47102 "


def _media_state(d):
    """(READ_MEDIA_IMAGES grant+flags, its uid app-op mode, the limited-access grant+flags)."""
    dump = d.shell("dumpsys", "package", PKG)

    def perm(name):
        m = re.search(r"android\.permission\." + name + r": (granted=\w+, flags=\[[^\]]*\])", dump)
        return m.group(1) if m else None
    o = re.search(r"Uid mode: READ_MEDIA_IMAGES: (\w+)", d.shell("appops", "get", "--uid", PKG, "READ_MEDIA_IMAGES"))
    return perm("READ_MEDIA_IMAGES"), (o.group(1) if o else "default"), perm("READ_MEDIA_VISUAL_USER_SELECTED")


def test_wallpaper_over_link_explains_once_when_no_prompt_can_help(lt, d, tmp_path):
    """Android 13+ (API 35 here): the photo prompt cannot make the wallpaper readable for Lawnchair,
    so the request posts nothing and the CLI says in one line what to do instead."""
    import subprocess
    from homalawn.session import forget_wallpaper
    from test_device_seam import _cli
    forget_wallpaper()  # the wallpaper was not set by homalawn: only the phone could read it
    with pytest.raises(ProviderError) as e:
        lt.wallpaper_backup()
    assert "SecurityException" in str(e.value)
    assert lt.call("launcher.request_media_permission") == {"granted": False, "asked": False, "why": "not_grantable"}
    assert not re.search(NOTIF_MEDIA, d.shell("dumpsys", "notification", "--noredact"))
    wp0 = d.call("wallpaper_info")["system_id"]
    img = tmp_path / "w.png"
    img.write_bytes(subprocess.run(["adb", "-s", SERIAL, "exec-out", "screencap", "-p"], capture_output=True).stdout)
    r = _cli("--via", "link", "wallpaper", str(img))
    print("SCREEN wallpaper over link:", r.stdout.strip(), "|", r.stderr.strip())
    assert r.returncode == 1
    target, err = r.stderr.strip().split("\n")  # which phone, then one line of what to do
    assert target.startswith("via link (")
    assert "--via adb" in err and "--no-backup" in err
    assert d.call("wallpaper_info")["system_id"] == wp0  # nothing was changed
    assert not re.search(NOTIF_MEDIA, d.shell("dumpsys", "notification", "--noredact"))


def _find_in_shade(d, text, timeout=15):
    """Centre of `text` in the open shade. Android groups Lawnchair's notifications; a tap on a
    collapsed group only opens the group, so the group is expanded first."""
    end = time.time() + timeout
    while time.time() < end:
        d.shell("cmd", "statusbar", "expand-notifications")
        time.sleep(1)
        nodes = _ui(d)
        rows = [c for t, desc, c in nodes if t.startswith(("Homa Lawn", "Lawnchair"))]
        exp = [c for t, desc, c in nodes if desc == "Expand" and rows and abs(c[1] - rows[0][1]) < 60]
        if exp:
            d.shell("input", "tap", str(exp[0][0]), str(exp[0][1]))
            time.sleep(1.5)
            nodes = _ui(d)
        hit = next((c for t, desc, c in nodes if text in t), None)
        if hit:
            return hit
    return None


def _home_again(d, timeout=30):
    """Homa Lawn back in front and loaded after its process was killed (a permission revoked).
    No provider call until the home screen is in front: a call into a starting process can
    deadlock it (see tests/device/NOTES.md, "ping during a cold start")."""
    from test_device_seam import _go_home
    time.sleep(1)
    _go_home(d, timeout=timeout)
    time.sleep(2)
    end = time.time() + timeout
    while time.time() < end:
        try:
            if d.call("ping")["model_loaded"]:
                return
        except Exception:
            pass
        time.sleep(0.5)
    raise AssertionError("Homa Lawn did not come back")


def test_media_prompt_path_notification_to_system_prompt(lt, d):
    """The notification -> trampoline -> system prompt path (forced by the debug hook, since on
    API 35 no prompt can unlock the wallpaper): tap, Allow, notification gone."""
    state0 = _media_state(d)
    granted0 = (state0[0] or "").startswith("granted=true")
    if granted0:
        # the suite's starting state allows photos (testing.md); Android shows no prompt for a granted
        # permission, so take it away for this test (it kills Homa Lawn) and grant it back after
        d.shell("pm", "revoke", PKG, "android.permission.READ_MEDIA_IMAGES")
        _home_again(d)
    try:
        assert d.call("debug_media_prompt") == {"granted": False, "asked": True}
        assert re.search(NOTIF_MEDIA, d.shell("dumpsys", "notification", "--noredact"))
        hit = _find_in_shade(d, "Homa Lawn needs access to photos")
        assert hit, "notification not in the shade"
        d.shell("input", "tap", str(hit[0]), str(hit[1]))
        end = time.time() + 15
        allow = None
        while time.time() < end and allow is None:
            time.sleep(1)
            nodes = _ui(d)
            allow = next((c for t, desc, c in nodes if t in ("Allow all", "Allow")), None)
        print("SCREEN media prompt:", [t for t, _, _ in nodes if t][:6])
        assert allow, "the system permission prompt did not open"
        d.shell("input", "tap", str(allow[0]), str(allow[1]))
        time.sleep(2)
        after = _media_state(d)
        print("SCREEN media state before/after Allow:", state0, after)
        assert after != state0 and "REVOKED_COMPAT" not in (after[0] or "")  # the prompt granted it
        assert not re.search(NOTIF_MEDIA, d.shell("dumpsys", "notification", "--noredact"))
    finally:
        d.shell("cmd", "statusbar", "collapse")
        # back to how it was: the grant the prompt made is undone flag by flag
        d.shell("pm", "clear-permission-flags", PKG, "android.permission.READ_MEDIA_IMAGES", "user-set", "user-fixed")
        if granted0 and not (_media_state(d)[0] or "").startswith("granted=true"):
            d.shell("pm", "grant", PKG, "android.permission.READ_MEDIA_IMAGES")
        d.shell("appops", "set", "--uid", PKG, "READ_MEDIA_IMAGES", state0[1])
        if "granted=false" in (state0[2] or "granted=false"):
            d.shell("pm", "revoke", PKG, "android.permission.READ_MEDIA_VISUAL_USER_SELECTED")
            d.shell("pm", "clear-permission-flags", PKG, "android.permission.READ_MEDIA_VISUAL_USER_SELECTED",
                    "user-set", "user-fixed")
        # last: revoking the limited access marks the photo permission revoked-compat
        d.shell("pm", "set-permission-flags" if "REVOKED_COMPAT" in (state0[0] or "") else "clear-permission-flags",
                PKG, "android.permission.READ_MEDIA_IMAGES", "revoked-compat")
        d.shell("input", "keyevent", "HOME")
    assert _media_state(d) == state0


def test_screen_off_answer(lt, d):
    """With the service off every screen.* but status answers screen_off; status says so."""
    services = _get(d, "enabled_accessibility_services")
    _put(d, "enabled_accessibility_services", _without_us(services))
    try:
        assert _wait_enabled(d, False)
        assert lt.call("screen.status")["enabled"] is False
        for m, p in (("screen.tree", {}), ("screen.tap", {"x": 1, "y": 1}), ("screen.shot", {}),
                     ("screen.key", {"name": "back"}), ("screen.launch", {"package": SETTINGS})):
            with pytest.raises(ProviderError) as e:
                lt.call(m, p)
            assert e.value.code == "screen_off"
            assert "Permissions › Screen control" in str(e.value)  # patch 0033's words
    finally:
        _put(d, "enabled_accessibility_services", services)
        assert _wait_enabled(d, True)


def _ongoing(d):
    """Homa Lawn's notifications that stay in the shade (ongoing or a foreground service's)."""
    dump = d.shell("dumpsys", "notification", "--noredact")
    recs = re.findall(r"NotificationRecord\([^)]*pkg=" + re.escape(PKG) + r" user=\S+ id=\d+ .*?flags=([A-Z_|]*)", dump)
    return [f for f in recs if "ONGOING_EVENT" in f or "FOREGROUND_SERVICE" in f or "NO_CLEAR" in f]


def test_phone_tracks_who_is_connected_and_screen_use_without_a_notification(lt, d):
    """The link still knows which computer is connected and that the screen was used in the last
    30 s (the provider's debug_link_state shows it; on/off status checks do not count), but since
    2026-10-17 nothing about it stays in the shade: no status notification, no foreground service."""
    lt.call("link.ping")
    assert "homalawn-test-desk" in d.call("debug_link_state")["connected"]
    tree(lt)
    st = d.call("debug_link_state")
    assert 25_000 < st["screen_in_use_ms"] <= 30_000, st
    assert _ongoing(d) == [], _ongoing(d)
    assert "id=47100 " not in d.shell("dumpsys", "notification", "--noredact")
    assert "isForeground=true" not in d.shell("dumpsys", "activity", "services", PKG)
    time.sleep(31)
    assert d.call("debug_link_state")["screen_in_use_ms"] == 0
    lt.call("screen.status")
    assert d.call("debug_link_state")["screen_in_use_ms"] == 0


def test_checkable_nodes_report_checked(lt, d):
    """Task 6: switches and checkboxes say whether they are on (the Location page's main switch
    follows location_mode; nothing is toggled)."""
    import subprocess
    from homalawn import screen
    subprocess.run(["adb", "-s", SERIAL, "shell", "am", "force-stop", SETTINGS], check=True, capture_output=True)
    subprocess.run(["adb", "-s", SERIAL, "shell", "am", "start", "-a", "android.settings.LOCATION_SOURCE_SETTINGS"],
                   check=True, capture_output=True)
    tr = wait_tree(lt, lambda t2: t2["package"] == SETTINGS and any(n.get("checkable") for n in t2["nodes"]))
    boxes = [n for n in tr["nodes"] if n.get("checkable")]
    assert all(isinstance(n["checked"], bool) for n in boxes), boxes
    assert not any("checked" in n for n in tr["nodes"] if not n.get("checkable"))
    on = d.shell("settings", "get", "secure", "location_mode").strip() not in ("0", "")
    assert boxes[0]["checked"] is on, (boxes[0], on)
    assert any(f"[{'checked' if on else 'unchecked'}]" in line for line in screen.format_tree(tr))
    lt.call("screen.key", {"name": "home"})


PAIRING_OPEN = "pairing_open"
PAUSED = "Screen control is paused while the phone shows a pairing request; a person answers it on the phone."


def _refused_while_pairing(t, method, params):
    with pytest.raises(ProviderError) as e:
        t.call(method, params)
    assert e.value.code == PAIRING_OPEN and PAUSED in str(e.value), (method, str(e.value))


@pytest.mark.parametrize("route", ["review", "home"])
def test_screen_control_cannot_answer_a_pairing_request(lt, d, tmp_path, monkeypatch, route):
    """Patch 0035: a paired computer with Screen control on asks to pair from a new key and tries to
    tap Accept itself. While a pair request is open (the shade's Review / Reject before the pairing
    screen is even open) and while the pairing screen shows, every screen.* call but screen.status
    is refused with pairing_open, the tree and the screenshot too (the words stay on the phone);
    nothing is paired; a finger's Reject ends it and Screen control works again. Two ways the
    pairing screen comes up: Review on the notification, and the home screen showing."""
    from homalawn.link import identity
    from test_device_seam import _ask, _confirm_screen, _conn, _go_home, _hello_paired, _tap_confirm, pins
    with monkeypatch.context() as m:  # the new key; the session's paired link keeps its own identity
        m.setenv("HOMALAWN_CONFIG", str(tmp_path / "newkey" / "config.toml"))
        newkey = identity.ensure()
    pins0 = pins(d)
    local0 = d.call("debug_link_state")["local_pairing"]
    d.call("debug_pair_reset")
    d.call("debug_allow_local_pairing", "on")  # adb forward arrives as loopback
    c = _conn(newkey, timeout=60)
    try:
        if route == "review":
            _ask(c)  # the pairing window open: the prompt goes to the shade
        else:
            d.call("debug_close_pairing")
            _go_home(d)
            _ask(c, reopen=False)  # the home screen showing: the pairing screen comes up in front
        # a request is open: Screen control cannot even pull down the shade or read the screen
        if route == "review":
            for method, params in (("screen.tree", {}), ("screen.shot", {}), ("screen.key", {"name": "notifications"}),
                                   ("screen.launch", {"package": SETTINGS})):
                _refused_while_pairing(lt, method, params)
            assert lt.call("screen.status")["enabled"] is True  # status still answers
            assert d.call("debug_pair_fire_current")["current"] == "sent"  # Review, as a tap in the shade does
        end = time.time() + 15
        while (nodes := _confirm_screen(d)) is None and time.time() < end:
            time.sleep(0.5)
        assert nodes, "the pairing screen did not come up"
        x, y = next(c_ for t_, _, c_ in nodes if t_ == "Accept")
        time.sleep(1.5)  # past Accept's 1 s arming: a finger here would pair
        for method, params in (("screen.tap", {"x": x, "y": y}), ("screen.long_press", {"x": x, "y": y, "ms": 100}),
                               ("screen.swipe", {"x1": x, "y1": y, "x2": x, "y2": y + 1}),
                               ("screen.type", {"text": "x"}), ("screen.key", {"name": "back"}),
                               ("screen.launch", {"package": SETTINGS}), ("screen.tree", {}), ("screen.shot", {})):
            _refused_while_pairing(lt, method, params)
        time.sleep(1)
        assert _hello_paired(newkey) is False and pins(d) == pins0  # nothing new is paired
        assert _confirm_screen(d) is not None  # still waiting for a person
        _tap_confirm(d, "Reject")
        assert c.call("link.pair_wait", {}) == {"accepted": False, "outcome": "rejected"}
    finally:
        c.close()
        d.call("debug_close_pairing")
        d.call("debug_allow_local_pairing", "on" if local0 else "off")
        d.call("debug_pair_reset")
        d.shell("cmd", "statusbar", "collapse")
        d.shell("input", "keyevent", "HOME")
    assert _hello_paired(newkey) is False and pins(d) == pins0
    tr = tree(lt)  # the request is over and the screen gone: Screen control works again
    assert tr["nodes"], tr


def test_no_crash(lt, d):
    assert crash_log(d) == ""
