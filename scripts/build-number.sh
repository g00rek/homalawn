#!/usr/bin/env bash
# Print Homa Lawn's build number: the commit count of HEAD plus the number in patches/BUILD_OFFSET.
# The phone app's versionCode and versionName (`…+homalawn.<N>`) and the release tag `build-<N>`
# come from it, so the same commit always gives the same number and every later commit a larger one.
# It needs a full git clone: a source archive or a shallow clone has no (or a cut) history.
set -euo pipefail
root="$(cd "$(dirname "$0")/.." && pwd)"
if ! command -v git >/dev/null 2>&1 || ! git -C "$root" rev-parse --git-dir >/dev/null 2>&1; then
  echo "Building Homa Lawn needs a git clone of its repository (the build number comes from its commit history); a source archive cannot be built." >&2
  exit 1
fi
if [ "$(git -C "$root" rev-parse --is-shallow-repository)" = true ]; then
  echo "This is a shallow clone, so its commit count (the build number) would be wrong: run \`git fetch --unshallow\` first." >&2
  exit 1
fi
offset="$(grep -v '^[[:space:]]*#' "$root/patches/BUILD_OFFSET" 2>/dev/null | awk 'NF {print $1; exit}' || true)"
if ! [[ "$offset" =~ ^[0-9]+$ ]]; then
  echo "patches/BUILD_OFFSET must hold a whole number (0 when the history never started over)." >&2
  exit 1
fi
echo $(( $(git -C "$root" rev-list --count HEAD) + offset ))
