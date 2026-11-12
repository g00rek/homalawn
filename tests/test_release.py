"""release.py: the newest GitHub release's APK, its checksum and its signing certificate, against a
fake GitHub and tiny synthetic APKs (a ZIP end record plus a hand-built APK Signing Block); and
update.pick_apk / update.offer, which put the release after `--apk` and this checkout's build.
Nothing reaches the network (conftest makes every real request fail as "no network")."""
from __future__ import annotations

import hashlib
import json
import struct
from pathlib import Path

import pytest

from homalawn import appid, release, update

CERT = b"0\x82\x01\x00 a pretend DER certificate of Homa Lawn"
OTHER = b"0\x82\x01\x00 somebody else's certificate"


# -- synthetic APKs --------------------------------------------------------------------------------
def _lp(b: bytes) -> bytes:
    return struct.pack("<I", len(b)) + b


def _signer(cert: bytes, v3: bool = False) -> bytes:
    digests = _lp(_lp(struct.pack("<I", 0x0103) + _lp(b"d" * 32)))
    certs = _lp(_lp(cert))
    signed = digests + certs + (struct.pack("<II", 24, 0x7FFFFFFF) if v3 else b"") + _lp(b"")
    tail = (struct.pack("<II", 24, 0x7FFFFFFF) if v3 else b"") + _lp(b"sig") + _lp(b"key")
    return _lp(signed) + tail


def sig_block(signers: dict[int, list[bytes]], *, tail_size: int | None = None) -> bytes:
    """An APK Signing Block with one value per scheme id holding a signer per certificate."""
    pairs = b""
    for pid, certs in signers.items():
        value = _lp(b"".join(_lp(_signer(c, v3=pid != release.V2_ID)) for c in certs))
        pairs += struct.pack("<QI", len(value) + 4, pid) + value
    size = len(pairs) + 8 + 16
    return struct.pack("<Q", size) + pairs + struct.pack("<Q", tail_size or size) + b"APK Sig Block 42"


def eocd(cd_size: int, cd_offset: int, comment_len: int) -> bytes:
    return b"PK\x05\x06" + b"\0" * 8 + struct.pack("<IIH", cd_size, cd_offset, comment_len)


def fake_apk(signers: dict[int, list[bytes]] | None = None, body: bytes = b"PK\x03\x04 entries", *,
             comment: bytes = b"", cd_size_delta: int = 0, gap: bytes = b"", tail_size: int | None = None) -> bytes:
    """ZIP-shaped bytes: entries, then (unless `signers` is None) an APK Signing Block, then a
    central directory and its end record (with `comment` after it). The keyword arguments make the
    broken shapes: a wrong central directory size, bytes between the block and the directory, a
    block whose two size fields disagree."""
    block = sig_block(signers, tail_size=tail_size) if signers is not None else b""
    cd_offset = len(body) + len(block) + len(gap)
    cd = b"PK\x01\x02 central directory"
    return body + block + gap + cd + eocd(len(cd) + cd_size_delta, cd_offset, len(comment)) + comment


FP = hashlib.sha256(CERT).hexdigest()


@pytest.fixture
def ours(monkeypatch):
    """The synthetic CERT is "Homa Lawn's" certificate for these tests."""
    monkeypatch.setattr(release, "CERT_SHA256", FP)


def test_signer_certificate_is_read_from_v2(tmp_path):
    p = tmp_path / "a.apk"
    p.write_bytes(fake_apk({release.V2_ID: [CERT]}))
    assert release.cert_fingerprints(p) == [FP]


def test_v2_and_v3_with_the_same_signer_read_as_one(tmp_path):
    p = tmp_path / "a.apk"
    p.write_bytes(fake_apk({release.V2_ID: [CERT], release.V3_ID: [CERT], release.V31_ID: [CERT]}))
    assert release.cert_fingerprints(p) == [FP]


@pytest.mark.parametrize("signers", [
    {release.V2_ID: [OTHER], release.V3_ID: [CERT]},                       # v3 ours, v2 another key
    {release.V2_ID: [CERT], release.V3_ID: [OTHER]},
    {release.V2_ID: [CERT], release.V3_ID: [CERT], release.V31_ID: [OTHER]},   # a v3.1 rotation to another key
    {release.V2_ID: [CERT], release.V3_ID: [CERT, OTHER]},                 # an additional signer
    {release.V2_ID: [CERT, CERT]},                                         # two signers, even both ours
])
def test_every_signing_scheme_block_must_hold_only_our_certificate(tmp_path, ours, signers):
    p = tmp_path / "a.apk"
    p.write_bytes(fake_apk(signers))
    with pytest.raises(release.ReleaseError) as e:
        release.check_signer(p)
    assert str(e.value) == "a.apk is not signed with Homa Lawn's key, so it was not used."


def _zip_comment_attack() -> bytes:
    """The attack the old reader fell for: the real signing block (another key), then a ZIP comment
    holding a copy of a block with Homa Lawn's (public) certificate and a fake end record pointing
    at it. Android finds the real end record (its comment length matches the bytes after it)."""
    entries = b"PK\x03\x04 local file data"
    real = sig_block({release.V2_ID: [OTHER]})
    cd = b"PK\x01\x02 central directory"
    prefix = entries + real + cd
    comment_start = len(prefix) + 22
    fake = sig_block({release.V2_ID: [CERT]})
    comment = fake + eocd(len(cd), comment_start + len(fake), 7)
    return prefix + eocd(len(cd), len(entries) + len(real), len(comment)) + comment


def test_a_zip_comment_with_a_planted_block_is_refused(tmp_path, ours):
    p = tmp_path / "a.apk"
    p.write_bytes(_zip_comment_attack())
    with pytest.raises(release.ReleaseError) as e:
        release.check_signer(p)
    assert str(e.value) == "a.apk has no APK signature Homa Lawn can read, so it was not used."
    assert release.signed_by_homalawn(p) is not True


@pytest.mark.parametrize("shape", [
    dict(comment=b"release notes"),          # release APKs have no ZIP comment
    dict(cd_size_delta=1),                   # the directory would run into the end record
    dict(gap=b"PK\x01\x02"),                 # bytes between the signing block and the directory
    dict(tail_size=7),                       # the block's two size fields disagree
])
def test_inconsistent_zip_shapes_are_refused(tmp_path, ours, shape):
    p = tmp_path / "a.apk"
    p.write_bytes(fake_apk({release.V2_ID: [CERT]}, **shape))
    with pytest.raises(release.ReleaseError) as e:
        release.check_signer(p)
    assert str(e.value) == "a.apk has no APK signature Homa Lawn can read, so it was not used."


def test_a_scheme_id_given_twice_is_refused(tmp_path, ours):
    """Android reads the first pair with an id; a reader that kept the last could be shown another."""
    good = sig_block({release.V2_ID: [OTHER]})
    pairs_other = good[8:-24]
    pairs_ours = sig_block({release.V2_ID: [CERT]})[8:-24]
    pairs = pairs_other + pairs_ours
    size = len(pairs) + 24
    block = struct.pack("<Q", size) + pairs + struct.pack("<Q", size) + b"APK Sig Block 42"
    body, cd = b"PK\x03\x04 entries", b"PK\x01\x02 central directory"
    p = tmp_path / "a.apk"
    p.write_bytes(body + block + cd + eocd(len(cd), len(body) + len(block), 0))
    with pytest.raises(release.ReleaseError) as e:
        release.check_signer(p)
    assert "no APK signature" in str(e.value)


def test_bytes_after_the_end_record_are_refused(tmp_path, ours):
    p = tmp_path / "a.apk"
    p.write_bytes(fake_apk({release.V2_ID: [CERT]}) + b"trailing")
    with pytest.raises(release.ReleaseError):
        release.check_signer(p)


def test_check_signer_refuses_another_key_and_no_signature(tmp_path, ours):
    good, bad, two, none = (tmp_path / n for n in ("good.apk", "bad.apk", "two.apk", "none.apk"))
    good.write_bytes(fake_apk({release.V2_ID: [CERT]}))
    bad.write_bytes(fake_apk({release.V2_ID: [OTHER]}))
    two.write_bytes(fake_apk({release.V2_ID: [CERT, OTHER]}))
    none.write_bytes(fake_apk(None))
    release.check_signer(good)
    for p in (bad, two):
        with pytest.raises(release.ReleaseError) as e:
            release.check_signer(p)
        assert str(e.value) == f"{p.name} is not signed with Homa Lawn's key, so it was not used."
    with pytest.raises(release.ReleaseError) as e:
        release.check_signer(none)
    assert "no APK signature" in str(e.value)
    assert release.signed_by_homalawn(good) is True and release.signed_by_homalawn(bad) is False
    assert release.signed_by_homalawn(none) is None


def test_garbage_is_not_signed(tmp_path):
    p = tmp_path / "x.apk"
    p.write_bytes(b"not a zip at all")
    with pytest.raises(release.NotSigned):
        release.signer_certificates(p.read_bytes())


REAL = Path(__file__).resolve().parents[1] / "lawnchair" / "build" / "outputs" / "apk" / "lawnWithQuickstepHomalawn"


@pytest.mark.skipif(not any(REAL.glob("release/*.apk")), reason="no release build in this checkout")
def test_the_real_release_build_is_homa_lawns_and_the_test_build_is_not():
    """Read-only: the checkout's release APK carries the published certificate; the debug one not."""
    rel = sorted(REAL.glob("release/*.apk"))[-1]
    assert release.cert_fingerprints(rel) == [release.CERT_SHA256]
    for dbg in REAL.glob("debug/*.apk"):
        assert release.signed_by_homalawn(dbg) is False


# -- version from the asset name -------------------------------------------------------------------
def test_build_number_and_version_from_names():
    assert release.version_of("HomaLawn-16.0-dev.59396cd+homalawn.325-release.apk") == "16.0-dev.59396cd+homalawn.325"
    assert release.build_number("16.0-dev.59396cd+homalawn.325") == 325
    assert release.build_number("HomaLawn-16.0-dev.59396cd.homalawn.326-release.apk") == 326  # GitHub's '+' -> '.'
    assert release.build_number("16.0-dev.59396cd+homalawn.325.dirty") == 325
    assert release.build_number("15.Dev") is None and release.build_number(None) is None
    assert release.version_of("HomaLawn-16-debug.apk") is None


# -- GitHub ----------------------------------------------------------------------------------------
NAME = "HomaLawn-16.0-dev.abc1234+homalawn.330-release.apk"


def _release_json(digest: str | None = "sha256:" + "a" * 64, sha_asset: bool = False, name: str = NAME) -> dict:
    assets = [{"name": name, "size": 1234, "browser_download_url": f"https://github.com/g00rek/homalawn/releases/download/build-330/{name}",
               **({"digest": digest} if digest else {})},
              {"name": "notes.txt", "size": 1, "browser_download_url": "https://github.com/g00rek/homalawn/releases/download/build-330/notes.txt"}]
    if sha_asset:
        assets.append({"name": name + ".sha256", "size": 90, "browser_download_url": f"https://github.com/g00rek/homalawn/releases/download/build-330/{name}.sha256"})
    return {"tag_name": "build-330", "draft": False, "prerelease": False, "assets": assets}


class FakeGitHub:
    def __init__(self, status=200, body=None, files=None):
        self.status, self.body, self.files = status, body, files or {}
        self.calls: list[tuple[str, dict]] = []

    def __call__(self, url, headers=None):
        self.calls.append((url, dict(headers or {})))
        if url in self.files:
            return 200, {}, self.files[url]
        if headers and headers.get("If-None-Match") == '"e1"':
            return 304, {}, b""
        return self.status, {"etag": '"e1"'}, json.dumps(self.body).encode() if self.body is not None else b""


def test_latest_reads_the_release_and_asks_github_once_an_hour(monkeypatch):
    gh = FakeGitHub(body=_release_json())
    clock = [1000.0]
    rel = release.latest(get=gh, now=lambda: clock[0])
    assert (rel.tag, rel.version, rel.build, rel.asset) == ("build-330", "16.0-dev.abc1234+homalawn.330", 330, NAME)
    assert rel.sha256 == "a" * 64 and rel.url == f"https://github.com/g00rek/homalawn/releases/download/build-330/{NAME}"
    assert gh.calls[0][0] == "https://api.github.com/repos/g00rek/homalawn/releases/latest"
    clock[0] += 3000
    assert release.latest(get=gh, now=lambda: clock[0]) == rel and len(gh.calls) == 1   # within the hour
    clock[0] += 1000
    assert release.latest(get=gh, now=lambda: clock[0]) == rel                           # 304: the same answer
    assert len(gh.calls) == 2 and gh.calls[1][1]["If-None-Match"] == '"e1"'
    release.latest(force=True, get=gh, now=lambda: clock[0])
    assert len(gh.calls) == 3


def test_latest_follows_the_repo_override(monkeypatch):
    monkeypatch.setenv("HOMALAWN_RELEASES_REPO", "someone/fork")
    monkeypatch.setenv("HOMALAWN_RELEASES_CERT_SHA256", FP)
    gh = FakeGitHub(body=_release_json())
    release.latest(get=gh)
    assert gh.calls[0][0].endswith("/repos/someone/fork/releases/latest")


def test_no_release_no_network_rate_limit_and_no_apk_are_one_sentence_each():
    with pytest.raises(release.ReleaseError) as e:
        release.latest(get=FakeGitHub(status=404))
    assert str(e.value) == ("There is no Homa Lawn release on GitHub yet (g00rek/homalawn), so there is nothing "
                            "to download.")
    with pytest.raises(release.ReleaseError) as e:   # the "none yet" answer is kept for the hour too
        release.latest(get=lambda *a: pytest.fail("asked again"))
    assert "no Homa Lawn release" in str(e.value)
    with pytest.raises(release.ReleaseError) as e:
        release.latest(force=True)                    # conftest: every real request fails
    assert str(e.value) == release.NO_NETWORK
    with pytest.raises(release.ReleaseError) as e:
        release.latest(force=True, get=FakeGitHub(status=403))
    assert str(e.value) == release.RATE_LIMITED
    with pytest.raises(release.ReleaseError) as e:
        release.latest(force=True, get=FakeGitHub(body=_release_json(name="other.apk")))
    assert str(e.value) == "The newest Homa Lawn release (build-330) has no phone app in it, so there is nothing to download."


def _rel(data: bytes, **kw) -> release.Release:
    base = dict(tag="build-330", version="16.0-dev.abc1234+homalawn.330", build=330, asset=NAME,
                url=f"https://github.com/g00rek/homalawn/releases/download/build-330/{NAME}", size=len(data), sha256=hashlib.sha256(data).hexdigest(), sha_url=None)
    return release.Release(**(base | kw))


def _streamer(data: bytes, calls: list):
    def stream(url, out, say=None, size=0):
        calls.append(url)
        out.write(data)
    return stream


def test_download_checks_and_keeps_the_file(ours):
    data = fake_apk({release.V2_ID: [CERT]})
    calls: list = []
    said: list = []
    p = release.download(_rel(data), say=said.append, stream=_streamer(data, calls))
    assert p == release.cache_dir() / NAME and p.read_bytes() == data and calls
    assert said and said[0].startswith("Downloading Homa Lawn 16.0-dev.abc1234+homalawn.330 from GitHub")
    assert release.download(_rel(data), stream=_streamer(data, calls)) == p and len(calls) == 1   # reused
    assert not list(release.cache_dir().glob(".*.tmp"))


def test_download_takes_the_sha256_asset_without_a_digest(ours):
    data = fake_apk({release.V2_ID: [CERT]})
    sha = hashlib.sha256(data).hexdigest()
    url = f"https://github.com/g00rek/homalawn/releases/download/build-330/{NAME}.sha256"
    gh = FakeGitHub(files={url: f"{sha}  {NAME}\n".encode()})
    p = release.download(_rel(data, sha256=None, sha_url=url), get=gh, stream=_streamer(data, []))
    assert p.read_bytes() == data
    with pytest.raises(release.ReleaseError) as e:
        release.download(_rel(data, sha256=None, sha_url=None), stream=_streamer(data, []))
    assert str(e.value) == "The Homa Lawn release build-330 gives no checksum for its app, so the app was not used."


def test_a_bad_checksum_or_another_signer_is_refused_and_nothing_is_kept(ours):
    data = fake_apk({release.V2_ID: [CERT]})
    with pytest.raises(release.ReleaseError) as e:
        release.download(_rel(data, sha256="0" * 64), stream=_streamer(data, []))
    assert str(e.value) == ("The downloaded Homa Lawn app does not match the checksum of release build-330, so it "
                            "was not used; try again.")
    theirs = fake_apk({release.V2_ID: [OTHER]})
    with pytest.raises(release.ReleaseError) as e:
        release.download(_rel(theirs), stream=_streamer(theirs, []))
    assert str(e.value) == f"{NAME} is not signed with Homa Lawn's key, so it was not used."
    assert not release.cache_dir().exists() or not any(release.cache_dir().iterdir())


def test_a_cached_file_that_changed_is_downloaded_again(ours):
    data = fake_apk({release.V2_ID: [CERT]})
    release.cache_dir().mkdir(parents=True)
    (release.cache_dir() / NAME).write_bytes(b"tampered")
    calls: list = []
    assert release.download(_rel(data), stream=_streamer(data, calls)).read_bytes() == data and calls


# -- the order: --apk, this checkout's build, the release -----------------------------------------
@pytest.fixture
def github_release(monkeypatch, ours):
    data = fake_apk({release.V2_ID: [CERT]})
    rel = _rel(data)
    monkeypatch.setattr(release, "latest", lambda force=False, **k: rel)
    monkeypatch.setattr(release, "_stream", _streamer(data, []))
    return rel


def test_pick_apk_order(tmp_path, monkeypatch, github_release):
    given = tmp_path / "given.apk"
    given.write_bytes(b"x")
    build = tmp_path / "build"
    build.mkdir()
    (build / "HomaLawn-1+homalawn.9-release.apk").write_bytes(b"checkout build")
    assert update.pick_apk(given, build) == given
    assert update.pick_apk(None, build, package=appid.RELEASE).parent == build
    got = update.pick_apk(None, tmp_path / "empty", package=appid.RELEASE)
    assert got == release.cache_dir() / NAME
    assert update.pick_apk(None, tmp_path / "empty", package=None) == got        # a new phone: the release


def test_a_new_phone_gets_the_release_from_its_github_address(tmp_path, github_release):
    app = update.new_phone_app(apk_dir=tmp_path / "empty")
    assert app["source"] == "release" and app["url"] == github_release.url
    assert app["url"].startswith("https://github.com/") and app["apk"] == release.cache_dir() / NAME
    build = tmp_path / "build"
    build.mkdir()
    (build / "HomaLawn-1+homalawn.9-release.apk").write_bytes(b"checkout build")
    local = update.new_phone_app(apk_dir=build)
    assert (local["source"], local["url"]) == ("checkout", None)
    given = update.new_phone_app(build / "HomaLawn-1+homalawn.9-release.apk")
    assert (given["source"], given["url"]) == ("file", None)


def test_update_download_of_a_release_prints_the_github_address_and_serves_nothing(github_release, monkeypatch):
    from typer.testing import CliRunner
    from homalawn import cli
    monkeypatch.setattr(update, "DownloadServer", lambda *a, **k: pytest.fail("a server was started"))
    r = CliRunner().invoke(cli.app, ["update", "--download"])
    assert r.exit_code == 0, r.output
    assert ("The phone downloads Homa Lawn from GitHub. Homa Lawn 16.0-dev.abc1234+homalawn.330 "
            f"({NAME}), checked on this computer first.") in r.output
    assert f"Address: {github_release.url}" in r.output and "http://" not in r.output


def test_a_test_build_phone_never_gets_the_release(tmp_path, github_release, monkeypatch):
    monkeypatch.setattr(appid, "in_checkout", lambda: True)
    with pytest.raises(update.UpdateError) as e:
        update.pick_apk(None, tmp_path / "empty", package=appid.TEST, phone="Pixel")
    assert str(e.value) == ("Pixel runs Homa Lawn's test build, which comes only from a build on this computer: "
                            "build it with scripts/build-lawnchair.sh, or pass --apk FILE.")


def test_outside_the_checkout_no_message_points_at_the_build_script(tmp_path, github_release, monkeypatch):
    monkeypatch.setattr(appid, "in_checkout", lambda: False)
    with pytest.raises(update.UpdateError) as e:
        update.pick_apk(None, tmp_path / "empty", package=appid.TEST, phone="Pixel")
    assert "build it from Homa Lawn's source (https://github.com/g00rek/homalawn" in str(e.value)
    assert not str(e.value).startswith("scripts/")


def test_pick_apk_says_why_the_release_cannot_be_used(tmp_path):
    with pytest.raises(update.UpdateError) as e:
        update.pick_apk(None, tmp_path / "empty", package=appid.RELEASE)
    assert str(e.value) == release.NO_NETWORK


def test_offer_compares_build_numbers(tmp_path, monkeypatch):
    rel = _rel(b"x")
    assert update.offer(appid.RELEASE, "16.0-dev.x+homalawn.325", latest=lambda: rel) == {
        "version": "16.0-dev.abc1234+homalawn.330", "newer": True, "source": "release"}
    assert update.offer(appid.RELEASE, "16.0-dev.x+homalawn.330", latest=lambda: rel)["newer"] is False
    assert update.offer(appid.TEST, "16.0-dev.x+homalawn.1", latest=lambda: rel) is None     # never the release
    assert update.offer(appid.LEGACY, "15.Dev", latest=lambda: rel) is None
    assert update.offer(appid.RELEASE, "x+homalawn.1") is None                              # no network: nothing known


def test_offer_prefers_this_checkouts_build(tmp_path, monkeypatch):
    build = tmp_path / "debug"
    build.mkdir()
    (build / "HomaLawn-16.0-dev.abc+homalawn.331.dirty-debug.apk").write_bytes(b"not a zip")
    monkeypatch.setattr(appid, "in_checkout", lambda: True)
    monkeypatch.setitem(appid.APK_DIRS, appid.TEST, build)
    assert update.offer(appid.TEST, "16.0-dev.abc+homalawn.330") == {
        "version": "16.0-dev.abc+homalawn.331.dirty", "newer": True, "source": "checkout"}


# -- what GitHub's answer may name ----------------------------------------------------------------
@pytest.mark.parametrize("name", ["HomaLawn-1/../../x+homalawn.9-release.apk", "HomaLawn-1\\x+homalawn.9-release.apk"])
def test_an_asset_name_that_is_a_path_is_refused(name, ours):
    with pytest.raises(release.ReleaseError) as e:
        release.latest(force=True, get=FakeGitHub(body=_release_json(name=name)))
    assert str(e.value).count(". ") == 0 and "file name" in str(e.value)
    data = fake_apk({release.V2_ID: [CERT]})
    with pytest.raises(release.ReleaseError):
        release.download(_rel(data, asset=".HomaLawn-1-release.apk"), stream=_streamer(data, []))
    assert not list(release.cache_dir().glob("*.apk")) and not list(release.cache_dir().glob(".*"))


@pytest.mark.parametrize("url", ["http://github.com/g00rek/homalawn/releases/download/b/x.apk",
                                 "https://evil.example/x.apk", "https://github.com.evil.example/x.apk",
                                 "file:///etc/passwd", "", None])
def test_a_download_address_off_github_or_not_https_is_refused(url):
    body = _release_json()
    body["assets"][0]["browser_download_url"] = url
    with pytest.raises(release.ReleaseError) as e:
        release.latest(force=True, get=FakeGitHub(body=body))
    assert str(e.value) == ("The newest Homa Lawn release (build-330) names a download address that is not an HTTPS "
                            "address on GitHub, so it was not used.")


def test_the_three_github_hosts_are_accepted():
    for host in ("github.com", "api.github.com", "objects.githubusercontent.com"):
        assert release.is_github_https(f"https://{host}/x")


def test_a_redirect_away_from_https_is_refused():
    handler = release._HttpsOnlyRedirect()
    req = release.urllib.request.Request("https://github.com/x")
    assert handler.redirect_request(req, None, 302, "Found", {},
                                    "https://release-assets.githubusercontent.com/y") is not None
    with pytest.raises(release.urllib.error.URLError):
        handler.redirect_request(req, None, 302, "Found", {}, "http://release-assets.githubusercontent.com/y")


def test_a_repo_that_is_not_owner_slash_name_is_refused(monkeypatch):
    monkeypatch.setenv("HOMALAWN_RELEASES_REPO", "someone/fork/../../x")
    monkeypatch.setenv("HOMALAWN_RELEASES_CERT_SHA256", FP)
    with pytest.raises(release.ReleaseError) as e:
        release.latest(get=lambda *a: pytest.fail("asked GitHub"))
    assert "HOMALAWN_RELEASES_REPO" in str(e.value)


# -- forks: their own certificate --------------------------------------------------------------------
def test_a_fork_needs_its_own_certificate_fingerprint(monkeypatch, tmp_path):
    monkeypatch.setenv("HOMALAWN_RELEASES_REPO", "someone/fork")
    with pytest.raises(release.ReleaseError) as e:
        release.pinned_cert()
    assert str(e.value) == ("HOMALAWN_RELEASES_REPO names another repository than Homa Lawn's, so set "
                            "HOMALAWN_RELEASES_CERT_SHA256 to the SHA-256 of that repository's signing certificate "
                            "as well.")
    with pytest.raises(release.ReleaseError):
        release.latest(get=lambda *a: pytest.fail("asked GitHub"))
    monkeypatch.setenv("HOMALAWN_RELEASES_CERT_SHA256", "not hex")
    with pytest.raises(release.ReleaseError) as e:
        release.pinned_cert()
    assert "HOMALAWN_RELEASES_CERT_SHA256" in str(e.value)
    monkeypatch.setenv("HOMALAWN_RELEASES_CERT_SHA256", hashlib.sha256(OTHER).hexdigest().upper())
    assert release.pinned_cert() == hashlib.sha256(OTHER).hexdigest()
    theirs, ours_ = tmp_path / "theirs.apk", tmp_path / "ours.apk"
    theirs.write_bytes(fake_apk({release.V2_ID: [OTHER]}))
    ours_.write_bytes(fake_apk({release.V2_ID: [CERT]}))
    release.check_signer(theirs)                       # the fork's own key
    with pytest.raises(release.ReleaseError):
        release.check_signer(ours_)                    # Homa Lawn's key is not the fork's


def test_the_default_repo_ignores_a_certificate_override(monkeypatch):
    monkeypatch.setenv("HOMALAWN_RELEASES_CERT_SHA256", "b" * 64)
    assert release.pinned_cert() == release.CERT_SHA256
