"""adb pairing by QR code, the way Android Studio does it (spec N2).

The terminal shows a QR encoding `WIFI:T:ADB;S:<name>;P:<password>;;`. The phone (Wireless
debugging › Pair device with QR code) scans it and advertises `_adb-tls-pairing._tcp` with
instance `<name>`; we run `adb pair <host>:<port> <password>`, then wait for the phone in
`adb devices` (adb auto-connects its `_adb-tls-connect._tcp` service by default) and run
`adb connect` ourselves if it does not.

`adb mdns services` format (platform-tools 37.0.1, read 2026-10-13): a header line
`List of discovered mdns services`, then one `instance<TAB>service<TAB>host:port` per service.
"""
from __future__ import annotations

import os
import secrets
import string
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Mapping

import qrcode

PAIRING_SERVICE = "_adb-tls-pairing._tcp"
CONNECT_SERVICE = "_adb-tls-connect._tcp"
CONNECT_WAIT_S = 20
CONNECT_GRACE_S = 3  # let adb's own auto-connect win before we run `adb connect`
CMD_TIMEOUT_S = 30
QR_COLOURS = "\x1b[38;5;16;48;5;231m"  # pure black foreground on pure white background (256-colour)
_ALNUM = string.ascii_letters + string.digits

NOT_SEEN = ("The phone did not show up: is it on the same Wi-Fi, and did you scan with "
            "Wireless debugging › Pair device with QR code?")


class AdbPairError(Exception):
    pass


@dataclass(frozen=True)
class MdnsService:
    instance: str
    service: str
    host: str
    port: int


def qr_payload(name: str, password: str) -> str:
    return f"WIFI:T:ADB;S:{name};P:{password};;"


def _rand(n: int) -> str:
    return "".join(secrets.choice(_ALNUM) for _ in range(n))


def new_session() -> tuple[str, str]:
    return "homalawn-" + _rand(6), _rand(12)


def qr_matrix(payload: str) -> list[list[bool]]:
    """The QR's module matrix (True = dark), with the standard 4-module quiet zone."""
    qr = qrcode.QRCode(border=4)
    qr.add_data(payload)
    return qr.get_matrix()


def render_qr(payload: str) -> str:
    """Terminal text: two module rows per line as half blocks, drawn in explicit black on white
    so the code reads the same on a dark or a light terminal theme."""
    m = qr_matrix(payload)
    if len(m) % 2:
        m = m + [[False] * len(m[0])]
    glyph = {(True, True): "█", (True, False): "▀", (False, True): "▄", (False, False): " "}
    lines = ["".join(glyph[(a, b)] for a, b in zip(m[i], m[i + 1])) for i in range(0, len(m), 2)]
    return "".join(f"{QR_COLOURS}{line}\x1b[0m\n" for line in lines)


QR_FILE = "homalawn-pair-qr.png"


def qr_png_path(env: Mapping[str, str] | None = None, home: Path | None = None) -> Path:
    """Where the QR picture goes: the per-user runtime dir if there is one, else ~/Pictures."""
    env = os.environ if env is None else env
    runtime = env.get("XDG_RUNTIME_DIR")
    if runtime and Path(runtime).is_dir():
        return Path(runtime) / QR_FILE
    return (home or Path.home()) / "Pictures" / QR_FILE


def save_qr_png(payload: str, path: Path) -> Path:
    """The QR as a black-on-white PNG (Pillow), readable by the phone's camera off any screen."""
    qr = qrcode.QRCode(border=4, box_size=12)
    qr.add_data(payload)
    path.parent.mkdir(parents=True, exist_ok=True)
    qr.make_image(fill_color="black", back_color="white").save(str(path))
    return path


def open_on_desktop(path: Path, env: Mapping[str, str] | None = None, popen=subprocess.Popen) -> bool:
    """Open the picture with `xdg-open` when a desktop session exists (DISPLAY or WAYLAND_DISPLAY);
    True when a viewer was started."""
    env = os.environ if env is None else env
    if not (env.get("DISPLAY") or env.get("WAYLAND_DISPLAY")):
        return False
    try:
        popen(["xdg-open", str(path)], stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
              stderr=subprocess.DEVNULL, start_new_session=True)
    except OSError:
        return False
    return True


def parse_mdns(output: str) -> list[MdnsService]:
    found = []
    for line in output.splitlines():
        parts = line.split("\t")
        if len(parts) != 3:
            continue
        instance, service, addr = (p.strip() for p in parts)
        host, sep, port = addr.rpartition(":")
        if not sep or not port.isdigit():
            continue
        found.append(MdnsService(instance, service.rstrip("."), host.strip("[]"), int(port)))
    return found


def _adb(run, *args: str) -> str:
    argv = ["adb", *args]
    try:
        p = run(argv, capture_output=True, text=True, timeout=CMD_TIMEOUT_S)
    except FileNotFoundError:
        raise AdbPairError("adb is not installed or not on PATH (install Android platform-tools)")
    except subprocess.TimeoutExpired:
        # only the subcommand: the arguments of `adb pair` carry the pairing code
        raise AdbPairError(f"adb did not answer within {CMD_TIMEOUT_S} s (adb {args[0]})")
    return (p.stdout or "") + (p.stderr or "")


def _devices(run) -> set[str]:
    serials = set()
    for line in _adb(run, "devices").splitlines()[1:]:
        parts = line.split()
        if len(parts) >= 2 and parts[1] == "device":
            serials.add(parts[0])
    return serials


def _addr(host: str, port: int) -> str:
    return f"[{host}]:{port}" if ":" in host else f"{host}:{port}"


def _pair(run, host_port: str, secret: str) -> str:
    # adb exits 0 on every pairing failure (client/commandline.cpp adb_query_command), so read
    # the text: success is "Successfully paired to <host> [guid=<guid>]" (client/adb_wifi.cpp:250);
    # failures start with "Failed:" (adb_wifi.cpp:223-244, one of them "Failed: Successfully
    # paired but server returned unknown response="); transport errors start with "error:".
    out = _adb(run, "pair", host_port, secret)
    lines = [ln.strip() for ln in out.splitlines()]
    failed = any(ln.startswith(("Failed:", "error:")) for ln in lines)
    if failed or not any(ln.startswith("Successfully paired to") for ln in lines):
        detail = out.strip().splitlines()[-1] if out.strip() else "no answer"
        raise AdbPairError(f"The phone refused the Wireless debugging code ({detail}). Try again with a fresh code.")
    return out


def mdns_available(run=subprocess.run) -> bool:
    try:
        out = _adb(run, "mdns", "check")
    except AdbPairError:
        return False
    return "mdns daemon version" in out


def pair_by_code(host_port: str, code: str, run=subprocess.run) -> None:
    _pair(run, host_port, code)


def pair_by_qr(say: Callable[[str], None], timeout_s: float = 120, run=subprocess.run, *,
               sleep: Callable[[float], None] = time.sleep,
               clock: Callable[[], float] = time.monotonic, qr_file: Path | None = None,
               open_qr: Callable[[Path], bool] | None = None) -> str:
    """Show a QR, pair with the phone that scans it, return its connected adb serial.

    With `qr_file`, the QR is also saved there as a PNG (and handed to `open_qr`, which puts it on
    the screen) so it reaches the person when the terminal output does not (an agent running this
    in the background); the file is deleted once pairing succeeded or failed."""
    name, password = new_session()
    payload = qr_payload(name, password)
    say(render_qr(payload))
    if qr_file is not None:
        save_qr_png(payload, qr_file)
        opened = bool(open_qr and open_qr(qr_file))
        say(f"QR code also saved to {qr_file}" + (" and opened on your screen." if opened else "."))
    try:
        return _pair_and_connect(say, name, password, timeout_s, run, sleep, clock, qr_file)
    finally:
        if qr_file is not None:
            qr_file.unlink(missing_ok=True)


def _pair_and_connect(say, name, password, timeout_s, run, sleep, clock, qr_file) -> str:
    deadline = clock() + timeout_s
    target = None
    while target is None:
        target = next((s for s in parse_mdns(_adb(run, "mdns", "services"))
                       if s.service == PAIRING_SERVICE and s.instance == name), None)
        if target is None:
            if clock() >= deadline:
                raise AdbPairError(NOT_SEEN)
            sleep(1)
    out = _pair(run, _addr(target.host, target.port), password)
    if qr_file is not None:  # paired: the code is spent, the picture can go now
        qr_file.unlink(missing_ok=True)
    guid = out.split("[guid=", 1)[1].split("]", 1)[0] if "[guid=" in out else None
    say("Code accepted. Connecting to the phone over Wireless debugging…")

    start = clock()
    connected_to: set[str] = set()
    while True:
        services = [s for s in parse_mdns(_adb(run, "mdns", "services"))
                    if s.service == CONNECT_SERVICE and (s.instance == guid or s.host == target.host)]
        serials = _devices(run)
        for s in services:
            for serial in (f"{s.instance}.{s.service}", _addr(s.host, s.port)):
                if serial in serials:
                    return serial
        if clock() - start >= CONNECT_WAIT_S:
            raise AdbPairError("The phone accepted the code but did not connect. Keep Wireless debugging on "
                               "and the phone on the same Wi-Fi, then run the setup again.")
        if clock() - start >= CONNECT_GRACE_S:
            for s in services:
                addr = _addr(s.host, s.port)
                if addr not in connected_to:
                    connected_to.add(addr)
                    _adb(run, "connect", addr)
        sleep(1)
