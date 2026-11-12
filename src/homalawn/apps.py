"""Launchable apps on the phone."""
from __future__ import annotations

import re

from . import appid
from .transport import Transport

_LINE = re.compile(r"^\s+([A-Za-z0-9_.]+)/(\S+)\s*$")
# Lawnchair's home entry: also a launcher activity (so other launchers can start it) but hidden from
# its own drawer, so nothing to place. Its "Homa Lawn" settings icon (patch 0018) is listed.
_HOME_ENTRY = appid.HOME_ACTIVITY  # the same class in every Homa Lawn app (appid.PACKAGES)


def list_apps(transport: Transport) -> list[dict]:
    """Launcher activities as {label, package, activity}. Over the link Lawnchair lists them itself
    (`launcher.apps_list`, with real labels); over adb `query-activities --brief` gives one
    `package/activity` line per match and the label is the package name. Either way the launcher's
    own home entry is left out, as in the drawer."""
    if not transport.has_shell:
        return [{"label": a.get("label") or a["package"], "package": a["package"], "activity": a["activity"]}
                for a in transport.call("launcher.apps_list") if a["activity"] != _HOME_ENTRY]
    out = transport.shell("cmd", "package", "query-activities", "--brief", "-a", "android.intent.action.MAIN",
                          "-c", "android.intent.category.LAUNCHER")
    apps, seen = [], set()
    for line in out.splitlines():
        m = _LINE.match(line)
        if not m or (m.group(1), m.group(2)) in seen or m.group(2) == _HOME_ENTRY:
            continue
        seen.add((m.group(1), m.group(2)))
        apps.append({"label": m.group(1), "package": m.group(1), "activity": m.group(2)})
    return apps
