"""Which Homa Lawn app a phone runs, and the package an APK installs."""
from pathlib import Path

from homalawn import appid

FIX = Path(__file__).parent / "fixtures"


def test_apk_package_reads_the_compiled_manifest():
    assert appid.apk_package(FIX / "manifest-app.homalawn.apk") == "app.homalawn"
    assert appid.apk_package(FIX / "manifest-app.lawnchair.debug.apk") == "app.lawnchair.debug"


def test_apk_package_of_something_else_is_none(tmp_path):
    junk = tmp_path / "x.apk"
    junk.write_bytes(b"not a zip")
    assert appid.apk_package(junk) is None
    assert appid.apk_package(tmp_path / "missing.apk") is None


def test_names_follow_the_package():
    assert appid.authority("app.homalawn") == "app.homalawn.omalawn"
    assert appid.launcher("app.homalawn.debug") == "app.homalawn.debug/app.lawnchair.LawnchairLauncher"
    assert appid.a11y_service("app.lawnchair.debug") == "app.lawnchair.debug/app.lawnchair.LawnchairAccessibilityService"
    assert appid.is_old("app.lawnchair.debug") and not appid.is_old("app.homalawn")


def test_build_folder_per_app():
    assert appid.apk_dir("app.homalawn.debug").name == "debug"
    assert appid.apk_dir("app.homalawn").name == "release"
    assert appid.apk_dir("app.lawnchair.debug").name == "release"   # an old phone moves to the release
    assert appid.apk_dir(None).name == "release"
