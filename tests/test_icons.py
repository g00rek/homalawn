"""The icon cache and the home-screen editor's reads (home.get / home.apps / home.widgets), over the
fake link: keys first, pictures only for what is missing or stale, batches, deferred answers."""
import base64
import json
import os

import pytest

from homalawn import icons, ops
from test_transport import linked  # noqa: F401 (fixture)


def png(tag: str) -> bytes:
    return b"\x89PNG\r\n\x1a\n" + tag.encode()


class Pictures:
    """launcher.app_icons / launcher.widget_previews behind the fake link, like the phone: every
    entry has a key; pictures only without keys_only; past `budget` pictures the rest are deferred."""

    def __init__(self, phone, field, method, names):
        self.field, self.keys = field, {n: "k1" for n in names}
        self.asked: list[list[str] | None] = []   # names asked for with pictures (None = all)
        self.keys_calls = 0
        self.budget = None
        phone.handlers[method] = self

    def __call__(self, p):
        want = p.get("components") or p.get("providers") or list(self.keys)
        if p.get("keys_only"):
            self.keys_calls += 1
        else:
            self.asked.append(list(want))
        out = []
        for i, n in enumerate(w for w in want if w in self.keys):
            e = {self.field: n, "label": n.split("/")[0].upper(), "key": self.keys[n]}
            if not p.get("keys_only"):
                if self.budget is not None and i >= self.budget:
                    e["deferred"] = True
                else:
                    e["png_b64"] = base64.b64encode(png(n + self.keys[n])).decode()
            out.append(e)
        return out, None


# launcher.device_profile of the emulator at 1440x3120, density 560, grid 4x6 (patch 0024)
GEOMETRY = {"screen": {"w": 1440, "h": 3120, "density": 3.5},
            "insets": {"left": 0, "top": 177, "right": 0, "bottom": 84}, "grid": {"cols": 4, "rows": 6},
            "workspace": {"x": 28, "y": 215, "w": 1384, "h": 2089, "cell_w": 346, "cell_h": 348, "gap_x": 0, "gap_y": 0},
            "icon": {"size": 228, "text_size": 46, "drawable_padding": 16, "top": 6, "labels": True},
            "hotseat": {"x": 28, "y": 2388, "w": 1384, "h": 257, "cell_w": 346, "cell_h": 257, "gap_x": 0,
                        "count": 4, "icon_size": 228, "icon_top": 14},
            "folder_icon_size": 210, "widget_padding": {"left": 21, "top": 21, "right": 21, "bottom": 21}}


@pytest.fixture
def home_phone(linked):  # noqa: F811
    t, dev, phone = linked
    phone.handlers["launcher.prefs_list"] = lambda p: ({
        "datastore": {"home_icon_size_factor": {"type": "float", "value": 1.2},
                      "homar_label_gap_dp": {"type": "float", "value": -3.0},
                      "enable_smartspace": {"type": "bool", "value": False}},
        "shared": {}}, None)
    phone.apps = [{"package": "com.a", "activity": "com.a.A", "label": "Alpha"},
                  {"package": "com.b", "activity": ".B", "label": "beta"}]
    apps = Pictures(phone, "component", "launcher.app_icons", ["com.a/com.a.A", "com.b/com.b.B"])
    phone.handlers["launcher.device_profile"] = lambda p: (GEOMETRY, None)
    s = ops.Session()
    s._t = t
    return s, phone, apps


def test_home_get_gives_layout_settings_and_icon_files(home_phone):
    s, phone, apps = home_phone
    d = ops.home_get(s).data
    assert d["grid"] == [4, 5] and d["hotseat_count"] == 4
    assert [i["package"] for i in d["screens"][0]["items"]] == ["com.a"]
    assert d["icon_scale"] == 1.2 and d["label_scale"] == 1.0 and d["label_gap_dp"] == -3.0
    assert d["top_row_reserved"] is False and d["show_labels"] is True
    path = d["icons"]["com.a/com.a.A"]
    assert open(path, "rb").read() == png("com.a/com.a.Ak1")
    assert apps.asked == [["com.a/com.a.A"]]   # only what is on the home screen
    assert d["geometry"] == GEOMETRY             # where the phone draws, as it sent it
    root = os.environ["XDG_CACHE_HOME"]
    assert path.startswith(os.path.join(root, "homalawn", "icons", "link-"))


def test_home_get_gives_the_label_gap_the_phone_draws(home_phone):
    # patch 0025: the phone holds the stored gap at what its cell has room for and says so
    s, phone, apps = home_phone
    geo = json.loads(json.dumps(GEOMETRY))
    geo["icon"]["label_gap"] = {"dp": 7.0, "max_dp": 7, "shape_margin": 9, "cap_inset": 15,
                                "line_height": 61, "icon_block": 233}
    phone.handlers["launcher.device_profile"] = lambda p: (geo, None)
    assert ops.home_get(s).data["label_gap_dp"] == 7.0


def test_cache_fetches_only_missing_or_stale(home_phone):
    s, phone, apps = home_phone
    first = ops.home_apps(s).data
    assert [(a["component"], a["label"]) for a in first] == [("com.a/com.a.A", "COM.A"), ("com.b/com.b.B", "COM.B")]
    assert all(a["icon"] and os.path.isfile(a["icon"]) for a in first)
    assert len(apps.asked) == 1
    ops.home_apps(s)
    assert len(apps.asked) == 1 and apps.keys_calls == 2   # keys asked again, no pictures
    apps.keys["com.b/com.b.B"] = "k2"                       # its icon changed (pack, update, ...)
    again = {a["component"]: a for a in ops.home_apps(s).data}
    assert apps.asked[-1] == ["com.b/com.b.B"]
    assert open(again["com.b/com.b.B"]["icon"], "rb").read() == png("com.b/com.b.Bk2")
    os.unlink(again["com.a/com.a.A"]["icon"])               # a file gone from the cache comes back
    ops.home_apps(s)
    assert apps.asked[-1] == ["com.a/com.a.A"]


def test_batches_and_deferred_pictures_are_asked_again(home_phone, monkeypatch):
    s, phone, apps = home_phone
    names = [f"com.p{i}/com.p{i}.M" for i in range(7)]
    apps.keys = {n: "k" for n in names}
    phone.apps = [{"package": n.split("/")[0], "activity": n.split("/")[1], "label": n} for n in names]
    monkeypatch.setattr(icons, "BATCH", 3)
    apps.budget = 2
    got = ops.home_apps(s).data
    assert all(a["icon"] for a in got)
    assert all(len(b) <= 3 for b in apps.asked)
    assert sorted(n for b in apps.asked for n in b[:2]) == sorted(names)


def test_index_maps_components_to_key_and_file(home_phone):
    s, phone, apps = home_phone
    ops.home_apps(s)
    cache = icons.IconCache(s.launcher().device_key())
    idx = json.loads((cache.dir / icons.INDEX).read_text())
    rec = idx["apps"]["com.a/com.a.A"]
    assert rec["key"] == "k1" and (cache.dir / rec["file"]).is_file() and rec["file"].endswith(".png")


def test_older_phone_build_without_icons_still_lists(home_phone):
    s, phone, apps = home_phone
    del phone.handlers["launcher.app_icons"]
    d = ops.home_get(s).data
    assert d["icons"] == {}
    assert [a["icon"] for a in ops.home_apps(s).data] == [None, None]


def test_home_widgets_with_previews(home_phone):
    s, phone, apps = home_phone
    phone.widgets = [{"provider": "com.w/.W", "label": "Weather", "cells": [2, 1], "minW": 110, "minH": 40}]
    Pictures(phone, "provider", "launcher.widget_previews", ["com.w/com.w.W"])
    w, = ops.home_widgets(s).data
    assert w["provider"] == "com.w/.W" and w["cells"] == [2, 1] and w["minW"] == 110
    assert open(w["preview"], "rb").read() == png("com.w/com.w.Wk1")


def test_phone_dir_name_is_a_plain_folder_name():
    assert icons.phone_dir_name("link:ab12") == "link-ab12"
    assert icons.phone_dir_name("adb:192.168.1.2:5555") == "adb-192.168.1.2-5555"
    assert icons.phone_dir_name(None) == "unknown"
