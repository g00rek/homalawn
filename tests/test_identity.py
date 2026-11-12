import json
import os
import stat
import subprocess

import pytest

from homalawn.link import identity


@pytest.fixture
def cfg(tmp_path, monkeypatch):
    monkeypatch.setenv("HOMALAWN_CONFIG", str(tmp_path / "cfg" / "config.toml"))
    return tmp_path / "cfg" / "link"


def test_ensure_makes_p256_key_and_cert_owner_only(cfg):
    ident = identity.ensure()
    assert ident.dir == cfg
    assert stat.S_IMODE(os.stat(cfg / "key.pem").st_mode) == 0o600
    assert stat.S_IMODE(os.stat(cfg).st_mode) == 0o700
    text = subprocess.run(["openssl", "x509", "-in", str(cfg / "cert.pem"), "-noout", "-text"],
                          check=True, capture_output=True, text=True).stdout
    assert "prime256v1" in text or "P-256" in text


def test_fingerprint_matches_openssl(cfg):
    ident = identity.ensure()
    out = subprocess.run(["openssl", "x509", "-in", str(cfg / "cert.pem"), "-noout", "-fingerprint", "-sha256"],
                         check=True, capture_output=True, text=True).stdout
    assert ident.fingerprint == out.strip().split("=", 1)[1].replace(":", "").lower()
    assert len(ident.cert_der) > 100


def test_ensure_is_stable_and_warns_about_an_exposed_key(cfg, capsys):
    a = identity.ensure()
    assert capsys.readouterr().err == ""
    os.chmod(cfg / "key.pem", 0o644)
    b = identity.ensure()
    assert a.fingerprint == b.fingerprint
    err = capsys.readouterr().err
    assert err.count("\n") == 1 and "may have been exposed" in err and "pair" in err and "644" in err
    assert stat.S_IMODE(os.stat(cfg / "key.pem").st_mode) == 0o600
    identity.ensure()
    assert capsys.readouterr().err == ""  # once fixed, quiet again


def test_peers_round_trip_owner_only(cfg):
    identity.ensure()
    assert identity.load_peers() == []
    identity.save_peer(identity.Peer(fp="ab" * 32, name="Pixel", hosts=["127.0.0.1:47100"], last_lan=None))
    identity.save_peer(identity.Peer(fp="cd" * 32, name="Tab", hosts=["192.168.1.7:47100"], last_lan="192.168.1.7:47100"))
    identity.save_peer(identity.Peer(fp="ab" * 32, name="Pixel 9", hosts=["10.0.0.2:47100"], last_lan="10.0.0.2:47100"))
    peers = identity.load_peers()
    assert [(p.name, p.fp) for p in peers] == [("Pixel 9", "ab" * 32), ("Tab", "cd" * 32)]
    assert stat.S_IMODE(os.stat(cfg / "peers.json").st_mode) == 0o600
    assert json.loads((cfg / "peers.json").read_text())["peers"][0]["last_lan"] == "10.0.0.2:47100"
    assert identity.remove_peer("ab" * 32) is True
    assert [p.name for p in identity.load_peers()] == ["Tab"]
    assert identity.remove_peer("ab" * 32) is False


def test_lan_address_detection():
    assert identity.lan_address("192.168.1.7", 47100) == "192.168.1.7:47100"
    assert identity.lan_address("10.1.2.3", 47100) == "10.1.2.3:47100"
    assert identity.lan_address("172.20.0.1", 47100) == "172.20.0.1:47100"
    assert identity.lan_address("127.0.0.1", 47100) is None
    assert identity.lan_address("100.101.102.103", 47100) is None  # Tailscale CGNAT, not LAN
    assert identity.lan_address("phone.tailnet.ts.net", 47100) is None


def test_peer_address_fields_default_and_round_trip():
    from homalawn.link import identity
    old = identity.link_dir()
    old.mkdir(parents=True, exist_ok=True)
    (old / "peers.json").write_text('{"peers": [{"fp": "' + "d" * 64 + '", "name": "P", "extra": 1}]}')
    p = identity.load_peers()[0]
    assert (p.addresses, p.learned, p.last_ok) == ([], [], None)
    p.addresses, p.learned, p.last_ok = ["a"], ["100.64.0.1"], "a"
    identity.save_peer(p)
    q = identity.load_peers()[0]
    assert (q.addresses, q.learned, q.last_ok) == (["a"], ["100.64.0.1"], "a")


def test_update_peer_sets_only_the_given_fields(cfg):
    identity.save_peer(identity.Peer(fp="a" * 64, name="Pixel", addresses=["pixel-8"]))
    assert identity.update_peer("a" * 64, last_ok="10.0.0.5:47100", last_lan="10.0.0.5:47100") is True
    p = identity.load_peers()[0]
    assert (p.name, p.addresses, p.last_ok, p.last_lan) == ("Pixel", ["pixel-8"], "10.0.0.5:47100", "10.0.0.5:47100")
    assert identity.update_peer("b" * 64, last_ok="x") is False and len(identity.load_peers()) == 1
    with pytest.raises(TypeError):
        identity.update_peer("a" * 64, nonsense=1)


def test_concurrent_updates_of_different_fields_both_survive(cfg, monkeypatch):
    import threading
    import time
    identity.save_peer(identity.Peer(fp="a" * 64, name="Pixel"))
    real = identity.load_peers

    def slow():   # widens the read-to-write window so an unlocked update would lose the other's field
        peers = real()
        time.sleep(0.2)
        return peers
    monkeypatch.setattr(identity, "load_peers", slow)
    ts = [threading.Thread(target=identity.update_peer, args=("a" * 64,), kwargs=kw)
          for kw in ({"addresses": ["pixel-8"]}, {"last_ok": "10.0.0.5"})]
    for t in ts:
        t.start()
    for t in ts:
        t.join()
    monkeypatch.setattr(identity, "load_peers", real)
    p = identity.load_peers()[0]
    assert (p.addresses, p.last_ok) == (["pixel-8"], "10.0.0.5")


def test_pairing_the_same_phone_again_keeps_its_addresses(cfg):
    identity.save_peer(identity.Peer(fp="a" * 64, name="Pixel", addresses=["pixel-8"], learned=["100.70.1.2"]))
    identity.record_pairing(identity.Peer(fp="a" * 64, name="Pixel 8", hosts=["10.0.0.5"]))
    p = identity.load_peers()[0]
    assert (p.name, p.hosts, p.addresses, p.learned) == ("Pixel 8", ["10.0.0.5"], ["pixel-8"], ["100.70.1.2"])


def test_migration_keeps_fields_written_meanwhile(cfg, monkeypatch):
    from homalawn import config
    from homalawn.link import addresses
    identity.save_peer(identity.Peer(fp="a" * 64, name="Pixel"))
    config.path().write_text('[peers.Pixel]\nhosts = ["pixel-8"]\n')
    real = identity.load_peers
    calls = []

    def racing():
        peers = real()
        if not calls:                 # the first (unlocked) read is stale by the time anything is written
            calls.append(1)
            identity.update_peer("a" * 64, last_ok="10.0.0.5")
        return peers
    monkeypatch.setattr(identity, "load_peers", racing)
    assert addresses.migrate_config_hosts() == 1
    monkeypatch.setattr(identity, "load_peers", real)
    p = identity.load_peers()[0]
    assert (p.addresses, p.last_ok) == (["pixel-8"], "10.0.0.5")
