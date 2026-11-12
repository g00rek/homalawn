"""`homalawn update` (spec U2) and `homalawn update --download` (U3) against fakes: a fake link phone
in Python for the offer logic, the in-process TLS fake phone for one run over the real wire, and a
real HTTP fetch on 127.0.0.1 for the download server. Nothing touches a device or opens a window."""
from __future__ import annotations

import contextlib
import hashlib
import os
import signal
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

import pytest

from homalawn import update
from homalawn.link.client import LinkDown
from homalawn.transport import ProviderError

APK = bytes(range(256)) * 41  # 10 496 bytes


class FakePhone:
    """The phone's side of launcher.app_info / launcher.update_offer, as agreed for patch 0005:
    chunks appended in order to one file per sha256; `bad_offset` (with `expected`) on a gap or an
    overlap; checks and the notification when the last byte arrives."""
    name = "link"
    has_shell = False

    def __init__(self, installed: bytes = b"old build", *, knows_update=True, refuse: str | None = None,
                 partial: bytes = b"", drop_once_at: int | None = None):
        self.label = "Pixel"
        self.installed_sha = hashlib.sha256(installed).hexdigest()
        self.knows_update = knows_update
        self.refuse = refuse
        self.file = bytearray(partial)
        self.file_sha = hashlib.sha256(APK).hexdigest() if partial else None
        self.drop_once_at = drop_once_at  # the connection breaks after this chunk offset arrived
        self.offers: list[tuple[dict, int]] = []
        self.notified = False

    def call(self, method, params=None):
        if method == update.APP_INFO and self.knows_update:
            return {"package": "app.lawnchair.debug", "versionCode": 15, "versionName": "15.Dev",
                    "sha256": self.installed_sha}
        raise ProviderError(f"unknown_method: unknown method {method}", code="unknown_method")

    def request(self, method, params=None, blob=None):
        if method != update.UPDATE_OFFER or not self.knows_update:
            raise ProviderError(f"unknown_method: unknown method {method}", code="unknown_method")
        assert blob is not None and len(blob) <= update.CHUNK
        self.offers.append((dict(params), len(blob)))
        if params["sha256"] != self.file_sha:
            self.file, self.file_sha = bytearray(), params["sha256"]
        if params["offset"] != len(self.file):
            raise ProviderError("bad_offset: not where the file ends", code="bad_offset",
                                data={"expected": len(self.file)})
        self.file += blob
        if self.drop_once_at == params["offset"]:
            self.drop_once_at = None
            raise LinkDown("the link to Pixel broke during launcher.update_offer: ConnectionResetError")
        if len(self.file) < params["size"]:
            return {"received": len(self.file)}, None
        if self.refuse:
            raise ProviderError(f"{self.refuse}: refused", code=self.refuse)
        if hashlib.sha256(self.file).hexdigest() != params["sha256"]:
            raise ProviderError("bad_hash: mismatch", code="bad_hash")
        self.notified = True
        return {"received": len(self.file), "offered": True}, None


@pytest.fixture
def apk(tmp_path):
    p = tmp_path / "Lawnchair.15.Dev.github.debug.apk"
    p.write_bytes(APK)
    return p


def _send(phone, apk, chunk=4096):
    out: list[str] = []
    sent = update.send(out.append, phone, apk, chunk=chunk)
    return sent, out


# -- the offer -------------------------------------------------------------------------------------
def test_sends_the_apk_in_order_and_says_what_to_tap(apk):
    phone = FakePhone()
    sent, out = _send(phone, apk)
    assert sent == update.NOTIFIED and phone.notified
    assert bytes(phone.file) == APK
    sha = hashlib.sha256(APK).hexdigest()
    assert [(p["offset"], n) for p, n in phone.offers] == [(0, 4096), (4096, 4096), (8192, 2304)]
    assert all(p == {"name": apk.name, "sha256": sha, "size": len(APK), "offset": p["offset"]} for p, _ in phone.offers)
    assert out[-2] == update.TAP
    assert out[-2] == "On the phone: tap the Homa Lawn update notification, then Update."
    assert out[-1] == update.PLAY_PROTECT_LINK  # the scan dialog a new build meets on the phone
    assert "tell me" not in out[-1] and out[-1].startswith("If Google Play Protect shows a dialog on the phone")
    assert "tap " not in out[-1].lower()  # no button names nobody has seen on the phone
    assert any("Pixel" in line and apk.name in line for line in out)


def test_default_chunk_stays_under_the_link_limit():
    from homalawn.link.wire import MAX_BLOB
    assert update.CHUNK == 8 << 20 and update.CHUNK < MAX_BLOB


def test_same_build_on_the_phone_sends_nothing(apk):
    phone = FakePhone(installed=APK)
    sent, out = _send(phone, apk)
    assert sent == update.SAME and phone.offers == []
    assert out == [f"Homa Lawn on Pixel is already this build ({apk.name}); nothing sent."]


def test_a_phone_build_without_the_updater_says_how_to_update_once(apk):
    phone = FakePhone(knows_update=False)
    with pytest.raises(update.CannotUpdate) as e:
        _send(phone, apk)
    assert str(e.value) == ("The Homa Lawn on Pixel cannot update itself yet: update it once with "
                            "`homalawn update --download` or `homalawn setup`.")
    assert phone.offers == []


@pytest.mark.parametrize("code, words", [
    ("not_ours", "not the Homa Lawn app the phone runs"),
    ("wrong_key", "signed with another key"),
    ("older", "newer than this build"),
    ("bad_hash", "arrived damaged"),
])
def test_refusals_are_one_plain_line(apk, code, words):
    phone = FakePhone(refuse=code)
    with pytest.raises(update.UpdateError) as e:
        _send(phone, apk)
    assert words in str(e.value) and "\n" not in str(e.value)
    assert not isinstance(e.value, update.CannotUpdate)


def test_resumes_where_the_phone_says_it_is(apk):
    phone = FakePhone(partial=APK[:4096])
    sent, out = _send(phone, apk)
    assert sent and bytes(phone.file) == APK
    assert [p["offset"] for p, _ in phone.offers] == [0, 4096, 8192]  # 0 refused, then from 4096
    assert any("continuing" in line.lower() for line in out)


def test_a_dropped_connection_costs_one_chunk(apk):
    phone = FakePhone(drop_once_at=4096)
    sent, _ = _send(phone, apk)
    assert sent and bytes(phone.file) == APK
    # the chunk at 4096 arrived before the drop: resent, refused with expected=8192, then continued
    assert [p["offset"] for p, _ in phone.offers] == [0, 4096, 4096, 8192]


def test_a_link_that_stays_down_stops_with_one_line(apk):
    class Down(FakePhone):
        def request(self, method, params=None, blob=None):
            raise LinkDown("Pixel did not answer over the link: 192.168.1.50:47100 (TimeoutError)")
    with pytest.raises(update.UpdateError) as e:
        _send(Down(), apk)
    assert "run `homalawn update` again" in str(e.value)


def test_a_phone_that_jumps_backwards_forever_is_not_a_loop(apk):
    class Stuck(FakePhone):
        def request(self, method, params=None, blob=None):
            raise ProviderError("bad_offset: x", code="bad_offset", data={"expected": 0})
    with pytest.raises(update.UpdateError):
        _send(Stuck(), apk)


def test_bad_offset_without_a_usable_expected_stops(apk):
    class Odd(FakePhone):
        def request(self, method, params=None, blob=None):
            raise ProviderError("bad_offset: x", code="bad_offset", data={"expected": 10 ** 9})
    with pytest.raises(update.UpdateError):
        _send(Odd(), apk)


def test_app_info_on_a_dropped_or_untrusting_link_is_one_line(apk):
    from homalawn.link.client import NotPaired
    for err in (LinkDown("the link to Pixel (192.168.1.50:47100) broke during launcher.app_info: OSError: x."),
                NotPaired("Pixel no longer trusts this computer; run `homalawn unpair`, then `homalawn pair HOST`.")):
        class Broken(FakePhone):
            def call(self, method, params=None, err=err):
                raise err
        with pytest.raises(update.UpdateError) as e:
            _send(Broken(), apk)
        assert str(e.value) == str(err) and "\n" not in str(e.value)


@pytest.mark.parametrize("which", ["not_paired", "pin"])
def test_untrusted_during_the_chunks_stops_at_once(apk, which):
    from homalawn.link.client import NotPaired, PinMismatch
    err = NotPaired("Pixel no longer trusts this computer.") if which == "not_paired" else PinMismatch("pin_mismatch: x")
    tries = []

    class Untrusting(FakePhone):
        def request(self, method, params=None, blob=None):
            tries.append(params["offset"])
            raise err
    with pytest.raises(update.UpdateError) as e:
        _send(Untrusting(), apk)
    assert tries == [0] and str(e.value) == str(err)


def test_a_phone_that_kept_less_than_was_sent_is_continued_where_it_is(apk):
    class Short(FakePhone):
        cut = True

        def request(self, method, params=None, blob=None):
            if self.cut and params["offset"] == 4096:
                self.cut = False
                blob = blob[:1000]  # it stored only part of this chunk
            return super().request(method, params, blob)
    phone = Short()
    sent, _ = _send(phone, apk)
    assert sent and bytes(phone.file) == APK
    assert [p["offset"] for p, _ in phone.offers] == [0, 4096, 5096, 9192]


# -- silent updates (patch 0007) -------------------------------------------------------------------
class SilentPhone(FakePhone):
    """A phone allowed to install its own updates: the last chunk answers `silent` (when given), then
    the app is replaced: app_info fails `down_for` times (the link is down while it restarts) and
    reports the new build from then on, unless `never_back`."""

    def __init__(self, *, silent=True, down_for=3, never_back=False, **kw):
        super().__init__(**kw)
        self.silent = silent
        self.down_for = down_for
        self.never_back = never_back
        self.installed = False
        self.info_calls = 0

    def request(self, method, params=None, blob=None):
        result, b = super().request(method, params, blob)
        if result.get("offered") and self.silent is not None:
            result = dict(result, silent=self.silent)
            self.installed = self.silent and not self.never_back
        return result, b

    def call(self, method, params=None):
        if self.offers and method == update.APP_INFO:
            self.info_calls += 1
            if self.down_for > 0:
                self.down_for -= 1
                raise LinkDown("Pixel did not answer over the link: 192.168.1.50:47100 (ConnectionRefusedError)")
            if self.installed:
                return {"package": "app.lawnchair.debug", "versionCode": 15, "versionName": "15.Dev",
                        "sha256": hashlib.sha256(APK).hexdigest()}
        return super().call(method, params)


class Clock:
    def __init__(self):
        self.t = 0.0
        self.sleeps: list[float] = []

    def __call__(self):
        return self.t

    def sleep(self, s):
        self.sleeps.append(s)
        self.t += s


def _send_silent(phone, apk, wait_s=60):
    out: list[str] = []
    clock = Clock()
    sent = update.send(out.append, phone, apk, chunk=4096, wait_s=wait_s, sleep=clock.sleep, clock=clock)
    return sent, out, clock


def test_silent_update_waits_for_the_new_build_and_says_updated(apk):
    phone = SilentPhone(down_for=3)
    sent, out, clock = _send_silent(phone, apk)
    assert sent == update.INSTALLED and bytes(phone.file) == APK
    assert out[-2:] == ["Updating Pixel in the background; it restarts its home screen in a few seconds.",
                        "Updated."]
    assert update.TAP not in out and update.PLAY_PROTECT_LINK not in out  # nothing to tap
    assert phone.info_calls == 4  # after the offer: 3 while the link is down, then the new sha256
    assert clock.t < 60


def test_without_silent_the_notification_path_stays(apk):
    for silent in (False, None):  # None: an older phone build whose answer has no `silent`
        phone = SilentPhone(silent=silent)
        sent, out, clock = _send_silent(phone, apk)
        assert sent == update.NOTIFIED
        assert out[-2:] == [update.TAP, update.PLAY_PROTECT_LINK]
        assert not any("background" in line or line == "Updated." for line in out)
        assert phone.info_calls == 0 and clock.sleeps == []  # no waiting for a build that waits for a tap


def test_silent_update_that_never_comes_back_stops_after_the_wait(apk):
    phone = SilentPhone(never_back=True, down_for=2)
    with pytest.raises(update.UpdateError) as e:
        _send_silent(phone, apk, wait_s=60)
    assert str(e.value) == ("Pixel did not come back with the new build within 60 seconds; Android may still be "
                            "checking it (Google Play Protect). If the Homa Lawn update notification shows up on the "
                            "phone, tap it, then Update; then run `homalawn update` again to check.")
    assert "\n" not in str(e.value)
    assert phone.info_calls >= 60  # asked about once a second for the whole minute, then gave up


def test_waiting_for_the_build_keeps_its_deadline_across_blocking_calls(apk):
    """A dial or call that blocks (the phone half-gone while it restarts) is capped to the time left,
    and no call starts after the deadline."""
    clock = Clock()
    caps: list[float] = []

    class Blocking:
        label = "Pixel"

        @contextlib.contextmanager
        def capped(self, seconds):
            caps.append(seconds)
            self.cap = seconds
            yield self

        def call(self, method, params=None):
            clock.t += min(25.0, self.cap)  # blocks as long as it is allowed to, at most 25 s
            raise LinkDown("Pixel did not answer over the link: 192.168.1.50:47100 (TimeoutError)")
    assert update.wait_for_build(Blocking(), "0" * 64, seconds=60, sleep=clock.sleep, clock=clock) is False
    assert clock.t <= 60.0 + 1e-9
    assert caps[0] == 60 and all(c <= 60 - i for i, c in enumerate(caps))
    assert caps == sorted(caps, reverse=True) and caps[-1] > 0


def test_link_transport_capped_bounds_its_timeouts_and_restores_them():
    from homalawn.link.client import LinkTransport
    from homalawn.link.identity import Peer
    t = LinkTransport(Peer(fp="f" * 64, name="Pixel", hosts=["192.0.2.1"]), keepalive_s=None)
    before = (t._connect_s, t._hello_s, t._call_s, t._discover)
    with t.capped(2.0):
        assert (t._connect_s, t._hello_s, t._call_s) == (min(before[0], 2.0), 2.0, 2.0) and t._discover is None
    assert (t._connect_s, t._hello_s, t._call_s, t._discover) == before


def test_silent_update_on_a_phone_that_stops_trusting_stops_at_once(apk):
    from homalawn.link.client import NotPaired

    class Forgot(SilentPhone):
        def call(self, method, params=None):
            if self.offers:
                raise NotPaired("Pixel no longer trusts this computer.")
            return super().call(method, params)
    with pytest.raises(update.UpdateError) as e:
        _send_silent(Forgot(), apk)
    assert str(e.value) == "Pixel no longer trusts this computer."


# -- a locked phone, and what the phone reports after the wait (launcher.update_status) -------------
class LockedPhone(SilentPhone):
    """Locked when the last chunk arrives: it keeps the build until the next unlock."""

    def request(self, method, params=None, blob=None):
        result, b = super().request(method, params, blob)
        if result.get("offered"):
            result = dict(result, deferred_until_unlock=True)
            self.installed = False
        return result, b


def test_a_locked_phone_installs_at_the_next_unlock_and_nothing_waits(apk):
    phone = LockedPhone()
    sent, out, clock = _send_silent(phone, apk)
    assert sent == update.DEFERRED
    assert out[-1] == "Pixel is locked; the update installs by itself the next time it is unlocked."
    assert not any("background" in line for line in out)
    assert phone.info_calls == 0 and clock.sleeps == []  # no 60 s wait


class StatusPhone(SilentPhone):
    """Never comes back with the build within the wait; launcher.update_status answers `status`."""

    def __init__(self, status, *, runs_after_status=False, **kw):
        super().__init__(never_back=True, down_for=0, **kw)
        self.status = status
        self.status_calls = 0
        self.runs_after_status = runs_after_status  # app_info reports the new build once status was asked

    def call(self, method, params=None):
        if method == update.UPDATE_STATUS:
            self.status_calls += 1
            if isinstance(self.status, Exception):
                raise self.status
            return dict(self.status)
        if method == update.APP_INFO and self.status_calls and self.runs_after_status:
            return {"package": "app.lawnchair.debug", "versionCode": 15, "versionName": "15.Dev",
                    "sha256": hashlib.sha256(APK).hexdigest()}
        return super().call(method, params)


SHA = hashlib.sha256(APK).hexdigest()


@pytest.mark.parametrize("outcome,line,result", [
    ("pending_user_action", "Pixel needs a tap to install it: tap the Homa Lawn update notification, then Update.",
     update.NOTIFIED),
    ("deferred", "Pixel is locked; the update installs by itself the next time it is unlocked.", update.DEFERRED),
    ("installed", "Updated.", update.INSTALLED),
])
def test_after_the_wait_the_phone_says_what_happened(apk, outcome, line, result):
    phone = StatusPhone({"outcome": outcome, "sha256": SHA, "version": "15.Dev"}, runs_after_status=True)
    sent, out, _ = _send_silent(phone, apk)
    assert sent == result and out[-1] == line and phone.status_calls == 1


def test_after_the_wait_installed_is_said_only_when_the_phone_runs_the_build(apk):
    """The status says installed, but app_info still reports another build (an adb install of another
    build in between, say): no "Updated.", the status outcome instead."""
    phone = StatusPhone({"outcome": "installed", "sha256": SHA})
    out: list[str] = []
    clock = Clock()
    with pytest.raises(update.UpdateError) as e:
        update.send(out.append, phone, apk, chunk=4096, wait_s=60, sleep=clock.sleep, clock=clock)
    assert "Updated." not in out
    assert str(e.value) == ("Pixel reports the update as installed, but it does not run this build now; run "
                            "`homalawn update` again.")


def test_after_the_wait_a_failure_is_one_line_with_the_reason(apk):
    phone = StatusPhone({"outcome": "failed", "sha256": SHA, "reason": "INSTALL_FAILED_VERIFICATION_FAILURE."})
    with pytest.raises(update.UpdateError) as e:
        _send_silent(phone, apk)
    assert str(e.value) == ("Pixel did not install it (INSTALL_FAILED_VERIFICATION_FAILURE). Run `homalawn update` "
                            "again.")
    assert "notification" not in str(e.value)  # only pending_user_action means one is up


def test_after_the_wait_still_installing_says_so(apk):
    phone = StatusPhone({"outcome": "installing", "sha256": SHA})
    with pytest.raises(update.UpdateError) as e:
        _send_silent(phone, apk)
    assert str(e.value).startswith("Pixel has not finished installing it after 60 seconds;")
    assert "\n" not in str(e.value)


@pytest.mark.parametrize("status", [
    {"outcome": "installed", "sha256": "0" * 64},  # about another build: not this one
    {"outcome": "none", "sha256": ""},
    ProviderError("unknown_method: unknown method launcher.update_status", code="unknown_method"),
    LinkDown("Pixel did not answer over the link: 192.168.1.50:47100 (TimeoutError)"),
])
def test_after_the_wait_without_a_usable_status_the_old_line_stays(apk, status):
    phone = StatusPhone(status)
    with pytest.raises(update.UpdateError) as e:
        _send_silent(phone, apk)
    assert str(e.value).startswith("Pixel did not come back with the new build within 60 seconds;")


# -- the same over the real wire -------------------------------------------------------------------
def test_offer_over_a_real_link(tmp_path, apk):
    """One run through LinkTransport and the TLS fake phone: blob frames, error detail (`expected`)."""
    from fake_link import FakeLinkPhone, make_cert
    from homalawn.link import identity
    from homalawn.link.client import LinkTransport
    from homalawn.link.identity import Peer
    from test_session import FakeDevice

    ident = identity.ensure()
    logic = FakePhone(partial=APK[:4096])
    p = FakeLinkPhone(make_cert(tmp_path / "phone"), FakeDevice(tmp_path), trust=[ident.cert],
                      pinned={ident.fingerprint}, name="Pixel")
    p.handlers[update.APP_INFO] = lambda params: (logic.call(update.APP_INFO), None)
    p.handlers[update.UPDATE_OFFER] = lambda params, blob: logic.request(update.UPDATE_OFFER, params, blob)
    t = LinkTransport(Peer(fp=p.fp, name="Pixel", hosts=[p.addr]), discover=lambda: [], keepalive_s=None)
    out: list[str] = []
    try:
        assert update.send(out.append, t, apk, chunk=4096) == update.NOTIFIED
    finally:
        t.close()
        p.close()
    assert bytes(logic.file) == APK and logic.notified
    assert [n for _, m, _, n in p.log if m == update.UPDATE_OFFER] == [4096, 4096, 2304]  # 0 refused (bad_offset, expected 4096), then 4096, 8192


# -- --download ------------------------------------------------------------------------------------
IP_OUT = """1: lo    inet 127.0.0.1/8 scope host lo\\       valid_lft forever preferred_lft forever
2: wlan0    inet 192.168.1.20/24 brd 192.168.1.255 scope global dynamic wlan0\\       valid_lft 80000sec
3: docker0    inet 172.17.0.1/16 brd 172.17.255.255 scope global docker0\\       valid_lft forever
4: tailscale0    inet 100.101.102.103/32 scope global tailscale0\\       valid_lft forever
5: br-1a2b    inet 172.18.0.1/16 scope global br-1a2b\\       valid_lft forever
6: enp3s0    inet 10.0.0.7/24 scope global enp3s0\\       valid_lft forever
7: wwan0    inet 100.70.1.2/10 scope global wwan0\\       valid_lft forever
8: wlan1    inet 169.254.3.4/16 scope link wlan1\\       valid_lft forever
"""


def test_addresses_are_lan_and_tailscale_only():
    import subprocess

    def run(argv, **kw):
        assert argv == ["ip", "-o", "-4", "addr", "show"]
        return subprocess.CompletedProcess(argv, 0, IP_OUT, "")
    assert update.addresses(run=run) == ["192.168.1.20", "10.0.0.7", "100.101.102.103"]


def _get(url, method="GET"):
    req = urllib.request.Request(url, method=method)
    with urllib.request.urlopen(req, timeout=5) as r:
        return r.status, dict(r.headers), r.read()


def test_download_server_serves_exactly_one_file_then_stops(apk):
    log: list[str] = []
    srv = update.DownloadServer(apk, ["127.0.0.1"], log.append)
    url = srv.urls[0]
    sha = hashlib.sha256(APK).hexdigest()
    assert url.startswith("http://127.0.0.1:") and url.endswith(f"/homalawn-{sha[:8]}.apk")
    token = url.split("/")[3]
    assert len(token) >= 16
    done = threading.Event()
    th = threading.Thread(target=lambda: (srv.serve(seconds=30), done.set()), daemon=True)
    th.start()
    status, headers, body = _get(url)
    assert status == 200 and body == APK
    assert headers["Content-Type"] == "application/vnd.android.package-archive"
    assert headers["Content-Disposition"] == f'attachment; filename="homalawn-{sha[:8]}.apk"'
    assert headers["Content-Length"] == str(len(APK))
    base = url.rsplit("/", 2)[0]
    for wrong in (base + "/", base + "/nope/homalawn.apk", url + "x", base + f"/{token}"):
        with pytest.raises(urllib.error.HTTPError) as e:
            _get(wrong)
        assert e.value.code == 404
    assert any("127.0.0.1" in line and "download" in line.lower() for line in log)
    srv.stop()
    assert done.wait(5)
    with pytest.raises(OSError):
        _get(url)


def test_download_server_serves_the_file_it_opened_even_if_it_is_replaced(apk):
    """The release cache may replace or remove its APK while a phone downloads it: the server holds
    the file it opened at start and serves exactly that."""
    srv = update.DownloadServer(apk, ["127.0.0.1"], lambda m: None, grace=60)
    th = threading.Thread(target=lambda: srv.serve(seconds=30), daemon=True)
    th.start()
    try:
        tmp = apk.with_name("new.apk")
        tmp.write_bytes(b"another build")
        os.replace(tmp, apk)                       # what release.download does
        assert _get(srv.urls[0])[2] == APK
        apk.unlink()                               # what its cleanup does
        assert _get(srv.urls[0])[2] == APK
    finally:
        srv.stop()
        th.join(5)


def test_download_server_drops_a_connection_that_sends_nothing(apk, monkeypatch):
    """A device on the network cannot hold the server's threads open by connecting and waiting."""
    import socket
    monkeypatch.setattr(update, "CONNECTION_TIMEOUT_S", 0.3)
    srv = update.DownloadServer(apk, ["127.0.0.1"], lambda m: None)
    th = threading.Thread(target=lambda: srv.serve(seconds=30), daemon=True)
    th.start()
    try:
        host, port = srv.urls[0].split("/")[2].split(":")
        with socket.create_connection((host, int(port)), timeout=5) as c:
            c.settimeout(3)
            t0 = time.monotonic()
            assert c.recv(1) == b""                # closed by the server, not by our 3 s wait
            assert time.monotonic() - t0 < 2.5
    finally:
        srv.stop()
        th.join(5)


def test_download_server_stops_by_itself(apk):
    srv = update.DownloadServer(apk, ["127.0.0.1"], lambda m: None)
    t0 = time.monotonic()
    srv.serve(seconds=0.3)
    assert time.monotonic() - t0 < 5
    with pytest.raises(OSError):
        _get(srv.urls[0])


def test_download_server_stops_shortly_after_a_completed_download(apk):
    log: list[str] = []
    srv = update.DownloadServer(apk, ["127.0.0.1"], log.append, grace=0.3)
    th = threading.Thread(target=lambda: srv.serve(seconds=30), daemon=True)
    th.start()
    assert _get(srv.urls[0])[2] == APK
    th.join(5)
    assert not th.is_alive() and srv.downloaded
    assert any(line.startswith("Downloaded by") for line in log)
    with pytest.raises(OSError):
        _get(srv.urls[0])


def test_download_server_waits_out_the_grace_for_a_retry(apk):
    srv = update.DownloadServer(apk, ["127.0.0.1"], lambda m: None, grace=60)
    th = threading.Thread(target=lambda: srv.serve(seconds=30), daemon=True)
    th.start()
    assert _get(srv.urls[0])[2] == APK
    assert _get(srv.urls[0])[2] == APK       # a retry inside the grace still works
    assert th.is_alive()
    srv.stop()
    th.join(5)


def test_download_says_downloaded_when_it_stopped_after_one(apk, tmp_path):
    out: list[str] = []

    def serve(srv, seconds):
        srv.downloaded = True
    update.download(out.append, apk, hosts=["127.0.0.1"], qr_file=tmp_path / "q.png", open_qr=lambda p: False,
                    run=_fake_run("inactive"), serve=serve)
    assert out[-1] == "Downloaded. Stopped serving."


def test_download_prints_url_and_qr_and_opens_the_picture(apk, tmp_path):
    out: list[str] = []
    opened: list[Path] = []
    qr = tmp_path / "qr.png"
    served = {}

    def serve(srv, seconds):
        served["seconds"] = seconds
        assert qr.exists()
        th = threading.Thread(target=srv.serve, args=(30,), daemon=True)
        th.start()
        status, _, body = _get(srv.urls[0])
        assert body == APK
        srv.stop()
        th.join(5)

    update.download(out.append, apk, hosts=["127.0.0.1"], qr_file=qr,
                    open_qr=lambda p: opened.append(p) or True, serve=serve)
    text = "\n".join(out)
    assert served["seconds"] == 600
    assert opened == [qr] and not qr.exists()  # shown while serving, removed after
    assert "http://127.0.0.1:" in text and "█" in text  # the URL and a terminal QR
    assert "10 minutes" in text
    assert "This address is plain HTTP on your own network, meant for your own builds." in text
    assert ("If Google Play Protect warns about the app, choose to install it anyway; if it does not let you, "
            "run `homalawn setup --new`.") in text  # Play Protect hint
    assert ("On a Samsung phone, if the install is blocked, turn off Auto Blocker for the install: "
            "Settings › Security and privacy › Auto Blocker.") in text  # S23, 2026-10-12: "blocked by auto blocker"


@pytest.mark.parametrize("sig", [signal.SIGTERM, signal.SIGHUP])
def test_download_stopped_by_a_signal_removes_the_qr_picture(apk, tmp_path, sig):
    qr = tmp_path / "qr.png"
    before = signal.getsignal(sig)

    def serve(srv, seconds):
        assert qr.exists()
        os.kill(os.getpid(), sig)
        time.sleep(5)  # the signal handler interrupts this
        pytest.fail("not stopped by the signal")

    with pytest.raises(KeyboardInterrupt):
        update.download(lambda m: None, apk, hosts=["127.0.0.1"], qr_file=qr, open_qr=lambda p: False, serve=serve)
    assert not qr.exists()
    assert signal.getsignal(sig) is before  # the old handler is back


def test_download_without_any_address_says_so(apk, tmp_path):
    with pytest.raises(update.UpdateError) as e:
        update.download(lambda m: None, apk, hosts=[], qr_file=tmp_path / "q.png", open_qr=lambda p: False,
                        serve=lambda srv, seconds: None)
    assert "Wi-Fi" in str(e.value) or "network" in str(e.value)


def _fake_run(active="active", ip=None):
    import subprocess

    def run(argv, **kw):
        if argv[0] == "systemctl":
            return subprocess.CompletedProcess(argv, 0, active + "\n", "")
        return subprocess.CompletedProcess(argv, 0, ip if ip is not None else IP_OUT, "")
    return run


UFW_IP = "3: enp34s0    inet 192.168.1.21/24 brd 192.168.1.255 scope global enp34s0\\ valid_lft forever\n"


def test_download_defaults_to_the_fixed_port_and_takes_an_override(apk, tmp_path):
    assert update.DOWNLOAD_PORT == 47101
    seen = []
    for kw in ({}, {"port": 47102}):
        update.download(lambda m: None, apk, hosts=["127.0.0.1"], qr_file=tmp_path / "q.png", open_qr=lambda p: False,
                        run=_fake_run("inactive"), serve=lambda srv, sec: seen.append(srv.urls[0]), **kw)
    assert ":47101/" in seen[0] and ":47102/" in seen[1]


def test_a_busy_port_is_one_line_naming_port(apk, tmp_path):
    import socket
    with socket.socket() as blocker:
        blocker.bind(("127.0.0.1", 0))
        blocker.listen()
        busy = blocker.getsockname()[1]
        with pytest.raises(update.UpdateError) as e:
            update.download(lambda m: None, apk, hosts=["127.0.0.1"], port=busy, qr_file=tmp_path / "q.png",
                            open_qr=lambda p: False, run=_fake_run("inactive"), serve=lambda s, n: None)
    assert str(e.value) == f"Port {busy} is already in use on this computer; pick another with `--port N`."


def _hint(tmp_path, rules, active="active"):
    f = tmp_path / "user.rules"
    f.write_text(rules)
    return update.firewall_hint("192.168.1.21", 47101, _fake_run(active, UFW_IP), f)


def test_ufw_active_without_a_rule_gives_the_hint_with_the_lan_subnet(tmp_path):
    rules = "-A ufw-user-input -p tcp --dport 9427 -j ACCEPT\n-A ufw-user-input -p udp --dport 47101 -j ACCEPT\n"
    assert _hint(tmp_path, rules) == (
        "The firewall blocks the phone from reaching this computer. Allow it once: sudo ufw allow from "
        "192.168.1.0/24 to any port 47101 proto tcp comment homalawn-download")


@pytest.mark.parametrize("rule", [
    "-A ufw-user-input -p tcp --dport 47101 -j ACCEPT",
    "-A ufw-user-input -p tcp --dport 47101 -s 192.168.0.0/16 -j ACCEPT",
    "-A ufw-user-input -i wlan0 -p tcp --dport 47101 -s 192.168.1.0/24 -j ACCEPT",
])
def test_ufw_with_a_rule_gives_no_hint(tmp_path, rule):
    assert _hint(tmp_path, rule + "\n") is None


def test_ufw_inactive_or_unreadable_gives_no_hint(tmp_path):
    assert _hint(tmp_path, "", active="inactive") is None
    assert update.firewall_hint("192.168.1.21", 47101, _fake_run(), tmp_path / "missing") is None


def test_download_says_the_hint_and_still_serves(apk, tmp_path):
    served = []
    rules = tmp_path / "r"
    rules.write_text("")
    srv_out = []
    import unittest.mock as m
    with m.patch.object(update, "DownloadServer") as ds:
        ds.return_value.size, ds.return_value.urls = 1, ["http://192.168.1.21:47101/x"]
        update.download(srv_out.append, apk, hosts=["192.168.1.21"], qr_file=tmp_path / "q.png",
                        open_qr=lambda p: False, run=_fake_run("active", UFW_IP), ufw_rules=rules,
                        serve=lambda s, n: served.append(1))
    assert served and any("sudo ufw allow from 192.168.1.0/24 to any port 47101" in l for l in srv_out)


# -- which APK -------------------------------------------------------------------------------------
def test_pick_apk(tmp_path, apk):
    assert update.pick_apk(apk, tmp_path / "nothing") == apk
    assert update.pick_apk(None, apk.parent) == apk
    with pytest.raises(update.UpdateError) as e:   # no build here: the release, which cannot be reached here
        update.pick_apk(None, tmp_path / "nothing")
    assert "GitHub could not be reached" in str(e.value)
    with pytest.raises(update.UpdateError):
        update.pick_apk(tmp_path / "missing.apk", apk.parent)


def test_an_old_app_phone_is_refused_before_anything_is_sent():
    """The old app is no longer supported: say so before uploading 18 MB."""
    from pathlib import Path
    new_apk = Path(__file__).parent / "fixtures" / "manifest-app.homalawn.apk"

    class OldPhone:
        label = "Pixel"
        sent = []

        def call(self, method, params=None):
            assert method == update.APP_INFO
            return {"package": "app.lawnchair.debug", "sha256": "0" * 64}

        def request(self, *a):
            self.sent.append(a)
    phone = OldPhone()
    with pytest.raises(update.OldApp, match="no longer supported"):
        update.send(lambda m: None, phone, new_apk)
    with pytest.raises(update.OldApp, match="no longer supported"):
        update.send(lambda m: None, phone, None)
    assert phone.sent == []


def test_another_apps_build_is_refused_before_sending():
    from pathlib import Path
    new_apk = Path(__file__).parent / "fixtures" / "manifest-app.homalawn.apk"

    class TestPhone:
        label = "emu"

        def call(self, method, params=None):
            return {"package": "app.homalawn.debug", "sha256": "0" * 64}
    with pytest.raises(update.UpdateError, match="This build is app.homalawn, but emu runs app.homalawn.debug"):
        update.send(lambda m: None, TestPhone(), new_apk)
