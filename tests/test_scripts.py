"""scripts/release.sh and scripts/make-signing-key.sh, run for real against stand-ins: a throwaway
git checkout with a pushed commit, and fake `gh`, `uv`, `keytool` and build scripts on PATH. Nothing
reaches GitHub, no APK is built and no real key is made or read. Also the CI workflow's checkouts."""
from __future__ import annotations

import hashlib
import os
import shutil
import stat
import subprocess
import sys
from pathlib import Path

import pytest
from test_release import CERT, OTHER, fake_apk

from homalawn import release

REPO = Path(__file__).resolve().parents[1]
SCRIPTS = REPO / "scripts"
N = 7   # the stand-in build number

pytestmark = pytest.mark.skipif(not shutil.which("git") or not shutil.which("bash"), reason="needs git and bash")


def _exe(path: Path, text: str) -> None:
    path.write_text(text)
    path.chmod(0o755)


def _git(cwd: Path, *args: str) -> None:
    subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@t", "-c", "commit.gpgsign=false", *args],
                   cwd=cwd, check=True, capture_output=True)


@pytest.fixture
def checkout(tmp_path):
    """A pushed, clean checkout holding release.sh; build-lawnchair.sh "builds" $FAKE_APK_BYTES."""
    root = tmp_path / "repo"
    (root / "scripts").mkdir(parents=True)
    (root / "patches").mkdir()
    shutil.copy(SCRIPTS / "release.sh", root / "scripts" / "release.sh")
    _exe(root / "scripts" / "build-number.sh", f"#!/bin/sh\necho {N}\n")
    out = tmp_path / "out"
    out.mkdir()
    apk = out / f"HomaLawn-16.0-dev.abc+homalawn.{N}-release.apk"
    _exe(root / "scripts" / "build-lawnchair.sh",
         f'#!/bin/sh\ncp "$FAKE_APK_BYTES" "{apk}"\necho "{apk}"\n')
    (root / "patches" / "BASE").write_text("0123456789abcdef\n")
    (root / ".gitignore").write_text("")
    _git(root, "init", "-q", "-b", "main")
    _git(root, "add", "-A")
    _git(root, "commit", "-qm", "x")
    remote = tmp_path / "remote.git"
    _git(tmp_path, "init", "-q", "--bare", str(remote))
    _git(root, "remote", "add", "origin", str(remote))
    _git(root, "push", "-q", "origin", "main")
    _git(root, "fetch", "-q", "origin")

    bin_ = tmp_path / "bin"
    bin_.mkdir()
    log = tmp_path / "gh.log"
    _exe(bin_ / "gh", f"""#!/bin/sh
echo "$@" >> "{log}"
case "$1 $2" in
  "release view") exit 1 ;;
  "repo view") echo "$FAKE_GH_REPO" ;;
esac
""")
    # uv run --quiet python -c CODE APK -> this test's python with the real homalawn package
    _exe(bin_ / "uv", f"""#!/bin/sh
while [ "$1" != python ]; do shift; done
shift
PYTHONPATH="{REPO / 'src'}" exec "{sys.executable}" "$@"
""")
    env = {k: v for k, v in os.environ.items() if not k.startswith(("HOMALAWN_", "HOMAR_", "OMALAWN_"))}
    env |= {"PATH": f"{bin_}:{env['PATH']}", "FAKE_GH_REPO": release.DEFAULT_REPO}

    class C:
        pass
    c = C()
    c.root, c.env, c.log, c.tmp = root, env, log, tmp_path

    def run(*args: str, apk_bytes: bytes, **env_extra: str) -> subprocess.CompletedProcess:
        src = tmp_path / "built.apk"
        src.write_bytes(apk_bytes)
        return subprocess.run(["bash", str(root / "scripts" / "release.sh"), *args], cwd=root, text=True,
                              capture_output=True, env=c.env | {"FAKE_APK_BYTES": str(src)} | env_extra, timeout=60)
    c.run = run
    c.created = lambda: [line for line in (log.read_text().splitlines() if log.exists() else [])
                         if line.startswith("release create")]
    return c


def test_release_refuses_an_apk_not_signed_with_homa_lawns_certificate(checkout):
    r = checkout.run(apk_bytes=fake_apk({release.V2_ID: [OTHER]}))
    assert r.returncode == 1, r.stderr
    assert "Not released to g00rek/homalawn:" in r.stderr and "not signed with Homa Lawn's key" in r.stderr
    assert checkout.created() == []


def test_release_refuses_an_apk_with_a_planted_signing_block(checkout):
    from test_release import _zip_comment_attack
    r = checkout.run(apk_bytes=_zip_comment_attack())
    assert r.returncode == 1 and "Not released" in r.stderr and checkout.created() == []


def test_release_apk_option_is_only_for_a_dry_run(checkout, tmp_path):
    given = tmp_path / f"HomaLawn-16.0-dev.abc+homalawn.{N}-release.apk"
    given.write_bytes(fake_apk({release.V2_ID: [OTHER]}))
    r = checkout.run("--apk", str(given), apk_bytes=b"")
    assert r.returncode == 2
    assert r.stderr.strip() == "--apk is only for --dry-run: a release is always built by this script from this commit."
    assert checkout.created() == []
    r = checkout.run("--dry-run", "--apk", str(given), apk_bytes=b"")
    assert r.returncode == 0, r.stderr
    assert "Would refuse to release this to g00rek/homalawn" in r.stderr and checkout.created() == []


def test_a_fork_releases_with_its_own_certificate_and_needs_it_named(checkout):
    fork = {"HOMALAWN_RELEASES_REPO": "someone/fork"}
    r = checkout.run(apk_bytes=fake_apk({release.V2_ID: [OTHER]}), **fork)
    assert r.returncode == 1 and "HOMALAWN_RELEASES_CERT_SHA256" in r.stderr and checkout.created() == []
    r = checkout.run(apk_bytes=fake_apk({release.V2_ID: [CERT]}), **fork,
                     HOMALAWN_RELEASES_CERT_SHA256=hashlib.sha256(OTHER).hexdigest())
    assert r.returncode == 1 and checkout.created() == []          # Homa Lawn's key is not the fork's
    r = checkout.run(apk_bytes=fake_apk({release.V2_ID: [OTHER]}), **fork,
                     HOMALAWN_RELEASES_CERT_SHA256=hashlib.sha256(OTHER).hexdigest())
    assert r.returncode == 0, r.stderr
    [created] = checkout.created()
    assert f"release create build-{N} --draft" in created and "--repo someone/fork" in created


# -- make-signing-key.sh ----------------------------------------------------------------------------
@pytest.fixture
def keytool(tmp_path):
    """A keytool that writes the store file it is given and lists a made-up fingerprint."""
    bin_ = tmp_path / "kbin"
    bin_.mkdir()
    _exe(bin_ / "keytool", """#!/bin/sh
case "$1" in
  -genkeypair)
    while [ $# -gt 0 ]; do [ "$1" = -keystore ] && { : > "$2"; }; shift; done ;;
  -list) echo "   SHA256: AB:CD" ;;
esac
""")
    env = {k: v for k, v in os.environ.items() if not k.startswith("HOMALAWN_")}
    return env | {"PATH": f"{bin_}:{env['PATH']}"}


def _make_key(env: dict, props: Path) -> subprocess.CompletedProcess:
    return subprocess.run(["bash", str(SCRIPTS / "make-signing-key.sh")], text=True, capture_output=True,
                          env=env | {"HOMALAWN_SIGNING_PROPERTIES": str(props)}, timeout=30)


def _mode(p: Path) -> int:
    return stat.S_IMODE(p.stat().st_mode)


def test_make_signing_key_leaves_an_existing_folder_as_it_is(keytool, tmp_path):
    home = tmp_path / "home"
    home.mkdir()
    home.chmod(0o755)
    r = _make_key(keytool, home / "homalawn-release.properties")
    assert r.returncode == 0, r.stderr
    assert _mode(home) == 0o755                                      # never chmod 700 on, say, $HOME
    assert _mode(home / "homalawn-release.properties") == 0o600 and _mode(home / "homalawn-release.jks") == 0o600
    assert "already exists (mode 755), so it is left as it is" in r.stderr


def test_make_signing_key_makes_its_own_folder_private(keytool, tmp_path):
    folder = tmp_path / "signing"
    r = _make_key(keytool, folder / "homalawn-release.properties")
    assert r.returncode == 0, r.stderr
    assert _mode(folder) == 0o700 and _mode(folder / "homalawn-release.properties") == 0o600
    assert "already exists" not in r.stderr


# -- CI -----------------------------------------------------------------------------------------------
def test_ci_checkouts_keep_no_credentials():
    text = (REPO / ".github" / "workflows" / "ci.yml").read_text()
    lines = text.splitlines()
    checkouts = [i for i, line in enumerate(lines) if "uses: actions/checkout@" in line]
    assert len(checkouts) == 2
    for i in checkouts:
        assert lines[i + 1].strip() == "with:" and lines[i + 2].strip() == "persist-credentials: false"
