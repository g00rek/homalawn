import pytest
from homalawn import prefs
from homalawn.adb import AdbTransport

KT_DS = '''
    val darkStatusBar = preference(
        key = booleanPreferencesKey(name = "dark_status_bar"),
    val hotseatMode = preference(key = stringPreferencesKey("hotseat_mode"),
    val a = intPreferencesKey("some_int")
    val s = stringSetPreferencesKey("some_set")
    val f = floatPreferencesKey(
        "some_float")
'''
KT_SH = '''
    val hotseatColumns = IntPref("pref_hotseatColumns", 4, reloadGrid)
    val flag = BoolPref("pref_flag", false)
    val folderRows = IdpIntPref("pref_folderRows", { 4 }, reloadGrid)
    val name = StringPref("pref_name", "")
    val ratio = FloatPref("pref_ratio", 1f)
'''


def test_catalog_from_sources():
    cat = prefs.catalog_from_sources([KT_DS], KT_SH)
    assert cat["dark_status_bar"] == {"store": "datastore", "type": "bool"}
    assert cat["hotseat_mode"] == {"store": "datastore", "type": "string"}
    assert cat["some_int"] == {"store": "datastore", "type": "int"}
    assert cat["some_set"] == {"store": "datastore", "type": "stringset"}
    assert cat["some_float"] == {"store": "datastore", "type": "float"}
    assert cat["pref_hotseatColumns"] == {"store": "shared", "type": "int"}
    assert cat["pref_folderRows"] == {"store": "shared", "type": "int"}
    assert cat["pref_flag"] == {"store": "shared", "type": "bool"}
    assert cat["pref_name"] == {"store": "shared", "type": "string"}
    assert cat["pref_ratio"] == {"store": "shared", "type": "float"}


class Dev:
    def __init__(self, current=None):
        self.calls = []
        self.current = current or {"datastore": {}, "shared": {}}

    def call(self, method, arg=None, extras=None):
        self.calls.append((method, arg, extras))
        if method == "prefs_list":
            return self.current
        return {}


@pytest.fixture
def cat(monkeypatch):
    monkeypatch.setattr(prefs, "catalog", lambda: {
        "dark_status_bar": {"store": "datastore", "type": "bool"},
        "pref_workspaceRows": {"store": "shared", "type": "int"},
        "pref_workspaceColumns": {"store": "shared", "type": "int"},
        "pref_hotseatColumns": {"store": "shared", "type": "int"},
        "pref_allowRotation": {"store": "shared", "type": "bool"},
        "some_set": {"store": "datastore", "type": "stringset"}})


def sets(d):
    return [c for c in d.calls if c[0] == "pref_set"]


def test_set_uses_catalog_when_unset(cat):
    d = Dev()
    prefs.set_(AdbTransport(device=d), "dark_status_bar", "true")
    assert sets(d) == [("pref_set", None, {"store": "datastore", "key": "dark_status_bar",
                                           "type": "bool", "value": "true"})]


def test_set_unknown_key_refused_unless_explicit(cat):
    d = Dev()
    with pytest.raises(ValueError, match="unknown"):
        prefs.set_(AdbTransport(device=d), "nope", "1")
    assert not sets(d)
    prefs.set_(AdbTransport(device=d), "nope", "1", store="shared", type_="int")
    assert sets(d)[0][2]["store"] == "shared"


def test_set_refuses_type_change_of_stored_key(cat):
    d = Dev({"datastore": {"dark_status_bar": {"type": "bool", "value": False}}, "shared": {}})
    with pytest.raises(ValueError, match="type"):
        prefs.set_(AdbTransport(device=d), "dark_status_bar", "x", type_="string")
    with pytest.raises(ValueError, match="bool"):
        prefs.set_(AdbTransport(device=d), "dark_status_bar", "maybe")
    assert not sets(d)
    prefs.set_(AdbTransport(device=d), "dark_status_bar", "true")
    assert sets(d)[0][2]["type"] == "bool"


def test_set_refuses_stored_key_with_other_catalog_type(cat):
    # stored type wins over the catalog
    d = Dev({"datastore": {}, "shared": {"pref_workspaceRows": {"type": "string", "value": "5"}}})
    prefs.set_(AdbTransport(device=d), "pref_workspaceRows", "6")
    assert sets(d)[0][2]["type"] == "string"


def test_bad_values_and_stringset(cat):
    d = Dev()
    with pytest.raises(ValueError):
        prefs.set_(AdbTransport(device=d), "pref_workspaceRows", "five")
    with pytest.raises(ValueError, match="set"):
        prefs.set_(AdbTransport(device=d), "some_set", "a")
    assert not sets(d)


def test_get_all_merges_catalog_defaults(cat):
    d = Dev({"datastore": {}, "shared": {"pref_workspaceRows": {"type": "int", "value": 5}}})
    allp = prefs.get_all(AdbTransport(device=d))
    assert allp["pref_workspaceRows"] == {"store": "shared", "type": "int", "value": 5}
    assert allp["dark_status_bar"] == {"store": "datastore", "type": "bool", "value": None}


def test_restore_plan_touches_catalog_keys_only(cat):
    old = {"datastore": {"dark_status_bar": {"type": "bool", "value": True}},
           "shared": {"pref_workspaceRows": {"type": "int", "value": 5},
                      "pref_allowRotation": {"type": "bool", "value": True},  # unset now: written back
                      "migration_src_db_file": {"type": "string", "value": "launcher_5_4_4.db"},
                      "migration_src_workspace_size": {"type": "string", "value": "4,5"},
                      "version": {"type": "int", "value": 1},
                      "launcher.home_bounce_count": {"type": "int", "value": 0},
                      "not_in_catalog": {"type": "int", "value": 1}}}
    cur = {"datastore": {"dark_status_bar": {"type": "bool", "value": False}},
           "shared": {"pref_workspaceRows": {"type": "int", "value": 5},
                      "pref_workspaceColumns": {"type": "int", "value": 5},  # set after the snapshot
                      "migration_src_db_file": {"type": "string", "value": "launcher_6_5_4.db"},
                      "migration_src_workspace_size": {"type": "string", "value": "5,6"},
                      "version": {"type": "int", "value": 2},
                      "launcher.home_bounce_count": {"type": "int", "value": 3}}}
    writes, removals = prefs.restore_plan(old, cur)
    assert sorted(writes) == [("datastore", "dark_status_bar", "bool", True),
                              ("shared", "pref_allowRotation", "bool", True)]
    assert removals == [("shared", "pref_workspaceColumns")]
    assert prefs.restore_plan(old, cur, keep=frozenset({"pref_workspaceColumns"}))[1] == []


def test_internal_and_stored_unknown_keys_are_refused(cat):
    d = Dev({"datastore": {"some_lawnchair_flag": {"type": "bool", "value": True}},
             "shared": {"migration_src_db_file": {"type": "string", "value": "x"}}})
    for key in ("migration_src_db_file", "migration_src_hotseat_count", "version",
                "launcher.home_bounce_count", "EMPTY_DATABASE_CREATED"):
        with pytest.raises(ValueError, match="internal"):
            prefs.set_(AdbTransport(device=d), key, "1", store="shared", type_="int")
    with pytest.raises(ValueError, match="stored by Homa Lawn itself"):
        prefs.set_(AdbTransport(device=d), "some_lawnchair_flag", "false", store="datastore", type_="bool")
    assert not sets(d)
    prefs.set_(AdbTransport(device=d), "brand_new_key", "1", store="shared", type_="int")  # unknown AND unset: allowed explicitly
    assert sets(d)[0][2]["key"] == "brand_new_key"


def test_legacy_popup_flag_refused_against_real_catalog():
    """Lawnchair's one-shot migration flag sits in the generated catalog; writing it back (e.g. from
    an old prefs snapshot) would re-run the migration and reorder the long-press menu."""
    key = "legacy_popup_options_migrated"
    assert key in prefs.catalog()  # the real, shipped catalog: no fixture
    assert key not in prefs.writable_keys()
    d = Dev({"datastore": {key: {"type": "bool", "value": True}}, "shared": {}})
    with pytest.raises(ValueError, match="internal"):
        prefs.set_(AdbTransport(device=d), key, "false")
    assert not sets(d)


def test_value_text():
    assert prefs.value_text(True) == "true"
    assert prefs.value_text(5) == "5"


# -- Launcher integration (snapshot before write, grid, restore) ------------
import json
from homalawn import snapshots
from homalawn.session import Launcher


@pytest.fixture
def data(tmp_path, monkeypatch):
    monkeypatch.setenv("HOMALAWN_DATA", str(tmp_path / "data"))
    monkeypatch.setattr("time.sleep", lambda s: None)


class StatefulDev:
    """prefs_list/pref_set/ping backed by a dict; ping's db follows the grid prefs."""
    def __init__(self):
        self.shared = {"pref_workspaceColumns": {"type": "int", "value": 4},
                       "pref_workspaceRows": {"type": "int", "value": 5},
                       "pref_hotseatColumns": {"type": "int", "value": 4}}
        self.ds = {"dark_status_bar": {"type": "bool", "value": False}}
        self.log = []
        self.snaps_at_write = []
        self.wp_fail = False
        self.need_grant = False
        self.perm_before = False
        self.op_before = "ignore"
        self.live = False
        self.granted = False
        self.backup_tries = 0
        self.shells = []
        self.wp = b"OLDWALLPAPER"
        self.wp_set = []
        self.serial = "emu"
        import sqlite3, tempfile, os
        fd, t = tempfile.mkstemp(suffix=".db"); os.close(fd)
        con = sqlite3.connect(t)
        con.execute("create table favorites (_id INTEGER PRIMARY KEY, title TEXT, intent TEXT, container INTEGER,"
                    " screen INTEGER, cellX INTEGER, cellY INTEGER, spanX INTEGER, spanY INTEGER, itemType INTEGER,"
                    " appWidgetId INTEGER NOT NULL DEFAULT -1, appWidgetProvider TEXT, restored INTEGER DEFAULT 0,"
                    " rank INTEGER DEFAULT 0)")
        con.execute("insert into favorites values (1,'A','#Intent;component=com.a/.A;end',-100,0,2,3,1,1,0,-1,null,0,0)")
        con.commit(); con.close()
        self.files = {"databases/" + self.db(): open(t, "rb").read()}
        os.unlink(t)

    def db(self):
        s = self.shared
        g = lambda k, d: s.get(k, {}).get("value", d)  # noqa: E731
        return f"launcher_{g('pref_workspaceRows', 5)}_{g('pref_workspaceColumns', 4)}_{g('pref_hotseatColumns', 4)}.db"

    def _migrate(self, old):
        """Lawnchair's grid migration: icons move to a new DB, positions are lost, old DB deleted."""
        new = self.db()
        if new != old and "databases/" + old in self.files:
            self.files["databases/" + new] = self._scramble(self.files.pop("databases/" + old))

    @staticmethod
    def _scramble(data):
        import sqlite3, tempfile, os
        fd, t = tempfile.mkstemp(suffix=".db"); os.write(fd, data); os.close(fd)
        con = sqlite3.connect(t); con.execute("update favorites set cellX=0, cellY=1"); con.commit(); con.close()
        out = open(t, "rb").read(); os.unlink(t)
        return out

    HOME = "/data/user/0/app.homalawn.debug"
    package = "app.homalawn.debug"

    def run_as(self, *a):
        return self.shell("run-as", self.package, *a)

    def _rel(self, path):
        return path[len(self.HOME) + 1:] if path.startswith(self.HOME + "/") else path

    def run_as_write(self, path, data):
        self.files[self._rel(path)] = data
        if self._rel(path) == "files/omalawn/wp":
            self.pushed = data

    def start_home(self):
        pass

    def set_home(self):
        pass

    def _write(self, e):
        self.snaps_at_write.append(sorted(p.name for p in snapshots.root().glob("*/prefs.json")))
        store = self.ds if e["store"] == "datastore" else self.shared
        if e.get("remove"):
            store.pop(e["key"], None)
            return
        v = e["value"]
        store[e["key"]] = {"type": e["type"], "value": {"bool": v == "true", "int": int(v) if e["type"] == "int" else v,
                                                         "string": v}[e["type"]]}

    def run_as_read(self, path):
        path = self._rel(path)
        if path.startswith(("databases/", "files/omalawn/")):
            if path not in self.files:
                from homalawn.adb import AdbError
                raise AdbError("No such file")
            return self.files[path]
        return self.wp

    def shell(self, *a):
        self.shells.append(a)
        if a[:2] == ("pm", "grant") or (a[:3] == ("appops", "set", "--uid") and a[-1] == "allow"):
            self.granted = True
        if a[:2] == ("pm", "revoke") or (a[:3] == ("appops", "set", "--uid") and a[-1] != "allow"):
            self.granted = False
        if a[:2] == ("dumpsys", "package"):
            return f"android.permission.READ_EXTERNAL_STORAGE: granted={'true' if self.perm_before else 'false'}"
        if a[:3] == ("appops", "get", "--uid"):
            return f"READ_MEDIA_IMAGES: {self.op_before}"
        return self.HOME if a[-1] == "pwd" else ""

    def call(self, method, arg=None, extras=None):
        self.log.append((method, extras if method != "prefs_set_many" else json.loads(arg)))
        if method == "prefs_set_many":
            old = self.db()
            for e in json.loads(arg):
                self._write(e)
            self._migrate(old)
            return {}
        if method == "wallpaper_backup":
            from homalawn.adb import ProviderError
            if self.live:
                from homalawn.adb import ProviderError
                raise ProviderError("IllegalStateException: live wallpaper: the current wallpaper is a live wallpaper")
            if self.wp_fail:
                raise ProviderError("cannot back up")
            if self.need_grant and self.granted and getattr(self, "wp_fail_after_grant", False):
                raise ProviderError("boom")
            if self.need_grant and not self.granted:
                self.backup_tries += 1
                raise ProviderError("IllegalStateException: (SecurityException: READ_MEDIA_IMAGES)")
            return {"path": "/x/wp-backup.png"}
        if method == "wallpaper_set":
            self.wp_set.append(arg)
            return {}
        if method == "prefs_list":
            return json.loads(json.dumps({"datastore": self.ds, "shared": self.shared}))
        if method == "pref_set":
            old = self.db()
            self._write(extras)
            self._migrate(old)
            return {}
        if method == "layout_export":
            self.files["files/omalawn/export.db"] = self.files["databases/" + self.db()]
            return {"path": self.HOME + "/files/omalawn/export.db", "db": self.db()}
        if method == "layout_import":
            self.files["databases/" + self.db()] = self.files.pop(self._rel(arg))
            return {"items": 1}
        if method in ("ping", "ensure_loaded"):
            return {"db": self.db(), "model_loaded": True, "loaded": True, "default_home": True}
        if method == "widget_status":
            return {"bound": True}


def test_set_pref_snapshots_before_writing(data, cat):
    d = StatefulDev()
    la = Launcher(AdbTransport(device=d))
    snap = la.set_pref("dark_status_bar", "true")
    files = snapshots.load(snap)
    assert json.loads(files["prefs.json"])["datastore"]["dark_status_bar"]["value"] is False
    assert len(d.snaps_at_write) == 1 and len(d.snaps_at_write[0]) == 1  # snapshot on disk at write time
    assert d.ds["dark_status_bar"]["value"] is True
    assert snapshots.list_auto() == []  # undo is not affected


def test_set_pref_invalid_saves_nothing(data, cat):
    la = Launcher(AdbTransport(device=StatefulDev()))
    with pytest.raises(ValueError):
        la.set_pref("dark_status_bar", "maybe")
    assert snapshots.list_() == []


def test_grid_sets_both_keys_and_refreshes(data):
    d = StatefulDev()
    la = Launcher(AdbTransport(device=d))
    assert la.grid() == (4, 5)
    snap = la.set_grid(5, 6)
    assert la.grid() == (5, 6)
    assert d.shared["pref_workspaceColumns"]["value"] == 5 and d.shared["pref_workspaceRows"]["value"] == 6
    assert d.shared["pref_hotseatColumns"]["value"] == 4
    assert json.loads(snapshots.load(snap)["prefs.json"])["shared"]["pref_workspaceRows"]["value"] == 5
    assert la.set_grid(5, 6) is None  # unchanged: nothing written


def test_grid_limits(data):
    la = Launcher(AdbTransport(device=StatefulDev()))
    with pytest.raises(ValueError):
        la.set_grid(2, 5)
    with pytest.raises(ValueError):
        la.set_grid(4, 11)


def test_restore_reapplies_differing_prefs_only(data, cat):
    d = StatefulDev()
    la = Launcher(AdbTransport(device=d))
    snap = la.set_grid(5, 6)
    la.set_pref("dark_status_bar", "true")
    d.log.clear()
    la.restore(snap)  # prefs-only snapshot: no DB work
    assert la.grid() == (4, 5)
    assert d.ds["dark_status_bar"]["value"] is False  # the snapshot predates it
    calls = [(m, e) for m, e in d.log if m in ("pref_set", "prefs_set_many")]
    assert [m for m, _ in calls] == ["prefs_set_many"]  # one batch, one reload
    assert sorted(w["key"] for w in calls[0][1]) == ["dark_status_bar", "pref_workspaceColumns", "pref_workspaceRows"]


def test_grid_is_one_batch_and_snapshot_first(data):
    d = StatefulDev()
    la = Launcher(AdbTransport(device=d))
    la.set_grid(5, 6)
    writes = [(m, e) for m, e in d.log if m in ("pref_set", "prefs_set_many")]
    assert [m for m, _ in writes] == ["prefs_set_many"]
    assert sorted(w["key"] for w in writes[0][1]) == ["pref_workspaceColumns", "pref_workspaceRows"]
    assert len(d.snaps_at_write[0]) == 1  # prefs.json snapshot already on disk when the write was sent


def test_prefs_set_on_grid_key_waits_for_new_db(data):
    d = StatefulDev()
    la = Launcher(AdbTransport(device=d))
    la.set_pref("pref_workspaceRows", "7")
    assert la.grid() == (4, 7)
    assert [m for m, _ in d.log if m == "prefs_set_many"]


def test_wallpaper_backs_up_before_overwriting(data, tmp_path):
    d = StatefulDev()
    la = Launcher(AdbTransport(device=d))
    f = tmp_path / "n.png"
    f.write_bytes(b"NEW")
    snap = la.set_wallpaper(str(f))
    assert snapshots.load(snap) == {"wallpaper.png": b"OLDWALLPAPER"}
    methods = [m for m, _ in d.log]
    assert methods.index("wallpaper_backup") < methods.index("wallpaper_set")
    assert d.wp_set == ["/data/user/0/app.homalawn.debug/files/omalawn/wp"] and d.pushed == b"NEW"
    la.restore(snap)  # puts the old one back
    assert d.pushed == b"OLDWALLPAPER" and len(d.wp_set) == 2


def test_wallpaper_refuses_without_backup(data, tmp_path):
    d = StatefulDev()
    d.wp_fail = True
    f = tmp_path / "n.png"
    f.write_bytes(b"NEW")
    la = Launcher(AdbTransport(device=d))
    with pytest.raises(ValueError, match="--no-backup"):
        la.set_wallpaper(str(f))
    assert d.wp_set == []
    assert la.set_wallpaper(str(f), backup=False) is None
    assert d.wp_set


def test_wallpaper_backup_grants_media_read_then_retries(data, tmp_path):
    d = StatefulDev()
    d.need_grant = True
    f = tmp_path / "n.png"
    f.write_bytes(b"NEW")
    snap = Launcher(AdbTransport(device=d)).set_wallpaper(str(f))
    assert d.backup_tries == 1 and snapshots.load(snap)["wallpaper.png"] == b"OLDWALLPAPER"
    mut = [a for a in d.shells if a[:2] in (("pm", "grant"), ("pm", "revoke")) or a[:3] == ("appops", "set", "--uid")]
    assert [(a[0], a[-1]) for a in mut] == [("pm", "android.permission.READ_EXTERNAL_STORAGE"),
                                           ("appops", "allow"), ("appops", "ignore"), ("pm", "android.permission.READ_EXTERNAL_STORAGE")]
    assert mut[0][1] == "grant" and mut[3][1] == "revoke"  # granted, then reverted to the prior state
    assert not d.granted


def test_wallpaper_grants_only_what_is_missing(data, tmp_path):
    d = StatefulDev()
    d.need_grant = True
    d.perm_before = True  # permission already there: only the appop is missing
    f = tmp_path / "n.png"
    f.write_bytes(b"NEW")
    Launcher(AdbTransport(device=d)).set_wallpaper(str(f))
    assert not [a for a in d.shells if a[:2] in (("pm", "grant"), ("pm", "revoke"))]
    assert [a[-1] for a in d.shells if a[:3] == ("appops", "set", "--uid")] == ["allow", "ignore"]


def test_wallpaper_state_reverted_even_when_retry_fails(data, tmp_path):
    d = StatefulDev()
    d.need_grant = True
    d.wp_fail_after_grant = True
    f = tmp_path / "n.png"
    f.write_bytes(b"NEW")
    with pytest.raises(ValueError):
        Launcher(AdbTransport(device=d)).set_wallpaper(str(f))
    assert not d.granted and d.wp_set == []


def test_wallpaper_live_refuses(data, tmp_path):
    d = StatefulDev()
    d.live = True
    f = tmp_path / "n.png"
    f.write_bytes(b"NEW")
    la = Launcher(AdbTransport(device=d))
    with pytest.raises(ValueError, match="live wallpaper.*--no-backup"):
        la.set_wallpaper(str(f))
    assert d.wp_set == []
    assert la.set_wallpaper(str(f), backup=False) is None


def _positions(d):
    import sqlite3, tempfile, os
    fd, t = tempfile.mkstemp(suffix=".db"); os.write(fd, d.files["databases/" + d.db()]); os.close(fd)
    out = sqlite3.connect(t).execute("select _id, cellX, cellY from favorites order by _id").fetchall()
    os.unlink(t)
    return out


def test_grid_snapshot_holds_prefs_and_layout_and_restores_positions(data, cat):
    d = StatefulDev()
    la = Launcher(AdbTransport(device=d))
    before = _positions(d)
    snap = la.set_grid(5, 6)
    files = snapshots.load(snap)
    assert snap.startswith("grid-") and set(files) == {"prefs.json", "launcher_5_4_4.db"}
    assert _positions(d) != before  # the migration lost the positions
    la.restore(snap)
    assert la.grid() == (4, 5) and d.db() == "launcher_5_4_4.db"
    assert _positions(d) == before  # the saved DB went back on top of the migrated one


def test_restore_of_old_prefs_snapshot_never_writes_internal_keys(data, cat):
    d = StatefulDev()
    la = Launcher(AdbTransport(device=d))
    d.shared["migration_src_db_file"] = {"type": "string", "value": "launcher_5_4_4.db"}
    d.shared["version"] = {"type": "int", "value": 2}
    snap = la.set_pref("pref_allowRotation", "true")
    d.shared["migration_src_db_file"] = {"type": "string", "value": "launcher_6_5_4.db"}
    d.log.clear()
    la.restore(snap)
    sent = [w for m, e in d.log if m == "prefs_set_many" for w in e]
    assert {w["key"] for w in sent} == {"pref_allowRotation"}
    assert d.shared["migration_src_db_file"]["value"] == "launcher_6_5_4.db"


def test_restore_unsets_a_key_that_was_unset(data, cat):
    d = StatefulDev()
    la = Launcher(AdbTransport(device=d))
    assert "pref_allowRotation" not in d.shared
    snap = la.set_pref("pref_allowRotation", "true")
    assert d.shared["pref_allowRotation"]["value"] is True
    la.restore(snap)
    assert "pref_allowRotation" not in d.shared  # back to Lawnchair's default


def test_undo_walks_prefs_grid_and_wallpaper_in_order(data, cat, tmp_path):
    d = StatefulDev()
    la = Launcher(AdbTransport(device=d))
    f = tmp_path / "n.png"
    f.write_bytes(b"NEW")
    clock = iter(range(100))
    import homalawn.session as S
    real = S.time.strftime
    S.time.strftime = lambda fmt: f"20261001-1200{next(clock):02d}"  # distinct, increasing stamps
    try:
        la.set_pref("dark_status_bar", "true")
        la.set_grid(5, 6)
        la.set_wallpaper(str(f))
        assert la.undo()[0].startswith("wallpaper-") and d.pushed == b"OLDWALLPAPER"
        assert la.undo()[0].startswith("grid-") and la.grid() == (4, 5)
        assert la.undo()[0].startswith("prefs-") and d.ds["dark_status_bar"]["value"] is False
        with pytest.raises(LookupError):
            la.undo()
    finally:
        S.time.strftime = real


def test_label_gap_is_a_known_float_setting():
    # patch 0014 (homalawn-label-gap): the real catalog lists our own setting, so
    # `homalawn prefs set homar_label_gap_dp 6` resolves without --store/--type
    assert prefs.catalog()["homar_label_gap_dp"] == {"store": "datastore", "type": "float"}
    assert "homar_label_gap_dp" in prefs.writable_keys()
    d = Dev()
    assert prefs.resolve(AdbTransport(device=d), "homar_label_gap_dp", "6") == (
        "datastore", "homar_label_gap_dp", "float", "6")
    with pytest.raises(ValueError, match="not a valid float"):
        prefs.resolve(AdbTransport(device=d), "homar_label_gap_dp", "wide")


def test_gesture_values_are_checked_and_instant_search_is_known():
    # patch 0015 (homalawn-search-gesture): the swipe-down gesture can be our instant search
    d = Dev()
    v = '{"type":"homarSearch"}'
    assert prefs.resolve(AdbTransport(device=d), "swipe_down_gesture_handler", v) == (
        "datastore", "swipe_down_gesture_handler", "string", v)
    app = '{"type":"openApp","appName":"Maps","target":{}}'
    assert prefs.resolve(AdbTransport(device=d), "double_tap_gesture_handler", app)[3] == app
    for bad in ('{"type":"homarSerch"}', "homarSearch", "[]"):
        with pytest.raises(ValueError, match="not a gesture"):
            prefs.resolve(AdbTransport(device=d), "swipe_down_gesture_handler", bad)
