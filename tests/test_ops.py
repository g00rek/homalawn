import pytest
from homalawn import config, ops


@pytest.fixture
def paired(tmp_path, monkeypatch):
    """A paired fake phone answering over real TLS on localhost, and an adb serial configured."""
    from fake_link import FakeLinkPhone, make_cert
    from homalawn.link import identity
    from homalawn.link.identity import Peer
    from test_session import FakeDevice
    monkeypatch.setenv("HOMALAWN_CONFIG", str(tmp_path / "cfg" / "config.toml"))
    monkeypatch.delenv("HOMALAWN_SERIAL", raising=False)
    monkeypatch.delenv("HOMALAWN_PHONE", raising=False)
    config.save_serial("emu-2")  # the adb fallback (a named serial would mean adb, never the link)
    monkeypatch.setattr("homalawn.link.discovery.browse", lambda timeout=3.0: [])
    ident = identity.ensure()
    phone = FakeLinkPhone(make_cert(tmp_path / "phone"), FakeDevice(tmp_path), trust=[ident.cert],
                          pinned={ident.fingerprint}, name="Pixel")
    identity.save_peer(Peer(fp=phone.fp, name="Pixel", hosts=[phone.addr]))
    yield phone
    phone.close()


def test_session_picks_the_paired_link_and_names_its_target(paired):
    s = ops.Session()
    assert s.transport().name == "link"
    assert s.target() == "via link (Pixel)"


def test_status_returns_text_and_data(paired):
    r = ops.status(ops.Session())
    assert "Pixel" in r.text and r.data["default_home"] in (True, False)


def test_write_announces_target_through_notify_and_never_prints(paired, capsys):
    said = []
    s = ops.Session(notify=said.append)
    with pytest.raises(ops.OpError):
        ops.remove(s, "com.not.there")  # whatever the fake answers, the target is announced first
    assert said[0] == "via link (Pixel)"
    assert capsys.readouterr().out == ""


def test_reset_drops_the_cached_transport(paired):
    s = ops.Session()
    t1 = s.transport()
    s.reset()
    assert s.transport() is not t1


def test_bad_app_spec_is_an_op_error_with_cli_wording(paired, monkeypatch):
    from homalawn import apps as apps_mod
    monkeypatch.setattr(apps_mod, "list_apps", lambda t: [])
    with pytest.raises(ops.OpError, match="has no launchable activity on this phone"):
        ops.place(ops.Session(), "p.missing", 0, 1, 1)


class _Stub:
    """A session whose transport answers launcher.update_status with `answer` (or raises it)."""

    def __init__(self, answer):
        self.answer = answer
        self._t = self

    name = "link"

    def transport(self):
        return self

    def call(self, method, params=None):
        assert method == "launcher.update_status"
        if isinstance(self.answer, Exception):
            raise self.answer
        return self.answer


def test_a_write_left_for_the_unlock_says_so_in_its_notes():
    from homalawn.session import DEFERRED_NOTE

    class La:
        deferred = False

        def home_guard(self, require=True, notify=None):
            import contextlib
            return contextlib.nullcontext()
    la, notes = La(), []

    def write():
        la.deferred = True          # wait_loaded found the phone locked and did not wait
        return "look-1"
    assert ops._write(_Stub(None), la, write, notes) == "look-1"
    assert notes == [DEFERRED_NOTE]


def test_a_home_screen_that_did_not_reload_is_not_a_broken_link():
    from homalawn.session import NotLoaded
    s = _Stub(None)

    def boom():
        raise NotLoaded("Lawnchair did not finish loading its home screen within 60s")
    with pytest.raises(ops.OpError) as e:
        ops._run(s, boom)
    assert e.value.code == ops.NOT_LOADED and e.value.transport is False
    assert str(e.value) == "link failed: Lawnchair did not finish loading its home screen within 60s"


class _Phone:
    """A session whose link answers from `answers` (method -> value or exception); records calls."""

    def __init__(self, answers, name="link"):
        self.answers, self.name, self.calls = answers, name, []
        self.label, self.address, self._t = "Pixel", "100.1.2.3:47100", self

    def transport(self):
        return self

    def call(self, method, params=None):
        self.calls.append(method)
        a = self.answers[method]
        if isinstance(a, Exception):
            raise a
        return a


def test_phone_info_reads_the_phone_without_asking_for_photo_access():
    from homalawn.transport import ProviderError
    s = _Phone({"launcher.ping": {"version": "15", "default_home": True},
                "launcher.app_info": {"versionName": "15.0-homalawn", "versionCode": 7, "sha256": "ab"},
                "screen.status": {"enabled": False, "restricted": None}})
    d = ops.phone_info(s).data
    assert d["address"] == "100.1.2.3:47100"
    assert d["app"] == {"versionName": "15.0-homalawn", "versionCode": 7, "sha256": "ab"}
    assert d["permissions"] == {"default_home": True, "screen_control": False, "wallpaper_backup": None}
    assert "launcher.request_media_permission" not in s.calls        # that one posts a notification
    old = _Phone({"launcher.ping": {"version": "14", "default_home": False},
                  "launcher.app_info": ProviderError("unknown method", code="unknown_method"),
                  "screen.status": ProviderError("unknown method", code="unknown_method")})
    d = ops.phone_info(old).data
    assert d["app"] == {"versionName": "14"} and d["permissions"]["screen_control"] is None


def test_build_key_from_the_install_time_else_the_apk_sha256():
    """Our builds share one versionName and versionCode: the connection check tells them apart by
    launcher.ping's installed_at (no extra call), or the APK sha256 of a build without it."""
    from homalawn.transport import ProviderError, TransportError
    new = _Phone({"launcher.ping": {"version": "15", "installed_at": 1760000000000}})
    assert ops._build(new, new.call("launcher.ping")) == "installed:1760000000000"
    assert new.calls == ["launcher.ping"]
    old = _Phone({"launcher.ping": {"version": "15"}, "launcher.app_info": {"versionName": "15", "sha256": "AB"}})
    assert ops._build(old, old.call("launcher.ping")) == "sha256:ab"
    for answer in (ProviderError("unknown method", code="unknown_method"), TransportError("gone"), {"versionName": "15"}):
        none = _Phone({"launcher.ping": {"version": "15", "installed_at": True}, "launcher.app_info": answer})
        assert ops._build(none, none.call("launcher.ping")) is None


def test_app_update_sends_the_newest_build_and_says_what_happened(monkeypatch, tmp_path):
    from homalawn import update
    apk = tmp_path / "x.apk"
    apk.write_bytes(b"apk")
    seen = {}

    def send(say, link, path):
        seen["link"], seen["path"] = link, path
        say("Sending x.apk (0.0 MB) to Pixel over the link.")
        say("Updated.")
        return update.INSTALLED
    monkeypatch.setattr(update, "send", send)
    s = _Phone({})
    r = ops.app_update(s)
    assert r.text == "Updated." and r.notes == ["Sending x.apk (0.0 MB) to Pixel over the link."]
    # no build named: send picks the newest build of the app the phone runs
    assert r.data == {"outcome": "installed"} and seen == {"link": s, "path": None}
    ops.app_update(s, apk)
    assert seen["path"] == apk
    monkeypatch.setattr(update, "send", lambda say, link, path: (_ for _ in ()).throw(update.UpdateError("no")))
    with pytest.raises(ops.OpError, match="^no$"):
        ops.app_update(s)
    with pytest.raises(ops.OpError, match="over the Homa Lawn link"):
        ops.app_update(_Phone({}, name="adb"))


def test_peer_named_ambiguous_lists_fingerprints(monkeypatch):
    from homalawn.link import identity
    peers = [identity.Peer(fp="a" * 64, name="Pixel 8"), identity.Peer(fp="b" * 64, name="Pixel 8")]
    monkeypatch.setattr(identity, "load_peers", lambda: peers)
    with pytest.raises(ops.OpError) as e:
        ops._peer_named("Pixel 8")
    assert "aaaaaaaa…" in str(e.value) and "bbbbbbbb…" in str(e.value)
    assert ops._peer_named("bbbbbbbb").fp == "b" * 64


def test_a_forgotten_phone_reports_its_own_message_not_a_joined_reason(paired):
    paired.pinned.clear()
    paired.handlers["link.hello"] = lambda p: ({"paired": False, "forgotten": True, "v": 1}, None)
    from homalawn.link import identity
    with pytest.raises(ops.OpError, match="forgot this computer"):
        ops._link_to(identity.load_peers())


def test_look_state_asks_the_phone_for_its_look_book():
    class T:
        def call(self, m, params=None):
            assert m == "launcher.look_state"
            return {"source": None, "scope": "latest", "you": {"key": None, "shown": False}, "applied": None}

    class S:
        def transport(self):
            return T()
    assert ops.look_state(S())["scope"] == "latest"


def test_no_openssl_is_one_sentence_for_every_caller(tmp_path, monkeypatch):
    """A computer without openssl has no key to dial a phone with: the CLI, the agent tools and the
    service all get NO_OPENSSL from the op, not a traceback."""
    from homalawn.link import identity
    monkeypatch.setenv("HOMALAWN_CONFIG", str(tmp_path / "c" / "config.toml"))
    monkeypatch.delenv("HOMALAWN_SERIAL", raising=False)
    identity.save_peer(identity.Peer(fp="a" * 64, name="Pixel", hosts=["127.0.0.1:9"]))

    def no_openssl(argv, *a, **k):
        raise FileNotFoundError(argv[0])
    monkeypatch.setattr(identity.subprocess, "run", no_openssl)
    with pytest.raises(ops.OpError) as e:
        ops.Session(via="link").transport()
    assert str(e.value) == identity.NO_OPENSSL


# -- undo / restore say what they undid; snapshot list names the phone -----------------------------
@pytest.fixture
def snaps(tmp_path, monkeypatch):
    from homalawn import snapshots
    monkeypatch.setenv("HOMALAWN_DATA", str(tmp_path / "data"))
    monkeypatch.setenv("HOMALAWN_CONFIG", str(tmp_path / "cfg" / "config.toml"))
    return snapshots


class _Home:
    deferred = False

    def __init__(self, name):
        self.name = name

    def undo(self, no_backup=False):
        return self.name, []

    def restore(self, name, force=False, no_backup=False):
        return []

    import contextlib

    @contextlib.contextmanager
    def home_guard(self, require=True, notify=None):
        yield


def _stub_writer(monkeypatch, la):
    monkeypatch.setattr(ops, "_writer", lambda s: la)
    monkeypatch.setattr(ops, "_run", lambda s, fn, notes=None: fn())


def test_undo_names_the_title_then_the_snapshot(snaps, monkeypatch):
    snaps.save("home-20261112-100000", {"a": b"1"}, device="adb:x", meta={"section": "home", "changes": 3})
    _stub_writer(monkeypatch, _Home("home-20261112-100000"))
    r = ops.undo(ops.Session())
    assert r.text == "Undid: Home screen: 3 changes (home-20261112-100000)" and r.data == "home-20261112-100000"


def test_restore_names_the_title_then_the_snapshot(snaps, monkeypatch):
    snaps.save("look-20261112-100000", {"a": b"1"}, device="adb:x", meta={"section": "style", "theme": "Tokyo Night"})
    _stub_writer(monkeypatch, _Home("x"))
    r = ops.snapshot_restore(ops.Session(), "look-20261112-100000")
    assert r.text == "Restored: Wallpaper & style: Tokyo Night (look-20261112-100000)"
    assert r.data == "look-20261112-100000"


def test_snapshot_list_names_paired_phones_and_gives_titles(snaps):
    from homalawn.link import identity
    from homalawn.link.identity import Peer
    identity.save_peer(Peer(fp="ab" * 32, name="Pixel", hosts=[]))
    snaps.save("home-20261112-100000", {"a": b"1"}, device="link:" + "ab" * 32, meta={"section": "home", "changes": 1})
    snaps.save("home-20261112-110000", {"a": b"1"}, device="link:" + "cd" * 32, meta={"section": "home", "changes": 2})
    snaps.save("home-20261112-120000", {"a": b"1"})
    r = ops.snapshot_list(ops.Session())
    by = {row["name"]: row for row in r.data}
    assert by["home-20261112-100000"]["phone"] == "Pixel" and by["home-20261112-100000"]["device"] == "link:" + "ab" * 32
    assert by["home-20261112-110000"]["phone"] == "cdcdcdcd"
    assert by["home-20261112-120000"]["phone"] == "(unknown phone)"
    lines = r.text.splitlines()
    assert "home-20261112-100000  Pixel  Home screen: 1 change" in lines
    assert "home-20261112-110000  cdcdcdcd  Home screen: 2 changes" in lines
