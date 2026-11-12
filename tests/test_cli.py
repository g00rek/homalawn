from pathlib import Path

import pytest
from typer.testing import CliRunner
from homalawn import cli, config, ops

PING = {"version": "v", "default_home": True, "db": "d"}

def test_connect_without_host_uses_configured_serial(tmp_path, monkeypatch):
    monkeypatch.setenv("HOMALAWN_CONFIG", str(tmp_path / "c.toml"))
    monkeypatch.setenv("HOMALAWN_SERIAL", "emu-2")
    monkeypatch.setattr(ops, "_single_device", lambda: (_ for _ in ()).throw(AssertionError("scanned")))
    seen = []
    monkeypatch.setattr(ops, "_ping", lambda t: seen.append(t.label) or PING)
    res = CliRunner().invoke(cli.app, ["connect"])
    assert res.exit_code == 0, res.output
    assert seen == ["emu-2"]


def _la_with(monkeypatch, apps):
    from homalawn import apps as apps_mod
    monkeypatch.setattr(apps_mod, "list_apps", lambda d: apps)
    class L:
        class transport: has_shell = True
    return L()


def test_resolve_bare_package(monkeypatch):
    import pytest, typer
    la = _la_with(monkeypatch, [{"package": "p.one", "activity": ".A"},
                                {"package": "p.two", "activity": ".B"}, {"package": "p.two", "activity": ".C"}])
    assert cli._resolve(la, "p.one") == "p.one/.A"
    assert cli._resolve(la, "x/y") == "x/y"
    with pytest.raises(typer.Exit):
        cli._resolve(la, "p.missing")
    with pytest.raises(typer.Exit):
        cli._resolve(la, "p.two")


def _no_guard(monkeypatch):
    import contextlib
    from homalawn.session import Launcher
    monkeypatch.setattr(Launcher, "home_guard", lambda self, require=True, notify=None: contextlib.nullcontext())


def test_widget_add_and_timeout_exit_codes(monkeypatch):
    from homalawn import widgets
    _no_guard(monkeypatch)
    monkeypatch.setenv("HOMALAWN_SERIAL", "emu-2")
    calls = []
    monkeypatch.setattr(widgets, "add", lambda la, p, s, x, y, w, h, wait_s, notify: calls.append(
        (p, s, x, y, w, h, wait_s)) or 7)
    res = CliRunner().invoke(cli.app, ["widget", "add", "a/.W", "--screen", "0", "--x", "1", "--y", "2", "--w", "2"])
    assert res.exit_code == 0, res.output
    assert calls == [("a/.W", 0, 1, 2, 2, None, 120)] and "id 7" in res.output

    def slow(*a, **k):
        raise TimeoutError("widget not ready after 1s")
    monkeypatch.setattr(widgets, "add", slow)
    res = CliRunner().invoke(cli.app, ["widget", "add", "a/.W", "--screen", "0", "--x", "1", "--y", "2"])
    assert res.exit_code == 1 and "not ready" in res.output


def test_widgets_list(monkeypatch):
    from homalawn import widgets
    monkeypatch.setenv("HOMALAWN_SERIAL", "emu-2")
    monkeypatch.setattr(widgets, "list_providers", lambda d: [
        {"provider": "a/.W", "label": "Clock", "cells": [2, 1]}])
    res = CliRunner().invoke(cli.app, ["widgets"])
    assert res.exit_code == 0 and "2x1" in res.output and "a/.W" in res.output and "Clock" in res.output


def test_status_shows_grid(monkeypatch):
    from homalawn.session import Launcher
    monkeypatch.setenv("HOMALAWN_SERIAL", "emu-2")
    monkeypatch.setattr(ops, "_ping", lambda t: PING)
    monkeypatch.setattr(Launcher, "grid", lambda self: (4, 5))
    monkeypatch.setattr(Launcher, "hotseat", lambda self: 4)
    res = CliRunner().invoke(cli.app, ["status"])
    assert res.exit_code == 0 and "Grid 4x5 (columns x rows), dock 4" in res.output
    assert "Home app: yes" in res.output and "True" not in res.output and " db " not in res.output
    res = CliRunner().invoke(cli.app, ["status", "--json"])
    assert res.exit_code == 0 and '"db": "d"' in res.output and '"default_home": true' in res.output


def test_write_refused_when_not_default_home(monkeypatch):
    from homalawn.session import Launcher, HomeError
    import contextlib
    monkeypatch.setenv("HOMALAWN_SERIAL", "emu-2")

    @contextlib.contextmanager
    def refuse(self, require=True, notify=None):
        raise HomeError("Lawnchair is not the default home app on emu-2; make it the default")
        yield
    monkeypatch.setattr(Launcher, "home_guard", refuse)
    res = CliRunner().invoke(cli.app, ["remove", "com.x"])
    assert res.exit_code == 1 and "not the default home" in res.output
    assert res.stderr.strip().splitlines() == ["via adb (emu-2)",
                                               "Lawnchair is not the default home app on emu-2; make it the default"]


def test_undo_prints_snapshot_and_notes(monkeypatch):
    from homalawn.session import Launcher
    _no_guard(monkeypatch)
    monkeypatch.setenv("HOMALAWN_SERIAL", "emu-2")
    monkeypatch.setattr(Launcher, "undo", lambda self, no_backup=False: ("grid-20261001-120000", ["note A"]))
    res = CliRunner().invoke(cli.app, ["undo"])
    assert res.exit_code == 0 and "note A" in res.output and "grid-20261001-120000" in res.output


# -- transport choice (Task 6) -------------------------------------------------------------------
import pytest  # noqa: E402


@pytest.fixture
def paired(tmp_path, monkeypatch):
    """A paired fake phone answering over real TLS on localhost, and an adb serial configured."""
    from fake_link import FakeLinkPhone, make_cert
    from homalawn.link import identity
    from homalawn.link.identity import Peer
    from test_session import FakeDevice
    monkeypatch.setenv("HOMALAWN_CONFIG", str(tmp_path / "cfg" / "config.toml"))
    monkeypatch.delenv("HOMALAWN_SERIAL", raising=False)
    monkeypatch.delenv("HOMALAWN_PHONE", raising=False)
    config.save_serial("emu-2")  # the adb fallback (a named serial would mean adb, never the link)
    monkeypatch.setattr("homalawn.link.discovery.browse", lambda timeout=3.0: [])
    ident = identity.ensure()
    phone = FakeLinkPhone(make_cert(tmp_path / "phone"), FakeDevice(tmp_path), trust=[ident.cert],
                          pinned={ident.fingerprint}, name="Pixel")
    identity.save_peer(Peer(fp=phone.fp, name="Pixel", hosts=[phone.addr]))
    yield phone
    phone.close()


def _adb_ping(monkeypatch, seen=None):
    from homalawn.adb import AdbTransport
    real = AdbTransport.call

    def call(self, method, params=None):
        if seen is not None:
            seen.append(self.serial)
        if method == "launcher.ping":
            return {**PING, "model_loaded": True}
        if method == "launcher.ensure_loaded":
            return {"loaded": True, "db": PING.get("db")}
        return real(self, method, params)
    monkeypatch.setattr(AdbTransport, "call", call)


def _grid(monkeypatch):
    from homalawn.session import Launcher
    monkeypatch.setattr(Launcher, "grid", lambda self: (4, 5))
    monkeypatch.setattr(Launcher, "hotseat", lambda self: 4)


def test_status_uses_the_link_when_paired_and_reachable(paired, monkeypatch):
    _grid(monkeypatch)
    _adb_ping(monkeypatch, seen := [])
    res = CliRunner().invoke(cli.app, ["status"])
    assert res.exit_code == 0, res.output
    assert f"over the link ({paired.addr})" in res.output and "Pixel" in res.output
    assert seen == []  # adb was never asked


def test_status_falls_back_to_adb_and_says_why(paired, monkeypatch):
    _grid(monkeypatch)
    _adb_ping(monkeypatch)
    paired.close()  # the phone is out of reach
    res = CliRunner().invoke(cli.app, ["status"])
    assert res.exit_code == 0, res.output
    assert "over adb (emu-2)" in res.output
    assert "Pixel did not answer over the link" in res.output and "Using adb (emu-2)" in res.output


def test_via_adb_never_dials_the_link(paired, monkeypatch):
    _grid(monkeypatch)
    _adb_ping(monkeypatch)
    res = CliRunner().invoke(cli.app, ["--via", "adb", "status"])
    assert res.exit_code == 0 and "over adb (emu-2)" in res.output
    assert paired.handshakes == 0


def test_via_link_does_not_fall_back(paired, monkeypatch):
    _adb_ping(monkeypatch, seen := [])
    paired.close()
    res = CliRunner().invoke(cli.app, ["--via", "link", "status"])
    assert res.exit_code == 1 and "did not answer over the link" in res.output and seen == []


def test_via_link_without_a_paired_phone_says_pair(tmp_path, monkeypatch):
    res = CliRunner().invoke(cli.app, ["--via", "link", "status"])
    assert res.exit_code == 1 and "homalawn pair" in res.output


def test_a_wrong_certificate_stops_without_adb_fallback(paired, tmp_path, monkeypatch):
    from homalawn.link import identity
    _adb_ping(monkeypatch, seen := [])
    p = identity.load_peers()[0]
    identity.remove_peer(p.fp)
    p.fp = "0" * 64  # the phone answering is not the one we pinned
    identity.save_peer(p)
    res = CliRunner().invoke(cli.app, ["status"])
    assert res.exit_code == 1 and "pin_mismatch" in res.output and seen == []


def test_errors_over_the_link_are_not_called_adb_errors(paired, monkeypatch):
    from homalawn.session import Launcher
    from homalawn.link.client import LinkDown

    def boom(self):
        raise LinkDown("the link to Pixel broke during launcher.layout_export")
    monkeypatch.setattr(Launcher, "get_layout", boom)
    res = CliRunner().invoke(cli.app, ["layout", "get"])
    assert res.exit_code == 1 and res.output.startswith("link failed:") and "adb" not in res.output


def _addresses(fp):
    from homalawn.link import identity
    return next(p for p in identity.load_peers() if p.fp == fp).addresses


def test_connect_host_over_the_link_remembers_it(paired, monkeypatch):
    from homalawn.link import identity
    identity.update_peer(paired.fp, addresses=["pixel-8"])
    _adb_ping(monkeypatch, seen := [])
    real_run = cli.subprocess.run
    monkeypatch.setattr(cli.subprocess, "run", lambda argv, *a, **k: pytest.fail("ran adb connect")
                        if argv[0] == "adb" else real_run(argv, *a, **k))
    res = CliRunner().invoke(cli.app, ["connect", paired.addr])
    assert res.exit_code == 0, res.output
    assert f"via link ({paired.addr})" in res.output
    assert _addresses(paired.fp) == [paired.addr, "pixel-8"] and seen == []
    assert f"Address {paired.addr} added for Pixel." in res.output


def test_connect_host_with_five_addresses_keeps_them(paired, monkeypatch):
    from homalawn.link import identity
    five = [f"pixel-{i}" for i in range(5)]
    identity.update_peer(paired.fp, addresses=five)
    _adb_ping(monkeypatch)
    res = CliRunner().invoke(cli.app, ["connect", paired.addr])
    assert res.exit_code == 0, res.output
    assert _addresses(paired.fp) == five and "Pixel has 5 addresses; remove one first." in res.output
    assert identity.load_peers()[0].last_ok == paired.addr   # still dialled first while it answers


def test_apps_over_the_link(paired):
    res = CliRunner().invoke(cli.app, ["apps"])
    assert res.exit_code == 0, res.output
    assert "com.a/com.a.Main" in res.output and "com.b/com.b.X" in res.output


def test_connect_to_an_address_that_does_not_answer_runs_adb_connect(paired, monkeypatch):
    """The paired phone answers elsewhere (its stored address), but the user named another HOST:
    only HOST is tried over the link, then `adb connect HOST`."""
    import socket
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    ran = []
    real_run = cli.subprocess.run

    def run(argv, *a, **k):
        if argv[0] == "adb":
            ran.append(argv)
            import subprocess
            return subprocess.CompletedProcess(argv, 0, f"connected to 127.0.0.1:{port}\n", "")
        return real_run(argv, *a, **k)
    monkeypatch.setattr(cli.subprocess, "run", run)
    _adb_ping(monkeypatch)
    res = CliRunner().invoke(cli.app, ["connect", f"127.0.0.1:{port}"])
    assert res.exit_code == 0, res.output
    assert ran == [["adb", "connect", f"127.0.0.1:{port}"]]
    assert paired.handshakes == 0 and _addresses(paired.fp) == []
    assert "via adb" in res.output


def test_connect_host_of_the_second_paired_phone(paired, tmp_path, monkeypatch):
    from fake_link import FakeLinkPhone, make_cert
    from homalawn.link import identity
    from homalawn.link.identity import Peer
    from test_session import FakeDevice
    d = tmp_path / "tab"
    d.mkdir()
    ident = identity.ensure()
    tab = FakeLinkPhone(make_cert(tmp_path / "tabcert"), FakeDevice(d), trust=[ident.cert],
                        pinned={ident.fingerprint}, name="Tablet")
    try:
        identity.save_peer(Peer(fp=tab.fp, name="Tablet", hosts=[]))
        res = CliRunner().invoke(cli.app, ["connect", tab.addr])  # Pixel is first in peers.json
        assert res.exit_code == 0, res.output
        assert f"via link ({tab.addr}) to Tablet" in res.output
        assert _addresses(tab.fp) == [tab.addr] and _addresses(paired.fp) == [] and paired.handshakes == 0
    finally:
        tab.close()


# -- which phone (final review C1) ----------------------------------------------------------------
def test_a_named_serial_means_adb_even_with_a_paired_phone(paired, monkeypatch):
    """HOMALAWN_SERIAL=emulator-5560 must never drive the paired real phone."""
    _grid(monkeypatch)
    _adb_ping(monkeypatch, seen := [])
    monkeypatch.setenv("HOMALAWN_SERIAL", "emu-9")
    res = CliRunner().invoke(cli.app, ["status"])
    assert res.exit_code == 0, res.output
    assert "over adb (emu-9)" in res.output and seen and set(seen) == {"emu-9"}
    assert paired.handshakes == 0


def test_serial_option_means_adb_too(paired, monkeypatch):
    _grid(monkeypatch)
    _adb_ping(monkeypatch, seen := [])
    res = CliRunner().invoke(cli.app, ["--serial", "emu-7", "status"])
    assert res.exit_code == 0, res.output
    assert "over adb (emu-7)" in res.output and set(seen) == {"emu-7"} and paired.handshakes == 0


def test_via_link_still_reaches_the_phone_with_a_serial_set(paired, monkeypatch):
    _grid(monkeypatch)
    _adb_ping(monkeypatch, seen := [])
    monkeypatch.setenv("HOMALAWN_SERIAL", "emu-9")
    res = CliRunner().invoke(cli.app, ["--via", "link", "status"])
    assert res.exit_code == 0, res.output
    assert f"over the link ({paired.addr})" in res.output and seen == []


@pytest.fixture
def two_phones(paired, tmp_path):
    from fake_link import FakeLinkPhone, make_cert
    from homalawn.link import identity
    from homalawn.link.identity import Peer
    from test_session import FakeDevice
    d = tmp_path / "tab"
    d.mkdir()
    ident = identity.ensure()
    tab = FakeLinkPhone(make_cert(tmp_path / "tabcert"), FakeDevice(d), trust=[ident.cert],
                        pinned={ident.fingerprint}, name="Tablet")
    identity.save_peer(Peer(fp=tab.fp, name="Tablet", hosts=[tab.addr]))
    yield paired, tab
    tab.close()


def test_two_paired_phones_and_none_named_is_refused(two_phones, monkeypatch):
    pixel, tab = two_phones
    _adb_ping(monkeypatch, seen := [])
    res = CliRunner().invoke(cli.app, ["status"])
    assert res.exit_code == 1, res.output
    assert "Several phones are paired" in res.output and "--phone" in res.output
    assert "Pixel" in res.output and "Tablet" in res.output
    assert pixel.handshakes == 0 and tab.handshakes == 0 and seen == []


def test_phone_option_and_env_pick_one(two_phones, monkeypatch):
    pixel, tab = two_phones
    _grid(monkeypatch)
    res = CliRunner().invoke(cli.app, ["--phone", "Tablet", "status"])
    assert res.exit_code == 0, res.output
    assert f"over the link ({tab.addr})" in res.output and pixel.handshakes == 0
    monkeypatch.setenv("HOMALAWN_PHONE", "Pixel")
    res = CliRunner().invoke(cli.app, ["status"])
    assert res.exit_code == 0, res.output
    assert f"over the link ({pixel.addr})" in res.output


def test_a_named_phone_that_does_not_answer_never_falls_back_to_adb(two_phones, monkeypatch):
    pixel, tab = two_phones
    _adb_ping(monkeypatch, seen := [])
    tab.close()
    res = CliRunner().invoke(cli.app, ["--phone", "Tablet", "status"])
    assert res.exit_code == 1 and "did not answer over the link" in res.output and seen == []


def test_unknown_phone_name_is_refused(two_phones):
    res = CliRunner().invoke(cli.app, ["--phone", "Nokia", "status"])
    assert res.exit_code == 1 and "No paired phone called 'Nokia'" in res.output


def test_every_write_says_which_phone_it_changes(paired, monkeypatch):
    from homalawn.session import Launcher
    _no_guard(monkeypatch)
    monkeypatch.setattr(Launcher, "remove", lambda self, spec: [])
    res = CliRunner().invoke(cli.app, ["remove", "com.x"])
    assert res.exit_code == 0, res.output
    assert res.stderr.splitlines()[0] == "via link (Pixel)" and "via link" not in res.stdout
    monkeypatch.setenv("HOMALAWN_SERIAL", "emu-9")
    res = CliRunner().invoke(cli.app, ["remove", "com.x"])
    assert res.exit_code == 0, res.output
    assert res.stderr.splitlines()[0] == "via adb (emu-9)"


def test_reads_do_not_announce_a_target(paired):
    res = CliRunner().invoke(cli.app, ["apps"])
    assert res.exit_code == 0 and "via link" not in res.stderr


def test_screen_actions_say_which_phone(paired):
    paired.handlers["screen.tap"] = lambda p: ({"x": 1, "y": 2}, None)
    res = CliRunner().invoke(cli.app, ["screen", "tap", "1,2"])
    assert res.exit_code == 0, res.output
    assert res.stderr.splitlines()[0] == "via link (Pixel)"


def test_setup_passes_the_serial_and_prints_each_step(monkeypatch):
    from homalawn import setup
    seen = {}

    def fake_run(say, **kw):
        seen.update(kw)
        say("Phone: emulator-5560 (connected over adb).")
    monkeypatch.setattr(setup, "run", fake_run)
    res = CliRunner().invoke(cli.app, ["setup", "--serial", "emulator-5560"])
    assert res.exit_code == 0, res.output
    assert seen["serial"] == "emulator-5560" and seen["debug_forward"] is False
    assert "Phone: emulator-5560 (connected over adb)." in res.output


def test_setup_takes_the_global_serial_too(monkeypatch):
    from homalawn import setup
    seen = {}
    monkeypatch.setattr(setup, "run", lambda say, **kw: seen.update(kw))
    res = CliRunner().invoke(cli.app, ["--serial", "emulator-5560", "setup"])
    assert res.exit_code == 0, res.output
    assert seen["serial"] == "emulator-5560"


def test_setup_failure_is_one_line_and_exit_1(monkeypatch):
    from homalawn import setup

    def boom(say, **kw):
        raise setup.SetupError("Samsung Auto Blocker is on: turn it off.")
    monkeypatch.setattr(setup, "run", boom)
    res = CliRunner().invoke(cli.app, ["setup"])
    assert res.exit_code == 1
    assert "Samsung Auto Blocker is on: turn it off." in res.output


def test_setup_help_hides_the_debug_forward_option():
    res = CliRunner().invoke(cli.app, ["setup", "--help"])
    assert res.exit_code == 0
    assert "--debug-forward" not in res.output and "--apk" in res.output and "--serial" in res.output


def test_setup_new_goes_to_qr_pairing_and_ignores_homalawn_serial(monkeypatch):
    from homalawn import setup
    seen = {}
    monkeypatch.setenv("HOMALAWN_SERIAL", "emulator-5560")
    monkeypatch.setattr(setup, "run", lambda say, **kw: seen.update(kw))
    res = CliRunner().invoke(cli.app, ["setup", "--new"])
    assert res.exit_code == 0, res.output
    assert seen["new"] is True and seen["serial"] is None


def test_setup_without_new_passes_new_false(monkeypatch):
    from homalawn import setup
    seen = {}
    monkeypatch.setattr(setup, "run", lambda say, **kw: seen.update(kw))
    res = CliRunner().invoke(cli.app, ["setup", "--serial", "emulator-5560"])
    assert res.exit_code == 0, res.output
    assert seen["new"] is False


@pytest.mark.parametrize("argv", [["setup", "--new", "--serial", "emulator-5560"],
                                  ["--serial", "emulator-5560", "setup", "--new"]])
def test_setup_new_with_a_serial_stops(monkeypatch, argv):
    from homalawn import setup
    monkeypatch.setattr(setup, "run", lambda say, **kw: pytest.fail("ran"))
    res = CliRunner().invoke(cli.app, argv)
    assert res.exit_code == 1
    assert "--new" in res.output and "--serial" in res.output


def test_setup_help_names_new():
    res = CliRunner().invoke(cli.app, ["setup", "--help"])
    assert "--new" in res.output and "never seen" in res.output


# -- update (spec U2, U3) ------------------------------------------------------------------------
def _apk(tmp_path):
    p = tmp_path / "L.apk"
    p.write_bytes(b"apk")
    return p


def test_update_sends_over_the_link(tmp_path, monkeypatch):
    from homalawn import update
    sent = []

    class T:
        label = "Pixel"
        def close(self): sent.append("closed")
    monkeypatch.setattr(ops.Session, "transport", lambda self: sent.append(("via", self.via)) or T())
    monkeypatch.setattr(update, "send", lambda say, link, apk: sent.append((link.label, apk.name)) or say(update.TAP))
    res = CliRunner().invoke(cli.app, ["update", "--apk", str(_apk(tmp_path))])
    assert res.exit_code == 0, res.output
    assert ("via", "link") in sent and ("Pixel", "L.apk") in sent
    assert update.TAP in res.output


def test_update_from_an_old_phone_build_says_how(tmp_path, monkeypatch):
    from homalawn import update
    monkeypatch.setattr(ops.Session, "transport", lambda self: object())
    def send(say, link, apk):
        raise update.CannotUpdate(update.OLD_PHONE.format(phone="Pixel"))
    monkeypatch.setattr(update, "send", send)
    res = CliRunner().invoke(cli.app, ["update", "--apk", str(_apk(tmp_path))])
    assert res.exit_code == 1
    assert "homalawn update --download" in res.output


def test_update_without_a_reachable_phone_points_to_download(tmp_path, monkeypatch):
    from homalawn import update
    monkeypatch.setattr(ops.Session, "transport", lambda self: (_ for _ in ()).throw(ops.OpError("No phone is paired; run `homalawn pair` first")))
    monkeypatch.setattr(update, "send", lambda *a: (_ for _ in ()).throw(AssertionError("sent")))
    res = CliRunner().invoke(cli.app, ["update", "--apk", str(_apk(tmp_path))])
    assert res.exit_code == 1
    assert "No phone is paired" in res.output and "homalawn update --download" in res.output


def test_update_via_adb_is_refused(tmp_path, monkeypatch):
    from homalawn import update
    monkeypatch.setattr(update, "send", lambda *a: (_ for _ in ()).throw(AssertionError("sent")))
    res = CliRunner().invoke(cli.app, ["--via", "adb", "update", "--apk", str(_apk(tmp_path))])
    assert res.exit_code == 1 and "homalawn setup" in res.output


def test_update_download_serves_and_never_dials(tmp_path, monkeypatch):
    from homalawn import update
    seen = []
    monkeypatch.setattr(ops.Session, "transport", lambda self: (_ for _ in ()).throw(AssertionError("dialled")))
    monkeypatch.setattr(update, "download", lambda say, apk, port: seen.append((apk.name, port)) or say("Serving"))
    res = CliRunner().invoke(cli.app, ["update", "--download", "--apk", str(_apk(tmp_path))])
    assert res.exit_code == 0, res.output
    assert seen == [("L.apk", update.DOWNLOAD_PORT)] and "Serving" in res.output
    res = CliRunner().invoke(cli.app, ["update", "--download", "--port", "8123", "--apk", str(_apk(tmp_path))])
    assert res.exit_code == 0, res.output
    assert seen[-1] == ("L.apk", 8123)


def test_update_without_a_build_takes_the_release(tmp_path, monkeypatch):
    from homalawn import update
    monkeypatch.setattr(update, "APK_DIR", tmp_path / "none")
    res = CliRunner().invoke(cli.app, ["update", "--download"])     # conftest: GitHub cannot be reached
    assert res.exit_code == 1 and "GitHub could not be reached" in res.output


def test_setup_via_link_runs_the_link_path(monkeypatch):
    from homalawn import setup
    got = {}
    monkeypatch.setattr(setup, "run", lambda say, **kw: got.update(kw))
    for argv in (["setup", "--via", "link"], ["--via", "link", "setup"], ["--phone", "Pixel", "setup", "--via", "link"]):
        got.clear()
        res = CliRunner().invoke(cli.app, argv)
        assert res.exit_code == 0, res.output
        assert got["via"] == "link" and "serial" not in got
    assert got["phone_name"] == "Pixel"


def test_phone_after_setup_and_update_too(tmp_path, monkeypatch):
    from homalawn import setup, update
    got = {}
    monkeypatch.setattr(setup, "run", lambda say, **kw: got.update(kw))
    for argv in (["setup", "--via", "link", "--phone", "Pixel"], ["setup", "--phone", "Pixel"],
                 ["--phone", "Pixel", "setup"]):
        got.clear()
        res = CliRunner().invoke(cli.app, argv)
        assert res.exit_code == 0, res.output
        assert got["phone_name"] == "Pixel"
    sessions = []

    class T:
        label = "Pixel"
    monkeypatch.setattr(ops.Session, "transport", lambda self: sessions.append(self.phone) or T())
    monkeypatch.setattr(update, "send", lambda say, link, apk: None)
    for argv in (["update", "--apk", str(_apk(tmp_path)), "--phone", "Tablet"],
                 ["--phone", "Tablet", "update", "--apk", str(_apk(tmp_path))]):
        sessions.clear()
        res = CliRunner().invoke(cli.app, argv)
        assert res.exit_code == 0, res.output
        assert sessions == ["Tablet"]


def test_setup_via_link_refuses_new_and_serial(monkeypatch):
    from homalawn import setup
    monkeypatch.setattr(setup, "run", lambda say, **kw: (_ for _ in ()).throw(AssertionError("ran")))
    for argv in (["setup", "--via", "link", "--new"], ["setup", "--via", "link", "--serial", "x"]):
        res = CliRunner().invoke(cli.app, argv)
        assert res.exit_code == 1 and "--via link" in res.output


def test_wallpaper_flags_choose_the_target_and_crop(tmp_path, monkeypatch):
    img = tmp_path / "w.png"
    img.write_bytes(b"\x89PNG")
    seen = []
    monkeypatch.setattr(ops, "wallpaper", lambda s, path, **k: seen.append(k) or ops.Result("Wallpaper set.", [], None))
    for args in ([], ["--lock"], ["--both", "--crop", "center"]):
        res = CliRunner().invoke(cli.app, ["wallpaper", str(img), *args])
        assert res.exit_code == 0, res.output
    assert [(k["target"], k["crop"]) for k in seen] == [("home", "none"), ("lock", "none"), ("both", "center")]
    res = CliRunner().invoke(cli.app, ["wallpaper", str(img), "--lock", "--both"])
    assert res.exit_code == 1 and "cannot be used together" in res.output
    res = CliRunner().invoke(cli.app, ["wallpaper", str(img), "--crop", "fill"])
    assert res.exit_code == 1 and "--crop" in res.output
    assert len(seen) == 3


def test_only_the_homalawn_command_is_installed():
    import tomllib
    from homalawn import cli
    scripts = tomllib.loads((Path(__file__).parent.parent / "pyproject.toml").read_text())["project"]["scripts"]
    assert scripts == {"homalawn": "homalawn.cli:app"}
    assert not hasattr(cli, "legacy") and not hasattr(cli, "legacy_homar")


def test_version():
    from importlib.metadata import version
    res = CliRunner().invoke(cli.app, ["--version"])
    assert res.exit_code == 0 and res.output == f"homalawn {version('homalawn')}\n"


def _commands(group=None, path=()):
    """(path, command) for every visible command and group, from Typer's own command tree."""
    import typer.main
    group = group or typer.main.get_command(cli.app)
    ctx = type(group).context_class(group)
    for name in group.list_commands(ctx):
        c = group.get_command(ctx, name)
        if getattr(c, "hidden", False):
            continue
        yield path + (name,), c
        if hasattr(c, "list_commands"):
            yield from _commands(c, path + (name,))


def test_every_command_argument_and_option_has_help():
    missing = []
    for path, c in _commands():
        if not (c.help or "").strip():
            missing.append(" ".join(path))
        for p in c.params:
            if getattr(p, "hidden", False) or p.name in ("help",):
                continue
            if not (getattr(p, "help", None) or "").strip():
                missing.append(f"{' '.join(path)} {p.name}")
    assert missing == []


def test_cells_are_documented_as_counted_from_zero():
    for path in (("place",), ("widget", "add"), ("folder", "create")):
        res = CliRunner().invoke(cli.app, [*path, "--help"])
        assert "counted from 0" in " ".join(res.output.split()), path


def test_no_command_help_uses_old_or_internal_words():
    import re
    pages = " ".join(CliRunner().invoke(cli.app, [*path, "--help"]).output for path, _ in _commands())
    pages += CliRunner().invoke(cli.app, ["--help"]).output
    words = " ".join(pages.split())
    for bad in ("peer", "Lawnchair", "Omarchy look", "fingerprint", "mDNS", " > ", "→"):
        assert bad not in words, bad
    assert not re.search(r"\bdevice\b", words.replace("adb device", ""))


def test_look_apply_and_show_use_this_phones_saved_settings(tmp_path, monkeypatch):
    from homalawn import lookconf
    lookconf.save({"Pixel": lookconf.LookSettings(source="chosen", theme="tokyo-night",
                                                           icon_colors={"tokyo-night": "#112233"})})

    class T:
        label = "Pixel"
    monkeypatch.setattr(ops.Session, "transport", lambda self: T())
    seen = []
    monkeypatch.setattr(ops, "look_apply", lambda s, st, **k: seen.append(st) or ops.Result("Look applied.", [], None))
    res = CliRunner().invoke(cli.app, ["look", "apply"])
    assert res.exit_code == 0 and "Look applied." in res.output
    assert seen[0].theme == "tokyo-night" and seen[0].icon_colors == {"tokyo-night": "#112233"}
    res = CliRunner().invoke(cli.app, ["look", "show"])
    assert res.exit_code == 0 and "Pixel" in res.output and '"theme": "tokyo-night"' in res.output
    T.label = "Other"  # a phone with no saved settings gets the defaults (source off)
    seen.clear()
    assert CliRunner().invoke(cli.app, ["look", "apply"]).exit_code == 0 and seen[0].source == "off"


def test_look_show_uses_fingerprint(monkeypatch, tmp_path):
    from homalawn import lookconf
    from homalawn.link import identity
    monkeypatch.setenv("HOMALAWN_CONFIG", str(tmp_path / "c.toml"))
    fp = "c" * 64
    monkeypatch.setattr(identity, "load_peers", lambda: [identity.Peer(fp=fp, name="Pixel 8")])
    lookconf.save({fp: lookconf.validate({"source": "off"})})

    class T:
        label = "Pixel 8"
        peer = identity.Peer(fp=fp, name="Pixel 8")
    monkeypatch.setattr(cli, "_session", lambda: type("S", (), {"transport": lambda self: T()})())
    name, st = cli._look_settings()
    assert name == "Pixel 8" and st.source == "off"


def test_addresses_add_list_remove(paired):
    from homalawn.link import identity
    run = lambda *a: CliRunner().invoke(cli.app, list(a))
    r = run("addresses", "add", "Pixel-8.ts.net")
    assert r.exit_code == 0 and identity.load_peers()[0].addresses == ["pixel-8.ts.net"]
    identity.update_peer(paired.fp, learned=["100.70.1.2"], last_ok="100.70.1.2")
    r = run("addresses")
    assert r.exit_code == 0, r.output
    assert "pixel-8.ts.net  added" in r.output and "100.70.1.2  learned (Tailscale)  answered last" in r.output
    r = run("addresses", "remove", "pixel-8.ts.net")
    assert r.exit_code == 0 and identity.load_peers()[0].addresses == []
    r = run("addresses", "remove", "nope")
    assert r.exit_code != 0 and "has no address nope" in r.output


def test_addresses_cap_is_an_error_and_phone_is_chosen_by_name(paired):
    from homalawn.link import identity
    identity.update_peer(paired.fp, addresses=[f"pixel-{i}" for i in range(5)])
    r = CliRunner().invoke(cli.app, ["--phone", "pixel", "addresses", "add", "one-more"])
    assert r.exit_code != 0 and "Pixel has 5 addresses; remove one first." in r.output
    assert len(identity.load_peers()[0].addresses) == 5
    r = CliRunner().invoke(cli.app, ["--phone", "nobody", "addresses"])
    assert r.exit_code != 0 and "No paired phone called" in r.output


def test_addresses_two_phones_named_alike_is_a_clear_error(paired):
    from homalawn.link import identity
    identity.save_peer(identity.Peer(fp="b" * 64, name="Pixel"))
    r = CliRunner().invoke(cli.app, ["--phone", "Pixel", "addresses", "add", "x"])
    assert r.exit_code != 0 and "2 phones are named" in r.output


def test_removing_the_last_answered_address_clears_it(paired):
    from homalawn.link import identity
    identity.update_peer(paired.fp, addresses=["pixel-8"], last_ok="pixel-8:47100", last_lan="pixel-8")
    r = CliRunner().invoke(cli.app, ["addresses", "remove", "pixel-8"])
    p = identity.load_peers()[0]
    assert r.exit_code == 0 and (p.addresses, p.last_ok, p.last_lan) == ([], None, None)
    assert "pixel-8" not in CliRunner().invoke(cli.app, ["addresses"]).output.replace(paired.addr, "")


def test_addresses_list_in_dial_order_and_survives_an_odd_last_ok(paired):
    from homalawn.link import identity
    identity.update_peer(paired.fp, addresses=["b-host"], learned=["100.70.1.2"],
                         last_lan="192.168.1.20:47100", last_ok="not a host!")
    r = CliRunner().invoke(cli.app, ["addresses"])
    assert r.exit_code == 0, r.output
    rows = [l.split("  ")[0] for l in r.output.splitlines()[1:5]]
    assert rows == ["not a host!", "192.168.1.20", "b-host", "100.70.1.2"]
    assert "Only added addresses can be removed" in r.output
