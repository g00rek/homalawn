import subprocess

from homalawn.link import discovery

# Lines in the shape avahi-browse 0.9 -rpt prints (checked on this machine: `=` resolved entries,
# `;`-separated, names escaped as \DDD, TXT records quoted and space-separated).
OUT = (
    "+;wlan0;IPv4;OmaLawn\\032Pixel\\0328;_omalawn._tcp;local\n"
    '=;wlan0;IPv4;OmaLawn\\032Pixel\\0328;_omalawn._tcp;local;Pixel-8.local;192.168.1.23;47100;"v=1" "fp=658d8fe8b7d1baea"\n'
    '=;wlan0;IPv6;OmaLawn\\032Pixel\\0328;_omalawn._tcp;local;Pixel-8.local;fe80::1c2:3;47100;"fp=658d8fe8b7d1baea" "v=1"\n'
    '=;wlan0;IPv4;OmaLawn\\032Other;_omalawn._tcp;local;other.local;192.168.1.40;47100;"v=1"\n'
    "garbage line\n"
)


def test_parse_resolved_lines():
    hits = discovery.parse(OUT)
    assert [(h.name, h.address, h.port, h.fp_prefix) for h in hits] == [
        ("OmaLawn Pixel 8", "192.168.1.23", 47100, "658d8fe8b7d1baea"),
        ("OmaLawn Pixel 8", "fe80::1c2:3%wlan0", 47100, "658d8fe8b7d1baea"),
        ("OmaLawn Other", "192.168.1.40", 47100, ""),
    ]
    assert hits[0].host == "192.168.1.23:47100" and hits[1].host == "[fe80::1c2:3%wlan0]:47100"


def test_hits_naming_the_peer_first_then_anonymous_ones_never_another_fingerprint():
    fp = "658d8fe8b7d1baea" + "0" * 48
    assert [h.address for h in discovery.for_peer(fp, discovery.parse(OUT))] == [
        "192.168.1.23", "fe80::1c2:3%wlan0", "192.168.1.40"]
    assert [h.address for h in discovery.for_peer("ff" * 32, discovery.parse(OUT))] == ["192.168.1.40"]


def test_current_phones_advertise_no_fingerprint_and_are_all_tried():
    out = '=;wlan0;IPv4;OmaLawn;_omalawn._tcp;local;a.local;192.168.1.5;47100;"v=1"\n' \
          '=;wlan0;IPv4;OmaLawn\\032(2);_omalawn._tcp;local;b.local;192.168.1.6;47100;"v=1"\n'
    hits = discovery.parse(out)
    assert [h.name for h in hits] == ["OmaLawn", "OmaLawn (2)"]
    assert [h.address for h in discovery.for_peer("ab" * 32, hits)] == ["192.168.1.5", "192.168.1.6"]


def test_no_avahi_means_no_hints(monkeypatch):
    def missing(*a, **k):
        raise FileNotFoundError("avahi-browse")
    monkeypatch.setattr(subprocess, "run", missing)
    assert discovery.browse() == []

    def slow(*a, **k):
        raise subprocess.TimeoutExpired("avahi-browse", 3)
    monkeypatch.setattr(subprocess, "run", slow)
    assert discovery.browse() == []


def test_browse_asks_avahi_for_the_homalawn_type(monkeypatch):
    seen = []
    monkeypatch.setattr(subprocess, "run", lambda argv, **k: seen.append(argv) or
                        subprocess.CompletedProcess(argv, 0, OUT, ""))
    assert len(discovery.browse()) == 3
    assert seen == [["avahi-browse", "-rpt", "_omalawn._tcp"]]


def test_one_phone_per_host_name_with_its_ipv4_address_first():
    hits = discovery.parse(OUT)
    assert [h.hostname for h in hits] == ["Pixel-8.local", "Pixel-8.local", "other.local"]
    assert [(h.address, h.hostname) for h in discovery.phones(hits)] == [
        ("192.168.1.23", "Pixel-8.local"), ("192.168.1.40", "other.local")]
    only_v6 = [h for h in hits if ":" in h.address]
    assert [h.address for h in discovery.phones(only_v6)] == ["fe80::1c2:3%wlan0"]


def test_a_browse_that_times_out_keeps_what_resolved(monkeypatch):
    def slow(*a, **kw):
        raise subprocess.TimeoutExpired(a[0], kw["timeout"], output=OUT.encode())
    monkeypatch.setattr(discovery.subprocess, "run", slow)
    assert [h.address for h in discovery.browse(timeout=0.1)] == ["192.168.1.23", "fe80::1c2:3%wlan0", "192.168.1.40"]


def test_missing_avahi_is_detected():
    from homalawn.link import discovery
    assert discovery.missing(which=lambda c: None) is True
    assert discovery.missing(which=lambda c: "/usr/bin/" + c) is False
