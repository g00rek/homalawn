import QtQuick
import ".."

// What every page shows while the Homa Lawn service is not running: one sentence and one Start.
Column {
  id: down
  property var backend: null
  signal said(string text)
  spacing: 14

  Mark {
    anchors.horizontalCenter: parent.horizontalCenter
    size: 48
    color: Theme.muted
  }
  Heading {
    anchors.horizontalCenter: parent.horizontalCenter
    text: "Homa Lawn service is not running"
  }
  Txt {
    anchors.horizontalCenter: parent.horizontalCenter
    text: "It keeps your phones connected, also with this window closed."
    color: Theme.darkForeground
    font.pixelSize: Theme.small
  }
  AccentButton {
    id: startButton
    anchors.horizontalCenter: parent.horizontalCenter
    text: "Start"
    active: !!down.backend
    onClicked: {
      working = true
      down.backend.startService(function (ok, why) {
        startButton.working = false
        if (!ok) down.said(why)
      })
    }
  }
}
