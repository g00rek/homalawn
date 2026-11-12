"""The Home screen page's draft rules (shell/HomaLawn/pages/HomeDraft.js), run under node, and the
layouts they build checked by the desktop's own layout.problems (what home.apply validates)."""
import json
import shutil
import subprocess
from pathlib import Path

import pytest

from homalawn import layout

JS = Path(__file__).parent.parent / "src" / "homalawn" / "shell" / "HomaLawn" / "pages" / "HomeDraft.js"
NODE = shutil.which("node")
pytestmark = pytest.mark.skipif(not NODE, reason="needs node")


def app(i, x, y=None, title=None, pkg=None):
    out = {"id": i, "type": "app", "package": pkg or f"com.example.a{i}", "activity": ".Main",
           "title": title or f"A{i}", "x": x}
    if y is not None:
        out["y"] = y
    return out


HOME = {
    "grid": [4, 5], "hotseat_count": 4, "top_row_reserved": True,
    "icon_scale": 1.0, "label_scale": 1.0, "label_gap_dp": 0.0, "show_labels": True,
    "screens": [
        {"index": 0, "id": 0, "items": [app(1, 0, 4), app(2, 1, 4),
                               {"id": 3, "type": "folder", "title": "Tools", "x": 2, "y": 4,
                                "items": [app(4, 0, 0), app(5, 1, 0)]},
                               {"id": 6, "type": "widget", "provider": "com.w/.Clock", "appWidgetId": 9,
                                "w": 2, "h": 2, "x": 0, "y": 1, "state": "ready"}]},
        {"index": 1, "id": 1, "items": [app(7, 3, 4)]},
    ],
    "hotseat": [app(10, 0), app(11, 1)],
    "icons": {},
}
CAMERA = {"component": "com.cam/com.cam.Main", "package": "com.cam", "activity": "com.cam.Main", "label": "Camera"}
WIDE = {"provider": "com.w/.Wide", "label": "Wide", "cells": [4, 1]}


def run(script: str):
    """Run `script` with D (HomeDraft.js), HOME and the list items in scope; it prints JSON."""
    prelude = (f"const vm=require('vm');const src=require('fs').readFileSync({json.dumps(str(JS))},'utf8')"
               ".replace(/^\\.pragma library/,'');const module={exports:{}};"
               "vm.runInNewContext(src,{module,JSON,Math,String,Number,isFinite});const D=module.exports;"
               f"const HOME={json.dumps(HOME)};const CAMERA={json.dumps(CAMERA)};const WIDE={json.dumps(WIDE)};"
               "const out=x=>console.log(JSON.stringify(x));")
    r = subprocess.run([NODE, "-e", prelude + script], capture_output=True, text=True, timeout=30)
    assert r.returncode == 0, r.stderr
    return json.loads(r.stdout)


def problems(apply):
    known = {f"com.example.a{i}/com.example.a{i}.Main" for i in range(20)} | {CAMERA["component"]}
    return layout.problems(apply["layout"], known, None, True, apply.get("hotseat_count", 4))


def test_unchanged_draft_counts_nothing_and_sends_the_same_layout():
    got = run("const d=D.fromHome(HOME);out({n:D.changes(d,D.fromHome(HOME)),a:D.toApply(d,D.fromHome(HOME))})")
    assert got["n"] == 0
    assert "grid" not in got["a"] and "look_prefs" not in got["a"] and "hotseat_count" not in got["a"]
    assert got["a"]["layout"]["screens"] == [{"index": s["index"], "id": s["id"], "items": s["items"]}
                                               for s in HOME["screens"]]
    assert problems(got["a"]) == []


def test_the_search_row_is_on_the_page_with_screen_id_0_wherever_it_is():
    # pages reordered on the phone: the date bar's page (screen id 0) is second, a new page has no id
    got = run("""
      const h=JSON.parse(JSON.stringify(HOME));
      h.screens=[{index:0,id:1,items:[]},{index:1,id:0,items:[]}];
      let d=D.fromHome(h);
      const added=D.addScreen(d);
      d=added.draft;
      out({first:D.verdict(d,{app:CAMERA},{screen:0,x:0,y:0}).ok,
           search:D.verdict(d,{app:CAMERA},{screen:1,x:0,y:0}).ok,
           fresh:D.verdict(d,{app:CAMERA},{screen:added.index,x:0,y:0}).ok,
           a:D.toApply(d,D.fromHome(h)).layout.screens})""")
    assert got["first"] and not got["search"] and got["fresh"]
    assert got["a"] == [{"index": 0, "id": 1, "items": []}, {"index": 1, "id": 0, "items": []},
                        {"index": 2, "items": []}]


def test_place_an_app_from_the_list_then_make_a_folder_then_add_to_it():
    got = run("""
      const s=D.fromHome(HOME); let d=s;
      let r=D.drop(d,{app:CAMERA},{screen:1,x:0,y:0}); d=r.draft;
      const placed=r.ok;
      r=D.drop(d,{item:'i7'},{screen:1,x:0,y:0}); d=r.draft;            // app onto app: a folder
      const folderOk=r.ok;
      r=D.drop(d,{item:'i10'},{screen:1,x:0,y:0}); d=r.draft;           // dock app onto the folder
      out({placed,folderOk,addOk:r.ok,n:D.changes(d,s),a:D.toApply(d,s)})""")
    assert got["placed"] and got["folderOk"] and got["addOk"]
    s1 = got["a"]["layout"]["screens"][1]["items"]
    assert len(s1) == 1
    f = s1[0]
    assert f["type"] == "folder" and f["title"] == "Folder" and (f["x"], f["y"]) == (0, 0) and "id" not in f
    assert [i.get("id") for i in f["items"]] == [None, 7, 10]
    assert f["items"][0]["package"] == "com.cam" and "id" not in f["items"][0]
    assert [i["id"] for i in got["a"]["layout"]["hotseat"]] == [11]
    assert got["n"] == 4      # camera added, folder added, 7 moved, 10 moved
    assert problems(got["a"]) == []


def test_collisions_refuse_with_the_cells_marked():
    got = run("""
      const d=D.fromHome(HOME);
      out({top:D.verdict(d,{app:CAMERA},{screen:0,x:3,y:0}),
           widgetOnApp:D.verdict(d,{item:'i6'},{screen:0,x:0,y:3}),
           out:D.verdict(d,{widget:WIDE},{screen:1,x:1,y:0}),
           folderIntoApp:D.verdict(d,{item:'i3'},{screen:0,x:0,y:4}),
           widgetDock:D.verdict(d,{widget:WIDE},{dock:3}),
           drop:D.drop(d,{item:'i6'},{screen:0,x:0,y:3}).ok})""")
    assert not got["top"]["ok"] and "search bar" in got["top"]["message"]
    assert not got["widgetOnApp"]["ok"] and any(not c["ok"] for c in got["widgetOnApp"]["cells"])
    assert not got["out"]["ok"] and got["out"]["message"] == "It doesn't fit there."
    assert not got["folderIntoApp"]["ok"] and not got["widgetDock"]["ok"]
    assert got["drop"] is False


def test_trash_a_folder_app_turns_a_two_app_folder_back_into_an_app():
    got = run("""
      const s=D.fromHome(HOME); let d=D.drop(s,{item:'i4'},{trash:true}).draft;
      out(D.toApply(d,s))""")
    items = got["layout"]["screens"][0]["items"]
    left = [i for i in items if i["id"] == 5]
    assert left and left[0]["type"] == "app" and (left[0]["x"], left[0]["y"]) == (2, 4)
    assert not any(i["type"] == "folder" for i in items)
    assert problems(got) == []


def test_thumbnail_moves_to_the_first_free_cell_of_that_screen():
    got = run("""
      const s=D.fromHome(HOME); const r=D.drop(s,{item:'i1'},{thumb:1});
      out(D.toApply(r.draft,s))""")
    moved = [i for i in got["layout"]["screens"][1]["items"] if i["id"] == 1][0]
    assert (moved["x"], moved["y"]) == (0, 0)


def test_smaller_grid_re_lays_items_and_a_wide_widget_refuses():
    got = run("""
      const s=D.fromHome(HOME);
      const g=D.setGrid(s,3,4);
      let w=D.drop(s,{widget:WIDE},{screen:1,x:0,y:0}).draft;
      out({g:g.ok,msg:g.message,a:D.toApply(g.draft,s),wide:D.setGrid(w,3,5)})""")
    assert got["g"] and "moved" in got["msg"]
    assert got["a"]["grid"] == [3, 4] and got["a"]["layout"]["grid"] == {"cols": 3, "rows": 4}
    assert problems(got["a"]) == []
    n = sum(len(s["items"]) for s in got["a"]["layout"]["screens"])
    assert n == 5
    assert not got["wide"]["ok"] and "4x1 widget" in got["wide"]["message"]


def test_narrower_dock_moves_what_does_not_fit():
    got = run("""
      const s=D.fromHome(HOME); let d=D.drop(s,{app:CAMERA},{dock:3}).draft;
      const r=D.setDock(d,3); out({ok:r.ok,msg:r.message,a:D.toApply(r.draft,s)})""")
    assert got["ok"]
    assert got["a"]["hotseat_count"] == 3
    assert sorted(i["x"] for i in got["a"]["layout"]["hotseat"]) == [0, 1, 2]
    assert problems(got["a"]) == []


def test_settings_go_as_look_prefs_and_count():
    got = run("""
      const s=D.fromHome(HOME); const d=D.copy(s); d.label_gap_dp=-4; d.icon_scale=1.2;
      out({n:D.changes(d,s),a:D.toApply(d,s)})""")
    assert got["n"] == 2 and got["a"]["look_prefs"] == {"icon_scale": 1.2, "label_gap_dp": -4}


def test_problems_point_at_items_by_id_or_cell():
    got = run("""
      let d=D.drop(D.fromHome(HOME),{app:CAMERA},{screen:1,x:1,y:1}).draft;
      out(D.problemKeys(d,[{message:'bad',where:'screen',screen:1,x:1,y:1,id:null},
                           {message:'kid',where:'screen',screen:0,x:2,y:4,id:4},
                           {message:'dock',where:'hotseat',screen:null,x:1,y:null,id:null}]))""")
    assert set(got.values()) == {"bad", "kid", "dock"}
    assert "i3" in got and "i11" in got          # a folder app's problem marks its folder


# launcher.device_profile's icon part of the emulator at 1440x3120, density 600, grid 4x7, label size
# 0.9, font scale 1.15 (patch 0025): the label gap as the phone laid it out for gap 3 dp.
GAP_GEOMETRY = {"screen": {"w": 1440, "h": 3120, "density": 3.75},
                "workspace": {"x": 30, "y": 217, "w": 1380, "h": 2448, "cell_w": 345, "cell_h": 349,
                              "gap_x": 0, "gap_y": 0},
                "icon": {"size": 244, "text_size": 45, "drawable_padding": -16, "top": 13, "labels": True,
                         "label_gap": {"dp": 3, "max_dp": 18, "shape_margin": 10, "cap_inset": 17,
                                       "line_height": 64, "icon_block": 275}}}


def label_gap(gaps, **draft):
    g = json.dumps(GAP_GEOMETRY)
    return run(f"const g={g};const s={{grid:[4,7],icon_scale:1,label_scale:1,label_gap_dp:3}};"
               f"out({json.dumps(gaps)}.map(v=>D.labelGap(g,Object.assign({{}},s,{json.dumps(draft)},{{label_gap_dp:v}}),s)))")


def test_label_gap_is_the_phones_padding_and_icon_top_for_every_gap():
    r = label_gap([0, 1, 3, 18])
    # padding = round(gap * 3.75) - 10 - 17; the block 275 + padding + 64 centred in the 349 px cell
    assert [(x["pad"], x["top"]) for x in r] == [(-27, 18), (-23, 16), (-16, 13), (41, 0)]
    assert r[2]["pad"] == GAP_GEOMETRY["icon"]["drawable_padding"]
    assert all(x["exact"] and x["maxDp"] == 18 for x in r)


def test_label_gap_holds_at_the_phones_maximum_and_never_below_touching():
    r = label_gap([19, 32, -4])
    assert [x["gapDp"] for x in r] == [18, 18, 0]
    assert r[0]["pad"] == r[1]["pad"] == 41


def test_label_gap_follows_a_bigger_icon_in_the_draft():
    r = label_gap([18], icon_scale=1.2)[0]
    assert not r["exact"] and r["icon"] == 293 and r["maxDp"] < 18 and r["gapDp"] == r["maxDp"]


def test_no_label_gap_from_a_phone_without_patch_0025():
    assert run("out(D.labelGap({screen:{density:3},icon:{size:10},workspace:{}},"
               "{grid:[4,5],icon_scale:1,label_scale:1,label_gap_dp:2},null))") is None
