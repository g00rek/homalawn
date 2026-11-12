import re
import subprocess

import pytest

from homalawn import adbpair
from homalawn.adbpair import AdbPairError, MdnsService

# Captured 2026-10-13 from the installed adb (platform-tools 37.0.1) with `adb mdns services`
# while the AVD ran; the only line was a real phone's connect service, name and address
# replaced with dummies. Format: header line, then `instance<TAB>service<TAB>host:port`,
# then an empty line.
REAL_CONNECT_ONLY = (
    "List of discovered mdns services\n"
    "adb-TESTSERIAL01-AbCdEf\t_adb-tls-connect._tcp\t192.168.1.50:38957\n"
    "\n"
)
NO_SERVICES = "List of discovered mdns services\n\n"
NAME, PASSWORD = "homalawn-abc123", "pw0123456789"
WITH_PAIRING = (
    "List of discovered mdns services\n"
    "adb-TESTSERIAL01-AbCdEf\t_adb-tls-connect._tcp\t192.168.1.50:38957\n"
    f"{NAME}\t_adb-tls-pairing._tcp\t192.168.1.77:37123\n"
    "\n"
)
NEW_PHONE_CONNECT = (
    "List of discovered mdns services\n"
    "adb-TESTSERIAL01-AbCdEf\t_adb-tls-connect._tcp\t192.168.1.50:38957\n"
    "adb-NEWPHONE02-XyZabc\t_adb-tls-connect._tcp\t192.168.1.77:41111\n"
    "\n"
)
PAIR_OK = "Successfully paired to 192.168.1.77:37123 [guid=adb-NEWPHONE02-XyZabc]\n"
DEVICES_BEFORE = "List of devices attached\nemulator-5560\tdevice\n\n"
DEVICES_AUTO = ("List of devices attached\nemulator-5560\tdevice\n"
                "adb-NEWPHONE02-XyZabc._adb-tls-connect._tcp\tdevice\n\n")


def test_qr_payload_format():
    assert adbpair.qr_payload("homalawn-abc123", "pw") == "WIFI:T:ADB;S:homalawn-abc123;P:pw;;"


def test_new_session_is_random_and_well_formed():
    name, pw = adbpair.new_session()
    assert re.fullmatch(r"homalawn-[A-Za-z0-9]{6}", name)
    assert re.fullmatch(r"[A-Za-z0-9]{12}", pw)
    assert adbpair.new_session() != (name, pw)


def _unrender(text):
    """Turn the printed half-block QR back into a module matrix (True = dark)."""
    import re as _re
    rows = []
    for line in text.splitlines():
        line = _re.sub(r"\x1b\[[0-9;]*m", "", line)
        top = [ch in "█▀" for ch in line]
        bottom = [ch in "█▄" for ch in line]
        rows += [top, bottom]
    return rows


def test_render_qr_dark_on_light_whatever_the_terminal_theme():
    import qrcode
    payload = adbpair.qr_payload(NAME, PASSWORD)
    out = adbpair.render_qr(payload)
    assert adbpair.QR_COLOURS in out and "\x1b[0m" in out  # explicit black-on-white, reset per line
    ref = qrcode.QRCode(border=4)
    ref.add_data(payload)
    matrix = ref.get_matrix()
    assert adbpair.qr_matrix(payload) == matrix
    assert all(not any(r) for r in matrix[:4]) and all(not any(r[:4]) for r in matrix)  # 4-module light quiet zone
    got = _unrender(out)
    assert got[: len(matrix)] == matrix
    assert all(not any(r) for r in got[len(matrix):])  # odd-height padding row is light


def test_parse_real_output():
    assert adbpair.parse_mdns(REAL_CONNECT_ONLY) == [
        MdnsService("adb-TESTSERIAL01-AbCdEf", "_adb-tls-connect._tcp", "192.168.1.50", 38957)]
    assert adbpair.parse_mdns(NO_SERVICES) == []


def test_parse_pairing_line_and_ignores_junk():
    got = adbpair.parse_mdns(WITH_PAIRING + "garbage line\n* daemon started *\n")
    assert MdnsService(NAME, "_adb-tls-pairing._tcp", "192.168.1.77", 37123) in got
    assert len(got) == 2


class FakeAdb:
    """Scripted adb: `script` maps a subcommand key to a list of outputs (last one repeats)."""

    def __init__(self, script):
        self.script = {k: list(v) for k, v in script.items()}
        self.calls: list[list[str]] = []
        self.now = 0.0

    def key(self, argv):
        return " ".join(argv[1:3]) if argv[1] == "mdns" else argv[1]

    def __call__(self, argv, **kw):
        self.calls.append(list(argv))
        outs = self.script[self.key(argv)]
        out = outs.pop(0) if len(outs) > 1 else outs[0]
        return subprocess.CompletedProcess(argv, 0, out, "")

    def sleep(self, s):
        self.now += s

    def clock(self):
        return self.now


def test_pair_by_qr_waits_for_service_pairs_and_returns_serial(monkeypatch):
    monkeypatch.setattr(adbpair, "new_session", lambda: (NAME, PASSWORD))
    fake = FakeAdb({
        "mdns services": [NO_SERVICES, NO_SERVICES, WITH_PAIRING, NEW_PHONE_CONNECT],
        "pair": [PAIR_OK],
        "devices": [DEVICES_BEFORE, DEVICES_AUTO],
    })
    said = []
    serial = adbpair.pair_by_qr(said.append, run=fake, sleep=fake.sleep, clock=fake.clock)
    assert serial == "adb-NEWPHONE02-XyZabc._adb-tls-connect._tcp"
    assert ["adb", "pair", "192.168.1.77:37123", PASSWORD] in fake.calls
    assert sum(c[1:3] == ["mdns", "services"] for c in fake.calls) >= 3
    assert any(adbpair.qr_payload(NAME, PASSWORD) in s or "█" in s or "▀" in s for s in said)
    assert all(c[0] == "adb" for c in fake.calls)
    assert not any(c[1] == "connect" for c in fake.calls)


def test_pair_by_qr_connects_when_adb_did_not(monkeypatch):
    monkeypatch.setattr(adbpair, "new_session", lambda: (NAME, PASSWORD))
    connected = ("List of devices attached\nemulator-5560\tdevice\n"
                 "192.168.1.77:41111\tdevice\n\n")
    fake = FakeAdb({
        "mdns services": [WITH_PAIRING, NEW_PHONE_CONNECT],
        "pair": [PAIR_OK],
        "devices": [DEVICES_BEFORE] * 6 + [connected],
        "connect": ["connected to 192.168.1.77:41111\n"],
    })
    serial = adbpair.pair_by_qr(lambda s: None, run=fake, sleep=fake.sleep, clock=fake.clock)
    assert serial == "192.168.1.77:41111"
    assert ["adb", "connect", "192.168.1.77:41111"] in fake.calls
    assert all(c[0] == "adb" for c in fake.calls)


def test_pair_by_qr_ignores_other_instances(monkeypatch):
    monkeypatch.setattr(adbpair, "new_session", lambda: (NAME, PASSWORD))
    other = WITH_PAIRING.replace(NAME, "homalawn-zzzzzz")
    fake = FakeAdb({"mdns services": [other], "pair": [PAIR_OK], "devices": [DEVICES_BEFORE]})
    with pytest.raises(AdbPairError):
        adbpair.pair_by_qr(lambda s: None, timeout_s=5, run=fake, sleep=fake.sleep, clock=fake.clock)
    assert not any(c[1] == "pair" for c in fake.calls)


def test_pair_by_qr_timeout_plain_message(monkeypatch):
    monkeypatch.setattr(adbpair, "new_session", lambda: (NAME, PASSWORD))
    fake = FakeAdb({"mdns services": [NO_SERVICES], "devices": [DEVICES_BEFORE]})
    with pytest.raises(AdbPairError, match="same Wi-Fi") as e:
        adbpair.pair_by_qr(lambda s: None, timeout_s=10, run=fake, sleep=fake.sleep, clock=fake.clock)
    assert "Pair device with QR code" in str(e.value)
    polls = sum(c[1:3] == ["mdns", "services"] for c in fake.calls)
    assert 8 <= polls <= 12  # about once a second
    assert all(c[0] == "adb" for c in fake.calls)


def test_pair_by_qr_pair_failure_is_plain(monkeypatch):
    monkeypatch.setattr(adbpair, "new_session", lambda: (NAME, PASSWORD))
    fake = FakeAdb({"mdns services": [WITH_PAIRING], "pair": ["Failed: Wrong password or connection was dropped.\n"],
                    "devices": [DEVICES_BEFORE]})
    with pytest.raises(AdbPairError, match="Wireless debugging code"):
        adbpair.pair_by_qr(lambda s: None, run=fake, sleep=fake.sleep, clock=fake.clock)


def test_pair_by_qr_device_never_appears(monkeypatch):
    monkeypatch.setattr(adbpair, "new_session", lambda: (NAME, PASSWORD))
    fake = FakeAdb({"mdns services": [WITH_PAIRING, NEW_PHONE_CONNECT], "pair": [PAIR_OK],
                    "devices": [DEVICES_BEFORE], "connect": ["failed to connect to 192.168.1.77:41111\n"]})
    with pytest.raises(AdbPairError, match="The phone accepted the code but did not connect"):
        adbpair.pair_by_qr(lambda s: None, run=fake, sleep=fake.sleep, clock=fake.clock)
    connects = [c for c in fake.calls if c[1] == "connect"]
    assert connects == [["adb", "connect", "192.168.1.77:41111"]]  # once per address
    assert fake.now <= 120 + 25


def test_pair_by_qr_rejects_failed_line_containing_success_words(monkeypatch):
    # adb client/adb_wifi.cpp:244 — a failure that contains "Successfully paired", exit status 0.
    monkeypatch.setattr(adbpair, "new_session", lambda: (NAME, PASSWORD))
    fake = FakeAdb({"mdns services": [WITH_PAIRING],
                    "pair": ["Failed: Successfully paired but server returned unknown response=3\n"],
                    "devices": [DEVICES_BEFORE]})
    with pytest.raises(AdbPairError, match="refused the Wireless debugging code"):
        adbpair.pair_by_qr(lambda s: None, run=fake, sleep=fake.sleep, clock=fake.clock)
    assert not any(c[1] in ("devices", "connect") for c in fake.calls)


def test_pair_by_code_rejects_error_lines():
    out = "error: no devices/emulators found\nSuccessfully paired to 1.2.3.4:5 [guid=x]\n"
    with pytest.raises(AdbPairError):
        adbpair.pair_by_code("1.2.3.4:5", "123456", run=FakeAdb({"pair": [out]}))


def test_timeout_message_hides_the_code():
    def run(argv, **kw):
        raise subprocess.TimeoutExpired(argv, 30)
    with pytest.raises(AdbPairError) as e:
        adbpair.pair_by_code("1.2.3.4:5", "987654", run=run)
    assert "987654" not in str(e.value) and "1.2.3.4" not in str(e.value)
    assert "pair" in str(e.value)


def test_ipv6_hosts_keep_brackets(monkeypatch):
    monkeypatch.setattr(adbpair, "new_session", lambda: (NAME, PASSWORD))
    v6 = ("List of discovered mdns services\n"
          f"{NAME}\t_adb-tls-pairing._tcp\t[fe80::1]:37123\n"
          "adb-NEWPHONE02-XyZabc\t_adb-tls-connect._tcp\t[fe80::1]:41111\n\n")
    assert adbpair.parse_mdns(v6)[0].host == "fe80::1"
    connected = "List of devices attached\n[fe80::1]:41111\tdevice\n\n"
    fake = FakeAdb({"mdns services": [v6], "pair": ["Successfully paired to [fe80::1]:37123 [guid=adb-NEWPHONE02-XyZabc]\n"],
                    "devices": [DEVICES_BEFORE] * 6 + [connected], "connect": ["connected\n"]})
    serial = adbpair.pair_by_qr(lambda s: None, run=fake, sleep=fake.sleep, clock=fake.clock)
    assert ["adb", "pair", "[fe80::1]:37123", PASSWORD] in fake.calls
    assert ["adb", "connect", "[fe80::1]:41111"] in fake.calls
    assert serial == "[fe80::1]:41111"


def test_pair_by_code():
    fake = FakeAdb({"pair": [PAIR_OK]})
    adbpair.pair_by_code("192.168.1.77:37123", "123456", run=fake)
    assert fake.calls == [["adb", "pair", "192.168.1.77:37123", "123456"]]
    bad = FakeAdb({"pair": ["Failed: Wrong password or connection was dropped.\n"]})
    with pytest.raises(AdbPairError):
        adbpair.pair_by_code("192.168.1.77:37123", "000000", run=bad)


def test_mdns_available():
    ok = FakeAdb({"mdns check": ["mdns daemon version [adb discovery 0.0.0]\n"]})
    assert adbpair.mdns_available(run=ok) is True
    assert ok.calls == [["adb", "mdns", "check"]]
    bad = FakeAdb({"mdns check": ["ERROR: mdns daemon unavailable\n"]})
    assert adbpair.mdns_available(run=bad) is False


def test_missing_adb_is_plain():
    def run(argv, **kw):
        raise FileNotFoundError(argv[0])
    assert adbpair.mdns_available(run=run) is False
    with pytest.raises(AdbPairError, match="adb"):
        adbpair.pair_by_code("1.2.3.4:5", "123456", run=run)


# -- the QR as a picture (final review I3) ---------------------------------------------------------
def test_qr_png_goes_to_the_runtime_dir_else_pictures(tmp_path):
    rt = tmp_path / "run"
    rt.mkdir()
    assert adbpair.qr_png_path({"XDG_RUNTIME_DIR": str(rt)}, home=tmp_path) == rt / "homalawn-pair-qr.png"
    assert adbpair.qr_png_path({}, home=tmp_path) == tmp_path / "Pictures" / "homalawn-pair-qr.png"
    assert adbpair.qr_png_path({"XDG_RUNTIME_DIR": str(tmp_path / "gone")}, home=tmp_path) == \
        tmp_path / "Pictures" / "homalawn-pair-qr.png"


def test_qr_png_has_exactly_the_qr_modules(tmp_path):
    """The picture is the same code as the terminal one: every module's centre pixel matches."""
    from PIL import Image
    payload = adbpair.qr_payload(NAME, PASSWORD)
    path = adbpair.save_qr_png(payload, tmp_path / "sub" / "qr.png")
    m = adbpair.qr_matrix(payload)
    with Image.open(path) as img:
        assert img.format == "PNG"
        img = img.convert("L")
        box = img.size[0] // len(m)
        assert img.size == (len(m) * box, len(m) * box) and box >= 8
        for y, row in enumerate(m):
            for x, dark in enumerate(row):
                v = img.getpixel((x * box + box // 2, y * box + box // 2))
                assert (v < 128) == dark, (x, y)


def test_open_on_desktop_only_with_a_desktop_session(tmp_path):
    calls = []
    popen = lambda argv, **kw: calls.append((argv, kw))
    p = tmp_path / "qr.png"
    assert adbpair.open_on_desktop(p, env={}, popen=popen) is False
    assert calls == []
    assert adbpair.open_on_desktop(p, env={"WAYLAND_DISPLAY": "wayland-1"}, popen=popen) is True
    assert adbpair.open_on_desktop(p, env={"DISPLAY": ":0"}, popen=popen) is True
    assert [c[0] for c in calls] == [["xdg-open", str(p)]] * 2
    assert calls[0][1]["start_new_session"] is True

    def missing(argv, **kw):
        raise FileNotFoundError("xdg-open")
    assert adbpair.open_on_desktop(p, env={"DISPLAY": ":0"}, popen=missing) is False


def test_pair_by_qr_saves_opens_and_deletes_the_picture_after_pairing(monkeypatch, tmp_path):
    monkeypatch.setattr(adbpair, "new_session", lambda: (NAME, PASSWORD))
    qr = tmp_path / "homalawn-pair-qr.png"
    fake = FakeAdb({
        "mdns services": [NO_SERVICES, WITH_PAIRING, NEW_PHONE_CONNECT],
        "pair": [PAIR_OK],
        "devices": [DEVICES_AUTO],
    })
    seen_before_pair = []

    def run(argv, **kw):
        if argv[1] == "pair":
            seen_before_pair.append(qr.is_file())
        return fake(argv, **kw)
    opened = []
    said = []
    serial = adbpair.pair_by_qr(said.append, run=run, sleep=fake.sleep, clock=fake.clock, qr_file=qr,
                                open_qr=lambda p: opened.append(p) or True)
    assert serial == "adb-NEWPHONE02-XyZabc._adb-tls-connect._tcp"
    assert opened == [qr] and seen_before_pair == [True]
    assert f"QR code also saved to {qr} and opened on your screen." in said
    assert not qr.exists()


def test_pair_by_qr_deletes_the_picture_on_timeout(monkeypatch, tmp_path):
    monkeypatch.setattr(adbpair, "new_session", lambda: (NAME, PASSWORD))
    qr = tmp_path / "homalawn-pair-qr.png"
    fake = FakeAdb({"mdns services": [NO_SERVICES], "devices": [DEVICES_BEFORE]})
    said = []
    with pytest.raises(AdbPairError):
        adbpair.pair_by_qr(said.append, timeout_s=5, run=fake, sleep=fake.sleep, clock=fake.clock, qr_file=qr,
                           open_qr=lambda p: False)
    assert f"QR code also saved to {qr}." in said
    assert not qr.exists()
