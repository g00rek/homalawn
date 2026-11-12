# Releasing

This document covers how a release of the phone app is made: the `build-<N>` scheme, what
`scripts/release.sh` checks and uploads, and publishing the draft.

The desktop tool is installed from the repository itself (`uv tool install git+…`), so a release is
about the **phone app**: the APK that `homalawn setup`, `homalawn update`, `homalawn update
--download` and the window's **Add phone** and **Update app** download.

## The scheme

- **Build number N** = the commit count of HEAD + the number in `patches/BUILD_OFFSET`
  (`scripts/build-number.sh`). It needs a full clone.
- **Tag** `build-<N>`, **title** "Homa Lawn build <N>".
- **Assets**:
  - `HomaLawn-<versionName>-release.apk`, exactly the file `scripts/build-lawnchair.sh release`
    makes, for example `HomaLawn-16.0-dev.c4fe52d+homalawn.345-release.apk`. The tool reads the
    version (and with it N) from this name alone.
  - `HomaLawn-<versionName>-release.apk.sha256`, in `sha256sum` format. The tool uses it when
    GitHub's own digest for the asset is missing.
- The tool takes the **latest published release** (`releases/latest`): drafts and prereleases are
  never offered. `HOMALAWN_RELEASES_REPO=owner/name` points the tool (and `release.sh`) at another
  repository, for example a fork; with it, `HOMALAWN_RELEASES_CERT_SHA256` must give the SHA-256
  (hex) of the fork's signing certificate, which the tool then pins instead of Homa Lawn's (the tool
  and `release.sh` refuse a fork without it).
- A new phone downloads the APK from its `browser_download_url` (HTTPS on GitHub); the asset must
  have a plain file name and its addresses must be HTTPS on GitHub, or the tool refuses the release.

## Making one

You need the GitHub CLI (`gh`, logged in), the release signing key (see
[patching-lawnchair.md](patching-lawnchair.md#signing)) and the build requirements.

```sh
scripts/release.sh --dry-run            # say what it would do; changes nothing
scripts/release.sh                      # build the release APK, then create the draft release build-<N>
scripts/release.sh --dry-run --apk FILE # also run the checks on an APK you have and print the notes
```

A real release is always built by `release.sh` from the clean, pushed commit: `--apk` works only
with `--dry-run`.

`release.sh`:

1. refuses a tree with uncommitted changes, and a commit that is not pushed yet (the tag is made on
   it);
2. computes N and refuses when the release `build-<N>` already exists (a build number is released
   once);
3. builds the release APK (`scripts/build-lawnchair.sh release`);
4. checks the APK's name says build N and is not `.dirty`;
5. writes `<apk>.sha256`;
6. checks the APK's signing certificate with the same code the tool uses (`src/homalawn/release.py`)
   and refuses an APK that is not signed with the certificate the target repository pins (Homa
   Lawn's published one for `g00rek/homalawn`, `HOMALAWN_RELEASES_CERT_SHA256` for a fork), or whose
   signature block it cannot read;
7. creates a **draft** release with `gh release create build-<N> --draft`, both files attached, and
   notes that give the versionName, the SHA-256, the certificate fingerprint and the source: this
   repository at tag `build-<N>` plus the Lawnchair commit in `patches/BASE`, with `patches/`
   applied.

## Publishing

Open the draft on GitHub, check the notes and the two files, and publish it. Publishing makes the
tag `build-<N>` on the commit and makes the release the one the tool finds. Within an hour (the tool
asks GitHub at most once an hour) the window's Overview shows **Update available** on phones that
run an older release.

Before publishing, check the APK by hand if you like:

```sh
sha256sum -c HomaLawn-<version>-release.apk.sha256
apksigner verify --print-certs HomaLawn-<version>-release.apk
```

The certificate of published builds is listed in [security.md](../guide/security.md#release-builds-and-their-signature).

Add the user-visible changes to [CHANGELOG.md](../../CHANGELOG.md).
