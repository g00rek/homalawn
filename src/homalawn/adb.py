"""adb: the raw `Device` (adb commands) and `AdbTransport`, the `Transport` over it."""
from __future__ import annotations

import json
import re
import shlex
import subprocess
import time
from typing import Any, Callable

from . import appid
from .transport import NotSupported, ProviderError, TransportError

TIMEOUT_S = 60
# `run-as` works only for a debuggable app: the test build and the old app, not the release build
NOT_DEBUGGABLE = ("Over adb, files move only to a debuggable Homa Lawn (the test build); {pkg} is reached "
                  "over the paired link for this: pair once with `homalawn pair HOST`, or use `--via link`.")

Runner = Callable[[list[str], bytes | None], tuple[int, bytes, bytes]]


IO_DIR = "files/omalawn"  # Lawnchair's private exchange dir (0700); files inside are 0600; kept: wire compatibility
_PRIVATE_NAME = re.compile(r"[a-z0-9._-]{1,64}")
# The provider's positional `arg` carries this param of each command (OmalawnControlProvider.params).
ARG_PARAM = {"prefs_set_many": "writes", "wallpaper_set": "path", "widget_status": "id", "layout_import": "path",
             "nova_convert": "path"}

__all__ = ["AdbError", "AdbTransport", "Device", "NotSupported", "ProviderError", "TransportError"]


class AdbError(TransportError):
    pass


def subprocess_runner(argv: list[str], stdin: bytes | None = None) -> tuple[int, bytes, bytes]:
    try:
        p = subprocess.run(argv, input=stdin, capture_output=True, timeout=TIMEOUT_S)
    except FileNotFoundError:
        raise AdbError("adb is not installed or not on PATH (install Android platform-tools)")
    except subprocess.TimeoutExpired:
        raise AdbError(f"adb did not answer within {TIMEOUT_S}s (phone unreachable?): {' '.join(argv[3:])[:120]}")
    return p.returncode, p.stdout, p.stderr


def _parse_bundle(text: str) -> Any:
    """Parse `Result: Bundle[{k=v, ...}]`; key order is not guaranteed and the
    result JSON may itself contain ', ' or 'ok=' so it is decoded in place."""
    start = text.find("Bundle[{")
    if start < 0:
        raise AdbError(f"unexpected provider output: {text.strip()[:200]}")
    body = text[start + len("Bundle[{"):].rstrip()
    if body.endswith("}]"):
        body = body[:-2]
    dec = json.JSONDecoder()
    result: Any = None
    rest = body
    for m in re.finditer(r"(?:^|, )result=", body):
        try:
            result, end = dec.raw_decode(body, m.end())
        except ValueError:
            continue
        rest = body[: m.start()] + body[end:]
        break
    else:
        m = re.search(r"(?:^|, )error=(.*?)(?:, ok=(?:true|false))?$", body, re.S)
        if m:
            raise ProviderError(m.group(1))
        raise AdbError(f"unparseable provider output: {body[:200]}")
    if re.search(r"(?:^|, )ok=false", rest):
        raise ProviderError(str(result))
    return result


def pick_package(installed: set[str], home: str | None) -> str | None:
    """Which Homa Lawn app to talk to among the ones installed: the default home app when it is
    one of them, else the first in appid.PACKAGES (the release before the test build before the
    old app). None when none is installed."""
    have = [p for p in appid.PACKAGES if p in installed]
    if home in have:
        return home
    return have[0] if have else None


class Device:
    def __init__(self, serial: str, runner: Runner = subprocess_runner, package: str | None = None):
        """`package`: the Homa Lawn app on the phone; by default HOMALAWN_PACKAGE, else the
        config's `package`, else found on the phone (`pick_package`) when first needed."""
        self.serial = serial
        self._run = runner
        self._package = package

    @property
    def package(self) -> str:
        if self._package is None:
            from . import config
            self._package = config.load().package or self.detect_package() or appid.RELEASE
        return self._package

    @package.setter
    def package(self, pkg: str) -> None:
        self._package = pkg

    def installed_packages(self) -> set[str]:
        """Which Homa Lawn apps (appid.PACKAGES) are installed."""
        try:
            out = self.shell("pm", "list", "packages")
        except AdbError:
            return set()
        return {ln[len("package:"):].strip() for ln in out.splitlines() if ln.startswith("package:")} \
            & set(appid.PACKAGES)

    def home_holder(self) -> str | None:
        try:
            out = self.shell("cmd", "role", "get-role-holders", "android.app.role.HOME").strip()
        except AdbError:
            return None
        return out.split(";")[0].strip() or None

    def detect_package(self) -> str | None:
        installed = self.installed_packages()
        if len(installed) <= 1:
            return next(iter(installed), None)
        return pick_package(installed, self.home_holder())

    def _adb(self, *args: str, stdin: bytes | None = None) -> bytes:
        code, out, err = self._run(["adb", "-s", self.serial, *args], stdin)
        if code != 0:
            msg = (err or out).decode(errors="replace").strip()
            raise AdbError(msg or f"adb exited with {code}")
        return out

    def shell(self, *args: str) -> str:
        return self._adb("shell", " ".join(shlex.quote(a) for a in args)).decode(errors="replace")

    def _run_as_failed(self, e: AdbError) -> TransportError:
        if "not debuggable" in str(e):
            return NotSupported(NOT_DEBUGGABLE.format(pkg=self.package))
        return e

    def run_as_read(self, path: str) -> bytes:
        try:
            out = self._adb("exec-out", f"run-as {self.package} cat {shlex.quote(path)}")
        except AdbError as e:
            raise self._run_as_failed(e) from e
        if out.startswith(b"run-as: package not debuggable"):  # exec-out mixes stderr into the bytes
            raise NotSupported(NOT_DEBUGGABLE.format(pkg=self.package))
        return out

    def run_as(self, *args: str) -> str:
        """A shell command as the app (`run-as PKG ...`); NotSupported for the release build."""
        try:
            return self.shell("run-as", self.package, *args)
        except AdbError as e:
            raise self._run_as_failed(e) from e

    def run_as_write(self, path: str, data: bytes) -> None:
        """Atomic: stream into `<path>.omalawn-tmp`, then `mv` over the target in the same
        run-as command, so a dropped link never leaves a truncated file at `path`. The file is
        created 0600 (umask 077), so it is never readable by anyone else, not even briefly."""
        tmp = path + ".omalawn-tmp"
        parent = path.rpartition("/")[0]
        mkdir = f"mkdir -p {shlex.quote(parent)} && " if parent else ""
        script = f"umask 077; {mkdir}cat > {shlex.quote(tmp)} && mv {shlex.quote(tmp)} {shlex.quote(path)}"
        try:
            self._adb("shell", "-T", f"run-as {self.package} sh -c {shlex.quote(script)}", stdin=data)
        except AdbError as e:
            raise self._run_as_failed(e) from e

    def is_running(self) -> bool:
        try:
            return bool(self.shell("pidof", self.package).strip())
        except AdbError:  # pidof exits 1 when there is no such process
            return False

    def call(self, method: str, arg: str | None = None, extras: dict[str, str] | None = None) -> Any:
        cmd = f"content call --uri content://{appid.authority(self.package)} --method {shlex.quote(method)}"
        if arg is not None:
            cmd += f" --arg {shlex.quote(arg)}"
        for k, v in (extras or {}).items():
            cmd += f" --extra {shlex.quote(f'{k}:s:{v}')}"
        out = self._adb("shell", cmd).decode(errors="replace")
        return _parse_bundle(out)

    def force_stop(self) -> None:
        self._adb("shell", f"am force-stop {self.package}")

    def start_home(self) -> None:
        """Start Lawnchair by explicit component: an implicit HOME intent opens the chooser (or
        another launcher) when Lawnchair has lost its default-home status."""
        self._adb("shell", f"am start -n {appid.launcher(self.package)} -a android.intent.action.MAIN -c android.intent.category.HOME")

    def set_home(self) -> None:
        """Make Lawnchair the default home app again."""
        try:
            out = self._adb("shell", f"cmd package set-home-activity {appid.launcher(self.package)}").decode(errors="replace")
        except AdbError:  # some builds exit non-zero here; the role command still works
            out = ""
        if "Success" in out:
            return
        self._adb("shell", f"cmd role add-role-holder android.app.role.HOME {self.package}")

    def screencap(self) -> bytes:
        return self._adb("exec-out", "screencap -p")

    def push(self, local: str, remote: str) -> None:
        self._adb("push", local, remote)


def _param_text(v: Any) -> str:
    if isinstance(v, str):
        return v
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, (list, dict)):
        return json.dumps(v)
    return str(v)


class AdbTransport:
    """`Transport` over adb: `launcher.*` commands go through the shell-only ContentProvider
    (`content call`), files move through `run-as`, and the shell is available."""
    name = "adb"
    has_shell = True

    def __init__(self, serial: str | None = None, runner: Runner = subprocess_runner, *, device=None,
                 package: str | None = None):
        self.device = device if device is not None else Device(serial, runner, package)
        self.serial = serial if serial is not None else getattr(self.device, "serial", "?")
        self.label = self.serial
        self._home: str | None = None

    # -- commands -----------------------------------------------------------
    def call(self, method: str, params: dict | None = None) -> Any:
        ns, _, bare = method.partition(".")
        if method == "screen.status":  # the one screen command the shell-only provider offers
            return self.device.call("screen_status")
        if ns != "launcher" or not bare:
            raise NotSupported(f"{method} works only over the paired link, not over adb; pair once with "
                               "`homalawn pair HOST` and homalawn uses the link by itself (or force it with `--via link`)")
        params = dict(params or {})
        arg_key = ARG_PARAM.get(bare)
        arg = _param_text(params.pop(arg_key)) if arg_key and params.get(arg_key) is not None else None
        extras = {k: _param_text(v) for k, v in params.items() if v is not None} or None
        return self.device.call(bare, arg, extras)

    # -- files --------------------------------------------------------------
    def app_home(self) -> str:
        """Lawnchair's data dir on the phone (the provider takes absolute paths)."""
        if self._home is None:
            self._home = self.device.run_as("pwd").strip()
        return self._home

    def _rm(self, *paths: str) -> None:
        try:
            self.device.run_as("rm", "-f", *paths)
        except TransportError:
            pass

    def _take(self, path: str) -> bytes:
        """Read a file a provider method left in files/omalawn/, and delete it."""
        try:
            return self.device.run_as_read(path)
        finally:
            self._rm(path)

    def export_layout(self) -> tuple[str, bytes]:
        """(db name, bytes): the consistent single-file copy Lawnchair makes on its model thread."""
        r = self.call("launcher.layout_export")
        data = self._take(r["path"])
        if not data.startswith(b"SQLite format 3\x00"):
            raise AdbError("the launcher's layout export came back damaged")
        return r["db"], data

    def import_layout(self, data: bytes, db: str | None = None) -> dict:
        """Stage the DB under files/omalawn/ (slow part; Lawnchair untouched), then have Lawnchair
        replace its rows with it in one transaction and reload. The phone deletes the staged file."""
        name = f"{IO_DIR}/import-{time.time_ns()}.db"
        try:
            self.device.run_as_write(name, data)
            return self.call("launcher.layout_import", {"path": f"{self.app_home()}/{name}", "db": db})
        except BaseException:
            self._rm(name, name + ".omalawn-tmp")
            raise

    def nova_convert(self, backup: bytes, params: dict) -> tuple[dict, bytes]:
        """Stage the backup under files/omalawn/, have Lawnchair convert it (it deletes the backup),
        take the converted DB it left there."""
        name = f"{IO_DIR}/import-nova-{time.time_ns()}.zip"
        try:
            self.device.run_as_write(name, backup)
            r = self.call("launcher.nova_convert", {"path": f"{self.app_home()}/{name}", **params})
        except BaseException:
            self._rm(name, name + ".omalawn-tmp")
            raise
        data = self._take(r.pop("path"))
        if not data.startswith(b"SQLite format 3\x00"):
            raise AdbError("the converted layout came back damaged")
        return r, data

    def write_private(self, name: str, data: bytes) -> str:
        if not _PRIVATE_NAME.fullmatch(name):
            raise ValueError(f"private file name {name!r} must match [a-z0-9._-]{{1,64}}")
        home = self.app_home()
        self.device.run_as_write(f"{IO_DIR}/{name}", data)
        return f"{home}/{IO_DIR}/{name}"

    def wallpaper_backup(self, which: str = "home") -> bytes:
        params = {"which": which} if which != "home" else None  # an older phone knows home only
        return self._take(self.call("launcher.wallpaper_backup", params)["path"])

    def screenshot(self) -> bytes:
        return self.device.screencap()

    # -- shell-only ---------------------------------------------------------
    def shell(self, *args: str) -> str:
        return self.device.shell(*args)

    def is_running(self) -> bool:
        return self.device.is_running()

    def start_home(self) -> None:
        self.device.start_home()

    def set_home(self) -> None:
        self.device.set_home()

    def media_access(self) -> tuple[bool, str]:
        """(READ_EXTERNAL_STORAGE granted?, READ_MEDIA_IMAGES appop mode) as they are now."""
        pkg = self.device.package
        dump = self.shell("dumpsys", "package", pkg)
        m = re.search(r"android\.permission\.READ_EXTERNAL_STORAGE: granted=(true|false)", dump)
        ops = self.shell("appops", "get", "--uid", pkg, "READ_MEDIA_IMAGES")
        o = re.search(r"READ_MEDIA_IMAGES: (\w+)", ops)
        return bool(m and m.group(1) == "true"), (o.group(1) if o else "default")

    def allow_media_read(self, had_perm: bool, mode: str) -> None:
        """Grant what is missing from (had_perm, mode) for reading the system wallpaper."""
        pkg = self.device.package
        if not had_perm:
            self.shell("pm", "grant", pkg, "android.permission.READ_EXTERNAL_STORAGE")
        if mode != "allow":
            self.shell("appops", "set", "--uid", pkg, "READ_MEDIA_IMAGES", "allow")

    def revert_media_read(self, had_perm: bool, mode: str) -> None:
        """Put back what `allow_media_read(had_perm, mode)` changed. Revoking kills Lawnchair."""
        pkg = self.device.package
        if mode != "allow":
            self.shell("appops", "set", "--uid", pkg, "READ_MEDIA_IMAGES", mode)
        if not had_perm:
            self.shell("pm", "revoke", pkg, "android.permission.READ_EXTERNAL_STORAGE")
