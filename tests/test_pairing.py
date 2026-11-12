"""Desktop pairing against an in-process fake phone (TLS server with an openssl-made cert)."""
import json
import socket
import ssl
import subprocess
import threading

import pytest

from homalawn.link import identity, pairing, sas
from homalawn.link.wire import LinkError


@pytest.fixture
def cfg(tmp_path, monkeypatch):
    monkeypatch.setenv("HOMALAWN_CONFIG", str(tmp_path / "cfg" / "config.toml"))
    return tmp_path


class FakePhone:
    """Answers link.hello / pair_request / pair_wait / unpair like the phone, per `script`."""

    def __init__(self, tmp, outcome="accepted", error=None, wrong_words=False, paired=False, nonce_phone=None,
                 closed=0, hang_up_after_closed=None, sticky=False, unpair_error=False):
        key, cert = tmp / "phone.key", tmp / "phone.pem"
        subprocess.run(["openssl", "req", "-x509", "-newkey", "ec", "-pkeyopt", "ec_paramgen_curve:prime256v1",
                        "-nodes", "-keyout", str(key), "-out", str(cert), "-days", "2", "-subj", "/CN=OmaLawn link"],
                       check=True, capture_output=True)
        self.der = subprocess.run(["openssl", "x509", "-in", str(cert), "-outform", "der"], check=True,
                                  capture_output=True).stdout
        self.ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        self.ctx.load_cert_chain(str(cert), str(key))
        self.outcome, self.error, self.wrong_words, self.paired = outcome, error, wrong_words, paired
        self.bad_nonce = nonce_phone  # sent instead of a real nonce, when set
        self.closed = closed  # this many pair_requests are answered pairing_closed first
        self.hang_up_after_closed = hang_up_after_closed  # close a connection after this many of them
        self.sticky = sticky  # link.unpair answers but the phone keeps trusting this computer
        self.unpair_error = unpair_error  # link.unpair fails
        self.conns = 0
        self.calls = []
        self.conn_of = []  # connection number of each call
        self.srv = socket.create_server(("127.0.0.1", 0))
        self.port = self.srv.getsockname()[1]
        threading.Thread(target=self._loop, daemon=True).start()

    def _loop(self):
        while True:
            try:
                raw, _ = self.srv.accept()
            except OSError:
                return
            threading.Thread(target=self._serve, args=(raw,), daemon=True).start()

    def _serve(self, raw):
        try:
            s = self.ctx.wrap_socket(raw, server_side=True)
        except (ssl.SSLError, OSError):
            return
        try:
            self._requests(s)
        except OSError:
            pass  # the desktop hung up

    def _requests(self, s):
        desktop_der = identity.ensure().cert_der  # the fake does not ask TLS for it
        f = s.makefile("rb")
        nonce_phone = bytes(range(16))
        self.conns += 1
        no, closed_here = self.conns, 0
        for line in f:
            req = json.loads(line)
            m, p = req["method"], req.get("params") or {}
            self.calls.append((m, p))
            self.conn_of.append(no)
            if m == "link.pair_request" and self.closed > 0:
                self.closed -= 1
                closed_here += 1
                out = {"id": req["id"], "error": {"code": "pairing_closed", "message": "pairing is closed"}}
                s.sendall(json.dumps(out).encode() + b"\n")
                if self.hang_up_after_closed and closed_here >= self.hang_up_after_closed:
                    return  # like the phone dropping a stranger after its 30 s
                continue
            if m == "link.hello":  # an unpaired computer learns nothing about the phone
                res = ({"fp": "x", "paired": True, "v": 1, "name": "Fake Pixel", "device": "fake"} if self.paired
                       else {"paired": False, "v": 1})
            elif m == "link.pair_request" and self.error:
                res = None
                err = {"code": self.error, "message": "retry in 9 minutes"}
            elif m == "link.pair_request":
                commit = p["commit"]
                res = {"nonce_phone": nonce_phone.hex() if self.bad_nonce is None else self.bad_nonce,
                       "name": p["name"], "expires_in": 120}
            elif m == "link.pair_reveal":
                nd = bytes.fromhex(p["nonce_desktop"])
                assert sas.commitment(nd).hex() == commit  # revealed nonce matches the commitment
                w = sas.sas(self.der, desktop_der, nonce_phone, nd)
                res = {"words": w[::-1] if self.wrong_words else w}
            elif m == "link.pair_wait" and self.outcome == "hang":
                threading.Event().wait(30)  # the owner never taps
                return
            elif m == "link.pair_wait":
                res = {"accepted": self.outcome == "accepted", "outcome": self.outcome}
                if self.outcome == "accepted":
                    res.update(name="Fake\u202e Pixel\x07", device="fake")  # cleaned before it is stored
            elif m == "link.unpair" and self.unpair_error:
                res = None
                err = {"code": "internal", "message": "cannot forget"}
            elif m == "link.unpair":
                self.paired = self.paired and self.sticky
                res = {"unpaired": True}
            else:
                res = None
                err = {"code": "not_paired", "message": "no"}
            out = {"id": req["id"], "result": res} if res is not None else {"id": req["id"], "error": err}
            s.sendall(json.dumps(out).encode() + b"\n")

    @property
    def fp(self):
        import hashlib
        return hashlib.sha256(self.der).hexdigest()

    def methods(self):
        return [m for m, _ in self.calls]


def run(phone, confirm=True):
    shown = []
    said = []
    peer = pairing.pair("127.0.0.1", phone.port, name="desk", say=said.append,
                        confirm=lambda w: shown.append(w) or confirm)
    return peer, said, shown


def test_accept_and_yes_pins_the_phone(cfg):
    phone = FakePhone(cfg)
    peer, said, shown = run(phone)
    assert peer.fp == phone.fp and peer.name == "Fake Pixel"
    assert peer.hosts == [f"127.0.0.1:{phone.port}"] and peer.last_lan is None
    assert [p.fp for p in identity.load_peers()] == [phone.fp]
    calls = dict(phone.calls)
    req, rev = calls["link.pair_request"], calls["link.pair_reveal"]
    assert req == {"name": "desk", "commit": sas.commitment(bytes.fromhex(rev["nonce_desktop"])).hex()}
    assert "nonce_desktop" not in req  # the nonce only travels after the phone has sent its own
    words_line = next(l for l in said if l.startswith("Words: "))
    assert words_line.split()[1:] == shown[0] and len(shown[0]) == 4
    assert any("Confirm on the phone" in l for l in said)
    assert phone.methods() == ["link.hello", "link.pair_request", "link.pair_reveal", "link.pair_wait"]


def test_accept_but_no_here_unpairs_the_phone_and_saves_nothing(cfg):
    phone = FakePhone(cfg)
    with pytest.raises(pairing.PairError, match="did not match"):
        run(phone, confirm=False)
    assert identity.load_peers() == []
    assert phone.methods()[-1] == "link.unpair"


@pytest.mark.parametrize("outcome,text", [("rejected", "Reject was tapped"), ("timeout", "within 2 minutes")])
def test_reject_or_timeout_saves_nothing(cfg, outcome, text):
    phone = FakePhone(cfg, outcome=outcome)
    with pytest.raises(pairing.PairError, match=text):
        run(phone)
    assert identity.load_peers() == []
    assert "link.unpair" not in phone.methods()


@pytest.mark.parametrize("bad", ["zz" * 16, "00" * 15, 42, {"not": "a string"}, ""])
def test_a_malformed_phone_nonce_is_a_pair_error(cfg, bad):
    phone = FakePhone(cfg, nonce_phone=bad)
    with pytest.raises(pairing.PairError, match="malformed"):
        run(phone)
    assert "link.pair_reveal" not in phone.methods() and identity.load_peers() == []


def test_words_from_the_phone_that_differ_stop_before_waiting(cfg):
    phone = FakePhone(cfg, wrong_words=True)
    with pytest.raises(pairing.PairError, match="different words"):
        run(phone)
    assert "link.pair_wait" not in phone.methods() and identity.load_peers() == []


@pytest.mark.parametrize("code,text", [("notifications_off", "notifications for Homa Lawn are off"),
                                       ("pair_busy", "another pairing request"),
                                       ("rate_limited", "retry in 9 minutes"),
                                       ("local_pairing", "from the phone itself")])
def test_phone_refusals_are_explained(cfg, code, text):
    phone = FakePhone(cfg, error=code)
    with pytest.raises(pairing.PairError, match=text):
        run(phone)
    assert identity.load_peers() == []


def test_already_paired_on_both_sides_is_a_no_op(cfg):
    phone = FakePhone(cfg)
    run(phone)
    phone.paired = True
    peer, said, _ = run(phone)
    assert said == ["Already paired with Fake Pixel."] and peer.fp == phone.fp


def test_phone_paired_but_no_local_record_starts_over(cfg):
    phone = FakePhone(cfg, paired=True)
    peer, said, _ = run(phone)
    assert peer.fp == phone.fp
    assert phone.methods()[:3] == ["link.hello", "link.unpair", "link.hello"]


# The phone may trust other computers too: recovery texts name this computer's own Forget, never
# "Forget all computers" (which unpairs every computer).
def test_a_phone_still_trusting_us_after_forgetting_says_forget_this_computer(cfg):
    phone = FakePhone(cfg, paired=True, sticky=True)
    with pytest.raises(pairing.PairError) as e:
        run(phone)
    assert "Homa Lawn's settings › Computers › desk › Forget" in str(e.value)
    assert "Forget all" not in str(e.value)


def test_words_that_do_not_match_here_and_a_failed_unpair_say_forget_this_computer(cfg):
    phone = FakePhone(cfg, unpair_error=True)
    with pytest.raises(pairing.PairError, match="did not match") as e:
        run(phone, confirm=False)
    assert "Homa Lawn's settings › Computers › desk › Forget" in str(e.value)
    assert "Forget all" not in str(e.value)


def test_cli_unpair_of_an_unreachable_phone_says_forget_this_computer(cfg):
    from typer.testing import CliRunner
    from homalawn import cli
    phone = FakePhone(cfg)
    run(phone)
    phone.unpair_error = True        # nothing answers link.unpair: the phone is not told
    r = CliRunner().invoke(cli.app, ["unpair"])
    assert r.exit_code == 0, r.output
    out = " ".join(r.output.split())
    assert "Homa Lawn's settings › Computers › this computer › Forget" in out, r.output
    assert "Forget all" not in out


def test_unpair_tells_the_phone_and_forgets_locally(cfg):
    phone = FakePhone(cfg)
    peer, _, _ = run(phone)
    assert pairing.unpair(peer) == f"127.0.0.1:{phone.port}"
    assert phone.methods()[-1] == "link.unpair" and identity.load_peers() == []


def test_unpair_never_talks_to_a_device_with_another_fingerprint(cfg):
    phone = FakePhone(cfg)
    peer, _, _ = run(phone)
    identity.remove_peer(peer.fp)
    peer.fp = "00" * 32  # a pin the device at that address does not match
    identity.save_peer(peer)
    before = len(phone.calls)
    assert pairing.unpair(peer) is None
    assert phone.calls[before:] == []  # TLS only, not a single request
    assert identity.load_peers() == []


def test_unreachable_phone_is_reported(cfg):
    s = socket.create_server(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    with pytest.raises(pairing.PairError, match="Cannot reach the phone"):
        pairing.pair("127.0.0.1", port, name="d", say=print, confirm=lambda w: True)


def test_cli_pair_peers_unpair(cfg):
    from typer.testing import CliRunner
    from homalawn import cli
    phone = FakePhone(cfg)
    r = CliRunner().invoke(cli.app, ["phones"])
    assert r.exit_code == 0 and "No phone is paired yet" in r.output
    r = CliRunner().invoke(cli.app, ["pair", f"127.0.0.1:{phone.port}"])
    assert r.exit_code == 0, r.output
    assert "Words: " in r.output and "Confirm on the phone" in r.output and "Paired with Fake Pixel" in r.output
    r = CliRunner().invoke(cli.app, ["phones"])
    assert r.output.startswith("Fake Pixel: paired ") and f"(id {phone.fp[:16]})" in r.output
    assert CliRunner().invoke(cli.app, ["peers"]).output == r.output      # the old name still works
    r = CliRunner().invoke(cli.app, ["unpair"])
    assert r.exit_code == 0 and "forgotten each other" in r.output, r.output
    assert identity.load_peers() == []


def test_the_phone_name_comes_with_the_accept_not_with_hello(cfg):
    phone = FakePhone(cfg)
    peer, said, _ = run(phone)
    assert peer.name == "Fake Pixel"  # from pair_wait, control and format characters removed
    assert not any("Fake" in l for l in said)  # nothing about the phone was known before the accept


def test_unpair_tries_the_phones_addresses_in_dial_order(cfg):
    phone = FakePhone(cfg)
    peer, _, _ = run(phone)
    peer.hosts, peer.last_lan = ["127.0.0.1:1"], None  # the address it paired through is dead
    peer.addresses = [f"127.0.0.1:{phone.port}"]       # one the user added answers
    identity.save_peer(peer)
    assert pairing.unpair(peer) == f"127.0.0.1:{phone.port}"
    assert phone.methods()[-1] == "link.unpair" and identity.load_peers() == []


class Clock:
    """Stands in for time.monotonic / time.sleep: sleeping moves the clock, nothing waits."""

    def __init__(self):
        self.t = 0.0
        self.sleeps = []

    def __call__(self):
        return self.t

    def sleep(self, s):
        self.sleeps.append(s)
        self.t += s


def run_waiting(phone, clock, **kw):
    said = []
    peer = pairing.pair("127.0.0.1", phone.port, name="desk", say=said.append, confirm=lambda w: True,
                        sleep=clock.sleep, clock=clock, **kw)
    return peer, said


def test_pairing_closed_says_once_how_to_open_it_and_asks_again_every_2s(cfg):
    phone = FakePhone(cfg, closed=4)
    clock = Clock()
    peer, said = run_waiting(phone, clock)
    assert peer.fp == phone.fp
    hint = ("On the phone: unlock it and show its home screen (or open Homa Lawn's settings › "
            "Computers › Pair a computer)")
    assert said.count(hint) == 1 and said[0] == hint
    assert clock.sleeps == [2.0] * 4
    assert phone.methods() == ["link.hello"] + ["link.pair_request"] * 5 + ["link.pair_reveal", "link.pair_wait"]
    assert set(phone.conn_of) == {1}  # all on one connection while it lasts


def test_pairing_closed_for_5_minutes_gives_up_and_saves_nothing(cfg):
    phone = FakePhone(cfg, closed=10_000)
    clock = Clock()
    with pytest.raises(pairing.PairError, match="not opened on the phone within 5 minutes"):
        run_waiting(phone, clock)
    assert 295 <= clock.t <= 302 and len(clock.sleeps) == 150
    assert "link.pair_reveal" not in phone.methods() and identity.load_peers() == []


def test_waiting_survives_the_phone_hanging_up_the_idle_connection(cfg):
    phone = FakePhone(cfg, closed=5, hang_up_after_closed=2)
    clock = Clock()
    peer, said = run_waiting(phone, clock)
    assert peer.fp == phone.fp and len(set(phone.conn_of)) == 3  # dialled again twice
    assert sum("show its home screen" in l for l in said) == 1


def test_ctrl_c_while_waiting_stops_cleanly(cfg):
    phone = FakePhone(cfg, closed=10_000)

    def interrupt(_):
        raise KeyboardInterrupt
    with pytest.raises(KeyboardInterrupt):
        pairing.pair("127.0.0.1", phone.port, name="desk", say=lambda m: None, confirm=lambda w: True,
                     sleep=interrupt)
    assert identity.load_peers() == []


def test_cli_ctrl_c_while_waiting_says_nothing_was_paired(cfg, monkeypatch):
    from typer.testing import CliRunner
    from homalawn import cli
    phone = FakePhone(cfg, closed=10_000)

    def interrupt(_):
        raise KeyboardInterrupt
    monkeypatch.setattr(pairing, "_sleep", interrupt)
    r = CliRunner().invoke(cli.app, ["pair", f"127.0.0.1:{phone.port}"])
    assert r.exit_code == 1 and "Stopped. Nothing was paired." in r.output, r.output
    assert "show its home screen" in r.output


def test_phone_trusting_us_without_a_record_and_pairing_closed_repins_after_opening(cfg):
    """Spec item 9: the phone still pins this computer (a half-finished earlier pairing), this
    computer has no record; the phone is asked to forget it, then a full pairing pins both sides."""
    phone = FakePhone(cfg, paired=True, closed=2)
    clock = Clock()
    peer, said = run_waiting(phone, clock)
    assert peer.fp == phone.fp and [p.fp for p in identity.load_peers()] == [phone.fp]
    assert phone.methods()[:3] == ["link.hello", "link.unpair", "link.hello"]
    assert phone.methods()[-2:] == ["link.pair_reveal", "link.pair_wait"]


def test_cli_pair_without_a_terminal_pins_on_the_phone_accept_alone(cfg):
    # owner decision 2026-10-12: Accept on the phone is the only confirmation; nothing asks on this computer
    from typer.testing import CliRunner
    from homalawn import cli
    phone = FakePhone(cfg)
    r = CliRunner().invoke(cli.app, ["pair", f"127.0.0.1:{phone.port}"], input="")
    assert r.exit_code == 0, r.output
    words = next(l for l in r.output.splitlines() if l.startswith("Words: ")).split()[1:]
    assert len(words) == 4 and "[y/N]" not in r.output and "Paired with" in r.output
    assert [p.fp for p in identity.load_peers()] == [phone.fp]


def _hits(*rows):
    from homalawn.link import discovery
    return [discovery.Hit(name="Homa Lawn", address=a, port=port, fp_prefix="", hostname=hn) for a, port, hn in rows]


def test_cli_pair_without_host_uses_the_one_phone_mdns_finds(cfg, monkeypatch):
    from typer.testing import CliRunner
    from homalawn import cli
    from homalawn.link import discovery
    phone = FakePhone(cfg)
    monkeypatch.setattr(discovery, "missing", lambda: False)
    monkeypatch.setattr(discovery, "browse", lambda timeout=3.0: _hits(
        ("fe80::1", phone.port, "Android.local"), ("127.0.0.1", phone.port, "Android.local")))
    r = CliRunner().invoke(cli.app, ["pair"])
    assert r.exit_code == 0, r.output
    assert f"Found the phone at 127.0.0.1:{phone.port}" in r.output and "Paired with Fake Pixel" in r.output


@pytest.mark.parametrize("rows,text", [
    ([], "No phone with Homa Lawn answered on this network"),
    ([("192.168.1.23", 47100, "a.local"), ("192.168.1.40", 47100, "b.local")],
     "Several Homa Lawn phones found: 192.168.1.23:47100 (a.local), 192.168.1.40:47100 (b.local)"),
])
def test_cli_pair_without_host_and_not_exactly_one_phone_says_how_to_pass_host(cfg, monkeypatch, rows, text):
    from typer.testing import CliRunner
    from homalawn import cli
    from homalawn.link import discovery
    monkeypatch.setattr(discovery, "missing", lambda: False)
    monkeypatch.setattr(discovery, "browse", lambda timeout=3.0: _hits(*rows))
    r = CliRunner().invoke(cli.app, ["pair"])
    assert r.exit_code == 1 and text in r.output and "homalawn pair HOST" in r.output, r.output


def test_cli_pair_without_host_and_without_avahi_says_how(cfg, monkeypatch):
    from typer.testing import CliRunner
    from homalawn import cli
    from homalawn.link import discovery
    monkeypatch.setattr(discovery, "missing", lambda: True)
    monkeypatch.setattr(discovery, "browse", lambda timeout=3.0: pytest.fail("browsed without avahi-browse"))
    r = CliRunner().invoke(cli.app, ["pair"])
    assert r.exit_code == 1 and "avahi-browse is not installed" in r.output and "homalawn pair HOST" in r.output
    assert len(r.output.strip().splitlines()) == 1


def test_cli_pair_without_openssl_is_one_sentence(cfg, monkeypatch):
    from typer.testing import CliRunner
    from homalawn import cli
    from homalawn.link import identity
    phone = FakePhone(cfg)
    real = identity.subprocess.run

    def no_openssl(argv, *a, **k):
        if argv[0] == "openssl":
            raise FileNotFoundError(argv[0])
        return real(argv, *a, **k)
    for f in identity.link_dir().glob("*.pem"):
        f.unlink()
    monkeypatch.setattr(identity.subprocess, "run", no_openssl)
    r = CliRunner().invoke(cli.app, ["pair", f"127.0.0.1:{phone.port}"])
    assert r.exit_code == 1 and r.output.strip() == identity.NO_OPENSSL, r.output


def test_stop_ends_waiting_for_pairing_to_open(cfg):
    phone = FakePhone(cfg, closed=10_000)
    stop = threading.Event()
    timer = threading.Timer(0.2, stop.set)
    timer.start()
    with pytest.raises(pairing.PairError) as e:
        pairing.pair("127.0.0.1", phone.port, confirm=lambda w: True, say=lambda m: None,
                     retry_s=0.05, stop=stop)
    timer.join()
    assert "Stopped" in str(e.value)
