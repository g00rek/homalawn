"""Move a Nova Launcher layout onto the phone (`homalawn nova-import`, `phone_nova_import`).

The phone converts: `launcher.nova_convert` runs Lawnchair's own Nova converter
(NovaBackupConverter, patch 0012) on the backup and hands back the launcher DB that
Lawnchair's "Restore Nova backup" screen would restore, without touching anything. The
computer then applies it the safe way: one snapshot of everything the import changes (the
settings, which hold the grid, and the layout), the grid settings the converter would write,
then the rows through the in-process layout import (no restart, the link stays up). `undo`
puts the snapshot back.

What the computer adds on top of the converter is the account: every item of the Nova home
screen (screens, dock, folders) is placed or dropped with a reason, and widgets that wait for
the person's tap are counted. The backup itself is read here too (nova.db is plain SQLite), only to
name what the converter left out.
"""
from __future__ import annotations

import io
import re
import sqlite3
import tempfile
import time
import zipfile
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import unquote

from . import prefs as prefs_mod, snapshots
from .layout import (CONTAINER_DESKTOP, CONTAINER_HOTSEAT, ITEM_APP, ITEM_DEEP_SHORTCUT, ITEM_FOLDER,
                     ITEM_SHORTCUT, ITEM_WIDGET, RESTORE_ID_NOT_VALID, _parse_intent, widget_state)
from .session import PREF_COLUMNS, PREF_HOTSEAT, PREF_ROWS, Launcher

NOVA_PKG = "com.teslacoilsw.launcher"
NOVA_FILES = ("nova.xml", "nova.db")  # all the converter reads; the rest of the backup stays here
ITEM_CUSTOM_WIDGET = 5
PREF_SMARTSPACE = "enable_smartspace"
PREF_ICON_PACK = "pref_iconPackPackage"
SNAPSHOT_PREFIX = "nova-"
WIDGET_WAIT_S = 20  # how long the import waits for widgets to leave "loading"
_SMART = re.compile(r"FOLDER%3A(-\d+)")

KIND = {ITEM_APP: "app", ITEM_SHORTCUT: "shortcut", ITEM_FOLDER: "folder", ITEM_WIDGET: "widget",
        ITEM_CUSTOM_WIDGET: "widget", ITEM_DEEP_SHORTCUT: "shortcut"}

R_GRID = "outside Homa Lawn's grid"
R_DOCK = "beyond the dock's places"
R_NOT_INSTALLED = "not installed"
R_WIDGET_APP = "widget's app not installed"
R_NOVA_ONLY = "a Nova-only action, Homa Lawn has no such thing"
R_NO_FOLDER = "its folder is not in the backup"
R_EMPTY_FOLDER = "none of its apps are installed"
R_LOADER = "Homa Lawn could not place it (no room)"


class NovaError(ValueError):
    pass


def slim_backup(data: bytes) -> bytes:
    """The backup cut down to what the converter reads (nova.xml, nova.db): the phone gets no
    more of it than it needs. Raises NovaError for anything that is not a Nova backup."""
    try:
        zin = zipfile.ZipFile(io.BytesIO(data))
    except zipfile.BadZipFile:
        raise NovaError("not a Nova backup (a .novabackup file is a zip; this is not one)")
    with zin:
        names = set(zin.namelist())
        missing = [n for n in NOVA_FILES if n not in names]
        if missing:
            raise NovaError(f"not a Nova backup: {' and '.join(missing)} missing")
        try:
            files = {n: zin.read(n) for n in NOVA_FILES}
        except (zipfile.BadZipFile, OSError, EOFError) as e:
            raise NovaError(f"the backup file is damaged ({e})")
    if not files["nova.db"].startswith(b"SQLite format 3\x00"):
        raise NovaError("the backup is damaged: its nova.db is not a database")

    def check(path):
        con = sqlite3.connect(path)
        try:
            con.execute("select _id, container, itemType, screen, cellX, cellY, spanX, spanY "
                        "from favorites limit 1").fetchall()
        finally:
            con.close()
    try:
        _db_file(files["nova.db"], check)
    except sqlite3.DatabaseError as e:
        raise NovaError(f"the backup is damaged: Nova's database cannot be read ({e})")
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
        for n in NOVA_FILES:
            z.writestr(n, files[n])
    return out.getvalue()


def _db_file(data: bytes, fn):
    with tempfile.TemporaryDirectory() as d:
        p = Path(d) / "x.db"
        p.write_bytes(data)
        return fn(str(p))


@dataclass
class Item:
    id: int
    kind: str                 # app, shortcut, folder, widget
    label: str
    where: str                # "screen 2", "dock", "folder Gry"
    package: str | None = None
    provider: str | None = None
    status: str = "placed"    # placed / dropped / unfolded (a folder of one app, shown as that app)
    reason: str | None = None
    state: str | None = None  # widgets after the import: ready, needs setup, needs permission, loading


@dataclass
class Plan:
    grid: tuple[int, int, int]           # cols, rows, dock places the import uses
    grid_from_backup: bool
    glance_row: bool
    subgrid: bool
    icon_pack: str | None
    icon_pack_installed: bool | None
    items: list[Item] = field(default_factory=list)
    db: bytes = b""                      # the rows to write (dropped ones already left out)

    @property
    def placed(self) -> list[Item]:
        return [i for i in self.items if i.status == "placed"]

    @property
    def dropped(self) -> list[Item]:
        return [i for i in self.items if i.status == "dropped"]

    @property
    def unfolded(self) -> list[Item]:
        return [i for i in self.items if i.status == "unfolded"]


def _all_rows(backup: bytes) -> list[dict]:
    with zipfile.ZipFile(io.BytesIO(backup)) as z:
        db = z.read("nova.db")

    def read(path):
        con = sqlite3.connect(path)
        con.row_factory = sqlite3.Row
        try:
            return [dict(r) for r in con.execute("select * from favorites where itemType != -1 order by _id")]
        finally:
            con.close()
    return _db_file(db, read)


def nova_rows(backup: bytes, rows: list[dict] | None = None) -> list[dict]:
    """The Nova home screen's items (screens, dock, folder contents), as the converter selects
    them: app drawer groups (containers -20X) only count when a desktop folder shows one."""
    rows = _all_rows(backup) if rows is None else rows
    smart = {}
    for r in rows:
        if r["itemType"] == ITEM_FOLDER and r["container"] == CONTAINER_DESKTOP and r.get("intent"):
            m = _SMART.search(r["intent"])
            if m:
                smart[int(m.group(1))] = r["_id"]
    out = []
    for r in rows:
        c = r["container"]
        if c in (CONTAINER_DESKTOP, CONTAINER_HOTSEAT) or c >= 0 or c in smart:
            out.append({**r, "container": smart.get(c, c)})
    return out


def _target_pkg(row: dict) -> str | None:
    if row["itemType"] in (ITEM_WIDGET, ITEM_CUSTOM_WIDGET):
        prov = row.get("appWidgetProvider") or ""
        return prov.split("/", 1)[0] or None
    pkg, _ = _parse_intent(row.get("intent"))
    if pkg is None and row.get("intent"):
        m = re.search(r";package=([^;]+)", row["intent"]) or re.search(r"^([\w.]+)/", row["intent"])
        pkg = unquote(m.group(1)) if m else None
    return pkg


def _norm_provider(p: str) -> str:
    pkg, _, cls = p.partition("/")
    return f"{pkg}/{pkg + cls if cls.startswith('.') else cls}"


def build_plan(backup: bytes, meta: dict, converted: bytes, apps: list[dict], widgets: list[dict],
               current_grid: tuple[int, int, int]) -> Plan:
    """Account for every Nova item and cut the converted DB down to what can be placed.

    `meta` is `launcher.nova_convert`'s answer, `converted` its DB, `apps` / `widgets` the
    phone's launchable apps and widget providers, `current_grid` (cols, rows, dock) the phone's
    grid now (kept when the backup does not say its own)."""
    cols, rows, hot = meta.get("columns"), meta.get("rows"), meta.get("hotseat")
    from_backup = None not in (cols, rows, hot)
    grid = (int(cols), int(rows), int(hot)) if from_backup else current_grid
    glance = bool(meta.get("glance_row"))
    app_labels: dict[str, str] = {}
    for a in apps:
        app_labels.setdefault(a["package"], a.get("label") or a["package"])
    widget_labels = {_norm_provider(w["provider"]): w.get("label") or w["provider"] for w in widgets}

    every = _all_rows(backup)
    nova = nova_rows(backup, every)
    by_id = {r["_id"]: r for r in nova}
    nova_titles: dict[str, str] = {}  # package -> the app's name in Nova (drawer included), for widgets
    for r in every:
        p = _target_pkg(r) if r["itemType"] == ITEM_APP else None
        if p and (r.get("title") or "").strip():
            nova_titles.setdefault(p, r["title"].strip())

    def conv_rows(path):
        con = sqlite3.connect(path)
        con.row_factory = sqlite3.Row
        try:
            return {r["_id"]: dict(r) for r in con.execute("select * from favorites order by _id")}
        finally:
            con.close()
    kept = _db_file(converted, conv_rows)

    def label(r: dict) -> str:
        t = r["itemType"]
        if t in (ITEM_WIDGET, ITEM_CUSTOM_WIDGET):
            prov = r.get("appWidgetProvider") or ""
            if prov and _norm_provider(prov) in widget_labels:
                return widget_labels[_norm_provider(prov)]
            app_name = nova_titles.get(prov.split("/", 1)[0]) if prov else None
            return f"{app_name} widget" if app_name else (prov.rsplit(".", 1)[-1] if prov else "widget")
        title = (r.get("title") or "").strip()
        pkg = _target_pkg(r)
        phone = app_labels.get(pkg) if t == ITEM_APP else None
        if phone and phone != pkg:  # over adb the phone's "label" is the package name
            return phone
        return title or pkg or f"item {r['_id']}"

    def where(r: dict) -> str:
        c = r["container"]
        if c == CONTAINER_DESKTOP:
            return f"screen {r['screen']}"
        if c == CONTAINER_HOTSEAT:
            return "dock"
        f = by_id.get(c)
        return f"folder {(f.get('title') or '').strip() or c}" if f else "a missing folder"

    items: dict[int, Item] = {}
    for r in nova:
        t = r["itemType"]
        it = Item(r["_id"], KIND.get(t, "item"), label(r), where(r), package=_target_pkg(r),
                  provider=r.get("appWidgetProvider") if t in (ITEM_WIDGET, ITEM_CUSTOM_WIDGET) else None)
        items[r["_id"]] = it
        if r["_id"] not in kept:
            it.status, it.reason = "dropped", (R_DOCK if r["container"] == CONTAINER_HOTSEAT else R_GRID)
            continue
        if r["container"] >= 0 and r["container"] not in by_id:
            it.status, it.reason = "dropped", R_NO_FOLDER
        elif it.package == NOVA_PKG:
            it.status, it.reason = "dropped", R_NOVA_ONLY
        elif t in (ITEM_WIDGET, ITEM_CUSTOM_WIDGET):
            if not it.provider or _norm_provider(it.provider) not in widget_labels:
                it.status, it.reason = "dropped", R_WIDGET_APP
        elif t in (ITEM_APP, ITEM_SHORTCUT, ITEM_DEEP_SHORTCUT):
            if it.package and it.package not in app_labels:
                it.status, it.reason = "dropped", R_NOT_INSTALLED
    # a folder whose apps all went goes too (Lawnchair would delete it empty)
    for fid, f in items.items():
        if f.kind == "folder" and f.status == "placed":
            kids = [i for i in items.values() if by_id[i.id]["container"] == fid]
            if not any(k.status == "placed" for k in kids):
                f.status, f.reason = "dropped", R_EMPTY_FOLDER
    _cascade(items, by_id)
    _collisions(items, kept, grid, glance)
    _cascade(items, by_id)
    # Lawnchair shows a folder that holds a single app as that app, in the folder's place
    for fid, f in items.items():
        if f.kind == "folder" and f.status == "placed":
            if sum(1 for i in items.values() if by_id[i.id]["container"] == fid and i.status == "placed") == 1:
                f.status = "unfolded"

    drop_ids = [i for i, it in items.items() if it.status == "dropped" and i in kept]

    def edit(path):
        con = sqlite3.connect(path)
        try:
            with con:
                if drop_ids:
                    con.execute(f"delete from favorites where _id in ({','.join('?' * len(drop_ids))})", drop_ids)
                # a widget row from the converter has no id; FLAG_ID_NOT_VALID makes the loader
                # allocate and bind one (as homalawn's own widget placement does)
                con.execute("update favorites set appWidgetId=-1, restored=? where itemType in (?,?)",
                            (RESTORE_ID_NOT_VALID, ITEM_WIDGET, ITEM_CUSTOM_WIDGET))
            con.execute("vacuum")
        finally:
            con.close()
        return Path(path).read_bytes()
    db = _db_file(converted, edit)
    return Plan(grid=grid, grid_from_backup=from_backup, glance_row=glance, subgrid=bool(meta.get("subgrid")),
                icon_pack=meta.get("icon_pack"), icon_pack_installed=meta.get("icon_pack_installed"),
                items=list(items.values()), db=db)


def _cascade(items: dict[int, Item], by_id: dict[int, dict]) -> None:
    """Children of a dropped folder go with it."""
    for it in items.values():
        c = by_id[it.id]["container"]
        if it.status == "placed" and c >= 0 and items.get(c) and items[c].status == "dropped":
            it.status, it.reason = "dropped", f"its folder was left out ({items[c].reason})"


def _collisions(items: dict[int, Item], kept: dict[int, dict], grid: tuple[int, int, int], glance: bool) -> None:
    """Mark what the loader would refuse for lack of room: two items on one cell or one dock
    place (the older Nova item keeps it, as Lawnchair loads rows in id order), or the search
    row of screen 0 while At a Glance is on."""
    cols, rows, hot = grid
    taken: dict[tuple, int] = {}
    if glance:
        for x in range(cols):
            taken[("d", 0, x, 0)] = -1
    for rid in sorted(kept):
        it = items.get(rid)
        if it is None or it.status != "placed":
            continue
        r = kept[rid]
        if r["container"] == CONTAINER_HOTSEAT:
            pos = r["screen"] or 0
            cells = [("h", pos)]
            if pos < 0 or pos >= hot:
                it.status, it.reason = "dropped", R_DOCK
                continue
        elif r["container"] == CONTAINER_DESKTOP:
            x, y, w, h = r["cellX"] or 0, r["cellY"] or 0, r["spanX"] or 1, r["spanY"] or 1
            if x < 0 or y < 0 or (r["screen"] or 0) < 0 or x + w > cols or y + h > rows:
                it.status, it.reason = "dropped", R_GRID
                continue
            cells = [("d", r["screen"] or 0, cx, cy) for cx in range(x, x + w) for cy in range(y, y + h)]
        else:
            continue
        other = next((taken[c] for c in cells if c in taken), None)
        if other is not None:
            it.status = "dropped"
            it.reason = ("the search row of the first screen" if other == -1
                         else f"its place is taken by {items[other].label}")
            continue
        for c in cells:
            taken[c] = rid


# -- talking to the phone -------------------------------------------------------------------------
def convert(launcher: Launcher, backup: bytes, glance_row: bool | None, pin: bool) -> tuple[dict, bytes]:
    """`backup` is the slimmed one (slim_backup)."""
    params: dict = {"pin": pin}
    if glance_row is not None:
        params["glance_row"] = glance_row
    return launcher.transport.nova_convert(backup, params)


def _pref_writes(launcher: Launcher, cur: dict, plan: Plan, glance_row: bool | None) -> list[tuple]:
    t = launcher.transport
    want: list[tuple[str, str]] = []
    c, r, h = Launcher._grid3_from(cur)
    if plan.grid_from_backup:
        for key, now, new in ((PREF_COLUMNS, c, plan.grid[0]), (PREF_ROWS, r, plan.grid[1]),
                              (PREF_HOTSEAT, h, plan.grid[2])):
            if now != new:
                want.append((key, str(new)))
    if glance_row is not None:
        now = cur.get("datastore", {}).get(PREF_SMARTSPACE, {}).get("value")
        if now is None or bool(now) != glance_row:
            want.append((PREF_SMARTSPACE, "true" if glance_row else "false"))
    if plan.icon_pack and plan.icon_pack_installed:
        now = cur.get("shared", {}).get(PREF_ICON_PACK, {}).get("value")
        if now != plan.icon_pack:
            want.append((PREF_ICON_PACK, plan.icon_pack))
    return [prefs_mod.resolve(t, k, v, cur=cur) for k, v in want]


def check_grid(cur: dict, grid: tuple[int, int, int]) -> None:
    limit = 20 if cur.get("shared", {}).get("pref_workspace_increase_max_grid_size", {}).get("value") else 10
    for n, v in (("columns", grid[0]), ("rows", grid[1])):
        if not 3 <= v <= limit:
            raise NovaError(f"the backup needs {v} {n}; Homa Lawn allows 3 to {limit}"
                            + (" (try --no-glance-row: one row fewer)" if n == "rows" else ""))
    if not 3 <= grid[2] <= 10:  # Lawnchair's dock settings slider (DockPreferences.kt)
        raise NovaError(f"the backup's dock has {grid[2]} places; Homa Lawn allows 3 to 10")


def run(launcher: Launcher, backup: bytes, glance_row: bool | None = None, dry_run: bool = False,
        notify=lambda m: None) -> tuple[Plan, str | None, list[str]]:
    """`backup` is the slimmed backup (slim_backup). Convert, account and check first: a refusal
    there changes nothing. A dry run stops there. Otherwise snapshot, deep shortcuts, grid
    settings, rows, then wait for widgets to settle; a failure from the snapshot on puts the
    snapshot back. Returns (plan, snapshot name or None, notes)."""
    from .apps import list_apps
    from .widgets import list_providers
    la = launcher
    la.wait_loaded()
    cur = prefs_mod.current(la.transport)
    meta, converted = convert(la, backup, glance_row, pin=False)
    plan = build_plan(backup, meta, converted, list_apps(la.transport), list_providers(la.transport), la._grid3())
    check_grid(cur, plan.grid)
    writes = _pref_writes(la, cur, plan, glance_row)  # validated before anything changes
    if dry_run:
        return plan, None, []
    # everything the import may change: settings (grid, At a Glance, icon pack) and layout
    snap = la._snapshot(SNAPSHOT_PREFIX, cur, with_db=True)
    try:
        if meta.get("deep_shortcuts"):
            convert(la, backup, glance_row, pin=True)  # pinned as the restore does, now that it goes ahead
        if writes:
            c, r, h = plan.grid if plan.grid_from_backup else la._grid3()
            la._send_prefs(writes, expect_db=Launcher._db_for(c, r, h))
        notes = la._write_db(plan.db, rollback=None)
    except BaseException:
        try:
            notify("The import failed; putting the previous home screen back.")
            la.restore(snap, force=True, no_backup=True)
            snapshots.delete(snap)
        except BaseException as e2:  # noqa: BLE001
            notify(f"Putting it back failed too ({e2}); recover with `homalawn snapshot restore {snap}`.")
        raise
    # the loader's removals are in the summary, item by item
    notes = [n for n in notes if not n.startswith("Homa Lawn removed ")]
    _settle(la, plan)
    return plan, snap, notes


def _settle(la: Launcher, plan: Plan, wait_s: float = WIDGET_WAIT_S) -> None:
    """After the write: what the loader removed after all (rows keep their ids through the
    import), and each widget's state once none is "loading" any more (or after `wait_s`)."""
    def q(path):
        con = sqlite3.connect(path)
        try:
            return {r[0]: (r[1], r[2]) for r in con.execute("select _id, appWidgetId, restored from favorites")}
        finally:
            con.close()
    deadline = time.monotonic() + wait_s
    while True:
        try:
            have = la._with_local_db(la._export()[1], q)
        except Exception:  # noqa: BLE001 - a read that raced a reload: try again
            have = None
        if have is not None:
            for i in plan.placed:
                if i.id not in have:
                    i.status, i.reason, i.state = "dropped", R_LOADER, None
                elif i.kind == "widget":
                    i.state = widget_state(*have[i.id])
            if all(i.state != "loading" for i in plan.placed if i.kind == "widget"):
                return
        if time.monotonic() > deadline:
            for i in plan.placed:
                if i.kind == "widget" and i.state is None:
                    i.state = "loading"
            return
        time.sleep(2)


# -- the summary ----------------------------------------------------------------------------------
def summary(plan: Plan, dry_run: bool, snap: str | None = None) -> list[str]:
    cols, rows, hot = plan.grid
    placed, dropped = plan.placed, plan.dropped
    verb = "would be placed" if dry_run else "placed"
    out = [f"Grid: {cols} columns x {rows} rows, dock {hot}"
           + (" (an extra top row for At a Glance)" if plan.glance_row and plan.grid_from_backup else "")
           + ("" if plan.grid_from_backup else " (the backup does not say its grid; the phone's is kept)")]
    if plan.subgrid:
        out.append("The backup uses Nova's subgrid; items in half cells go to the nearest whole cell.")
    if plan.icon_pack:
        out.append(f"Icon pack {plan.icon_pack}: " + ("set too." if plan.icon_pack_installed
                                                     else "not installed on the phone, left as it is."))
    kinds = {}
    for i in placed:
        kinds[i.kind] = kinds.get(i.kind, 0) + 1
    detail = ", ".join(f"{n} {k}{'s' if n != 1 else ''}" for k, n in sorted(kinds.items()))
    out.append(f"{len(placed)} items {verb}" + (f" ({detail})" if detail else "") + f"; {len(dropped)} left out.")
    if dropped:
        by_reason: dict[str, list[Item]] = {}
        for i in dropped:
            by_reason.setdefault(i.reason or "?", []).append(i)
        for reason, its in sorted(by_reason.items(), key=lambda kv: -len(kv[1])):
            counts: dict[str, int] = {}
            for i in its:
                counts[_name(i)] = counts.get(_name(i), 0) + 1
            names = ", ".join(n + (f" x{c}" if c > 1 else "") for n, c in sorted(counts.items(), key=lambda kv: kv[0].lower()))
            out.append(f"  {reason} ({len(its)}): {names}")
    if plan.unfolded:
        out.append(f"{len(plan.unfolded)} folder{'s hold' if len(plan.unfolded) != 1 else ' holds'} just one installed "
                   "app; Homa Lawn shows that app in the folder's place: " + ", ".join(i.label for i in plan.unfolded))
    widgets = [i for i in placed if i.kind == "widget"]
    if widgets and not dry_run:
        ready = [i for i in widgets if i.state == "ready"]
        if ready:
            out.append(f"{len(ready)} widget{'s are' if len(ready) != 1 else ' is'} ready: "
                       + ", ".join(_name(i) for i in ready))
        perm = [i for i in widgets if i.state == "needs permission"]
        setup = [i for i in widgets if i.state == "needs setup"]
        loading = [i for i in widgets if i.state == "loading"]
        if perm:
            out.append(f"{len(perm)} widget{'s wait' if len(perm) != 1 else ' waits'} for your tap on the phone: "
                       "tap it, then Create on Android's dialog: " + ", ".join(_name(i) for i in perm))
        if setup:
            out.append(f"{len(setup)} widget{'s need' if len(setup) != 1 else ' needs'} its setup screen: "
                       + ", ".join(_name(i) for i in setup))
        if loading:
            out.append(f"{len(loading)} widget{'s are' if len(loading) != 1 else ' is'} still loading: "
                       + ", ".join(_name(i) for i in loading))
    elif widgets:
        out.append(f"{len(widgets)} widget{'s' if len(widgets) != 1 else ''} would be added; any that Android "
                   "asks about wait for your tap on the phone (Create).")
    if snap:
        out.append(f"Previous home screen saved as {snap}; `homalawn undo` puts it back.")
    return out


def _name(i: Item) -> str:
    return f"{i.label} ({i.where})" if i.where.startswith("folder") else i.label


def as_data(plan: Plan, snap: str | None, dry_run: bool) -> dict:
    return {
        "dry_run": dry_run,
        "grid": {"cols": plan.grid[0], "rows": plan.grid[1], "dock": plan.grid[2]},
        "glance_row": plan.glance_row,
        "placed": len(plan.placed),
        "dropped": [{"label": i.label, "kind": i.kind, "where": i.where, "reason": i.reason} for i in plan.dropped],
        "unfolded": [i.label for i in plan.unfolded],
        "widgets": [{"label": i.label, "where": i.where, "state": i.state} for i in plan.placed if i.kind == "widget"],
        "snapshot": snap,
    }
