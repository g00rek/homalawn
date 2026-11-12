"""Link server in Lawnchair, against the test AVD (HOMALAWN_SERIAL=emulator-5560).

The desktop reaches the phone's TCP 47100 through `adb forward`. Client identities are throwaway
EC P-256 certs made with the openssl CLI in a temp dir; one of them is pinned through the
debug-only provider method `debug_pin` (shell uid only, never over the link) and unpinned at
the end. Wire format (spec D6): JSON lines; a blob is a line {"id":n,"blob":len[,"result":{...}]}
followed by exactly len bytes. A request that carries bytes sends its JSON line, then the blob.

Optional soak (2 min each by default when enabled): HOMALAWN_LINK_SOAK_S=120 runs link.ping every
10 s with Settings in front, then with the screen off.
"""
import hashlib
import json
import os
import re
import socket
import sqlite3
import ssl
import subprocess
import threading
import time

import pytest

pytestmark = pytest.mark.device
SERIAL = os.environ.get("HOMALAWN_SERIAL")
if not SERIAL:
    pytest.skip("HOMALAWN_SERIAL not set", allow_module_level=True)

PKG = os.environ.get("HOMALAWN_PACKAGE") or "app.homalawn.debug"
PORT = 47100
# adb forward reaches the phone over loopback; `adb emu redir add tcp:47101:47100` goes through the
# emulator's NAT onto the phone's real interface, where the per-app network firewall applies
DIAL_PORT = int(os.environ.get("HOMALAWN_LINK_DIAL_PORT", PORT))
AUTH = f"content://{PKG}.omalawn"


def adb(*args, check=True):
    r = subprocess.run(["adb", "-s", SERIAL, *args], capture_output=True, text=True)
    if check and r.returncode:
        raise RuntimeError(f"adb {' '.join(args)}: {r.stderr or r.stdout}")
    return r.stdout


def provider(method, arg=None, **extras):
    cmd = ["shell", "content", "call", "--uri", AUTH, "--method", method]
    if arg is not None:
        cmd += ["--arg", arg]
    for k, v in extras.items():
        cmd += ["--extra", f"{k}:s:{v}"]
    return adb(*cmd)


def make_identity(d, name):
    key, cert = d / f"{name}.key", d / f"{name}.pem"
    subprocess.run(["openssl", "req", "-x509", "-newkey", "ec", "-pkeyopt", "ec_paramgen_curve:prime256v1",
                    "-nodes", "-keyout", str(key), "-out", str(cert), "-days", "2", "-subj", f"/CN={name}"],
                   check=True, capture_output=True)
    os.chmod(key, 0o600)
    der = subprocess.run(["openssl", "x509", "-in", str(cert), "-outform", "der"], check=True,
                         capture_output=True).stdout
    return {"cert": str(cert), "key": str(key), "fp": hashlib.sha256(der).hexdigest()}


class Conn:
    def __init__(self, ident=None, timeout=30):
        ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        ctx.minimum_version = ssl.TLSVersion.TLSv1_3
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
        if ident:
            ctx.load_cert_chain(ident["cert"], ident["key"])
        raw = socket.create_connection(("127.0.0.1", DIAL_PORT), timeout=timeout)
        self.s = ctx.wrap_socket(raw)
        self.peer_der = self.s.getpeercert(binary_form=True)
        self.f = self.s.makefile("rb")
        self.n = 0

    def close(self):
        try:
            self.f.close()
            self.s.close()
        except OSError:
            pass

    def send_line(self, obj):
        self.s.sendall(json.dumps(obj).encode() + b"\n")

    def read(self):
        line = self.f.readline()
        if not line:
            raise EOFError("connection closed")
        msg = json.loads(line)
        if "blob" in msg:
            msg["bytes"] = self.f.read(msg["blob"])
            assert len(msg["bytes"]) == msg["blob"]
        return msg

    def call(self, method, params=None, blob=None):
        self.n += 1
        req = {"id": self.n, "method": method}
        if params is not None:
            req["params"] = params
        self.send_line(req)
        if blob is not None:
            self.s.sendall(json.dumps({"id": self.n, "blob": len(blob)}).encode() + b"\n" + blob)
        msg = self.read()
        assert msg["id"] == self.n
        return msg


def run_as(*args, text=True):
    r = subprocess.run(["adb", "-s", SERIAL, "exec-out", "run-as", PKG, *args], capture_output=True)
    return r.stdout.decode() if text else r.stdout


def favorites(db_bytes, tmp_path, name):
    p = tmp_path / name
    p.write_bytes(db_bytes)
    with sqlite3.connect(p) as c:
        cols = [r[1] for r in c.execute("PRAGMA table_info(favorites)")]
        return cols, sorted(c.execute("SELECT * FROM favorites ORDER BY _id").fetchall(), key=repr)


@pytest.fixture(scope="module")
def ids(tmp_path_factory):
    adb("forward", f"tcp:{PORT}", f"tcp:{PORT}")
    adb("logcat", "-b", "crash", "-c")
    d = tmp_path_factory.mktemp("link-ids")
    pinned, stranger = make_identity(d, "homalawn-test-pinned"), make_identity(d, "homalawn-test-stranger")
    out = provider("debug_pin", pinned["fp"], name="homalawn-test")
    assert "ok=true" in out, out
    # every connection here comes through adb forward, i.e. from loopback: the shell-only toggle
    # lifts the per-address handshake limit for loopback (test_handshake_limit checks the limit)
    provider("debug_allow_local_pairing", "on")
    provider("debug_pair_reset")
    yield {"pinned": pinned, "stranger": stranger}
    provider("debug_allow_local_pairing", "off")
    provider("debug_pair_reset")
    provider("debug_unpin", pinned["fp"])
    assert adb("logcat", "-b", "crash", "-d").strip() == "", "crash logged during the link tests"
    assert '"default_home":true' in provider("ping")


def test_handshake_is_tls13_with_the_keystore_identity(ids, tmp_path):
    c = Conn(ids["stranger"])
    try:
        assert c.s.version() == "TLSv1.3"
        (tmp_path / "phone.der").write_bytes(c.peer_der)
        text = subprocess.run(["openssl", "x509", "-inform", "der", "-in", str(tmp_path / "phone.der"),
                               "-noout", "-text"], check=True, capture_output=True, text=True).stdout
        assert "prime256v1" in text or "P-256" in text
        assert "CN=OmaLawn link" in text.replace(" = ", "=")
        # an unpaired computer learns nothing about the phone (final review I3)
        assert c.call("link.hello")["result"] == {"paired": False, "v": 1}
    finally:
        c.close()
    c = Conn(ids["pinned"])
    try:
        hello = c.call("link.hello")["result"]
        assert hello["fp"] == hashlib.sha256(c.peer_der).hexdigest()
        assert hello["paired"] is True and hello["v"] == 1
        assert hello["device"] and hello["name"] and hello["version"]
    finally:
        c.close()


def test_handshake_limit_per_address(ids):
    """At most 10 connection attempts a minute from one address that do not turn out to be a paired
    computer; a paired computer's own handshakes are given back (it does not use up the budget)."""
    provider("debug_allow_local_pairing", "off")
    provider("debug_pair_reset")
    try:
        for _ in range(3):  # a paired computer's handshakes are given back: they use up nothing
            c = Conn(ids["pinned"], timeout=10)
            try:
                assert c.call("link.hello")["result"]["paired"] is True
            finally:
                c.close()
        for i in range(10):
            c = Conn(ids["stranger"], timeout=10)
            try:
                assert c.call("link.hello")["result"]["paired"] is False, i
            finally:
                c.close()
        with pytest.raises((ssl.SSLError, OSError, EOFError)):
            c = Conn(ids["stranger"], timeout=10)  # the 11th: closed before any TLS
            try:
                c.call("link.hello")
            finally:
                c.close()
        log = adb("logcat", "-d", "-s", "OmalawnLink")
        assert "handshakes a minute from one address" in log
    finally:
        provider("debug_allow_local_pairing", "on")
        provider("debug_pair_reset")


def test_no_client_certificate_is_refused(ids):
    with pytest.raises((ssl.SSLError, OSError, EOFError)):
        c = Conn(None)
        try:
            c.call("link.hello")  # TLS 1.3: the refusal can surface on the first read
        finally:
            c.close()


def test_unpinned_computer_only_gets_hello(ids):
    c = Conn(ids["stranger"])
    try:
        assert c.call("link.hello")["result"]["paired"] is False
        assert c.call("link.hello")["result"]["paired"] is False  # hello keeps the connection
    finally:
        c.close()
    # anything else, existing or not, blob or not: the same not_paired, then the phone hangs up
    for m, blob in (("launcher.ping", None), ("link.ping", None), ("launcher.apps_list", None),
                    ("launcher.nope", None), ("launcher.stage_file", b"zz"), ("x", b"zz")):
        c = Conn(ids["stranger"])
        try:
            r = c.call(m, {"name": "x"} if blob else None, blob=blob)
            assert r["error"]["code"] == "not_paired", m
            with pytest.raises((EOFError, OSError)):
                c.call("link.hello")
        finally:
            c.close()


def test_pinned_computer_gets_everything(ids):
    c = Conn(ids["pinned"])
    try:
        assert c.call("link.hello")["result"]["paired"] is True
        t = c.call("link.ping")["result"]["t"]
        assert abs(t / 1000 - time.time()) < 120
        assert c.call("launcher.ping")["result"]["package"] == PKG
        apps = c.call("launcher.apps_list")["result"]
        assert any(a["package"] == "com.android.settings" for a in apps)
        assert all(set(a) == {"package", "activity", "label"} for a in apps)
    finally:
        c.close()


def test_apps_list_through_the_provider(ids):
    out = provider("apps_list")
    assert "ok=true" in out and "com.android.settings" in out


def test_unknown_methods_and_params_are_refused(ids):
    c = Conn(ids["pinned"])
    try:
        assert c.call("launcher.nope")["error"]["code"] == "unknown_method"
        assert c.call("link.ping", {"x": 1})["error"]["code"] == "bad_params"
        assert c.call("launcher.layout_import", {"path": "/data/x"}, blob=b"")["error"]["code"] == "bad_params"
        # debug hooks exist only on the shell provider
        assert c.call("link.debug_pin", {})["error"]["code"] == "unknown_method"
        assert c.call("launcher.debug_pin", {})["error"]["code"] == "unknown_method"
        assert c.call("link.ping")["result"]["t"] > 0  # connection still usable
    finally:
        c.close()


def test_debug_hooks_unreachable_for_unknown_shell_names(ids):
    assert "unknown method" in provider("debug_whatever")


def test_oversized_blob_is_refused_before_reading(ids):
    c = Conn(ids["pinned"])
    try:
        c.send_line({"id": 1, "method": "launcher.stage_file", "params": {"name": "big.bin"}})
        c.send_line({"id": 1, "blob": (32 << 20) + 1})
        assert c.read()["error"]["code"] == "frame_too_large"
    finally:
        c.close()
    c = Conn(ids["pinned"])
    try:
        assert c.call("link.ping")["result"]["t"] > 0
    finally:
        c.close()


@pytest.mark.parametrize("path", ["/system/etc/hosts", f"/data/user/0/{PKG}/files/omalawn/../../shared_prefs/x.xml",
                                  f"/data/user/0/{PKG}/files/omalawn/missing.png"])
def test_wallpaper_set_over_the_link_only_takes_staged_files(ids, path):
    c = Conn(ids["pinned"])
    try:
        assert c.call("launcher.wallpaper_set", {"path": path})["error"]["code"] == "bad_path"
    finally:
        c.close()


def _closed_within(sock, seconds):
    """Seconds until the phone closes a socket we never write to, or None."""
    sock.settimeout(seconds)
    t0 = time.time()
    try:
        while sock.recv(4096):
            pass
        return time.time() - t0
    except (socket.timeout, TimeoutError):
        return None
    except OSError:
        return time.time() - t0


def test_idle_raw_sockets_do_not_lock_out_a_paired_computer(ids):
    raws = [socket.create_connection(("127.0.0.1", DIAL_PORT), timeout=5) for _ in range(6)]
    try:
        time.sleep(1)
        t0 = time.time()
        c = Conn(ids["pinned"], timeout=10)
        try:
            assert c.call("link.ping")["result"]["t"] > 0
        finally:
            c.close()
        assert time.time() - t0 < 5
        # the handshake deadline drops every one of them within ~10 s
        print("paired ping with 6 idle raw sockets open took (s):", round(time.time() - t0, 2))
        waited = _closed_within(raws[-1], 15)
        print("idle raw socket closed by the phone after (s):", waited and round(waited, 1))
        assert waited is not None and waited < 14
    finally:
        for r in raws:
            r.close()


def test_trickling_strangers_do_not_lock_out_a_paired_computer(ids):
    stop = threading.Event()
    ended = {}

    def stranger(i):
        c = Conn(ids["stranger"])
        t0 = time.time()
        try:
            while not stop.is_set():
                c.call("link.hello")  # a request every 5 s keeps it from going idle
                stop.wait(5)
        except (EOFError, OSError, ssl.SSLError):
            ended[i] = time.time() - t0
        finally:
            c.close()

    ts = [threading.Thread(target=stranger, args=(i,)) for i in range(3)]
    for t in ts:
        t.start()
    try:
        time.sleep(2)
        for _ in range(4):
            c = Conn(ids["pinned"], timeout=10)
            try:
                assert c.call("link.ping")["result"]["t"] > 0
            finally:
                c.close()
        for t in ts:
            t.join(40)
    finally:
        stop.set()
    print("stranger connections ended after (s):", {k: round(v, 1) for k, v in ended.items()})
    # a third stranger evicts the oldest at once; the other two end at the 30 s lifetime
    assert len(ended) == 3, ended
    assert sorted(ended.values())[-1] < 37 and sorted(ended.values())[-1] > 25, ended


def test_two_mib_line_is_rejected(ids):
    c = Conn(ids["pinned"])
    try:
        c.s.sendall(b'{"id":1,"method":"link.ping","params":{"pad":"' + b"a" * (2 << 20) + b'"}}\n')
        msg = c.read()
        assert msg["id"] is None and msg["error"]["code"] == "frame_too_large"
        with pytest.raises((EOFError, OSError)):
            c.read()
    finally:
        c.close()


def test_one_mib_line_is_still_accepted(ids):
    c = Conn(ids["pinned"])
    try:
        pad = "a" * ((1 << 20) - 100)
        assert c.call("link.ping", {"pad": pad})["error"]["code"] == "bad_params"  # read fully, then refused
    finally:
        c.close()


def test_five_mib_blob_staged_byte_identical(ids):
    data = os.urandom(5 << 20)
    name = "homalawn-test-blob.bin"
    c = Conn(ids["pinned"])
    try:
        r = c.call("launcher.stage_file", {"name": name}, blob=data)["result"]
        assert r["path"].endswith(f"/files/omalawn/{name}")
        sha = run_as("sha256sum", f"files/omalawn/{name}").split()[0]
        assert sha == hashlib.sha256(data).hexdigest()
        mode = run_as("stat", "-c", "%a", f"files/omalawn/{name}").strip()
        assert mode == "600"
    finally:
        c.close()
        run_as("rm", "-f", f"files/omalawn/{name}")


@pytest.mark.parametrize("name", ["", "../x", ".hidden", "UPPER", "a/b", "x" * 65, "export-1.db", "import-x"])
def test_stage_file_refuses_bad_names(ids, name):
    c = Conn(ids["pinned"])
    try:
        assert c.call("launcher.stage_file", {"name": name}, blob=b"x")["error"]["code"] == "bad_name"
    finally:
        c.close()


def test_layout_export_blob_matches_adb_export(ids, tmp_path):
    c = Conn(ids["pinned"])
    try:
        msg = c.call("launcher.layout_export")
        assert msg["bytes"][:16] == b"SQLite format 3\x00"
        assert msg["result"]["db"].startswith("launcher") and "path" not in msg["result"]
    finally:
        c.close()
    out = provider("layout_export")
    name = re.search(r'"path":"[^"]*?(export-\d+\.db)"', out).group(1)
    rel = f"files/omalawn/{name}"
    adb_bytes = run_as("cat", rel, text=False)
    run_as("rm", "-f", rel)
    assert favorites(msg["bytes"], tmp_path, "link.db") == favorites(adb_bytes, tmp_path, "adb.db")


def test_layout_import_blob_round_trip_keeps_the_layout(ids, tmp_path):
    c = Conn(ids["pinned"])
    try:
        before = c.call("launcher.layout_export")
        rows = favorites(before["bytes"], tmp_path, "before.db")
        bad = c.call("launcher.layout_import", {"db": before["result"]["db"]}, blob=b"not a database")
        assert bad["error"]["code"] in ("bad_schema", "failed")
        r = c.call("launcher.layout_import", {"db": before["result"]["db"]}, blob=before["bytes"])
        assert r["result"]["items"] == len(rows[1])
        for _ in range(30):  # forceReload
            if c.call("launcher.ping")["result"]["model_loaded"]:
                break
            time.sleep(1)
        after = c.call("launcher.layout_export")
        assert favorites(after["bytes"], tmp_path, "after.db") == rows
    finally:
        c.close()
    left = run_as("ls", "files/omalawn")
    assert "import-" not in left and ".stage-" not in left


def test_at_most_four_connections_per_computer(ids):
    conns = [Conn(ids["pinned"]) for _ in range(4)]
    try:
        for c in conns:
            assert c.call("link.ping")["result"]["t"] > 0
        extra = Conn(ids["pinned"], timeout=10)   # a fifth: the computer's oldest is dropped, not this one
        try:
            assert extra.call("link.ping")["result"]["t"] > 0
        finally:
            extra.close()
        with pytest.raises((ssl.SSLError, OSError, EOFError)):
            conns[0].call("link.ping")
        assert conns[3].call("link.ping")["result"]["t"] > 0
    finally:
        for c in conns:
            c.close()
    time.sleep(1)
    c = Conn(ids["pinned"])
    try:
        assert c.call("link.ping")["result"]["t"] > 0
    finally:
        c.close()


def test_concurrent_exports_all_succeed(ids):
    results, errors = [], []

    def worker():
        try:
            c = Conn(ids["pinned"], timeout=120)
            try:
                for _ in range(3):
                    results.append(c.call("launcher.layout_export")["bytes"][:16])
            finally:
                c.close()
        except Exception as e:  # noqa: BLE001
            errors.append(e)

    ts = [threading.Thread(target=worker) for _ in range(4)]
    for t in ts:
        t.start()
    for t in ts:
        t.join()
    assert not errors, errors
    assert results == [b"SQLite format 3\x00"] * 12
    assert "export-" not in run_as("ls", "files/omalawn")


def test_foreground_service_is_connected_device(ids):
    dump = adb("shell", "dumpsys", "activity", "services", PKG)
    assert "app.lawnchair.omalawn.link.LinkService" in dump
    assert "isForeground=true" in dump
    assert "types=0x00000010" in dump  # FOREGROUND_SERVICE_TYPE_CONNECTED_DEVICE


SOAK = int(os.environ.get("HOMALAWN_LINK_SOAK_S", "0"))


def _ping_for(ids, seconds, log):
    end = time.time() + seconds
    while time.time() < end:
        c = Conn(ids["pinned"])  # a fresh dial each time, like the CLI
        try:
            t = c.call("link.ping")["result"]["t"]
            log.append((time.strftime("%H:%M:%S"), t))
        finally:
            c.close()
        time.sleep(10)


@pytest.mark.skipif(not SOAK, reason="set HOMALAWN_LINK_SOAK_S to run")
def test_ping_with_settings_in_front_then_screen_off(ids):
    log = []
    try:
        adb("shell", "am", "start", "-a", "android.settings.SETTINGS")
        time.sleep(25)  # past the 20 s background-network grace period
        state = adb("shell", "dumpsys", "activity", "processes", PKG)
        _ping_for(ids, SOAK, log)
        adb("shell", "input", "keyevent", "KEYCODE_SLEEP")
        time.sleep(25)
        _ping_for(ids, SOAK, log)
        adb("shell", "dumpsys", "deviceidle", "force-idle")
        _ping_for(ids, 30, log)  # forced Doze
        print(adb("shell", "dumpsys", "deviceidle", "get", "deep").strip())
        adb("shell", "dumpsys", "deviceidle", "unforce")
        print("\n".join(f"{a} t={b}" for a, b in log))
        print([ln.strip() for ln in state.splitlines() if "procState" in ln or "curProcState" in ln][:3])
    finally:
        adb("shell", "input", "keyevent", "KEYCODE_WAKEUP")
        adb("shell", "wm", "dismiss-keyguard", check=False)
        adb("shell", "input", "keyevent", "KEYCODE_HOME")
    assert len(log) >= 2 * (SOAK // 11)
