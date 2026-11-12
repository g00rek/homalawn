import pytest
from homalawn import snapshots


@pytest.fixture(autouse=True)
def data(tmp_path, monkeypatch):
    monkeypatch.setenv("HOMALAWN_DATA", str(tmp_path))
    return tmp_path


def test_save_load_roundtrip_binary():
    snapshots.save("one", {"a.db": b"\x00\xff\r\n", "a.db-journal": b"j"})
    assert snapshots.load("one") == {"a.db": b"\x00\xff\r\n", "a.db-journal": b"j"}


def test_list_newest_first():
    for n in ("auto-20260101-000001", "manual", "auto-20260101-000003"):
        snapshots.save(n, {"f": b"x"})
    assert snapshots.list_()[0] == "auto-20260101-000003"
    assert set(snapshots.list_()) == {"auto-20260101-000001", "manual", "auto-20260101-000003"}


def test_auto_name_format():
    import re
    assert re.fullmatch(r"auto-\d{8}-\d{6}", snapshots.auto_name())


def test_keeps_30_newest_auto_only():
    snapshots.save("manual", {"f": b"x"})
    for i in range(35):
        snapshots.save(f"auto-20260101-{i:06d}", {"f": b"x"})
    autos = [n for n in snapshots.list_() if n.startswith("auto-")]
    assert len(autos) == 30 and autos[-1] == "auto-20260101-000005"
    assert "manual" in snapshots.list_()


def test_load_missing_and_bad_name():
    with pytest.raises(FileNotFoundError):
        snapshots.load("nope")
    with pytest.raises(ValueError):
        snapshots.save("../evil", {"f": b""})


def test_delete():
    snapshots.save("x", {"f": b"1"})
    snapshots.delete("x")
    assert snapshots.list_() == []


# -- snapshots belong to one device (final review I1) -------------------------------------------
def test_device_is_recorded_and_not_one_of_the_files():
    snapshots.save("one", {"a.db": b"x"}, device="link:abc")
    assert snapshots.device_of("one") == "link:abc"
    assert snapshots.load("one") == {"a.db": b"x"}
    snapshots.save("two", {"a.db": b"x"})
    assert snapshots.device_of("two") is None
    with pytest.raises(ValueError):
        snapshots.save("three", {"meta.json": b"{}"})


def test_undo_list_is_per_device_and_skips_unknown_ones():
    snapshots.save("auto-20260101-000001", {"f": b"x"}, device="adb:emulator-5560")
    snapshots.save("auto-20260101-000002", {"f": b"x"}, device="link:phone")
    snapshots.save("auto-20260101-000003", {"f": b"x"})  # an older homalawn: unknown
    snapshots.save("prefs-20260101-000004", {"f": b"x"}, device="link:phone")
    assert snapshots.list_undoable("link:phone") == ["prefs-20260101-000004", "auto-20260101-000002"]
    assert snapshots.list_undoable("adb:emulator-5560") == ["auto-20260101-000001"]
    assert snapshots.list_undoable(None) == []


def test_keeps_20_newest_look_snapshots_per_device():
    for i in range(25):
        snapshots.save(f"look-20260101-{i:06d}", {"f": b"x"}, device="link:a")
    for i in range(3):
        snapshots.save(f"look-20250101-{i:06d}", {"f": b"x"}, device="link:b")  # older, other phone
    looks_a = [n for n in snapshots.list_() if n.startswith("look-") and snapshots.device_of(n) == "link:a"]
    assert len(looks_a) == 20 and min(looks_a) == "look-20260101-000005"
    assert len([n for n in snapshots.list_() if snapshots.device_of(n) == "link:b"]) == 3


def test_look_pruning_never_deletes_the_snapshot_just_made():
    # a clock set back: the new snapshot sorts oldest by name, it is still kept
    for i in range(20):
        snapshots.save(f"look-20260101-{i:06d}", {"f": b"x"}, device="link:a")
    snapshots.save("look-20200101-000000", {"f": b"x"}, device="link:a")
    names = snapshots.list_()
    assert "look-20200101-000000" in names
    assert len([n for n in names if n.startswith("look-")]) == 20


# -- what History shows: kind (the window section) and a plain title --------------------------------
def test_titles_from_what_was_recorded():
    snapshots.save("home-20261101-000001", {"f": b"x"}, device="link:a", meta={"section": "home", "changes": 3})
    snapshots.save("prefs-20261101-000002", {"f": b"x"}, meta={"section": "search", "changes": 2})
    snapshots.save("look-20261101-000003", {"f": b"x"})
    snapshots.annotate("look-20261101-000003", section="style", theme="Tokyo Night")
    snapshots.save("manual-20261101-000004", {"f": b"x"}, meta={"section": "manual"})
    snapshots.save("before-trip", {"f": b"x"}, meta={"section": "manual"})
    snapshots.save("grid-20261101-000005", {"f": b"x"}, meta={"section": "home", "detail": "grid 5x6"})
    snapshots.save("home-20261101-000006", {"f": b"x"}, meta={"section": "home", "changes": 1})
    assert snapshots.describe("home-20261101-000001") == {"kind": "home", "title": "Home screen: 3 changes"}
    assert snapshots.describe("home-20261101-000006")["title"] == "Home screen: 1 change"
    assert snapshots.describe("prefs-20261101-000002") == {"kind": "search", "title": "Search & gestures: 2 settings"}
    assert snapshots.describe("look-20261101-000003") == {"kind": "style", "title": "Wallpaper & style: Tokyo Night"}
    assert snapshots.describe("manual-20261101-000004") == {"kind": "manual", "title": "Saved by hand"}
    assert snapshots.describe("before-trip") == {"kind": "manual", "title": "Saved by hand: before-trip"}
    assert snapshots.describe("grid-20261101-000005")["title"] == "Home screen: grid 5x6"
    assert snapshots.device_of("home-20261101-000001") == "link:a"     # the device is kept beside it
    assert snapshots.load("look-20261101-000003") == {"f": b"x"}


def test_older_snapshots_get_their_sections_plain_title():
    for n in ("look-20261001-000001", "wallpaper-20261001-000002", "pre-restore-20261001-000003",
              "pre-restore-wallpaper-20261001-000004", "auto-20261001-000005", "nova-20261001-000006",
              "home-20261001-000007", "prefs-20261001-000008", "grid-20261001-000009", "manual-20261001-000010"):
        snapshots.save(n, {"f": b"x"}, device="link:a")
    got = {n: snapshots.describe(n) for n in snapshots.list_()}
    assert got["look-20261001-000001"] == {"kind": "style", "title": "Wallpaper & style"}
    assert got["wallpaper-20261001-000002"] == {"kind": "wallpaper", "title": "Wallpaper"}
    assert got["pre-restore-20261001-000003"] == {"kind": "before_restore", "title": "Before a restore"}
    assert got["pre-restore-wallpaper-20261001-000004"] == {"kind": "before_restore", "title": "Before a restore"}
    for n in ("auto-20261001-000005", "nova-20261001-000006", "home-20261001-000007", "grid-20261001-000009"):
        assert got[n] == {"kind": "home", "title": "Home screen"}
    assert got["prefs-20261001-000008"] == {"kind": "other", "title": "Settings"}
    assert got["manual-20261001-000010"] == {"kind": "manual", "title": "Saved by hand"}


def test_annotate_refuses_unknown_sections_and_ignores_missing_snapshots():
    snapshots.annotate("nope", section="home")          # nothing to annotate: no error
    snapshots.annotate(None, section="home")
    snapshots.save("x", {"f": b"x"})
    with pytest.raises(ValueError):
        snapshots.annotate("x", section="look")
