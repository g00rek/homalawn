#!/usr/bin/env bash
# Apply patches/*.patch onto the pristine Lawnchair clone and build Homa Lawn's phone app.
# The upstream commit we patch is pinned in patches/BASE (first word of its first line).
#
#   scripts/build-lawnchair.sh            # release (app.homalawn, your key) + test build (app.homalawn.debug);
#                                         # without a signing key: the test build only, and it says so
#   scripts/build-lawnchair.sh release    # only the release build (stops at once without a signing key)
#   scripts/build-lawnchair.sh debug      # only the test build (no signing key needed)
#   scripts/build-lawnchair.sh --dry-run [what]   # say what would be built, build nothing
#
# Prints the path of each APK it built, release first. A release build is signed with your own key:
# make one once with scripts/make-signing-key.sh (see docs/development/patching-lawnchair.md#signing).
# The version comes from this repository: versionCode = upstream's + the build number
# (scripts/build-number.sh: the commit count of HEAD plus patches/BUILD_OFFSET), versionName says both
# (and ".dirty" when the patches or this script have uncommitted changes), so the same commit always
# gives the same version. It needs a git clone (not a source archive).
set -euo pipefail
root="$(cd "$(dirname "$0")/.." && pwd)"
# shellcheck source=scripts/signing.sh
. "$root/scripts/signing.sh"
dry=false
if [ "${1:-}" = "--dry-run" ]; then dry=true; shift; fi
what="${1:-all}"
case "$what" in
  all|release|debug) ;;
  *) echo "usage: $0 [--dry-run] [all|release|debug]" >&2; exit 2 ;;
esac
if ! signing_key_found "$root/lawnchair"; then
  if [ "$what" = release ]; then
    echo "No signing key for a release build: make your own once with scripts/make-signing-key.sh, or build the test build with \`scripts/build-lawnchair.sh debug\`." >&2
    exit 1
  fi
  if [ "$what" = all ]; then
    echo "No signing key, so this builds only the test build (app.homalawn.debug); scripts/make-signing-key.sh makes a key for release builds." >&2
    what=debug
  fi
fi
case "$what" in
  all) tasks=(:assembleLawnWithQuickstepHomalawnRelease :assembleLawnWithQuickstepHomalawnDebug); kinds=(release debug) ;;
  release) tasks=(:assembleLawnWithQuickstepHomalawnRelease); kinds=(release) ;;
  debug) tasks=(:assembleLawnWithQuickstepHomalawnDebug); kinds=(debug) ;;
esac
base="$(grep -v '^[[:space:]]*#' "$root/patches/BASE" | awk 'NF {print $1; exit}')"
[ -n "$base" ] || { echo "patches/BASE names no commit" >&2; exit 1; }
build="$("$root/scripts/build-number.sh")"
dirty=false
[ -z "$(git -C "$root" status --porcelain -- patches scripts/build-lawnchair.sh scripts/build-number.sh)" ] || dirty=true
if $dry; then
  echo "Would build ${kinds[*]} as build $build of Lawnchair ${base:0:7}$($dirty && echo ' (dirty)' || true)." >&2
  exit 0
fi
if [ ! -d "$root/lawnchair/.git" ]; then
  echo "Lawnchair's source is not here yet: run \`git clone https://github.com/LawnchairLauncher/lawnchair.git lawnchair\` in $root first." >&2
  exit 1
fi
cd "$root/lawnchair"
git cat-file -e "$base^{commit}" 2>/dev/null || git fetch -q --no-recurse-submodules origin "$base"
git -c advice.detachedHead=false checkout -q -f "$base" 2>/dev/null || true
git reset -q --hard "$base"
git submodule sync -q --recursive
git submodule update -q --init --recursive --force
git clean -qfd -e local.properties
for p in "$root"/patches/*.patch; do [ -e "$p" ] && git apply --index "$p"; done
for k in "${kinds[@]}"; do rm -f build/outputs/apk/lawnWithQuickstepHomalawn/"$k"/*.apk; done
./gradlew --no-daemon -q "${tasks[@]}" \
  -Phomalawn.build="$build" -Phomalawn.base="${base:0:7}" -Phomalawn.dirty="$dirty" >&2
for k in "${kinds[@]}"; do
  ls -t build/outputs/apk/lawnWithQuickstepHomalawn/"$k"/*.apk | head -1 | sed "s|^|$root/lawnchair/|"
done
