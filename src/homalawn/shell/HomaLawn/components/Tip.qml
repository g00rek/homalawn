import QtQuick
import ".."

// A tooltip under (or, with `side`, beside) its parent while `shown`: one short line.
Rectangle {
  id: tip
  property string text: ""
  property bool shown: false
  property bool side: false

  visible: shown && text !== ""
  x: side ? (parent ? parent.width + 6 : 0) : (parent ? Math.min(0, parent.width - width) : 0)
  y: side ? (parent ? (parent.height - height) / 2 : 0) : (parent ? parent.height + 6 : 0)
  width: Math.min(label.implicitWidth, 360) + 18
  height: label.implicitHeight + 10
  radius: 4
  color: Theme.darkerBackground
  border.width: 1
  border.color: Theme.muted
  z: 200
  Txt {
    id: label
    x: 9
    anchors.verticalCenter: parent.verticalCenter
    width: Math.min(implicitWidth, 360)
    text: tip.text
    wrapMode: Text.Wrap
    font.pixelSize: Theme.small
    color: Theme.brightForeground
  }
}
