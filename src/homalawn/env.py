"""Homa Lawn's environment: HOMALAWN_* variables (the older HOMAR_* and OMALAWN_* names still
work) and the default config, data and state folders, moved once from their older names.

The project was called OmaLawn until 2026-10-13 and Homar until 2026-10-15; scripts, units and
agent registrations made then may still set HOMAR_SERIAL / OMALAWN_SERIAL and friends, and the
folders may still sit at ~/.config/homar (or ~/.config/omalawn). The newest name wins whenever
several are set.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

PREFIX = "HOMALAWN_"
OLD_PREFIXES = ("HOMAR_", "OMALAWN_")  # the pre-rename names, newest first, read only as a fallback
OLD_NAMES = ("homar", "omalawn")         # the folder names before, newest first
NAME = "homalawn"


def get(name: str) -> str | None:
    """HOMALAWN_<name>, else HOMAR_<name>, else OMALAWN_<name>; None when none is set (or all
    are empty)."""
    for prefix in (PREFIX, *OLD_PREFIXES):
        v = os.environ.get(prefix + name)
        if v:
            return v
    return None


def migrate(old: Path, new: Path, was: str = "Homar") -> Path:
    """`new`, after moving `old` there if only `old` exists (one rename, so either the whole
    folder moved or nothing did), leaving a link at `old` for anything still using that path."""
    if new.exists() or new.is_symlink() or old.is_symlink() or not old.is_dir():
        return new
    new.parent.mkdir(parents=True, exist_ok=True)
    try:
        os.rename(old, new)
    except OSError as e:
        print(f"homalawn: could not move {old} to {new} ({e}); using {new}", file=sys.stderr)
        return new
    try:
        old.symlink_to(new, target_is_directory=True)
    except OSError:
        pass  # the move is what matters; the link is only a courtesy
    print(f"homalawn: moved {old} to {new} ({was} is now Homa Lawn); {old} links to it", file=sys.stderr)
    return new


def migrate_chain(parent: Path) -> Path:
    """parent/homalawn, after moving the newest older real folder there: parent/homar, or, for a
    computer still on OmaLawn's folder, parent/omalawn. Links an earlier move left behind (omalawn
    pointing at homar) keep working: they point at the link the move leaves at homar."""
    new = parent / NAME
    for old, was in zip(OLD_NAMES, ("Homar", "OmaLawn")):
        p = parent / old
        if p.is_dir() and not p.is_symlink():
            return migrate(p, new, was)
    return new


def config_dir() -> Path:
    return migrate_chain(Path.home() / ".config")


def data_dir() -> Path:
    return migrate_chain(Path.home() / ".local" / "share")


def state_dir() -> Path:
    """HOMALAWN_STATE_DIR (or an older name), else $XDG_STATE_HOME/homalawn (~/.local/state/homalawn),
    moved there from the homar folder the first time."""
    p = get("STATE_DIR")
    if p:
        return Path(p)
    base = os.environ.get("XDG_STATE_HOME") or str(Path(os.environ.get("HOME") or Path.home()) / ".local" / "state")
    return migrate_chain(Path(base))
