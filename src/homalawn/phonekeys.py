# Adapted from Flux (https://github.com/bjarneo/flux), MIT License, Copyright (c) 2026 Bjarne Oeverli
"""Phones are known by their certificate fingerprint; names are for people (spec section 4).

`find` is adapted from Flux: internal/core/api.go `find` (exact id, else name without case, two
names an error that lists the ids)."""
from __future__ import annotations

import copy
import os
import re
from pathlib import Path

_FP = re.compile(r"[0-9a-f]{64}")
_PREFIX = re.compile(r"[0-9a-f]{8,64}")


def is_fp(key: str) -> bool:
    return isinstance(key, str) and _FP.fullmatch(key) is not None


class NotFound(LookupError):
    pass


class Ambiguous(LookupError):
    def __init__(self, message: str, matches: list[tuple[str, str]]):
        super().__init__(message)
        self.matches = matches


def rekey(data: dict, peers: list[tuple[str, str]]) -> tuple[dict, bool]:
    """Entries keyed by a phone's name move to its fingerprint (copied to each phone of that name);
    an entry already under the fingerprint wins; names no paired phone has stay as they are."""
    out = {k: v for k, v in data.items() if is_fp(k)}
    moved = False
    for key, value in data.items():
        if is_fp(key):
            continue
        fps = [fp for fp, name in peers if name == key]
        if not fps:
            out[key] = value
            continue
        moved = True
        for fp in fps:
            out.setdefault(fp, copy.deepcopy(value))
    return out, moved


def find(key: str | None, phones: list[tuple[str, str]]) -> str:
    """The fingerprint `key` names among `phones` [(fp, name)]: an exact fingerprint, a prefix of
    8+ hex characters naming one phone, else a name without case. None: the only phone."""
    if key is None:
        if len(phones) == 1:
            return phones[0][0]
        if not phones:
            raise NotFound("No phone is paired; run `homalawn pair`.")
        raise Ambiguous("Several phones are paired (" + ", ".join(sorted(n for _, n in phones))
                        + "); name one.", phones)
    k = key.strip()
    if any(fp == k for fp, _ in phones):
        return k
    if _PREFIX.fullmatch(k.lower()):
        hit = [(fp, n) for fp, n in phones if fp.startswith(k.lower())]
        if len(hit) == 1:
            return hit[0][0]
    named = [(fp, n) for fp, n in phones if n.casefold() == k.casefold()]
    if len(named) == 1:
        return named[0][0]
    if not named:
        raise NotFound(f"No paired phone called {key!r}; `homalawn peers` lists them.")
    ids = ", ".join(sorted(fp[:8] + "…" for fp, _ in named))
    raise Ambiguous(f"{len(named)} phones are named \"{key}\": {ids}. Name one by its fingerprint "
                    "(`homalawn peers` lists them).", named)


def keep_once(path: Path, suffix: str) -> None:
    """`path` as it is now, beside it as name+suffix, owner-only; never replaced once written."""
    b = path.with_name(path.name + suffix)
    data = path.read_bytes()
    try:
        fd = os.open(b, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError:
        return
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(data)
        os.chmod(b, 0o600)
    except BaseException:
        try:
            os.unlink(b)
        except OSError:
            pass
        raise
