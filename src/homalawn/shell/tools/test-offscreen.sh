#!/usr/bin/env bash
# Homa Lawn's offscreen test shell: a throwaway omarchy-shell, with no screen, that loads this plugin
# so its bar mark and window can be driven and photographed from a terminal.
#
#   test-offscreen.sh start  DIR [--socket PATH] [--theme NAME] [--as-is]
#   test-offscreen.sh summon DIR [SECTION]
#   test-offscreen.sh call   DIR FUNCTION [ARG]     snapshot SECTION:PNG | facts | probe METHOD
#                                                  | rail open|closed | pick ID   | info
#                                                  | picker open|close|click|add|"choose ID"|esc
#                                                  | pickerFacts
#                                                  | history rows|"restore NAME" | size WxH
#                                                  | lookFacts | lookEdit JSON | undo | retry
#                                                  | overwrite yes|no | homeFacts | homeEdit JSON
#                                                  | searchFacts | searchEdit JSON
#                                                  | agentsFacts | agentsEdit JSON
#                                                  | settingsFacts | settingsEdit JSON | ask WHAT
#                                                  | pairFacts | pairEdit JSON
#                                                  | addrFacts | addrEdit JSON
#                                                  | addFacts | addEdit JSON | overviewFacts | barFacts
#   test-offscreen.sh stop   DIR
#
# DIR holds everything the test shell owns: its copy of the Omarchy shell, a fake home, the log
# (DIR/shell.log) and the pictures (DIR/shots). The desktop's own shell, ~/.config/omarchy and the
# session bus are never used. --socket defaults to the Homa Lawn service's own rule. --as-is loads
# the plugin folder exactly as checked out (no test stand-ins); the default installs it the way
# `homalawn desktop install` does and swaps in tools/TestPanel.qml and tools/TestBarWidget.qml.
set -euo pipefail

here=$(dirname -- "$(realpath -- "${BASH_SOURCE[0]}")")
plugin=$(realpath -- "$here/..")
omarchy=${OMARCHY_SOURCE:-/usr/share/omarchy}

die() { echo "test-offscreen: $*" >&2; exit 1; }

# The test shell is the only qs process started with this exact shell path.
shell_pid() { pgrep -f "^qs -p $1/omarchy-copy/shell\$" || true; }

need_pid() {
  local pid; pid=$(shell_pid "$1")
  [[ -n $pid ]] || die "no test shell runs in $1"
  echo "$pid"
}

# A copy of the Omarchy tree (the shell plus its default config; the shell finds both through
# OMARCHY_PATH) that can live without a compositor. The bar is a PanelWindow,
# which needs Wayland layer-shell; here every PanelWindow becomes a plain FloatingWindow that
# accepts (and ignores) the layer-shell settings the shell gives it.
copy_shell() {
  local dir=$1
  local copy=$dir/omarchy-copy
  rm -rf "$copy"
  mkdir -p "$copy"
  cp -r "$omarchy/shell" "$omarchy/config" "$copy/"
  cat > "$copy/shell/Commons/HeadlessPanel.qml" <<'QML'
import QtQuick
import Quickshell

// Written by Homa Lawn's test-offscreen.sh into a throwaway shell copy. Never installed.
FloatingWindow {
  component Sides: QtObject {
    property var top: 0
    property var right: 0
    property var bottom: 0
    property var left: 0
  }
  property Sides anchors: Sides {}
  property Sides margins: Sides {}
  property int exclusiveZone: 0
  property int exclusionMode: 0
  property bool focusable: false
  property bool aboveWindows: true
}
QML
  echo "HeadlessPanel 1.0 HeadlessPanel.qml" >> "$copy/shell/Commons/qmldir"
  find "$copy/shell" -name '*.qml' ! -name HeadlessPanel.qml -print0 |
    xargs -0 perl -pi -e 's/\bPanelWindow\b/HeadlessPanel/g'
}

# The fake home: the chosen theme's palette, and a shell.json whose bar shows only Homa Lawn.
# Every first-party plugin that runs as a service, overlay, menu or panel is switched off: the
# services hold real things (idle, lock, notifications, polkit …) that a test must not grab, and
# the others are not needed (the OSD panel cannot even load without layer-shell).
make_home() {
  local dir=$1 theme=$2
  local home=$dir/home
  mkdir -p "$home/.config/omarchy/plugins" "$home/.local/state/omarchy/current/theme"
  [[ -f $omarchy/themes/$theme/colors.toml ]] || die "no Omarchy theme named $theme"
  cp "$omarchy/themes/$theme/colors.toml" "$home/.local/state/omarchy/current/theme/"
  python3 - "$omarchy/shell/plugins" "$home/.config/omarchy/shell.json" <<'PY'
import json, pathlib, sys
plugins, out = pathlib.Path(sys.argv[1]), pathlib.Path(sys.argv[2])
off = set()
for manifest in plugins.rglob("manifest.json"):
    m = json.loads(manifest.read_text())
    if m.get("id") != "omarchy.bar" and set(m.get("kinds", [])) & {"service", "overlay", "menu", "panel"}:
        off.add(m["id"])
config = {"version": 1, "plugins": [], "disabledPlugins": sorted(off),
          "bar": {"position": "top", "layout": {"left": [], "center": [], "right": [{"id": "homalawn"}]}}}
out.write_text(json.dumps(config, indent=2))
PY
}

# Put the plugin where omarchy-shell looks for third-party plugins.
place_plugin() {
  local dir=$1 as_is=$2
  local target=$dir/home/.config/omarchy/plugins/homalawn
  rm -rf "$target"
  if [[ $as_is == yes ]]; then
    ln -s "$plugin" "$target"
    return
  fi
  # The same file list `homalawn desktop install` copies, read from desktop.py itself.
  python3 - "$plugin" "$target" <<'PY'
import importlib.util, json, pathlib, shutil, sys
src, dst = pathlib.Path(sys.argv[1]), pathlib.Path(sys.argv[2])
spec = importlib.util.spec_from_file_location("desktop", src.parent / "desktop.py")
desktop = importlib.util.module_from_spec(spec)
spec.loader.exec_module(desktop)
for rel, path in desktop._files(src).items():
    (dst / rel).parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(path, dst / rel)
shutil.copytree(src / "tools", dst / "tools")
manifest = dst / "manifest.json"
m = json.loads(manifest.read_text())
m["entryPoints"].update(panel="tools/TestPanel.qml", barWidget="tools/TestBarWidget.qml")
manifest.write_text(json.dumps(m, indent=2))
PY
}

start() {
  local dir socket theme=tokyo-night as_is=no
  dir=$(realpath -m -- "${1:?start needs DIR}"); shift
  socket=${HOMALAWN_SOCKET:-${XDG_RUNTIME_DIR:-/run/user/$(id -u)}/homalawn/homalawnd.sock}
  while (($#)); do
    case $1 in
      --socket) socket=$2; shift 2 ;;
      --theme) theme=$2; shift 2 ;;
      --as-is) as_is=yes; shift ;;
      *) die "unknown option $1" ;;
    esac
  done
  [[ -z $(shell_pid "$dir") ]] || die "a test shell already runs in $dir; stop it first"
  mkdir -p "$dir/shots"
  copy_shell "$dir"
  make_home "$dir" "$theme"
  place_plugin "$dir" "$as_is"
  # No display of any kind, so also no GTK platform theme (it exits without one).
  env -u WAYLAND_DISPLAY -u DISPLAY -u HYPRLAND_INSTANCE_SIGNATURE -u QT_QPA_PLATFORMTHEME -u QT_IM_MODULE \
    HOME="$dir/home" XDG_CONFIG_HOME="$dir/home/.config" XDG_STATE_HOME="$dir/home/.local/state" \
    XDG_DATA_HOME="$dir/home/.local/share" XDG_CACHE_HOME="$dir/home/.cache" \
    OMARCHY_PATH="$dir/omarchy-copy" HOMALAWN_SOCKET="$socket" HOMALAWN_TEST_SHOTS="$dir/shots" \
    QT_QPA_PLATFORM=offscreen QT_QUICK_BACKEND=software \
    dbus-run-session -- qs -p "$dir/omarchy-copy/shell" > "$dir/shell.log" 2>&1 &
  echo "test shell starting in $dir (log: $dir/shell.log, socket: $socket)"
}

cmd=${1:-}; shift || true
case $cmd in
  start) start "$@" ;;
  summon)
    dir=$(realpath -m -- "${1:?summon needs DIR}")
    page=${2:-}
    payload='{}'; [[ -n $page ]] && payload="{\"page\":\"$page\"}"
    qs ipc --pid "$(need_pid "$dir")" call shell summon homalawn "$payload" ;;
  call)
    dir=$(realpath -m -- "${1:?call needs DIR}")
    qs ipc --pid "$(need_pid "$dir")" call shell call homalawn "${2:?call needs FUNCTION}" "${3:-}" ;;
  stop)
    dir=$(realpath -m -- "${1:?stop needs DIR}")
    pid=$(shell_pid "$dir")
    if [[ -n $pid ]]; then kill "$pid"; echo "stopped test shell $pid"; else echo "no test shell in $dir"; fi ;;
  *) sed -n '2,21p' "${BASH_SOURCE[0]}"; exit 2 ;;
esac
