"""An in-process fake of the phone's link server (spec D6, Task 4 wire format) for desktop tests.

TLS 1.3 on 127.0.0.1 with an openssl-made certificate, newline-delimited JSON, blob frames
(`{"id":n,"blob":len}` + bytes, both directions), pin classification (an unpaired computer gets
`not_paired` for everything but hello/pairing and is hung up on), idle close. `launcher.*` methods
run against a `FakeDevice` from test_session, the same fake the adb tests use — but only through
the link's own semantics (blobs, staged files); the fake has no shell to offer at all.
"""
from __future__ import annotations

import hashlib
import json
import re
import socket
import ssl
import subprocess
import threading
import time
from pathlib import Path

from homalawn.transport import ProviderError

HOME = "/data/user/0/app.homalawn.debug"
OPEN = {"link.hello", "link.pair_request", "link.pair_reveal", "link.pair_wait"}
KNOWN = {"launcher.ping", "launcher.ensure_loaded", "launcher.prefs_list", "launcher.pref_set", "launcher.prefs_set_many",
         "launcher.wallpaper_info", "launcher.wallpaper_set", "launcher.wallpaper_lock_reset"}
SCREEN_WRITES = {"launcher.wallpaper_set", "launcher.pref_set", "launcher.prefs_set_many", "launcher.wallpaper_lock_reset"}
BLOB_IN = {"launcher.layout_import", "launcher.stage_file", "launcher.update_offer"}


def make_cert(d: Path, name: str = "phone") -> tuple[Path, Path, bytes]:
    d.mkdir(parents=True, exist_ok=True)
    key, cert = d / f"{name}.key", d / f"{name}.pem"
    if not cert.exists():
        subprocess.run(["openssl", "req", "-x509", "-newkey", "ec", "-pkeyopt", "ec_paramgen_curve:prime256v1",
                        "-nodes", "-keyout", str(key), "-out", str(cert), "-days", "2", "-subj", "/CN=OmaLawn link"],
                       check=True, capture_output=True)
    der = subprocess.run(["openssl", "x509", "-in", str(cert), "-outform", "der"], check=True,
                         capture_output=True).stdout
    return key, cert, der


class FakeLinkPhone:
    def __init__(self, cert: tuple[Path, Path, bytes], dev=None, *, trust: list[Path] = (), pinned=(),
                 name="Fake Pixel", idle_close_s: float | None = None):
        key, crt, self.der = cert
        self.fp = hashlib.sha256(self.der).hexdigest()
        self.ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        self.ctx.minimum_version = ssl.TLSVersion.TLSv1_3
        self.ctx.load_cert_chain(str(crt), str(key))
        self.ctx.verify_mode = ssl.CERT_REQUIRED  # like the phone: no client certificate, no TLS
        for t in trust:
            self.ctx.load_verify_locations(str(t))
        self.dev = dev
        self.pinned = set(pinned)
        self.name = name
        self.idle_close_s = idle_close_s
        self.silent: set[str] = set()       # methods it never answers
        self.hang_up_on: set[str] = set()   # methods after which it closes without answering
        self.raw_answers: dict[str, bytes] = {}  # method -> bytes sent instead of an answer (hostile phone)
        self.wallpaper = lambda: b"\x89PNG wallpaper"
        self.lock_wallpaper = lambda: self.dev.lock_now  # what launcher.wallpaper_backup {which: lock} reads
        self.not_paired_detail: dict = {}  # merged into the not_paired error (e.g. {"forgotten": True})
        self.handlers = {}  # method -> fn(params) -> (result, blob or None); checked first
        # patch 0031's look book, for the one computer the tests use (per caller fingerprint):
        # look_put stores, look_show puts the settings and the pictures it sent on FakeDevice.
        # False: a build without it (look_* answer unknown_method).
        self.look_book = True
        self.looks: dict[str, dict] = {}       # caller fp -> {key, changed_at, theme, prefs, pictures, sent}
        self.look_applied: tuple[str, str] | None = None   # (fp, key): the screen holds exactly this look
        self.look_source: str | None = None    # another computer's fp as the source; None: the caller
        self.apps = [{"package": "com.a", "activity": "com.a.Main", "label": "A"},
                     {"package": "com.b", "activity": "com.b.X", "label": "B"}]
        self.widgets = [{"provider": "com.w/com.w.W", "label": "W", "cells": [2, 1]}]
        self.log: list[tuple[int, str, dict, int | None]] = []   # (connection no, method, params, blob length)
        self.lines: list[bytes] = []                                # every line received, raw
        self.handshakes = 0
        self.conns = 0
        self.srv = socket.create_server(("127.0.0.1", 0))
        self.port = self.srv.getsockname()[1]
        self.addr = f"127.0.0.1:{self.port}"
        self._socks: list[socket.socket] = []
        threading.Thread(target=self._loop, daemon=True).start()

    # -- server ---------------------------------------------------------------
    def _loop(self):
        while True:
            try:
                raw, _ = self.srv.accept()
            except OSError:
                return
            threading.Thread(target=self._serve, args=(raw,), daemon=True).start()

    def close(self):
        try:
            self.srv.shutdown(socket.SHUT_RDWR)  # wakes the accept() in the server thread
        except OSError:
            pass
        self.srv.close()
        for s in self._socks:
            try:
                s.close()
            except OSError:
                pass

    def drop_all(self):
        """Hang up every open connection (like the phone restarting)."""
        for s in list(self._socks):
            try:
                s.shutdown(socket.SHUT_RDWR)
                s.close()
            except OSError:
                pass

    def methods(self, conn: int | None = None):
        return [m for c, m, _, _ in self.log if conn is None or c == conn]

    def _serve(self, raw):
        try:
            s = self.ctx.wrap_socket(raw, server_side=True)
        except (ssl.SSLError, OSError):
            return
        self.handshakes += 1
        self.conns += 1
        no = self.conns
        self._socks.append(s)
        desk_fp = hashlib.sha256(s.getpeercert(binary_form=True)).hexdigest()
        s.settimeout(self.idle_close_s)
        f = s.makefile("rb")
        try:
            while True:
                line = f.readline()
                if not line:
                    return
                self.lines.append(line)
                req = json.loads(line)
                if not self._one(no, s, f, req, desk_fp):
                    return
        except (OSError, ValueError):
            return
        finally:
            try:
                s.close()
            except OSError:
                pass

    def _send(self, s, obj, blob=None):
        s.sendall(json.dumps(obj).encode() + b"\n" + (blob or b""))

    def _one(self, no, s, f, req, desk_fp) -> bool:
        rid, m, p = req.get("id"), req.get("method", ""), req.get("params") or {}
        paired = desk_fp in self.pinned
        if not paired and m not in OPEN:
            self.log.append((no, m, p, None))
            self._send(s, {"id": rid, "error": {"code": "not_paired", "message": "this computer is not paired with the phone", **self.not_paired_detail}})
            return False
        blob = None
        if m in BLOB_IN:
            hl = f.readline()
            self.lines.append(hl)
            head = json.loads(hl)
            assert head == {"id": rid, "blob": head["blob"]}, head
            blob = f.read(head["blob"])
            assert len(blob) == head["blob"]
        self.log.append((no, m, p, None if blob is None else len(blob)))
        if m in self.silent:
            time.sleep(3600)
        if m in self.hang_up_on:
            return False
        if m in self.raw_answers:
            s.sendall(self.raw_answers[m].replace(b"$ID", str(rid).encode()))
            return True
        try:
            res, out = self._dispatch(m, p, blob, paired, desk_fp)
        except ProviderError as e:
            self._send(s, {"id": rid, "error": {**e.data, "code": e.code or "failed", "message": str(e)}})
            return True
        if out is not None:
            self._send(s, {"id": rid, "blob": len(out), "result": res}, out)
        else:
            self._send(s, {"id": rid, "result": res})
        return True

    def _dispatch(self, m, p, blob, paired, caller=None):
        d = self.dev
        if m in SCREEN_WRITES:  # written outside the look book: the screen no longer holds its look
            self.look_applied = None
        if m in self.handlers:  # a handler for a method that takes a blob gets it too
            return self.handlers[m](p, blob) if m in BLOB_IN else self.handlers[m](p)
        if m in ("launcher.look_put", "launcher.look_show") and self.look_book:
            return self._look(m, p, caller), None
        if m == "link.hello":  # like the phone: an unpaired computer learns only paired:false and v
            if not paired:
                return {"paired": False, "v": 1}, None
            return {"device": "fake", "name": self.name, "fp": self.fp, "paired": True, "v": 1, "version": "t"}, None
        if m == "link.ping":
            return {"t": int(time.time() * 1000)}, None
        if m == "launcher.ping":
            return {"version": "15-test", "package": "app.homalawn.debug", **d.call("ping")}, None
        if m == "launcher.ensure_loaded":
            r = d.call("ensure_loaded")
            return {"loaded": r.get("loaded", r.get("model_loaded")), "db": r.get("db")}, None
        if m == "launcher.apps_list":
            return self.apps, None
        if m == "launcher.widgets_list":
            return self.widgets, None
        if m == "launcher.wallpaper_backup":
            if p.get("which") == "lock":
                return {"how": "file"}, self.lock_wallpaper()
            return {"how": "file"}, self.wallpaper()
        if m == "launcher.layout_export":
            r = d.call("layout_export")
            data = d.files.pop(d.rel(r["path"]))
            return {"db": r["db"], "size": len(data), "how": "copy"}, data
        if m == "launcher.layout_import":
            name = f"files/omalawn/import-{time.time_ns()}.db"
            d.files[name] = blob
            try:
                return d.call("layout_import", f"{HOME}/{name}", {"db": p["db"]} if p.get("db") else None), None
            finally:
                d.files.pop(name, None)
        if m == "launcher.stage_file":
            d.files[f"files/omalawn/{p['name']}"] = blob
            return {"path": f"{HOME}/files/omalawn/{p['name']}"}, None
        if m == "launcher.widget_status":
            return d.call("widget_status", str(p["id"])), None
        if m in KNOWN:
            return d.call(m.split(".", 1)[1], None, {k: str(v) for k, v in p.items()} or None), None
        raise ProviderError(f"unknown_method: unknown method {m}", code="unknown_method")

    def _look(self, m, p, fp):
        """LookBook.putFromLink / showFromLink for the calling computer; the source is the caller
        unless `look_source` names another computer."""
        d = self.dev
        src = self.look_source or fp
        if m == "launcher.look_put":
            if not re.fullmatch(r"[0-9a-f]{16}", str(p.get("key", ""))):
                raise ProviderError("bad_params: key must be 16 lowercase hex characters", code="bad_params")
            prefs = p.get("prefs") or []
            for w in prefs:   # OmalawnCommands.checkPrefWrites
                ok = w.get("store") in ("datastore", "shared") and (w.get("remove") or _typed(w.get("type"), w.get("value")))
                if not ok:
                    raise ProviderError(f"bad_params: prefs: {w}", code="bad_params")
            staged = p.get("pictures") or {}
            for part in staged:
                if part not in ("home", "lock", "both"):
                    raise ProviderError(f"bad_params: pictures.{part}", code="bad_params")
            pics = {}
            for part, path in staged.items():
                data = d.files.pop(d.rel(path))   # the phone moves the staged picture into its book
                for t in (("home", "lock") if part == "both" else (part,)):
                    pics[t] = data
            old = self.looks.get(fp, {})
            same = old.get("key") == p["key"]       # a re-send keeps the stored look's changed_at
            self.looks[fp] = {"key": p["key"], "changed_at": old["changed_at"] if same else p["changed_at"],
                              "theme": old["theme"] if same else p["theme"], "prefs": old["prefs"] if same else prefs,
                              "pictures": old.get("pictures", {}) | pics, "sent": set(pics)}
            return {"stored": True, "show": src == fp and self.look_applied != (fp, p["key"]),
                    "source": {"fp": src, "name": "This computer" if src == fp else "Other"},
                    "scope": "latest"}
        if src != fp:
            raise ProviderError("not_source: the phone shows another computer's wallpaper & style now", code="not_source")
        e = self.looks.get(fp)
        if e is None or e["key"] != p.get("key"):
            raise ProviderError("stale_key: send look_put again", code="stale_key")
        if e["prefs"]:
            self._dispatch("launcher.prefs_set_many", {"writes": e["prefs"]}, None, True, fp)
        for part in ("home", "lock"):   # home first, each on its own (setStream per screen)
            if part in e["sent"]:
                name = f"files/omalawn/look-show-{part}.img"
                d.files[name] = e["pictures"][part]
                d.call("wallpaper_set", None, {"path": f"{HOME}/{name}", "which": part})
                d.files.pop(name, None)
        self.look_applied = (fp, e["key"])
        return {"shown": True}


def _typed(t, v) -> bool:
    """A value the phone parses as its type (OmalawnCommands.validate)."""
    try:
        if t == "bool":
            return v in ("true", "false")
        if t == "int":
            int(v)
        elif t == "float":
            float(v)
        elif t != "string":
            return False
    except (TypeError, ValueError):
        return False
    return True
