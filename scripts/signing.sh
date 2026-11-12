# Sourced by build-lawnchair.sh and make-signing-key.sh: where Homa Lawn's release signing key is,
# and whether one is set up. Mirrors homalawn.gradle (patch 0019), which signs release builds with
# the four values from HOMALAWN_SIGNING_STORE_FILE / _STORE_PASSWORD / _KEY_ALIAS / _KEY_PASSWORD,
# each falling back to storeFile / storePassword / keyAlias / keyPassword in the properties file
# (HOMALAWN_SIGNING_PROPERTIES, else ~/.config/homalawn/signing/homalawn-release.properties), and
# needs the store file to exist. Only whether each value is there is checked; no password is read
# into a variable or printed.

signing_properties() {
  echo "${HOMALAWN_SIGNING_PROPERTIES:-$HOME/.config/homalawn/signing/homalawn-release.properties}"
}

# _signing_has ENV_NAME PROPERTY: the variable is set, or the properties file gives the property a value
_signing_has() {
  local props
  props="$(signing_properties)"
  [ -n "${!1:-}" ] && return 0
  [ -f "$props" ] && grep -Eq "^[[:space:]]*$2[[:space:]]*[=:][[:space:]]*[^[:space:]]" "$props"
}

# signing_key_found [LAWNCHAIR_DIR]: exit status 0 when a release build would find its key (a
# relative storeFile is read from LAWNCHAIR_DIR, as gradle's file() does)
signing_key_found() {
  local props store
  props="$(signing_properties)"
  _signing_has HOMALAWN_SIGNING_STORE_FILE storeFile || return 1
  _signing_has HOMALAWN_SIGNING_STORE_PASSWORD storePassword || return 1
  _signing_has HOMALAWN_SIGNING_KEY_ALIAS keyAlias || return 1
  _signing_has HOMALAWN_SIGNING_KEY_PASSWORD keyPassword || return 1
  store="${HOMALAWN_SIGNING_STORE_FILE:-}"
  if [ -z "$store" ]; then
    store="$(sed -nE 's/^[[:space:]]*storeFile[[:space:]]*[=:][[:space:]]*//p' "$props" | head -n 1)"
  fi
  case "$store" in
    /*) ;;
    *) store="${1:-.}/$store" ;;
  esac
  [ -f "$store" ]
}
