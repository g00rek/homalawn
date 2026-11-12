"""One TLS connection to the phone's link, speaking newline-delimited JSON (spec D6).

The phone's certificate is not checked by TLS (there is no CA); callers compare `peer_fp` with
the pin, or, while pairing, derive the four words from `peer_der`.
"""
from __future__ import annotations

import hashlib
import json
import socket
import ssl
from typing import Any

PORT = 47100
MAX_LINE = 1 << 20   # spec D6: a JSON line is at most 1 MiB (newline excluded)
MAX_BLOB = 32 << 20  # and a blob at most 32 MiB


class LinkError(Exception):
    """The phone answered with an error (`code` is its stable machine code; `detail` is the whole
    error object, for the fields some errors add, e.g. `expected` on `bad_offset`)."""

    def __init__(self, code: str, message: str, detail: dict | None = None):
        super().__init__(f"{code}: {message}")
        self.code = code
        self.message = message
        self.detail = dict(detail or {})


def parse_host(spec: str) -> tuple[str, int]:
    """HOST, HOST:PORT, [IPv6] or [IPv6]:PORT."""
    if spec.startswith("["):
        host, _, rest = spec[1:].partition("]")
        return host, int(rest[1:]) if rest.startswith(":") else PORT
    if spec.count(":") == 1:
        host, port = spec.split(":")
        return host, int(port)
    return spec, PORT


def fmt_host(host: str, port: int) -> str:
    return f"[{host}]:{port}" if ":" in host else f"{host}:{port}"


class Conn:
    def __init__(self, host: str, port: int, cert: str, key: str, timeout: float = 10.0,
                 handshake_timeout: float | None = None):
        ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        ctx.minimum_version = ssl.TLSVersion.TLSv1_3
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE  # pinned by the caller, see peer_fp
        ctx.load_cert_chain(cert, key)
        raw = socket.create_connection((host, port), timeout=timeout)
        if handshake_timeout is not None:
            raw.settimeout(handshake_timeout)
        try:
            self.sock = ctx.wrap_socket(raw)
        except BaseException:
            raw.close()
            raise
        der = self.sock.getpeercert(binary_form=True)
        if not der:
            self.close_raw()
            raise ConnectionError("the other side sent no certificate")
        self.peer_der: bytes = der
        self.peer_fp = hashlib.sha256(self.peer_der).hexdigest()
        self._f = self.sock.makefile("rb")
        self._n = 0

    def close_raw(self) -> None:
        try:
            self.sock.close()
        except OSError:
            pass

    def close(self) -> None:
        for c in (self._f, self.sock):
            try:
                c.close()
            except OSError:
                pass

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()

    def call(self, method: str, params: dict | None = None, timeout: float | None = None) -> Any:
        return self.request(method, params, timeout=timeout)[0]

    def request(self, method: str, params: dict | None = None, *, blob: bytes | None = None,
                timeout: float | None = None) -> tuple[Any, bytes | None]:
        """One request; `blob` travels as a blob frame right after the request line. Returns
        (result, blob bytes the phone answered with, or None)."""
        if blob is not None and len(blob) > MAX_BLOB:
            raise ValueError(f"{len(blob)} bytes is more than the link carries in one piece ({MAX_BLOB})")
        self._n += 1
        req: dict[str, Any] = {"id": self._n, "method": method}
        if params is not None:
            req["params"] = params
        if timeout is not None:
            self.sock.settimeout(timeout)
        out = json.dumps(req).encode() + b"\n"
        if blob is not None:
            out += json.dumps({"id": self._n, "blob": len(blob)}).encode() + b"\n"
        try:
            self.sock.sendall(out)
            if blob:
                self.sock.sendall(blob)
        except OSError:
            # the phone may have refused before reading the blob (e.g. not_paired) and hung up;
            # its answer can still be waiting to be read
            msg = self._read_msg_or_none()
            if msg is None:
                raise
            return self._answer(msg)
        msg = self._read_msg_or_none()
        if msg is None:
            raise ConnectionError("the phone closed the connection")
        return self._answer(msg)

    def _read_msg_or_none(self) -> dict | None:
        try:
            line = self._f.readline(MAX_LINE + 1)  # MAX_LINE bytes plus the newline
        except OSError:
            return None
        if not line:
            return None
        if not line.endswith(b"\n"):
            raise ConnectionError("the phone sent a line longer than the link allows")
        try:
            msg = json.loads(line)
        except ValueError:
            raise ConnectionError("the phone sent something that is not JSON")
        if not isinstance(msg, dict):
            raise ConnectionError("the phone sent an answer that is not a JSON object")
        return msg

    def _answer(self, msg: dict) -> tuple[Any, bytes | None]:
        if msg.get("id") != self._n and not ("error" in msg and msg.get("id") is None):
            # an error without an id is the phone's answer to a broken frame; anything else must be ours
            raise ConnectionError(f"answer for request {msg.get('id')}, expected {self._n}")
        if "error" in msg:
            e = msg["error"] if isinstance(msg["error"], dict) else {}
            raise LinkError(str(e.get("code", "error")), str(e.get("message", "")), e)
        if msg.get("id") != self._n:
            raise ConnectionError(f"answer for request {msg.get('id')}, expected {self._n}")
        if "blob" not in msg:
            return msg.get("result"), None
        n = msg["blob"]
        if not isinstance(n, int) or isinstance(n, bool) or not 0 <= n <= MAX_BLOB:
            raise ConnectionError(f"the phone announced a blob of {n!r} bytes")
        data = self._f.read(n)
        if len(data) != n:
            raise ConnectionError("the phone closed the connection in the middle of a blob")
        return msg.get("result"), data

    def readable_eof(self) -> bool:
        """Between requests the phone never sends anything; readable now means it has hung up
        (idle timeout, restart). Never blocks."""
        import select
        try:
            if self.sock.pending():
                return True
            r, _, _ = select.select([self.sock], [], [], 0)
            return bool(r)
        except (OSError, ValueError):
            return True
