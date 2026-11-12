import json

import pytest

from homalawn.adb import AdbTransport
from homalawn.transport import NotSupported, ProviderError, Transport
from test_session import DB, FakeDevice


class FakeRunner:
    def __init__(self, outputs=None):
        self.calls, self.outputs = [], list(outputs or [])

    def __call__(self, argv, stdin=None):
        self.calls.append((argv, stdin))
        return self.outputs.pop(0) if self.outputs else (0, b'Result: Bundle[{ok=true, result={}}]\n', b"")



@pytest.fixture(autouse=True)
def _test_build(monkeypatch):
    """The adb transport talks to the test build here, without asking the fake phone which app it runs."""
    monkeypatch.setenv("HOMALAWN_PACKAGE", "app.homalawn.debug")

def ok(result="{}"):
    return (0, f"Result: Bundle[{{ok=true, result={result}}}]\n".encode(), b"")


# -- AdbTransport: namespaced calls onto the provider's content-call contract ----------------
def test_namespaced_call_maps_to_bare_provider_method():
    r = FakeRunner([ok('{"db":"x"}')])
    assert AdbTransport("emu", r).call("launcher.ping") == {"db": "x"}
    cmd = r.calls[0][0][-1]
    assert r.calls[0][0][:3] == ["adb", "-s", "emu"]
    assert "--method ping" in cmd and "--arg" not in cmd and "--extra" not in cmd


@pytest.mark.parametrize("method,params,arg,extras", [
    ("launcher.widget_status", {"id": 25}, "25", []),
    ("launcher.wallpaper_set", {"path": "/d/files/omalawn/wp"}, "/d/files/omalawn/wp", []),
    ("launcher.layout_import", {"path": "/d/i.db", "db": "launcher_5_4_4.db"}, "/d/i.db",
     ["db:s:launcher_5_4_4.db"]),
    ("launcher.pref_set", {"store": "shared", "key": "k", "type": "bool", "value": "true"}, None,
     ["store:s:shared", "key:s:k", "type:s:bool", "value:s:true"]),
])
def test_params_become_arg_and_string_extras_as_before(method, params, arg, extras):
    seen = []

    class Dev:
        def call(self, m, arg=None, extras=None):
            seen.append((m, arg, extras))
            return {}
    AdbTransport(device=Dev()).call(method, params)
    m, a, e = seen[0]
    assert m == method.split(".", 1)[1] and a == arg
    assert [f"{k}:s:{v}" for k, v in (e or {}).items()] == extras


def test_prefs_set_many_writes_go_as_the_same_json_arg():
    seen = []

    class Dev:
        def call(self, m, arg=None, extras=None):
            seen.append((m, arg, extras))
            return {}
    writes = [{"store": "shared", "key": "k", "type": "int", "value": "1"}, {"store": "s", "key": "x", "remove": True}]
    AdbTransport(device=Dev()).call("launcher.prefs_set_many", {"writes": writes})
    assert seen == [("prefs_set_many", json.dumps(writes), None)]


def test_other_namespaces_are_not_supported_over_adb():
    with pytest.raises(NotSupported):
        AdbTransport("emu", FakeRunner()).call("screen.tap", {"x": 1, "y": 2})


def test_provider_error_code_is_parsed():
    r = FakeRunner([(0, b"Result: Bundle[{ok=false, error=busy: the launcher has not finished loading}]\n", b"")])
    with pytest.raises(ProviderError) as e:
        AdbTransport("emu", r).call("launcher.layout_export")
    assert e.value.code == "busy"


def test_export_reads_and_deletes_the_copy(tmp_path):
    dev = FakeDevice(tmp_path)
    t = AdbTransport(device=dev)
    name, data = t.export_layout()
    assert name == "launcher_5_4_4.db" and data == dev.files[DB] and dev.leftovers() == []


def test_import_stages_privately_then_imports(tmp_path):
    dev = FakeDevice(tmp_path)
    t = AdbTransport(device=dev)
    _, data = t.export_layout()
    assert t.import_layout(data, db="launcher_5_4_4.db") == {"items": 1}
    (path, extras), = dev.imports
    assert path.startswith("/data/user/0/app.homalawn.debug/files/omalawn/import-") and extras == {"db": "launcher_5_4_4.db"}
    assert dev.leftovers() == []


def test_failed_import_leaves_no_staged_file(tmp_path):
    dev = FakeDevice(tmp_path)
    dev.fail_import = 1
    t = AdbTransport(device=dev)
    with pytest.raises(ProviderError):
        t.import_layout(dev.files[DB])
    assert dev.leftovers() == []


def test_write_private_is_0600_from_the_start():
    r = FakeRunner([(0, b"/data/user/0/app.homalawn.debug\n", b""), (0, b"", b"")])
    path = AdbTransport("emu", r).write_private("wp", b"PNG")
    assert path == "/data/user/0/app.homalawn.debug/files/omalawn/wp"
    argv, stdin = r.calls[1]
    cmd = argv[-1]
    assert stdin == b"PNG" and "run-as app.homalawn.debug" in cmd
    assert cmd.index("umask 077") < cmd.index("cat >") < cmd.index(" mv ")
    assert "files/omalawn/wp.omalawn-tmp" in cmd


def test_write_private_refuses_paths():
    with pytest.raises(ValueError):
        AdbTransport("emu", FakeRunner()).write_private("../x", b"")


def test_adb_transport_is_a_transport():
    t = AdbTransport("emu", FakeRunner())
    assert isinstance(t, Transport) and t.name == "adb" and t.has_shell and t.label == "emu"


# -- a transport without a shell: the real LinkTransport against a fake phone over TLS --------
# The fake phone (tests/fake_link.py) speaks only the link protocol; it has no shell to offer, so
# every test here also proves that nothing took a shell path: the FakeDevice behind it records a
# "shell" event for any shell use, and none may appear.
@pytest.fixture
def linked(tmp_path, monkeypatch):
    from fake_link import FakeLinkPhone, make_cert
    from homalawn.link import identity
    from homalawn.link.client import LinkTransport
    from homalawn.link.identity import Peer
    monkeypatch.setenv("HOMALAWN_CONFIG", str(tmp_path / "cfg" / "config.toml"))
    monkeypatch.setenv("HOMALAWN_DATA", str(tmp_path / "data"))
    ident = identity.ensure()
    dev = FakeDevice(tmp_path)
    phone = FakeLinkPhone(make_cert(tmp_path / "phone"), dev, trust=[ident.cert], pinned={ident.fingerprint},
                          name="Pixel")
    t = LinkTransport(Peer(fp=phone.fp, name="Pixel", hosts=[phone.addr]), discover=lambda: [], keepalive_s=None)
    yield t, dev, phone
    t.close()
    phone.close()
    assert "shell" not in dev.events  # no shell path was ever taken


def test_shell_raises_on_the_link_transport(linked):
    t, _, phone = linked
    assert isinstance(t, Transport) and not t.has_shell
    with pytest.raises(NotSupported):
        t.shell("pm", "list", "packages")
    assert phone.log == []


def test_launcher_without_shell_skips_home_repair_with_one_line(linked):
    from homalawn.session import Launcher
    t, dev, _ = linked
    dev.lose_home_on_import = True
    la = Launcher(t)
    msgs = []
    with la.home_guard(notify=msgs.append):
        lay = la.get_layout()
        lay["screens"][0]["items"].append({"type": "app", "package": "com.b", "activity": ".X", "x": 1, "y": 1})
        la.apply_layout(lay)
    assert dev.set_home_calls == 0
    assert len(msgs) == 1 and "\n" not in msgs[0]
    assert "Home app" in msgs[0] and "adb" not in msgs[0]


def test_launcher_without_shell_refuses_home_with_a_phone_fix(linked):
    from homalawn.session import HomeError, Launcher
    t, dev, _ = linked
    dev.default_home = False
    with pytest.raises(HomeError) as e:
        with Launcher(t).home_guard():
            pass
    assert "Pixel" in str(e.value) and "adb" not in str(e.value) and "\n" not in str(e.value)


def test_wallpaper_backup_without_shell_names_the_fix(linked):
    from homalawn.session import Launcher
    t, _, phone = linked

    def denied():  # what the phone answers when Lawnchair may not read the wallpaper
        raise ProviderError("IllegalStateException: no backup: getWallpaperFile: SecurityException: "
                            "READ_MEDIA_IMAGES", code="failed")
    phone.wallpaper = denied
    with pytest.raises(NotSupported) as e:
        Launcher(t).backup_wallpaper()
    assert "Allow Homa Lawn to read photos once on the phone" in str(e.value) and "\n" not in str(e.value)


def test_wallpaper_over_the_link_is_backed_up_staged_and_set(linked, tmp_path):
    from homalawn import snapshots
    from homalawn.session import Launcher
    t, dev, phone = linked
    img = tmp_path / "new.png"
    img.write_bytes(b"\x89PNG new")
    snap = Launcher(t).set_wallpaper(str(img))
    assert snapshots.load(snap)["wallpaper.png"] == b"\x89PNG wallpaper"
    assert dev.files["files/omalawn/wp"] == b"\x89PNG new"
    assert phone.methods()[-3:] == ["launcher.stage_file", "launcher.wallpaper_set", "launcher.wallpaper_info"]
    assert phone.log[-2][2] == {"path": "/data/user/0/app.homalawn.debug/files/omalawn/wp"}


# -- the picture homalawn set is the backup while the phone still shows it --------------------
def _phone_cannot_read_wallpaper(phone):
    def denied():
        raise ProviderError("failed: getWallpaperFile: SecurityException: READ_EXTERNAL_STORAGE", code="failed")
    phone.wallpaper = denied
    phone.handlers["launcher.request_media_permission"] = lambda p: (
        {"granted": False, "asked": False, "why": "not_grantable"}, None)


def _img(tmp_path, name, data):
    f = tmp_path / name
    f.write_bytes(data)
    return str(f)


def test_second_set_over_the_link_backs_up_the_first_picture_without_a_phone_read(linked, tmp_path):
    from homalawn import snapshots
    from homalawn.session import WALLPAPER_RECORD, Launcher
    t, dev, phone = linked
    _phone_cannot_read_wallpaper(phone)
    la = Launcher(t)
    assert la.set_wallpaper(_img(tmp_path, "a.png", b"\x89PNG first"), backup=False) is None
    rec = json.loads((snapshots.root().parent / WALLPAPER_RECORD).read_text())
    assert rec["system_id"] == dev.wallpaper_id == 8 and rec["device"] == f"link:{phone.fp}"
    snap = la.set_wallpaper(_img(tmp_path, "b.png", b"\x89PNG second"))
    assert snapshots.load(snap) == {"wallpaper.png": b"\x89PNG first"}
    assert "launcher.wallpaper_backup" not in phone.methods()
    assert "launcher.request_media_permission" not in phone.methods()
    assert dev.wallpaper_now == b"\x89PNG second"
    name, _ = la.undo()  # back to the first picture, still without reading the phone
    assert name == snap and dev.wallpaper_now == b"\x89PNG first"
    assert "launcher.wallpaper_backup" not in phone.methods()


def test_wallpaper_changed_outside_homalawn_asks_the_phone_again(linked, tmp_path):
    from homalawn.session import MSG_MEDIA_NOT_GRANTABLE, Launcher
    t, dev, phone = linked
    _phone_cannot_read_wallpaper(phone)
    la = Launcher(t)
    la.set_wallpaper(_img(tmp_path, "a.png", b"\x89PNG first"), backup=False)
    dev.wallpaper_id += 1  # someone picked another wallpaper on the phone
    before = dev.wallpaper_now
    with pytest.raises(NotSupported) as e:
        Launcher(t).set_wallpaper(_img(tmp_path, "b.png", b"\x89PNG second"))
    assert str(e.value) == MSG_MEDIA_NOT_GRANTABLE
    assert "launcher.wallpaper_backup" in phone.methods() and dev.wallpaper_now == before


def test_record_without_its_picture_falls_back_to_the_phone(linked, tmp_path):
    from homalawn import snapshots
    from homalawn.session import HELD_WALLPAPER, Launcher
    t, dev, phone = linked
    la = Launcher(t)
    la.set_wallpaper(_img(tmp_path, "a.png", b"\x89PNG first"), backup=False)
    snapshots.delete(HELD_WALLPAPER)
    snap = la.set_wallpaper(_img(tmp_path, "b.png", b"\x89PNG second"))
    assert snapshots.load(snap)["wallpaper.png"] == b"\x89PNG wallpaper"  # what the phone read
    assert "launcher.wallpaper_backup" in phone.methods()


def test_no_wallpaper_id_means_no_record(linked, tmp_path):
    from homalawn import snapshots
    from homalawn.session import HELD_WALLPAPER, WALLPAPER_RECORD, Launcher

    def info(p):
        raise ProviderError("unknown_method: launcher.wallpaper_info", code="unknown_method")
    t, dev, phone = linked
    phone.handlers["launcher.wallpaper_info"] = info
    Launcher(t).set_wallpaper(_img(tmp_path, "a.png", b"\x89PNG first"), backup=False)
    assert dev.wallpaper_now == b"\x89PNG first"
    assert not (snapshots.root().parent / WALLPAPER_RECORD).exists()
    Launcher(t).set_wallpaper(_img(tmp_path, "b.png", b"\x89PNG second"))
    assert "launcher.wallpaper_backup" in phone.methods()
    assert HELD_WALLPAPER not in snapshots.list_undoable(f"link:{t.peer.fp}")


def test_held_picture_is_not_an_undo_step(linked, tmp_path):
    from homalawn import snapshots
    from homalawn.session import HELD_WALLPAPER, Launcher
    t, _, _ = linked
    Launcher(t).set_wallpaper(_img(tmp_path, "a.png", b"\x89PNG first"), backup=False)
    assert HELD_WALLPAPER in snapshots.list_() and snapshots.list_undoable(f"link:{t.peer.fp}") == []


def test_wait_loaded_without_shell_does_not_kick_the_launcher(linked, monkeypatch):
    from homalawn.session import Launcher
    t, dev, _ = linked
    monkeypatch.setattr("homalawn.session.time.sleep", lambda s: None)
    dev.loaded = False
    dev.fail_load = 20
    clock = iter(range(0, 1000, 1))
    monkeypatch.setattr("homalawn.session.time.monotonic", lambda: next(clock))
    Launcher(t).wait_loaded()
    assert "start_home" not in dev.events


def test_apps_over_the_link_come_from_the_launcher(linked):
    from homalawn import apps
    t, _, phone = linked
    assert apps.list_apps(t) == [{"label": "A", "package": "com.a", "activity": "com.a.Main"},
                                 {"label": "B", "package": "com.b", "activity": "com.b.X"}]
    assert phone.methods()[-1] == "launcher.apps_list"


def test_widget_cause_without_shell_is_empty(linked):
    from homalawn import widgets
    t, _, phone = linked
    assert widgets._removal_cause(t, "com.w/.W") == ""
    assert phone.log == []


def test_bind_advice_names_adb_only_when_there_is_adb(linked):
    from homalawn import widgets
    t, _, _ = linked
    assert "adb" not in widgets.msg_bind(t) and "Tap the widget" in widgets.msg_bind(t)
    a = AdbTransport("emu-7", FakeRunner())
    assert "adb -s emu-7 shell appwidget grantbind" in widgets.msg_bind(a)


def test_shell_only_helpers_are_reached_through_shell_of(linked):
    from homalawn.transport import shell_of
    t, _, _ = linked
    with pytest.raises(NotSupported):
        shell_of(t, "Setting the default home", "do it on the phone")
    a = AdbTransport("emu", FakeRunner())
    assert shell_of(a, "x", "y") is a


def test_link_only_methods_over_adb_point_at_pairing():
    with pytest.raises(NotSupported) as e:
        AdbTransport("emu", FakeRunner()).call("screen.tap", {"x": 1, "y": 2})
    assert "homalawn pair HOST" in str(e.value) and "--via link" in str(e.value)


def test_over_adb_the_held_picture_also_spares_the_temporary_grant(tmp_path, monkeypatch):
    from homalawn import snapshots
    from homalawn.session import Launcher
    monkeypatch.setenv("HOMALAWN_DATA", str(tmp_path / "data"))
    dev = FakeDevice(tmp_path)
    la = Launcher(AdbTransport(device=dev))
    la.set_wallpaper(_img(tmp_path, "a.png", b"\x89PNG first"), backup=False)
    snap = la.set_wallpaper(_img(tmp_path, "b.png", b"\x89PNG second"))
    assert snapshots.load(snap)["wallpaper.png"] == b"\x89PNG first"
    assert "wallpaper_backup" not in dev.events  # so no temporary media grant either


def test_record_of_another_device_with_the_same_id_is_not_used(linked, tmp_path):
    """The emulator and the real phone share one data folder and wallpaper ids are small numbers:
    a record written for another device is never taken, even when the id coincides."""
    from homalawn import snapshots
    from homalawn.session import WALLPAPER_RECORD, Launcher
    t, dev, phone = linked
    la = Launcher(t)
    la.set_wallpaper(_img(tmp_path, "a.png", b"\x89PNG first"), backup=False)
    path = snapshots.root().parent / WALLPAPER_RECORD
    rec = json.loads(path.read_text())
    assert rec["system_id"] == dev.wallpaper_id
    path.write_text(json.dumps({**rec, "device": "adb:emulator-5560"}))  # same id, other device
    snap = la.set_wallpaper(_img(tmp_path, "b.png", b"\x89PNG second"))
    assert snapshots.load(snap)["wallpaper.png"] == b"\x89PNG wallpaper"  # read from this phone
    assert "launcher.wallpaper_backup" in phone.methods()


def test_adb_record_names_the_serial(tmp_path, monkeypatch):
    from homalawn import snapshots
    from homalawn.session import WALLPAPER_RECORD, Launcher
    monkeypatch.setenv("HOMALAWN_DATA", str(tmp_path / "data"))
    dev = FakeDevice(tmp_path)
    Launcher(AdbTransport(device=dev)).set_wallpaper(_img(tmp_path, "a.png", b"\x89PNG first"), backup=False)
    assert json.loads((snapshots.root().parent / WALLPAPER_RECORD).read_text())["device"] == "adb:emu"


# -- undo/restore never overwrite a wallpaper they could not save (final review I2) -------------
def test_undo_over_the_link_refuses_when_the_current_wallpaper_cannot_be_saved(linked, tmp_path):
    from homalawn import snapshots
    from homalawn.session import MSG_MEDIA_NOT_GRANTABLE, Launcher
    t, dev, phone = linked
    _phone_cannot_read_wallpaper(phone)
    la = Launcher(t)
    la.set_wallpaper(_img(tmp_path, "a.png", b"\x89PNG first"), backup=False)
    la.set_wallpaper(_img(tmp_path, "b.png", b"\x89PNG second"))  # backed up from the held picture
    dev.wallpaper_id += 1  # then the user picked another wallpaper on the phone
    dev.wallpaper_now = b"\x89PNG chosen on the phone"
    undoable = snapshots.list_undoable(la.device_key())
    with pytest.raises(NotSupported) as e:
        la.undo()
    assert str(e.value) == MSG_MEDIA_NOT_GRANTABLE  # names `--via adb` once, or --no-backup
    assert dev.wallpaper_now == b"\x89PNG chosen on the phone"  # not overwritten
    assert snapshots.list_undoable(la.device_key()) == undoable  # the undo step is still there
    name, _ = la.undo(no_backup=True)  # the user said so: overwrite without a backup
    assert name == undoable[0] and dev.wallpaper_now == b"\x89PNG first"


def test_restore_by_name_with_a_failing_backup_changes_nothing(linked, tmp_path):
    from homalawn import snapshots
    from homalawn.session import Launcher
    t, dev, phone = linked
    la = Launcher(t)
    snap = la.set_wallpaper(_img(tmp_path, "a.png", b"\x89PNG first"))
    snapshots.delete("current-wallpaper")  # only the phone could say what is shown now

    def broken():
        raise ProviderError("failed: IOException: disk", code="failed")
    phone.wallpaper = broken
    with pytest.raises(ValueError) as e:
        la.restore(snap)
    assert "--no-backup" in str(e.value)
    assert dev.wallpaper_now == b"\x89PNG first"
    la.restore(snap, no_backup=True)
    assert dev.wallpaper_now == b"\x89PNG wallpaper"


def test_apps_leave_out_the_launchers_home_entry_but_keep_its_settings_icon():
    """The drawer hides Lawnchair's home entry and shows its "Homa Lawn" settings icon (patch 0018);
    the desktop's list matches, over adb and over a link to a phone app older than patch 0018."""
    from homalawn import apps
    home = {"package": "app.homalawn.debug", "activity": "app.lawnchair.LawnchairLauncher", "label": "Homa Lawn"}
    icon = {"package": "app.homalawn.debug", "activity": "app.lawnchair.omalawn.HomaLawnSettings",
            "label": "Homa Lawn"}

    class Adb:
        has_shell = True

        def shell(self, *a):
            return ("Activity #0:\n    app.homalawn.debug/app.lawnchair.LawnchairLauncher\n"
                    "Activity #1:\n    app.homalawn.debug/app.lawnchair.omalawn.HomaLawnSettings\n")

    class Link:
        has_shell = False

        def call(self, method, params=None):
            assert method == "launcher.apps_list"
            return [home, icon]

    assert apps.list_apps(Adb()) == [{"label": "app.homalawn.debug", "package": "app.homalawn.debug",
                                      "activity": "app.lawnchair.omalawn.HomaLawnSettings"}]
    assert apps.list_apps(Link()) == [icon]
