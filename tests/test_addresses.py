import json
import pytest
from homalawn.link import addresses as ad


@pytest.mark.parametrize("raw,want", [
    ("100.101.102.103", "100.101.102.103"),
    ("Pixel-8.tail1234.ts.net.", "pixel-8.tail1234.ts.net"),
    ("[fd7a:115c:a1e0::1]", "fd7a:115c:a1e0::1"),
    ("10.0.0.5:47100", "10.0.0.5"),
    ("10.0.0.5:4711", "10.0.0.5:4711"),
    ("pixel:4711", "pixel:4711"),
])
def test_normalize(raw, want):
    assert ad.normalize(raw) == want


@pytest.mark.parametrize("raw", ["", "http://x", "a/b", "0.0.0.0", "-bad-", "x:99999"])
def test_normalize_refuses(raw):
    with pytest.raises(ValueError):
        ad.normalize(raw)


def test_is_tailscale():
    assert ad.is_tailscale("100.64.0.1") and ad.is_tailscale("fd7a:115c:a1e0::5")
    assert not ad.is_tailscale("192.168.1.2") and not ad.is_tailscale("pixel")


def test_config_hosts_move_into_peers(tmp_path):
    from homalawn import config
    from homalawn.link import identity
    identity.save_peer(identity.Peer(fp="a" * 64, name="Pixel 8"))
    config.path().parent.mkdir(parents=True, exist_ok=True)
    config.path().write_text('serial = "x"\n[peers."Pixel 8"]\nhosts = ["100.1.2.3", "10.0.0.5:47100"]\n')
    assert ad.migrate_config_hosts() == 2
    assert identity.load_peers()[0].addresses == ["100.1.2.3", "10.0.0.5"]
    assert "hosts" not in config.path().read_text() and 'serial = "x"' in config.path().read_text()
    assert config.path().with_name("config.toml.hosts").exists()
    assert ad.migrate_config_hosts() == 0


def test_hosts_keyed_by_fingerprint_prefix_and_unpaired_key_stays(tmp_path):
    from homalawn import config
    from homalawn.link import identity
    identity.save_peer(identity.Peer(fp="ab" * 32, name="Pixel 8"))
    config.path().parent.mkdir(parents=True, exist_ok=True)
    config.path().write_text('[peers.abababababababab]\nhosts = ["pixel.ts.net"]\n'
                             '[peers."Gone"]\nhosts = ["10.9.9.9"]\n')
    assert ad.migrate_config_hosts() == 1
    assert identity.load_peers()[0].addresses == ["pixel.ts.net"]
    text = config.path().read_text()
    assert "pixel.ts.net" not in text and "10.9.9.9" in text   # no paired phone: the key stays in config.toml


def test_migration_caps_at_five(tmp_path):
    from homalawn import config
    from homalawn.link import identity
    identity.save_peer(identity.Peer(fp="c" * 64, name="P"))
    config.path().parent.mkdir(parents=True, exist_ok=True)
    hs = ", ".join(f'"10.0.0.{i}"' for i in range(1, 8))
    config.path().write_text(f'[peers.P]\nhosts = [{hs}]\n')
    assert ad.migrate_config_hosts() == 5
    assert len(identity.load_peers()[0].addresses) == 5


def test_same_named_phones_leave_the_key_and_log(tmp_path, caplog):
    from homalawn import config
    from homalawn.link import identity
    identity.save_peer(identity.Peer(fp="1" * 64, name="Pixel"))
    identity.save_peer(identity.Peer(fp="2" * 64, name="Pixel"))
    config.path().parent.mkdir(parents=True, exist_ok=True)
    config.path().write_text('[peers.Pixel]\nhosts = ["10.0.0.1"]\n')
    assert ad.migrate_config_hosts() == 0
    assert all(p.addresses == [] for p in identity.load_peers())
    assert "10.0.0.1" in config.path().read_text()
    assert "fingerprint prefix" in caplog.text


def test_unmoved_hosts_are_logged(tmp_path, caplog):
    from homalawn import config
    from homalawn.link import identity
    identity.save_peer(identity.Peer(fp="3" * 64, name="P"))
    config.path().parent.mkdir(parents=True, exist_ok=True)
    hs = ", ".join(f'"10.0.0.{i}"' for i in range(1, 7))
    config.path().write_text(f'[peers.P]\nhosts = ["http://bad", {hs}]\n')
    assert ad.migrate_config_hosts() == 5
    assert "http://bad" in caplog.text and "10.0.0.6" in caplog.text


IP_J_ADDR = json.dumps([
    {"ifname": "lo", "addr_info": [{"family": "inet", "local": "127.0.0.1", "prefixlen": 8}]},
    {"ifname": "wlo1", "addr_info": []},
    {"ifname": "enp3s0", "addr_info": [{"family": "inet", "local": "192.168.50.21", "prefixlen": 24},
                                        {"family": "inet6", "local": "fe80::1", "prefixlen": 64}]},
    {"ifname": "tailscale0", "addr_info": [{"family": "inet", "local": "100.64.0.7", "prefixlen": 32}]},
])


def _ip(monkeypatch, stdout=IP_J_ADDR, exc=None):
    import subprocess
    calls = []

    def run(cmd, **kw):
        calls.append(cmd)
        if exc:
            raise exc
        return subprocess.CompletedProcess(cmd, 0, stdout=stdout, stderr="")
    monkeypatch.setattr(subprocess, "run", run)
    return calls


def test_local_addresses_read_ip_once(monkeypatch):
    calls = _ip(monkeypatch)
    assert ad.local_addresses() == ["127.0.0.1", "192.168.50.21", "fe80::1", "100.64.0.7"]
    assert calls == [["ip", "-j", "addr"]]


def test_local_tailscale(monkeypatch):
    _ip(monkeypatch)
    assert ad.local_tailscale() is True
    _ip(monkeypatch, stdout=json.dumps([{"ifname": "lo", "addr_info": [{"local": "127.0.0.1"}]}]))
    assert ad.local_tailscale() is False
    _ip(monkeypatch, exc=FileNotFoundError("ip"))
    assert ad.local_addresses() == [] and ad.local_tailscale() is False


def test_local_network_addresses_skip_noise(monkeypatch):
    ifs = [
        {"ifname": "lo", "addr_info": [{"local": "127.0.0.1", "scope": "host"}]},
        {"ifname": "enp1", "addr_info": [{"local": "192.168.1.5", "scope": "global"},
                                         {"local": "fe80::1", "scope": "link"},
                                         {"local": "2001:db8::9", "scope": "global", "temporary": True},
                                         {"local": "2001:db8::1", "scope": "global"}]},
        {"ifname": "docker0", "addr_info": [{"local": "172.17.0.1", "scope": "global"}]},
        {"ifname": "br-abc", "addr_info": [{"local": "172.18.0.1", "scope": "global"}]},
        {"ifname": "veth1", "addr_info": [{"local": "10.9.9.9", "scope": "global"}]},
        {"ifname": "virbr0", "addr_info": [{"local": "192.168.122.1", "scope": "global"}]},
        {"ifname": "tailscale0", "addr_info": [{"local": "100.64.0.7", "scope": "global"}]},
    ]
    _ip(monkeypatch, stdout=json.dumps(ifs))
    assert ad.local_network_addresses() == ["192.168.1.5", "2001:db8::1", "100.64.0.7"]


def test_lan_neighbours_are_the_other_hosts_of_this_computers_home_networks(monkeypatch):
    ifs = [
        {"ifname": "lo", "addr_info": [{"local": "127.0.0.1", "prefixlen": 8, "family": "inet", "scope": "host"}]},
        {"ifname": "enp1", "addr_info": [{"local": "192.168.50.21", "prefixlen": 24, "family": "inet", "scope": "global"},
                                         {"local": "2001:db8::1", "prefixlen": 64, "family": "inet6", "scope": "global"}]},
        {"ifname": "wlan0", "addr_info": [{"local": "10.1.0.7", "prefixlen": 16, "family": "inet", "scope": "global"}]},
        {"ifname": "docker0", "addr_info": [{"local": "172.17.0.1", "prefixlen": 24, "family": "inet", "scope": "global"}]},
        {"ifname": "tailscale0", "addr_info": [{"local": "100.64.0.7", "prefixlen": 32, "family": "inet", "scope": "global"}]},
    ]
    _ip(monkeypatch, stdout=json.dumps(ifs))
    got = ad.lan_neighbours()
    # a /24 is searched; a /16 (65k addresses) is too big to search; containers, Tailscale, IPv6 never
    assert len(got) == 253 and "192.168.50.21" not in got
    assert got[0] == "192.168.50.1" and got[-1] == "192.168.50.254"


def test_open_ports_returns_only_hosts_with_the_port_open():
    import socket
    srv = socket.socket()
    srv.bind(("127.0.0.1", 0))
    srv.listen()
    port = srv.getsockname()[1]
    try:
        assert ad.open_ports(port, ["127.0.0.1", "127.0.0.2", "127.0.0.3"]) == [f"127.0.0.1:{port}"]
    finally:
        srv.close()
