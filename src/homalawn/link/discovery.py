"""mDNS hints for where a paired phone is (spec D3: only a hint; the pin decides).

The phone advertises `_omalawn._tcp` on port 47100 as "OmaLawn" (kept: wire compatibility) with TXT `v=1` only (no model, no
fingerprint: nothing that tells phones apart or tracks one across networks). The desktop tries
every hit and the pinned certificate decides; older phones also sent `fp=<16 hex>`, which is
still used to try a matching hit first. We ask the local Avahi daemon with `avahi-browse -rpt _omalawn._tcp`
(-r resolve, -p parsable, -t terminate after the current list). Resolved entries are lines of
`=;iface;IPv4|IPv6;name;type;domain;hostname;address;port;"txt" "txt"...` (format checked against
avahi-browse 0.9 output on this machine). No avahi, a stopped daemon or a timeout: no hints.
"""
from __future__ import annotations

import shutil
import subprocess
from dataclasses import dataclass

TIMEOUT_S = 3.0
AVAHI = "avahi-browse"
MISSING = ("This computer cannot look for phones on the network: avahi-browse is not installed (install the avahi "
           "package and start avahi-daemon).")


def missing(which=None) -> bool:
    """True when avahi-browse is not on this computer, so looking for phones finds nothing."""
    return (which or shutil.which)(AVAHI) is None


@dataclass(frozen=True)
class Hit:
    name: str
    address: str      # IP literal; IPv6 link-local carries %iface
    port: int
    fp_prefix: str    # lowercase hex from TXT fp=, "" when absent
    hostname: str = ""  # the advertising device's mDNS host name (one phone: one host name)

    @property
    def host(self) -> str:
        from .wire import fmt_host
        return fmt_host(self.address, self.port)


def _unescape(s: str) -> str:
    """avahi's parsable mode writes special characters of names as \\DDD (decimal)."""
    out, i = [], 0
    while i < len(s):
        if s[i] == "\\" and s[i + 1:i + 4].isdigit() and len(s[i + 1:i + 4]) == 3:
            out.append(chr(int(s[i + 1:i + 4])))
            i += 4
        else:
            out.append(s[i])
            i += 1
    return "".join(out)


def _txt(field: str) -> dict[str, str]:
    out = {}
    for part in field.split('" "'):
        part = part.strip().strip('"')
        k, eq, v = part.partition("=")
        if eq:
            out[k.lower()] = v
    return out


def parse(text: str) -> list[Hit]:
    hits, seen = [], set()
    for line in text.splitlines():
        if not line.startswith("="):
            continue
        f = line.split(";", 9)
        if len(f) < 9:
            continue
        iface, name, hostname, addr, port = f[1], _unescape(f[3]), f[6], f[7], f[8]
        try:
            port_n = int(port)
        except ValueError:
            continue
        if addr.lower().startswith("fe80:") and "%" not in addr:
            addr = f"{addr}%{iface}"
        fp = _txt(f[9]).get("fp", "").lower() if len(f) > 9 else ""
        key = (addr, port_n)
        if key in seen:
            continue
        seen.add(key)
        hits.append(Hit(name=name, address=addr, port=port_n, fp_prefix=fp, hostname=hostname))
    return hits


def browse(timeout: float = TIMEOUT_S) -> list[Hit]:
    """Resolved hits within `timeout`. avahi waits ~5 s for an advert that does not resolve (a
    phone asleep, a stale record), so on a timeout what did resolve by then is still used."""
    try:
        # `_omalawn._tcp`: kept, wire compatibility with phones already set up
        p = subprocess.run([AVAHI, "-rpt", "_omalawn._tcp"], capture_output=True, text=True,
                           timeout=timeout)
    except subprocess.TimeoutExpired as e:
        out = e.stdout or ""
        return parse(out.decode(errors="replace") if isinstance(out, bytes) else out)
    except (FileNotFoundError, OSError):
        return []
    return parse(p.stdout)


def for_peer(fp: str, hits: list[Hit]) -> list[Hit]:
    """Hits that may be the phone with this fingerprint, in the order to try them: one that names
    this fingerprint first, then those that name none; one naming another fingerprint is dropped.
    The TLS pin decides."""
    fp = fp.lower()
    named = [h for h in hits if len(h.fp_prefix) >= 8 and fp.startswith(h.fp_prefix)]
    return named + [h for h in hits if not h.fp_prefix]


def _rank(h: Hit) -> int:
    """Which of one phone's addresses to dial: IPv4, then routable IPv6, then link-local IPv6."""
    a = h.address.lower()
    return 0 if ":" not in a else (2 if a.startswith("fe80:") else 1)


def phones(hits: list[Hit]) -> list[Hit]:
    """One hit per advertising device (its IPv4 and IPv6 answers share a host name), the best
    address of each, in the order found. Used by `homalawn pair` without HOST."""
    groups: dict[str, list[Hit]] = {}
    for h in hits:
        groups.setdefault(h.hostname or h.address, []).append(h)
    return [min(g, key=_rank) for g in groups.values()]
