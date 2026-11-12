import QtQuick
import Quickshell
import Quickshell.Io
import qs.Commons

// Homa Lawn's always-loaded part. It owns the single connection to the Homa Lawn service and keeps the
// text of the active Omarchy palette (colors.toml) for the window to paint with.
Item {
  id: homalawn
  visible: false

  property var shell: null
  property var manifest: null

  readonly property alias backend: connection
  property string themeText: ""

  readonly property string colorsPath:
    Quickshell.env("HOME") + "/.local/state/omarchy/current/theme/colors.toml"

  // The window calls this whenever it opens.
  function refresh() {
    palette.reload()
    connection.wake()
  }

  Backend {
    id: connection
  }

  FileView {
    id: palette
    path: homalawn.colorsPath
    watchChanges: true
    printErrors: false
    onLoaded: homalawn.themeText = text()
    onLoadFailed: homalawn.themeText = ""
    // The change signal carries stale text; read the file again instead.
    onFileChanged: reload()
  }

  // A theme switch swaps the whole theme folder and tells the shell over IPC rather than
  // through the file we watch, so re-read whenever the shell's own colours move. Several
  // colours change together; one read covers them.
  Timer {
    id: rereadSoon
    interval: 200
    onTriggered: palette.reload()
  }

  Connections {
    target: Color
    function onForegroundChanged() { rereadSoon.restart() }
    function onBackgroundChanged() { rereadSoon.restart() }
    function onAccentChanged() { rereadSoon.restart() }
  }
}
