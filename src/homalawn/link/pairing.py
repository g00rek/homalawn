"""Pairing this computer with a phone, and forgetting it again (spec D5).

The desktop dials the phone unpinned and sends `link.pair_request {name, commit}`, where commit =
SHA-256("omalawn-commit-v1" ‖ nonce_desktop); the phone answers `{nonce_phone, name, expires_in}`.
Only then does the desktop send `link.pair_reveal {nonce_desktop}`: the phone checks it against
the commitment, answers `{words}` and shows "Pair with <name>? <words>" with Review/Reject (Review
opens Lawnchair's confirmation screen, where Accept is tapped). A fake
phone has fixed its nonce before it sees ours, so it cannot search for one that gives matching
words. Both sides derive the words themselves (sas.py); the desktop prints ITS OWN words, then
`link.pair_wait {}` blocks until the tap or the 120 s deadline; an accepted answer carries the
phone's name (an unpaired `link.hello` tells nothing about the phone). Only after Accept on the
phone and "yes" here is the phone's fingerprint pinned in peers.json; a "no" here asks the phone
to forget this computer again (`link.unpair`).

The phone takes pairing requests while Lawnchair's home screen is in front and the phone is
unlocked, or while pairing is open on it ("Pair a computer" on the Computers screen in Homa Lawn's settings opens
Lawnchair's pairing screen; it stays open while that screen shows, up to 5 minutes), and never
from itself (an app on the phone). While it answers `pairing_closed` the
desktop says once how to open it and asks again every 2 s for up to 5 minutes, so the order in
which the two sides are started does not matter.
"""
from __future__ import annotations

import os
import socket
import threading
import time
from typing import Callable

from . import identity, sas
from .identity import Peer
from .wire import Conn, LinkError, fmt_host, parse_host

WAIT_S = 150  # the phone answers pair_wait by its 120 s deadline at the latest
CLOSED_RETRY_S = 2.0   # while the phone says pairing_closed, ask again this often ...
CLOSED_WAIT_S = 300.0  # ... for up to 5 minutes
OPEN_ON_PHONE = ("On the phone: unlock it and show its home screen (or open Homa Lawn's settings › "
                 "Computers › Pair a computer)")


def _sleep(s: float) -> None:
    time.sleep(s)


class PairError(Exception):
    """Pairing did not happen; the message is for the user. Nothing is pinned on this computer."""


_PHONE_SAYS = {
    "notifications_off": "The phone cannot show the pairing request because notifications for Homa Lawn are off. "
                         "Unlock the phone and go to its home screen; allow notifications when it asks "
                         "(or in Homa Lawn's settings › Permissions › Notifications), then run pair again.",
    "pair_busy": "The phone is already showing another pairing request. Answer it, or wait up to 2 minutes, "
                 "then try again.",
    "rate_limited": "The phone refused: too many pairing requests in the last 10 minutes. {message}",
    "pairing_closed": "Pairing is closed on the phone. " + "{open}",
    "local_pairing": "The phone refused: pairing requests from the phone itself are not accepted.",
}


def _explain(e: LinkError) -> str:
    t = _PHONE_SAYS.get(e.code)
    return t.format(message=e.message, open=OPEN_ON_PHONE) if t else f"The phone refused the pairing request ({e.code}: {e.message})"


def _nonce(r) -> bytes:
    """The phone's 16-byte nonce from its pair_request answer; anything else ends pairing cleanly."""
    v = r.get("nonce_phone") if isinstance(r, dict) else None
    if isinstance(v, str) and len(v) == 32:
        try:
            return bytes.fromhex(v)
        except ValueError:
            pass
    raise PairError("Stopped: the phone sent a malformed pairing answer (nonce). Nothing was paired.")


def pair(host: str, port: int, *, confirm: Callable[[list[str]], bool], say: Callable[[str], None],
         name: str | None = None, timeout: float = 10.0, show_words: Callable[[list[str]], None] | None = None,
         retry_s: float = CLOSED_RETRY_S, wait_closed_s: float = CLOSED_WAIT_S,
         sleep: Callable[[float], None] | None = None, clock: Callable[[], float] | None = None,
         stop: threading.Event | None = None, on_conn: Callable[[Conn], None] | None = None) -> Peer:
    """Pair with the phone at host:port. While the phone answers `pairing_closed`, say once how to
    open pairing there and ask again every `retry_s` seconds for up to `wait_closed_s` (Ctrl+C
    stops it: KeyboardInterrupt passes through, nothing is pinned). A phone that already trusts
    this computer, which has no record of it, is asked to forget it first; the pairing then pins
    both sides afresh. `stop` (set from another thread) ends the waiting; `on_conn` is handed every
    connection opened, so the caller can close one that is blocked."""
    own_sleep = sleep
    sleep = sleep or _sleep
    clock = clock or time.monotonic
    ident = identity.ensure()
    name = (name or socket.gethostname() or "computer")[:64]
    addr = fmt_host(host, port)
    deadline: float | None = None  # set once the phone said pairing_closed
    forgot = False

    def check_stop() -> None:
        if stop is not None and stop.is_set():
            raise PairError("Stopped. Nothing was paired.")

    def nap(s: float) -> None:
        if stop is not None and own_sleep is None:
            stop.wait(s)
        else:
            sleep(s)
        check_stop()

    def waiting() -> bool:
        return deadline is not None and clock() < deadline

    while True:
        check_stop()
        try:
            conn = Conn(host, port, str(ident.cert), str(ident.key), timeout=timeout)
        except OSError as e:
            if waiting():  # e.g. the phone limits a stranger's reconnects: keep trying quietly
                nap(retry_s)
                continue
            raise PairError(f"Cannot reach the phone at {addr}: {e}")
        if on_conn is not None:
            on_conn(conn)
        with conn:
            try:
                hello = conn.call("link.hello")
                if hello.get("paired"):
                    known = {p.fp: p for p in identity.load_peers()}
                    if conn.peer_fp in known:
                        say(f"Already paired with {known[conn.peer_fp].name}.")
                        return known[conn.peer_fp]
                    # the phone trusts our key but we have no record of it: start over
                    if forgot:
                        raise PairError("The phone still lists this computer as paired after forgetting it; "
                                        f"forget it there (Homa Lawn's settings › Computers › {name} "
                                        "› Forget) and try again.")
                    say("The phone already knows this computer; asking it to forget it and pairing again.")
                    conn.call("link.unpair")
                    forgot = True
                    continue
                while True:  # on one connection while it lasts (the phone drops a stranger after 30 s)
                    check_stop()
                    nonce_desktop = os.urandom(16)
                    try:
                        r = conn.call("link.pair_request",
                                      {"name": name, "commit": sas.commitment(nonce_desktop).hex()})
                        break
                    except LinkError as e:
                        if e.code != "pairing_closed":
                            raise PairError(_explain(e))
                    if deadline is None:
                        deadline = clock() + wait_closed_s
                        say(OPEN_ON_PHONE)
                    if not waiting():
                        raise PairError(f"Pairing was not opened on the phone within {int(wait_closed_s // 60)} "
                                        "minutes. Nothing was paired.")
                    nap(retry_s)
                nonce_phone = _nonce(r)
                revealed = conn.call("link.pair_reveal", {"nonce_desktop": nonce_desktop.hex()})
            except LinkError as e:
                raise PairError(_explain(e))
            except (OSError, ConnectionError) as e:
                if waiting():  # the phone hung up the idle stranger connection: dial again
                    continue
                raise PairError(f"The connection to the phone at {addr} broke ({e}); nothing was paired.")
            return _finish(conn, ident, addr, host, port, r, nonce_phone, nonce_desktop, revealed,
                           name=name, confirm=confirm, say=say, show_words=show_words)


def _finish(conn: Conn, ident, addr: str, host: str, port: int, r, nonce_phone: bytes, nonce_desktop: bytes,
            revealed, *, name, confirm, say, show_words) -> Peer:
    words = sas.sas(conn.peer_der, ident.cert_der, nonce_phone, nonce_desktop)
    if not isinstance(revealed, dict) or revealed.get("words") != words:
        # the phone's own derivation disagrees: a different certificate is in the middle
        raise PairError("Stopped: the phone derived different words than this computer, so something "
                        "between them is not the phone. Nothing was paired; reject the request on the phone.")
    say(f"Pairing with the phone at {addr}.")
    if show_words is not None:
        show_words(words)
    else:
        say(f"Words: {' '.join(words)}")
    say(f"Confirm on the phone (you have {r.get('expires_in', 120)} seconds): Accept there only if it shows "
        "the same four words.")
    try:
        outcome = conn.call("link.pair_wait", {}, timeout=WAIT_S)
    except LinkError as e:
        raise PairError(_explain(e))
    except (OSError, ConnectionError) as e:
        raise PairError(f"The connection to the phone broke while waiting for the tap ({e}); nothing was paired.")
    if not outcome.get("accepted"):
        why = {"rejected": "Reject was tapped on the phone", "timeout": "nobody answered on the phone within 2 minutes",
               "dismissed": "the request was dismissed on the phone"}.get(outcome.get("outcome"), "the phone did not accept")
        raise PairError(f"Not paired: {why}. Nothing was saved on either side.")
    if not confirm(words):
        try:
            conn.call("link.unpair")
            gone = "The phone has forgotten this computer again."
        except (LinkError, OSError, ConnectionError):
            gone = ("The phone may still trust this computer; forget it there: Homa Lawn's settings "
                    f"› Computers › {name} › Forget.")
        raise PairError(f"Not paired: the words did not match on this computer. {gone}")
    peer = Peer(fp=conn.peer_fp, name=_clean(outcome.get("name") or outcome.get("device")) or "phone",
                hosts=[addr], last_lan=identity.lan_address(host, port))
    identity.record_pairing(peer)
    return peer


def _clean(name) -> str:
    """The phone's name as the computer stores it: text only, no control or format characters."""
    if not isinstance(name, str):
        return ""
    import unicodedata
    return "".join(c for c in name if unicodedata.category(c) not in ("Cc", "Cf")).strip()[:64]


def unpair(peer: Peer, *, timeout: float = 3.0) -> str | None:
    """Ask the phone to forget this computer, then forget the phone here. Tries its addresses in
    the order every command dials them. Returns the address it was told through, or None when none
    answered (the local record is removed anyway)."""
    from .client import LinkTransport
    ident = identity.ensure()
    told = None
    for addr in LinkTransport(peer).candidates():
        host, port = parse_host(addr)
        try:
            with Conn(host, port, str(ident.cert), str(ident.key), timeout=timeout) as c:
                if c.peer_fp != peer.fp:
                    continue  # some other device answers there; never tell it anything
                c.call("link.unpair")
                told = addr
                break
        except (OSError, ConnectionError, LinkError):
            continue
    identity.remove_peer(peer.fp)
    return told
