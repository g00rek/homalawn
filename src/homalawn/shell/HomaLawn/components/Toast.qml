import QtQuick
import ".."

// A short message in the top right corner. It stays 4 seconds (errors need reading time) or
// until clicked.
Rectangle {
  id: toast
  property string message: ""

  width: Math.min(380, (parent ? parent.width : 412) - 32)
  implicitHeight: body.implicitHeight + 22
  radius: 6
  color: Theme.darkBackground
  border.width: 1
  border.color: Theme.accent
  visible: message !== ""
  z: 100

  function clear() {
    message = ""
    clock.stop()
  }

  function show(text) {
    message = String(text || "")
    clock.restart()
  }

  Column {
    id: body
    anchors.left: parent.left
    anchors.right: parent.right
    anchors.top: parent.top
    anchors.margins: 12
    anchors.topMargin: 10
    spacing: 3
    Txt { text: "Homa Lawn"; color: Theme.accent; font.pixelSize: Theme.tiny; font.bold: true }
    Txt {
      width: parent.width
      text: toast.message
      font.pixelSize: Theme.small
      color: Theme.brightForeground
      wrapMode: Text.Wrap
    }
  }

  MouseArea {
    anchors.fill: parent
    onClicked: toast.message = ""
  }

  Timer {
    id: clock
    interval: 4000
    onTriggered: toast.message = ""
  }
}
