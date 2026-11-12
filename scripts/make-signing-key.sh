#!/usr/bin/env bash
# Make your own key for signing Homa Lawn release builds (scripts/build-lawnchair.sh release).
#
# Writes, in ~/.config/homalawn/signing/ (or the folder of HOMALAWN_SIGNING_PROPERTIES):
#   homalawn-release.jks          an RSA-4096 key made by keytool (PKCS12), valid for 10000 days
#   homalawn-release.properties   storeFile, storePassword, keyAlias, keyPassword: what the build reads
# both mode 600. A folder the script makes is mode 700; an existing folder (say $HOME) keeps its
# mode, and the script says so when it is open to others. The password is random and goes only into the properties file;
# it is never printed. An existing key is never replaced: a phone takes Homa Lawn updates only when
# they are signed with the key its Homa Lawn was installed with.
set -euo pipefail
root="$(cd "$(dirname "$0")/.." && pwd)"
# shellcheck source=scripts/signing.sh
. "$root/scripts/signing.sh"
props="$(signing_properties)"
dir="$(dirname "$props")"
store="$dir/homalawn-release.jks"
alias_name="homalawn"

if [ -e "$props" ] || [ -e "$store" ]; then
  echo "A signing key is already there ($dir), so nothing was made: a key is never replaced, because phones take updates only signed with the key they were installed with." >&2
  exit 1
fi
if ! command -v keytool >/dev/null 2>&1; then
  echo "Making a key needs keytool, which comes with a Java JDK (the one the build uses): install a JDK, e.g. \`sudo pacman -S jdk-openjdk\`." >&2
  exit 1
fi

umask 077
if [ -d "$dir" ]; then
  mode="$(stat -c %a "$dir")"
  if [ "$mode" != 700 ]; then
    echo "$dir already exists (mode $mode), so it is left as it is; the key files in it are made readable only by you (mode 600)." >&2
  fi
else
  mkdir -p "$dir"
  chmod 700 "$dir"
fi
# 48 hex characters from the kernel's random source; kept in the environment for keytool, never in argv
HOMALAWN_KEY_PASS="$(od -An -tx1 -N24 /dev/urandom | tr -d ' \n')"
export HOMALAWN_KEY_PASS
trap 'unset HOMALAWN_KEY_PASS' EXIT

keytool -genkeypair -noprompt -keystore "$store" -storetype PKCS12 -alias "$alias_name" \
  -keyalg RSA -keysize 4096 -validity 10000 -dname "CN=Homa Lawn release key" \
  -storepass:env HOMALAWN_KEY_PASS -keypass:env HOMALAWN_KEY_PASS >/dev/null 2>&1 || {
  rm -f "$store"
  echo "keytool could not make the key; nothing was kept." >&2
  exit 1
}
chmod 600 "$store"
tmp="$(mktemp "$dir/.properties.XXXXXX")"
{
  echo "# Homa Lawn release signing key, made by scripts/make-signing-key.sh. Back up this file and the key."
  echo "storeFile=$store"
  echo "storePassword=$HOMALAWN_KEY_PASS"
  echo "keyAlias=$alias_name"
  echo "keyPassword=$HOMALAWN_KEY_PASS"
} > "$tmp"
chmod 600 "$tmp"
mv "$tmp" "$props"

fp="$(keytool -list -v -keystore "$store" -alias "$alias_name" -storepass:env HOMALAWN_KEY_PASS 2>/dev/null \
  | sed -nE 's/^[[:space:]]*SHA256:[[:space:]]*//p' | tr -d ':' | tr 'A-F' 'a-f' | head -n 1)"
echo "Made your signing key: $store (its passwords are in $props)."
echo "Certificate SHA-256: $fp"
echo "Back up both files somewhere safe: a phone takes Homa Lawn updates only when they are signed with this same key; without it every phone needs Homa Lawn uninstalled (which wipes its settings) before a new build installs."
