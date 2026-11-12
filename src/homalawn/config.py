"""~/.config/homalawn/config.toml (or HOMALAWN_CONFIG):

    serial = "emulator-5560"              # adb device (HOMALAWN_SERIAL wins)
    package = "app.homalawn"              # which Homa Lawn app adb talks to (HOMALAWN_PACKAGE wins;
                                          # default: the one installed, see adb.Device.package)

A phone's addresses live in its pairing record (link/peers.json). Older files listed them here as
`[peers."Pixel 8"] hosts = [...]`; those move there once (link/addresses.migrate_config_hosts) and
are not read otherwise.
"""
from __future__ import annotations

import datetime
import json
import os
import re
import tomllib
from dataclasses import dataclass
from pathlib import Path

from . import env


@dataclass
class Config:
    serial: str | None = None
    package: str | None = None


def path() -> Path:
    p = env.get("CONFIG")
    return Path(p) if p else env.config_dir() / "config.toml"


def _raw() -> dict:
    p = path()
    return tomllib.loads(p.read_text()) if p.exists() else {}


def _from_raw(raw: dict) -> Config:
    pkg = raw.get("package")
    return Config(serial=raw.get("serial"), package=pkg if isinstance(pkg, str) and pkg else None)


def _file() -> Config:
    return _from_raw(_raw())


def load() -> Config:
    cfg = _file()
    cfg.serial = env.get("SERIAL") or cfg.serial
    cfg.package = env.get("PACKAGE") or cfg.package
    return cfg


HEADER = "# homalawn settings. homalawn rewrites this file when it saves (comments are not kept; every setting is).\n"
_BARE = re.compile(r"[A-Za-z0-9_-]+")


def _q(s: str) -> str:
    return json.dumps(s, ensure_ascii=False)  # a JSON string is a valid TOML basic string


def _key(k: str) -> str:
    return k if _BARE.fullmatch(k) else _q(k)


def _val(v) -> str:
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, (int, float)):
        return repr(v)
    if isinstance(v, str):
        return _q(v)
    if isinstance(v, (datetime.datetime, datetime.date, datetime.time)):
        return v.isoformat()
    if isinstance(v, list):
        return "[" + ", ".join(_val(x) for x in v) + "]"
    if isinstance(v, dict):
        return "{" + ", ".join(f"{_key(k)} = {_val(x)}" for k, x in v.items()) + "}"
    raise TypeError(f"cannot write {type(v).__name__} to the config")


def _is_table_array(v) -> bool:
    return isinstance(v, list) and bool(v) and all(isinstance(x, dict) for x in v)


def _emit(d: dict, prefix: tuple[str, ...], out: list[str]) -> None:
    simple = {k: v for k, v in d.items() if not isinstance(v, dict) and not _is_table_array(v)}
    for k, v in simple.items():
        out.append(f"{_key(k)} = {_val(v)}")
    for k, v in d.items():
        name = ".".join(_key(x) for x in prefix + (k,))
        if isinstance(v, dict):
            out += ["", f"[{name}]"]
            _emit(v, prefix + (k,), out)
        elif _is_table_array(v):
            for item in v:
                out += ["", f"[[{name}]]"]
                _emit(item, prefix + (k,), out)


def dumps(raw: dict) -> str:
    out: list[str] = []
    _emit(raw, (), out)
    return HEADER + "\n".join(out).lstrip("\n") + "\n"


def _write(raw: dict) -> None:
    p = path()
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_name(p.name + ".homalawn-tmp")
    tmp.write_text(dumps(raw))
    os.replace(tmp, p)


def _merge(raw: dict, cfg: Config) -> dict:
    """`raw` (everything the file holds) with homalawn's own settings from `cfg`. `[peers]` hosts
    the migration has not moved yet (a phone not paired, or a name two phones share) stay as written."""
    raw = dict(raw)
    if cfg.serial is None:
        raw.pop("serial", None)
    else:
        raw["serial"] = cfg.serial
    return raw


def save(cfg: Config) -> None:
    """Write homalawn's settings, keeping every other setting in the file."""
    _write(_merge(_raw(), cfg))


def save_serial(serial: str) -> None:
    """Remember the adb device, keeping everything else in the file."""
    raw = _raw()
    cfg = _from_raw(raw)
    cfg.serial = serial
    _write(_merge(raw, cfg))
