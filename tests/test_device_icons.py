"""The home-screen editor against the test AVD (HOMALAWN_SERIAL=emulator-5560), over the paired link.

Drives the real `launcher.app_icons` / `launcher.widget_previews` (patch 0016) and the desktop's
icon cache on top of them, then one home.apply round trip (the draft written in one go, with a
negative label gap) that `undo` puts back. The cache lives in a temporary XDG_CACHE_HOME; the AVD
ends as it was found (the session hygiene fixture checks that)."""
import base64
import io
import os

import pytest
from PIL import Image

from homalawn import icons, ops, prefs, snapshots
from homalawn.session import Launcher

pytestmark = pytest.mark.device
SERIAL = os.environ.get("HOMALAWN_SERIAL")
if not SERIAL:
    pytest.skip("HOMALAWN_SERIAL not set", allow_module_level=True)


@pytest.fixture
def phone(link_transport, avd_hygiene, tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))
    s = ops.Session()
    s._t = link_transport
    la = Launcher(link_transport)
    la.refresh()
    la.wait_loaded()
    return s, link_transport


def picture(b64: str) -> Image.Image:
    return Image.open(io.BytesIO(base64.b64decode(b64)))


def test_app_icons_keys_then_pictures_at_the_asked_size(phone):
    s, t = phone
    keys = t.call("launcher.app_icons", {"keys_only": True, "size": 96})
    names = {a["package"] + "/" + (a["package"] + a["activity"] if a["activity"].startswith(".") else a["activity"])
             for a in t.call("launcher.apps_list")}
    assert {e["component"] for e in keys} == names
    assert all(e["key"] and e["label"] and "png_b64" not in e for e in keys)
    two = [e["component"] for e in keys[:2]]
    got = t.call("launcher.app_icons", {"components": two, "size": 96})
    assert [e["component"] for e in got] == two
    for e, k in zip(got, keys[:2]):
        img = picture(e["png_b64"])
        assert img.size == (96, 96) and img.format == "PNG"
        assert img.convert("RGBA").getextrema()[3][1] > 0          # something is drawn
        assert e["key"] == k["key"]                                  # same key with or without the picture
    other = t.call("launcher.app_icons", {"components": two[:1], "size": 48, "keys_only": True})
    assert other[0]["key"] != keys[0]["key"]                          # the size is part of the key


def test_widget_previews_fit_the_box(phone):
    s, t = phone
    keys = t.call("launcher.widget_previews", {"keys_only": True})
    assert keys and all(e["key"] for e in keys)
    got = t.call("launcher.widget_previews", {"providers": [keys[0]["provider"]], "size": 120})
    img = picture(got[0]["png_b64"])
    assert max(img.size) == 120 and got[0]["kind"] in ("preview", "icon")


def test_editor_reads_fill_the_cache_once(phone):
    s, t = phone
    home = ops.home_get(s).data
    comps = ops._layout_components(home)
    assert comps and set(home["icons"]) == set(comps)
    assert all(os.path.isfile(p) for p in home["icons"].values())
    apps = ops.home_apps(s).data
    assert apps and all(a["icon"] and os.path.isfile(a["icon"]) for a in apps)
    cache = icons.IconCache(s.launcher().device_key())
    files = sorted(p.name for p in cache.dir.glob("*.png"))
    ops.home_apps(s)
    assert sorted(p.name for p in cache.dir.glob("*.png")) == files    # nothing fetched again
    widgets = ops.home_widgets(s).data
    assert widgets and any(w["preview"] for w in widgets)


def test_home_apply_writes_the_draft_in_one_go_and_undo_puts_it_back(phone):
    s, t = phone
    before = prefs.current(t)["datastore"].get("homar_label_gap_dp")
    home = ops.home_get(s).data
    layout = {"grid": {"cols": home["grid"][0], "rows": home["grid"][1]}, "screens": home["screens"],
              "hotseat": home["hotseat"]}
    r = ops.home_apply(s, layout, look={"label_gap_dp": 4, "icon_scale": home["icon_scale"]})
    snap = r.data["snapshot"]
    assert snap.startswith("home-") and snapshots.list_undoable(s.launcher().device_key())[0] == snap
    assert prefs.current(t)["datastore"]["homar_label_gap_dp"]["value"] == 4.0
    after = ops.home_get(s).data
    assert after["label_gap_dp"] == 4.0
    assert ops._layout_components(after) == ops._layout_components(home)
    with pytest.raises(ops.OpError) as e:   # a bad draft changes nothing and says where
        ops.home_apply(s, dict(layout, hotseat=layout["hotseat"] + [{"type": "app", "package": "x.y",
                                                                     "activity": "x.y.Z", "x": 99}]))
    assert e.value.code == ops.INVALID and any(p["where"] == "hotseat" for p in e.value.problems)
    ops.undo(s)
    assert prefs.current(t)["datastore"].get("homar_label_gap_dp") == before
