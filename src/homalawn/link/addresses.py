# Adapted from Flux (https://github.com/bjarneo/flux), MIT License, Copyright (c) 2026 Bjarne Oeverli
"""A paired phone's addresses (spec section 8). `normalize` is adapted from Flux
(https://github.com/bjarneo/flux), MIT License, Copyright (c) 2026 Bjarne Oeverli:
internal/core/addresses.go normalizeAddress (here a port may follow; 47100 is dropped)."""
from __future__ import annotations

import ipaddress
import logging
import re

from .wire import PORT

log = logging.getLogger(__name__)
MAX_ADDRESSES = 5
MAX_LEARNED = 4
_LABEL = re.compile(r"[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?")
_TS4 = ipaddress.ip_network("100.64.0.0/10")
_TS6 = ipaddress.ip_network("fd7a:115c:a1e0::/48")


def normalize(raw: str) -> str:
    s = (raw or "").strip()
    if not s:
        raise ValueError("Give a host name or an IP address.")
    if "://" in s or "/" in s:
        raise ValueError(f"{raw!r} is not a host name or an IP address; give the host only, e.g. pixel-8 or 100.101.102.103.")
    host, port = s, None
    if s.startswith("["):
        host, _, rest = s[1:].partition("]")
        port = rest[1:] if rest.startswith(":") else None
    elif s.count(":") == 1:
        host, port = s.split(":")
    try:
        ip = ipaddress.ip_address(host)
        if ip.is_unspecified or ip.is_multicast:
            raise ValueError(f"{ip} cannot be a phone's address.")
        host = ip.compressed
    except ValueError as e:
        if "cannot be" in str(e):
            raise
        name = host.lower().rstrip(".")
        if not name or len(name) > 253 or not all(_LABEL.fullmatch(x) for x in name.split(".")):
            raise ValueError(f"{raw!r} is not a valid host name.")
        host = name
    if port is not None:
        if not port.isdigit() or not 0 < int(port) < 65536:
            raise ValueError(f"{port!r} is not a port.")
        if int(port) != PORT:
            return f"[{host}]:{port}" if ":" in host else f"{host}:{port}"
    return host


def is_tailscale(host: str) -> bool:
    try:
        ip = ipaddress.ip_address(host.strip("[]"))
    except ValueError:
        return False
    return ip in (_TS4 if ip.version == 4 else _TS6)


def _ip_addr() -> list[tuple[str, dict]]:
    """(interface name, address entry) for every address, from `ip -j addr` (the one place it is read)."""
    import json
    import subprocess
    try:
        out = subprocess.run(["ip", "-j", "addr"], capture_output=True, text=True, timeout=2).stdout
        return [(i.get("ifname", ""), a) for i in json.loads(out) for a in i.get("addr_info", []) if a.get("local")]
    except (OSError, ValueError, subprocess.TimeoutExpired):
        return []


def local_addresses() -> list[str]:
    """This computer's own interface addresses."""
    return [a["local"] for _, a in _ip_addr()]


_VIRTUAL = ("lo", "docker", "br-", "veth", "virbr")


def local_network_addresses() -> list[str]:
    """The addresses that say which network this computer is on: no loopback, link-local, temporary
    IPv6 or container / bridge interface (their churn is not a network change)."""
    return [a["local"] for ifname, a in _ip_addr()
            if not ifname.startswith(_VIRTUAL) and not a.get("temporary")
            and a.get("scope") not in ("host", "link")]


def local_tailscale() -> bool:
    """This computer has a Tailscale address itself (else the phone's learned ones are not dialled)."""
    return any(is_tailscale(a) for a in local_addresses())


SWEEP_MIN_PREFIX = 22    # a /22 (1022 addresses) is the biggest home network searched
SWEEP_CONNECT_S = 1.0
SWEEP_WORKERS = 128


def lan_neighbours() -> list[str]:
    """Every other IPv4 address of this computer's own private networks (home Wi-Fi / Ethernet),
    where a phone whose address the router changed may now be. Containers, Tailscale and networks
    bigger than a /22 are not searched."""
    out: list[str] = []
    for ifname, a in _ip_addr():
        if ifname.startswith(_VIRTUAL) or a.get("family") != "inet" or a.get("scope") != "global":
            continue
        try:
            net = ipaddress.ip_interface(f"{a['local']}/{a.get('prefixlen', 32)}").network
        except ValueError:
            continue
        if not net.is_private or net.subnet_of(_TS4) or net.prefixlen < SWEEP_MIN_PREFIX:
            continue
        out += [str(h) for h in net.hosts() if str(h) != a["local"] and str(h) not in out]
    return out


def lan_sweep(port: int = PORT, hosts: list[str] | None = None) -> list[str]:
    """HOST:PORT for each host (default: lan_neighbours) that accepts a connection on `port`. Only a
    hint: the caller dials each and the pinned certificate decides."""
    return open_ports(port, lan_neighbours() if hosts is None else hosts)


def open_ports(port: int, hosts: list[str]) -> list[str]:
    """HOST:PORT for each of `hosts` accepting a connection on `port`, in the order given."""
    import socket
    from concurrent.futures import ThreadPoolExecutor
    from .wire import fmt_host

    def open_(h: str) -> bool:
        try:
            socket.create_connection((h, port), timeout=SWEEP_CONNECT_S).close()
            return True
        except OSError:
            return False
    if not hosts:
        return []
    with ThreadPoolExecutor(min(SWEEP_WORKERS, len(hosts))) as pool:
        found = list(pool.map(open_, hosts))
    return [fmt_host(h, port) for h, ok in zip(hosts, found) if ok]


def migrate_config_hosts() -> int:
    """config.toml's [peers.X] hosts into peers.json `addresses`, once; the file keeps the rest.
    X is a phone's name or a fingerprint prefix of 8+ characters; hosts of a phone nobody paired stay."""
    from .. import config
    from ..phonekeys import keep_once
    from . import identity
    raw = config._raw()
    tables = raw.get("peers")
    if not isinstance(tables, dict):
        return 0
    hosted = {k: t["hosts"] for k, t in tables.items() if isinstance(t, dict) and isinstance(t.get("hosts"), list)}
    if not hosted:
        return 0
    peers = identity.load_peers()
    matches = {key: [p for p in peers if key == p.name or (len(key) >= 8 and p.fp.startswith(key.lower()))]
               for key in hosted}
    done = set()
    for key, ps in matches.items():
        if len(ps) > 1:
            log.warning("config.toml [peers.%s] hosts left alone: %d phones are called that (%s); "
                        "set the hosts with a fingerprint prefix instead", key, len(ps),
                        ", ".join(p.name for p in ps))
    moved = 0
    for p0 in peers:
        bad, extra = [], []
        count = [0]

        def change(p):
            new = list(p.addresses)
            bad.clear(); extra.clear(); count[0] = 0
            for key, hs in hosted.items():
                if len(matches[key]) != 1 or matches[key][0].fp != p.fp:
                    continue
                for h in hs:
                    try:
                        n = normalize(str(h))
                    except ValueError:
                        bad.append(str(h))
                        continue
                    if n in new:
                        continue
                    if len(new) < MAX_ADDRESSES:
                        new.append(n)
                        count[0] += 1
                    else:
                        extra.append(n)
            p.addresses = new       # the only field touched; the rest is as the file has it now
        for key in hosted:
            if len(matches[key]) == 1 and matches[key][0].fp == p0.fp:
                done.add(key)
        identity.change_peer(p0.fp, change)
        moved += count[0]
        if bad:
            log.warning("phone %s: config.toml hosts not moved, not valid addresses: %s", p0.name, ", ".join(bad))
        if extra:
            log.warning("phone %s: config.toml hosts not moved, more than %d addresses: %s",
                        p0.name, MAX_ADDRESSES, ", ".join(extra))
    if not done:
        return 0
    keep_once(config.path(), ".hosts")
    for key in done:
        rest = {k: v for k, v in tables[key].items() if k != "hosts"}
        if rest:
            tables[key] = rest
        else:
            del tables[key]
    if tables:
        raw["peers"] = tables
    else:
        del raw["peers"]
    config._write(raw)
    return moved
