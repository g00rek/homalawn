"""The Homa Lawn phone app from a GitHub release: find the newest one, download its APK, check it.

Release naming (the code reads only the asset names; the tag is for people):
- tag `build-<N>` (N: the build number, the `<N>` in the app's versionName `…+homalawn.<N>`), title
  "Homa Lawn build <N>"; published (not draft, not prerelease) so `releases/latest` finds it;
- asset `HomaLawn-<versionName>-release.apk`, exactly the file scripts/build-lawnchair.sh makes
  (e.g. `HomaLawn-16.0-dev.59396cd+homalawn.325-release.apk`): the version, and with it the build
  number, comes from the asset's name alone (GitHub may show `+` as `.`; both are read);
- asset `<that apk name>.sha256`: `sha256sum` output (`<64 hex>  <file name>`), used when GitHub's
  own `digest` for the asset is missing.

A downloaded APK is used only when both checks pass: its SHA-256 is the release's, and its signing
certificate is the pinned one (CERT_SHA256, published in docs/development/patching-lawnchair.md; a
fork named by HOMALAWN_RELEASES_REPO pins its own with HOMALAWN_RELEASES_CERT_SHA256).

What these checks are, and are not:
- The trust anchor for a first install is the SHA-256 that GitHub serves over HTTPS for the
  release (its `digest`, else the `.sha256` asset): every address used is HTTPS on GitHub, and
  redirects are followed only over HTTPS.
- The certificate comparison reads the certificate each signer names in the APK Signing Block and
  catches a wrong or mislabelled file (another key, a debug build, a planted second block). It
  does NOT verify the APK's cryptographic signature: Android does that when it installs the file,
  and for an update the phone refuses any other key than the one its Homa Lawn was installed with.
- So the reader is strict about the file's shape, the way Android finds the block: the ZIP end
  record must be the one Android finds (scanning back from the end, its comment length matching
  the bytes after it), the comment must be empty (release APKs have none), the central directory
  must end where the end record starts, the signing block must end exactly where the central
  directory starts with its two size fields equal, no id may appear twice, and every signing-scheme
  block present (v2, v3, v3.1) must hold exactly one signer, whose certificate is the pinned one.

The answer from GitHub is kept for an hour in ~/.cache/homalawn/apk/latest.json (asked again
with its ETag, which GitHub answers with 304 without counting it against the rate limit); `force`
asks at once. Downloads land in ~/.cache/homalawn/apk/ (XDG_CACHE_HOME respected), written whole
(temp file + rename) and reused while their checksum matches. Standard library only.

Every refusal is a ReleaseError whose message is one sentence a person can act on.
"""
from __future__ import annotations

import contextlib
import hashlib
import json
import os
import re
import struct
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Callable

from . import env

DEFAULT_REPO = "g00rek/homalawn"
API = "https://api.github.com"
GITHUB_HOSTS = ("github.com", "api.github.com", "objects.githubusercontent.com")
# SHA-256 of the DER of Homa Lawn's release signing certificate (docs/development/patching-lawnchair.md)
CERT_SHA256 = "eadef047d0f7401d545d0945e52e43c3461ae3b61a9d26be586de07847e8a7a9"
TTL_S = 3600            # GitHub's answer is reused this long
TIMEOUT_S = 30
CHUNK = 1 << 16
ASSET = re.compile(r"HomaLawn-(?P<version>.+)-release\.apk")
_BUILD = re.compile(r"homalawn\.(\d+)")
_HEX64 = re.compile(r"\b([0-9a-fA-F]{64})\b")
_REPO = re.compile(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+")

NO_RELEASE = "There is no Homa Lawn release on GitHub yet ({repo}), so there is nothing to download."
NO_APK = "The newest Homa Lawn release ({tag}) has no phone app in it, so there is nothing to download."
NO_NETWORK = "GitHub could not be reached, so no Homa Lawn release was checked: check this computer's internet connection."
RATE_LIMITED = "GitHub refuses more questions from this computer for now; try again in an hour."
NO_SUM = "The Homa Lawn release {tag} gives no checksum for its app, so the app was not used."
BAD_SUM = "The downloaded Homa Lawn app does not match the checksum of release {tag}, so it was not used; try again."
WRONG_SIGNER = "{name} is not signed with Homa Lawn's key, so it was not used."
NOT_SIGNED = "{name} has no APK signature Homa Lawn can read, so it was not used."
BROKEN = "The download of the Homa Lawn app stopped before the end; try again."
BAD_URL = ("The newest Homa Lawn release ({tag}) names a download address that is not an HTTPS address on GitHub, "
           "so it was not used.")
BAD_NAME = "The newest Homa Lawn release ({tag}) names its app with a file name Homa Lawn does not use, so it was not used."
NOT_GITHUB = "Homa Lawn downloads its app only from HTTPS addresses on GitHub, so {url} was not used."
BAD_REPO = "HOMALAWN_RELEASES_REPO must be a GitHub repository written owner/name, so no release was checked."
FORK_CERT = ("HOMALAWN_RELEASES_REPO names another repository than Homa Lawn's, so set HOMALAWN_RELEASES_CERT_SHA256 "
             "to the SHA-256 of that repository's signing certificate as well.")
BAD_FORK_CERT = ("HOMALAWN_RELEASES_CERT_SHA256 must be the 64-character hex SHA-256 of the fork's signing "
                 "certificate, so no release was used.")


class ReleaseError(Exception):
    """The release cannot be used; the message is one sentence saying why (and what to do)."""


@dataclass(frozen=True)
class Release:
    tag: str
    version: str           # the app's versionName, from the asset's name
    build: int | None      # the <N> in …+homalawn.<N>
    asset: str             # the APK's file name
    url: str               # where it downloads from
    size: int
    sha256: str | None     # from GitHub's digest; None: read from the .sha256 asset
    sha_url: str | None    # the .sha256 asset, when there is one


def repo() -> str:
    """owner/name of the GitHub repository the releases come from; ReleaseError when malformed."""
    name = env.get("RELEASES_REPO") or DEFAULT_REPO
    if not _REPO.fullmatch(name) or ".." in name:
        raise ReleaseError(BAD_REPO)
    return name


def pinned_cert() -> str:
    """The SHA-256 of the signing certificate a release must carry: Homa Lawn's for the default
    repository; a fork's own (HOMALAWN_RELEASES_CERT_SHA256, required with it) otherwise."""
    if repo() == DEFAULT_REPO:
        return CERT_SHA256
    fp = (env.get("RELEASES_CERT_SHA256") or "").strip()
    if not fp:
        raise ReleaseError(FORK_CERT)
    if not re.fullmatch(r"[0-9a-fA-F]{64}", fp):
        raise ReleaseError(BAD_FORK_CERT)
    return fp.lower()


def is_github_https(url: object) -> bool:
    """True for an https:// address on one of GITHUB_HOSTS (no user part, no other port)."""
    if not isinstance(url, str) or not url:
        return False
    try:
        u = urllib.parse.urlsplit(url)
        port = u.port
    except ValueError:
        return False
    return u.scheme == "https" and u.hostname in GITHUB_HOSTS and port in (None, 443) and "@" not in u.netloc


def safe_asset_name(name: str) -> bool:
    """A plain file name: no folder separator, not hidden (it becomes a file in cache_dir())."""
    return bool(name) and "/" not in name and "\\" not in name and not name.startswith(".") and "\0" not in name


def cache_dir() -> Path:
    base = os.environ.get("XDG_CACHE_HOME") or str(Path(os.environ.get("HOME") or Path.home()) / ".cache")
    return Path(base) / "homalawn" / "apk"


def build_number(version: str | None) -> int | None:
    """N of a versionName (or an APK file name) `…+homalawn.<N>[.dirty]`; None without one."""
    m = _BUILD.search(version or "")
    return int(m.group(1)) if m else None


def version_of(asset_name: str) -> str | None:
    m = ASSET.fullmatch(asset_name)
    return m.group("version") if m else None


# -- GitHub ---------------------------------------------------------------------------------------
def _user_agent() -> str:
    return "homalawn"


class _HttpsOnlyRedirect(urllib.request.HTTPRedirectHandler):
    """GitHub answers a release download with a redirect to its file storage; follow it only over
    HTTPS."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        if urllib.parse.urlsplit(newurl).scheme != "https":
            raise urllib.error.URLError(f"refused a redirect to a non-HTTPS address ({newurl})")
        return super().redirect_request(req, fp, code, msg, headers, newurl)


_OPENER = urllib.request.build_opener(_HttpsOnlyRedirect())


def _open(req: urllib.request.Request):
    if not is_github_https(req.full_url):
        raise ReleaseError(NOT_GITHUB.format(url=req.full_url))
    return _OPENER.open(req, timeout=TIMEOUT_S)


def _get(url: str, headers: dict[str, str] | None = None) -> tuple[int, dict[str, str], bytes]:
    """One GET: (status, headers, body). 304 and 4xx come back as statuses; no network raises
    ReleaseError(NO_NETWORK)."""
    req = urllib.request.Request(url, headers={"User-Agent": _user_agent(), **(headers or {})})
    try:
        with _open(req) as r:
            return r.status, {k.lower(): v for k, v in r.headers.items()}, r.read()
    except urllib.error.HTTPError as e:
        return e.code, {k.lower(): v for k, v in (e.headers or {}).items()}, b""
    except (urllib.error.URLError, OSError, TimeoutError) as e:
        raise ReleaseError(NO_NETWORK) from e


def _parse(rel: dict) -> Release:
    tag = str(rel.get("tag_name") or rel.get("name") or "")
    assets = [a for a in rel.get("assets") or [] if isinstance(a, dict)]
    apks = [a for a in assets if version_of(str(a.get("name") or ""))]
    if not apks:
        raise ReleaseError(NO_APK.format(tag=tag or "untitled"))
    apk = max(apks, key=lambda a: build_number(str(a["name"])) or -1)
    name = str(apk["name"])
    label = tag or "untitled"
    if not safe_asset_name(name):
        raise ReleaseError(BAD_NAME.format(tag=label))
    digest = apk.get("digest")
    sha = digest.split(":", 1)[1].lower() if isinstance(digest, str) and digest.startswith("sha256:") else None
    side = next((a for a in assets if a.get("name") == name + ".sha256"), None)
    url = apk.get("browser_download_url")
    sha_url = side.get("browser_download_url") if side else None
    if not is_github_https(url) or (side is not None and not is_github_https(sha_url)):
        raise ReleaseError(BAD_URL.format(tag=label))
    version = version_of(name) or ""
    return Release(tag=tag, version=version, build=build_number(version), asset=name, url=str(url),
                   size=int(apk.get("size") or 0), sha256=sha, sha_url=str(sha_url) if side else None)


def _load_cache(path: Path) -> dict:
    try:
        d = json.loads(path.read_text())
    except (OSError, ValueError):
        return {}
    return d if isinstance(d, dict) else {}


def _save_cache(path: Path, data: dict) -> None:
    _write_whole(path, json.dumps(data, indent=1).encode())


def latest(force: bool = False, *, get: Callable | None = None, now: Callable[[], float] = time.time) -> Release:
    """The newest published release of repo(): its APK asset, version and checksum. GitHub is asked
    at most once an hour (unless `force`), conditionally with the last ETag."""
    get = get or _get
    name = repo()
    pinned_cert()           # a fork without its own certificate is refused before GitHub is asked
    path = cache_dir() / "latest.json"
    cached = _load_cache(path)
    if cached.get("repo") != name:
        cached = {}
    fresh = isinstance(cached.get("checked"), (int, float)) and now() - cached["checked"] < TTL_S
    if not (fresh and not force):
        headers = {"Accept": "application/vnd.github+json", "X-GitHub-Api-Version": "2022-11-28"}
        if cached.get("etag") and "body" in cached:
            headers["If-None-Match"] = cached["etag"]
        status, h, body = get(f"{API}/repos/{name}/releases/latest", headers)
        if status == 304 and "body" in cached:
            cached["checked"] = now()
        elif status == 200:
            try:
                rel = json.loads(body)
            except ValueError as e:
                raise ReleaseError(NO_NETWORK) from e
            cached = {"repo": name, "checked": now(), "etag": h.get("etag"), "body": rel}
        elif status == 404:
            cached = {"repo": name, "checked": now(), "etag": None, "body": None}
        elif status in (403, 429):
            raise ReleaseError(RATE_LIMITED)
        else:
            raise ReleaseError(NO_NETWORK)
        with contextlib.suppress(OSError):
            _save_cache(path, cached)
    body = cached.get("body")
    if not isinstance(body, dict):
        raise ReleaseError(NO_RELEASE.format(repo=name))
    return _parse(body)


# -- download --------------------------------------------------------------------------------------
def _write_whole(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}-", suffix=".tmp")
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(data)
        os.replace(tmp, path)
    except BaseException:
        with contextlib.suppress(OSError):
            os.unlink(tmp)
        raise


def _sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        while block := f.read(1 << 20):
            h.update(block)
    return h.hexdigest()


def expected_sha(rel: Release, *, get: Callable | None = None) -> str:
    """The release's SHA-256 of its APK: GitHub's digest, else the `.sha256` asset."""
    get = get or _get
    if rel.sha256:
        return rel.sha256
    if rel.sha_url:
        status, _, body = get(rel.sha_url, {})
        m = _HEX64.search(body.decode("utf-8", "replace")) if status == 200 else None
        if m:
            return m.group(1).lower()
    raise ReleaseError(NO_SUM.format(tag=rel.tag))


def _stream(url: str, out, *, say=None, size: int = 0) -> None:
    req = urllib.request.Request(url, headers={"User-Agent": _user_agent(), "Accept": "application/octet-stream"})
    try:
        with _open(req) as r:
            got, step = 0, 8 << 20
            while block := r.read(CHUNK):
                out.write(block)
                got += len(block)
                if say and size and got // step != (got - len(block)) // step:
                    say(f"Downloaded {got / 1e6:.1f} MB of {size / 1e6:.1f} MB.")
    except urllib.error.HTTPError as e:
        raise ReleaseError(NO_NETWORK if e.code >= 500 else BROKEN) from e
    except (urllib.error.URLError, OSError, TimeoutError) as e:
        raise ReleaseError(NO_NETWORK) from e


def download(rel: Release, *, say: Callable[[str], None] | None = None, get: Callable | None = None,
             stream: Callable | None = None) -> Path:
    """The release's APK in cache_dir(), checked: the cached file when its checksum still matches,
    else a fresh download (temp file, checks, then renamed into place)."""
    stream = stream or _stream
    if not safe_asset_name(rel.asset):
        raise ReleaseError(BAD_NAME.format(tag=rel.tag or "untitled"))
    if not is_github_https(rel.url):
        raise ReleaseError(BAD_URL.format(tag=rel.tag or "untitled"))
    want = expected_sha(rel, get=get)
    folder = cache_dir()
    path = folder / rel.asset
    if path.is_file() and _sha256_file(path) == want:
        check_signer(path)
        return path
    folder.mkdir(parents=True, exist_ok=True)
    if say:
        say(f"Downloading Homa Lawn {rel.version} from GitHub ({rel.size / 1e6:.1f} MB).")
    fd, tmp = tempfile.mkstemp(dir=folder, prefix=f".{rel.asset}-", suffix=".tmp")
    try:
        with os.fdopen(fd, "wb") as f:
            stream(rel.url, f, say=say, size=rel.size)
        tmp_path = Path(tmp)
        if rel.size and tmp_path.stat().st_size != rel.size and _sha256_file(tmp_path) != want:
            raise ReleaseError(BROKEN)
        if _sha256_file(tmp_path) != want:
            raise ReleaseError(BAD_SUM.format(tag=rel.tag))
        check_signer(tmp_path, name=rel.asset)
        os.replace(tmp, path)
    except BaseException:
        with contextlib.suppress(OSError):
            os.unlink(tmp)
        raise
    # one release APK is enough; a file the LAN download server is serving stays readable to it,
    # which holds it open from start to end (update.DownloadServer)
    for old in folder.glob("HomaLawn-*-release.apk"):
        if old != path:
            with contextlib.suppress(OSError):
                old.unlink()
    return path


def newest_apk(*, force: bool = False, say: Callable[[str], None] | None = None) -> tuple[Release, Path]:
    rel = latest(force)
    return rel, download(rel, say=say)


def as_dict(rel: Release) -> dict:
    return asdict(rel)


# -- the APK Signing Block (v2 / v3 / v3.1) --------------------------------------------------------
V2_ID = 0x7109871A
V3_ID = 0xF05368C0
V31_ID = 0x1B93AD61
SCHEMES = (V2_ID, V3_ID, V31_ID)
_MAGIC = b"APK Sig Block 42"
_EOCD = b"PK\x05\x06"
_EOCD_SIZE = 22


class NotSigned(ValueError):
    pass


def _lp(buf: bytes, off: int) -> tuple[bytes, int]:
    """A uint32 length-prefixed piece of `buf` at `off`, and the offset after it."""
    if off + 4 > len(buf):
        raise NotSigned("truncated")
    n = struct.unpack_from("<I", buf, off)[0]
    if off + 4 + n > len(buf):
        raise NotSigned("truncated")
    return buf[off + 4:off + 4 + n], off + 4 + n


def _seq(buf: bytes) -> list[bytes]:
    out, off = [], 0
    while off < len(buf):
        item, off = _lp(buf, off)
        out.append(item)
    return out


def _end_record(data: bytes) -> int:
    """Where the ZIP end of central directory record starts, found as Android finds it: scanning
    back from the end, the first record whose comment length equals the bytes after it."""
    last = len(data) - _EOCD_SIZE
    if last < 0:
        raise NotSigned("not a zip")
    for comment in range(min(0xFFFF, last) + 1):
        i = last - comment
        if data[i:i + 4] == _EOCD and struct.unpack_from("<H", data, i + 20)[0] == comment:
            return i
    raise NotSigned("not a zip")


def signing_block(data: bytes) -> dict[int, bytes]:
    """The id -> value pairs of the APK Signing Block that sits right before the ZIP central directory.
    NotSigned for any shape Android would read differently (see the module's docstring)."""
    i = _end_record(data)
    if i + _EOCD_SIZE != len(data):
        raise NotSigned("a zip comment")           # release APKs have none
    cd_size, cd = struct.unpack_from("<II", data, i + 12)
    if cd + cd_size != i:
        raise NotSigned("the central directory does not end at the end record")
    if cd < 32 or data[cd - 16:cd] != _MAGIC:
        raise NotSigned("no signing block")
    size = struct.unpack_from("<Q", data, cd - 24)[0]
    start = cd - size - 8
    if size < 24 or start < 0 or struct.unpack_from("<Q", data, start)[0] != size:
        raise NotSigned("bad signing block")
    pairs, off, end = {}, start + 8, cd - 24
    while off < end:
        if off + 12 > end:
            raise NotSigned("bad signing block")
        n = struct.unpack_from("<Q", data, off)[0]
        if n < 4 or off + 8 + n > end:
            raise NotSigned("bad signing block")
        pid = struct.unpack_from("<I", data, off + 8)[0]
        if pid in pairs:
            raise NotSigned("an id given twice")
        pairs[pid] = data[off + 12:off + 8 + n]
        off += 8 + n
    return pairs


def _signers(value: bytes) -> list[bytes]:
    """The DER certificate of each signer of one scheme block (the first of each signer's chain;
    v2, v3 and v3.1 all start a signer with its signed data: digests, then certificates)."""
    seq, _ = _lp(value, 0)
    certs = []
    for signer in _seq(seq):
        signed, _ = _lp(signer, 0)
        _digests, off = _lp(signed, 0)
        certs_buf, _ = _lp(signed, off)
        chain = _seq(certs_buf)
        if not chain:
            raise NotSigned("a signer without a certificate")
        certs.append(chain[0])
    if not certs:
        raise NotSigned("no signer")
    return certs


def scheme_signers(data: bytes) -> dict[int, list[bytes]]:
    """For each signing-scheme block present (v2, v3, v3.1): its signers' certificates."""
    pairs = signing_block(data)
    out = {pid: _signers(pairs[pid]) for pid in SCHEMES if pid in pairs}
    if not out:
        raise NotSigned("no v2 or v3 signature")
    return out


def signer_certificates(data: bytes) -> list[bytes]:
    """Every signer certificate of every signing-scheme block present."""
    return [c for certs in scheme_signers(data).values() for c in certs]


def _fingerprints(path: Path) -> dict[int, list[str]]:
    return {pid: [hashlib.sha256(c).hexdigest() for c in certs]
            for pid, certs in scheme_signers(path.read_bytes()).items()}


def cert_fingerprints(path: Path) -> list[str]:
    """The distinct signer certificate fingerprints of all scheme blocks, in order."""
    return list(dict.fromkeys(fp for fps in _fingerprints(path).values() for fp in fps))


def _only(schemes: dict[int, list[str]], fingerprint: str) -> bool:
    """Every scheme block holds exactly one signer, with `fingerprint`."""
    return all(fps == [fingerprint] for fps in schemes.values())


def check_signer(path: Path, *, name: str | None = None, fingerprint: str | None = None) -> None:
    """Refuse (ReleaseError) an APK whose every signing-scheme block does not name the pinned
    certificate (pinned_cert()) as its only signer. A comparison of the named certificate, not a
    verification of the signature (Android does that at install)."""
    fingerprint = fingerprint or pinned_cert()
    label = name or path.name
    try:
        schemes = _fingerprints(path)
    except (NotSigned, OSError, struct.error) as e:
        raise ReleaseError(NOT_SIGNED.format(name=label)) from e
    if not _only(schemes, fingerprint):
        raise ReleaseError(WRONG_SIGNER.format(name=label))


def signed_by_homalawn(path: Path) -> bool | None:
    """True / False for an APK this can read, None when it has no readable signature."""
    try:
        schemes = _fingerprints(path)
    except (NotSigned, OSError, struct.error):
        return None
    return _only(schemes, CERT_SHA256)
