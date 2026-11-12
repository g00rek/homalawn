"""LinkTransport against an in-process fake phone (real TLS on localhost, openssl-made certs)."""
import json
import socket
import time

import pytest

from fake_link import FakeLinkPhone, make_cert
from homalawn.link import discovery, identity
from homalawn.link.client import LinkDown, LinkTransport, NotPaired, PinMismatch
from homalawn.link.identity import Peer
from homalawn.transport import NotSupported, ProviderError, Transport
from test_session import DB, FakeDevice


@pytest.fixture(scope="session")
def certs(tmp_path_factory):
    d = tmp_path_factory.mktemp("certs")
    return {"phone": make_cert(d, "phone"), "impostor": make_cert(d, "impostor")}


@pytest.fixture
def ident(tmp_path, monkeypatch):
    monkeypatch.setenv("HOMALAWN_CONFIG", str(tmp_path / "cfg" / "config.toml"))
    return identity.ensure()


@pytest.fixture
def phone(certs, ident, tmp_path):
    p = FakeLinkPhone(certs["phone"], FakeDevice(tmp_path), trust=[ident.cert], pinned={ident.fingerprint})
    yield p
    p.close()


def _sub(tmp_path):
    d = tmp_path / "impostor"
    d.mkdir()
    return d


def peer_of(p, hosts=None, last_lan=None):
    return Peer(fp=p.fp, name="Fake Pixel", hosts=list(hosts if hosts is not None else [p.addr]), last_lan=last_lan)


def link(peer, hosts=None, **kw):
    kw.setdefault("discover", lambda: [])
    kw.setdefault("sweep", lambda: [])
    kw.setdefault("keepalive_s", None)
    return LinkTransport(peer, hosts, **kw)


def closed_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


def test_fingerprint_is_this_computers_not_the_phones(phone, ident):
    t = link(peer_of(phone))
    assert t.fingerprint == ident.fingerprint != phone.fp
    t.close()


# -- pin -----------------------------------------------------------------------------------------
def test_pin_match_connects_and_answers(phone):
    t = link(peer_of(phone))
    assert isinstance(t, Transport) and t.name == "link" and t.has_shell is False and t.label == "Fake Pixel"
    assert t.call("launcher.ping")["db"] == "launcher_5_4_4.db"
    assert t.address == phone.addr
    assert phone.methods() == ["link.hello", "launcher.ping"]
    t.close()


def test_pin_mismatch_is_a_hard_error_and_no_other_host_is_tried(certs, ident, phone, tmp_path):
    impostor = FakeLinkPhone(certs["impostor"], FakeDevice(_sub(tmp_path)), trust=[ident.cert], pinned={ident.fingerprint})
    try:
        t = link(peer_of(phone, hosts=[impostor.addr, phone.addr]))
        with pytest.raises(PinMismatch) as e:
            t.call("launcher.ping")
        assert e.value.code == "pin_mismatch" and "pin_mismatch" in str(e.value) and "\n" not in str(e.value)
        assert impostor.log == []          # nothing was sent to the wrong certificate, not even hello
        assert phone.handshakes == 0       # and the real address after it was never dialled
    finally:
        impostor.close()


# -- addresses -----------------------------------------------------------------------------------
def test_candidates_order(monkeypatch):
    from homalawn.link import addresses
    monkeypatch.setattr(addresses, "local_tailscale", lambda: True)
    p = identity.Peer(fp="a" * 64, name="P", hosts=["192.168.1.9:47100"], last_lan="192.168.1.20:47100",
                      addresses=["pixel-8", "fd7a:115c:a1e0::5", "host.lan:1234"],
                      learned=["100.70.1.2"], last_ok="100.70.1.2:47100")
    assert LinkTransport(p).candidates() == ["100.70.1.2:47100", "192.168.1.20:47100", "pixel-8:47100",
                                             "[fd7a:115c:a1e0::5]:47100", "host.lan:1234", "192.168.1.9:47100"]


def test_learned_skipped_without_local_tailscale(monkeypatch):
    from homalawn.link import addresses
    monkeypatch.setattr(addresses, "local_tailscale", lambda: False)
    p = identity.Peer(fp="a" * 64, name="P", learned=["100.70.1.2"])
    assert LinkTransport(p).candidates() == []


def test_hosts_argument_is_only_for_only_hosts(phone):
    with pytest.raises(ValueError):
        LinkTransport(peer_of(phone), ["127.0.0.2:1"])


def test_falls_back_past_a_dead_and_a_silent_host(phone):
    dead = f"127.0.0.1:{closed_port()}"
    silent = socket.create_server(("127.0.0.1", 0))  # completes TCP, never speaks TLS
    try:
        silent_addr = f"127.0.0.1:{silent.getsockname()[1]}"
        t = link(peer_of(phone, hosts=[dead, silent_addr, phone.addr]), hello_s=0.5)
        t0 = time.monotonic()
        t.connect()
        took = time.monotonic() - t0
        assert t.address == phone.addr and took < 3
        assert [a.split(" ")[0] for a in t.tried] == [dead, silent_addr]
    finally:
        silent.close()
        t.close()


def test_mdns_hits_come_last_and_only_with_the_peers_fingerprint(phone):
    asked = []
    other = discovery.Hit("Other", "127.0.0.1", closed_port(), "0123456789abcdef")
    mine = discovery.Hit("Fake Pixel", "127.0.0.1", phone.port, phone.fp[:16])

    def discover():
        asked.append(1)
        return [other, mine]
    dead = f"127.0.0.1:{closed_port()}"
    t = link(peer_of(phone, hosts=[dead]), discover=discover)
    t.connect()
    assert asked == [1] and t.address == phone.addr
    assert [a.split(" ")[0] for a in t.tried] == [dead]  # the other phone's hit was never dialled
    t.close()


def test_another_phone_at_an_mdns_hint_is_skipped_not_a_hard_stop(certs, ident, phone, tmp_path):
    """mDNS carries no identity: a phone with another certificate answering a hint is just not
    this phone; the next hint is tried (a configured address with another certificate still stops)."""
    stranger = FakeLinkPhone(certs["impostor"], FakeDevice(_sub(tmp_path)), trust=[ident.cert], pinned={ident.fingerprint})
    try:
        hits = [discovery.Hit("OmaLawn", "127.0.0.1", stranger.port, ""),
                discovery.Hit("OmaLawn (2)", "127.0.0.1", phone.port, "")]
        dead = f"127.0.0.1:{closed_port()}"
        t = link(peer_of(phone, hosts=[dead]), discover=lambda: hits)
        t.connect()
        assert t.address == phone.addr and stranger.log == []
        assert any("another phone" in a for a in t.tried)
        t.close()
    finally:
        stranger.close()


def test_mdns_is_not_asked_when_a_configured_host_answers(phone):
    t = link(peer_of(phone), discover=lambda: pytest.fail("asked mDNS"))
    t.connect()
    t.close()


def test_nothing_answers_is_one_line_naming_every_address(phone):
    a, b = f"127.0.0.1:{closed_port()}", f"127.0.0.1:{closed_port()}"
    t = link(peer_of(phone, hosts=[a, b]))
    with pytest.raises(LinkDown) as e:
        t.call("launcher.ping")
    assert a in str(e.value) and b in str(e.value) and "\n" not in str(e.value)


def test_a_lan_address_that_answers_is_remembered(phone, monkeypatch):
    monkeypatch.setattr(identity, "lan_address", lambda h, p: f"192.168.5.5:{p}")
    peer = peer_of(phone)
    identity.save_peer(peer)
    link(peer).connect()
    assert identity.load_peers()[0].last_lan == f"192.168.5.5:{phone.port}"


def test_the_address_that_answered_is_remembered_first(phone):
    dead = f"127.0.0.1:{closed_port()}"
    peer = peer_of(phone, hosts=[dead, phone.addr])
    identity.save_peer(peer)
    t = link(peer)
    t.connect()
    t.close()
    stored = identity.load_peers()[0]
    assert stored.last_ok == phone.addr and t.candidates()[0] == phone.addr
    t2 = link(stored)
    t2.connect()
    assert t2.tried == [] and t2.address == phone.addr   # the dead address was not dialled first
    t2.close()


def test_remembering_keeps_addresses_edited_meanwhile(phone):
    """The transport writes only what it learned (last_ok, last_lan), never its whole old copy."""
    peer = peer_of(phone)
    identity.save_peer(peer)
    t = link(peer)
    edited = identity.load_peers()[0]
    edited.addresses = ["pixel-8"]
    identity.save_peer(edited)                      # e.g. `homalawn connect` from another process
    t.connect()
    t.close()
    stored = identity.load_peers()[0]
    assert stored.addresses == ["pixel-8"] and stored.last_ok == phone.addr


def test_a_phone_removed_meanwhile_is_not_written_back(phone):
    peer = peer_of(phone)
    t = link(peer)
    t.connect()
    t.close()
    assert identity.load_peers() == [] and peer.last_ok == phone.addr


def _hello_with(phone, addrs):
    body = {"paired": True, "v": 1, "name": "P", "fp": phone.fp}
    if addrs is not None:
        body["addresses"] = addrs
    phone.handlers["link.hello"] = lambda p: (body, None)


def test_hello_addresses_learn_tailscale_only(phone):
    peer = peer_of(phone)
    identity.save_peer(peer)
    _hello_with(phone, ["192.168.1.5", "100.70.1.2", "fd7a:115c:a1e0:0::9", "fe80::1", "100.70.1.2", 7])
    t = link(peer)
    t.call("link.ping")
    t.close()
    assert identity.load_peers()[0].learned == ["100.70.1.2", "fd7a:115c:a1e0::9"]


def test_learning_replaces_the_list_and_keeps_the_rest(phone):
    peer = peer_of(phone)
    peer.learned = ["100.64.0.9"]
    identity.save_peer(peer)
    t = link(peer)
    edited = identity.load_peers()[0]
    edited.addresses = ["pixel-8"]
    identity.save_peer(edited)                      # another process, meanwhile
    _hello_with(phone, ["100.70.1.2"])
    t.call("link.ping")
    t.close()
    stored = identity.load_peers()[0]
    assert stored.learned == ["100.70.1.2"] and stored.addresses == ["pixel-8"]


def test_learned_is_capped(phone):
    peer = peer_of(phone)
    identity.save_peer(peer)
    _hello_with(phone, [f"100.70.1.{i}" for i in range(1, 9)])
    t = link(peer)
    t.call("link.ping")
    t.close()
    assert identity.load_peers()[0].learned == [f"100.70.1.{i}" for i in range(1, 5)]


def test_a_hello_without_addresses_learns_nothing(phone):
    peer = peer_of(phone)
    peer.learned = ["100.64.0.9"]
    identity.save_peer(peer)
    _hello_with(phone, None)
    t = link(peer)
    t.call("link.ping")
    t.close()
    assert identity.load_peers()[0].learned == ["100.64.0.9"]


def test_an_unchanged_report_writes_nothing(phone, monkeypatch):
    peer = peer_of(phone)
    peer.learned = ["100.70.1.2"]
    identity.save_peer(peer)
    _hello_with(phone, ["100.70.1.2", "192.168.1.5"])
    calls = []
    real = identity.update_peer
    monkeypatch.setattr(identity, "update_peer", lambda fp, **kw: calls.append(kw) or real(fp, **kw))
    t = link(peer)
    t.connect()
    t.close()
    assert [c for c in calls if "learned" in c] == []


# -- blob frames ---------------------------------------------------------------------------------
def test_export_is_a_blob_with_the_db_name(phone):
    t = link(peer_of(phone))
    name, data = t.export_layout()
    assert name == "launcher_5_4_4.db" and data == phone.dev.files[DB]
    assert phone.dev.leftovers() == []
    t.close()


def test_import_sends_the_db_as_one_blob_frame(phone):
    t = link(peer_of(phone))
    _, data = t.export_layout()
    assert t.import_layout(data, db="launcher_5_4_4.db") == {"items": 1}
    i = [n for n, l in enumerate(phone.lines) if b"layout_import" in l][0]
    req, head = json.loads(phone.lines[i]), json.loads(phone.lines[i + 1])
    assert req["params"] == {"db": "launcher_5_4_4.db"} and "path" not in req["params"]
    assert head == {"id": req["id"], "blob": len(data)}
    assert phone.dev.leftovers() == []  # no staged copy left behind
    t.close()


def test_write_private_stages_through_the_phone_and_big_blobs_survive(phone):
    t = link(peer_of(phone))
    big = bytes(range(256)) * (12 * 1024)  # 3 MiB, every byte value
    assert t.write_private("wp", big) == "/data/user/0/app.homalawn.debug/files/omalawn/wp"
    assert phone.dev.files["files/omalawn/wp"] == big
    assert phone.log[-1][1:] == ("launcher.stage_file", {"name": "wp"}, len(big))
    t.close()


def test_wallpaper_backup_comes_back_as_bytes(phone):
    t = link(peer_of(phone))
    assert t.wallpaper_backup() == b"\x89PNG wallpaper"
    t.close()


def test_a_blob_that_is_not_sqlite_is_refused(phone):
    phone.dev.files[DB] = b"garbage"
    with pytest.raises(LinkDown, match="damaged"):
        link(peer_of(phone)).export_layout()


# -- errors --------------------------------------------------------------------------------------
def test_phone_error_codes_become_provider_errors(phone):
    phone.dev.fail_import = 1
    t = link(peer_of(phone))
    with pytest.raises(ProviderError) as e:
        t.import_layout(b"SQLite format 3\x00...")
    assert e.value.code == "failed"
    with pytest.raises(ProviderError) as e:
        t.call("launcher.nope")
    assert e.value.code == "unknown_method"
    t.close()


def test_not_paired_hello_is_a_clear_error(certs, ident, tmp_path):
    p = FakeLinkPhone(certs["phone"], FakeDevice(tmp_path), trust=[ident.cert], pinned=set())
    try:
        with pytest.raises(NotPaired) as e:
            link(peer_of(p)).call("launcher.ping")
        assert e.value.code == "not_paired" and "homalawn pair" in str(e.value)
        assert p.methods() == ["link.hello"]  # nothing past hello
    finally:
        p.close()


def test_not_paired_answer_mid_run_is_the_same_error(phone, ident):
    t = link(peer_of(phone))
    t.call("launcher.ping")
    phone.pinned.clear()  # "Forget all computers" tapped on the phone
    with pytest.raises(NotPaired, match="homalawn pair"):
        t.call("launcher.ping")


def test_pairing_methods_are_refused_so_nothing_is_sent_during_pair_wait(phone):
    t = link(peer_of(phone))
    for m in ("link.pair_request", "link.pair_reveal", "link.pair_wait", "link.unpair"):
        with pytest.raises(NotSupported):
            t.call(m, {})
    assert phone.log == []


def test_shell_is_not_supported(phone):
    t = link(peer_of(phone))
    with pytest.raises(NotSupported):
        t.shell("pm", "list", "packages")
    assert phone.log == []


def test_screenshot_is_screen_shot_and_says_how_when_off(phone):
    """Task 8: a screenshot over the link goes through screen control (screen.shot)."""
    t = link(peer_of(phone))
    with pytest.raises(NotSupported, match="no screen control yet.*--via adb"):  # a build without patch 0004
        t.screenshot()
    assert phone.methods()[-1] == "screen.shot"


# -- timeouts, keep-alive, reconnect -------------------------------------------------------------
def test_a_phone_that_never_answers_times_out(phone):
    phone.silent.add("launcher.ping")
    t = link(peer_of(phone), call_s=0.5)
    t0 = time.monotonic()
    with pytest.raises(LinkDown):
        t.call("launcher.ping")
    assert time.monotonic() - t0 < 3  # one retry for a read-only call, each 0.5 s


def test_idle_connection_closed_by_the_phone_is_dialled_again_before_sending(certs, ident, tmp_path):
    p = FakeLinkPhone(certs["phone"], FakeDevice(tmp_path), trust=[ident.cert], pinned={ident.fingerprint},
                      idle_close_s=0.3)
    try:
        t = link(peer_of(p))
        _, data = t.export_layout()
        time.sleep(0.8)  # the phone hangs up on the idle connection
        assert t.import_layout(data) == {"items": 1}  # a write: sent once, on a fresh connection
        assert p.conns == 2 and p.methods().count("launcher.layout_import") == 1
        assert p.methods(2) == ["link.hello", "launcher.layout_import"]
    finally:
        p.close()


def test_a_write_cut_off_mid_request_is_not_sent_twice(phone):
    phone.hang_up_on.add("launcher.layout_import")
    t = link(peer_of(phone))
    _, data = t.export_layout()
    with pytest.raises(LinkDown, match="may or may not"):
        t.import_layout(data)
    assert phone.methods().count("launcher.layout_import") == 1


def test_a_read_cut_off_mid_request_is_asked_again(phone):
    t = link(peer_of(phone))
    t.call("launcher.ping")
    phone.hang_up_on.add("launcher.widgets_list")
    calls = []
    orig = phone._one

    def once(no, s, f, req, fp):  # hang up only the first time
        calls.append(req["method"])
        if calls.count("launcher.widgets_list") > 1:
            phone.hang_up_on.discard("launcher.widgets_list")
        return orig(no, s, f, req, fp)
    phone._one = once
    assert t.call("launcher.widgets_list")[0]["provider"] == "com.w/com.w.W"
    assert phone.conns == 2


def test_keepalive_pings_an_idle_connection_so_the_phone_keeps_it(certs, ident, tmp_path):
    p = FakeLinkPhone(certs["phone"], FakeDevice(tmp_path), trust=[ident.cert], pinned={ident.fingerprint},
                      idle_close_s=0.8)
    try:
        t = link(peer_of(p), keepalive_s=0.2)
        t.call("launcher.ping")
        time.sleep(2.0)  # sitting idle well past the phone's idle timeout
        t.call("launcher.ping")
        assert p.conns == 1 and p.methods().count("link.ping") >= 3
        t.close()
    finally:
        p.close()


# -- fix round 1 ---------------------------------------------------------------------------------
def test_only_hosts_dials_exactly_those_hosts(phone):
    dead = f"127.0.0.1:{closed_port()}"
    t = link(peer_of(phone, hosts=[phone.addr], last_lan="192.168.1.9:47100"), hosts=[dead], only_hosts=True,
             discover=lambda: pytest.fail("asked mDNS"))
    assert t.candidates() == [dead]
    with pytest.raises(LinkDown):
        t.connect()
    assert phone.handshakes == 0  # the address it paired through was not tried behind the user's back


def test_another_paired_phones_certificate_is_skipped_not_fatal(certs, ident, phone, tmp_path):
    other = FakeLinkPhone(certs["impostor"], FakeDevice(_sub(tmp_path)), trust=[ident.cert], pinned={ident.fingerprint})
    try:
        t = link(peer_of(phone, hosts=[other.addr, phone.addr]), others={other.fp: "Tablet"})
        t.connect()
        assert t.address == phone.addr and other.log == []
        assert "Tablet" in t.tried[0] and t.other_seen
        t.close()
        # a certificate of no paired phone is still a hard stop
        with pytest.raises(PinMismatch):
            link(peer_of(phone, hosts=[other.addr, phone.addr]), others={}).connect()
    finally:
        other.close()


def test_the_other_phones_are_read_from_peers_json_by_default(certs, ident, phone, tmp_path):
    other = FakeLinkPhone(certs["impostor"], FakeDevice(_sub(tmp_path)), trust=[ident.cert], pinned={ident.fingerprint})
    try:
        identity.save_peer(Peer(fp=other.fp, name="Tablet", hosts=[other.addr]))
        t = link(peer_of(phone, hosts=[other.addr, phone.addr]))
        t.connect()
        assert t.address == phone.addr
        t.close()
    finally:
        other.close()


def test_an_oversize_outgoing_blob_is_refused_before_sending_and_keeps_the_connection(phone, monkeypatch):
    from homalawn.link import client
    from homalawn.link.client import BlobTooLarge
    monkeypatch.setattr(client, "MAX_BLOB", 1000)
    t = link(peer_of(phone))
    t.call("launcher.ping")
    with pytest.raises(BlobTooLarge) as e:
        t.write_private("wp", b"x" * 1001)
    assert "nothing was sent" in str(e.value) and "may or may not" not in str(e.value)
    t.call("launcher.ping")
    assert phone.conns == 1 and "launcher.stage_file" not in phone.methods()


@pytest.mark.parametrize("answer", [
    b'{"id":$ID,"blob":33554433,"result":{}}\n',   # more than 32 MiB announced
    b'{"id":$ID,"blob":-1,"result":{}}\n',
    b'{"id":$ID,"blob":"12","result":{}}\n',
    b'{"id":$ID,"blob":true,"result":{}}\n',
    b'{"id":$ID,"result":"' + b"a" * (1 << 20) + b'"}\n',  # a line over 1 MiB
    b'[1, 2, 3]\n',
    b'"just a string"\n',
    b'{"id":12345,"error":{"code":"busy","message":"for someone else"}}\n',  # an error for another request
    b'not json\n',
], ids=["blob-over-32MiB", "blob-negative", "blob-string", "blob-bool", "line-over-1MiB", "json-array",
        "json-string", "error-for-other-id", "not-json"])
def test_a_hostile_phone_answer_is_a_clean_link_error(phone, answer):
    phone.raw_answers["launcher.wallpaper_backup"] = answer
    t = link(peer_of(phone))
    with pytest.raises(LinkDown):
        t.wallpaper_backup()
    t.close()


def test_a_line_of_exactly_the_limit_is_accepted():
    import io
    from homalawn.link import wire
    body = b'{"id":1,"result":"' + b"a" * (wire.MAX_LINE - 20) + b'"}'
    assert len(body) == wire.MAX_LINE
    c = wire.Conn.__new__(wire.Conn)
    c._f, c._n = io.BufferedReader(io.BytesIO(body + b"\n")), 1
    assert c._read_msg_or_none()["id"] == 1
    c._f = io.BufferedReader(io.BytesIO(b'{"id":1,"result":"' + b"a" * (wire.MAX_LINE - 19) + b'"}\n'))
    with pytest.raises(ConnectionError):
        c._read_msg_or_none()


def test_no_peer_certificate_is_a_connection_error(phone, ident, monkeypatch):
    import ssl
    from homalawn.link import wire
    monkeypatch.setattr(ssl.SSLSocket, "getpeercert", lambda self, binary_form=False: None)
    with pytest.raises(ConnectionError, match="no certificate"):
        wire.Conn("127.0.0.1", phone.port, str(ident.cert), str(ident.key), timeout=2)


def test_wallpaper_backup_is_asked_again_after_a_broken_connection(phone):
    t = link(peer_of(phone))
    t.call("launcher.ping")
    phone.drop_all()
    time.sleep(0.2)
    real = phone._one
    seen = []

    def first_hangs_up(no, s, f, req, fp):
        seen.append(req["method"])
        if req["method"] == "launcher.wallpaper_backup" and seen.count("launcher.wallpaper_backup") == 1:
            return False
        return real(no, s, f, req, fp)
    phone._one = first_hangs_up
    assert t.wallpaper_backup() == b"\x89PNG wallpaper"
    t.close()


def test_keepalive_restarts_after_close(certs, ident, tmp_path):
    p = FakeLinkPhone(certs["phone"], FakeDevice(tmp_path), trust=[ident.cert], pinned={ident.fingerprint},
                      idle_close_s=0.8)
    try:
        t = link(peer_of(p), keepalive_s=0.2)
        t.call("launcher.ping")
        t.close()
        t.call("launcher.ping")  # dialled again after close: keep-alive must run again
        time.sleep(2.0)
        t.call("launcher.ping")
        assert p.conns == 2 and t._ka.is_alive()
        t.close()
    finally:
        p.close()


def test_forgotten_hello_removes_the_peer(phone, ident):
    peer = peer_of(phone)
    identity.save_peer(peer)
    phone.pinned.clear()
    phone.handlers["link.hello"] = lambda p: ({"paired": False, "forgotten": True, "v": 1}, None)
    t = link(peer)
    with pytest.raises(NotPaired) as e:
        t.call("link.ping")
    assert e.value.forgotten and "forgot this computer" in str(e.value)
    assert all(p.fp != peer.fp for p in identity.load_peers())


def test_not_paired_without_tombstone_keeps_the_peer(phone, ident):
    peer = peer_of(phone)
    identity.save_peer(peer)
    phone.pinned.clear()
    t = link(peer)
    with pytest.raises(NotPaired) as e:
        t.call("link.ping")
    assert not e.value.forgotten
    assert any(p.fp == peer.fp for p in identity.load_peers())


def test_forgotten_not_paired_answer_mid_run_removes_the_peer(phone, ident):
    peer = peer_of(phone)
    identity.save_peer(peer)
    t = link(peer)
    t.call("launcher.ping")
    phone.pinned.clear()
    phone.not_paired_detail = {"forgotten": True}
    with pytest.raises(NotPaired) as e:
        t.call("launcher.ping")
    assert e.value.forgotten and "forgot this computer" in str(e.value)
    assert all(p.fp != peer.fp for p in identity.load_peers())


def test_not_paired_answer_mid_run_without_detail_keeps_the_peer(phone, ident):
    peer = peer_of(phone)
    identity.save_peer(peer)
    t = link(peer)
    t.call("launcher.ping")
    phone.pinned.clear()
    with pytest.raises(NotPaired) as e:
        t.call("launcher.ping")
    assert not e.value.forgotten
    assert any(p.fp == peer.fp for p in identity.load_peers())


def test_wifi_search_finds_the_phone_at_a_new_home_address(phone):
    """The router gave the phone another address and mDNS is silent: the Wi-Fi search finds it,
    the pinned certificate decides, and the new address is remembered."""
    dead = f"127.0.0.1:{closed_port()}"
    t = link(peer_of(phone, hosts=[dead]), sweep=lambda: [dead, phone.addr])
    t.connect()
    assert t.address == phone.addr
    t.close()


def test_wifi_search_is_not_run_when_a_known_address_answers(phone):
    t = link(peer_of(phone), sweep=lambda: pytest.fail("searched the Wi-Fi"))
    t.connect()
    t.close()


def test_wifi_search_is_off_for_one_host_and_inside_capped(phone):
    dead = f"127.0.0.1:{closed_port()}"
    t = link(peer_of(phone), hosts=[dead], only_hosts=True, sweep=lambda: pytest.fail("searched the Wi-Fi"))
    with pytest.raises(LinkDown):
        t.connect()
    t = link(peer_of(phone, hosts=[dead]), sweep=lambda: pytest.fail("searched the Wi-Fi"))
    with t.capped(1.0), pytest.raises(LinkDown):
        t.connect()
