"""Which Homa Lawn app a phone runs, and where this computer's builds of it are.

Since 2026-10-16 the phone app has its own identity (docs/development/patching-lawnchair.md):

- `app.homalawn`: what phones run. A release build signed with Homa Lawn's own key.
- `app.homalawn.debug`: the debuggable test build, for the emulator (the device tests use its
  shell-only test hooks and `run-as`).
- `app.lawnchair.debug`: the old app, Lawnchair's debug build, which phones ran before. Still
  recognised (everyday commands work with it), but no longer supported: update and setup refuse it.

The three are separate Android apps and can be installed side by side. Class names inside them are
Lawnchair's (`app.lawnchair.*`) in all three; only the package differs.
"""
from __future__ import annotations

import re
import struct
import zipfile
from pathlib import Path

RELEASE = "app.homalawn"
TEST = "app.homalawn.debug"
LEGACY = "app.lawnchair.debug"
PACKAGES = (RELEASE, TEST, LEGACY)   # the order a phone's app is picked in when several are installed
OLD = (LEGACY,)                      # apps no longer supported: update and setup refuse them

HOME_ACTIVITY = "app.lawnchair.LawnchairLauncher"
A11Y_SERVICE = "app.lawnchair.LawnchairAccessibilityService"   # screen control (patch 0004)
SETTINGS_ALIAS = "app.lawnchair.omalawn.HomaLawnSettings"      # the "Homa Lawn" settings icon (patch 0018)

REPO = Path(__file__).resolve().parents[2]
APK_ROOT = REPO / "lawnchair" / "build" / "outputs" / "apk" / "lawnWithQuickstepHomalawn"  # build-lawnchair.sh
APK_DIRS = {RELEASE: APK_ROOT / "release", TEST: APK_ROOT / "debug"}


def in_checkout() -> bool:
    """Is this the Homa Lawn source checkout (not an installed copy of the tool)?"""
    return (REPO / "pyproject.toml").is_file() and (REPO / "patches").is_dir()


def authority(pkg: str) -> str:
    """The shell-only control provider (patch 0001); the name keeps its OmaLawn-era suffix."""
    return f"{pkg}.omalawn"


def launcher(pkg: str) -> str:
    return f"{pkg}/{HOME_ACTIVITY}"


def a11y_service(pkg: str) -> str:
    return f"{pkg}/{A11Y_SERVICE}"


def is_old(pkg: str | None) -> bool:
    return pkg in OLD


def apk_dir(pkg: str | None = None) -> Path:
    """Where build-lawnchair.sh leaves the build for a phone running `pkg`: the test build's folder
    for the test app, the release folder for everything else (an old app moves to the release)."""
    return APK_DIRS.get(pkg or RELEASE, APK_DIRS[RELEASE])


def newest_apk(apk_dir: Path) -> Path | None:
    apks = sorted(apk_dir.glob("*.apk"), key=lambda p: p.stat().st_mtime, reverse=True) if apk_dir.is_dir() else []
    return apks[0] if apks else None


# -- the package an APK installs ------------------------------------------------------------------
_RES_STRING_POOL, _RES_XML_START_ELEMENT = 0x0001, 0x0102


def _pool_string(data: bytes, pool: int, i: int) -> str:
    """String `i` of the binary XML string pool starting at offset `pool`."""
    _, header, _ = struct.unpack_from("<HHI", data, pool)
    count, _, flags, strings_start, _ = struct.unpack_from("<IIIII", data, pool + 8)
    if not 0 <= i < count:
        raise ValueError("string index out of range")
    off = pool + strings_start + struct.unpack_from("<I", data, pool + header + 4 * i)[0]
    if flags & 0x100:  # UTF-8: length in characters, then in bytes (each 1 or 2 bytes long)
        off += 2 if data[off] & 0x80 else 1
        n = data[off]
        if n & 0x80:
            n, off = ((n & 0x7F) << 8) | data[off + 1], off + 2
        else:
            off += 1
        return data[off:off + n].decode("utf-8")
    n = struct.unpack_from("<H", data, off)[0]
    off += 2
    if n & 0x8000:
        n, off = ((n & 0x7FFF) << 16) | struct.unpack_from("<H", data, off)[0], off + 2
    return data[off:off + 2 * n].decode("utf-16-le")


def _manifest_strings(apk: Path, names: tuple[str, ...]) -> dict[str, str]:
    """String attributes `names` of the <manifest> element of an APK's compiled
    AndroidManifest.xml ({} when it is not an APK this can read)."""
    out: dict[str, str] = {}
    try:
        with zipfile.ZipFile(apk) as z:
            data = z.read("AndroidManifest.xml")
        pool = 8
        kind, _, pool_size = struct.unpack_from("<HHI", data, pool)
        if kind != _RES_STRING_POOL:
            return {}
        pos = pool + pool_size
        while pos + 8 <= len(data):
            kind, _, size = struct.unpack_from("<HHI", data, pos)
            if size < 8:
                return {}
            if kind == _RES_XML_START_ELEMENT:
                _, name, attr_start, attr_size, attr_count = struct.unpack_from("<iiHHH", data, pos + 16)
                if _pool_string(data, pool, name) != "manifest":
                    return {}
                for k in range(attr_count):
                    a = pos + 16 + attr_start + k * attr_size
                    _, aname, raw = struct.unpack_from("<iii", data, a)
                    if raw >= 0 and (n := _pool_string(data, pool, aname)) in names:
                        out[n] = _pool_string(data, pool, raw)
                return out
            pos += size
    except (OSError, KeyError, ValueError, UnicodeDecodeError, struct.error, zipfile.BadZipFile):
        return {}
    return out


def apk_package(apk: Path) -> str | None:
    """The package name in an APK's compiled AndroidManifest.xml, or None when it is not an APK
    this can read."""
    return _manifest_strings(apk, ("package",)).get("package")


def apk_version(apk: Path) -> str | None:
    """The versionName an APK carries (`16.0-dev.59396cd+homalawn.325`), else the one in its file
    name as build-lawnchair.sh names it (`HomaLawn-<versionName>-<release|debug>.apk`)."""
    v = _manifest_strings(apk, ("versionName",)).get("versionName")
    if v:
        return v
    m = re.fullmatch(r"HomaLawn-(.+)-(?:release|debug)\.apk", apk.name)
    return m.group(1) if m else None
