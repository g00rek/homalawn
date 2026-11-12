#!/usr/bin/env bash
# Make a DRAFT GitHub release of the phone app from this commit; publishing it stays a person's click.
#
#   scripts/release.sh               # build the release APK, then create the draft release build-<N>
#   scripts/release.sh --dry-run             # say what it would do; changes nothing
#   scripts/release.sh --dry-run --apk FILE  # ...and, for that APK, print the checks and the notes
#
# A real release is always built here, from this clean, pushed commit: --apk is accepted only with
# --dry-run, so a published APK cannot come from another tree.
#
# Steps: refuse a tree with uncommitted changes; build number N (scripts/build-number.sh); build the
# release APK (scripts/build-lawnchair.sh release, which needs your signing key); check its name says
# build N and is not ".dirty"; write <apk>.sha256 in `sha256sum` format; check the APK's signing
# certificate (src/homalawn/release.py, the same check the homalawn tool makes before it uses a
# release) and refuse an APK not signed with the certificate the target repository's users pin
# (Homa Lawn's published one for g00rek/homalawn; a fork's HOMALAWN_RELEASES_CERT_SHA256); create the draft with `gh release create build-<N> --draft --title "Homa Lawn build <N>"`,
# both files attached, and notes with the versionName, the SHA-256, the certificate fingerprint and
# where the source is. The tag is made by GitHub when the draft is published, on this commit, which
# must already be pushed. HOMALAWN_RELEASES_REPO (owner/name) picks the GitHub repository, as it does
# for the tool (a fork also sets HOMALAWN_RELEASES_CERT_SHA256); default: the repository gh finds
# from this checkout.
set -euo pipefail
root="$(cd "$(dirname "$0")/.." && pwd)"
dry=false
apk=""
while [ $# -gt 0 ]; do
  case "$1" in
    --dry-run) dry=true ;;
    --apk) [ $# -ge 2 ] || { echo "--apk needs a file." >&2; exit 2; }; apk="$2"; shift ;;
    *) echo "usage: $0 [--dry-run] [--apk FILE]" >&2; exit 2 ;;
  esac
  shift
done
say() { echo "$*" >&2; }
if [ -n "$apk" ] && ! $dry; then
  say "--apk is only for --dry-run: a release is always built by this script from this commit."
  exit 2
fi
default_repo="g00rek/homalawn"
target="${HOMALAWN_RELEASES_REPO:-}"
if [ -z "$target" ] && command -v gh >/dev/null 2>&1; then
  target="$(gh repo view --json nameWithOwner --jq .nameWithOwner 2>/dev/null || true)"
fi
if [ -z "$target" ]; then
  if ! $dry; then
    say "Which GitHub repository to release to is not known: set HOMALAWN_RELEASES_REPO=owner/name, or run this where \`gh repo view\` finds it."
    exit 1
  fi
  target="$default_repo"
  say "Dry run: no repository found, so checking as for $default_repo."
fi
repo_args=(--repo "$target")

if [ -n "$(git -C "$root" status --porcelain)" ]; then
  say "This checkout has uncommitted changes, and a release is built only from a committed tree: commit or stash them first."
  exit 1
fi
n="$("$root/scripts/build-number.sh")"
tag="build-$n"
head="$(git -C "$root" rev-parse HEAD)"
base="$(grep -v '^[[:space:]]*#' "$root/patches/BASE" | awk 'NF {print $1; exit}')"
[ -n "$base" ] || { say "patches/BASE names no commit."; exit 1; }

if ! $dry; then
  command -v gh >/dev/null 2>&1 || { say "Creating the release needs the GitHub CLI (gh): install it and run \`gh auth login\`."; exit 1; }
  if gh release view "$tag" "${repo_args[@]}" >/dev/null 2>&1; then
    say "Release $tag already exists on GitHub; a build number is released once (commit something new for a new build)."
    exit 1
  fi
  if [ -z "$(git -C "$root" branch -r --contains "$head" 2>/dev/null)" ]; then
    say "This commit ($head) is not pushed yet, and the release's tag is made on it: push it first."
    exit 1
  fi
fi

if [ -z "$apk" ]; then
  if $dry; then
    say "Would build the release APK: scripts/build-lawnchair.sh release (build $n)."
    say "Nothing more to show without an APK; pass --apk FILE with --dry-run to see the rest."
    exit 0
  fi
  say "Building the release APK (build $n); this takes a while."
  apk="$("$root/scripts/build-lawnchair.sh" release | head -n 1)"
fi
[ -f "$apk" ] || { say "$apk is not a file."; exit 1; }
apk="$(cd "$(dirname "$apk")" && pwd)/$(basename "$apk")"
name="$(basename "$apk")"
version="$(sed -nE 's/^HomaLawn-(.+)-release\.apk$/\1/p' <<<"$name")"
[ -n "$version" ] || { say "$name is not a release APK named HomaLawn-<version>-release.apk."; exit 1; }
case "$version" in
  *".dirty") say "$name was built from uncommitted changes (.dirty), so it is not released."; exit 1 ;;
  *"+homalawn.$n") ;;
  *) say "$name is not build $n (this commit's build number): build it again from this commit."; exit 1 ;;
esac

sha="$(sha256sum "$apk" | awk '{print $1}')"
# line 1: the certificate fingerprint(s); line 2: "ok", or "refused: <one sentence>"
checked="$(cd "$root" && HOMALAWN_RELEASES_REPO="$target" uv run --quiet python -c '
import sys
from pathlib import Path
from homalawn import release
apk = Path(sys.argv[1])
try:
    print(" ".join(release.cert_fingerprints(apk)) or "(none)")
except Exception:
    print("(unreadable)")
try:
    release.check_signer(apk, fingerprint=release.pinned_cert())
    print("ok")
except release.ReleaseError as e:
    print(f"refused: {e}")
' "$apk")"
cert="$(sed -n 1p <<<"$checked")"
verdict="$(sed -n 2p <<<"$checked")"
if [ "$verdict" != ok ]; then
  why="${verdict#refused: }"
  [ -n "$why" ] || why="its signing certificate could not be checked."
  if ! $dry; then
    say "Not released to $target: $why"
    exit 1
  fi
  say "Would refuse to release this to $target: $why"
fi

notes="$(mktemp)"
trap 'rm -f "$notes"' EXIT
cat > "$notes" <<EOF
Homa Lawn's phone app, build $n.

- File: \`$name\`
- versionName: \`$version\`
- SHA-256: \`$sha\` (also in \`$name.sha256\`)
- Signing certificate SHA-256: \`$cert\`

\`homalawn setup\` and \`homalawn update\` (and the Homa Lawn window's Update app) download this file by themselves and check both the SHA-256 and the signing certificate before they use it.

Source: this repository at tag $tag plus Lawnchair commit $base (https://github.com/LawnchairLauncher/lawnchair/commit/$base) with patches/ applied.
EOF

if $dry; then
  say "Would write $apk.sha256: $sha  $name"
  say "Would create a draft release: gh release create $tag --draft --title \"Homa Lawn build $n\" --target $head ${repo_args[*]} with $name and $name.sha256, and these notes:"
  cat "$notes" >&2
  exit 0
fi
(cd "$(dirname "$apk")" && sha256sum "$name" > "$name.sha256")
gh release create "$tag" --draft --title "Homa Lawn build $n" --target "$head" --notes-file "$notes" \
  "${repo_args[@]}" "$apk" "$apk.sha256"
say "Created the draft release $tag with $name and $name.sha256. Check it on GitHub, then publish it there."
