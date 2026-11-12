import QtQuick
import ".."

// An on/off switch with an optional label on its left, as in the sketch's "What to sync" rows.
Item {
  id: toggle
  property string text: ""
  property bool checked: false
  property bool active: true
  signal toggled(bool checked)

  implicitWidth: (label.text !== "" ? label.implicitWidth + 12 : 0) + track.width
  implicitHeight: Math.max(track.height, label.implicitHeight)
  opacity: active ? 1 : Theme.disabledOpacity

  Txt {
    id: label
    anchors.left: parent.left
    anchors.verticalCenter: parent.verticalCenter
    text: toggle.text
  }

  Rectangle {
    id: track
    anchors.right: parent.right
    anchors.verticalCenter: parent.verticalCenter
    width: 34
    height: 18
    radius: 9
    color: toggle.checked ? Theme.accent : Theme.selection
    border.width: toggle.checked ? 0 : 1
    border.color: Theme.muted
    Rectangle {
      anchors.verticalCenter: parent.verticalCenter
      x: toggle.checked ? parent.width - width - 2 : 2
      width: 14
      height: 14
      radius: 7
      color: toggle.checked ? Theme.darkerBackground : Theme.foreground
      Behavior on x { NumberAnimation { duration: 120 } }
    }
  }

  MouseArea {
    anchors.fill: parent
    cursorShape: toggle.active ? Qt.PointingHandCursor : Qt.ArrowCursor
    onClicked: if (toggle.active) toggle.toggled(!toggle.checked)
  }
}
