"""Install Homa Lawn on the Omarchy desktop: the background service as a systemd user unit and the
shell plugin copied into ~/.config/omarchy/plugins/homalawn/. The copy follows Flux's update rules:
write only changed files, remember what was written, drop what a newer version no longer has,
keep the user's own files, never touch a symlinked plugin folder."""
from __future__ import annotations

import hashlib
import shutil
import subprocess
import time
from importlib import resources
from pathlib import Path

UNIT = "homalawnd.service"
TRACK = ".homalawn-files"
VERSION_FILE = ".homalawnd.version"  # next to the unit: the Homa Lawn the installer last (re)started the service with
PLUGIN_ID = "homalawn"

# What a Homar-era install (2026-10-13 to 2026-10-15) left: removed by the next install.
OLD_UNIT = "homard.service"
OLD_TRACK = ".homar-files"
OLD_VERSION_FILE = ".homard.version"
OLD_PLUGIN_ID = "homar"


class DesktopError(Exception):
    pass


# What `desktop install` runs, each with the one sentence it stops with when the command is missing
# (checked before anything is written).
NOT_INSTALLED = " Nothing was installed; the homalawn command and its tool server for coding agents work without it."
NEEDS = (
    ("systemctl", "`homalawn desktop install` runs Homa Lawn's service under systemd, and this computer has no "
                  "`systemctl`." + NOT_INSTALLED),
    ("omarchy", "`homalawn desktop install` is for Omarchy, and the `omarchy` command is not on this computer."
                + NOT_INSTALLED),
    ("omarchy-shell", "`homalawn desktop install` adds Homa Lawn to Omarchy's bar, and `omarchy-shell` is not on "
                      "this computer." + NOT_INSTALLED),
)


def preflight(which=None) -> None:
    """DesktopError naming the first command `desktop install` needs that is not here."""
    which = which or shutil.which
    for cmd, why in NEEDS:
        if not which(cmd):
            raise DesktopError(why)


class _Out(list):
    """The install's lines, each also said the moment it is known (`say`), not all at the end."""

    def __init__(self, say=None):
        super().__init__()
        self._say = say

    def append(self, line: str) -> None:
        super().append(line)
        if self._say is not None:
            self._say(line)


def default_sources() -> tuple[Path, Path]:
    root = resources.files("homalawn")
    return Path(str(root / "shell")), Path(str(root / "data" / UNIT))


def default_exe(home: Path | None = None) -> str | None:
    """The running `homalawn` executable when it lives under ~/.local/bin, else None (unit default)."""
    found = shutil.which("homalawn")
    if not found:
        return None
    base = ((home or Path.home()) / ".local" / "bin")
    p = Path(found)
    return str(p) if p.parent == base else None


def installed_version() -> str:
    """This Homa Lawn: the package version plus a digest of its code, so an upgrade (or, in an
    editable install, a code change under one version number) is told apart."""
    from importlib.metadata import PackageNotFoundError, version
    try:
        v = version("homalawn")
    except PackageNotFoundError:
        v = "unknown"
    root = Path(str(resources.files("homalawn")))
    h = hashlib.sha256()
    for p in sorted(root.rglob("*.py")):
        if "__pycache__" not in p.parts:
            h.update(p.relative_to(root).as_posix().encode() + b"\0" + p.read_bytes())
    return f"{v}+{h.hexdigest()[:12]}"


def _version_path(home: Path) -> Path:
    return _unit_path(home).parent / VERSION_FILE


def _unit_path(home: Path) -> Path:
    return home / ".config" / "systemd" / "user" / UNIT


def _plugin_dir(home: Path, plugin_id: str = PLUGIN_ID) -> Path:
    return home / ".config" / "omarchy" / "plugins" / plugin_id


# Development-only parts of the plugin source: the offscreen test harness and its notes.
DEV_ONLY = ("tools", "README.md")


def _files(root: Path) -> dict[str, Path]:
    out = {}
    for p in sorted(root.rglob("*")):
        rel = p.relative_to(root)
        if p.is_file() and "__pycache__" not in rel.parts and rel.parts[0] not in DEV_ONLY:
            out[rel.as_posix()] = p
    return out


def _check_plain(pdir: Path) -> None:
    if pdir.is_symlink() or any(p.is_symlink() for p in pdir.rglob("*") if pdir.exists()):
        raise DesktopError(f"{pdir} is a link or holds a link (a checkout of the plugin?). "
                           "Homa Lawn did not change it.")


def _safe(pdir: Path, rel: str) -> bool:
    if not rel or rel.startswith("/") or ".." in Path(rel).parts:
        return False
    try:
        (pdir / rel).resolve().relative_to(pdir.resolve())
    except ValueError:
        return False
    return True


def _tracked(pdir: Path, track: str = TRACK) -> tuple[list[str], list[str]]:
    """(entries safe to delete, entries ignored because they point outside the plugin folder)."""
    try:
        lines = [l for l in (pdir / track).read_text().splitlines() if l.strip()]
    except OSError:
        return [], []
    return ([l for l in lines if _safe(pdir, l)], [l for l in lines if not _safe(pdir, l)])


def _try(run, cmd) -> tuple[bool, str]:
    """Run a command quietly: (it succeeded, what it said on stderr). A missing command fails."""
    try:
        r = run(cmd, capture_output=True, text=True, check=False)
    except FileNotFoundError:
        return False, f"{cmd[0]} is not installed"
    rc = getattr(r, "returncode", 0)
    return rc == 0, (getattr(r, "stderr", "") or "").strip()


SETTLE_S = 2.0   # a service that cannot start has failed for good by then (Restart= gives up after 5 tries)
NOT_RUNNING = ("The Homa Lawn service did not stay running; `journalctl --user -u homalawnd` says why. Run the "
               "install again once that is fixed.")


def _running(run, sleep) -> bool:
    """Is the service still active a moment after it was started (not failing and restarting)?"""
    sleep(SETTLE_S)
    return _try(run, ["systemctl", "--user", "is-active", "homalawnd"])[0]


def _write_if_changed(dest: Path, data: bytes) -> bool:
    if dest.is_file() and dest.read_bytes() == data:
        return False
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_bytes(data)
    return True


def _prune(pdir: Path, rel: str) -> None:
    f = pdir / rel
    f.unlink(missing_ok=True)
    d = f.parent
    while d != pdir and pdir in d.parents and d.is_dir() and not any(d.iterdir()):
        d.rmdir()
        d = d.parent


def _remove_homar(home: Path, run, out: list[str]) -> bool:
    """Take out what a Homar-era install left: stop, disable and delete homard.service, and
    disable the `homar` plugin and delete the files that install copied (files the user added are
    kept). True when the old plugin was there, so the shell has it loaded and must restart."""
    unit = _unit_path(home).parent / OLD_UNIT
    if unit.exists():
        ok, err = _try(run, ["systemctl", "--user", "disable", "--now", OLD_UNIT.removesuffix(".service")])
        unit.unlink()
        (unit.parent / OLD_VERSION_FILE).unlink(missing_ok=True)
        _try(run, ["systemctl", "--user", "daemon-reload"])
        out.append("Stopped and removed the old Homar service (homard); Homar is now Homa Lawn." if ok else
                   f"Removed the old Homar service file (homard), but stopping it said: {err or 'no details'}.")
    old = _plugin_dir(home, OLD_PLUGIN_ID)
    if not (old / OLD_TRACK).is_file():
        return False  # not a folder an install made (absent, or the user's own): left alone
    if old.is_symlink() or any(p.is_symlink() for p in old.rglob("*")):
        out.append(f"Left the old Homar plugin at {old}: it is a link or holds one. "
                   f"Run `omarchy plugin disable {OLD_PLUGIN_ID}` and remove it yourself.")
        return False
    ok, err = _try(run, ["omarchy", "plugin", "disable", OLD_PLUGIN_ID])
    for rel in _tracked(old, OLD_TRACK)[0]:
        _prune(old, rel)
    (old / OLD_TRACK).unlink(missing_ok=True)
    kept = any(old.iterdir())
    if not kept:
        old.rmdir()
    out.append("Removed the old Homar plugin" + (f" (files you added were kept in {old})" if kept else "") + "." +
               ("" if ok else f" Disabling it said: {err or 'no details'}."))
    return True


def install(home: Path, run=subprocess.run, plugin_src: Path | None = None,
            unit_src: Path | None = None, exe: str | None = None, version: str | None = None,
            sleep=time.sleep, clock=time.monotonic, which=None, say=None) -> list[str]:
    """Install or update the service and the bar plugin; each line is passed to `say` as it
    happens and returned. DesktopError, before anything is written, when a command it needs is
    missing, the plugin folder is a link, or the program the service would run is not there."""
    preflight(which)
    version = version or installed_version()
    if plugin_src is None or unit_src is None:
        dp, du = default_sources()
        plugin_src, unit_src = plugin_src or dp, unit_src or du
    pdir = _plugin_dir(home)
    _check_plain(pdir)
    if exe is None and not (home / ".local" / "bin" / "homalawn").exists():
        raise DesktopError("The service runs ~/.local/bin/homalawn, which is not there: install Homa Lawn with "
                           "`uv tool install` first (see the README), then run this again. Nothing was installed.")
    out = _Out(say)
    migrated = _remove_homar(home, run, out)

    unit = unit_src.read_text()
    if exe:
        unit = "\n".join(f"ExecStart={exe} serve" if l.startswith("ExecStart=") else l
                         for l in unit.split("\n"))
    changed = _write_if_changed(_unit_path(home), unit.encode())
    if changed:
        out.append(f"Wrote the Homa Lawn service ({_unit_path(home)}).")
        ok, err = _try(run, ["systemctl", "--user", "daemon-reload"])
        if not ok:
            out.append(f"Could not reload systemd: {err or 'no details'}")
    else:
        out.append("The Homa Lawn service file is already in place.")
    enabled = _try(run, ["systemctl", "--user", "is-enabled", "homalawnd"])[0]
    # an install before 2026-10-22 hung the service on default.target, which starts it before the
    # desktop session has handed its environment to systemd; `enable` only adds links, re-enabling
    # moves it to the target the unit names now (a link still there means that has not happened yet)
    stale = (_unit_path(home).parent / "default.target.wants" / UNIT).is_symlink()
    if enabled and (changed or stale):
        ok, err = _try(run, ["systemctl", "--user", "reenable", "homalawnd"])
        out.append("Re-enabled the Homa Lawn service: it starts with the desktop session." if ok else
                   f"Could not re-enable the Homa Lawn service: {err or 'no details'}. Run the install again to retry.")
    live = enabled and _try(run, ["systemctl", "--user", "is-active", "homalawnd"])[0]
    vpath = _version_path(home)
    try:
        running = vpath.read_text().strip()
    except OSError:
        running = None
    if not live:
        ok, err = _try(run, ["systemctl", "--user", "enable", "--now", "homalawnd"])
        if ok:
            ok = _running(run, sleep)
            out.append("Started the Homa Lawn service; it starts with the desktop session from now on." if ok
                       else NOT_RUNNING)
        else:
            out.append(f"Could not start the Homa Lawn service: {err or 'no details'}. Run the install again to retry.")
    elif changed or running != version:
        # the running service still has the old code (or the old unit) until it restarts
        ok, err = _try(run, ["systemctl", "--user", "try-restart", "homalawnd"])
        if ok:
            ok = _running(run, sleep)
            out.append(f"Restarted the Homa Lawn service ({version.split('+')[0]})." if ok else NOT_RUNNING)
        else:
            out.append(f"Could not restart the Homa Lawn service: {err or 'no details'}. Run the install again to retry.")
    else:
        ok = False
    if ok:
        _write_if_changed(vpath, (version + "\n").encode())

    src = _files(plugin_src)
    # an earlier install's copy is there (the shell may have it loaded), or the old Homar plugin was
    upgrade = (pdir / TRACK).is_file() or migrated
    pdir.mkdir(parents=True, exist_ok=True)
    wrote = []
    for rel, p in src.items():
        if _write_if_changed(pdir / rel, p.read_bytes()):
            wrote.append(rel)
    old, skipped = _tracked(pdir)
    if skipped:
        out.append(f"Ignored {len(skipped)} entry(ies) in {TRACK} that point outside the plugin folder.")
    gone = [r for r in old if r not in src]
    for rel in gone:
        _prune(pdir, rel)
    _write_if_changed(pdir / TRACK, ("\n".join(sorted(src)) + "\n").encode())
    if wrote:
        out.append(f"Copied {len(wrote)} file(s) of the Homa Lawn plugin for Omarchy's bar to {pdir}.")
    if gone:
        out.append(f"Removed {len(gone)} plugin file(s) the new version no longer has.")
    if not wrote and not gone:
        out.append("The Homa Lawn plugin for Omarchy's bar is already up to date.")

    restarted = False
    if (wrote or gone) and upgrade:
        # a running shell keeps the QML it already loaded (rescan and disable/enable do not reload
        # it); only a restart of the shell loads the new files
        ok, err = _try(run, ["omarchy", "restart", "shell"])
        restarted = ok
        out.append("Restarted the Omarchy shell to load the new Homa Lawn plugin." if ok else
                   f"Could not restart the Omarchy shell: {err or 'no details'}. Run `omarchy restart shell` "
                   "to load the new Homa Lawn plugin.")
    elif wrote or gone:
        _try(run, ["omarchy-shell", "shell", "rescanPlugins"])
    # a shell that was just restarted does not answer for a few seconds: wait for it to list the
    # plugin before deciding it is missing (it usually comes back with Homa Lawn still enabled)
    # (after the Homar plugin, Homa Lawn's is new to the shell: waiting until it is listed is enough)
    enabled = (_wait_enabled(run, RESTART_WAIT_S, sleep, clock, until_listed=migrated) if restarted
               else _enabled(run))
    if not enabled:
        r = run(["omarchy", "plugin", "enable", PLUGIN_ID, "--section", "right"],
                capture_output=True, text=True, check=False)
        said = " ".join(((getattr(r, "stdout", "") or "") + "\n" + (getattr(r, "stderr", "") or "")).split())
        timed_out = NOT_RESPONDING in said.lower()
        ok = getattr(r, "returncode", 0) == 0 and not timed_out
        if timed_out:
            # the shell did not answer in time, but the change is written to shell.json: the list says
            ok = _enabled(run)
        out.append("Added the Homa Lawn icon to the right side of the bar." if ok else
                   f"Could not add the Homa Lawn icon to the bar: {said or 'no details'}. Run the install again to retry.")
    return out


NOT_RESPONDING = "not responding"   # `omarchy plugin enable` when the shell's answer timed out
RESTART_WAIT_S = 10                 # how long a restarted shell gets to list its plugins again


def _wait_enabled(run, seconds: float, sleep, clock, step: float = 0.5, until_listed: bool = False) -> bool:
    """Ask `omarchy plugin list` until it says Homa Lawn is enabled (or, with `until_listed`, lists
    it at all), for up to `seconds`; True when it is enabled."""
    end = clock() + seconds
    while True:
        state = _state(run)
        if state == "enabled" or (until_listed and state is not None):
            return state == "enabled"
        if clock() >= end:
            return False
        sleep(step)


def _state(run) -> str | None:
    """Homa Lawn's state in `omarchy plugin list` ("enabled", "disabled", ...), None when unlisted."""
    r = run(["omarchy", "plugin", "list"], capture_output=True, text=True, check=False)
    for line in (getattr(r, "stdout", "") or "").splitlines():
        parts = line.split()
        if len(parts) >= 2 and parts[0] == PLUGIN_ID:
            return parts[1]
    return None


def _enabled(run) -> bool:
    return _state(run) == "enabled"


def uninstall(home: Path, run=subprocess.run, say=None) -> list[str]:
    """Stop and remove the service and the bar plugin (files the user added to the plugin folder
    stay); each line is passed to `say` as it happens and returned. The commands' own output is
    not shown; a failure is said in one line."""
    pdir = _plugin_dir(home)
    _check_plain(pdir)
    out = _Out(say)
    unit = _unit_path(home)
    if unit.exists():
        ok, err = _try(run, ["systemctl", "--user", "disable", "--now", "homalawnd"])
        _version_path(home).unlink(missing_ok=True)
        unit.unlink()
        out.append("Stopped and removed the Homa Lawn service." if ok else
                   f"Removed the Homa Lawn service file; stopping the service said: {err or 'no details'}.")
        _try(run, ["systemctl", "--user", "daemon-reload"])
    else:
        _version_path(home).unlink(missing_ok=True)
    if pdir.is_dir():
        ok, err = _try(run, ["omarchy", "plugin", "disable", PLUGIN_ID])
        for rel in _tracked(pdir)[0]:
            _prune(pdir, rel)
        (pdir / TRACK).unlink(missing_ok=True)
        kept = any(pdir.iterdir())
        if not kept:
            pdir.rmdir()
        out.append("Removed the Homa Lawn plugin from Omarchy's bar" + (f" (files you added were kept in {pdir})"
                                                                         if kept else "") + "." +
                   ("" if ok else f" Taking it off the bar said: {err or 'no details'}."))
        _try(run, ["omarchy-shell", "shell", "rescanPlugins"])
    if not out:
        out.append("The Homa Lawn service and bar plugin were not installed.")
    return out


def kept_lines(home: Path, cfg: Path | None = None, data: Path | None = None, state: Path | None = None,
               cache: Path | None = None) -> list[str]:
    """What `desktop uninstall` leaves, and how to remove it: said after it. The folders default to
    where they are without HOMALAWN_* or XDG_* variables; the CLI passes the real ones."""
    def tilde(p: Path) -> str:
        try:
            return "~/" + p.relative_to(home).as_posix()
        except ValueError:
            return str(p)
    cfg = cfg or home / ".config" / "homalawn"
    data = data or home / ".local" / "share" / "homalawn"
    state = state or home / ".local" / "state" / "homalawn"
    cache = cache or home / ".cache" / "homalawn"
    return [
        f"Kept: the pairings and this computer's key in {tilde(cfg)}, the History (snapshots) in {tilde(data)}, "
        f"and small working files in {tilde(state)} and {tilde(cache)}.",
        "Each paired phone still trusts this computer: forget it on the phone (Homa Lawn's settings › Computers), "
        "or run `homalawn unpair NAME` for each phone before deleting the folders.",
        f"To remove everything: rm -r {tilde(cfg)} {tilde(data)} {tilde(state)} {tilde(cache)}, and "
        "`uv tool uninstall homalawn` removes the homalawn command.",
    ]
