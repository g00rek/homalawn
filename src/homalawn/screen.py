"""Screen control over the paired link (`screen.*`, spec D10): read what is on the phone's screen
and operate it through Lawnchair's accessibility service.

Everything here is pure (argument parsing, tree compaction) or a thin call through a `Transport`;
only the link carries `screen.*` (over adb just `screen.status`).
"""
from __future__ import annotations

import re
from typing import Any

from .transport import Transport

KEYS = ("back", "home", "recents", "notifications", "quick_settings")
SCREEN_OFF = "screen_off"
RATE_LIMITED = "rate_limited"
SHOT_RETRY_S = 0.4  # the phone allows one screenshot every 340 ms
MSG_OFF = ("Screen control is off on the phone: turn it on in Homa Lawn's settings › Permissions › Screen control "
           "(it lets Homa Lawn on this computer read and operate the screen)")
MSG_RESTRICTED = (" If Android says Restricted setting: Settings › Apps › Homa Lawn › ⋮ › "
                  "Allow restricted settings, then try again.")

_NODE = re.compile(r"n\d{1,6}")
_NUM = r"-?\d+(?:\.\d+)?"
_PAIR = re.compile(rf"\s*({_NUM})\s*[,x ]\s*({_NUM})\s*")


class ScreenArgError(ValueError):
    """The command line names no usable point, node or key."""


def _num(s: str, what: str) -> float:
    try:
        v = float(s)
    except ValueError:
        raise ScreenArgError(f"{what} must be a number, not {s!r}")
    if v < 0:
        raise ScreenArgError(f"{what} must not be negative")
    return v


def _int_or_float(v: float) -> int | float:
    return int(v) if v == int(v) else v


def parse_point(args: list[str], what: str = "point") -> dict:
    """`["n12"]` -> {"node": "n12"}; `["540,1200"]` or `["540", "1200"]` -> {"x": 540, "y": 1200}."""
    args = [a for a in args if a is not None and a != ""]
    if len(args) == 1 and _NODE.fullmatch(args[0]):
        return {"node": args[0]}
    if len(args) == 1:
        m = _PAIR.fullmatch(args[0])
        if not m:
            raise ScreenArgError(f"{what} must be a node id like n12 or X,Y like 540,1200, not {args[0]!r}")
        x, y = m.groups()
    elif len(args) == 2:
        x, y = args
    else:
        raise ScreenArgError(f"{what}: give a node id (n12), X,Y, or X Y")
    return {"x": _int_or_float(_num(x, "x")), "y": _int_or_float(_num(y, "y"))}


def parse_swipe(args: list[str]) -> dict:
    """`["100,1500", "100,500"]` or four numbers -> {x1, y1, x2, y2}. Node ids are not accepted."""
    args = [a for a in args if a is not None and a != ""]
    if len(args) == 2:
        a, b = (parse_point([s], what) for s, what in zip(args, ("from", "to")))
    elif len(args) == 4:
        a, b = parse_point(args[:2], "from"), parse_point(args[2:], "to")
    else:
        raise ScreenArgError("swipe needs FROM and TO as X,Y X,Y (or X1 Y1 X2 Y2)")
    if "node" in a or "node" in b:
        raise ScreenArgError("swipe takes coordinates, not node ids")
    return {"x1": a["x"], "y1": a["y"], "x2": b["x"], "y2": b["y"]}


def parse_key(name: str) -> str:
    k = name.strip().lower().replace("-", "_")
    if k not in KEYS:
        raise ScreenArgError(f"key must be one of {', '.join(KEYS)}, not {name!r}")
    return k


# -- tree ------------------------------------------------------------------------------------

def _label(n: dict) -> str:
    text, desc = n.get("text") or "", n.get("desc") or ""
    if text and desc and desc != text:
        return f"{text} ({desc})"
    return text or desc


def _actionable(n: dict) -> bool:
    return bool(n.get("click") or n.get("edit") or n.get("scroll"))


def _centre(n: dict) -> tuple[int, int]:
    left, top, right, bottom = n.get("bounds") or (0, 0, 0, 0)
    return (left + right) // 2, (top + bottom) // 2


def _folds(n: dict) -> bool:
    """A row that is tapped or typed into takes the text inside it as its name; a scrollable
    container does not (it would swallow the whole page)."""
    return bool(n.get("click") or n.get("edit"))


def _checked(n: dict) -> bool | None:
    """True/False for a checkable node (checkbox, switch), None for any other."""
    return bool(n.get("checked")) if n.get("checkable") else None


def compact(nodes: list[dict], max_parts: int = 3) -> list[dict]:
    """The phone's node list folded for reading: a clickable or editable node without a label of
    its own (a settings row, say) takes the labels of the plain text nodes inside it, which are
    then left out; a row also takes the checked state of a plain checkbox or switch inside it.
    Text inside a nested actionable node stays with that node. Returns
    [{id, class, label, flags, checked, at, depth}] in the phone's order."""
    out = []
    folded: set[int] = set()
    for i, n in enumerate(nodes):
        if i in folded:
            continue
        depth = int(n.get("depth") or 0)
        label = _label(n)
        checked = _checked(n)
        if _folds(n) and checked is None:  # a row's switch, even when the row has its own label
            j = i + 1
            while j < len(nodes) and int(nodes[j].get("depth") or 0) > depth:
                c = nodes[j]
                if _actionable(c):
                    break
                if _checked(c) is not None:
                    checked = _checked(c)
                    if not _label(c):
                        folded.add(j)
                    break
                j += 1
        if not label and _folds(n):
            parts = []
            inner: int | None = None  # depth of a nested actionable node whose subtree we skip
            j = i + 1
            while j < len(nodes) and int(nodes[j].get("depth") or 0) > depth:
                c = nodes[j]
                cd = int(c.get("depth") or 0)
                if inner is not None and cd <= inner:
                    inner = None
                if inner is None:
                    if _actionable(c):
                        inner = cd
                    elif _label(c):
                        folded.add(j)
                        if len(parts) < max_parts:
                            parts.append(_label(c))
                j += 1
            label = " · ".join(parts)
        flags = [f for f in ("click", "edit", "scroll", "focus") if n.get(f)]
        out.append({"id": n.get("id"), "class": n.get("class") or "", "label": label, "flags": flags,
                    "checked": checked, "at": _centre(n), "depth": depth})
    return out


def format_tree(tree: dict) -> list[str]:
    """The compacted tree as indented lines: `n12 Button "OK" [click] @540,1200`; a checkbox or
    switch (or the row holding one) adds `[checked]` or `[unchecked]`."""
    rows = compact(tree.get("nodes") or [])
    lines = [f"{tree.get('package') or '?'} ({len(rows)} items)"]
    base = min((r["depth"] for r in rows), default=0)
    for r in rows:
        ind = "  " * (r["depth"] - base + 1)
        label = f' "{r["label"]}"' if r["label"] else ""
        flags = f" [{','.join(r['flags'])}]" if r["flags"] else ""
        if r["checked"] is not None:
            flags += " [checked]" if r["checked"] else " [unchecked]"
        x, y = r["at"]
        lines.append(f"{ind}{r['id']} {r['class']}{label}{flags} @{x},{y}")
    if tree.get("truncated"):
        lines.append("  … (more items than the phone sends at once)")
    return lines


# -- calls -----------------------------------------------------------------------------------

def call(t: Transport, method: str, params: dict | None = None) -> Any:
    return t.call(method, params or {})


def shot(t: Transport) -> bytes:
    """A PNG of the screen. Over the link the transport itself waits once and retries when the
    phone says it is too soon (`rate_limited`), so `homalawn screenshot` gets the same retry."""
    return t.screenshot()


def off_message(status: dict | None) -> str:
    """What to tell the user when screen control is off, from `screen.status` when known."""
    msg = MSG_OFF + "."
    if status is None or status.get("restricted") is not False:
        msg += MSG_RESTRICTED
    return msg
