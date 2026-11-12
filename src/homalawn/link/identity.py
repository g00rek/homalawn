"""This computer's link identity and the phones it has paired with.

Everything lives in `<config dir>/link/` (default ~/.config/homalawn/link/; the config dir follows
HOMALAWN_CONFIG): `key.pem` (EC P-256, mode 600), `cert.pem` (self-signed, made with the openssl
CLI), `peers.json` (mode 600). Fingerprints are lowercase hex SHA-256 of the DER certificate.
"""
from __future__ import annotations

import contextlib
import fcntl
import hashlib
import ipaddress
import json
import os
import socket
import stat
import subprocess
import sys
import tempfile
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path

from .. import config


@dataclass
class Identity:
    dir: Path
    key: Path
    cert: Path
    cert_der: bytes
    fingerprint: str


@dataclass
class Peer:
    fp: str                                          # phone certificate SHA-256 (pinned)
    name: str                                        # the phone's name, from link.hello
    hosts: list[str] = field(default_factory=list)   # addresses tried (HOST:PORT), first worked
    last_lan: str | None = None                      # last private-LAN HOST:PORT it answered on
    paired: float = 0.0                              # unix time of pairing
    addresses: list[str] = field(default_factory=list)   # added by the user, at most 5 (spec section 8)
    learned: list[str] = field(default_factory=list)     # Tailscale addresses the phone reported, at most 4
    last_ok: str | None = None                           # the address that answered last


def link_dir() -> Path:
    return config.path().parent / "link"


class IdentityError(RuntimeError):
    """This computer's link key cannot be made or read; the message is one sentence saying what to do."""


NO_OPENSSL = ("Homa Lawn needs the openssl command to make this computer's pairing key, and it is not installed: "
              "install openssl (on Arch and Omarchy: `sudo pacman -S openssl`), then try again.")


def _openssl(*args: str) -> bytes:
    try:
        return subprocess.run(["openssl", *args], check=True, capture_output=True, timeout=60).stdout
    except FileNotFoundError:
        raise IdentityError(NO_OPENSSL)
    except subprocess.TimeoutExpired:
        raise IdentityError(f"openssl {args[0]} did not finish within 60 s; try again.")
    except subprocess.CalledProcessError as e:
        raise IdentityError(f"openssl {args[0]} failed, so this computer's pairing key could not be made or read: "
                            f"{e.stderr.decode(errors='replace').strip() or 'no details'}.")


def ensure() -> Identity:
    """Create the key and certificate once; afterwards just (re)check modes and load."""
    d = link_dir()
    d.mkdir(parents=True, exist_ok=True)
    os.chmod(d, 0o700)
    key, cert = d / "key.pem", d / "cert.pem"
    if not (key.is_file() and cert.is_file()):
        name = (socket.gethostname() or "computer")[:48]
        tmp = Path(tempfile.mkdtemp(dir=d))
        try:
            os.chmod(tmp, 0o700)
            tkey, tcert = tmp / "key.pem", tmp / "cert.pem"
            old = os.umask(0o077)
            try:
                _openssl("req", "-x509", "-newkey", "ec", "-pkeyopt", "ec_paramgen_curve:prime256v1",
                         "-nodes", "-keyout", str(tkey), "-out", str(tcert), "-days", "10950",
                         "-subj", f"/CN=Homa Lawn {name}")
            finally:
                os.umask(old)
            os.chmod(tkey, 0o600)
            os.replace(tkey, key)
            os.replace(tcert, cert)
        finally:
            for f in tmp.iterdir():
                f.unlink()
            tmp.rmdir()
    mode = stat.S_IMODE(os.stat(key).st_mode)
    if mode & 0o077:  # group or others could read it
        print(f"Warning: {key} was readable by others (mode {mode:o}); the link key may have been exposed. "
              "Fixed the mode; to be safe delete the link folder and run `homalawn pair` again.", file=sys.stderr)
        os.chmod(key, 0o600)
    der = _openssl("x509", "-in", str(cert), "-outform", "der")
    return Identity(dir=d, key=key, cert=cert, cert_der=der, fingerprint=hashlib.sha256(der).hexdigest())


def _peers_file() -> Path:
    return link_dir() / "peers.json"


def load_peers() -> list[Peer]:
    p = _peers_file()
    if not p.is_file():
        return []
    raw = json.loads(p.read_text()).get("peers", [])
    return [Peer(**{k: v for k, v in r.items() if k in Peer.__dataclass_fields__}) for r in raw]


def _write_peers(peers: list[Peer]) -> None:
    d = link_dir()
    d.mkdir(parents=True, exist_ok=True)
    os.chmod(d, 0o700)
    fd, tmp = tempfile.mkstemp(dir=d, prefix=".peers-")
    try:
        with os.fdopen(fd, "w") as f:
            json.dump({"peers": [asdict(p) for p in peers]}, f, indent=2)
        os.chmod(tmp, 0o600)
        os.replace(tmp, _peers_file())
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


@contextlib.contextmanager
def _locked():
    """Exclusive lock for a read-modify-write of peers.json (the CLI, the window and the service all
    write it): flock on a sidecar file, held across the read and the write. Not re-entrant."""
    d = link_dir()
    d.mkdir(parents=True, exist_ok=True)
    os.chmod(d, 0o700)
    fd = os.open(d / "peers.json.lock", os.O_RDWR | os.O_CREAT, 0o600)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
        yield
    finally:
        os.close(fd)   # closing releases the lock


def save_peer(peer: Peer) -> None:
    """Add or replace (by fingerprint) a paired phone."""
    if not peer.paired:
        peer.paired = time.time()
    with _locked():
        peers = load_peers()
        i = next((i for i, p in enumerate(peers) if p.fp == peer.fp), None)
        if i is None:
            peers.append(peer)
        else:
            peers[i] = peer
        _write_peers(peers)


def record_pairing(peer: Peer) -> None:
    """Store a phone just paired. Pairing the same phone again keeps the addresses and learned
    addresses it already had; everything else is the new pairing's."""
    with _locked():
        peers = load_peers()
        i = next((i for i, p in enumerate(peers) if p.fp == peer.fp), None)
        if i is None:
            peer.paired = peer.paired or time.time()
            peers.append(peer)
        else:
            old = peers[i]
            peer.addresses, peer.learned = old.addresses, old.learned
            peer.paired = time.time()
            peers[i] = peer
        _write_peers(peers)


def update_peer(fp: str, **fields) -> bool:
    """Set only `fields` on the stored phone `fp` (re-read from the file, so what another process
    changed meanwhile stays). False when no such phone is stored; it is not added."""
    bad = set(fields) - (set(Peer.__dataclass_fields__) - {"fp"})
    if bad:
        raise TypeError(f"not a phone field: {', '.join(sorted(bad))}")
    return change_peer(fp, lambda p: [setattr(p, k, v) for k, v in fields.items()])


def change_peer(fp: str, change) -> bool:
    """`change(peer)` on the stored phone `fp`, read and written back under the lock, so nothing
    another process wrote meanwhile is lost. An exception from `change` writes nothing. False when
    no such phone is stored."""
    with _locked():
        peers = load_peers()
        p = next((p for p in peers if p.fp == fp), None)
        if p is None:
            return False
        change(p)
        _write_peers(peers)
        return True


def remove_peer(fp: str) -> bool:
    with _locked():
        peers = load_peers()
        keep = [p for p in peers if p.fp != fp]
        if len(keep) == len(peers):
            return False
        _write_peers(keep)
        return True


def lan_address(host: str, port: int) -> str | None:
    """HOST:PORT when host is a private-LAN IPv4/IPv6 literal (not loopback, not Tailscale's 100.64/10)."""
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        return None
    if ip.is_loopback or ip.is_link_local or ip in ipaddress.ip_network("100.64.0.0/10"):
        return None
    if ip.is_private:
        return f"[{host}]:{port}" if ip.version == 6 else f"{host}:{port}"
    return None
