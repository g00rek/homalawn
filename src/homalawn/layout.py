"""Launcher layout model: Lawnchair `favorites` table <-> JSON, plus validation."""
from __future__ import annotations

import base64
import re
import sqlite3
import time
from urllib.parse import quote, unquote
from typing import Any

CONTAINER_DESKTOP = -100
CONTAINER_HOTSEAT = -101

ITEM_APP, ITEM_SHORTCUT, ITEM_FOLDER, ITEM_WIDGET, ITEM_DEEP_SHORTCUT = 0, 1, 2, 4, 6

# Widget restore flags (LauncherAppWidgetInfo.FLAG_*); widgets.py documents what each end state means.
RESTORE_ID_NOT_VALID = 1   # the launcher allocates and binds a fresh appWidgetId on load
RESTORE_UI_NOT_READY = 4   # bound, the provider's setup screen has not been finished
RESTORE_ID_ALLOCATED = 16  # id allocated, Android has not allowed Lawnchair to bind it


def widget_state(app_widget_id: int | None, restored: int | None) -> str:
    """What a widget row is waiting for: "ready", "needs setup" (its setup screen), "needs
    permission" (Allow for Lawnchair to create widgets) or "loading" (the launcher has not
    loaded the row yet)."""
    flags = restored or 0
    if flags & RESTORE_ID_ALLOCATED:
        return "needs permission"
    if flags & RESTORE_UI_NOT_READY and not flags & RESTORE_ID_NOT_VALID:
        return "needs setup"
    if flags == 0 and (app_widget_id or 0) > 0:
        return "ready"
    return "loading"

_COLUMNS = ["_id", "title", "intent", "container", "screen", "cellX", "cellY", "spanX", "spanY",
            "itemType", "appWidgetId", "icon", "appWidgetProvider", "modified", "restored",
            "profileId", "rank", "options", "appWidgetSource"]

_APP_INTENT = ("#Intent;action=android.intent.action.MAIN;category=android.intent.category.LAUNCHER;"
               "launchFlags=0x10200000;component={pkg}/{act};end")


def _connect(db_path: str) -> sqlite3.Connection:
    con = sqlite3.connect(db_path)
    con.row_factory = sqlite3.Row
    return con


def _parse_intent(intent: str | None) -> tuple[str | None, str | None]:
    if not intent:
        return None, None
    m = re.search(r";component=([^;]+)", intent)
    if m and "/" in m.group(1):
        pkg, act = m.group(1).split("/", 1)
        return unquote(pkg), unquote(act)
    m = re.search(r";package=([^;]+)", intent)
    return (unquote(m.group(1)) if m else None), None


def _jsonable_row(row: sqlite3.Row) -> dict[str, Any]:
    d = dict(row)
    if isinstance(d.get("icon"), bytes):
        d["icon"] = {"$b64": base64.b64encode(d["icon"]).decode()}
    return d


def _row_from_json(d: dict[str, Any]) -> dict[str, Any]:
    d = dict(d)
    if isinstance(d.get("icon"), dict):
        d["icon"] = base64.b64decode(d["icon"]["$b64"])
    return d


def _item(row: sqlite3.Row, children: dict[int, list[sqlite3.Row]], hotseat: bool) -> dict[str, Any]:
    t = row["itemType"]
    if hotseat:
        pos = {"x": row["screen"] if row["screen"] is not None else (row["cellX"] or 0)}
    else:
        pos = {"x": row["cellX"] or 0, "y": row["cellY"] or 0}
    base = {"id": row["_id"]}
    if t == ITEM_APP:
        pkg, act = _parse_intent(row["intent"])
        return {**base, "type": "app", "package": pkg, "activity": act, "title": row["title"], **pos}
    if t in (ITEM_SHORTCUT, ITEM_DEEP_SHORTCUT):
        return {**base, "type": "shortcut", "itemType": t, "intent": row["intent"],
                "title": row["title"], **pos}
    if t == ITEM_FOLDER:
        kids = [_item(c, children, False) for c in children.get(row["_id"], [])]
        return {**base, "type": "folder", "title": row["title"], **pos, "items": kids}
    if t == ITEM_WIDGET:
        return {**base, "type": "widget", "provider": row["appWidgetProvider"],
                "appWidgetId": row["appWidgetId"], "w": row["spanX"], "h": row["spanY"], **pos,
                "state": widget_state(row["appWidgetId"], row["restored"])}
    return {**base, "type": "raw", "row": _jsonable_row(row)}


FIRST_SCREEN_ID = 0  # Workspace.FIRST_SCREEN_ID: the page that holds the search/date bar


def parse_screen_order(saved: str | None) -> list[int]:
    """The launcher's saved page order (`workspace_screen_order`, e.g. "1, 0, 2"): screen ids from
    the first page to the last, as Workspace.getPersistedWorkspaceScreenIds reads it."""
    out: list[int] = []
    for part in (saved or "").split(","):
        try:
            n = int(part.strip())
        except ValueError:
            continue
        if n >= 0 and n not in out:
            out.append(n)
    return out


def visible_screen_ids(item_screens, saved_order: str | None, smartspace: bool) -> list[int]:
    """The home screen's pages as the launcher shows them, first to last, as screen ids. The same
    sums as Lawnchair's loader: WorkspaceData.collectWorkspaceScreens (the screens that hold items,
    plus screen 0 while the search/date bar is on or nothing is placed), mergeWorkspaceScreens
    (the saved order first, every page in it kept even when empty, then the other screens by id)
    and ModelCallbacks.bindScreens (screen 0 put first when it is missing)."""
    ids = set(item_screens)
    if smartspace or not ids:
        ids.add(FIRST_SCREEN_ID)
    merged = parse_screen_order(saved_order)
    merged += sorted(i for i in ids if i not in merged)
    if FIRST_SCREEN_ID not in merged:
        merged.insert(0, FIRST_SCREEN_ID)
    return merged


def read_layout(db_path: str, grid: tuple[int, int], saved_order: str | None = "",
                smartspace: bool = True) -> dict:
    """grid is (cols, rows). `saved_order` and `smartspace`: the launcher's saved page order and
    whether its search/date bar is on (see visible_screen_ids). Every page the phone shows is a
    screen, empty ones too: `index` is its position (0 = the first page a person swipes from),
    `id` the screen id the database files its items under."""
    con = _connect(db_path)
    try:
        rows = con.execute("select * from favorites order by _id").fetchall()
    finally:
        con.close()
    children: dict[int, list[sqlite3.Row]] = {}
    for r in rows:
        if r["container"] is not None and r["container"] > 0:
            children.setdefault(r["container"], []).append(r)
    for kids in children.values():
        kids.sort(key=lambda r: (r["rank"], r["_id"]))
    hotseat_rows = sorted((r for r in rows if r["container"] == CONTAINER_HOTSEAT),
                          key=lambda r: (r["screen"] or 0, r["cellX"] or 0, r["_id"]))
    screens: dict[int, list[sqlite3.Row]] = {}
    for r in rows:
        if r["container"] == CONTAINER_DESKTOP:
            screens.setdefault(r["screen"] or 0, []).append(r)
    order = visible_screen_ids(screens, saved_order, smartspace)
    return {
        "grid": {"cols": grid[0], "rows": grid[1]},
        "screens": [{"index": pos, "id": sid,
                     "items": [_item(r, children, False)
                               for r in sorted(screens.get(sid, []),
                                               key=lambda r: (r["cellY"] or 0, r["cellX"] or 0, r["_id"]))]}
                    for pos, sid in enumerate(order)],
        "hotseat": [_item(r, children, True) for r in hotseat_rows],
    }


def with_screen_ids(layout: dict, visible: list[int]) -> dict:
    """A copy of `layout` whose every screen names the screen id it is written to. A screen that
    carries an `id` keeps it; one without (a new page, or a hand-made layout) is the page at its
    `index` among `visible` (the phone's pages, first to last, from read_layout), and past the
    last page a new screen id, larger than every id in use, so the launcher puts it after the
    others in the order of `index`."""
    out = dict(layout)
    screens = [dict(s) for s in layout.get("screens", [])]
    taken = set(visible) | {s["id"] for s in screens if s.get("id") is not None}
    fresh = max(taken, default=-1) + 1
    for s in sorted(screens, key=lambda s: s["index"]):
        if s.get("id") is not None:
            continue
        if 0 <= s["index"] < len(visible):
            s["id"] = visible[s["index"]]
        else:
            s["id"] = fresh
            fresh += 1
    out["screens"] = screens
    return out


def _places(layout: dict) -> tuple[dict, int]:
    """Every item (folder contents too) by its database id -> where it is and what it shows; and how
    many items have no id (new ones)."""
    out: dict = {}
    new = 0

    def walk(items, where):
        nonlocal new
        for it in items:
            sig = (where, it.get("x"), it.get("y"), it.get("w"), it.get("h"), it.get("type"),
                   it.get("title") if it.get("type") == "folder" else None)
            if it.get("id") is None:
                new += 1
            else:
                out[it["id"]] = sig
            if it.get("type") == "folder":
                walk(it.get("items", []), ("folder", it.get("id")))
    walk(layout.get("hotseat", []), ("hotseat",))
    for sc in layout.get("screens", []):
        walk(sc.get("items", []), ("screen", screen_id(sc)))
    return out, new


def count_changes(old: dict, new: dict) -> int:
    """How many items a new layout adds, removes, moves, resizes or renames (a folder) against the
    old one, items matched by their database id."""
    a, _ = _places(old)
    b, added = _places(new)
    return added + sum(1 for i in a if i not in b) + sum(1 for i, sig in b.items() if i not in a or a[i] != sig)


def screen_id(screen: dict) -> int:
    """The screen id a layout's screen is written to: its `id` (with_screen_ids, read_layout), or
    for a layout that names none its `index` (the database's own numbering)."""
    return screen["id"] if screen.get("id") is not None else screen["index"]


def _walk_ids(items: list[dict]):
    for i in items:
        if i.get("id") is not None:
            yield i["id"]
        if i["type"] == "raw" and i["row"].get("_id") is not None:
            yield i["row"]["_id"]
        yield from _walk_ids(i.get("items", []))


def write_layout(db_path: str, layout: dict) -> None:
    con = sqlite3.connect(db_path)
    try:
        with con:  # one transaction: commit on success, rollback on any exception
            _write(con, layout)
    finally:
        con.close()


def _write(con: sqlite3.Connection, layout: dict) -> None:
    now = int(time.time() * 1000)
    con.row_factory = sqlite3.Row
    prof = con.execute("select profileId from favorites where profileId is not null limit 1").fetchone()
    profile_id = prof[0] if prof else 0  # only for NEW items; existing rows keep their own

    old_rows = {r["_id"]: dict(r) for r in con.execute("select * from favorites")}
    folder_ids = [r[0] for r in con.execute(
        "select _id from favorites where itemType=? and container in (?,?)",
        (ITEM_FOLDER, CONTAINER_DESKTOP, CONTAINER_HOTSEAT))]
    marks = ",".join("?" * len(folder_ids))
    con.execute("delete from favorites where container in (?,?)", (CONTAINER_DESKTOP, CONTAINER_HOTSEAT))
    if folder_ids:
        con.execute(f"delete from favorites where container in ({marks})", folder_ids)

    all_top = list(layout.get("hotseat", [])) + [i for s in layout.get("screens", []) for i in s["items"]]
    used = {r[0] for r in con.execute("select _id from favorites")} | set(_walk_ids(all_top))
    next_id = [max(used, default=0) + 1]

    def new_id(item_id):
        if item_id is not None:
            return item_id
        n = next_id[0]
        next_id[0] += 1
        return n

    def insert(**kw):
        vals = {c: None for c in _COLUMNS}
        vals.update({"appWidgetId": -1, "modified": now, "restored": 0, "profileId": profile_id,
                     "rank": 0, "options": 0, "appWidgetSource": -1, "spanX": 1, "spanY": 1})
        raw = kw.pop("_raw", False)
        old = old_rows.get(kw.get("_id"))
        if old is not None and not raw:
            vals.update(old)  # existing item: keep every column the JSON does not model
            vals["modified"] = now
        vals.update(kw)
        con.execute(f"insert into favorites ({','.join(_COLUMNS)}) values ({','.join('?' * len(_COLUMNS))})",
                    [vals[c] for c in _COLUMNS])

    def put(item: dict, container: int, screen: int | None, rank: int = 0) -> None:
        t = item["type"]
        if t == "raw":
            insert(_raw=True, **_row_from_json(item["row"]))
            return
        iid = new_id(item.get("id"))
        x, y = item.get("x", 0), item.get("y", 0)
        if container == CONTAINER_HOTSEAT:
            pos = {"screen": x, "cellX": x, "cellY": 0}
        else:
            pos = {"screen": screen, "cellX": x, "cellY": y}
            old = old_rows.get(iid)
            if old is not None and old["container"] == container:
                # keep NULLs (folder children) when the normalised position is unchanged
                if (old["cellX"] or 0, old["cellY"] or 0, old["screen"] or 0) == (x, y, screen or 0):
                    pos = {"screen": old["screen"], "cellX": old["cellX"], "cellY": old["cellY"]}
        common = {"_id": iid, "container": container, "rank": rank, **pos}
        if "title" in item or iid not in old_rows:
            common["title"] = item.get("title")
        if t == "app":
            act = item.get("activity")
            intent = (_APP_INTENT.format(pkg=quote(item["package"], safe="./"), act=quote(act, safe="./")) if act
                      else f"#Intent;action=android.intent.action.MAIN;category=android.intent.category.LAUNCHER;"
                           f"launchFlags=0x10200000;package={quote(item['package'], safe='./')};end")
            old = old_rows.get(iid)
            if old is not None and old["itemType"] == ITEM_APP and _parse_intent(old["intent"]) == (
                    item["package"], act or None):
                intent = old["intent"]
            insert(**common, itemType=ITEM_APP, intent=intent)
        elif t == "shortcut":
            insert(**common, itemType=item.get("itemType", ITEM_SHORTCUT), intent=item["intent"])
        elif t == "folder":
            insert(**common, itemType=ITEM_FOLDER)
            for n, kid in enumerate(item.get("items", [])):
                kid = dict(kid)
                kid.setdefault("x", 0)
                kid.setdefault("y", 0)
                put(kid, iid, 0, n)
        elif t == "widget":
            wid = item.get("appWidgetId")
            fresh = wid is None or wid <= 0
            old = old_rows.get(iid)
            extra = {}
            if fresh:
                extra = {"restored": RESTORE_ID_NOT_VALID}
            elif old is None or old["appWidgetId"] != wid:
                extra = {"restored": 0}
            insert(**common, itemType=ITEM_WIDGET, appWidgetProvider=item["provider"],
                   appWidgetId=-1 if fresh else wid, spanX=item.get("w", 1), spanY=item.get("h", 1),
                   **extra)
        else:
            raise ValueError(f"unknown item type {t!r}")

    for item in layout.get("hotseat", []):
        put(item, CONTAINER_HOTSEAT, None)
    for s in layout.get("screens", []):
        for item in s["items"]:
            put(item, CONTAINER_DESKTOP, screen_id(s))


def _component(item: dict) -> str:
    pkg, act = item["package"], item.get("activity") or ""
    if act.startswith("."):
        act = pkg + act
    return f"{pkg}/{act}"


def components_by_id(layout: dict) -> dict[int, str]:
    """id -> component of every app already in a layout (folders included)."""
    out: dict[int, str] = {}

    def walk(items):
        for i in items:
            if i["type"] == "app" and i.get("id") is not None:
                out[i["id"]] = _component(i)
            walk(i.get("items", []) if i["type"] == "folder" else [])
    walk(layout.get("hotseat", []))
    for s in layout.get("screens", []):
        walk(s["items"])
    return out


def _problem(errs: list[dict], message: str, where: str, screen: int | None = None, item: dict | None = None,
             x: int | None = None, y: int | None = None) -> None:
    """One validation problem, located so a UI can show it next to the item."""
    iid = None
    if item is not None:
        iid = item["row"].get("_id") if item.get("type") == "raw" else item.get("id")
    errs.append({"message": message, "where": where, "screen": screen, "x": x, "y": y, "id": iid})


def _check_components(items: list[dict], known: set[str], where: str, errs: list[dict],
                      existing: dict[int, str], kind: str, screen: int | None, top: dict | None = None) -> None:
    for i in items:
        owner = top or i  # an app inside a folder is shown at its folder's cell
        if i["type"] == "app" and i.get("activity") and _component(i) not in known:
            if existing.get(i.get("id")) != _component(i):  # items already on the phone are not re-checked
                _problem(errs, f"{where}: unknown component {i['package']}/{i['activity']}", kind, screen, i,
                         owner.get("x"), owner.get("y"))
        if i["type"] == "folder":
            _check_components(i.get("items", []), known, f"{where} folder", errs, existing, kind, screen, owner)


def _all_ids(layout: dict):
    def walk(items):
        for i in items:
            own = {i.get("id"), i["row"].get("_id") if i["type"] == "raw" else None} - {None}
            yield from own
            yield from walk(i.get("items", []))
    yield from walk(list(layout.get("hotseat", [])))
    for s in layout.get("screens", []):
        yield from walk(s["items"])


def validate(layout: dict, known_components: set[str] | None,
             existing: dict[int, str] | None = None, top_row_reserved: bool = True) -> list[str]:
    """`existing` (id -> component, see components_by_id) exempts unchanged apps from the
    known-components check: one app missing from the phone's list must not block every edit.
    `top_row_reserved`: Lawnchair keeps row 0 of screen id 0 (the page with the search/date bar, wherever it is) for that bar only
    while its smartspace is on (FeatureFlags.topQsbOnFirstScreenEnabled)."""
    return [p["message"] for p in problems(layout, known_components, existing, top_row_reserved)]


def problems(layout: dict, known_components: set[str] | None,
             existing: dict[int, str] | None = None, top_row_reserved: bool = True,
             hotseat_count: int | None = None) -> list[dict]:
    """What `validate` finds, each as {message, where ("layout", "screen" or "hotseat"), screen,
    x, y, id}: the cell and item it is about, None where it has none. `hotseat_count`: the dock's
    width when it differs from the grid's columns (validate checks the dock against the columns)."""
    errs: list[dict] = []
    seen: set[int] = set()
    for iid in _all_ids(layout):
        if iid in seen:
            errs.append({"message": f"item id {iid} appears more than once", "where": "layout", "screen": None,
                         "x": None, "y": None, "id": iid})
        seen.add(iid)
    cols, rows = layout["grid"]["cols"], layout["grid"]["rows"]
    ids_seen: dict[int, int] = {}
    for s in layout.get("screens", []):
        other = ids_seen.setdefault(screen_id(s), s["index"])
        if other != s["index"]:
            errs.append({"message": f"screens {other} and {s['index']} are the same page (screen id {screen_id(s)})",
                         "where": "layout", "screen": s["index"], "x": None, "y": None, "id": None})
    for s in layout.get("screens", []):
        occupied: dict[tuple[int, int], int] = {}
        # LoaderCursor reserves row 0 of screen id 0 (wherever that page is) for the search bar
        if screen_id(s) == FIRST_SCREEN_ID and top_row_reserved:
            for cx in range(cols):
                occupied[(cx, 0)] = -1
        for n, i in enumerate(s["items"]):
            if i["type"] == "raw":
                r = i["row"]
                x, y, w, h = r.get("cellX") or 0, r.get("cellY") or 0, r.get("spanX") or 1, r.get("spanY") or 1
                name = f"raw item {r.get('_id')}"
            else:
                w, h = (i.get("w", 1), i.get("h", 1)) if i["type"] == "widget" else (1, 1)
                x, y = i.get("x", 0), i.get("y", 0)
                name = i.get("provider") or (_component(i) if i["type"] == "app" else i["type"])
            if x < 0 or y < 0 or x + w > cols or y + h > rows:
                _problem(errs, f"screen {s['index']}: {name} at ({x},{y}) {w}x{h} is outside the {cols}x{rows} grid",
                         "screen", s["index"], i, x, y)
                continue
            for cx in range(x, x + w):
                for cy in range(y, y + h):
                    other = occupied.setdefault((cx, cy), n)
                    if other == -1:
                        _problem(errs, f"screen {s['index']}: {name} at ({cx},{cy}) is in the reserved search row",
                                 "screen", s["index"], i, x, y)
                        break
                    if other != n:
                        _problem(errs, f"screen {s['index']}: {name} overlaps another item at ({cx},{cy})",
                                 "screen", s["index"], i, x, y)
                        break
                else:
                    continue
                break
    dock: set[int] = set()
    width = cols if hotseat_count is None else hotseat_count
    for i in layout.get("hotseat", []):
        x = i.get("x", 0)
        if not 0 <= x < width:
            _problem(errs, f"hotseat: x={x} is outside " + (f"0..{width - 1}" if hotseat_count is None
                                                             else f"the dock (0..{width - 1})"),
                     "hotseat", None, i, x, None)
        if x in dock:
            _problem(errs, f"hotseat: two items at x={x}", "hotseat", None, i, x, None)
        dock.add(x)
    if known_components is not None:
        ex = existing or {}
        _check_components(layout.get("hotseat", []), known_components, "hotseat", errs, ex, "hotseat", None)
        for s in layout.get("screens", []):
            _check_components(s["items"], known_components, f"screen {s['index']}", errs, ex, "screen", s["index"])
    return errs
