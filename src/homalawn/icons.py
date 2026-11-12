"""The phone's app icons and widget previews, cached on this computer for the home-screen editor.

The phone draws them (`launcher.app_icons`, `launcher.widget_previews`, patch 0016) the way its
home screen does and gives each a `key` that changes when the picture would. The cache keeps one
folder per phone, `$XDG_CACHE_HOME/homalawn/icons/<phone>/` (`~/.cache/...` without it), with the
pictures as `<sha256>.png` and `index.json` mapping each component (or widget provider) to its key
and file. A sync first asks the phone for keys only, then fetches just the missing or stale
pictures, in batches that keep every answer well under the link's 1 MiB line.
"""
from __future__ import annotations

import base64
import contextlib
import hashlib
import json
import os
import re
import tempfile
from pathlib import Path

from .transport import ProviderError, Transport

APP_ICONS = "launcher.app_icons"
WIDGET_PREVIEWS = "launcher.widget_previews"
SIZE = 128          # px: sharp at the editor's size on a 2x screen
BATCH = 24          # pictures asked for in one call
MAX_ROUNDS = 50     # deferred answers asked again at most this often in one sync
INDEX = "index.json"


def cache_root() -> Path:
    base = os.environ.get("XDG_CACHE_HOME") or str(Path(os.environ.get("HOME") or Path.home()) / ".cache")
    return Path(base) / "homalawn" / "icons"


def phone_dir_name(device_key: str | None) -> str:
    """A folder name for a phone's device key (`link:<fp>`, `adb:<serial>`)."""
    return re.sub(r"[^A-Za-z0-9._-]", "-", device_key or "unknown")


class IconCache:
    """One phone's pictures. `kind` "apps" (keyed by component) or "widgets" (by provider)."""

    def __init__(self, device_key: str | None, root: Path | None = None, size: int = SIZE):
        self.dir = (root or cache_root()) / phone_dir_name(device_key)
        self.size = size

    # -- index --------------------------------------------------------------------------------
    def _load(self) -> dict:
        try:
            raw = json.loads((self.dir / INDEX).read_text())
        except (OSError, ValueError):
            return {}
        return raw if isinstance(raw, dict) else {}

    def _save(self, index: dict) -> None:
        self.dir.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=self.dir, prefix=".index-", suffix=".tmp")
        try:
            with os.fdopen(fd, "w") as f:
                json.dump(index, f, indent=1, sort_keys=True)
            os.replace(tmp, self.dir / INDEX)
        except BaseException:
            with contextlib.suppress(OSError):
                os.unlink(tmp)
            raise

    def _write_png(self, data: bytes) -> str:
        name = hashlib.sha256(data).hexdigest() + ".png"
        p = self.dir / name
        if not p.exists():
            self.dir.mkdir(parents=True, exist_ok=True)
            fd, tmp = tempfile.mkstemp(dir=self.dir, prefix=".png-", suffix=".tmp")
            with os.fdopen(fd, "wb") as f:
                f.write(data)
            os.replace(tmp, p)
        return name

    # -- sync ---------------------------------------------------------------------------------
    def apps(self, transport: Transport, components: list[str] | None = None) -> dict[str, dict]:
        """component -> {label, path} for the phone's launcher apps (all, or `components`);
        path is None when the phone has no picture for it. {} when the phone's build cannot draw
        icons (before patch 0016)."""
        return self._sync(transport, "apps", APP_ICONS, "components", "component", components)

    def widgets(self, transport: Transport, providers: list[str] | None = None) -> dict[str, dict]:
        """provider (as the phone names it) -> {label, path} of its preview picture."""
        return self._sync(transport, "widgets", WIDGET_PREVIEWS, "providers", "provider", providers)

    def _sync(self, transport: Transport, kind: str, method: str, param: str, field: str,
              names: list[str] | None) -> dict[str, dict]:
        if names is not None and not names:
            return {}
        base = {"size": self.size} | ({param: list(names)} if names else {})
        try:
            keys = transport.call(method, base | {"keys_only": True})
        except ProviderError as e:
            if e.code == "unknown_method" or "unknown method" in str(e):
                return {}
            raise
        index = self._load()
        known = index.setdefault(kind, {})
        want = {e[field]: e for e in keys if isinstance(e, dict) and e.get(field)}
        stale = [n for n, e in want.items()
                 if known.get(n, {}).get("key") != e.get("key") or not self._has(known.get(n, {}).get("file"))]
        rounds = 0
        while stale and rounds < MAX_ROUNDS:
            rounds += 1
            batch, stale = stale[:BATCH], stale[BATCH:]
            got = transport.call(method, base | {param: batch})
            again = []
            for e in got if isinstance(got, list) else []:
                n = e.get(field)
                if n not in want:
                    continue
                if e.get("deferred"):
                    again.append(n)
                    continue
                png = e.get("png_b64")
                rec = {"key": e.get("key"), "label": e.get("label"), "file": None}
                if png:
                    rec["file"] = self._write_png(base64.b64decode(png))
                known[n] = rec
            stale = again + stale
        for n, e in want.items():   # labels change with the language, keys do not
            if n in known:
                known[n]["label"] = e.get("label")
        self._save(index)
        return {n: {"label": e.get("label"),
                    "path": str(self.dir / known[n]["file"]) if known.get(n, {}).get("file") else None}
                for n, e in want.items()}

    def _has(self, name: str | None) -> bool:
        return bool(name) and (self.dir / name).is_file()
