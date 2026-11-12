import copy as _copy
import shutil
import sqlite3

from homalawn.layout import (CONTAINER_DESKTOP, CONTAINER_HOTSEAT, read_layout,
                             validate, write_layout)

FIX = "tests/fixtures/launcher-populated.db"
GRID = (4, 5)  # launcher_5_4_4.db -> rows=5, cols=4 (cols, rows)


def copy(tmp_path):
    p = tmp_path / "l.db"
    shutil.copy(FIX, p)
    return str(p)


def strip_ids(L):
    L = _copy.deepcopy(L)

    def walk(items):
        for i in items:
            i.pop("id", None)
            walk(i.get("items", []))
    walk(L["hotseat"])
    for s in L["screens"]:
        walk(s["items"])
    return L


def all_items(L):
    out = list(L["hotseat"])
    for s in L["screens"]:
        out += s["items"]
    return out


def test_constants():
    assert (CONTAINER_DESKTOP, CONTAINER_HOTSEAT) == (-100, -101)


def test_read_has_hotseat_and_screen0(tmp_path):
    L = read_layout(copy(tmp_path), GRID)
    assert L["grid"] == {"cols": 4, "rows": 5}
    assert L["hotseat"] and L["screens"][0]["index"] == 0
    assert all({"id", "type", "x"} <= set(i) for i in L["hotseat"])
    assert [i["x"] for i in L["hotseat"]] == [0, 1, 2, 3]


def test_read_folder_and_widget(tmp_path):
    L = read_layout(copy(tmp_path), GRID)
    items = all_items(L)
    folder = next(i for i in items if i["type"] == "folder")
    assert len(folder["items"]) == 7
    assert all(c["type"] == "app" and c["package"] for c in folder["items"])
    w = next(i for i in items if i["type"] == "widget")
    assert (w["w"], w["h"], w["appWidgetId"]) == (2, 2, 25)
    assert w["provider"].startswith("com.android.chrome/")


def test_activity_url_escapes_roundtrip(tmp_path):
    db = copy(tmp_path)
    L = read_layout(db, GRID)
    yt = next(c for i in all_items(L) if i["type"] == "folder" for c in i["items"]
              if c["package"] == "com.google.android.youtube")
    assert yt["activity"] == ".app.honeycomb.Shell$HomeActivity"
    write_layout(db, L)
    n = sqlite3.connect(db).execute(
        "select count(*) from favorites where intent like '%Shell%24HomeActivity%'").fetchone()[0]
    assert n == 1


def test_roundtrip_is_identity(tmp_path):
    db = copy(tmp_path)
    before = read_layout(db, GRID)
    write_layout(db, before)
    assert strip_ids(read_layout(db, GRID)) == strip_ids(before)
    assert sqlite3.connect(db).execute("select count(*) from favorites").fetchone()[0] == 17


def test_move_app(tmp_path):
    db = copy(tmp_path)
    L = read_layout(db, GRID)
    app = next(i for i in L["hotseat"] if i["type"] == "app")
    L["hotseat"].remove(app)
    app.update(x=1, y=3)
    L["screens"][0]["items"].append(app)
    write_layout(db, L)
    row = sqlite3.connect(db).execute(
        "select container,cellX,cellY from favorites where intent like ?",
        (f"%{app['package']}/%",)).fetchone()
    assert row == (-100, 1, 3)


def test_new_app_and_widget_rows(tmp_path):
    db = copy(tmp_path)
    L = read_layout(db, GRID)
    L["screens"][0]["items"].append(
        {"type": "app", "package": "com.x", "activity": "com.x.Main", "x": 2, "y": 3})
    L["screens"][0]["items"].append(
        {"type": "widget", "provider": "com.y/com.y.W", "x": 0, "y": 3, "w": 2, "h": 1})
    write_layout(db, L)
    con = sqlite3.connect(db)
    intent, = con.execute("select intent from favorites where intent like '%com.x%'").fetchone()
    assert intent == ("#Intent;action=android.intent.action.MAIN;category=android.intent.category.LAUNCHER;"
                      "launchFlags=0x10200000;component=com.x/com.x.Main;end")
    row = con.execute("select itemType,appWidgetId,restored,spanX,spanY from favorites "
                      "where appWidgetProvider='com.y/com.y.W'").fetchone()
    assert row == (4, -1, 1, 2, 1)  # launcher allocates+binds the id on load


def test_raw_rows_preserved(tmp_path):
    db = copy(tmp_path)
    con = sqlite3.connect(db)
    con.execute("insert into favorites(_id,itemType,container,screen,cellX,cellY,spanX,spanY,title,intent) "
                "values(99,9,-100,1,2,2,1,1,'odd','keep-me')")
    con.commit(); con.close()
    L = read_layout(db, GRID)
    raw = next(i for i in all_items(L) if i["type"] == "raw")
    assert raw["row"]["_id"] == 99
    write_layout(db, L)
    r = sqlite3.connect(db).execute("select itemType,intent,screen,cellX,cellY from favorites where _id=99").fetchone()
    assert r == (9, "keep-me", 1, 2, 2)


def test_write_is_atomic_on_failure(tmp_path):
    db = copy(tmp_path)
    L = read_layout(db, GRID)
    L["screens"][0]["items"].append({"type": "bogus"})
    try:
        write_layout(db, L)
    except Exception:
        pass
    assert sqlite3.connect(db).execute("select count(*) from favorites").fetchone()[0] == 17


def test_validate_clean_fixture(tmp_path):
    assert validate(read_layout(copy(tmp_path), GRID), None) == []


def test_validate_overlap_and_bounds():
    L = {"grid": {"cols": 5, "rows": 5}, "hotseat": [], "screens": [{"index": 1, "items": [
        {"type": "widget", "provider": "a/b", "x": 0, "y": 0, "w": 4, "h": 2},
        {"type": "app", "package": "p", "activity": "p.A", "x": 3, "y": 1},
        {"type": "app", "package": "p", "activity": "p.B", "x": 5, "y": 0}]}]}
    errs = validate(L, None)
    assert any("overlap" in e for e in errs) and any("outside" in e for e in errs)


def test_validate_hotseat_and_unknown_component():
    L = {"grid": {"cols": 4, "rows": 5},
         "hotseat": [{"type": "app", "package": "p", "activity": "p.A", "x": 4}],
         "screens": [{"index": 0, "items": [
             {"type": "app", "package": "q", "activity": ".B", "x": 0, "y": 1}]}]}
    errs = validate(L, {"q/q.B"})
    assert any("hotseat" in e for e in errs)
    assert any("unknown" in e and "p/p.A" in e for e in errs)
    assert not any("q/" in e for e in errs)  # relative activity is resolved


def test_roundtrip_preserves_every_column(tmp_path):
    db = copy(tmp_path)
    con = sqlite3.connect(db)
    con.execute("update favorites set profileId=10, restored=2, options=7, appWidgetSource=3 where _id=14")
    con.execute("update favorites set restored=8 where _id=15")
    con.commit()
    assert con.execute("select length(icon) from favorites where _id=4").fetchone()[0] > 1000
    cols = [r[1] for r in con.execute("pragma table_info(favorites)") if r[1] != "modified"]
    q = f"select {','.join(cols)} from favorites order by _id"
    before = con.execute(q).fetchall()
    con.close()
    write_layout(db, read_layout(db, GRID))
    after = sqlite3.connect(db).execute(q).fetchall()
    assert after == before


def test_moved_item_keeps_unmodelled_columns(tmp_path):
    db = copy(tmp_path)
    con = sqlite3.connect(db)
    con.execute("update favorites set profileId=10 where _id=1")
    con.commit(); con.close()
    L = read_layout(db, GRID)
    L["hotseat"][0]["x"] = 3
    L["hotseat"][3]["x"] = 0
    write_layout(db, L)
    r = sqlite3.connect(db).execute("select profileId,cellX,length(icon) from favorites where _id=1").fetchone()
    assert r[0] == 10 and r[1] == 3


def _grid_layout(screen_items, index=1):
    return {"grid": {"cols": 4, "rows": 5}, "hotseat": [], "screens": [{"index": index, "items": screen_items}]}


def test_validate_raw_items_use_their_own_cells():
    raw = lambda i, x, y, w, h: {"type": "raw", "id": i, "row": {
        "_id": i, "cellX": x, "cellY": y, "spanX": w, "spanY": h, "container": -100, "screen": 1}}
    app = {"type": "app", "package": "p", "activity": "p.A", "x": 0, "y": 0}
    # raw far away from the app: no false overlap at (0,0)
    assert validate(_grid_layout([raw(90, 2, 3, 1, 1), app]), None) == []
    # raw really covering the app's cell: overlap
    errs = validate(_grid_layout([raw(91, 0, 0, 2, 2), app]), None)
    assert any("overlap" in e for e in errs)


def test_validate_reserves_first_screen_top_row():
    app = lambda x, y: {"type": "app", "package": "p", "activity": "p.A", "x": x, "y": y}
    errs = validate(_grid_layout([app(2, 0)], index=0), None)
    assert any("reserved" in e for e in errs)
    assert validate(_grid_layout([app(2, 1)], index=0), None) == []
    assert validate(_grid_layout([app(2, 0)], index=1), None) == []


def test_validate_top_row_free_when_smartspace_off():
    app = {"type": "app", "package": "p", "activity": "p.A", "x": 2, "y": 0}
    assert validate(_grid_layout([app], index=0), None, top_row_reserved=False) == []


def test_validate_duplicate_dock_x_and_ids():
    lay = {"grid": {"cols": 4, "rows": 5}, "screens": [{"index": 1, "items": [
        {"id": 3, "type": "app", "package": "p", "activity": "p.A", "x": 0, "y": 0}]}],
        "hotseat": [{"id": 3, "type": "app", "package": "p", "activity": "p.B", "x": 1},
                    {"id": 4, "type": "app", "package": "p", "activity": "p.C", "x": 1}]}
    errs = validate(lay, None)
    assert any("id 3" in e for e in errs) and any("two items at x=1" in e for e in errs)


def test_validate_known_components_only_for_new_or_changed():
    lay = {"grid": {"cols": 4, "rows": 5}, "hotseat": [], "screens": [{"index": 1, "items": [
        {"id": 1, "type": "app", "package": "p", "activity": "p.Old", "x": 0, "y": 0},
        {"id": 2, "type": "app", "package": "p", "activity": "p.Changed", "x": 1, "y": 0},
        {"type": "app", "package": "p", "activity": "p.New", "x": 2, "y": 0}]}]}
    existing = {1: "p/p.Old", 2: "p/p.Before"}
    errs = validate(lay, set(), existing)
    assert len(errs) == 2 and not any("p.Old" in e for e in errs)


def test_widgets_carry_their_state(tmp_path):
    p = copy(tmp_path)
    w = next(i for i in all_items(read_layout(p, GRID)) if i["type"] == "widget")
    assert w["state"] == "ready"
    con = sqlite3.connect(p)
    con.execute("update favorites set restored=4 where itemType=4")
    con.commit()
    con.close()
    w = next(i for i in all_items(read_layout(p, GRID)) if i["type"] == "widget")
    assert w["state"] == "needs setup"


def test_layout_table_names_the_state_of_widgets_that_are_not_ready():
    from homalawn.ops import _table
    lay = {"grid": {"cols": 4, "rows": 5}, "hotseat": [], "screens": [{"index": 0, "items": [
        {"type": "widget", "provider": "a/.R", "w": 2, "h": 1, "x": 0, "y": 1, "state": "ready"},
        {"type": "widget", "provider": "a/.S", "w": 2, "h": 1, "x": 2, "y": 1, "state": "needs setup"}]}]}
    assert _table(lay)[1:] == ["     0   1   0  widget a/.R 2x1", "     0   1   2  widget a/.S 2x1 [needs setup]"]


# -- pages in the phone's order (patch 0028: page reorder moves pages, not items) --------------

from homalawn.layout import parse_screen_order, visible_screen_ids, with_screen_ids  # noqa: E402


def test_saved_order_parses_like_the_launcher():
    assert parse_screen_order("1, 0, 2, 3") == [1, 0, 2, 3]
    assert parse_screen_order("2, ,x,0,2,-201") == [2, 0]
    assert parse_screen_order(None) == parse_screen_order("") == []


def test_visible_pages_follow_the_saved_order_then_the_rest_by_id():
    assert visible_screen_ids({0, 1, 2}, "", True) == [0, 1, 2]
    assert visible_screen_ids({0, 1, 2, 5}, "2, 0, 1", True) == [2, 0, 1, 5]
    # a page in the saved order with nothing on it is still a page on the phone
    assert visible_screen_ids({0, 2}, "1, 0, 2", True) == [1, 0, 2]
    # screen 0 exists while the search/date bar is on, and is put first when the order lacks it
    assert visible_screen_ids({1}, "", True) == [0, 1]
    assert visible_screen_ids({1}, "", False) == [0, 1]
    assert visible_screen_ids(set(), "", False) == [0]


def test_read_puts_pages_in_the_phone_order_with_their_screen_ids(tmp_path):
    db = copy(tmp_path)
    ids = sorted({r[0] for r in sqlite3.connect(db).execute("select screen from favorites where container=-100")})
    plain = read_layout(db, GRID)
    order = list(reversed(ids))
    L = read_layout(db, GRID, ", ".join(map(str, order)), True)
    assert [s["index"] for s in L["screens"]] == list(range(len(ids)))
    assert [s["id"] for s in L["screens"]] == order
    by_id = {s["id"]: s["items"] for s in plain["screens"]}
    assert all(s["items"] == by_id[s["id"]] for s in L["screens"])


def test_writing_by_position_lands_on_that_pages_screen_id(tmp_path):
    db = copy(tmp_path)
    ids = sorted({r[0] for r in sqlite3.connect(db).execute("select screen from favorites where container=-100")})
    saved = ", ".join(map(str, reversed(ids)))
    L = read_layout(db, GRID, saved, True)
    visible = [s["id"] for s in L["screens"]]
    # a hand-made layout addresses pages by position only; a page past the last is a new one
    bare = {"grid": L["grid"], "hotseat": L["hotseat"],
            "screens": [{"index": s["index"], "items": s["items"]} for s in L["screens"]]
                       + [{"index": len(visible), "items": [{"type": "app", "package": "com.new",
                                                            "activity": "com.new.Main", "x": 0, "y": 1}]}]}
    resolved = with_screen_ids(bare, visible)
    assert [s["id"] for s in resolved["screens"]] == visible + [max(visible) + 1]
    write_layout(db, resolved)
    again = read_layout(db, GRID, saved, True)
    assert [s["id"] for s in again["screens"]] == visible + [max(visible) + 1]
    assert strip_ids(again)["screens"][:len(visible)] == strip_ids(L)["screens"]
    assert again["screens"][-1]["items"][0]["package"] == "com.new"


def test_validate_reserves_the_top_row_of_screen_id_0_wherever_it_is():
    app = {"type": "app", "package": "a", "activity": "a.M", "x": 0, "y": 0}
    moved = {"grid": {"cols": 4, "rows": 5}, "hotseat": [],
             "screens": [{"index": 0, "id": 1, "items": [app]}, {"index": 1, "id": 0, "items": [dict(app)]}]}
    errs = validate(moved, None)
    assert errs == ["screen 1: a/a.M at (0,0) is in the reserved search row"]


def test_validate_refuses_two_screens_on_one_page():
    two = {"grid": {"cols": 4, "rows": 5}, "hotseat": [],
           "screens": [{"index": 0, "id": 2, "items": []}, {"index": 1, "id": 2, "items": []}]}
    assert validate(two, None) == ["screens 0 and 1 are the same page (screen id 2)"]


def test_count_changes_counts_moves_additions_removals_and_folder_renames():
    from homalawn.layout import count_changes
    old = {"hotseat": [{"id": 1, "type": "app", "x": 0}],
           "screens": [{"index": 0, "id": 0, "items": [
               {"id": 2, "type": "app", "x": 0, "y": 1},
               {"id": 3, "type": "folder", "title": "Work", "x": 1, "y": 1, "items": [{"id": 4, "type": "app", "x": 0, "y": 0}]},
               {"id": 5, "type": "widget", "x": 0, "y": 2, "w": 2, "h": 1}]}]}
    assert count_changes(old, old) == 0
    new = {"hotseat": [{"id": 1, "type": "app", "x": 0}],
           "screens": [{"index": 0, "id": 0, "items": [
               {"id": 2, "type": "app", "x": 3, "y": 1},                                     # moved
               {"id": 3, "type": "folder", "title": "Jobs", "x": 1, "y": 1, "items": []},    # renamed; 4 removed
               {"id": 5, "type": "widget", "x": 0, "y": 2, "w": 2, "h": 2},                  # resized
               {"type": "app", "package": "p", "x": 3, "y": 3}]}]}                           # added
    assert count_changes(old, new) == 5
