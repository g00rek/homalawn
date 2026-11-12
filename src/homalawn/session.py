"""High-level launcher operations: read, validate, apply with snapshot + rollback, undo."""
from __future__ import annotations

import json
import os
import re
import sqlite3
import tempfile
import time
from contextlib import contextmanager, nullcontext
from dataclasses import dataclass

from . import appid
from .transport import NotSupported, ProviderError, Transport, TransportError, shell_of
from . import prefs as prefs_mod, snapshots
from .layout import (ITEM_WIDGET, _parse_intent, components_by_id, count_changes, problems, read_layout, with_screen_ids,
                     write_layout)

# Lawnchair grid preference keys (SharedPreferences), from
# lawnchair/src/app/lawnchair/preferences/PreferenceManager.kt. Only needed when the
# DB file name does not encode the grid (Task 7 `grid` command also sets these).
PREF_COLUMNS = "pref_workspaceColumns"
PREF_ROWS = "pref_workspaceRows"
PREF_HOTSEAT = "pref_hotseatColumns"

_DB_NAME = re.compile(r"launcher_(\d+)_(\d+)_(\d+)\.db$")
PREFS_FILE = "prefs.json"
WALLPAPER_FILE = "wallpaper.png"  # the home screen wallpaper in a snapshot
LOCK_WALLPAPER_FILE = "lock-wallpaper.png"  # the lock screen's own wallpaper in a snapshot
LOCK_FOLLOWS_HOME = "lock-follows-home"  # empty: the lock screen showed the home wallpaper then
WALLPAPER_PARTS = {"home": ("home",), "lock": ("lock",), "both": ("home", "lock")}
GRID_KEYS = (PREF_COLUMNS, PREF_ROWS, PREF_HOTSEAT)
# settings whose change makes Lawnchair reload the grid at once (PreferenceManager's reloadGrid /
# reloadIcons listeners), besides the reload that ends every write
RELOAD_AT_ONCE = frozenset({PREF_HOTSEAT, "pref_iconPackPackage", "pref_folderRows"})
SIDECARS = ("-journal", "-wal", "-shm")
LOAD_TIMEOUT_S = 60
# A locked phone whose home screen has not reloaded after this long: the change is written and
# Lawnchair shows it by itself when the phone is unlocked (Launcher.deferred), so nothing waits on it.
LOCKED_GRACE_S = 10
LOCK_ASK_S = 10   # how long asking whether the phone is locked may take
DEFERRED_NOTE = "The phone is locked: its home screen shows the change when it is unlocked, if it has not already."


class NotLoaded(TransportError):
    """Lawnchair answered but did not finish loading its home screen in time (it does not reload
    while the lock screen shows, for one). The link itself is fine."""
RESTORE_ID_NOT_VALID = 1  # LauncherAppWidgetInfo.FLAG_ID_NOT_VALID: the loader allocates a fresh id


class HomeError(Exception):
    """Lawnchair is not the phone's default home app."""


class RollbackFailed(TransportError):
    pass


class LayoutInvalid(ValueError):
    """A layout the phone would not take; `problems` are layout.problems' located entries."""

    def __init__(self, found: list[dict]):
        super().__init__("; ".join(p["message"] for p in found))
        self.problems = found


class WallpaperUnsaved(ValueError):
    """A look would replace a wallpaper that cannot be saved first. Applying it again with
    `overwrite_wallpaper` replaces it without that backup (after the user said so)."""
    code = "wallpaper_unsaved"


WALLPAPER_STAGED = "wp"  # files/omalawn/wp: the picture handed to launcher.wallpaper_set
MSG_MEDIA_READ = ("Homa Lawn may not read the current wallpaper, so it cannot be saved first. "
                  "Allow Homa Lawn to read photos once on the phone, or pass --no-backup")
MSG_MEDIA_NOT_GRANTABLE = ("Android does not let Homa Lawn read the current wallpaper on this phone, so it cannot be "
                           "saved first over the link; run this once with `--via adb` (which allows it for that one "
                           "call), or pass --no-backup")
# The picture homalawn set last, and the phone's wallpaper id right after it (getWallpaperId of the
# home screen). While the phone still reports that id, that picture IS the current wallpaper, so the
# backup before the next change is taken from here and the phone is not asked to read anything
# (over the link it cannot, on Android 13+). Lives next to the snapshots, in HOMALAWN_DATA.
HELD_WALLPAPER = "current-wallpaper"  # snapshot holding the picture homalawn set last (not undoable)
HELD_LOCK_WALLPAPER = "current-lock-wallpaper"  # the same for the lock screen (lock_id in the record)
_HELD = {"home": ("system_id", "snapshot", HELD_WALLPAPER), "lock": ("lock_id", "lock_snapshot", HELD_LOCK_WALLPAPER)}
WALLPAPER_RECORD = "wallpaper-set.json"
MSG_LOCK_NEEDS_UPDATE = ("the Homa Lawn build on this phone sets the home screen wallpaper only; update it "
                         "(`homalawn update`) to set the lock screen too")
MSG_SIZE_UNKNOWN = ("this phone does not say its screen size, so the picture cannot be cropped to it; update "
                    "Homa Lawn on the phone (`homalawn update`), or leave out --crop")
MSG_LOOK_NO_BACKUP = ("The current wallpaper cannot be saved first, so wallpaper & style was not applied. Allow "
                      "Homa Lawn photo access once on the phone, then try again; or switch Wallpaper off in "
                      "Wallpaper & style.")
WALLPAPER_UNSAVED = "wallpaper_unsaved"  # error code: a look needs consent to replace an unsaved wallpaper
MSG_MEDIA_ASKED = ("Homa Lawn needs access to photos to save the current wallpaper first: tap the Homa Lawn "
                   "notification on the phone and allow it, then run this again (or pass --no-backup)")


def set_home_hint(serial: str | None = None, package: str | None = None) -> str:
    """How to make Homa Lawn the default home again; the adb command only when there is adb."""
    if serial is None:
        return "make it the home app on the phone (Homa Lawn's settings › Permissions › Home app)"
    return (f"make it the home app (on the phone: Homa Lawn's settings › Permissions › Home app; or "
            f"`adb -s {serial} shell cmd package set-home-activity {appid.launcher(package or appid.RELEASE)}`)")


def _wallpaper_record_path():
    return snapshots.root().parent / WALLPAPER_RECORD


def forget_wallpaper() -> None:
    """Drop the record of the picture homalawn set; the next backup reads the phone again."""
    _wallpaper_record_path().unlink(missing_ok=True)


def parse_spec(spec: str) -> tuple[str, str | None]:
    pkg, _, act = spec.partition("/")
    return pkg, (act or None)


def _matches(item: dict, pkg: str, act: str | None) -> bool:
    if item.get("type") != "app" or item.get("package") != pkg:
        return False
    if act is None:
        return True
    have = item.get("activity") or ""
    want = pkg + act if act.startswith(".") else act
    have = pkg + have if have.startswith(".") else have
    return have == want


PREF_SMARTSPACE = "enable_smartspace"  # Lawnchair: smartspace on = screen 0's top row reserved
PREF_SCREEN_ORDER = "workspace_screen_order"  # the launcher's page order, screen ids first to last


@dataclass
class LookPut:
    """What putting a look over the link did: `snapshot` ("" when nothing on the screen changed),
    `shown`, the look source's name as the phone knows it, `scope` ("latest" or "chosen") and
    `mine` (the source is this computer: a look not shown is already on the screen)."""
    snapshot: str
    shown: bool
    source: str | None
    scope: str
    mine: bool = False



def _stored(cur: dict, key: str):
    """A setting's stored value in prefs_mod.current's answer (None: not stored)."""
    for store in ("datastore", "shared"):
        e = cur.get(store, {}).get(key)
        if e is not None:
            return e.get("value")
    return None


def _same_value(stored, text: str) -> bool:
    """Is the text a setting is written with the value it holds already?"""
    if stored is None:
        return False
    if isinstance(stored, bool):
        return text.lower() == str(stored).lower()
    try:
        return float(stored) == float(text)
    except (TypeError, ValueError):
        return str(stored) == text

class Launcher:
    def __init__(self, transport: Transport):
        self.transport = transport
        self.deferred = False  # a reload was left for the phone's unlock (wait_loaded); ops notes it
        self._db: str | None = None
        self._media_asked = False  # the phone is asked for photo access at most once per run
        self._media_msg = MSG_MEDIA_READ  # what to say after that one request

    def _hint(self) -> str:
        t = self.transport
        return set_home_hint(getattr(t, "serial", None) if t.has_shell else None,
                             getattr(getattr(t, "device", None), "package", None))

    # -- info ---------------------------------------------------------------
    def db_name(self) -> str:
        if self._db is None:
            db = self.transport.call("launcher.ping").get("db")
            if not db:  # nothing has opened the home screen in this process yet: have it opened
                self.wait_loaded()
                db = self._ensure_loaded().get("db") or self.transport.call("launcher.ping").get("db")
            if not db:
                raise TransportError("Homa Lawn has not opened its home screen yet")
            self._db = db
        return self._db

    def geometry(self) -> dict:
        """Where the home screen draws (`launcher.device_profile`, patches 0021 and 0024): screen,
        insets, the workspace's cells, icon and label sizes, the dock's cells, in the phone's
        pixels. The phone computes it from the profile its launcher lays out with, so it is the
        same whether or not the launcher is showing."""
        g = self.transport.call("launcher.device_profile")
        if not isinstance(g, dict):
            raise TransportError("the phone sent no home screen geometry")
        return g

    def refresh(self) -> None:
        """Forget cached db name / grid (they change when grid prefs change)."""
        self._db = None

    def _grid3(self) -> tuple[int, int, int]:
        """(cols, rows, hotseat)"""
        m = _DB_NAME.search(self.db_name())
        if m:
            rows, cols, hot = (int(g) for g in m.groups())
            return cols, rows, hot
        shared = self.transport.call("launcher.prefs_list")["shared"]
        get = lambda k, d: shared.get(k, {}).get("value", d)  # noqa: E731
        return int(get(PREF_COLUMNS, 4)), int(get(PREF_ROWS, 5)), int(get(PREF_HOTSEAT, 4))

    def grid(self) -> tuple[int, int]:
        c, r, _ = self._grid3()
        return c, r

    def hotseat(self) -> int:
        return self._grid3()[2]

    # -- default home -------------------------------------------------------
    @contextmanager
    def home_guard(self, require: bool = True, notify=None):
        """Around a write: refuse when Lawnchair is not the default home (unless `require` is
        False, for recovery commands), and if a crash during the write made Android drop it as
        the default home, set it back."""
        before = bool(self.transport.call("launcher.ping").get("default_home"))
        if require and not before:
            raise HomeError(f"Homa Lawn is not the home app on {self.transport.label}; " + self._hint())
        try:
            yield
        finally:
            if before:
                self._repair_home(notify)

    def _repair_home(self, notify=None) -> None:
        try:
            if self.transport.call("launcher.ping").get("default_home"):
                return
        except TransportError:
            return
        if not self.transport.has_shell:  # only a device shell can set the default home
            if notify:
                notify("Homa Lawn lost its place as the home app; " + self._hint())
            return
        shell_of(self.transport).set_home()
        ok = bool(self.transport.call("launcher.ping").get("default_home"))
        if notify:
            notify("Homa Lawn had lost its place as the home app; Homa Lawn was set as the home app again."
                   if ok else "Homa Lawn lost its place as the home app and could not be set "
                              "back; " + self._hint())

    # -- launcher state -----------------------------------------------------
    def locked(self) -> bool:
        """Does the phone say its lock screen shows (or its screen is off)? False when it does not
        say (builds without `locked` in launcher.update_status) or cannot be asked."""
        t = self.transport
        cap = getattr(t, "capped", None)
        try:
            with cap(LOCK_ASK_S) if cap is not None else nullcontext():
                st = t.call("launcher.update_status", {})
        except TransportError:
            return False
        return isinstance(st, dict) and st.get("locked") is True

    def wait_loaded(self, expect_db: str | None = None, timeout: float = LOAD_TIMEOUT_S) -> None:
        """Wait until Lawnchair has loaded its home screen (and, after a grid change, opened
        `expect_db`). `launcher.ensure_loaded` makes the phone load it even when no launcher
        activity is running (over the link nothing else could start one); over adb the launcher
        is also started if it is still not loaded after a few seconds. A locked phone that has not
        reloaded after LOCKED_GRACE_S (or by the timeout) is not waited for: what was written
        stays written, Lawnchair shows it by itself at the unlock, and `deferred` says so. Not
        after a grid change (`expect_db`): the layout that follows needs the new database."""
        self.refresh()
        start = time.monotonic()
        kicked = False
        asked_lock = False
        streak = 0
        while True:
            try:
                p = self._ensure_loaded()
                ready = bool(p.get("loaded")) and (expect_db is None or p.get("db") == expect_db)
            except TransportError:
                ready = False
            streak = streak + 1 if ready else 0
            if streak >= 2:  # twice in a row: a reload that was only just posted is not mistaken for done
                break
            waited = time.monotonic() - start
            if not ready and not kicked and waited > 3 and self.transport.has_shell:
                kicked = True
                try:
                    shell_of(self.transport).start_home()
                except TransportError:
                    pass
            if not ready and expect_db is None and ((waited > LOCKED_GRACE_S and not asked_lock) or waited > timeout):
                asked_lock = True
                if self.locked():
                    self.deferred = True
                    break
            if waited > timeout:
                what = f" with {expect_db}" if expect_db else ""
                raise NotLoaded(f"Homa Lawn did not finish loading its home screen{what} within {int(timeout)}s")
            time.sleep(0.5)
        self.refresh()

    def _ensure_loaded(self) -> dict:
        """{loaded, db}; an older Lawnchair build without `ensure_loaded` answers through ping."""
        try:
            return self.transport.call("launcher.ensure_loaded")
        except ProviderError as e:
            if e.code != "unknown_method" and "unknown method" not in str(e):
                raise
        p = self.transport.call("launcher.ping")
        return {"loaded": p.get("model_loaded"), "db": p.get("db")}

    # -- reading ------------------------------------------------------------
    def _with_local_db(self, data: bytes, fn):
        fd, tmp = tempfile.mkstemp(suffix=".db")
        try:
            with os.fdopen(fd, "wb") as f:
                f.write(data)
            return fn(tmp)
        finally:
            for suf in ("",) + SIDECARS:
                try:
                    os.unlink(tmp + suf)
                except FileNotFoundError:
                    pass

    def _export(self) -> tuple[str, bytes]:
        """(db name, bytes): a consistent single-file copy of the live launcher DB, made by Lawnchair
        on its model thread, so a half-written DB or a WAL that was not copied cannot happen."""
        try:
            return self.transport.export_layout()
        except ProviderError as e:
            if e.code != "busy":
                raise
        self.wait_loaded()  # starting up or reloading: export once it has loaded
        return self.transport.export_layout()

    def get_layout(self, cur: dict | None = None) -> dict:
        """The home screen with its pages in the order the phone shows them (`index` = position,
        `id` = screen id); `cur`: launcher settings already read (launcher.prefs_list)."""
        order, smartspace = self.screen_view(prefs_mod.current(self.transport) if cur is None else cur)
        return self._with_local_db(self._export()[1], lambda p: read_layout(p, self.grid(), order, smartspace))

    @classmethod
    def screen_view(cls, cur: dict) -> tuple[str, bool]:
        """(the launcher's saved page order, whether screen 0 shows the search/date bar), from
        settings read with launcher.prefs_list: what read_layout needs to put the pages in the
        phone's order."""
        order = cur.get("shared", {}).get(PREF_SCREEN_ORDER, {}).get("value")
        return (order if isinstance(order, str) else ""), cls.top_row_reserved_in(cur)

    def _read_current(self, original: dict[str, bytes], grid: tuple[int, int], cur: dict) -> dict:
        order, smartspace = self.screen_view(cur)
        return self._with_local_db(self._single_file(original, self.db_name()),
                                   lambda p: read_layout(p, grid, order, smartspace))

    def _read_all(self) -> dict[str, bytes]:
        name, data = self._export()
        return {name: data}

    def _single_file(self, files: dict[str, bytes], main: str, edit=None) -> bytes:
        """Fold a DB and its journal/WAL into one self-contained file (optionally editing it),
        so exactly one file has to be moved into place on the phone."""
        def run(path: str) -> bytes:
            for fname, data in files.items():
                suf = fname[len(main):]
                if fname != main and suf in SIDECARS:
                    with open(path + suf, "wb") as f:
                        f.write(data)
            con = sqlite3.connect(path)
            try:
                con.execute("select count(*) from favorites").fetchone()  # replays WAL / rolls back hot journal
            finally:
                con.close()
            if edit is not None:
                edit(path)
            with open(path, "rb") as f:
                return f.read()
        return self._with_local_db(files[main], run)

    # -- the one safe write path --------------------------------------------
    def _unbound_widgets_to_placeholders(self, data: bytes) -> tuple[bytes, list[str]]:
        """Widget rows whose id is no longer bound (Launcher3 frees ids missing from its DB on every
        load) would be deleted by the loader; turn them into placeholders it re-binds instead."""
        def edit(path: str) -> list[str]:
            con = sqlite3.connect(path)
            try:
                rows = con.execute("select _id, appWidgetId, appWidgetProvider from favorites "
                                   "where itemType=? and appWidgetId>0", (ITEM_WIDGET,)).fetchall()
                fixed = []
                for rid, wid, prov in rows:
                    if not self.transport.call("launcher.widget_status", {"id": wid}).get("bound", True):
                        con.execute("update favorites set appWidgetId=-1, restored=? where _id=?",
                                    (RESTORE_ID_NOT_VALID, rid))
                        fixed.append(prov or f"widget {rid}")
                con.commit()
                return fixed
            finally:
                con.close()
        out: dict = {}

        def run(path):
            out["fixed"] = edit(path)
            with open(path, "rb") as f:
                return f.read()
        new = self._with_local_db(data, run)
        return new, out["fixed"]

    def _import(self, data: bytes) -> None:
        """Have Lawnchair replace its rows with `data` in one transaction and reload. Lawnchair keeps
        running; the transport cleans up whatever it staged."""
        for attempt in (1, 2):
            try:
                self.transport.import_layout(data, db=self.db_name())
                return
            except ProviderError as e:
                if attempt == 2 or e.code != "busy":
                    raise
            # busy (the phone dropped the refused copy): wait for the reload, then send it again
            self.wait_loaded()

    def _write_db(self, data: bytes, rollback: dict[str, bytes] | None, recover_from: str | None = None) -> list[str]:
        """Put a complete DB file on the phone and wait until Lawnchair has loaded it.
        Returns a note per item the loader dropped. On failure, `rollback` goes back the same way."""
        data, fixed = self._unbound_widgets_to_placeholders(data)
        notes = [f"widget {p} was no longer bound; it comes back as a placeholder to tap" for p in fixed]
        try:
            self._import(data)
            self.wait_loaded()
            if not self.deferred:  # a locked phone loads it at the unlock: nothing to compare yet
                notes += self._dropped(data)
            return notes
        except BaseException as e:
            if rollback is not None:
                try:
                    back, _ = self._unbound_widgets_to_placeholders(self._single_file(rollback, self.db_name()))
                    self._import(back)
                    self.wait_loaded()
                except BaseException as e2:
                    hint = f"; recover with `homalawn snapshot restore {recover_from}`" if recover_from else ""
                    raise RollbackFailed(f"{e}; putting the previous layout back failed too ({e2}){hint}") from e
            raise

    def _dropped(self, written: bytes) -> list[str]:
        """Rows that were written but are gone after Lawnchair loaded the DB."""
        def rows(path):
            con = sqlite3.connect(path)
            try:
                return {r[0]: r[1:] for r in con.execute(
                    "select _id, itemType, intent, appWidgetProvider, title from favorites")}
            finally:
                con.close()
        want = self._with_local_db(written, rows)
        for attempt in range(5):
            try:
                have = self._with_local_db(self._export()[1], rows)
                break
            except (TransportError, sqlite3.DatabaseError):
                if attempt == 4:
                    raise
                time.sleep(1)
        out = []
        for rid in sorted(set(want) - set(have)):
            t, intent, prov, title = want[rid]
            pkg, act = _parse_intent(intent)
            what = prov if t == ITEM_WIDGET else (f"{pkg}/{act}" if pkg else (title or f"item {rid}"))
            out.append(f"Homa Lawn removed {what} while loading (not installed, or no room)")
        return out

    def _unique(self, make) -> str:
        name = make()
        while name in snapshots.list_():  # same-second collision: never overwrite a snapshot
            time.sleep(0.25)
            name = make()
        return name

    def _check_layout(self, layout: dict, known_components: set[str] | None, current: dict | None = None) -> None:
        cols, rows = self.grid()
        g = layout.get("grid") or {}
        if (g.get("cols"), g.get("rows")) != (cols, rows):
            raise ValueError(f"layout grid {g.get('cols')}x{g.get('rows')} (cols x rows) does not match "
                             f"the phone's {cols}x{rows}")
        self._check_cells(layout, known_components, current, self.hotseat())

    def _check_cells(self, layout: dict, known_components: set[str] | None, current: dict | None,
                     hotseat: int, top_row_reserved: bool | None = None) -> None:
        errs = problems(layout, known_components, components_by_id(current) if current else None,
                        top_row_reserved=self._top_row_reserved() if top_row_reserved is None else top_row_reserved,
                        hotseat_count=hotseat)  # the dock's own width, narrower or wider than the grid
        if errs:
            raise LayoutInvalid(errs)

    def _top_row_reserved(self) -> bool:
        """Lawnchair reserves the first screen's top row only while its smartspace (the date/search
        bar) is on; a phone with it off (e.g. after a Nova import without the glance row) has a
        normal top row. Unknown → reserved, the safe guess."""
        try:
            cur = prefs_mod.current(self.transport)
        except Exception:
            return True
        return self.top_row_reserved_in(cur)

    @staticmethod
    def top_row_reserved_in(cur: dict) -> bool:
        """_top_row_reserved, from settings already read (launcher.prefs_list)."""
        v = cur.get("datastore", {}).get(PREF_SMARTSPACE, {}).get("value")
        return True if v is None else str(v).lower() not in ("false", "0")

    def apply_layout(self, layout: dict, known_components: set[str] | None = None) -> list[str]:
        """Snapshot, write, wait for Lawnchair to load it. Returns notes about dropped items."""
        self.wait_loaded()
        cur = prefs_mod.current(self.transport)
        original = self._read_all()
        current = self._read_current(original, self.grid(), cur)
        # a page is addressed by its position on the phone; it is written to that page's screen id
        layout = with_screen_ids(layout, [s["id"] for s in current["screens"]])
        self._check_layout(layout, known_components, current)
        snap = self._unique(snapshots.auto_name)
        snapshots.save(snap, original, device=self.device_key())
        new_db = self._single_file(original, self.db_name(), edit=lambda p: write_layout(p, layout))
        return self._write_db(new_db, original, recover_from=snap)

    # -- preferences --------------------------------------------------------
    def _snapshot(self, prefix: str, cur: dict | None, with_db: bool) -> str:
        files: dict[str, bytes] = {}
        if cur is not None:
            files[PREFS_FILE] = json.dumps(cur, indent=1, sort_keys=True).encode()
        if with_db:
            files.update(self._read_all())
        name = self._unique(lambda: prefix + time.strftime("%Y%m%d-%H%M%S"))
        snapshots.save(name, files, device=self.device_key())
        return name

    @staticmethod
    def _db_for(c: int, r: int, h: int) -> str:
        return f"launcher_{r}_{c}_{h}.db"

    def _send_prefs(self, writes, removals=(), expect_db: str | None = None) -> None:
        writes = list(writes)
        first = [w for w in writes if w[1] in RELOAD_AT_ONCE]
        rest = [w for w in writes if w[1] not in RELOAD_AT_ONCE]
        if first and (rest or removals) and expect_db:
            # Lawnchair reloads the grid the moment one of these changes (its own listener), on
            # top of the reload every write ends with; with columns or rows in the same commit the
            # two loads migrate to different databases at once and the launcher can end up on a
            # deleted one (seen on the AVD with the dock size, 2026-10-14). So they go first, alone.
            c, r, h = self._grid3()
            h = next((int(w[3]) for w in first if w[1] == PREF_HOTSEAT), h)
            prefs_mod.send_many(self.transport, first, ())
            time.sleep(1.0)
            self.wait_loaded(self._db_for(c, r, h))
            writes = rest
        prefs_mod.send_many(self.transport, writes, removals)  # one commit, one reload (as Lawnchair's own screens)
        time.sleep(1.0)  # the reload is posted to Lawnchair's main thread
        self.wait_loaded(expect_db)

    def set_pref(self, key: str, value: str, store: str | None = None, type_: str | None = None) -> str:
        self.wait_loaded()
        cur = prefs_mod.current(self.transport)
        w = prefs_mod.resolve(self.transport, key, value, store, type_, cur=cur)  # validates; sends nothing
        if key in GRID_KEYS:  # same path as `grid`: layout saved with the prefs, wait for the new DB
            c, r, h = self._grid3_from(cur)
            c, r, h = {PREF_COLUMNS: (int(value), r, h), PREF_ROWS: (c, int(value), h),
                       PREF_HOTSEAT: (c, r, int(value))}[key]
            snap = self._snapshot("grid-", cur, with_db=True)
            self._send_prefs([w], expect_db=self._db_for(c, r, h))
        else:
            snap = self._snapshot("prefs-", cur, with_db=False)  # saved BEFORE the write
            self._send_prefs([w])
        return snap

    def set_prefs(self, pairs: list[tuple[str, str]]) -> str:
        """Several settings (key, value) in one undo step: all validated first, then ONE
        `prefs-` snapshot, then one commit. Grid keys are refused (they move the layout; see
        set_grid / apply_home). Returns the snapshot name."""
        if any(k in GRID_KEYS for k, _ in pairs):
            raise ValueError("the grid is set with the grid itself, not with other settings")
        self.wait_loaded()
        cur = prefs_mod.current(self.transport)
        writes = [prefs_mod.resolve(self.transport, k, v, cur=cur) for k, v in pairs]  # validates; sends nothing
        snap = self._snapshot("prefs-", cur, with_db=False)  # saved BEFORE the write
        self._send_prefs(writes)
        return snap

    def apply_look(self, images: dict[str, bytes], prefs: list[tuple[str, str]], notify=None,
                   overwrite_wallpaper: bool = False) -> str:
        """Apply a look in one undo step. `images`: ready-to-set pictures under "home", "lock" or
        "both"; `prefs`: (key, value) launcher settings. Everything that will change is saved in ONE
        snapshot first (`look-...`), then the settings go out in one commit, then the pictures
        (home before lock). Returns the snapshot name, or "" when there was nothing to change.
        A wallpaper that cannot be saved first raises WallpaperUnsaved, unless `overwrite_wallpaper`:
        then it is replaced anyway and the snapshot keeps the settings (and any picture that could
        be saved), so undo still puts the colours and mode back."""
        self._check_pictures(images)
        if not images and not prefs:
            return ""
        if "lock" in self._parts(images):
            self._require_lock(self._wallpaper_info())  # before anything is saved or changed
        self.wait_loaded()
        cur = prefs_mod.current(self.transport) if prefs else None
        writes = [prefs_mod.resolve(self.transport, k, v, cur=cur) for k, v in prefs]  # validates; sends nothing
        name = self._look_snapshot(cur, images, notify, overwrite_wallpaper)
        try:
            if writes:
                self._send_prefs(writes)
            for part in ("home", "lock", "both"):  # home first: Android may move the old home picture to the lock screen
                if part in images:
                    self._put_wallpaper(images[part], part)
        except Exception as e:
            e.args = (f"{e} Partly applied; `homalawn undo` puts back the previous wallpaper & style ({name}).",) \
                + e.args[1:]
            raise
        return name

    @staticmethod
    def _check_pictures(images: dict[str, bytes]) -> None:
        if not set(images) <= set(WALLPAPER_PARTS) or ("both" in images and len(images) > 1):
            raise ValueError("pictures go to home, lock, or both (and both stands alone)")

    @staticmethod
    def _parts(images: dict[str, bytes]) -> tuple[str, ...]:
        return ("home", "lock") if "both" in images else tuple(p for p in ("home", "lock") if p in images)

    def _look_snapshot(self, cur: dict | None, images: dict[str, bytes], notify=None,
                       overwrite_wallpaper: bool = False) -> str:
        """The one `look-` snapshot of everything a look replaces: the settings `cur` (None: the look
        writes none) and the wallpapers of the screens in `images`. A wallpaper that cannot be saved
        raises WallpaperUnsaved unless `overwrite_wallpaper` (then whatever part can be saved is kept)."""
        parts = self._parts(images)
        files: dict[str, bytes] = {}
        if cur is not None:
            files[PREFS_FILE] = json.dumps(cur, indent=1, sort_keys=True).encode()
        if parts:
            try:
                files.update(self._backup_files(parts, notify))
            except (NotSupported, TransportError) as e:
                unsaved = (isinstance(e, NotSupported) and "--no-backup" in str(e)) or (
                    isinstance(e, TransportError) and "live wallpaper" in str(e))
                if not unsaved:
                    if isinstance(e, NotSupported):
                        raise  # already says what to do
                    raise ValueError(f"The current wallpaper cannot be saved first ({e}), so wallpaper & style was "
                                     "not applied.")
                if not overwrite_wallpaper:
                    # a look's one undo step needs the old pictures: replacing them is the user's call
                    raise WallpaperUnsaved(MSG_LOOK_NO_BACKUP) from e
                for part in parts:  # keep whatever part can be saved (say, a lock screen homalawn set)
                    try:
                        files.update(self._backup_files((part,), notify))
                    except (NotSupported, TransportError):
                        pass
        name = self._unique(lambda: "look-" + time.strftime("%Y%m%d-%H%M%S"))
        snapshots.save(name, files, device=self.device_key())
        return name

    def put_look(self, images: dict[str, bytes], prefs: list[tuple[str, str]], key: str, changed_at: int,
                 theme: str, notify=None, overwrite_wallpaper: bool = False) -> LookPut:
        """A look over the link (spec 6.2): stored in the phone's look book with look_put, which never
        touches the screen. Only when the phone answers that it shows it (this computer is the source
        and the look is not on the screen yet) is the snapshot taken, as apply_look takes it, and
        look_show sent; then the held-picture record follows, as after _put_wallpaper. A phone build
        without the book is told to update; there is no other way."""
        self._check_pictures(images)
        self.wait_loaded()
        cur = prefs_mod.current(self.transport) if prefs else None
        writes = [prefs_mod.resolve(self.transport, k, v, cur=cur) for k, v in prefs]  # validates; sends nothing
        try:
            pictures = {p: self.transport.write_private(f"look-{key}-{p}.img", data) for p, data in images.items()}
            r = self.transport.call("launcher.look_put", {
                "key": key, "changed_at": int(changed_at), "theme": theme,
                "prefs": [prefs_mod.wire(w) for w in writes], "pictures": pictures})
        except ProviderError as e:
            if e.code == "unknown_method":
                raise NotSupported(f"Homa Lawn on {self.transport.label} is too old for several computers; "
                                   "update it (`homalawn update`).") from e
            raise
        src = r.get("source") if isinstance(r.get("source"), dict) else {}
        scope = r.get("scope", "latest")
        if not r.get("show"):
            return LookPut("", False, src.get("name") or None, scope,
                           mine=bool(src.get("fp")) and src.get("fp") == self.transport.fingerprint)
        name = self._look_snapshot(cur, images, notify, overwrite_wallpaper)
        try:
            before = self._home_moves_to_lock() if "home" in images else None
            try:
                self.transport.call("launcher.look_show", {"key": key})
            except ProviderError as e:
                if e.code != "not_source":
                    raise
                # another computer became the source since look_put: nothing on the screen changed,
                # and undoing this snapshot would overwrite that computer's look
                snapshots.delete(name)
                return LookPut("", False, None, scope)
            self._after_pictures(images, before)
        except Exception as e:
            e.args = (f"{e} Partly applied; `homalawn undo` puts back the previous wallpaper & style ({name}).",) \
                + e.args[1:]
            raise
        return LookPut(name, True, src.get("name") or None, scope, mine=True)

    @staticmethod
    def _check_grid_size(cur: dict, cols: int, rows: int) -> None:
        limit = 20 if cur["shared"].get("pref_workspace_increase_max_grid_size", {}).get("value") else 10
        for n, v in (("columns", cols), ("rows", rows)):
            if not 3 <= v <= limit:
                raise ValueError(f"{n} must be between 3 and {limit} (Homa Lawn's own limit), got {v}")

    def set_grid(self, cols: int, rows: int) -> str | None:
        self.wait_loaded()
        shared = prefs_mod.current(self.transport)
        cur_c, cur_r, hot = self._grid3_from(shared)
        self._check_grid_size(shared, cols, rows)
        if (cols, rows) == (cur_c, cur_r):
            return None
        writes = [prefs_mod.resolve(self.transport, k, str(v), cur=shared)
                  for k, v in ((PREF_COLUMNS, cols), (PREF_ROWS, rows))]
        # Lawnchair migrates the icons into a new DB and deletes the old one; migrating back does not
        # restore positions, so the current DB goes into the same snapshot as the prefs.
        snap = self._snapshot("grid-", shared, with_db=True)
        self._send_prefs(writes, expect_db=self._db_for(cols, rows, hot))
        return snap

    def apply_home(self, layout: dict, grid: tuple[int, int] | None = None, hotseat: int | None = None,
                   prefs: list[tuple[str, str]] = (), known_components: set[str] | None = None
                   ) -> tuple[str, list[str]]:
        """The home-screen editor's draft in one undo step: the grid (columns, rows), the dock's
        width, launcher settings (icon and label sizes, ...) and the whole layout, which must be
        laid out for the grid it ends up on. Everything is checked before the phone is touched
        (a bad layout raises LayoutInvalid with every problem located); then ONE snapshot
        (`home-...`: the settings and the current database), the settings in one commit, and the
        layout through the safe write path. Returns (snapshot name, notes about dropped items)."""
        self.wait_loaded()
        cur = prefs_mod.current(self.transport)
        c0, r0, h0 = self._grid3()
        cols, rows = grid if grid is not None else (c0, r0)
        hot = h0 if hotseat is None else hotseat
        if (cols, rows) != (c0, r0):
            self._check_grid_size(cur, cols, rows)
        if hot != h0 and not 3 <= hot <= 10:
            raise ValueError(f"dock icons must be between 3 and 10 (Homa Lawn's own limit), got {hot}")
        writes = [prefs_mod.resolve(self.transport, k, v, cur=cur) for k, v in prefs]  # validates; sends nothing
        grid_writes = [prefs_mod.resolve(self.transport, k, str(v), cur=cur)
                       for k, v, was in ((PREF_COLUMNS, cols, c0), (PREF_ROWS, rows, r0), (PREF_HOTSEAT, hot, h0))
                       if v != was]
        g = layout.get("grid") or {}
        if (g.get("cols"), g.get("rows")) != (cols, rows):
            raise ValueError(f"layout grid {g.get('cols')}x{g.get('rows')} (cols x rows) does not match "
                             f"the grid it is applied with, {cols}x{rows}")
        original = self._read_all()
        current = self._read_current(original, (c0, r0), cur)
        layout = with_screen_ids(layout, [s["id"] for s in current["screens"]])
        self._check_cells(layout, known_components, current, hot,
                          top_row_reserved=self.top_row_reserved_in(cur))
        changes = count_changes(current, layout) + len(grid_writes) + sum(
            1 for k, v in prefs if not _same_value(_stored(cur, k), v))
        snap = self._unique(lambda: "home-" + time.strftime("%Y%m%d-%H%M%S"))
        snapshots.save(snap, {PREFS_FILE: json.dumps(cur, indent=1, sort_keys=True).encode(), **original},
                       device=self.device_key(), meta={"section": "home", "changes": changes})
        sent = False
        try:
            if grid_writes or writes:
                expect = self._db_for(cols, rows, hot) if grid_writes else None
                self._send_prefs(grid_writes + writes, expect_db=expect)
                sent = True
            # the layout is written over the database it was read from (its item ids), then
            # replaces every row of the one the launcher uses now (after a grid change, the
            # migrated copy; that copy is what a failed write rolls back to)
            base = self._read_all() if grid_writes else original
            new_db = self._single_file(original, next(iter(original)), edit=lambda p: write_layout(p, layout))
            notes = self._write_db(new_db, base, recover_from=snap)
        except Exception as e:
            if sent:
                e.args = (f"{e} Partly applied; `homalawn undo` puts back the previous home screen ({snap}).",) + e.args[1:]
            raise
        return snap, notes

    @staticmethod
    def _grid3_from(cur: dict) -> tuple[int, int, int]:
        sh = cur.get("shared", {})
        get = lambda k, d: int(sh.get(k, {}).get("value", d))  # noqa: E731
        return get(PREF_COLUMNS, 4), get(PREF_ROWS, 5), get(PREF_HOTSEAT, 4)

    def _prefs_plan(self, saved: dict, cur: dict, db: str | None = None):
        # an unset grid key means the grid the snapshot's database was made for (`db`, e.g. a fresh
        # install's 4x6 picked from the screen), else Lawnchair's default; make that explicit so it
        # can be put back
        saved = json.loads(json.dumps(saved))
        m = _DB_NAME.search(db or "")
        dflt = (int(m.group(2)), int(m.group(1)), int(m.group(3))) if m else self._grid3_from(saved)
        for k, d in zip(GRID_KEYS, dflt):
            saved.setdefault("shared", {}).setdefault(k, {"type": "int", "value": d})
        for k, v in zip(GRID_KEYS, self._grid3_from(saved)):
            saved.setdefault("shared", {}).setdefault(k, {"type": "int", "value": v})
        writes, removals = prefs_mod.restore_plan(saved, cur, keep=frozenset(GRID_KEYS))
        target = self._db_for(*self._grid3_from(saved))
        # the grid the phone uses now, from its database: a fresh install picks one from the screen
        # (e.g. 4x6) without writing the grid settings, which then read as the 4x5 default
        now = self._grid3()
        grid_changes = target != self._db_for(*now)
        if grid_changes:  # every grid setting that differs from what the phone uses is written
            have = {w[1] for w in writes}
            writes += [("shared", k, "int", saved["shared"][k]["value"]) for k, v in zip(GRID_KEYS, now)
                       if k not in have and int(saved["shared"][k]["value"]) != v]
        return writes, removals, (target if grid_changes else None)

    # -- wallpaper ----------------------------------------------------------
    def _wallpaper_info(self) -> dict | None:
        try:
            info = self.transport.call("launcher.wallpaper_info")
        except TransportError:
            return None
        return info if isinstance(info, dict) else None

    @staticmethod
    def _wallpaper_id(info: dict | None, part: str = "home") -> int | None:
        wid = (info or {}).get(_HELD[part][0])
        return wid if isinstance(wid, int) and not isinstance(wid, bool) else None

    def _system_wallpaper_id(self) -> int | None:
        return self._wallpaper_id(self._wallpaper_info())

    @staticmethod
    def _lock_follows_home(info: dict | None) -> bool:
        """The lock screen has no wallpaper of its own and shows the home one (Android reports a
        negative lock id then)."""
        lid = Launcher._wallpaper_id(info, "lock")
        return bool((info or {}).get("lock_follows_home")) or (lid is not None and lid < 0)

    def _require_lock(self, info: dict | None) -> None:
        """An older Lawnchair build ignores `which` and would set or read the HOME wallpaper; only
        a build that reports lock_follows_home knows the lock screen."""
        if not isinstance(info, dict) or "lock_follows_home" not in info:
            raise NotSupported(MSG_LOCK_NEEDS_UPDATE)

    def screen_size(self) -> tuple[int, int]:
        """The phone's whole screen in pixels, portrait (width, height)."""
        info = self._wallpaper_info() or {}
        w, h = info.get("width"), info.get("height")
        if isinstance(w, int) and isinstance(h, int) and w > 0 and h > 0:
            return w, h
        if self.transport.has_shell:  # an older build over adb: ask the window manager
            out = shell_of(self.transport).shell("wm", "size")
            sizes = re.findall(r"(\d+)x(\d+)", out.splitlines()[-1] if out.strip() else "")
            if sizes:
                a, b = map(int, sizes[-1])
                return min(a, b), max(a, b)
        raise NotSupported(MSG_SIZE_UNKNOWN)

    def device_key(self) -> str | None:
        """Which phone this is, for snapshots and the wallpaper record: the paired certificate over
        the link, the adb serial over adb (so the same phone over the other transport is another
        key). Wallpaper ids are small numbers, so another device's id may well coincide."""
        t = self.transport
        peer = getattr(t, "peer", None)
        if t.name == "link" and getattr(peer, "fp", None):
            return f"link:{peer.fp}"
        serial = getattr(t, "serial", None)
        return f"adb:{serial}" if t.name == "adb" and serial else None

    def _wallpaper_record(self) -> dict:
        """The record of the pictures homalawn set last on THIS phone ({} for another one)."""
        try:
            rec = json.loads(_wallpaper_record_path().read_text())
        except (OSError, ValueError):
            return {}
        key = self.device_key()
        return rec if isinstance(rec, dict) and key is not None and rec.get("device") == key else {}

    def _held_wallpaper(self, part: str = "home", info: dict | None = None) -> bytes | None:
        """The picture homalawn set last on the home (or lock) screen, when the phone still shows
        it (same wallpaper id)."""
        id_key, snap_key, _ = _HELD[part]
        rec = self._wallpaper_record()
        if not rec.get(snap_key):
            return None  # nothing set there by homalawn (or set on another phone / over the other transport)
        try:
            data = snapshots.load(rec[snap_key])[LOCK_WALLPAPER_FILE if part == "lock" else WALLPAPER_FILE]
        except (OSError, ValueError, KeyError, TypeError):
            return None
        wid = self._wallpaper_id(info if info is not None else self._wallpaper_info(), part)
        if wid is None or wid != rec.get(id_key) or not data:
            return None  # changed outside homalawn since (or unknown): only the phone has it
        return data

    def _remember_wallpaper(self, pictures: dict[str, bytes | None], info: dict | None = None,
                            moved: dict[str, int] | None = None) -> None:
        """After a successful change: for each part in `pictures` keep the picture now shown there
        and the id the phone reports for it (None: forget that part). `moved`: part -> the id the
        phone gave the same picture without homalawn sending it (a re-numbered home wallpaper)."""
        key = self.device_key()
        if key is None:
            forget_wallpaper()
            return
        if info is None:
            info = self._wallpaper_info()
        rec = self._wallpaper_record()
        rec["device"] = key
        try:
            for part, data in pictures.items():
                id_key, snap_key, held = _HELD[part]
                rec.pop(id_key, None)
                rec.pop(snap_key, None)
                wid = self._wallpaper_id(info, part)
                if data and wid is not None and wid >= 0:
                    fname = LOCK_WALLPAPER_FILE if part == "lock" else WALLPAPER_FILE
                    snapshots.save(held, {fname: data}, device=key)
                    rec[id_key], rec[snap_key] = wid, held
            for part, wid in (moved or {}).items():
                if rec.get(_HELD[part][1]):
                    rec[_HELD[part][0]] = wid
            if not any(rec.get(_HELD[p][1]) for p in _HELD):
                forget_wallpaper()
                return
            path = _wallpaper_record_path()
            path.parent.mkdir(parents=True, exist_ok=True)
            tmp = path.with_suffix(".tmp")
            tmp.write_text(json.dumps(rec))
            os.replace(tmp, path)
        except OSError:
            forget_wallpaper()  # without the record the next backup just asks the phone

    def _call_backup(self, notify=None, part: str = "home", info: dict | None = None) -> bytes:
        held = self._held_wallpaper(part, info)
        if held is not None:
            return held
        t = self.transport
        try:
            return t.wallpaper_backup(part)
        except ProviderError as e:
            if "SecurityException" not in str(e):
                raise
        # Android only lets an app read the system wallpaper with the media-read permission.
        # Over adb, grant what is missing for this one call and put the previous state back right
        # after; without a shell only the user can allow it, once, on the phone.
        if not t.has_shell:
            if self._ask_media_permission():
                return t.wallpaper_backup(part)  # allowed on the phone since the first try
            raise NotSupported(self._media_msg)
        sh = shell_of(t)
        had_perm, mode = sh.media_access()
        if notify:
            notify("Temporarily allowing Homa Lawn to read the wallpaper (reverted right after the backup).")
        try:
            sh.allow_media_read(had_perm, mode)
            return sh.wallpaper_backup(part)
        finally:
            sh.revert_media_read(had_perm, mode)
            if not had_perm:
                self.wait_loaded()  # revoking a permission kills Lawnchair; wait until it is back

    def _ask_media_permission(self) -> bool:
        """Without a shell only the user can allow photo access: have the phone post its one
        notification for that (once per run). True when access is already there."""
        if self._media_asked:
            return False
        self._media_asked = True
        try:
            r = self.transport.call("launcher.request_media_permission")
        except ProviderError:  # an older Lawnchair build without the request
            return False
        if r.get("granted"):
            return True
        if r.get("asked"):
            self._media_msg = MSG_MEDIA_ASKED
        elif r.get("why") == "not_grantable":
            self._media_msg = MSG_MEDIA_NOT_GRANTABLE
        return False

    def _backup_files(self, parts, notify=None) -> dict[str, bytes]:
        """What a snapshot keeps of the wallpapers in `parts`: the home picture, and the lock
        screen's own picture or the note that the lock screen showed the home one."""
        files: dict[str, bytes] = {}
        info = self._wallpaper_info() if "lock" in parts else None
        for part in parts:
            if part == "lock":
                self._require_lock(info)
                if self._lock_follows_home(info):
                    files[LOCK_FOLLOWS_HOME] = b""
                    continue
                try:
                    data = self._call_backup(notify, "lock", info)
                except ProviderError as e:
                    if e.code != "lock_follows_home" and "lock_follows_home" not in str(e):
                        raise
                    files[LOCK_FOLLOWS_HOME] = b""
                    continue
            else:
                data = self._call_backup(notify, "home", info)
            if not data:
                raise TransportError("wallpaper backup came back empty")
            files[LOCK_WALLPAPER_FILE if part == "lock" else WALLPAPER_FILE] = data
        return files

    def backup_wallpaper(self, notify=None, prefix: str = "wallpaper-", target: str = "home") -> str:
        """Save the current home (`target` "home"), lock screen ("lock") or both wallpapers into one
        local snapshot first; returns the snapshot name."""
        files = self._backup_files(WALLPAPER_PARTS[target], notify)
        name = self._unique(lambda: prefix + time.strftime("%Y%m%d-%H%M%S"))
        snapshots.save(name, files, device=self.device_key())
        return name

    def set_wallpaper(self, local: str, backup: bool = True, notify=None, target: str = "home",
                      size: tuple[int, int] | None = None) -> str | None:
        """Set the home (`target` "home"), lock screen ("lock") or both wallpapers from an image
        file; `size` (width, height): scale it to cover that and keep the centre first. The
        wallpapers it replaces are saved in one snapshot first, unless not `backup`."""
        if target not in WALLPAPER_PARTS:
            raise ValueError(f"target must be home, lock or both, not {target!r}")
        with open(local, "rb") as f:
            data = f.read()
        if size is not None:
            from .wallcrop import crop_center
            data = crop_center(data, *size)
        if "lock" in WALLPAPER_PARTS[target]:
            self._require_lock(self._wallpaper_info())  # before anything is saved or changed
        snap = None
        if backup:
            try:
                snap = self.backup_wallpaper(notify, target=target)
            except NotSupported:
                raise  # already says what to do
            except TransportError as e:
                if "live wallpaper" in str(e):
                    raise ValueError("the current wallpaper is a live wallpaper and cannot be saved; "
                                     "pass --no-backup to replace it anyway")
                raise ValueError(f"cannot save the current wallpaper first ({e}); "
                                 "pass --no-backup to overwrite it anyway")
        self._put_wallpaper(data, target)
        return snap

    def _push_wallpaper(self, local: str) -> None:
        with open(local, "rb") as f:
            self._put_wallpaper(f.read())

    def _put_wallpaper(self, data: bytes, target: str = "home") -> None:
        before = self._home_moves_to_lock() if target == "home" else None
        path = self.transport.write_private(WALLPAPER_STAGED, data)  # 0600 in files/omalawn/
        self.transport.call("launcher.wallpaper_set", {"path": path} | ({"which": target} if target != "home" else {}))
        self._after_pictures({target: data}, before)

    def _home_moves_to_lock(self) -> tuple[int | None, bytes | None] | None:
        """Read BEFORE a new home picture goes on: Android moves the old home picture to the lock
        screen when the lock screen showed it, and what homalawn held for home then becomes the lock
        screen's own picture. (old home id, held picture), or None when the lock screen has its own."""
        info = self._wallpaper_info()
        return (self._wallpaper_id(info), self._held_wallpaper("home", info)) if self._lock_follows_home(info) else None

    def _after_pictures(self, images: dict[str, bytes], before=None) -> None:
        """The held-picture record after `images` ("home", "lock", "both") went on screen; `before`
        is _home_moves_to_lock's reading from ahead of the change."""
        info = self._wallpaper_info()
        pictures: dict[str, bytes | None] = {p: data for part, data in images.items() for p in WALLPAPER_PARTS[part]}
        if before and before[1] and before[0] is not None and "lock" not in pictures \
                and self._wallpaper_id(info, "lock") == before[0]:
            pictures["lock"] = before[1]
        self._remember_wallpaper(pictures, info)

    def _reset_lock_wallpaper(self) -> None:
        """The lock screen shows the home wallpaper again. Android re-numbers the home wallpaper
        while doing so; the picture homalawn held for it is still the one shown."""
        old = self._wallpaper_id(self._wallpaper_info())
        self.transport.call("launcher.wallpaper_lock_reset")
        info = self._wallpaper_info()
        new = self._wallpaper_id(info)
        moved = {"home": new} if (new is not None and old is not None and old != new
                                  and self._wallpaper_record().get("system_id") == old) else None
        self._remember_wallpaper({"lock": None}, info, moved=moved)

    def _backup_before_restore(self, target: str = "home") -> str:
        """The current wallpaper(s), saved before a restore replaces them (kept out of the undo
        list, so a repeated undo does not swap two wallpapers forever). Raises when they cannot be
        saved: the restore then changes nothing unless --no-backup."""
        try:
            return self.backup_wallpaper(
                prefix=snapshots.pre_restore_name().replace("pre-restore-", "pre-restore-wallpaper-"),
                target=target)
        except NotSupported:
            raise  # one line naming `--via adb` once or --no-backup
        except TransportError as e:
            if "live wallpaper" in str(e):
                raise ValueError("the current wallpaper is a live wallpaper; restoring would replace it with a "
                                 "picture; pass --no-backup to restore anyway")
            raise ValueError(f"cannot save the current wallpaper before restoring ({e}); "
                             "pass --no-backup to restore anyway")

    def _check_device(self, name: str) -> None:
        have = snapshots.device_of(name)
        here = self.device_key()
        if have is None:
            raise ValueError(f"snapshot {name!r} does not say which phone it was taken on; if it is this "
                             "phone's, restore it with --force")
        if have != here:
            raise ValueError(f"snapshot {name!r} was taken on {have}, not on this phone ({here}); "
                             "restore it with --force only if they are the same phone")

    # -- restore / undo -----------------------------------------------------
    def restore(self, name: str, force: bool = False, no_backup: bool = False) -> list[str]:
        """Put a snapshot back: preferences first (they may change the grid, i.e. the DB name),
        then the wallpaper, then the layout DB through the safe write path. A snapshot of another
        (or an unknown) phone is refused unless `force`; a wallpaper that cannot be saved first
        stops the restore before anything changed, unless `no_backup`."""
        if not force:
            self._check_device(name)
        files = snapshots.load(name)
        blob = files.pop(PREFS_FILE, None)
        wp = files.pop(WALLPAPER_FILE, None)
        lock_wp = files.pop(LOCK_WALLPAPER_FILE, None)
        lock_follows = files.pop(LOCK_FOLLOWS_HOME, None) is not None and lock_wp is None
        wp_target = {(True, False): "home", (False, True): "lock", (True, True): "both"}.get(
            (wp is not None, lock_wp is not None or lock_follows))
        mains = [f for f in files if not f.endswith(SIDECARS)]
        if files and len(mains) != 1:
            raise ValueError(f"snapshot {name!r} holds {len(mains)} launcher databases; expected one")
        target_db = mains[0] if files else None

        writes, removals, grid_db = [], [], None
        cur = None
        if blob is not None or target_db:
            self.wait_loaded()
        if blob is not None:
            cur = prefs_mod.current(self.transport)
            writes, removals, grid_db = self._prefs_plan(json.loads(blob), cur, target_db)
        db_after = grid_db or self.db_name()
        if target_db and target_db != db_after:
            raise ValueError(f"snapshot {name!r} holds {target_db}, but the phone will use {db_after}; "
                             "it needs the grid it was taken with")
        if wp_target and "lock" in WALLPAPER_PARTS[wp_target]:
            self._require_lock(self._wallpaper_info())  # an older build would put it on the home screen
        if wp_target and not no_backup:
            self._backup_before_restore(wp_target)  # before anything changes: a refusal leaves the phone as it was
        if writes or removals or target_db:
            # one pre-restore snapshot of everything this restore changes
            self._snapshot("pre-restore-", cur if (writes or removals) else None,
                           with_db=bool(target_db or grid_db))
        if writes or removals:
            self._send_prefs([(s, k, t, prefs_mod.value_text(v)) for s, k, t, v in writes], removals,
                             expect_db=grid_db)
        if wp is not None:
            self._put_wallpaper(wp)  # home first: Android may move the old home picture to the lock screen
        if lock_wp is not None:
            self._put_wallpaper(lock_wp, "lock")
        elif lock_follows:
            self._reset_lock_wallpaper()
        if not target_db:
            return []
        current = self._read_all()
        return self._write_db(self._single_file(files, target_db), current)

    def undo(self, no_backup: bool = False) -> tuple[str, list[str]]:
        """Restore the newest snapshot homalawn took of this phone before one of its changes, and
        drop it."""
        names = snapshots.list_undoable(self.device_key())
        if not names:
            raise LookupError(f"nothing to undo on this phone ({self.device_key() or 'unknown device'})")
        name = names[0]
        notes = self.restore(name, force=True, no_backup=no_backup)
        snapshots.delete(name)
        return name, notes

    # -- edits --------------------------------------------------------------
    @staticmethod
    def _find(layout: dict, pkg: str, act: str | None):
        """Yield (container_list, item) for every matching app, incl. in folders."""
        lists = [layout.get("hotseat", [])] + [s["items"] for s in layout.get("screens", [])]
        for lst in lists:
            for it in list(lst):
                if _matches(it, pkg, act):
                    yield lst, it
                if it.get("type") == "folder":
                    for kid in list(it.get("items", [])):
                        if _matches(kid, pkg, act):
                            yield it["items"], kid

    @staticmethod
    def _screen(layout: dict, index: int) -> list:
        for s in layout["screens"]:
            if s["index"] == index:
                return s["items"]
        s = {"index": index, "items": []}
        layout["screens"].append(s)
        layout["screens"].sort(key=lambda s: s["index"])
        return s["items"]

    def place(self, spec: str, screen: int, x: int, y: int, known_components: set[str] | None = None) -> list[str]:
        pkg, act = parse_spec(spec)
        layout = self.get_layout()
        found = list(self._find(layout, pkg, act))
        if found:
            lst, item = found[0]
            lst.remove(item)
        else:
            if not act:
                raise ValueError(f"{pkg}: an activity is required (use package/activity)")
            item = {"type": "app", "package": pkg, "activity": act}
        item = {k: v for k, v in item.items() if k not in ("x", "y")}
        item.update(x=x, y=y)
        self._screen(layout, screen).append(item)
        return self.apply_layout(layout, known_components)

    def remove(self, spec: str) -> list[str]:
        layout = self.get_layout()
        if spec.isdigit():
            target = int(spec)
            hits = []
            lists = [layout.get("hotseat", [])] + [s["items"] for s in layout["screens"]]
            for lst in lists:
                for it in lst:
                    if it.get("id") == target:
                        hits.append((lst, it))
                    for kid in it.get("items", []) if it.get("type") == "folder" else []:
                        if kid.get("id") == target:
                            hits.append((it["items"], kid))
        else:
            pkg, act = parse_spec(spec)
            hits = list(self._find(layout, pkg, act))
        if not hits:
            raise LookupError(f"{spec} is not on the home screen")
        for lst, it in hits:
            lst.remove(it)
        return self.apply_layout(layout)

    def folder_create(self, name: str, screen: int, x: int, y: int,
                      apps: list[tuple[str, str | None]], known_components: set[str] | None = None) -> list[str]:
        layout = self.get_layout()
        kids = []
        for pkg, act in apps:
            found = list(self._find(layout, pkg, act))
            if found:
                lst, it = found[0]
                lst.remove(it)
                kids.append({k: v for k, v in it.items() if k not in ("x", "y")})
            else:
                if not act:
                    raise ValueError(f"{pkg}: an activity is required (use package/activity)")
                kids.append({"type": "app", "package": pkg, "activity": act})
        self._screen(layout, screen).append(
            {"type": "folder", "title": name, "x": x, "y": y, "items": kids})
        return self.apply_layout(layout, known_components)
