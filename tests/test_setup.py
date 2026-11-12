"""`homalawn setup` (spec N1-N7) against fakes: a fake adb (both the `subprocess.run` style used for
`adb devices` / `install` / `forward` and the `Device` runner used for shell commands), a fake
link pairing, and a fake or in-process link phone for the proof run. Nothing touches a device."""
from __future__ import annotations

import hashlib
import shlex
import subprocess
from pathlib import Path

import pytest

from homalawn import adb, setup
from homalawn.link import identity
from homalawn.link.identity import Peer

S23 = "adb-R5CT00EXAMPLE-abc123._adb-tls-connect._tcp"
EMU = "emulator-5560"
IPS = "192.168.1.50:41234"  # the S23 again, as adb lists it after `adb connect`
PKG = "app.homalawn"
SERVICE = "app.homalawn/app.lawnchair.LawnchairAccessibilityService"
BASE = "/data/app/~~x==/app.homalawn-y==/base.apk"


class FakeAdb:
    """One phone's state behind both adb entry points. Every argv is logged."""

    def __init__(self, devices=None, installed: bytes | None = b"old build", default_home=False,
                 services="null", a11y="0", wlan="192.168.1.50", install_out="Performing Streamed Install\nSuccess\n",
                 mdns=True, ids=None, forwards=(), sdk="34", grant_ok=True, pkg=PKG, keep_ok=True):
        self.ids = {IPS: "R5CT00EXAMPLE", **(ids or {})}  # serial -> ro.serialno (default: from the mDNS name, else the serial)
        self.forwards = set(forwards)  # (serial, "tcp:P") forwards present
        self.devices = dict(devices if devices is not None else {S23: "device"})
        self.installed = installed
        self.default_home = default_home
        self.settings = {"enabled_accessibility_services": services, "accessibility_enabled": a11y}
        self.wlan = wlan
        self.install_out = install_out  # str, or an exception instance to raise
        self.mdns = mdns
        self.log: list[list[str]] = []
        self.keys: list[str] = []
        self.provider: list[tuple[str, str | None]] = []
        self.sdk = sdk
        self.grant_ok = grant_ok
        self.granted: list[tuple[str, str]] = []
        self.pkg = pkg  # the Homa Lawn app on the phone (when `installed`)
        self.keep_ok = keep_ok
        self.kept: list[str] = []  # packages taken off battery optimisation

    # -- subprocess.run style ------------------------------------------------
    def run(self, argv, capture_output=True, text=True, timeout=None, **kw):
        self.log.append(list(argv))
        assert argv[0] == "adb", argv
        rest = argv[1:]
        if rest == ["devices"]:
            out = "List of devices attached\n" + "".join(f"{s}\t{st}\n" for s, st in self.devices.items()) + "\n"
            return subprocess.CompletedProcess(argv, 0, out, "")
        if rest[:2] == ["mdns", "check"]:
            return subprocess.CompletedProcess(argv, 0, "mdns daemon version [Openscreen discovery 0.0.0]\n"
                                               if self.mdns else "ERROR: mdns daemon unavailable\n", "")
        assert rest[0] == "-s", argv
        serial, cmd = rest[1], rest[2:]
        assert serial in self.devices, argv
        if cmd[:2] == ["install", "-r"]:
            if isinstance(self.install_out, BaseException):
                raise self.install_out
            if "Success" in self.install_out:
                self.installed = Path(cmd[2]).read_bytes()
            return subprocess.CompletedProcess(argv, 0 if "Success" in self.install_out else 1, self.install_out, "")
        if cmd == ["shell", "getprop", "ro.serialno"]:
            import re
            m = re.match(r"adb-(.+)-[^-]+\._adb-tls-connect\._tcp", serial)
            return subprocess.CompletedProcess(argv, 0, self.ids.get(serial, m.group(1) if m else serial) + "\n", "")
        if cmd == ["forward", "--list"]:
            return subprocess.CompletedProcess(argv, 0, "".join(f"{s} {p} {p}\n" for s, p in self.forwards), "")
        if cmd[:2] == ["forward", "--remove"]:
            self.forwards.discard((serial, cmd[2]))
            return subprocess.CompletedProcess(argv, 0, "", "")
        if cmd[0] == "forward":
            self.forwards.add((serial, cmd[1]))
            return subprocess.CompletedProcess(argv, 0, "", "")
        raise AssertionError(f"unexpected adb call {argv}")

    # -- Device runner style --------------------------------------------------
    def runner(self, argv, stdin=None):
        self.log.append(list(argv))
        assert argv[:2] == ["adb", "-s"] and argv[2] in self.devices, argv
        assert argv[3] == "shell", argv
        words = shlex.split(argv[4])
        ok = lambda s="": (0, s.encode(), b"")
        if words == ["getprop", "ro.build.version.sdk"]:
            return ok(self.sdk + "\n")
        if words[:2] == ["pm", "grant"]:
            if not self.grant_ok:
                return (255, b"", b"Exception occurred while executing 'grant': Unknown permission\n")
            self.granted.append((words[2], words[3]))
            return ok()
        if words[:3] == ["dumpsys", "deviceidle", "whitelist"]:
            if not self.keep_ok:
                return ok("")
            self.kept.append(words[3])
            return ok(f"Added: {words[3][1:]}\n")
        if words[:3] == ["pm", "list", "packages"]:
            return ok("package:com.android.settings\n" + (f"package:{self.pkg}\n" if self.installed is not None else ""))
        if words[:2] == ["pm", "path"]:
            return ok(f"package:{BASE}\n") if self.installed is not None else (1, b"", b"")
        if words[0] == "sha256sum":
            return ok(f"{hashlib.sha256(self.installed).hexdigest()}  {words[1]}\n")
        if words[:3] == ["settings", "get", "secure"]:
            return ok(self.settings.get(words[3], "null") + "\n")
        if words[:3] == ["settings", "put", "secure"]:
            self.settings[words[3]] = words[4]
            return ok()
        if words[:3] == ["cmd", "package", "set-home-activity"]:
            self.default_home = True
            return ok("Success\n")
        if words[:2] == ["input", "keyevent"]:
            self.keys.append(words[2])
            return ok()
        if words[:5] == ["ip", "-f", "inet", "addr", "show"]:
            if not self.wlan:
                return (1, b"", b'Device "wlan0" does not exist.')
            return ok(f"30: wlan0: <BROADCAST,MULTICAST,UP,LOWER_UP> mtu 1500\n"
                      f"    inet {self.wlan}/24 brd 192.168.1.255 scope global wlan0\n")
        if words[:2] == ["content", "call"]:
            method = words[words.index("--method") + 1]
            arg = words[words.index("--arg") + 1] if "--arg" in words else None
            self.provider.append((method, arg))
            if method == "ping":
                home = "true" if self.default_home else "false"
                return ok(f'Result: Bundle[{{result={{"version":"15.Dev","default_home":{home}}}, ok=true}}]\n')
            return ok('Result: Bundle[{result={}, ok=true}]\n')
        raise AssertionError(f"unexpected shell command {words}")

    def serials_used(self) -> set[str]:
        return {a[2] for a in self.log if len(a) > 2 and a[1] == "-s"}


class FakeLink:
    """The proof-run transport: a paired phone answering over the link."""
    name = "link"
    has_shell = False

    def __init__(self, label="Pixel", screen_on=True, home=True):
        self.label = label
        self.address = "192.168.1.50:47100"
        self.screen_on = screen_on
        self.home = home
        self.calls = []
        self.closed = False

    def call(self, method, params=None):
        self.calls.append(method)
        if method == "launcher.ping":
            return {"version": "15.Dev", "default_home": self.home, "db": "launcher.db"}
        if method == "screen.status":
            return {"enabled": self.screen_on, "restricted": False}
        raise AssertionError(method)

    def screenshot(self):
        self.calls.append("screen.shot")
        return b"\x89PNG proof"

    def close(self):
        self.closed = True


@pytest.fixture
def apk(tmp_path):
    d = tmp_path / "apk"
    d.mkdir()
    p = d / "Lawnchair.github.debug.apk"
    p.write_bytes(b"new build")
    return p


@pytest.fixture
def proof(monkeypatch):
    """Stub the layout/status reads of the proof run (they are ops' own, tested elsewhere)."""
    from homalawn import ops
    monkeypatch.setattr(ops, "status", lambda s: ops.Result("ok", data={"version": "15.Dev", "default_home": True,
                                                                        "grid": [5, 5]}))
    monkeypatch.setattr(ops, "layout_get", lambda s: ops.Result("", data={
        "screens": [{"index": 0, "items": [{"type": "app"}, {"type": "app"}]}], "hotseat": [{"type": "app"}]}))


def _go(fake, apk_path, tmp_path, *, link=None, pair=None, serial=None, pair_qr=None, ask=None, apk=None,
        reachable=lambda host, port: True, new=False, interactive=True):
    out: list[str] = []
    paired: list = []
    link = link or FakeLink()
    state = {"peer": None}

    def fake_pair(host, port, *, confirm, say, show_words, **kw):
        paired.append((host, port))
        show_words(["apple", "brick", "cloud", "dune"])
        say("Confirm on the phone (you have 120 seconds): Accept there only if it shows the same four words.")
        peer = Peer(fp="f" * 64, name="Pixel", hosts=[f"{host}:{port}"])
        identity.save_peer(peer)
        state["peer"] = peer
        return peer

    def connect_link(peers, addr):
        return link if peers else None

    setup.run(out.append, serial=serial, apk=apk, apk_dir=apk_path.parent, run=fake.run, runner=fake.runner,
              pair=pair or fake_pair, connect_link=connect_link, pair_qr=pair_qr or (lambda say, run: pytest.fail("QR")),
              ask=ask or (lambda q: pytest.fail(f"asked {q!r}")), pictures=tmp_path / "pics", sleep=lambda s: None,
              reachable=reachable, new=new, interactive=interactive)
    return out, paired, link


def test_first_time_path_does_every_step_and_says_each(tmp_path, apk, proof):
    fake = FakeAdb()
    out, paired, link = _go(fake, apk, tmp_path)
    text = "\n".join(out)
    assert fake.installed == b"new build"
    assert fake.default_home is True
    assert fake.settings["enabled_accessibility_services"] == SERVICE
    assert fake.settings["accessibility_enabled"] == "1"
    assert "KEYCODE_HOME" in fake.keys
    assert paired == [("192.168.1.50", 47100)]
    assert "Words: apple brick cloud dune" in text
    assert text.count("tap Accept") == 1 and "Confirm on the phone" not in text
    assert "On the phone: check the four words and tap Accept." in text
    assert setup.SCREEN_ON in out
    assert "Turn it off any time: on the phone, Homa Lawn's settings › Permissions › Screen control." in text
    assert "Homa Lawn is now the home app." in text
    assert fake.granted == [(PKG, "android.permission.POST_NOTIFICATIONS")]
    assert "Notifications allowed for Homa Lawn" in text
    assert "screen.status" in link.calls and "screen.shot" in link.calls
    shots = list((tmp_path / "pics").glob("homalawn-setup-*.png"))
    assert len(shots) == 1 and shots[0].read_bytes() == b"\x89PNG proof"
    assert str(shots[0]) in text
    assert out[-2:] == ["Wireless debugging can be turned off; Homa Lawn does not need it.",
                        "Ready: Pixel is set up and paired."]
    assert fake.serials_used() == {S23}
    assert link.closed


def test_rerun_on_a_set_up_phone_skips_install_and_pairing(tmp_path, apk, proof):
    identity.save_peer(Peer(fp="f" * 64, name="Pixel", hosts=["192.168.1.50:47100"]))
    fake = FakeAdb(installed=b"new build", default_home=True, services=SERVICE, a11y="1")
    out, paired, link = _go(fake, apk, tmp_path, pair=lambda *a, **k: pytest.fail("paired again"))
    text = "\n".join(out)
    assert not any(a[3:5] == ["install", "-r"] for a in fake.log if len(a) > 4)
    assert "already" in text.lower()
    assert "Already paired with Pixel" in text
    assert not any(a[:2] == ["settings", "put"] for a in (shlex.split(x[4]) for x in fake.log if len(x) > 4
                                                           and x[3] == "shell"))
    assert "Ready: Pixel is set up and paired." in text


def test_accessibility_merge_keeps_other_services(tmp_path, apk, proof):
    other = "com.other/.Svc:org.x/org.x.Reader"
    fake = FakeAdb(services=other)
    _go(fake, apk, tmp_path)
    assert fake.settings["enabled_accessibility_services"] == f"{other}:{SERVICE}"


def test_service_listed_but_accessibility_off_only_switches_it_on(tmp_path, apk, proof):
    fake = FakeAdb(services=f"com.other/.Svc:{SERVICE}", a11y="0")
    out, _, _ = _go(fake, apk, tmp_path)
    assert fake.settings["enabled_accessibility_services"] == f"com.other/.Svc:{SERVICE}"  # not added twice
    assert fake.settings["accessibility_enabled"] == "1"
    assert any(line.startswith("Screen control switched on") for line in out)


@pytest.mark.parametrize("install_out", [
    "Performing Streamed Install\nadb: failed to install x.apk: Failure [INSTALL_FAILED_USER_RESTRICTED: Install canceled by user]\n",
    "Performing Streamed Install\nFailure: blocked by Auto Blocker\n",
])
def test_samsung_auto_blocker_is_named(tmp_path, apk, install_out):
    fake = FakeAdb(install_out=install_out)
    with pytest.raises(setup.SetupError) as e:
        _go(fake, apk, tmp_path)
    assert "Samsung Auto Blocker is on: Settings › Security and privacy › Auto Blocker, off for the install" in str(e.value)


def test_install_that_does_not_finish_names_both_causes(tmp_path, apk):
    fake = FakeAdb(install_out=subprocess.TimeoutExpired(["adb"], 180, output=b"Performing Streamed Install\n"))
    with pytest.raises(setup.SetupError) as e:
        _go(fake, apk, tmp_path)
    msg = str(e.value)
    assert "did not finish in 3 minutes" in msg and "slow Wi-Fi" in msg
    assert "check the phone for a Play Protect or install prompt" in msg
    assert "Samsung Auto Blocker" in msg and "Settings › Security and privacy › Auto Blocker" in msg


def test_signature_mismatch_never_uninstalls(tmp_path, apk):
    fake = FakeAdb(install_out="Performing Streamed Install\nadb: failed to install: Failure "
                               "[INSTALL_FAILED_UPDATE_INCOMPATIBLE: Existing package signatures do not match]\n")
    with pytest.raises(setup.SetupError) as e:
        _go(fake, apk, tmp_path)
    assert "signed with another key" in str(e.value) and "not done" in str(e.value)
    flat = [" ".join(a) for a in fake.log]
    assert not any("uninstall" in a or "pm clear" in a for a in flat)
    assert fake.installed == b"old build"


def test_emulator_is_never_chosen_implicitly(tmp_path, apk, proof):
    fake = FakeAdb(devices={EMU: "device"})
    calls = []

    def pair_qr(say, run):
        calls.append(1)
        fake.devices["192.168.1.50:41234"] = "device"
        return "192.168.1.50:41234"

    out, _, _ = _go(fake, apk, tmp_path, pair_qr=pair_qr)
    assert calls == [1]
    assert EMU not in fake.serials_used()
    assert "Pair device with QR code, then scan this." in "\n".join(out)


def test_named_serial_never_touches_the_real_phone(tmp_path, apk, proof):
    fake = FakeAdb(devices={S23: "device", EMU: "device"})
    _go(fake, apk, tmp_path, serial=EMU)
    assert fake.serials_used() == {EMU}


def test_several_phones_without_serial_stops(tmp_path, apk):
    fake = FakeAdb(devices={S23: "device", "R5CX123": "device", EMU: "device"})
    with pytest.raises(setup.SetupError) as e:
        _go(fake, apk, tmp_path)
    assert "--serial" in str(e.value)
    assert not any("install" in a for a in fake.log)
    assert all(a[3:] == ["shell", "getprop", "ro.serialno"] for a in fake.log if len(a) > 3 and a[1] == "-s")


def test_named_serial_not_connected_stops(tmp_path, apk):
    fake = FakeAdb(devices={S23: "device"})
    with pytest.raises(setup.SetupError) as e:
        _go(fake, apk, tmp_path, serial=EMU)
    assert EMU in str(e.value)
    assert fake.serials_used() == set()


def test_code_fallback_asks_only_when_mdns_is_off(tmp_path, apk, proof, monkeypatch):
    fake = FakeAdb(devices={}, mdns=False)
    asked = []
    answers = iter(["192.168.1.50:37000", "123456", "192.168.1.50:41234"])

    def ask(q):
        asked.append(q)
        return next(answers)

    paired = []
    from homalawn import adbpair
    monkeypatch.setattr(adbpair, "pair_by_code", lambda hp, code, run: paired.append((hp, code)))
    orig = fake.run

    def run(argv, **kw):
        if argv[1:2] == ["connect"]:
            fake.log.append(list(argv))
            fake.devices[argv[2]] = "device"
            return subprocess.CompletedProcess(argv, 0, f"connected to {argv[2]}\n", "")
        return orig(argv, **kw)
    fake.run = run
    _go(fake, apk, tmp_path, ask=ask)
    assert paired == [("192.168.1.50:37000", "123456")]
    assert len(asked) == 3
    assert fake.serials_used() == {"192.168.1.50:41234"}


def test_same_build_is_not_reinstalled(tmp_path, apk, proof):
    fake = FakeAdb(installed=b"new build")
    out, _, _ = _go(fake, apk, tmp_path)
    assert not any("install" in a for a in fake.log)
    assert any("already" in line and "build" in line for line in out)


def test_no_build_here_keeps_the_installed_app(tmp_path, proof):
    empty = tmp_path / "none" / "x.apk"
    empty.parent.mkdir()
    fake = FakeAdb(installed=b"old build")
    out, _, _ = _go(fake, empty, tmp_path)
    assert fake.installed == b"old build"
    assert "GitHub could not be reached" in "\n".join(out)          # why: no build here, the release unreachable
    assert "Keeping the Homa Lawn the phone has." in out


def test_no_build_and_nothing_installed_stops(tmp_path):
    empty = tmp_path / "none" / "x.apk"
    empty.parent.mkdir()
    with pytest.raises(setup.SetupError) as e:
        _go(FakeAdb(installed=None), empty, tmp_path)
    assert str(e.value).startswith("Homa Lawn is not on the phone and there is nothing to install: GitHub could "
                                   "not be reached")


def test_screen_control_that_does_not_come_on_is_said(tmp_path, apk, proof):
    with pytest.raises(setup.SetupError) as e:
        _go(FakeAdb(), apk, tmp_path, link=FakeLink(screen_on=False))
    assert "Homa Lawn's settings › Permissions › Screen control" in str(e.value)


def test_no_wifi_address_stops_with_what_to_do(tmp_path, apk):
    with pytest.raises(setup.SetupError) as e:
        _go(FakeAdb(wlan=None), apk, tmp_path)
    assert "Wi-Fi" in str(e.value)


def test_debug_forward_pairs_through_loopback_and_cleans_up(tmp_path, apk, proof):
    fake = FakeAdb(devices={EMU: "device"})
    out: list[str] = []
    paired = []

    def fake_pair(host, port, *, confirm, say, show_words, **kw):
        paired.append((host, port))
        peer = Peer(fp="f" * 64, name="AVD", hosts=[f"{host}:{port}"])
        identity.save_peer(peer)
        return peer

    setup.run(out.append, serial=EMU, apk_dir=apk.parent, run=fake.run, runner=fake.runner, pair=fake_pair,
              connect_link=lambda peers, addr: FakeLink("AVD") if peers else None, debug_forward=True,
              pictures=tmp_path / "pics", sleep=lambda s: None)
    assert paired == [("127.0.0.1", 47100)]
    assert ["adb", "-s", EMU, "forward", "tcp:47100", "tcp:47100"] in fake.log
    assert ["adb", "-s", EMU, "forward", "--remove", "tcp:47100"] in fake.log
    assert fake.forwards == set()
    assert ("debug_allow_local_pairing", "on") in fake.provider
    assert fake.provider[-1] == ("debug_allow_local_pairing", "off")


def test_real_pairing_prints_one_accept_instruction(tmp_path, apk, proof):
    """With the real pairing code (against test_pairing's fake phone) the Accept instruction is one line."""
    from test_pairing import FakePhone
    phone = FakePhone(tmp_path)
    fake = FakeAdb(wlan="127.0.0.1")
    out: list[str] = []
    setup.run(out.append, apk_dir=apk.parent, run=fake.run, runner=fake.runner, link_port=phone.port,
              connect_link=lambda peers, addr: FakeLink() if peers else None, pictures=tmp_path / "pics",
              sleep=lambda s: None)
    text = "\n".join(out)
    assert text.count("Accept") == 1, text
    assert "On the phone: check the four words and tap Accept." in text


def test_proof_run_over_a_real_link(tmp_path, apk):
    """Re-run path end to end over TLS: the already-paired check and the proof use the real link."""
    from fake_link import FakeLinkPhone, make_cert
    from test_session import FakeDevice
    ident = identity.ensure()
    dev = FakeDevice(tmp_path)
    phone = FakeLinkPhone(make_cert(tmp_path / "phone"), dev, trust=[ident.cert], pinned={ident.fingerprint},
                          name="Pixel")
    phone.handlers["screen.status"] = lambda p: ({"enabled": True, "restricted": False}, None)
    phone.handlers["screen.shot"] = lambda p: ({}, b"\x89PNG real")
    identity.save_peer(Peer(fp=phone.fp, name="Pixel", hosts=[phone.addr]))
    fake = FakeAdb(installed=b"new build", default_home=True, services=SERVICE, a11y="1", wlan="127.0.0.1")
    out: list[str] = []
    try:
        setup.run(out.append, apk_dir=apk.parent, run=fake.run, runner=fake.runner, link_port=phone.port,
                  pair=lambda *a, **k: pytest.fail("paired again"), pictures=tmp_path / "pics", sleep=lambda s: None)
    finally:
        phone.close()
    text = "\n".join(out)
    assert "Already paired with Pixel" in text
    assert "Ready: Pixel is set up and paired." in text
    assert next((tmp_path / "pics").glob("homalawn-setup-*.png")).read_bytes() == b"\x89PNG real"


def test_an_adb_failure_midway_is_one_line(tmp_path, apk):
    fake = FakeAdb()
    orig = fake.runner

    def runner(argv, stdin=None):
        if "settings" in argv[-1]:
            return (1, b"", b"error: device offline")
        return orig(argv, stdin)
    fake.runner = runner
    with pytest.raises(setup.SetupError) as e:
        _go(fake, apk, tmp_path)
    assert "device offline" in str(e.value) and "run setup again" in str(e.value)


# -- review round 1 ------------------------------------------------------------------------------


def test_one_wireless_phone_listed_twice_is_one_phone(tmp_path, apk, proof):
    fake = FakeAdb(devices={S23: "device", IPS: "device", EMU: "device"})
    out, paired, _ = _go(fake, apk, tmp_path)
    assert "Ready: Pixel is set up and paired." in "\n".join(out)
    assert out[0].startswith(f"Phone: {IPS} (connected over adb).")
    # the run itself uses the ip:port serial; the mDNS one is only asked who it is
    to_mdns = [a for a in fake.log if len(a) > 2 and a[1] == "-s" and a[2] == S23]
    assert all(a[3:] == ["shell", "getprop", "ro.serialno"] for a in to_mdns), to_mdns
    assert EMU not in fake.serials_used()
    assert paired == [("192.168.1.50", 47100)]


def test_rerun_with_a_double_listing_is_still_one_phone(tmp_path, apk, proof):
    identity.save_peer(Peer(fp="f" * 64, name="Pixel", hosts=["192.168.1.50:47100"]))
    fake = FakeAdb(devices={S23: "device", IPS: "device"}, installed=b"new build", default_home=True,
                   services=SERVICE, a11y="1")
    out, _, _ = _go(fake, apk, tmp_path, pair=lambda *a, **k: pytest.fail("paired again"))
    text = "\n".join(out)
    assert "Already paired with Pixel" in text and "Ready:" in text


def test_two_different_phones_stop(tmp_path, apk):
    fake = FakeAdb(devices={S23: "device", IPS: "device", "R5CX999": "device"})
    with pytest.raises(setup.SetupError) as e:
        _go(fake, apk, tmp_path)
    assert "Several phones" in str(e.value) and "--serial" in str(e.value)
    assert not any(a[3:5] == ["install", "-r"] for a in fake.log if len(a) > 4)


def test_qr_pairing_that_leaves_a_double_listing_uses_one_serial(tmp_path, apk, proof):
    fake = FakeAdb(devices={EMU: "device"})

    def pair_qr(say, run):  # adb's auto-connect and pair_by_qr's own `adb connect` both landed
        fake.devices[S23] = "device"
        fake.devices[IPS] = "device"
        return S23
    out, _, _ = _go(fake, apk, tmp_path, pair_qr=pair_qr)
    assert f"Phone: {IPS} (connected over Wireless debugging)." in out
    assert "Ready: Pixel is set up and paired." in out
    assert EMU not in fake.serials_used()


def test_link_pairing_tries_the_adb_host_first(tmp_path, apk, proof):
    fake = FakeAdb(devices={"10.0.0.7:5555": "device"}, wlan="192.168.1.50")
    _, paired, _ = _go(fake, apk, tmp_path)
    assert paired == [("10.0.0.7", 47100)]


def test_link_pairing_falls_back_to_wlan0_when_the_adb_host_does_not_answer(tmp_path, apk, proof):
    fake = FakeAdb(devices={"10.0.0.7:5555": "device"}, wlan="192.168.1.50")
    _, paired, _ = _go(fake, apk, tmp_path, reachable=lambda host, port: host == "192.168.1.50")
    assert paired == [("192.168.1.50", 47100)]


def test_no_address_answers_stops(tmp_path, apk):
    fake = FakeAdb(devices={"10.0.0.7:5555": "device"}, wlan="192.168.1.50")
    with pytest.raises(setup.SetupError) as e:
        _go(fake, apk, tmp_path, reachable=lambda host, port: False)
    assert "10.0.0.7:47100" in str(e.value) and "192.168.1.50:47100" in str(e.value)


@pytest.mark.parametrize("state, words", [("unauthorized", "tap Allow for this computer"),
                                          ("offline", "reconnect")])
def test_named_serial_not_ready_says_what_to_do(tmp_path, apk, state, words):
    fake = FakeAdb(devices={EMU: state})
    with pytest.raises(setup.SetupError) as e:
        _go(fake, apk, tmp_path, serial=EMU)
    assert words in str(e.value) and "not connected" not in str(e.value)


def test_debug_forward_keeps_a_forward_that_was_there(tmp_path, apk, proof):
    fake = FakeAdb(devices={EMU: "device"}, forwards={(EMU, "tcp:47100")})

    def fake_pair(host, port, *, confirm, say, show_words, **kw):
        peer = Peer(fp="f" * 64, name="AVD", hosts=[f"{host}:{port}"])
        identity.save_peer(peer)
        return peer
    setup.run(lambda m: None, serial=EMU, apk_dir=apk.parent, run=fake.run, runner=fake.runner, pair=fake_pair,
              connect_link=lambda peers, addr: FakeLink("AVD") if peers else None, debug_forward=True,
              pictures=tmp_path / "pics", sleep=lambda s: None)
    assert fake.forwards == {(EMU, "tcp:47100")}
    assert not any(a[3:5] == ["forward", "--remove"] for a in fake.log if len(a) > 4)


# -- final review fixes ---------------------------------------------------------------------------
NEW = "192.168.1.77:40001"  # the never-seen phone, as adb lists it after QR pairing


def _new_phone_qr(fake, *serials, returns=NEW):
    calls = []

    def pair_qr(say, run):
        calls.append(1)
        for s in serials or (returns,):
            fake.devices[s] = "device"
        return returns
    return pair_qr, calls


def test_new_goes_straight_to_qr_even_with_a_phone_on_adb(tmp_path, apk, proof):
    """I1: A phone set up before reconnects by itself while Wireless debugging is on; --new must not pick it."""
    fake = FakeAdb(devices={S23: "device", IPS: "device", EMU: "device"}, ids={NEW: "NEWPHONE02"}, wlan="192.168.1.77")
    pair_qr, calls = _new_phone_qr(fake)
    out, paired, _ = _go(fake, apk, tmp_path, pair_qr=pair_qr, new=True)
    assert calls == [1]
    assert f"Phone: {NEW} (connected over Wireless debugging)." in out
    assert "Pair device with QR code, then scan this." in "\n".join(out)
    assert fake.serials_used() == {NEW}  # the S23 (both listings) and the AVD are never touched
    assert paired == [("192.168.1.77", 47100)]


def test_new_uses_only_the_paired_phone_never_one_listed_before(tmp_path, apk, proof):
    """A double listing of the NEW phone is resolved; a phone that was there before is not a candidate
    even if its id were to collide."""
    mdns_name = "adb-NEWPHONE02-XyZabc._adb-tls-connect._tcp"
    fake = FakeAdb(devices={S23: "device"}, ids={NEW: "NEWPHONE02"}, wlan="192.168.1.77")
    pair_qr, _ = _new_phone_qr(fake, mdns_name, NEW, returns=mdns_name)
    out, _, _ = _go(fake, apk, tmp_path, pair_qr=pair_qr, new=True)
    assert f"Phone: {NEW} (connected over Wireless debugging)." in out
    assert S23 not in fake.serials_used()


def test_new_with_serial_stops(tmp_path, apk):
    fake = FakeAdb(devices={EMU: "device"})
    with pytest.raises(setup.SetupError) as e:
        _go(fake, apk, tmp_path, serial=EMU, new=True)
    assert "--new" in str(e.value) and "--serial" in str(e.value)
    assert fake.serials_used() == set()


def test_without_new_a_connected_phone_says_how_to_set_up_another(tmp_path, apk, proof):
    fake = FakeAdb()
    out, _, _ = _go(fake, apk, tmp_path)
    assert out[0] == (f"Phone: {S23} (connected over adb). (To set up a different phone that this computer has "
                      "never seen, run `homalawn setup --new`.)")


def test_several_phones_names_new_too(tmp_path, apk):
    fake = FakeAdb(devices={S23: "device", "R5CX123": "device"})
    with pytest.raises(setup.SetupError) as e:
        _go(fake, apk, tmp_path)
    assert "homalawn setup --new" in str(e.value)


@pytest.mark.parametrize("new", [False, True])
def test_background_run_without_mdns_stops_instead_of_prompting(tmp_path, apk, new):
    """I3: the typed fallback cannot be answered from a background run: one line, no prompt."""
    fake = FakeAdb(devices={EMU: "device"}, mdns=False)
    with pytest.raises(setup.SetupError) as e:
        _go(fake, apk, tmp_path, new=new, interactive=False)
    assert str(e.value) == setup.TYPE_IN_TERMINAL
    assert "run `homalawn setup --new` in your own terminal" in str(e.value)


def test_interactive_defaults_to_whether_stdin_is_a_terminal(tmp_path, apk, monkeypatch):
    class NotATty:
        def isatty(self):
            return False
    monkeypatch.setattr(setup.sys, "stdin", NotATty())
    fake = FakeAdb(devices={}, mdns=False)
    with pytest.raises(setup.SetupError) as e:
        setup.run(lambda m: None, apk_dir=apk.parent, run=fake.run, runner=fake.runner,
                  ask=lambda q: pytest.fail("asked"), pictures=tmp_path / "pics", sleep=lambda s: None)
    assert str(e.value) == setup.TYPE_IN_TERMINAL


def test_default_qr_pairing_saves_and_opens_the_picture(monkeypatch):
    from homalawn import adbpair
    seen = {}
    monkeypatch.setattr(adbpair, "pair_by_qr", lambda say, **kw: seen.update(kw) or NEW)
    assert setup._pair_qr_on_screen(lambda m: None, run="RUN") == NEW
    assert seen["run"] == "RUN"
    assert seen["qr_file"] == adbpair.qr_png_path()
    assert seen["open_qr"] is adbpair.open_on_desktop
    assert setup.run.__kwdefaults__["pair_qr"] is setup._pair_qr_on_screen


def test_notifications_before_android_13_are_left_alone(tmp_path, apk, proof):
    fake = FakeAdb(sdk="32")
    out, _, _ = _go(fake, apk, tmp_path)
    assert fake.granted == []
    assert not any("Notifications allowed" in line or "allow notifications" in line for line in out)


def test_notification_grant_that_fails_does_not_stop_setup(tmp_path, apk, proof):
    fake = FakeAdb(sdk="33", grant_ok=False)
    out, _, _ = _go(fake, apk, tmp_path)
    assert "Could not allow notifications for Homa Lawn; if the phone asks, tap Allow." in out
    assert "Ready: Pixel is set up and paired." in out


def test_notifications_are_granted_on_a_rerun_too(tmp_path, apk, proof):
    identity.save_peer(Peer(fp="f" * 64, name="Pixel", hosts=["192.168.1.50:47100"]))
    fake = FakeAdb(installed=b"new build", default_home=True, services=SERVICE, a11y="1")
    _go(fake, apk, tmp_path, pair=lambda *a, **k: pytest.fail("paired again"))
    assert fake.granted == [(PKG, "android.permission.POST_NOTIFICATIONS")]


def test_keep_connected_takes_the_app_off_battery_optimisation(tmp_path, apk, proof):
    """The link has no foreground service: off battery optimisation is what keeps it reachable
    behind other apps and in Doze (Android 15+), so setup does what "Keep connected" does."""
    fake = FakeAdb()
    out, _, _ = _go(fake, apk, tmp_path)
    assert fake.kept == [f"+{PKG}"]
    assert "Homa Lawn may run in the background (the link answers while the phone sleeps)." in out


def test_keep_connected_that_fails_says_what_to_tap_and_goes_on(tmp_path, apk, proof):
    fake = FakeAdb(keep_ok=False)
    out, _, _ = _go(fake, apk, tmp_path)
    assert setup.KEEP_OFF in out and "tap Run in background" in setup.KEEP_OFF
    assert "Ready: Pixel is set up and paired." in out


def test_notifications_off_says_run_setup_again(tmp_path, apk):
    from homalawn.link import pairing
    from homalawn.link.wire import LinkError

    def pair(host, port, **kw):
        raise pairing.PairError(pairing._explain(LinkError("notifications_off", "off")))
    with pytest.raises(setup.SetupError) as e:
        _go(FakeAdb(), apk, tmp_path, pair=pair)
    assert str(e.value) == setup.NOTIFY_OFF
    assert "run setup again" in str(e.value) and "run pair again" not in str(e.value)


# -- install path: adb when the phone is on adb, the link only when it is not (spec U2) -------------
class UpdatingLink(FakeLink):
    """A paired phone whose Homa Lawn has the updater: app_info + update_offer; `tap` is what the
    person does after the notification (here: the install lands on the fake phone)."""

    def __init__(self, fake, knows_update=True, tap=True, refuse=None, answer=None):
        super().__init__()
        self.fake, self.knows_update, self.tap, self.refuse = fake, knows_update, tap, refuse
        self.answer = answer or {}  # extra fields of the last chunk's answer (silent, deferred_until_unlock)
        self.got = b""

    def call(self, method, params=None):
        from homalawn import update
        from homalawn.transport import ProviderError
        if method == update.APP_INFO:
            self.calls.append(method)
            if not self.knows_update:
                raise ProviderError("unknown_method: x", code="unknown_method")
            return {"package": PKG, "versionCode": 1, "versionName": "x",
                    "sha256": hashlib.sha256(self.fake.installed).hexdigest()}
        return super().call(method, params)

    def request(self, method, params=None, blob=None):
        from homalawn.transport import ProviderError
        self.calls.append(method)
        if not self.knows_update:
            raise ProviderError("unknown_method: x", code="unknown_method")
        self.got += blob
        if len(self.got) < params["size"]:
            return {"received": len(self.got)}, None
        if self.refuse:
            raise ProviderError(f"{self.refuse}: no", code=self.refuse)
        if self.tap:
            self.fake.installed = self.got
        return {"received": len(self.got), "offered": True, **self.answer}, None


def _paired_set_up_phone():
    identity.save_peer(Peer(fp="f" * 64, name="Pixel", hosts=["192.168.1.50:47100"]))
    return FakeAdb(installed=b"old build", default_home=True, services=SERVICE, a11y="1")


def _drops_off_adb(fake):
    """The phone is found over adb (setup's first look, then find_phone), then is gone from
    `adb devices` when the install starts."""
    orig, seen = fake.run, {"n": 0}

    def run(argv, **kw):
        if argv[1:] == ["devices"]:
            seen["n"] += 1
            if seen["n"] > 2:
                fake.log.append(list(argv))
                return subprocess.CompletedProcess(argv, 0, "List of devices attached\n\n", "")
        return orig(argv, **kw)
    fake.run = run
    return fake


def _adb_installs(fake):
    return [a for a in fake.log if len(a) > 4 and a[3:5] == ["install", "-r"]]


def test_a_phone_on_adb_is_installed_over_adb_even_when_the_link_could_update(tmp_path, apk, proof):
    from homalawn import update
    fake = _paired_set_up_phone()
    link = UpdatingLink(fake)
    out, _, _ = _go(fake, apk, tmp_path, link=link, pair=lambda *a, **k: pytest.fail("paired again"))
    assert fake.installed == b"new build" and len(_adb_installs(fake)) == 1
    assert update.UPDATE_OFFER not in link.calls and update.APP_INFO not in link.calls
    assert f"Homa Lawn updated over adb from {apk.name}." in out
    assert update.TAP not in out


def test_first_install_says_adb(tmp_path, apk, proof):
    out, _, _ = _go(FakeAdb(installed=None), apk, tmp_path)
    assert f"Homa Lawn installed over adb from {apk.name}." in out


def test_a_phone_gone_from_adb_is_updated_over_the_link(tmp_path, apk, proof):
    from homalawn import update
    fake = _drops_off_adb(_paired_set_up_phone())
    link = UpdatingLink(fake)
    out, _, _ = _go(fake, apk, tmp_path, link=link, pair=lambda *a, **k: pytest.fail("paired again"))
    assert fake.installed == b"new build" and _adb_installs(fake) == []
    assert update.UPDATE_OFFER in link.calls
    assert any("not connected over adb; updating over the Homa Lawn link" in line for line in out)
    assert update.TAP in out
    assert f"Homa Lawn updated over the link from {apk.name}." in out
    assert "Ready: Pixel is set up and paired." in out


def test_a_link_update_nobody_taps_stops_with_what_to_do(tmp_path, apk):
    fake = _drops_off_adb(_paired_set_up_phone())
    with pytest.raises(setup.SetupError) as e:
        _go(fake, apk, tmp_path, link=UpdatingLink(fake, tap=False))
    assert str(e.value) == setup.UPDATE_NOT_TAPPED
    assert "notification" in str(e.value) and "run setup again" in str(e.value)


def test_a_phone_gone_from_adb_without_the_updater_says_what_to_do(tmp_path, apk):
    fake = _drops_off_adb(_paired_set_up_phone())
    with pytest.raises(setup.SetupError) as e:
        _go(fake, apk, tmp_path, link=UpdatingLink(fake, knows_update=False))
    assert str(e.value) == setup.LINK_NO_UPDATER
    assert _adb_installs(fake) == []


def test_a_refused_link_update_stops(tmp_path, apk):
    fake = _drops_off_adb(_paired_set_up_phone())
    with pytest.raises(setup.SetupError) as e:
        _go(fake, apk, tmp_path, link=UpdatingLink(fake, refuse="wrong_key"))
    assert "another key" in str(e.value)
    assert _adb_installs(fake) == []


def test_a_phone_gone_from_adb_and_silent_on_the_link_still_tries_adb(tmp_path, apk, proof):
    fake = _drops_off_adb(_paired_set_up_phone())
    out: list[str] = []
    asked = []

    def connect_link(peers, addr):
        asked.append(addr)
        return None if len(asked) <= 1 else FakeLink()  # silent for the install (one address), back after
    setup.run(out.append, apk_dir=apk.parent, run=fake.run, runner=fake.runner, connect_link=connect_link,
              pair=lambda *a, **k: pytest.fail("paired again"), pictures=tmp_path / "pics", sleep=lambda s: None)
    assert len(_adb_installs(fake)) == 1


def test_with_two_paired_phones_only_this_phone_is_dialled(tmp_path, apk, proof):
    identity.save_peer(Peer(fp="a" * 64, name="Other", hosts=["192.168.1.60:47100"]))
    fake = _drops_off_adb(_paired_set_up_phone())  # Pixel at 192.168.1.50, the adb phone's Wi-Fi address
    link = UpdatingLink(fake)
    dialled = []

    def connect_link(peers, addr):
        dialled.append(([p.name for p in peers], addr))
        return link if peers else None
    out: list[str] = []
    setup.run(out.append, apk_dir=apk.parent, run=fake.run, runner=fake.runner, connect_link=connect_link,
              pair=lambda *a, **k: pytest.fail("paired again"), pictures=tmp_path / "pics", sleep=lambda s: None)
    assert fake.installed == b"new build"
    install_dials = [d for d in dialled if len(d[0]) == 1]  # link_pair's later check passes every peer
    assert install_dials and all(d == (["Pixel"], "192.168.1.50:47100") for d in install_dials)


def test_a_phone_whose_address_no_paired_phone_has_is_never_offered_over_the_link(tmp_path, apk, proof):
    identity.save_peer(Peer(fp="a" * 64, name="Other", hosts=["192.168.1.60:47100"]))
    fake = _drops_off_adb(FakeAdb(installed=b"old build", default_home=True, services=SERVICE, a11y="1"))
    other = UpdatingLink(fake)
    out: list[str] = []
    calls = []

    def connect_link(peers, addr):
        calls.append(addr)
        return other if peers else None
    setup.run(out.append, apk_dir=apk.parent, run=fake.run, runner=fake.runner, connect_link=connect_link,
              pictures=tmp_path / "pics", sleep=lambda s: None)
    from homalawn import update
    assert update.UPDATE_OFFER not in other.calls
    assert len(_adb_installs(fake)) == 1


# -- a set-up phone over the link only (no adb) ------------------------------------------------------
from types import SimpleNamespace


def _link_only(tmp_path, apk, link, *, via=None, devices=None, phone_name=None, fake=None):
    identity.save_peer(Peer(fp="f" * 64, name="Pixel", hosts=["192.168.1.50:47100"]))
    fake = fake or FakeAdb(devices=devices if devices is not None else {})
    out: list[str] = []
    setup.run(out.append, apk_dir=apk.parent, run=fake.run, runner=fake.runner, via=via, phone_name=phone_name,
              link_to_peer=lambda peer: link, connect_link=lambda *a: pytest.fail("adb-path link"),
              pair=lambda *a, **k: pytest.fail("paired"), pair_qr=lambda say, run: pytest.fail("QR"),
              pictures=tmp_path / "pics", sleep=lambda s: None)
    return out, fake


def test_link_only_all_satisfied(tmp_path, apk, proof):
    phone = SimpleNamespace(installed=b"new build")
    link = UpdatingLink(phone)
    out, fake = _link_only(tmp_path, apk, link)
    assert out[0] == "Phone: Pixel (over the Homa Lawn link; not connected over adb)."
    assert f"Homa Lawn on Pixel is already this build ({apk.name}); nothing sent." in out
    assert setup.LINK_NOTIFY in out
    assert "Homa Lawn is already the home app." in out and "Screen control is already on." in out
    assert out[-1] == "Ready: Pixel is set up and paired."
    assert len(list((tmp_path / "pics").glob("homalawn-setup-*.png"))) == 1
    assert fake.log == [["adb", "devices"]]  # looked once for a phone on adb; nothing else over adb
    assert setup.NO_ADB_DEBUG not in out


def test_link_only_updates_over_the_link(tmp_path, apk, proof):
    from homalawn import update
    phone = SimpleNamespace(installed=b"old build")
    link = UpdatingLink(phone)
    out, _ = _link_only(tmp_path, apk, link)
    assert phone.installed == b"new build" and update.TAP in out
    assert f"Homa Lawn updated over the link from {apk.name}." in out
    assert out[-1] == "Ready: Pixel is set up and paired."


def test_link_only_locked_phone_says_it_installs_at_unlock_and_goes_on(tmp_path, apk, proof):
    """A locked phone keeps the build until the next unlock and posts no notification: setup says
    so, does not wait 3 minutes for a tap, and carries on with its checks."""
    from homalawn import update
    phone = SimpleNamespace(installed=b"old build")
    link = UpdatingLink(phone, tap=False, answer={"silent": True, "deferred_until_unlock": True})
    out, _ = _link_only(tmp_path, apk, link)
    assert "Pixel is locked; the update installs by itself the next time it is unlocked." in out
    assert update.TAP not in out and not any("Waiting for the update" in line for line in out)
    assert link.calls.count(update.APP_INFO) == 1  # asked once before sending; never polled for the build
    assert "Homa Lawn is already the home app." in out and "Screen control is already on." in out
    assert out[-1] == "Ready: Pixel is set up and paired."


def test_link_only_silent_update_says_updated_once_and_does_not_wait(tmp_path, apk, proof):
    from homalawn import update
    phone = SimpleNamespace(installed=b"old build")
    link = UpdatingLink(phone, answer={"silent": True})
    out, _ = _link_only(tmp_path, apk, link)
    assert phone.installed == b"new build"
    assert out.count("Updated.") == 1 and update.TAP not in out
    assert not any("Waiting for the update" in line or "updated over the Homa Lawn link" in line for line in out)
    assert out[-1] == "Ready: Pixel is set up and paired."


def test_link_only_screen_control_off_says_the_setting_and_goes_on(tmp_path, apk, proof):
    link = UpdatingLink(SimpleNamespace(installed=b"new build"))
    link.screen_on = False
    out, _ = _link_only(tmp_path, apk, link)
    assert "Screen control is off: on the phone, Homa Lawn's settings › Permissions › Screen control." in out
    assert "screen.shot" not in link.calls and not (tmp_path / "pics").exists()
    assert out[-1] == "Almost ready: Pixel is set up and paired; switch on what is listed above."


def test_link_only_default_home_lost_points_to_the_notification_button(tmp_path, apk, proof):
    link = UpdatingLink(SimpleNamespace(installed=b"new build"))
    link.home = False
    out, _ = _link_only(tmp_path, apk, link)
    assert setup.LINK_HOME_LOST in out
    assert setup.LINK_HOME_LOST == ("Homa Lawn is not the home app: on the phone, tap Set as home in Homa "
                                    "Lawn's notification, or open Homa Lawn's settings › Permissions › Home app.")
    assert "Screen control is already on." in out
    assert out[-1] == "Almost ready: Pixel is set up and paired; switch on what is listed above."


def test_link_only_with_a_build_without_the_updater_keeps_it_and_goes_on(tmp_path, apk, proof):
    link = UpdatingLink(SimpleNamespace(installed=b"old build"), knows_update=False)
    out, _ = _link_only(tmp_path, apk, link)
    assert setup.LINK_OLD_BUILD.format(phone="Pixel") in out
    assert out[-1] == "Ready: Pixel is set up and paired."


def test_via_link_forces_the_link_path_even_with_the_phone_on_adb(tmp_path, apk, proof):
    link = UpdatingLink(SimpleNamespace(installed=b"new build"))
    out, fake = _link_only(tmp_path, apk, link, via="link", devices={S23: "device"})
    assert out[0] == "Phone: Pixel (over the Homa Lawn link, as asked)."  # adb not asked: no claim about it
    assert "not connected over adb" not in "\n".join(out)
    assert fake.log == []  # adb is not asked anything


def test_via_link_with_a_phone_that_does_not_answer_stops(tmp_path, apk):
    with pytest.raises(setup.SetupError) as e:
        _link_only(tmp_path, apk, None, via="link")
    assert "does not answer over the Homa Lawn link" in str(e.value)


def test_via_link_with_two_paired_phones_needs_a_name(tmp_path, apk, proof):
    identity.save_peer(Peer(fp="a" * 64, name="Other", hosts=["192.168.1.60:47100"]))
    link = UpdatingLink(SimpleNamespace(installed=b"new build"))
    with pytest.raises(setup.SetupError) as e:
        _link_only(tmp_path, apk, link, via="link")
    assert "Several phones are paired" in str(e.value)
    out, _ = _link_only(tmp_path, apk, link, via="link", phone_name="Pixel")
    assert out[0].startswith("Phone: Pixel (over the Homa Lawn link")


def test_no_adb_phone_and_a_silent_link_still_pairs_over_adb(tmp_path, apk, proof):
    identity.save_peer(Peer(fp="f" * 64, name="Pixel", hosts=["192.168.1.50:47100"]))
    fake = FakeAdb(devices={})
    asked = []
    with pytest.raises(setup.SetupError):
        setup.run(lambda m: None, apk_dir=apk.parent, run=fake.run, runner=fake.runner,
                  link_to_peer=lambda peer: asked.append(peer.name),
                  pair_qr=lambda say, run: (_ for _ in ()).throw(setup.SetupError("QR shown")),
                  pictures=tmp_path / "pics", sleep=lambda s: None)
    assert asked == ["Pixel"]


def test_a_phone_on_the_old_app_is_refused(tmp_path):
    """The old app is no longer supported: setup does not install the new app next to it."""
    new_apk = Path(__file__).parent / "fixtures" / "manifest-app.homalawn.apk"
    fake = FakeAdb(pkg="app.lawnchair.debug")
    with pytest.raises(setup.SetupError, match="no longer supported"):
        _go(fake, new_apk, tmp_path, apk=new_apk)
    assert not any(a[3:5] == ["install", "-r"] for a in fake.log if len(a) > 4)


def test_no_screen_control_leaves_it_off_and_still_ends_ready(tmp_path, apk, proof):
    fake = FakeAdb()
    out: list[str] = []
    link = FakeLink()

    def fake_pair(host, port, *, confirm, say, show_words, **kw):
        peer = Peer(fp="f" * 64, name="Pixel", hosts=[f"{host}:{port}"])
        identity.save_peer(peer)
        return peer
    setup.run(out.append, apk_dir=apk.parent, run=fake.run, runner=fake.runner, pair=fake_pair,
              connect_link=lambda peers, addr: link if peers else None, pair_qr=lambda say, run: pytest.fail("QR"),
              ask=lambda q: pytest.fail(q), pictures=tmp_path / "pics", sleep=lambda s: None,
              reachable=lambda h, p: True, screen_control=False)
    assert fake.settings["enabled_accessibility_services"] == "null" and fake.settings["accessibility_enabled"] == "0"
    assert setup.SCREEN_LEFT_OFF in out and setup.SCREEN_ON not in out
    assert "screen.shot" not in link.calls
    assert out[-1] == "Ready: Pixel is set up and paired."


def test_cli_passes_no_screen_control(monkeypatch):
    from typer.testing import CliRunner
    from homalawn import cli
    seen = {}
    monkeypatch.setattr(setup, "run", lambda say, **kw: seen.update(kw))
    monkeypatch.delenv("HOMALAWN_SERIAL", raising=False)
    r = CliRunner().invoke(cli.app, ["setup", "--no-screen-control"])
    assert r.exit_code == 0, r.output
    assert seen["screen_control"] is False
