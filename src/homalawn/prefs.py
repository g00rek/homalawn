"""Lawnchair preferences: catalog of known keys, typed get/set through the provider."""
from __future__ import annotations

import json
import re
from importlib import resources
from typing import Any

_DS = re.compile(r'(boolean|int|float|string|stringSet)PreferencesKey\(\s*(?:name\s*=\s*)?"([^"]+)"')
_SH = re.compile(r'(Bool|Int|Float|String|IdpInt)Pref\(\s*"([^"]+)"')
_SH_TYPES = {"Bool": "bool", "Int": "int", "Float": "float", "String": "string", "IdpInt": "int"}
_DS_TYPES = {"boolean": "bool", "int": "int", "float": "float", "string": "string", "stringSet": "stringset"}
TYPES = ("bool", "int", "float", "string")

# Launcher3's own bookkeeping, stored next to the settings. Writing any of these back (e.g. an old
# migration_src_db_file) makes Launcher3 skip or fail its grid migration, crash, and reset the
# home screen to the default layout. legacy_popup_options_migrated is Lawnchair's one-shot flag
# for its long-press menu migration: it is in the generated catalog, but restoring an old value
# re-runs the migration and reorders the menu. Never written, whatever --store/--type say.
INTERNAL_PREFIXES = ("migration_src_", "launcher.", "EMPTY_DATABASE_CREATED")
INTERNAL_KEYS = frozenset({"version", "legacy_popup_options_migrated"})


def is_internal(key: str) -> bool:
    return key in INTERNAL_KEYS or key.startswith(INTERNAL_PREFIXES)


def writable_keys() -> set[str]:
    """Keys homalawn may write or remove: Lawnchair's settings catalog, minus internal state."""
    return {k for k in catalog() if not is_internal(k)}


def catalog_from_sources(datastore_sources: list[str], shared_source: str) -> dict[str, dict[str, str]]:
    cat: dict[str, dict[str, str]] = {}
    for text in datastore_sources:
        for kind, key in _DS.findall(text):
            cat[key] = {"store": "datastore", "type": _DS_TYPES[kind]}
    for kind, key in _SH.findall(shared_source):
        cat[key] = {"store": "shared", "type": _SH_TYPES[kind]}
    return dict(sorted(cat.items()))


def catalog() -> dict[str, dict[str, str]]:
    return json.loads(resources.files("homalawn").joinpath("prefs_catalog.json").read_text())


def value_text(v: Any) -> str:
    if isinstance(v, bool):
        return "true" if v else "false"
    return str(v)


def current(transport) -> dict[str, dict[str, dict]]:
    return transport.call("launcher.prefs_list")


def _find_current(cur: dict, key: str):
    for store in ("datastore", "shared"):
        if key in cur.get(store, {}):
            return store, cur[store][key]["type"]
    return None


def get_all(transport) -> dict[str, dict]:
    cur = current(transport)
    out = {k: {"store": c["store"], "type": c["type"], "value": None} for k, c in catalog().items()}
    for store in ("datastore", "shared"):
        for k, e in cur.get(store, {}).items():
            out[k] = {"store": store, "type": e["type"], "value": e["value"]}
    return dict(sorted(out.items()))


def _check_value(type_: str, value: str) -> None:
    try:
        if type_ == "bool":
            if value not in ("true", "false"):
                raise ValueError
        elif type_ == "int":
            int(value)
        elif type_ == "float":
            float(value)
    except ValueError:
        raise ValueError(f"{value!r} is not a valid {type_} value" + (" (use true or false)" if type_ == "bool" else ""))


# Lawnchair's gesture settings hold its kotlinx-serialized GestureHandlerConfig ({"type": ...});
# a value it cannot decode silently turns the gesture off. homarSearch is ours (patch 0015).
GESTURE_KEYS = frozenset({"double_tap_gesture_handler", "swipe_up_gesture_handler",
                          "swipe_down_gesture_handler", "home_press_gesture_handler",
                          "back_press_gesture_handler"})
GESTURE_TYPES = ("noOp", "sleep", "recents", "openNotificationdata", "openAppDrawer", "openAppSearch",
                 "homarSearch", "openSearch", "openAssistant", "openApp")


def _check_gesture(value: str) -> None:
    try:
        type_ = json.loads(value).get("type")
    except (ValueError, AttributeError):
        type_ = None
    if type_ not in GESTURE_TYPES:
        raise ValueError(f"{value!r} is not a gesture Homa Lawn knows; use JSON like "
                         f"'{{\"type\":\"homarSearch\"}}', type one of {', '.join(GESTURE_TYPES)}")


def resolve(transport, key: str, value: str, store: str | None = None, type_: str | None = None,
            cur: dict | None = None) -> tuple[str, str, str, str]:
    """Validate a write and return (store, key, type, value); nothing is sent."""
    cur = cur if cur is not None else current(transport)
    if is_internal(key):
        raise ValueError(f"{key} is Homa Lawn's internal state, not a setting; homalawn never changes it")
    found = _find_current(cur, key)
    if found and key not in catalog():
        raise ValueError(f"{key} is stored by Homa Lawn itself and is not a known setting; refusing to change it")
    if found:
        # the stored type wins: changing a DataStore key's type crashes Lawnchair's typed reader
        r_store, r_type = found
        if type_ and type_ != r_type:
            raise ValueError(f"{key} is stored as {r_type}; refusing to change its type to {type_}")
        if store and store != r_store:
            raise ValueError(f"{key} lives in the {r_store} store, not {store}")
    else:
        entry = catalog().get(key)
        if entry:
            r_store = store or entry["store"]
            r_type = type_ or entry["type"]
            if (store and store != entry["store"]) or (type_ and type_ != entry["type"]):
                raise ValueError(f"{key} is {entry['store']}/{entry['type']} in Homa Lawn; "
                                 f"refusing {store or entry['store']}/{type_ or entry['type']}")
        elif store and type_:
            r_store, r_type = store, type_
        else:
            raise ValueError(f"unknown preference {key!r}; pass --store (datastore|shared) and --type to set it anyway")
    if r_store not in ("datastore", "shared"):
        raise ValueError(f"bad store {r_store!r} (datastore or shared)")
    if r_type == "stringset":
        raise ValueError(f"{key} is a string set; sets are not supported")
    if r_type not in TYPES:
        raise ValueError(f"bad type {r_type!r} ({', '.join(TYPES)})")
    _check_value(r_type, value)
    if key in GESTURE_KEYS:
        _check_gesture(value)
    return r_store, key, r_type, value


def send(transport, w: tuple[str, str, str, str]) -> None:
    store, key, type_, value = w
    transport.call("launcher.pref_set", {"store": store, "key": key, "type": type_, "value": value})


def wire(w: tuple[str, str, str, str]) -> dict:
    """One resolved write (store, key, type, value) as prefs_set_many (and look_put) take it."""
    s, k, t, v = w
    return {"store": s, "key": k, "type": t, "value": v}


def send_many(transport, writes: list[tuple[str, str, str, str]],
              removals: list[tuple[str, str]] = ()) -> None:
    """One commit and one launcher reload for all keys; `removals` are (store, key) to unset."""
    transport.call("launcher.prefs_set_many", {"writes":
        [wire(w) for w in writes] + [{"store": s, "key": k, "remove": True} for s, k in removals]})


def set_(transport, key: str, value: str, store: str | None = None, type_: str | None = None,
         cur: dict | None = None) -> None:
    send(transport, resolve(transport, key, value, store, type_, cur))


def restore_plan(old: dict, cur: dict, keep: frozenset[str] = frozenset()
                 ) -> tuple[list[tuple[str, str, str, Any]], list[tuple[str, str]]]:
    """What puts the settings in `cur` back to `old`, touching only writable catalog keys.

    Returns (writes, removals): writes are (store, key, type, old_value) for keys whose value differs
    or that are missing now; removals are (store, key) for keys that are set now but were not set in
    `old` (Lawnchair then uses its default again). Keys in `keep` are never removed. Internal
    Launcher3 state (migration_src_*, version, launcher.*) and unknown keys are left alone."""
    allowed = writable_keys()
    writes, removals = [], []
    for store in ("datastore", "shared"):
        o, c = old.get(store, {}), cur.get(store, {})
        for k, e in o.items():
            if k not in allowed or e["type"] not in TYPES:
                continue
            now = c.get(k)
            if now is None or (now["type"] == e["type"] and now["value"] != e["value"]):
                writes.append((store, k, e["type"], e["value"]))
        for k, e in c.items():
            if k in allowed and k not in o and k not in keep and e["type"] in TYPES:
                removals.append((store, k))
    return writes, removals
