"""The phone's "Wallpaper & style" screen end to end on the test AVD (HOMALAWN_SERIAL=emulator-5560):
a real `homalawn serve` process, the AVD paired into a temp config, and a temp stand-in for Omarchy's
`current` folder (catppuccin).

The service pushes the theme's wallpapers and gradients to the phone (format 3: the whole look
model); the test opens Lawnchair's settings, then "Wallpaper & style", and works the screen as
a person would. The screen always shows a Lock screen panel and a Home screen panel (no "Same on
both" since 2026-10-17). Taps only change the draft: in the Lock screen panel Gradients, Glow and
a darker layer change nothing on the phone until Apply; then only the lock screen changes, in one
snapshot, and look.json keeps it as the window writes it (lock gradient with its layer). A home
screen gradient hides the Parallax switch; that draft, dropped with Discard, changes nothing. The
same theme wallpaper on both screens and Parallax, Apply: both screens get it in one snapshot, the
home picture is twice the screen's width and the launcher scrolls its wallpaper; undo takes that
back.
A second test plays the computer itself on the real link: a restarted Homa Lawn asks for the whole
list (needs_list) until one in the current format arrives, and says its install time in the ping; on
a Polish phone every label stays English; a computer from before format 3 gets no layer or
parallax controls and a per-screen choice; a failure because the wallpaper cannot be saved first
shows only the "Replace current wallpaper?" question, Cancel keeps the draft and Replace sends the
same choice with overwrite.
Screenshots (light and dark) go to HOMALAWN_WALL_SHOTS (default tests/build/phone-wallpaper).
Nothing real on this computer is touched: no ~/.local/state/omarchy, ~/.config/homalawn or ~/.cache."""
import html
import json
import os
import re
import shutil
import subprocess
import time
from pathlib import Path

import io

import pytest
from PIL import Image, ImageStat

from homalawn import look as look_mod, lookconf, prefs as prefs_mod, snapshots
from homalawn.session import LOCK_FOLLOWS_HOME, LOCK_WALLPAPER_FILE, WALLPAPER_FILE
from homalawn.session import Launcher
from test_device_follow import THEMES, _start_service, ask, background, looks
from test_device_shell_apply import phone  # noqa: F401  (the fixture)

pytestmark = pytest.mark.device
SERIAL = os.environ.get("HOMALAWN_SERIAL")
if not SERIAL:
    pytest.skip("HOMALAWN_SERIAL not set", allow_module_level=True)

from conftest import DEVICE_PACKAGE as PKG  # noqa: E402
SHOTS = Path(os.environ.get("HOMALAWN_WALL_SHOTS") or Path(__file__).parent / "build" / "phone-wallpaper")


def adb(*args, check=True) -> str:
    return subprocess.run(["adb", "-s", SERIAL, *args], check=check, capture_output=True, text=True).stdout


def shot(name: str, where: Path | None = None) -> Path:
    where = where or SHOTS
    where.mkdir(parents=True, exist_ok=True)
    p = where / f"{name}.png"
    p.write_bytes(subprocess.run(["adb", "-s", SERIAL, "exec-out", "screencap", "-p"], check=True,
                                 capture_output=True).stdout)
    return p


def nodes() -> list[dict]:
    adb("shell", "uiautomator", "dump", "/sdcard/hl-ui.xml")
    xml = adb("shell", "cat", "/sdcard/hl-ui.xml")
    out = []
    for m in re.finditer(r"<node [^>]*>", xml):
        a = {k: html.unescape(v) for k, v in re.findall(r'(\w[\w-]*)="([^"]*)"', m.group(0))}  # "Wallpaper &amp; style"
        b = [int(v) for v in re.findall(r"\d+", a.get("bounds", ""))]
        if len(b) == 4:
            a["center"] = ((b[0] + b[2]) // 2, (b[1] + b[3]) // 2)
            out.append(a)
    return out


def find(pred, timeout=10.0) -> dict:
    end = time.time() + timeout
    while True:
        for n in nodes():
            if pred(n):
                return n
        if time.time() > end:
            raise AssertionError("not on screen: " + ", ".join(
                sorted({n.get("text") or n.get("content-desc") for n in nodes()} - {""})))
        time.sleep(0.5)


def tap(n: dict) -> None:
    adb("shell", "input", "tap", str(n["center"][0]), str(n["center"][1]))


ME: dict = {}   # "fp": the session computer's fingerprint (each test sets it): its list is walls/<fp>/


def _own_the_look(d, link_transport) -> None:
    """The screen shows the list of the computer the look comes from; the session computer is it
    (the look book is emptied, then holds one look from this computer)."""
    ME["fp"] = link_transport._identity().fingerprint
    d.call("debug_look_reset")
    d.call("debug_look_put", json.dumps({"fp": ME["fp"], "key": "session", "changed_at": int(time.time() * 1000), "theme": "x"}))


def walls_dir() -> str:
    return f"files/omalawn/walls/{ME['fp']}"


def index() -> dict:
    out = adb("shell", "run-as", PKG, "cat", f"{walls_dir()}/index.json", check=False)
    return json.loads(out) if out.strip().startswith("{") else {}


def mean(png: bytes) -> tuple[float, ...]:
    return tuple(ImageStat.Stat(Image.open(io.BytesIO(png)).convert("RGB").resize((32, 64))).mean)


def screens(la) -> tuple[tuple, tuple]:
    """The average colour of the home and the lock screen wallpaper, as the phone holds them."""
    files = la._backup_files(("home", "lock"))
    home = mean(files[WALLPAPER_FILE])
    return home, home if LOCK_FOLLOWS_HOME in files else mean(files[LOCK_WALLPAPER_FILE])


def near(a, b, tol=6.0) -> bool:
    return max(abs(x - y) for x, y in zip(a, b)) <= tol


def wait(pred, timeout, what):
    end = time.time() + timeout
    while time.time() < end:
        v = pred()
        if v:
            return v
        time.sleep(0.3)
    pytest.fail(f"{what} did not happen within {timeout:.0f} s")


def by_text(t: str) -> dict:
    return find(lambda n: n.get("text") == t)


def desc(prefix: str, timeout=10.0) -> dict:
    return find(lambda n: (n.get("content-desc") or "").startswith(prefix), timeout)


def enabled(label: str) -> bool:
    """A button (Compose puts the text in a child of the clickable node) is enabled. The Apply bar
    shows only while there is a draft: no such button is not enabled."""
    ns = nodes()
    t = next((n for n in ns if n.get("text") == label), None)
    if t is None:
        return False
    box = [n for n in ns if n.get("clickable") == "true" and _inside(t, n)]
    return bool(box) and box[-1].get("enabled") == "true"


def _inside(n: dict, row: dict) -> bool:
    a = [int(v) for v in re.findall(r"\d+", n["bounds"])]
    b = [int(v) for v in re.findall(r"\d+", row["bounds"])]
    return b[0] <= a[0] and b[1] <= a[1] and a[2] <= b[2] and a[3] <= b[3]


def choose(prefix: str) -> None:
    """Tap a wallpaper or gradient tile until it shows as selected (it may sit under the bottom bar)."""
    for _ in range(4):
        n = reveal(lambda m: (m.get("content-desc") or "").startswith(prefix))
        b = [int(v) for v in re.findall(r"\d+", n["bounds"])]
        y = (b[1] + min(b[3], 2100)) // 2
        adb("shell", "input", "tap", str(n["center"][0]), str(y))
        time.sleep(0.8)
        if any((m.get("content-desc") or "").startswith(prefix) and ", selected" in m.get("content-desc", "")
               for m in nodes()):
            return
        scroll_down()
    pytest.fail(f"{prefix} could not be selected")


def scroll_down():
    adb("shell", "input", "swipe", "540", "1700", "540", "800", "400")
    time.sleep(0.8)


def scroll_up():
    for _ in range(2):
        adb("shell", "input", "swipe", "540", "700", "540", "1900", "300")
    time.sleep(0.8)


def to_top():
    for _ in range(4):
        adb("shell", "input", "swipe", "540", "500", "540", "2000", "200")
    time.sleep(1)


def visible(n: dict, ns: list[dict]) -> bool:
    """On screen and above the bottom bar (Apply / Discard) while there is one; since patch 0034
    the bar shows only with a draft, and without it the list's last row (Parallax) ends lower."""
    bar = [int(re.findall(r"\d+", b["bounds"])[1]) for b in ns if b.get("text") in ("Apply", "Discard")]
    return 250 < n["center"][1] < (min(bar) if bar else 2300)


def reveal(pred, tries=16) -> dict:
    """The first node matching `pred` that is on screen, scrolling down (then up) for it. Since
    patch 0034 the Source card sits above the panels, so the Home screen panel is further down."""
    for i in range(tries):
        ns = nodes()
        hit = [n for n in ns if pred(n) and visible(n, ns)]
        if hit:
            return hit[0]
        if i < tries // 2:
            scroll_down()
        else:
            scroll_up()
    shot("reveal-not-found")
    raise AssertionError("not found while scrolling; on screen: " + " | ".join(
        f"{n.get('text') or n.get('content-desc')}@{n['center'][1]}" for n in nodes()
        if n.get("text") or n.get("content-desc")))


def heading(title: str) -> dict:
    """A panel's heading, scrolled near the top so the panel shows below it."""
    n = reveal(lambda m: m.get("text") == title)
    if n["center"][1] > 700:
        y = n["center"][1]
        adb("shell", "input", "swipe", "540", str(y), "540", "450", "600")
        time.sleep(1)
        ns = nodes()
        if not [m for m in ns if m.get("text") == title and visible(m, ns)]:
            # the list ended first and left the heading under the top bar: back a little
            adb("shell", "input", "swipe", "540", "800", "540", "1100", "600")
            time.sleep(1)
        n = reveal(lambda m: m.get("text") == title)
    return n


def in_panel(title: str, text: str) -> dict:
    """The node with `text` in the panel under heading `title` (the first one below it)."""
    h = heading(title)
    ns = nodes()
    below = sorted((n for n in ns if n.get("text") == text and n["center"][1] > h["center"][1] and visible(n, ns)),
                   key=lambda n: n["center"][1])
    assert below, f"{text} not under {title}"
    return below[0]


def slide(prefix: str, share: float) -> None:
    """Tap the darken / lighten slider described by `prefix` at `share` of its width."""
    n = reveal(lambda m: (m.get("content-desc") or "") == prefix)
    b = [int(v) for v in re.findall(r"\d+", n["bounds"])]
    adb("shell", "input", "tap", str(int(b[0] + (b[2] - b[0]) * share)), str(n["center"][1]))
    time.sleep(0.8)


def switch_near(text: str) -> dict:
    """The switch on the row that says `text`."""
    t = reveal(lambda m: m.get("text") == text)
    sw = [n for n in nodes() if n.get("checkable") == "true" and abs(n["center"][1] - t["center"][1]) < 120]
    assert sw, f"no switch next to {text}"
    return sw[0]


def stored_home() -> Image.Image:
    return Image.open(io.BytesIO(Launcher(_LINK[0])._backup_files(("home",))[WALLPAPER_FILE]))


_LINK: list = []


def test_lock_and_home_panels_are_a_draft_and_applied_per_screen(avd_hygiene, d, link_transport, tmp_path, monkeypatch):
    _own_the_look(d, link_transport)
    dark = THEMES / "catppuccin"
    totoro_file = background("catppuccin", "1-totoro")
    if totoro_file is None or not (dark / "colors.toml").is_file():
        pytest.skip("the catppuccin Omarchy theme is not installed here")
    d = avd_hygiene.transport.device
    avd_hygiene.refresh()
    avd_hygiene.wait_loaded()
    saved = avd_hygiene.backup_wallpaper(prefix="wall-test-saved-", target="both")
    night = adb("shell", "cmd", "uimode", "night").strip()
    adb("shell", "cmd", "uimode", "night", "no")
    la = Launcher(link_transport)
    _LINK[:] = [link_transport]
    la.refresh()
    la.wait_loaded()
    screen = la.screen_size()
    scrolling0 = prefs_mod.current(link_transport)["shared"].get(look_mod.PARALLAX_PREF, {}).get("value")
    seed = tmp_path / "seed.png"
    Image.new("RGB", (400, 200), (30, 60, 200)).save(seed)
    la.set_wallpaper(str(seed), backup=False, target="both")   # a first picture, so the link can write wallpapers

    current = tmp_path / "current"
    current.mkdir()
    (current / "theme.name").write_text("catppuccin\n")
    (current / "background").symlink_to(totoro_file)
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))   # the service's, never ~/.cache
    svc, log, sock, run_dir = _start_service(tmp_path, current)
    name = link_transport.label
    look_json = Path(os.environ["HOMALAWN_CONFIG"]).parent / "look.json"

    def me():
        return [p for p in ask(sock, "phones") if p["name"] == name][0]

    def saved_look():
        return json.loads(look_json.read_text())[link_transport.peer.fp]

    def open_screen():
        adb("shell", "am", "force-stop", "com.android.settings", check=False)
        # a fresh task: no screen or draft left from before
        adb("shell", "am", "start", "-W", "-f", "0x10008000", "-n", f"{PKG}/app.lawnchair.ui.preferences.PreferenceActivity")
        tap(by_text("Wallpaper & style"))
        by_text("Omarchy theme: Catppuccin")
        time.sleep(1.5)

    before = looks(la)
    try:
        wait(lambda: me()["connected"], 30, "the service connecting to the phone")
        # light / dark stays the phone's own, so the screenshots show both looks of the screen
        first = ask(sock, "look.set", phone=name,
                    settings={"source": "follow", "sync": {"colors": True, "mode": False, "icons": False}})
        assert first["text"].startswith(f"Wallpaper & style applied (Catppuccin, {totoro_file.name}"), first
        idx = wait(lambda: (lambda i: i if i.get("theme") == "Catppuccin" and i.get("gradients") else None)(index()),
                   20, "the wallpaper list reaching the phone")
        assert idx["format"] == 3 and idx["wallpaper"] == {"same": False, "parallax": False}   # always two screens
        by_name = {i["name"]: i for i in idx["items"]}
        grads = {g["name"]: g for g in idx["gradients"]}
        assert {"1-totoro", "2-waves", "3-blue-eye"} <= set(by_name)
        assert list(grads) == ["Fade", "Diagonal", "Glow", "Duo", "Mesh", "Vignette"]
        assert all(i.get("wide") for i in idx["items"]) and not any(g.get("wide") for g in idx["gradients"])
        totoro = {"kind": "wallpaper", "id": by_name["1-totoro"]["id"]}
        assert idx["screens"] == {"home": totoro, "lock": totoro}
        keys = [k for i in idx["items"] + idx["gradients"] for k in (i["thumb"], i.get("wide")) if k]
        wait(lambda: (lambda ls: all(f"{k}.jpg" in ls for k in keys))(
            adb("shell", "run-as", PKG, "ls", walls_dir())), 40, "every thumbnail reaching the phone")

        open_screen()
        desc("Lock screen preview")
        desc("Home screen preview")
        texts = {n.get("text") for n in nodes()}
        assert not {"Same on both", "Separate", "Lock and home screens"} & texts, texts
        assert not enabled("Apply")
        shot("v3-light-panels")
        heading("Home screen")
        reveal(lambda n: n.get("text") == "Parallax")      # Home on Theme wallpapers: the switch shows
        shot("v3-light-home-panel-parallax")
        to_top()

        # in the Lock screen panel: Gradients, Glow, darker. A draft; nothing reaches the phone
        wp0 = d.call("wallpaper_info")
        home0, lock0 = screens(la)
        n0 = len(looks(la))
        heading("Lock screen")
        heading("Home screen")
        to_top()
        tap(in_panel("Lock screen", "Gradients"))
        choose("Lock screen: Glow gradient")
        slide("Lock screen: Darken / lighten", 0.75)
        assert enabled("Apply") and enabled("Discard")
        to_top()
        shot("v3-light-lock-glow-draft")
        heading("Home screen")
        shot("v3-light-home-panel")
        time.sleep(2)
        assert d.call("wallpaper_info") == wp0 and len(looks(la)) == n0
        assert saved_look()["wallpaper"]["lock"]["kind"] == "wallpaper"   # the draft reached nothing saved

        # Apply: only the lock screen changes, one snapshot, kept in look.json as the window keeps it
        tap(by_text("Apply"))

        def lock_glow():
            w = saved_look()["wallpaper"]
            return (w["home"]["kind"] == "wallpaper" and w["lock"]["kind"] == "gradient"
                    and w["lock"]["style"] == "glow" and 25 <= w["lock"]["layer"] <= 75) and w
        w = wait(lock_glow, 20, "the choice reaching the computer")
        assert w["home"]["layer"] == 0 and w["parallax"] is False
        wait(lambda: index().get("screens", {}).get("lock") == {"kind": "gradient", "id": grads["Glow"]["id"]}
             and index().get("wallpaper", {}).get("same") is False, 20, "the phone hearing it applied")
        wp1 = d.call("wallpaper_info")
        assert wp1["system_id"] == wp0["system_id"] and wp1["lock_id"] != wp0["lock_id"]
        home1, lock1 = screens(la)
        assert near(home1, home0) and not near(lock1, lock0), (home0, home1, lock0, lock1)
        assert len(looks(la)) == n0 + 1
        assert me()["look"]["wallpaper"] == w            # what the window reads
        to_top()
        shot("v3-light-lock-glow-applied")

        # a gradient on the home screen: no Parallax switch (it scrolls only a theme wallpaper);
        # Discard drops that draft and nothing changes
        tap(in_panel("Home screen", "Gradients"))
        choose("Home screen: Fade gradient")
        assert enabled("Apply")
        heading("Home screen")
        for _ in range(3):
            assert "Parallax" not in {n.get("text") for n in nodes()}
            scroll_down()
        shot("v3-light-home-gradient-no-parallax")
        tap(by_text("Discard"))
        wait(lambda: not any((n.get("content-desc") or "").startswith("Home screen: Fade gradient, selected")
                             for n in nodes()), 5, "the draft going")
        assert not enabled("Apply")
        time.sleep(2)
        assert d.call("wallpaper_info") == wp1 and len(looks(la)) == n0 + 1

        # the same theme wallpaper on both screens, Parallax, Apply: both screens in one snapshot,
        # the home picture wide and the launcher scrolling it
        to_top()
        tap(in_panel("Lock screen", "Theme wallpapers"))
        choose("Lock screen: 2-waves")
        tap(in_panel("Home screen", "Theme wallpapers"))      # it still shows Gradients from before Discard
        choose("Home screen: 2-waves")
        tap(switch_near("Parallax"))
        assert switch_near("Parallax").get("checked") == "true"
        to_top()
        shot("v3-light-waves-parallax-draft")
        tap(by_text("Apply"))
        waves = {"kind": "wallpaper", "id": by_name["2-waves"]["id"]}
        wait(lambda: index().get("screens") == {"home": waves, "lock": waves}
             and index().get("wallpaper") == {"same": False, "parallax": True}, 25, "both screens showing 2-waves")
        home2, lock2 = screens(la)
        assert not near(home2, home1) and not near(lock2, lock1), (home1, home2, lock1, lock2)
        assert len(looks(la)) == n0 + 2
        sl = saved_look()
        assert "same" not in sl["wallpaper"] and sl["wallpaper"]["parallax"] is True
        assert sl["wallpaper"]["home"]["kind"] == sl["wallpaper"]["lock"]["kind"] == "wallpaper"
        pick = sl["picks"]["catppuccin"]
        assert pick["home"].endswith("/" + background("catppuccin", "2-waves").name) and pick["lock"] == pick["home"]   # each screen its own pick
        assert stored_home().size == look_mod.parallax_size(screen)
        assert str(prefs_mod.current(link_transport)["shared"][look_mod.PARALLAX_PREF]["value"]).lower() == "true"
        time.sleep(1)
        shot("v3-light-waves-parallax-applied")

        # undo: the pictures and the settings go back to the separate lock screen gradient
        ask(sock, "undo", phone=name)
        w = saved_look()["wallpaper"]
        assert w["lock"]["kind"] == "gradient" and w["parallax"] is False
        home3, lock3 = screens(la)
        assert near(home3, home1) and near(lock3, lock1), (home1, home3, lock1, lock3)
        wait(lambda: index().get("screens", {}).get("lock", {}).get("kind") == "gradient", 15,
             "the phone hearing about the undo")
        assert len(looks(la)) == n0 + 1

        # dark: the screen opened afresh (the launcher follows the dark catppuccin theme)
        adb("shell", "cmd", "uimode", "night", "yes")
        adb("shell", "input", "keyevent", "KEYCODE_BACK")
        time.sleep(1)
        open_screen()
        shot("v3-dark-panels")
        heading("Lock screen")
        shot("v3-dark-lock-panel")
        heading("Home screen")
        shot("v3-dark-home-panel")
        reveal(lambda n: n.get("text") == "Parallax")
        shot("v3-dark-home-panel-parallax")

        # sync off: the screen says so
        ask(sock, "look.set", phone=name, settings={"source": "off"})
        wait(lambda: index().get("enabled") is False, 15, "the phone hearing that sync is off")
        adb("shell", "input", "keyevent", "KEYCODE_BACK")
        tap(by_text("Wallpaper & style"))
        find(lambda n: (n.get("text") or "").startswith("Wallpaper & style is off for this phone on "))
        shot("v3-sync-off")
    finally:
        adb("shell", "input", "keyevent", "KEYCODE_HOME", check=False)
        adb("shell", "cmd", "uimode", "night", "yes" if "yes" in night else "no", check=False)
        try:
            for _ in range(len(looks(la)) - len(before)):
                ask(sock, "undo", phone=name)
        finally:
            svc.terminate()
            try:
                svc.wait(10)
            except subprocess.TimeoutExpired:
                svc.kill()
            log.close()
            avd_hygiene.restore(saved, force=True, no_backup=True)
            snapshots.delete(saved)
            shutil.rmtree(run_dir, ignore_errors=True)
            if scrolling0 is not None:
                la.set_pref(look_mod.PARALLAX_PREF, str(scrolling0).lower())
    assert looks(la) == before


def _relink(t, timeout=60):
    """The link again after Lawnchair restarted."""
    end = time.time() + timeout
    while True:
        t.close()
        try:
            return t.call("launcher.ping", {})
        except Exception:
            if time.time() > end:
                raise
            time.sleep(1)


def _view(dark: Path):
    """A screen's contents as the computer would send them: three of the theme's pictures, both
    screens showing the first."""
    from homalawn import phonewall
    items = []
    for name in ("1-totoro", "2-waves", "3-blue-eye"):
        p = str(next((dark / "backgrounds").glob(name + ".*")))
        pid = phonewall.picture_id(p)
        items.append(phonewall.Item(pid, name, p, pid[:16]))
    first = {"kind": "wallpaper", "id": items[0].id}
    return phonewall.View(True, theme="Catppuccin", slug="catppuccin", items=items, current=items[0].id,
                          screens={"home": first, "lock": first}), items


ENGLISH = {"Lock screen", "Keep current", "Theme wallpapers", "Gradients"}
REPLACE_BODY = "The wallpaper set on this phone can't be backed up, so Undo can't bring it back."
DRAFT_BAR = {"Apply", "Discard"}  # shown only while there is a draft (patch 0034)


def screen_texts(scrolls=4) -> set:
    """Every text on the Wallpaper & style screen, top to bottom (the Source card comes first, so
    the panels are below the fold since patch 0034); left at the top again."""
    to_top()
    texts = {n.get("text") for n in nodes()}
    for _ in range(scrolls):
        scroll_down()
        texts |= {n.get("text") for n in nodes()}
    to_top()
    return texts


def test_restart_english_older_service_and_unsaved_asks_only_replace(avd_hygiene, d, link_transport):
    _own_the_look(d, link_transport)
    from homalawn import daemon, phonewall
    dark = THEMES / "catppuccin"
    if background("catppuccin", "1-totoro") is None:
        pytest.skip("the catppuccin Omarchy theme is not installed here")
    t = link_transport
    v, items = _view(dark)

    def open_screen():
        adb("shell", "am", "start", "-W", "-f", "0x10008000", "-n", f"{PKG}/app.lawnchair.ui.preferences.PreferenceActivity")
        tap(by_text("Wallpaper & style"))
        by_text("Omarchy theme: Catppuccin")
        time.sleep(1.5)
    try:
        # a Polish phone: every word on the screen stays English
        adb("shell", "cmd", "locale", "set-app-locales", PKG, "--locales", "pl-PL")
        # Homa Lawn restarts (an update does the same): no list from this process yet
        adb("shell", "am", "force-stop", PKG)
        time.sleep(2)
        adb("shell", "input", "keyevent", "KEYCODE_HOME")
        ping = _relink(t)
        assert isinstance(ping.get("installed_at"), int) and ping["installed_at"] > 0, ping
        assert phonewall.poll(t) == (None, True)
        # the older format an older build was sent: still asking
        t.call(phonewall.SET, {"enabled": True, "theme": "Catppuccin", "current_id": items[0].id, "message": None,
                               "target": "both", "items": phonewall._rows(items), "thumbs": []})
        assert phonewall.poll(t)[1] is True

        # a computer from before format 3: the per-screen list; no layer or parallax controls
        t.call(phonewall.SET, {**phonewall._base(v, 2), "thumbs": []})
        assert phonewall.poll(t) == (None, False)
        open_screen()
        texts = screen_texts()
        assert ENGLISH <= texts and "Zastosuj" not in texts, texts
        to_top()
        for _ in range(4):
            scroll_down()
        texts = {n.get("text") for n in nodes()}
        assert "Darken / lighten" not in texts and "Parallax" not in texts, texts
        shot("v3-older-service")
        adb("shell", "input", "keyevent", "KEYCODE_BACK")

        phonewall.push(t, v)
        assert phonewall.poll(t) == (None, False)
        assert index()["screens"]["home"] == {"kind": "wallpaper", "id": items[0].id} and index()["format"] == 3

        open_screen()
        texts = screen_texts()
        assert ENGLISH <= texts and "Zastosuj" not in texts and "Odrzuć" not in texts, texts
        assert not {"Same on both", "Separate"} & texts, texts
        reveal(lambda n: n.get("text") == "Parallax")
        assert {"Darken / lighten", "Parallax", "lighter", "darker"} <= {n.get("text") for n in nodes()}
        shot("v3-polish-phone-english")
        to_top()
        choose("Lock screen: 2-waves")
        choose("Home screen: 2-waves")
        texts = {n.get("text") for n in nodes()}  # a draft now: the Apply bar, in English too
        assert DRAFT_BAR <= texts and not {"Zastosuj", "Odrzuć"} & texts, texts
        to_top()
        want = {"home": {"kind": "wallpaper", "id": items[1].id}, "lock": {"kind": "wallpaper", "id": items[1].id}}

        def failed_unsaved() -> dict:
            tap(by_text("Apply"))
            c = wait(lambda: phonewall.poll(t)[0], 10, "the choice waiting for the computer")
            assert c["screens"] == want and "overwrite" not in c, c
            assert c["format"] == 3 and c["wallpaper"]["parallax"] is False, c
            raw = json.loads(adb("shell", "run-as", PKG, "cat", f"{walls_dir()}/choice.json"))
            assert raw["wallpaper"] == {"parallax": False}, raw          # the phone sends no `same` any more
            assert c["layer"] == {"home": 0, "lock": 0}, c
            phonewall.ack(t, c["at"], "received")
            phonewall.ack(t, c["at"], "failed", message=daemon.MSG_PICK_UNSAVED, code=daemon.WALLPAPER_UNSAVED)
            by_text("Replace current wallpaper?")
            texts = {n.get("text") for n in nodes()}
            # the question and its one-line reason (DESIGN.md 2.1, patch 0034), no message from the computer
            assert daemon.MSG_PICK_UNSAVED not in texts, texts
            assert {t for t in texts if t} == {"Replace current wallpaper?", REPLACE_BODY, "Cancel", "Replace"}, texts
            return c

        failed_unsaved()
        shot("v3-replace-dialog")
        tap(by_text("Cancel"))
        wait(lambda: "Replace current wallpaper?" not in {n.get("text") for n in nodes()}, 5, "the question going")
        assert enabled("Apply"), "Cancel dropped the draft"
        # the panels sit below the Source card (patch 0034): scroll to the choice
        reveal(lambda n: (n.get("content-desc") or "").startswith("Lock screen: 2-waves, selected"))
        to_top()
        assert phonewall.poll(t)[0] is None, "Cancel sent something"

        failed_unsaved()
        tap(by_text("Replace"))
        c = wait(lambda: phonewall.poll(t)[0], 10, "the Replace reaching the computer")
        assert c["screens"] == want and c.get("overwrite") is True, c
        phonewall.ack(t, c["at"], "applied", current_id=items[1].id)
        wait(lambda: not enabled("Apply"), 5, "the applied choice settling the draft")
        shot("v3-replace-applied")
    finally:
        adb("shell", "cmd", "locale", "set-app-locales", PKG, check=False)
        adb("shell", "input", "keyevent", "KEYCODE_HOME", check=False)


def test_a_choice_on_the_phone_shows_in_the_window(phone, d, link_transport, tmp_path):
    """The lock screen's Glow gradient with a darker layer and parallax on the home screen,
    chosen and applied on the phone, are what the window's Wallpaper & style page shows (a private
    service and the offscreen shell; HOMALAWN_APPLY_SHOTS gets its picture)."""
    _own_the_look(d, link_transport)
    from test_device_shell_apply import Window
    d, la = phone
    w = Window(tmp_path, link_transport.label)
    try:
        w.wait_connected()
        w.ask("look.set", phone=w.name, settings={"source": "chosen", "theme": "Catppuccin",
                                                  "sync": {"colors": True, "mode": False, "icons": False}})
        # this service's list, with its gradients: an earlier test may have left a Catppuccin list without any
        idx = wait(lambda: (lambda i: i if i.get("theme") == "Catppuccin" and i.get("format") == 3
                            and any(g["name"] == "Glow" for g in i.get("gradients") or []) else None)(index()),
                   20, "the wallpaper list reaching the phone")
        glow = next(g for g in idx["gradients"] if g["name"] == "Glow")
        wait(lambda: f"{glow['thumb']}.jpg" in adb("shell", "run-as", PKG, "ls", walls_dir()), 30,
             "the thumbnails reaching the phone")
        adb("shell", "am", "start", "-W", "-f", "0x10008000", "-n", f"{PKG}/app.lawnchair.ui.preferences.PreferenceActivity")
        tap(by_text("Wallpaper & style"))
        by_text("Omarchy theme: Catppuccin")
        time.sleep(1.5)
        tap(in_panel("Lock screen", "Gradients"))
        choose("Lock screen: Glow gradient")
        slide("Lock screen: Darken / lighten", 0.75)
        tap(switch_near("Parallax"))
        tap(by_text("Apply"))

        def chosen():
            wp = w.ask("look.get", phone=w.name)["wallpaper"]
            return (wp["parallax"] is True and wp["lock"]["kind"] == "gradient"
                    and wp["lock"]["style"] == "glow") and wp
        wp = wait(chosen, 30, "the phone's choice reaching the service")
        assert 25 <= wp["lock"]["layer"] <= 75 and wp["home"]["kind"] == "wallpaper"
        wait(lambda: index().get("wallpaper") == {"same": False, "parallax": True}, 30, "the phone hearing it applied")

        w.open_look()
        f = w.wait_for(lambda: (lambda x: x if x.get("parallax") is True else None)(w.facts()),
                       "the window never showed the phone's choice")
        assert f["parallax"] is True and f["changed"] is False
        assert f["screens"]["lock"]["kind"] == "gradient" and f["screens"]["lock"]["style"] == "glow"
        assert f["layers"]["lock"] == wp["lock"]["layer"] and f["layers"]["home"] == 0
        w.shot("look", "window-after-phone-choice.png")
        w.call("lookEdit", '{"scroll": 700}')
        time.sleep(1)
        w.shot("look", "window-after-phone-choice-panels.png")
    finally:
        adb("shell", "input", "keyevent", "KEYCODE_HOME", check=False)
        w.close()


KEEP_BOTH = {"home": {"kind": "keep"}, "lock": {"kind": "keep"}}
PIC = "1" * 16


def _list(theme: str) -> dict:
    return {"enabled": True, "theme": theme, "format": 3, "items": [{"id": PIC, "name": "One", "thumb": PIC}],
            "gradients": [], "screens": KEEP_BOTH, "layer": {"home": 0, "lock": 0}, "wallpaper": {"parallax": False}}


def test_lists_and_choices_are_per_computer(link_transport, second, second_transport, d):
    me = link_transport._identity().fingerprint
    link_transport.call("launcher.theme_wallpapers_set", _list("A"))
    second_transport.call("launcher.theme_wallpapers_set", _list("B"))
    assert link_transport.call("launcher.theme_wallpaper_choice")["needs_list"] is False
    walls = d.call("debug_walls")
    assert walls[me]["theme"] == "A" and walls[second.fingerprint]["theme"] == "B"
    # a choice belongs to the computer that made it: it is read by that one only, and only it can ack it
    want = {"screens": {"home": {"kind": "wallpaper", "id": PIC}, "lock": {"kind": "keep"}}}
    assert d.call("debug_walls_choice", f"{me} {json.dumps(want)}")["applied"] is True
    c = link_transport.call("launcher.theme_wallpaper_choice")["choice"]
    assert c["screens"]["home"] == {"kind": "wallpaper", "id": PIC}
    assert second_transport.call("launcher.theme_wallpaper_choice")["choice"] is None
    second_transport.call("launcher.theme_wallpaper_ack", {"at": c["at"], "state": "received"})
    assert link_transport.call("launcher.theme_wallpaper_choice")["choice"] is not None
    link_transport.call("launcher.theme_wallpaper_ack", {"at": c["at"], "state": "received"})
    assert link_transport.call("launcher.theme_wallpaper_choice")["choice"] is None
    # a forgotten computer's list and pictures go with it, the folder too
    assert adb("shell", "run-as", PKG, "ls", f"files/omalawn/walls/{second.fingerprint}", check=False).strip()
    d.call("debug_forget", second.fingerprint)
    assert second.fingerprint not in d.call("debug_walls")
    assert second.fingerprint not in adb("shell", "run-as", PKG, "ls", "files/omalawn/walls")


def test_a_damaged_look_book_is_said_on_the_screen(avd_hygiene, d):
    d.call("debug_look_corrupt")
    try:
        adb("shell", "am", "force-stop", "com.android.settings", check=False)
        adb("shell", "am", "start", "-W", "-f", "0x10008000", "-n", f"{PKG}/app.lawnchair.ui.preferences.PreferenceActivity")
        tap(by_text("Wallpaper & style"))
        by_text("The wallpaper & style stored on this phone can't be read. To clear it, forget all computers in Computers.")
    finally:
        d.call("debug_look_reset")
        adb("shell", "input", "keyevent", "KEYCODE_HOME", check=False)


def _open_wallpapers() -> None:
    adb("shell", "am", "force-stop", "com.android.settings", check=False)
    adb("shell", "am", "start", "-W", "-f", "0x10008000", "-n", f"{PKG}/app.lawnchair.ui.preferences.PreferenceActivity")
    tap(by_text("Wallpaper & style"))


def _look(d, fp, key, at, theme):
    d.call("debug_look_put", json.dumps({"fp": fp, "key": key, "changed_at": at, "theme": theme}))


def _wait_gone(d, fp, timeout=5.0):
    wait(lambda: d.call("debug_walls")[fp]["choice_at"] is None, timeout, "the choice of the old source to go")


def _fresh_list(t, theme: str) -> None:
    """The computer's list with no choice waiting: a list that shows the waiting choice done ends it."""
    shown = _list(theme)
    shown["screens"] = {"home": {"kind": "wallpaper", "id": PIC}, "lock": {"kind": "keep"}}
    t.call("launcher.theme_wallpapers_set", shown)
    t.call("launcher.theme_wallpapers_set", _list(theme))


KEEP_HOME_PIC = {"screens": {"home": {"kind": "wallpaper", "id": PIC}, "lock": {"kind": "keep"}}}


def test_the_choice_of_the_old_source_goes_when_the_source_changes_even_with_the_screen_closed(link_transport, second, d):
    me = link_transport._identity().fingerprint
    d.call("debug_look_reset")
    _look(d, me, "a1", 1, "A")
    _look(d, second.fingerprint, "b1", 2, "B")            # a new computer does not take over
    _fresh_list(link_transport, "A")
    assert d.call("debug_walls_choice", f"{me} {json.dumps(KEEP_HOME_PIC)}")["applied"] is True
    assert d.call("debug_walls")[me]["choice_at"] is not None
    d.call("debug_look_scope", second.fingerprint)         # nobody has the wallpaper screen open
    assert d.call("debug_walls")[me]["choice_at"] is None
    assert link_transport.call("launcher.theme_wallpaper_choice")["choice"] is None
    d.call("debug_look_reset")


def test_the_wallpaper_screen_names_its_source(link_transport, second, d):
    me = link_transport._identity().fingerprint
    mine = {c["fp"]: c["name"] for c in d.call("debug_computers")["computers"]}[me]
    d.call("debug_look_reset")
    _fresh_list(link_transport, "A")
    try:
        _open_wallpapers()
        by_text("No computer has sent its wallpapers yet. Open Homa Lawn on your computer.")
        shot("source-none", Path(os.environ.get("HOMALAWN_WALL_SHOTS") or SHOTS))
        _look(d, me, "a1", 1, "A")
        by_text("Latest change")                            # the Source card: Latest change, then each computer
        by_text(mine)
        by_text("Omarchy theme: A")                         # the latest change is this computer's: its list
        assert d.call("debug_walls_choice", f"{me} {json.dumps(KEEP_HOME_PIC)}")["applied"] is True
        _look(d, second.fingerprint, "b1", 2, "B")
        d.call("debug_look_scope", second.fingerprint)
        by_text("Laptop has not sent its wallpapers yet. Open Homa Lawn on that computer.")
        by_text("Wallpaper & style now comes from Laptop; choose again.")
        assert d.call("debug_walls")[me]["choice_at"] is None
    finally:
        d.call("debug_look_reset")
        adb("shell", "input", "keyevent", "KEYCODE_HOME", check=False)
