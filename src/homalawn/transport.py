"""How the desktop reaches Lawnchair: one interface, adb today, the paired link next.

`Launcher`, `widgets`, `prefs`, `apps` and the CLI talk to a `Transport` only. Anything that needs
a device shell (adb) is behind `has_shell`; without it they say, in one line, what the user can do
instead.
"""
from __future__ import annotations

import re
from typing import Any, Protocol, cast, runtime_checkable


class TransportError(Exception):
    """The phone could not be reached, or answered something unusable."""


class ProviderError(TransportError):
    """Lawnchair answered with an error. `code` is the stable machine code (`busy`, `bad_path`,
    `grid_mismatch`, ...) when the phone sent one, else None. `data`: the phone's whole error
    object (code and message included; e.g. `expected` on `bad_offset`), empty when there is none."""

    def __init__(self, message: str, code: str | None = None, data: dict | None = None):
        super().__init__(message)
        self.data = dict(data or {})
        if code is None:
            m = re.match(r"([a-z][a-z_]*): ", message)
            code = m.group(1) if m else None
        self.code = code


class NotSupported(TransportError):
    """This connection cannot do that; the message is one line naming what the user can do."""


@runtime_checkable
class Transport(Protocol):
    name: str                     # "adb" | "link"
    label: str                    # which phone, for messages (adb serial, or the paired phone's name)
    has_shell: bool               # adb only: pm/appops/set-home/screencap via shell

    def call(self, method: str, params: dict | None = None) -> Any: ...      # namespaced methods, e.g. "launcher.ping"
    def export_layout(self) -> tuple[str, bytes]: ...                        # (db name, DB bytes) via launcher.layout_export
    def import_layout(self, data: bytes, db: str | None = None) -> dict: ... # via launcher.layout_import; raises ProviderError codes
    def write_private(self, name: str, data: bytes) -> str: ...              # stage a file in files/omalawn/, return abs path (wallpaper)
    def wallpaper_backup(self, which: str = "home") -> bytes: ...            # the current home or lock wallpaper, via launcher.wallpaper_backup
    def nova_convert(self, backup: bytes, params: dict) -> tuple[dict, bytes]: ...  # (info, converted DB) via launcher.nova_convert
    def screenshot(self) -> bytes: ...                                       # PNG
    def shell(self, *args: str) -> str: ...                                  # raises NotSupported when not has_shell


def need_shell(t: Transport, what: str, fix: str) -> None:
    """Raise a one-line NotSupported unless `t` has a device shell."""
    if not t.has_shell:
        raise NotSupported(f"{what} needs an adb connection, which this {t.name} connection is not; {fix}")


class ShellTransport(Transport, Protocol):
    """What a transport with `has_shell` also offers. Callers use these only behind `has_shell`."""

    def start_home(self) -> None: ...                                        # start Lawnchair by component
    def set_home(self) -> None: ...                                          # make Lawnchair the default home again
    def is_running(self) -> bool: ...
    def media_access(self) -> tuple[bool, str]: ...                          # (READ_EXTERNAL_STORAGE granted, appop mode)
    def allow_media_read(self, had_perm: bool, mode: str) -> None: ...
    def revert_media_read(self, had_perm: bool, mode: str) -> None: ...


def shell_of(t: Transport, what: str = "That", fix: str = "use `--via adb`") -> ShellTransport:
    """`t` as a ShellTransport, or a one-line NotSupported when it has no device shell. The only
    way the rest of homalawn reaches the shell-only helpers."""
    need_shell(t, what, fix)
    return cast(ShellTransport, t)
