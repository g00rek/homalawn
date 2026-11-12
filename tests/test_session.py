import pytest
from homalawn import layout as L, snapshots
from homalawn.adb import AdbError, AdbTransport, ProviderError
from homalawn.session import Launcher


@pytest.fixture(autouse=True)
def data(tmp_path, monkeypatch):
    monkeypatch.setenv("HOMALAWN_DATA", str(tmp_path / "data"))
    monkeypatch.setattr("homalawn.session.time.sleep", lambda s: None)


def make_db(path, apps):
    import sqlite3
    con = sqlite3.connect(path)
    con.execute("""create table favorites (_id INTEGER PRIMARY KEY, title TEXT, intent TEXT, container INTEGER,
      screen INTEGER, cellX INTEGER, cellY INTEGER, spanX INTEGER, spanY INTEGER, itemType INTEGER,
      appWidgetId INTEGER NOT NULL DEFAULT -1, icon BLOB, appWidgetProvider TEXT, modified INTEGER NOT NULL DEFAULT 0,
      restored INTEGER NOT NULL DEFAULT 0, profileId INTEGER NOT NULL DEFAULT 0, rank INTEGER NOT NULL DEFAULT 0,
      options INTEGER NOT NULL DEFAULT 0, appWidgetSource INTEGER NOT NULL DEFAULT -1)""")
    for i, (pkg, x, y) in enumerate(apps, 1):
        con.execute("insert into favorites(_id,title,intent,container,screen,cellX,cellY,spanX,spanY,itemType) "
                    "values (?,?,?,?,?,?,?,1,1,0)",
                    (i, pkg, f"#Intent;action=android.intent.action.MAIN;component={pkg}/.A;end", -100, 0, x, y))
    con.commit()
    con.close()


HOME = "/data/user/0/app.homalawn.debug"


class FakeDevice:
    """Files in the app sandbox plus the provider calls the write path uses.

    `layout_export` copies the live DB to files/omalawn/export-N.db; `layout_import` replaces the
    live DB with a staged file (and deletes that file), like Lawnchair's in-process methods.
    `drop_on_load`: _ids the loader deletes when Lawnchair reloads. `unbound`: widget ids
    widget_status reports as not bound. `fail_import` / `fail_load`: number of imports / loads to fail."""
    def __init__(self, tmp_path, files=None, fail_write=False):
        p = tmp_path / "seed.db"
        make_db(str(p), [("com.a", 0, 1)])
        self.files = files if files is not None else {"databases/launcher_5_4_4.db": p.read_bytes()}
        self.events = []
        self.fail_write_on = None
        self.fail_import = 0
        self.fail_load = 0
        self.loaded = True
        self.default_home = True
        self.lose_home_on_import = False
        self.set_home_calls = 0
        self.drop_on_load = set()
        self.unbound = set()
        self.stage_hook = None
        self.imports = []
        self.busy_while_loading = True
        self.n = 0
        self.serial = "emu"
        self.wallpaper_id = 7  # getWallpaperId(FLAG_SYSTEM); every wallpaper_set gives a new one
        self.wallpaper_now = None  # the bytes of the picture last set
        # the lock screen, like Android: lock_id -1 = it shows the home wallpaper; setting the home
        # one then moves the old home picture (and its id) to the lock screen
        self.lock_id = 1
        self.lock_now = b"\x89PNG lock"
        self.old_build = False  # a Lawnchair build without the lock screen (and without the screen size)
        self.wallpaper_sets = []  # which= of every wallpaper_set

    @staticmethod
    def rel(path):
        return path[len(HOME) + 1:] if path.startswith(HOME + "/") else path

    def call(self, method, arg=None, extras=None):
        self.events.append(method)
        if method == "widget_status":
            return {"appWidgetId": int(arg), "bound": int(arg) not in self.unbound}
        if method == "wallpaper_info":
            if self.old_build:
                return {"system_id": self.wallpaper_id, "lock_id": 1, "live": False}
            return {"system_id": self.wallpaper_id, "lock_id": self.lock_id, "lock_follows_home": self.lock_id < 0,
                    "live": False, "width": 1080, "height": 2400}
        if method == "wallpaper_set":
            data = self.files[self.rel((extras or {}).get("path") or arg)]
            which = "home" if self.old_build else (extras or {}).get("which", "home")
            self.wallpaper_sets.append(which)
            if which in ("home", "both"):
                if which == "home" and self.lock_id < 0:  # Android keeps the old picture on the lock screen
                    self.lock_id, self.lock_now = self.wallpaper_id, self.wallpaper_now
                self.wallpaper_now = data
                self.wallpaper_id += 1
            if which == "lock":
                self.lock_id, self.lock_now = 1000 + len(self.wallpaper_sets), data
            if which == "both":
                self.lock_id, self.lock_now = -1, None
            return {}
        if method == "wallpaper_lock_reset":
            if self.lock_id >= 0:
                self.lock_id, self.lock_now = -1, None
                self.wallpaper_id += 1  # Android re-numbers the home wallpaper here
            return {}
        if method == "layout_export":
            self.n += 1
            name = f"files/omalawn/export-{self.n}.db"
            self.files[name] = self.run_as_read(DB)
            return {"path": f"{HOME}/{name}", "db": "launcher_5_4_4.db", "size": len(self.files[name])}
        if method == "layout_import":
            from homalawn.adb import ProviderError
            self.imports.append((arg, dict(extras or {})))
            data = self.files.pop(self.rel(arg), None)  # the phone deletes the staged file either way
            if self.fail_import:
                self.fail_import -= 1
                raise ProviderError("failed: SQLiteException: disk I/O error")
            if not self.loaded and self.busy_while_loading:  # an older build refused while loading
                raise ProviderError("busy: the launcher has not finished loading its home screen")
            if data is None:
                raise ProviderError(f"bad_path: {arg} does not exist")
            self.files[DB] = data
            self.loaded = False
            if self.lose_home_on_import:
                self.default_home = False
            return {"items": 1}
        if method in ("ping", "ensure_loaded") and not self.loaded:
            if self.fail_load:
                self.fail_load -= 1
                return {"db": "launcher_5_4_4.db", "model_loaded": False, "loaded": False,
                        "default_home": self.default_home}
            self.loaded = True
            self._load()
        return {"db": "launcher_5_4_4.db", "model_loaded": True, "loaded": True, "default_home": self.default_home}

    def _load(self):
        if self.drop_on_load and DB in self.files:
            import sqlite3, tempfile, os
            fd, t = tempfile.mkstemp(suffix=".db")
            os.write(fd, self.files[DB]); os.close(fd)
            con = sqlite3.connect(t)
            con.executemany("delete from favorites where _id=?", [(i,) for i in self.drop_on_load])
            con.commit(); con.close()
            self.files[DB] = open(t, "rb").read()
            os.unlink(t)

    def force_stop(self):
        self.events.append("force_stop")

    def start_home(self):
        self.events.append("start_home")

    def set_home(self):
        self.set_home_calls += 1
        self.default_home = True

    def run_as_read(self, path):
        self.events.append("read")
        path = self.rel(path)
        if path not in self.files:
            raise AdbError("No such file")
        return self.files[path]

    def run_as_write(self, path, data):
        self.events.append("write")
        if self.fail_write_on == "once":
            self.fail_write_on = None
            raise AdbError("link dropped")
        self.files[self.rel(path)] = data
        if self.stage_hook:
            self.stage_hook()

    package = "app.homalawn.debug"

    def run_as(self, *args):
        return self.shell("run-as", self.package, *args)

    def shell(self, *args):
        self.events.append("shell")
        if args[-1:] == ("pwd",):
            return HOME + "\n"
        if args[:4] == ("run-as", self.package, "rm", "-f"):
            for a in args[4:]:
                self.files.pop(self.rel(a), None)
        return ""

    def leftovers(self):
        return sorted(f for f in self.files if f.startswith("files/omalawn/"))


DB = "databases/launcher_5_4_4.db"


def items_of(data):
    import tempfile, os
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        f.write(data)
    try:
        return L.read_layout(f.name, (4, 5))
    finally:
        os.unlink(f.name)


def items(dev):
    import tempfile, os
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        f.write(dev.files[DB])
    try:
        return L.read_layout(f.name, (4, 5))
    finally:
        os.unlink(f.name)


def test_grid_from_db_name(tmp_path):
    la = Launcher(AdbTransport(device=FakeDevice(tmp_path)))
    assert la.grid() == (4, 5) and la.hotseat() == 4




def add_app(la, pkg, x, y=1):
    lay = la.get_layout()
    lay["screens"][0]["items"].append({"type": "app", "package": pkg, "activity": ".X", "x": x, "y": y})
    return lay


def test_write_stages_then_imports_in_process(tmp_path):
    """No force-stop, no file swap: the DB is staged under files/omalawn/ and Lawnchair imports it."""
    dev = FakeDevice(tmp_path)
    la = Launcher(AdbTransport(device=dev))
    lay = add_app(la, "com.b", 1)
    dev.events.clear()
    la.apply_layout(lay)
    ev = [e for e in dev.events if e in ("layout_export", "write", "layout_import", "force_stop", "start_home")]
    assert ev == ["layout_export", "write", "layout_import", "layout_export"]  # snapshot, stage, import, read-back
    (path, extras), = dev.imports
    assert path.startswith(HOME + "/files/omalawn/import-") and path.endswith(".db")
    assert extras == {"db": "launcher_5_4_4.db"}
    assert len(snapshots.list_()) == 1
    assert {i["package"] for i in items(dev)["screens"][0]["items"]} == {"com.a", "com.b"}
    assert dev.leftovers() == []  # export copies read and deleted, the staged file consumed


def test_read_all_uses_layout_export(tmp_path):
    dev = FakeDevice(tmp_path)
    la = Launcher(AdbTransport(device=dev))
    dev.files[DB + "-wal"] = b"stale sidecar"  # never read: Lawnchair's export is self-contained
    dev.events.clear()
    files = la._read_all()
    assert files == {"launcher_5_4_4.db": dev.files[DB]}
    assert dev.events[0] == "layout_export" and dev.leftovers() == []


def test_export_while_loading_waits_and_retries(tmp_path):
    from homalawn.adb import ProviderError
    dev = FakeDevice(tmp_path)
    la = Launcher(AdbTransport(device=dev))
    real = dev.call
    state = {"busy": 1}

    def call(method, arg=None, extras=None):
        if method == "layout_export" and state["busy"]:
            state["busy"] -= 1
            raise ProviderError("busy: the launcher has not finished loading its home screen")
        return real(method, arg, extras)
    dev.call = call
    assert la.get_layout()["screens"][0]["items"][0]["package"] == "com.a"


def test_apply_waits_for_model_loaded_before_returning(tmp_path):
    dev = FakeDevice(tmp_path)
    la = Launcher(AdbTransport(device=dev))
    lay = add_app(la, "com.b", 1)
    dev.fail_load = 3  # three pings say "still loading"
    la.apply_layout(lay)
    assert dev.loaded and dev.fail_load == 0


def test_slow_upload_does_not_touch_the_running_launcher(tmp_path):
    dev = FakeDevice(tmp_path)
    la = Launcher(AdbTransport(device=dev))
    lay = add_app(la, "com.b", 1)
    seen = []
    dev.stage_hook = lambda: seen.append(dev.files[DB])  # the live DB while the slow upload runs
    before = dev.files[DB]
    la.apply_layout(lay)
    assert seen == [before]  # untouched until the import


def test_invalid_layout_touches_nothing(tmp_path):
    dev = FakeDevice(tmp_path)
    la = Launcher(AdbTransport(device=dev))
    lay = add_app(la, "com.b", 9, 9)
    dev.events.clear()
    with pytest.raises(ValueError, match="outside"):
        la.apply_layout(lay)
    assert not {"write", "layout_import", "force_stop"} & set(dev.events)
    assert snapshots.list_() == []


def test_failure_after_import_reimports_the_snapshot(tmp_path, monkeypatch):
    dev = FakeDevice(tmp_path)
    orig = dev.files[DB]
    la = Launcher(AdbTransport(device=dev))
    lay = add_app(la, "com.b", 1)
    calls = {"n": 0}
    real = la.wait_loaded

    def flaky(*a, **k):
        calls["n"] += 1
        if calls["n"] == 2:  # the wait right after the import
            raise AdbError("link dropped")
        return real(*a, **k)
    monkeypatch.setattr(la, "wait_loaded", flaky)
    with pytest.raises(AdbError, match="link dropped"):
        la.apply_layout(lay)
    # rollback while the model reloads: refused once as busy, re-staged and re-imported once loaded
    assert dev.events.count("layout_import") == 3 and "force_stop" not in dev.events
    assert dev.events.count("write") == 3
    assert items_of(dev.files[DB]) == items_of(orig) and dev.leftovers() == []


def test_rollback_bytes_get_unbound_widgets_as_placeholders(tmp_path, monkeypatch):
    """The loader frees ids of widgets missing from the imported layout (removeGhostWidgets); a
    rollback carrying such an id must come back as a placeholder, not be dropped."""
    import sqlite3, tempfile, os
    dev = FakeDevice(tmp_path)
    dev.files[DB] = _db_with_widget(dev, 25)
    la = Launcher(AdbTransport(device=dev))
    lay = la.get_layout()
    lay["screens"][0]["items"] = [i for i in lay["screens"][0]["items"] if i["type"] != "widget"]
    real = la.wait_loaded
    calls = {"n": 0}

    def flaky(*a, **k):
        calls["n"] += 1
        if calls["n"] == 2:
            dev.unbound = {25}  # the reload of the widget-less layout freed the id
            raise AdbError("link dropped")
        return real(*a, **k)
    monkeypatch.setattr(la, "wait_loaded", flaky)
    with pytest.raises(AdbError, match="link dropped"):
        la.apply_layout(lay)
    fd, t = tempfile.mkstemp(); os.write(fd, dev.files[DB]); os.close(fd)
    assert sqlite3.connect(t).execute("select appWidgetId, restored from favorites where _id=9").fetchone() == (-1, 1)
    os.unlink(t)


def test_import_error_reimports_the_snapshot_bytes(tmp_path):
    dev = FakeDevice(tmp_path)
    orig = dev.files[DB]
    la = Launcher(AdbTransport(device=dev))
    lay = add_app(la, "com.b", 1)
    staged = []
    dev.stage_hook = lambda: staged.append(next(v for k, v in dev.files.items() if "import-" in k))
    dev.fail_import = 1
    with pytest.raises(ProviderError, match="disk I/O"):
        la.apply_layout(lay)
    assert len(dev.imports) == 2
    assert items_of(staged[1]) == items_of(orig)  # the second import carried the snapshot
    assert items_of(dev.files[DB]) == items_of(orig) and dev.leftovers() == []


def test_failed_upload_leaves_no_staged_file(tmp_path):
    dev = FakeDevice(tmp_path)
    la = Launcher(AdbTransport(device=dev))
    lay = add_app(la, "com.b", 1)
    dev.fail_write_on = "once"
    with pytest.raises(AdbError, match="link dropped"):
        la.apply_layout(lay)
    assert dev.leftovers() == []


def test_rollback_failure_names_the_snapshot(tmp_path):
    from homalawn.session import RollbackFailed
    dev = FakeDevice(tmp_path)
    la = Launcher(AdbTransport(device=dev))
    lay = add_app(la, "com.b", 1)
    dev.fail_import = 2
    with pytest.raises(RollbackFailed) as e:
        la.apply_layout(lay)
    snap = snapshots.list_auto()[0]
    assert f"homalawn snapshot restore {snap}" in str(e.value)


def test_items_dropped_by_the_loader_are_reported(tmp_path):
    dev = FakeDevice(tmp_path)
    la = Launcher(AdbTransport(device=dev))
    lay = add_app(la, "com.gone", 1)
    dev.drop_on_load = {2}  # the new app gets _id 2
    notes = la.apply_layout(lay)
    assert len(notes) == 1 and "com.gone/.X" in notes[0]


def _db_with_widget(dev, wid):
    import sqlite3, tempfile, os
    fd, t = tempfile.mkstemp(suffix=".db")
    os.write(fd, dev.files[DB]); os.close(fd)
    con = sqlite3.connect(t)
    con.execute("insert into favorites(_id,container,screen,cellX,cellY,spanX,spanY,itemType,appWidgetId,"
                "appWidgetProvider,restored) values (9,-100,0,1,2,2,1,4,?,'com.w/.W',0)", (wid,))
    con.commit(); con.close()
    data = open(t, "rb").read()
    os.unlink(t)
    return data


def _widget(dev):
    return [i for i in items(dev)["screens"][0]["items"] if i["type"] == "widget"]


def test_restore_turns_unbound_widgets_into_placeholders(tmp_path):
    import sqlite3, tempfile, os
    dev = FakeDevice(tmp_path)
    la = Launcher(AdbTransport(device=dev))
    snapshots.save("m", {"launcher_5_4_4.db": _db_with_widget(dev, 25)}, device="adb:emu")
    dev.unbound = {25}  # Launcher3 freed the id when the widget left its DB
    notes = la.restore("m")
    w = _widget(dev)
    assert [x["appWidgetId"] for x in w] == [-1] and "com.w/.W" in notes[0]
    fd, t = tempfile.mkstemp(); os.write(fd, dev.files[DB]); os.close(fd)
    assert sqlite3.connect(t).execute("select restored from favorites where _id=9").fetchone() == (1,)
    os.unlink(t)


def test_bound_widgets_keep_their_id(tmp_path):
    dev = FakeDevice(tmp_path)
    la = Launcher(AdbTransport(device=dev))
    snapshots.save("m", {"launcher_5_4_4.db": _db_with_widget(dev, 25)}, device="adb:emu")
    assert la.restore("m") == []
    assert [x["appWidgetId"] for x in _widget(dev)] == [25]


def test_unknown_existing_component_does_not_block_edits(tmp_path):
    dev = FakeDevice(tmp_path)  # com.a/.A is on the phone but not in the known list
    la = Launcher(AdbTransport(device=dev))
    la.place("com.z/.Z", 0, 2, 2, known_components={"com.z/com.z.Z"})
    with pytest.raises(ValueError, match="unknown component com.q"):
        la.place("com.q/.Q", 0, 3, 3, known_components={"com.z/com.z.Z"})


def test_undo_walks_back(tmp_path):
    dev = FakeDevice(tmp_path)
    la = Launcher(AdbTransport(device=dev))
    states = [dev.files[DB]]
    for pkg, x in (("com.b", 2), ("com.c", 3)):
        la.apply_layout(add_app(la, pkg, x))
        states.append(dev.files[DB])
    n1, _ = la.undo()
    assert n1.startswith("auto-") and dev.files[DB] == states[1]
    la.undo()
    assert dev.files[DB] == states[0]
    with pytest.raises(LookupError):
        la.undo()  # the pre-restore snapshots are not picked up
    assert any(n.startswith("pre-restore-") for n in snapshots.list_())


def test_restore_folds_wal_into_one_file(tmp_path):
    import sqlite3, os
    dev = FakeDevice(tmp_path)
    p = tmp_path / "w.db"
    p.write_bytes(dev.files[DB])
    con = sqlite3.connect(p)
    con.execute("pragma journal_mode=wal")
    con.execute("pragma wal_autocheckpoint=0")
    con.execute("update favorites set cellX=3 where _id=1")
    con.commit()
    files = {"launcher_5_4_4.db": p.read_bytes(), "launcher_5_4_4.db-wal": (tmp_path / "w.db-wal").read_bytes()}
    con.close()
    snapshots.save("m", files, device="adb:emu")
    Launcher(AdbTransport(device=dev)).restore("m")
    assert [i["x"] for i in items(dev)["screens"][0]["items"]] == [3]


def test_restore_takes_pre_restore_snapshot(tmp_path):
    dev = FakeDevice(tmp_path)
    la = Launcher(AdbTransport(device=dev))
    before = dev.files[DB]
    la.apply_layout(add_app(la, "com.b", 1))
    snapshots.save("m", {"launcher_5_4_4.db": before}, device="adb:emu")
    after_edit = dev.files[DB]
    la.restore("m")
    pre = [n for n in snapshots.list_() if n.startswith("pre-restore-")]
    assert len(pre) == 1 and snapshots.load(pre[0])["launcher_5_4_4.db"] == after_edit
    assert dev.files[DB] == before


def test_restore_refuses_db_of_another_grid(tmp_path):
    dev = FakeDevice(tmp_path)
    snapshots.save("m", {"launcher_6_5_4.db": dev.files[DB]}, device="adb:emu")
    with pytest.raises(ValueError, match="grid"):
        Launcher(AdbTransport(device=dev)).restore("m")
    assert "layout_import" not in dev.events


def test_apply_rejects_other_grid(tmp_path):
    dev = FakeDevice(tmp_path)
    la = Launcher(AdbTransport(device=dev))
    lay = la.get_layout()
    lay["grid"] = {"cols": 5, "rows": 6}
    dev.events.clear()
    with pytest.raises(ValueError, match="does not match"):
        la.apply_layout(lay)
    assert "layout_import" not in dev.events


def test_apply_rejects_hotseat_beyond_dock(tmp_path, monkeypatch):
    dev = FakeDevice(tmp_path)
    la = Launcher(AdbTransport(device=dev))
    monkeypatch.setattr(la, "hotseat", lambda: 3)
    lay = la.get_layout()
    lay["hotseat"] = [{"type": "app", "package": "com.h", "activity": ".H", "x": 3}]
    with pytest.raises(ValueError, match="dock"):
        la.apply_layout(lay)


def test_place_moves_existing_and_adds(tmp_path):
    dev = FakeDevice(tmp_path)
    la = Launcher(AdbTransport(device=dev))
    la.place("com.a", 0, 2, 3)
    its = items(dev)["screens"][0]["items"]
    assert [(i["package"], i["x"], i["y"]) for i in its] == [("com.a", 2, 3)]
    la.place("com.z/.Z", 0, 0, 1)
    assert len(items(dev)["screens"][0]["items"]) == 2
    with pytest.raises(ValueError, match="reserved"):
        la.place("com.q/.Q", 0, 1, 0)


def test_remove_and_folder(tmp_path):
    dev = FakeDevice(tmp_path)
    la = Launcher(AdbTransport(device=dev))
    la.folder_create("F", 0, 1, 2, [("com.a", None), ("com.k", ".K")])
    top = items(dev)["screens"][0]["items"]
    assert [i["type"] for i in top] == ["folder"] and len(top[0]["items"]) == 2
    la.remove("com.k")
    assert len(items(dev)["screens"][0]["items"][0]["items"]) == 1
    with pytest.raises(LookupError):
        la.remove("com.nope")


def test_place_new_app_needs_activity(tmp_path):
    la = Launcher(AdbTransport(device=FakeDevice(tmp_path)))
    with pytest.raises(ValueError, match="activity is required"):
        la.place("com.new", 0, 1, 1)
    with pytest.raises(ValueError, match="activity is required"):
        la.folder_create("F", 0, 1, 1, [("com.new", None)])


# -- default home -----------------------------------------------------------
def test_home_guard_refuses_when_not_default_home(tmp_path):
    from homalawn.session import HomeError
    dev = FakeDevice(tmp_path)
    dev.default_home = False
    la = Launcher(AdbTransport(device=dev))
    with pytest.raises(HomeError, match="set-home-activity"):
        with la.home_guard():
            la.apply_layout(add_app(la, "com.b", 1))
    assert "layout_import" not in dev.events
    with la.home_guard(require=False):  # recovery commands still run
        pass
    assert dev.set_home_calls == 0  # it was not the default before: not ours to change


def test_home_guard_repairs_lost_default_home(tmp_path):
    dev = FakeDevice(tmp_path)
    dev.lose_home_on_import = True  # Android drops a crashing home app's default status
    la = Launcher(AdbTransport(device=dev))
    msgs = []
    with la.home_guard(notify=msgs.append):
        la.apply_layout(add_app(la, "com.b", 1))
    assert dev.set_home_calls == 1 and dev.default_home
    assert len(msgs) == 1 and "set as the home app again" in msgs[0]


def test_home_guard_repairs_even_when_the_write_fails(tmp_path):
    dev = FakeDevice(tmp_path)
    dev.lose_home_on_import = True
    la = Launcher(AdbTransport(device=dev))
    with pytest.raises(RuntimeError):
        with la.home_guard():
            la.apply_layout(add_app(la, "com.b", 1))
            raise RuntimeError("boom")
    assert dev.set_home_calls == 1


def test_wait_loaded_asks_the_phone_to_load_not_ping(tmp_path):
    """ping has no side effects; ensure_loaded is what makes a phone without a launcher activity load."""
    dev = FakeDevice(tmp_path)
    dev.loaded = False
    Launcher(AdbTransport(device=dev)).wait_loaded()
    assert "ensure_loaded" in dev.events and "ping" not in dev.events
    assert dev.loaded


def test_wait_loaded_falls_back_to_ping_on_an_older_phone(tmp_path):
    dev = FakeDevice(tmp_path)
    real = dev.call

    def call(method, arg=None, extras=None):
        if method == "ensure_loaded":
            dev.events.append(method)
            raise ProviderError("unknown method ensure_loaded")
        return real(method, arg, extras)
    dev.call = call
    Launcher(AdbTransport(device=dev)).wait_loaded()
    assert "ping" in dev.events


# -- snapshots and undo belong to one phone (final review I1) ----------------------------------
def test_undo_skips_another_phones_newer_snapshot(tmp_path):
    dev = FakeDevice(tmp_path)
    la = Launcher(AdbTransport(device=dev))
    before = dev.files[DB]
    la.apply_layout(add_app(la, "com.b", 2))
    mine = snapshots.list_undoable("adb:emu")
    assert len(mine) == 1 and snapshots.device_of(mine[0]) == "adb:emu"
    snapshots.save("auto-29991231-235959", {"launcher_5_4_4.db": b"another phone"}, device="link:" + "f" * 64)
    name, _ = la.undo()
    assert name == mine[0] and dev.files[DB] == before


def test_restore_by_name_refuses_another_or_an_unknown_phone_unless_forced(tmp_path):
    dev = FakeDevice(tmp_path)
    la = Launcher(AdbTransport(device=dev))
    before = dev.files[DB]
    la.apply_layout(add_app(la, "com.b", 2))
    edited = dev.files[DB]
    snapshots.save("theirs", {"launcher_5_4_4.db": before}, device="adb:emulator-5554")
    snapshots.save("old", {"launcher_5_4_4.db": before})  # no device recorded
    with pytest.raises(ValueError, match="taken on adb:emulator-5554, not on this phone"):
        la.restore("theirs")
    with pytest.raises(ValueError, match="does not say which phone"):
        la.restore("old")
    assert dev.files[DB] == edited  # nothing changed
    la.restore("old", force=True)
    assert dev.files[DB] == before


def test_cli_snapshot_restore_force_and_list_show_the_phone(tmp_path, monkeypatch):
    from typer.testing import CliRunner
    from homalawn import cli
    import contextlib
    monkeypatch.setattr(Launcher, "home_guard", lambda self, require=True, notify=None: contextlib.nullcontext())
    monkeypatch.setenv("HOMALAWN_SERIAL", "emu")
    seen = []
    monkeypatch.setattr(Launcher, "restore", lambda self, name, force=False, no_backup=False:
                        seen.append((name, force, no_backup)) or [])
    snapshots.save("m1", {"f": b"x"}, device="adb:emu")
    snapshots.save("m2", {"f": b"x"})
    r = CliRunner().invoke(cli.app, ["snapshot", "list"])
    assert "m1  adb:emu" in r.stdout and "m2  (unknown phone)" in r.stdout
    r = CliRunner().invoke(cli.app, ["snapshot", "restore", "m2", "--force", "--no-backup"])
    assert r.exit_code == 0, r.output
    assert seen == [("m2", True, True)]


def test_top_row_reserved_follows_smartspace(monkeypatch):
    from homalawn import session as session_mod
    la = session_mod.Launcher.__new__(session_mod.Launcher)
    la.transport = object()
    for stored, want in ((None, True), (False, False), ("false", False), (True, True)):
        cur = {"datastore": {} if stored is None else {"enable_smartspace": {"value": stored}}}
        monkeypatch.setattr(session_mod.prefs_mod, "current", lambda t, cur=cur: cur)
        assert la._top_row_reserved() is want
    monkeypatch.setattr(session_mod.prefs_mod, "current", lambda t: (_ for _ in ()).throw(RuntimeError("down")))
    assert la._top_row_reserved() is True


# -- a locked phone: a reload Lawnchair leaves for the unlock is not waited for ----------------
class _LockT:
    """A link-like transport: ensure_loaded answers `loaded`, update_status `status` (or raises it)."""
    has_shell = False
    name = "link"
    label = "Phone"

    def __init__(self, loaded, status):
        self.loaded, self.status, self.calls = loaded, status, []

    def call(self, method, params=None):
        self.calls.append(method)
        if method == "launcher.ensure_loaded":
            return {"loaded": self.loaded, "db": "launcher_5_4_4.db"}
        if method == "launcher.update_status":
            if isinstance(self.status, Exception):
                raise self.status
            return self.status
        raise AssertionError(method)


def _clock(monkeypatch, step=0.5):
    t = iter(x * step for x in range(100000))
    monkeypatch.setattr("homalawn.session.time.monotonic", lambda: next(t))


def test_a_locked_phone_that_does_not_reload_is_left_for_its_unlock(monkeypatch):
    from homalawn import session
    _clock(monkeypatch)
    t = _LockT(loaded=False, status={"outcome": "none", "locked": True})
    la = Launcher(t)
    la.wait_loaded()                                   # no NotLoaded: it returns after the grace
    assert la.deferred is True
    assert t.calls.count("launcher.update_status") == 1
    assert t.calls.count("launcher.ensure_loaded") <= session.LOCKED_GRACE_S * 2 + 4   # not the whole minute


def test_an_unlocked_phone_that_does_not_reload_still_fails(monkeypatch):
    from homalawn.session import NotLoaded
    _clock(monkeypatch)
    la = Launcher(_LockT(loaded=False, status={"outcome": "none", "locked": False}))
    with pytest.raises(NotLoaded):
        la.wait_loaded()
    assert la.deferred is False


def test_a_grid_change_is_never_left_for_the_unlock(monkeypatch):
    """After a grid change the layout that follows needs the new database: no deferral then."""
    from homalawn.session import NotLoaded
    _clock(monkeypatch)
    t = _LockT(loaded=False, status={"outcome": "none", "locked": True})
    with pytest.raises(NotLoaded):
        Launcher(t).wait_loaded("launcher_6_4_4.db")
    assert "launcher.update_status" not in t.calls


def test_locked_reads_the_phones_answer_and_false_when_it_cannot_say():
    assert Launcher(_LockT(True, {"outcome": "none", "locked": True})).locked() is True
    assert Launcher(_LockT(True, {"outcome": "none", "locked": False})).locked() is False
    assert Launcher(_LockT(True, {"outcome": "none"})).locked() is False                      # older build
    assert Launcher(_LockT(True, ProviderError("unknown method", code="unknown_method"))).locked() is False


class OrderedDevice(FakeDevice):
    """Two pages reordered on the phone (patch 0028): screen 1 is shown first, screen 0 (the
    search/date bar's page) second."""
    def __init__(self, tmp_path):
        super().__init__(tmp_path)
        import sqlite3, tempfile, os
        fd, t = tempfile.mkstemp(suffix=".db")
        os.write(fd, self.files[DB]); os.close(fd)
        con = sqlite3.connect(t)
        con.execute("insert into favorites(_id,title,intent,container,screen,cellX,cellY,spanX,spanY,itemType) "
                    "values (2,'com.b','#Intent;action=android.intent.action.MAIN;component=com.b/.A;end',"
                    "-100,1,0,0,1,1,0)")
        con.commit(); con.close()
        self.files[DB] = open(t, "rb").read()
        os.unlink(t)

    def call(self, method, arg=None, extras=None):
        if method == "prefs_list":
            self.events.append(method)
            return {"datastore": {}, "shared": {"workspace_screen_order": {"type": "string", "value": "1, 0"}}}
        return super().call(method, arg, extras)


def screen_ids(dev):
    """{package: screen id} of every top-level app in the phone's database."""
    import sqlite3, tempfile, os
    fd, t = tempfile.mkstemp(suffix=".db")
    os.write(fd, dev.files[DB]); os.close(fd)
    try:
        con = sqlite3.connect(t)
        return {L._parse_intent(r[0])[0]: r[1]
                for r in con.execute("select intent, screen from favorites where container=-100")}
    finally:
        os.unlink(t)


def test_pages_are_read_and_addressed_in_the_phone_order(tmp_path):
    dev = OrderedDevice(tmp_path)
    la = Launcher(AdbTransport(device=dev))
    lay = la.get_layout()
    assert [(s["index"], s["id"]) for s in lay["screens"]] == [(0, 1), (1, 0)]
    assert [i["package"] for i in lay["screens"][0]["items"]] == ["com.b"]
    la.place("com.z/.Z", 0, 1, 0)      # the first page shown: screen 1, its top row is free
    assert screen_ids(dev)["com.z"] == 1
    with pytest.raises(ValueError, match="reserved"):
        la.place("com.q/.Q", 1, 2, 0)  # the second page shown is screen 0, with the search row
    la.place("com.n/.N", 2, 0, 0)      # past the last page: a new screen after the others
    assert screen_ids(dev)["com.n"] == 2
    assert [(s["index"], s["id"]) for s in la.get_layout()["screens"]] == [(0, 1), (1, 0), (2, 2)]
