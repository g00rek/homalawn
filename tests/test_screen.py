"""Screen control on the desktop: argument parsing, tree compaction and printing, the CLI group,
screenshots over the link, the photo-access request for wallpaper backups, and ping's db=null."""
import json

import pytest
from typer.testing import CliRunner

from homalawn import cli, screen
from homalawn.adb import AdbTransport
from homalawn.transport import NotSupported, ProviderError
from test_transport import FakeRunner, linked, ok  # noqa: F401  (linked is a fixture)

PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 8 + (1080).to_bytes(4, "big") + (2400).to_bytes(4, "big")


# -- coordinates ----------------------------------------------------------------------------
@pytest.mark.parametrize("args,want", [
    (["n12"], {"node": "n12"}),
    (["540,1200"], {"x": 540, "y": 1200}),
    (["540, 1200"], {"x": 540, "y": 1200}),
    (["540x1200"], {"x": 540, "y": 1200}),
    (["540", "1200"], {"x": 540, "y": 1200}),
    (["10.5,3"], {"x": 10.5, "y": 3}),
])
def test_parse_point(args, want):
    assert screen.parse_point(args) == want


@pytest.mark.parametrize("args", [[], ["n"], ["abc"], ["1,2,3"], ["1", "2", "3"], ["-1,5"], ["x", "5"], ["n12", "5"]])
def test_parse_point_rejects(args):
    with pytest.raises(screen.ScreenArgError):
        screen.parse_point(args)


def test_parse_swipe():
    assert screen.parse_swipe(["100,1500", "100,500"]) == {"x1": 100, "y1": 1500, "x2": 100, "y2": 500}
    assert screen.parse_swipe(["1", "2", "3", "4"]) == {"x1": 1, "y1": 2, "x2": 3, "y2": 4}
    for bad in (["1,2"], ["n1", "2,3"], ["1,2", "3,4", "5"]):
        with pytest.raises(screen.ScreenArgError):
            screen.parse_swipe(bad)


def test_parse_key():
    assert screen.parse_key("Back") == "back"
    assert screen.parse_key("quick-settings") == "quick_settings"
    with pytest.raises(screen.ScreenArgError):
        screen.parse_key("power")


# -- tree -----------------------------------------------------------------------------------
def node(i, depth, cls="TextView", text=None, desc=None, click=False, edit=False, scroll=False, focus=False,
         bounds=(0, 0, 100, 50)):
    n = {"id": f"n{i}", "class": cls, "bounds": list(bounds), "click": click, "focus": focus, "edit": edit,
         "scroll": scroll, "depth": depth}
    if text is not None:
        n["text"] = text
    if desc is not None:
        n["desc"] = desc
    return n


SETTINGS_TREE = {"package": "com.android.settings", "nodes": [
    node(0, 0, "RecyclerView", scroll=True, bounds=(0, 300, 1080, 2400)),
    node(1, 1, "LinearLayout", click=True, bounds=(0, 700, 1080, 900)),
    node(2, 2, text="Network & internet"),
    node(3, 2, text="Mobile, Wi‑Fi, hotspot"),
    node(4, 1, "LinearLayout", click=True, bounds=(0, 900, 1080, 1100)),
    node(5, 2, text="Apps"),
    node(6, 1, "Button", text="OK", click=True, bounds=(100, 1200, 300, 1300)),
    node(7, 1, "EditText", text="", edit=True, focus=True),
    node(8, 1, "ImageView", desc="Back", click=True),
    node(9, 1, text="Plain", desc="Plain"),
    node(10, 1, text="Title", desc="Spoken title"),
]}


def test_compact_folds_labels_into_their_clickable_row():
    rows = screen.compact(SETTINGS_TREE["nodes"])
    ids = [r["id"] for r in rows]
    assert ids == ["n0", "n1", "n4", "n6", "n7", "n8", "n9", "n10"]  # n2, n3, n5 folded
    by = {r["id"]: r for r in rows}
    assert by["n1"]["label"] == "Network & internet · Mobile, Wi‑Fi, hotspot"
    assert by["n1"]["flags"] == ["click"] and by["n1"]["at"] == (540, 800)
    assert by["n4"]["label"] == "Apps"
    assert by["n6"]["label"] == "OK"                    # own text: nothing folded
    assert by["n7"]["flags"] == ["edit", "focus"] and by["n7"]["label"] == ""
    assert by["n8"]["label"] == "Back"
    assert by["n9"]["label"] == "Plain"                 # desc equal to text is not repeated
    assert by["n10"]["label"] == "Title (Spoken title)"


def test_compact_keeps_actionable_children_and_caps_parts():
    nodes = [node(0, 0, "FrameLayout", click=True), node(1, 1, "Switch", click=True, text="On"),
             *[node(i, 1, text=f"t{i}") for i in range(2, 7)], node(7, 0, text="after")]
    rows = screen.compact(nodes)
    by = {r["id"]: r for r in rows}
    assert by["n0"]["label"] == "t2 · t3 · t4"           # at most three parts
    assert "n1" in by and by["n1"]["label"] == "On"      # actionable child stays its own row
    assert all(f"n{i}" not in by for i in range(2, 7))
    assert by["n7"]["label"] == "after"                  # a sibling after the row is not folded


def test_compact_without_depth_field_is_flat():
    rows = screen.compact([{"id": "n0", "class": "Button", "bounds": [0, 0, 10, 10], "click": True},
                           {"id": "n1", "class": "TextView", "bounds": [0, 0, 10, 10], "text": "x"}])
    assert [r["label"] for r in rows] == ["", "x"]


def test_format_tree_indents_by_depth():
    lines = screen.format_tree(SETTINGS_TREE)
    assert lines[0] == "com.android.settings (8 items)"
    assert lines[1] == "  n0 RecyclerView [scroll] @540,1350"
    assert lines[2] == '    n1 LinearLayout "Network & internet · Mobile, Wi‑Fi, hotspot" [click] @540,800'
    assert '    n6 Button "OK" [click] @200,1250' in lines
    assert lines[-1].startswith("    n10 TextView")
    trunc = screen.format_tree({"package": "p", "nodes": [], "truncated": True})
    assert trunc == ["p (0 items)", "  … (more items than the phone sends at once)"]


def test_off_message_mentions_restricted_settings_unless_known_free():
    assert "Allow restricted settings" in screen.off_message(None)
    assert "Allow restricted settings" in screen.off_message({"enabled": False, "restricted": None})
    assert "Allow restricted settings" in screen.off_message({"enabled": False, "restricted": True})
    m = screen.off_message({"enabled": False, "restricted": False})
    assert "Allow restricted" not in m and "Permissions › Screen control" in m and "\n" not in m


# -- link transport -------------------------------------------------------------------------
def test_screenshot_over_the_link_is_screen_shot(linked):
    t, _, phone = linked
    phone.handlers["screen.shot"] = lambda p: ({"width": 1080, "height": 2400, "format": "png"}, PNG)
    assert t.screenshot() == PNG
    assert phone.methods()[-1] == "screen.shot"


def test_screenshot_over_the_link_when_screen_control_is_off(linked):
    t, _, phone = linked

    def off(p):
        raise ProviderError("screen_off: Turn on Lawnchair in Settings › Accessibility", code="screen_off")
    phone.handlers["screen.shot"] = off
    with pytest.raises(NotSupported) as e:
        t.screenshot()
    assert "Permissions › Screen control" in str(e.value) and "--via adb screenshot" in str(e.value)


def test_shot_waits_once_when_rate_limited(linked, monkeypatch):
    t, _, phone = linked
    sleeps = []
    monkeypatch.setattr("homalawn.link.client.time.sleep", sleeps.append)
    calls = []

    def shot(p):
        calls.append(1)
        if len(calls) == 1:
            raise ProviderError("rate_limited: at most one screenshot every 340 ms", code="rate_limited")
        return {"width": 1, "height": 1}, PNG
    phone.handlers["screen.shot"] = shot
    assert screen.shot(t) == PNG
    assert len(calls) == 2 and sleeps.count(screen.SHOT_RETRY_S) == 1  # time.sleep is patched for every thread


def test_link_screenshot_waits_once_when_rate_limited(linked, monkeypatch):
    """`homalawn screenshot` over the link goes straight to the transport: it gets the same retry."""
    t, _, phone = linked
    sleeps = []
    monkeypatch.setattr("homalawn.link.client.time.sleep", sleeps.append)
    calls = []

    def shot(p):
        calls.append(1)
        if len(calls) == 1:
            raise ProviderError("rate_limited: try again in 200 ms", code="rate_limited")
        return {"width": 1, "height": 1}, PNG
    phone.handlers["screen.shot"] = shot
    assert t.screenshot() == PNG
    assert len(calls) == 2 and sleeps.count(screen.SHOT_RETRY_S) == 1 and screen.SHOT_RETRY_S == 0.4


def test_link_screenshot_retries_only_once(linked, monkeypatch):
    t, _, phone = linked
    sleeps = []
    monkeypatch.setattr("homalawn.link.client.time.sleep", sleeps.append)
    calls = []

    def shot(p):
        calls.append(1)
        raise ProviderError("rate_limited: try again in 200 ms", code="rate_limited")
    phone.handlers["screen.shot"] = shot
    with pytest.raises(ProviderError) as e:
        screen.shot(t)
    assert e.value.code == "rate_limited" and len(calls) == 2 and sleeps.count(screen.SHOT_RETRY_S) == 1


def test_screen_status_over_adb_is_the_provider_method():
    r = FakeRunner([ok('{"enabled":true,"restricted":null}')])
    assert AdbTransport("emu", r, package="app.homalawn.debug").call("screen.status") == {"enabled": True, "restricted": None}
    assert "--method screen_status" in r.calls[0][0][-1]


def test_other_screen_methods_over_adb_point_at_pairing():
    with pytest.raises(NotSupported) as e:
        AdbTransport("emu", FakeRunner(), package="app.homalawn.debug").call("screen.tree")
    assert "homalawn pair HOST" in str(e.value)


# -- CLI ------------------------------------------------------------------------------------
@pytest.fixture
def run_cli(linked, monkeypatch):
    t, _, phone = linked
    from homalawn import ops
    monkeypatch.setattr(ops.Session, "transport", lambda self: t)
    runner = CliRunner()

    def run(*args):
        return runner.invoke(cli.app, ["screen", *args])
    return run, phone


def test_cli_tree_prints_the_compacted_list(run_cli):
    run, phone = run_cli
    phone.handlers["screen.tree"] = lambda p: (SETTINGS_TREE, None)
    r = run("tree")
    assert r.exit_code == 0, r.output
    assert '    n1 LinearLayout "Network & internet · Mobile, Wi‑Fi, hotspot" [click] @540,800' in r.output
    r = run("tree", "--json")
    assert json.loads(r.output) == SETTINGS_TREE


@pytest.mark.parametrize("args,method,params", [
    (["tap", "n3"], "screen.tap", {"node": "n3"}),
    (["tap", "540,1200"], "screen.tap", {"x": 540, "y": 1200}),
    (["tap", "540", "1200"], "screen.tap", {"x": 540, "y": 1200}),
    (["long-press", "n2", "--ms", "1200"], "screen.long_press", {"node": "n2", "ms": 1200}),
    (["swipe", "540,2000", "540,600"], "screen.swipe", {"x1": 540, "y1": 2000, "x2": 540, "y2": 600}),
    (["swipe", "1", "2", "3", "4", "--ms", "500"], "screen.swipe", {"x1": 1, "y1": 2, "x2": 3, "y2": 4, "ms": 500}),
    (["type", "hello world"], "screen.type", {"text": "hello world"}),
    (["type", "x", "--node", "n7"], "screen.type", {"text": "x", "node": "n7"}),
    (["key", "quick-settings"], "screen.key", {"name": "quick_settings"}),
    (["launch", "com.android.settings"], "screen.launch", {"package": "com.android.settings"}),
])
def test_cli_actions_send_the_right_request(run_cli, args, method, params):
    run, phone = run_cli
    phone.handlers[method] = lambda p: ({"package": p.get("package")} if "package" in p else {}, None)
    r = run(*args)
    assert r.exit_code == 0, r.output
    assert phone.log[-1][1:3] == (method, params)


def test_cli_bad_point_is_one_line_and_sends_nothing(run_cli):
    run, phone = run_cli
    r = run("tap", "somewhere")
    assert r.exit_code == 1 and "node id like n12" in r.output
    assert phone.log == []


def test_cli_screen_off_says_how_to_turn_it_on(run_cli):
    run, phone = run_cli

    def off(p):
        raise ProviderError("screen_off: Turn on Lawnchair in Settings › Accessibility", code="screen_off")
    phone.handlers["screen.tap"] = off
    phone.handlers["screen.status"] = lambda p: ({"enabled": False, "restricted": False}, None)
    r = run("tap", "1,1")
    assert r.exit_code == 1
    assert "Screen control is off" in r.output and "Allow restricted" not in r.output


def test_cli_shot_writes_the_png(run_cli, tmp_path):
    run, phone = run_cli
    phone.handlers["screen.shot"] = lambda p: ({"width": 1080, "height": 2400}, PNG)
    out = tmp_path / "s.png"
    r = run("shot", str(out))
    assert r.exit_code == 0, r.output
    assert out.read_bytes() == PNG and str(out) in r.output


def test_cli_status(run_cli):
    run, phone = run_cli
    phone.handlers["screen.status"] = lambda p: ({"enabled": True, "restricted": None}, None)
    r = run("status")
    assert r.exit_code == 0 and "Screen control is on." in r.output and "unknown" in r.output


# -- photo access for the wallpaper backup over the link -------------------------------------
def _denied(p=None):
    raise ProviderError("failed: cannot back up the current wallpaper (getWallpaperFile: SecurityException: "
                        "READ_MEDIA_IMAGES)", code="failed")


def test_wallpaper_backup_over_link_asks_once_with_a_notification(linked):
    from homalawn.session import MSG_MEDIA_ASKED, Launcher
    t, _, phone = linked
    phone.handlers["launcher.wallpaper_backup"] = _denied
    phone.handlers["launcher.request_media_permission"] = lambda p: ({"granted": False, "asked": True}, None)
    la = Launcher(t)
    with pytest.raises(NotSupported) as e:
        la.backup_wallpaper()
    assert str(e.value) == MSG_MEDIA_ASKED and "tap the Homa Lawn notification" in str(e.value)
    assert "\n" not in str(e.value)
    with pytest.raises(NotSupported):  # again in the same run: no second notification
        la.backup_wallpaper()
    assert phone.methods().count("launcher.request_media_permission") == 1


def test_wallpaper_backup_over_link_retries_once_when_already_allowed(linked):
    from homalawn.session import Launcher
    t, _, phone = linked
    tries = []

    def backup(p):
        tries.append(1)
        if len(tries) == 1:
            _denied()
        return {"how": "file"}, b"\x89PNG wallpaper"
    phone.handlers["launcher.wallpaper_backup"] = backup
    phone.handlers["launcher.request_media_permission"] = lambda p: ({"granted": True, "asked": False}, None)
    name = Launcher(t).backup_wallpaper()
    assert name.startswith("wallpaper-") and len(tries) == 2


def test_wallpaper_backup_over_link_with_notifications_off(linked):
    from homalawn.session import MSG_MEDIA_READ, Launcher
    t, _, phone = linked
    phone.handlers["launcher.wallpaper_backup"] = _denied
    phone.handlers["launcher.request_media_permission"] = lambda p: (
        {"granted": False, "asked": False, "why": "notifications_off"}, None)
    with pytest.raises(NotSupported) as e:
        Launcher(t).backup_wallpaper()
    assert str(e.value) == MSG_MEDIA_READ


def test_wallpaper_backup_over_link_where_no_prompt_can_help(linked):
    from homalawn.session import MSG_MEDIA_NOT_GRANTABLE, Launcher
    t, _, phone = linked
    phone.handlers["launcher.wallpaper_backup"] = _denied
    phone.handlers["launcher.request_media_permission"] = lambda p: (
        {"granted": False, "asked": False, "why": "not_grantable"}, None)
    with pytest.raises(NotSupported) as e:
        Launcher(t).backup_wallpaper()
    assert str(e.value) == MSG_MEDIA_NOT_GRANTABLE and "--via adb" in str(e.value) and "\n" not in str(e.value)


def test_set_wallpaper_over_link_stops_before_writing(linked, tmp_path):
    from homalawn.session import Launcher
    t, dev, phone = linked
    phone.handlers["launcher.wallpaper_backup"] = _denied
    phone.handlers["launcher.request_media_permission"] = lambda p: ({"granted": False, "asked": True}, None)
    img = tmp_path / "w.png"
    img.write_bytes(b"\x89PNG new")
    with pytest.raises(NotSupported):
        Launcher(t).set_wallpaper(str(img))
    assert "launcher.wallpaper_set" not in phone.methods() and "files/omalawn/wp" not in dev.files


# -- ping no longer opens the home screen database ------------------------------------------
def test_db_name_when_ping_has_no_db_yet(linked, monkeypatch):
    from homalawn.session import Launcher
    t, dev, phone = linked
    monkeypatch.setattr("homalawn.session.time.sleep", lambda s: None)
    pings = []

    def ping(p):
        pings.append(1)
        return {"version": "t", "package": "app.homalawn.debug", "db": None, "model_loaded": False,
                "default_home": True}, None
    phone.handlers["launcher.ping"] = ping
    phone.handlers["launcher.ensure_loaded"] = lambda p: ({"loaded": True, "db": "launcher_5_4_4.db"}, None)
    assert Launcher(t).db_name() == "launcher_5_4_4.db"
    assert "launcher.ensure_loaded" in phone.methods()


def test_compact_leaves_text_of_a_nested_button_with_it():
    nodes = [node(0, 0, "LinearLayout", click=True), node(1, 1, text="Wi-Fi"),
             node(2, 1, "Switch", click=True), node(3, 2, text="On"), node(4, 1, text="Saved")]
    by = {r["id"]: r for r in screen.compact(nodes)}
    assert by["n0"]["label"] == "Wi-Fi · Saved"
    assert by["n2"]["label"] == "On" and "n3" not in by


def test_format_tree_shows_checked_state_of_checkable_nodes():
    tree = {"package": "android", "nodes": [
        {**node(0, 0, "CheckBox", text="Always allow", click=True), "checkable": True, "checked": True},
        {**node(1, 0, "Switch", text="Wi-Fi", click=True), "checkable": True, "checked": False},
        node(2, 0, "Button", text="Create", click=True),
    ]}
    lines = screen.format_tree(tree)
    assert lines[1] == '  n0 CheckBox "Always allow" [click] [checked] @50,25'
    assert lines[2] == '  n1 Switch "Wi-Fi" [click] [unchecked] @50,25'
    assert lines[3] == '  n2 Button "Create" [click] @50,25'


def test_row_takes_the_checked_state_of_the_switch_inside_it():
    tree = {"package": "com.android.settings", "nodes": [
        node(0, 0, "LinearLayout", click=True, bounds=(0, 0, 1080, 200)),
        node(1, 1, text="Use location"),
        {**node(2, 1, "Switch"), "checkable": True, "checked": True},
    ]}
    lines = screen.format_tree(tree)
    assert lines == ["com.android.settings (1 items)", '  n0 LinearLayout "Use location" [click] [checked] @540,100']


def test_clickable_container_takes_its_label_from_the_text_inside():
    """The "Tap to finish setup" placeholder: a clickable view whose label is only further down."""
    tree = {"package": "app.homalawn.debug", "nodes": [
        node(0, 0, "FrameLayout", click=True, bounds=(0, 400, 540, 940)),
        node(1, 1, "FrameLayout"),
        node(2, 2, text="Tap to finish setup"),
    ]}
    assert screen.format_tree(tree)[1] == '  n0 FrameLayout "Tap to finish setup" [click] @270,670'


@pytest.mark.parametrize("args,line", [
    (["tap", "n3"], "Tapped n3."),
    (["tap", "540,1200"], "Tapped 540,1200."),
    (["long-press", "n2"], "Long-pressed n2."),
    (["swipe", "540,2000", "540,600"], "Swiped from 540,2000 to 540,600."),
    (["type", "hello"], "Typed 5 characters."),
    (["type", "a"], "Typed 1 character."),
    (["key", "back"], "Pressed Back."),
    (["key", "quick-settings"], "Pressed Quick settings."),
    (["launch", "com.example"], "Opened com.example."),
])
def test_cli_actions_confirm_in_one_line(run_cli, args, line):
    run, phone = run_cli
    phone.handlers[f"screen.{args[0].replace('-', '_')}"] = lambda p: ({"package": p.get("package")}, None)
    r = run(*args)
    assert r.exit_code == 0 and r.output.strip().splitlines()[-1] == line, r.output
