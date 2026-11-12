import QtQuick
import ".."

// A "Details" disclosure: a line that opens to show its children (technical facts kept out of
// the way).
Column {
  id: details
  property string label: "Details"
  property bool open: false
  default property alias content: inner.data
  width: parent ? parent.width : 0
  spacing: 8

  Txt {
    text: (details.open ? "▾ " : "▸ ") + details.label
    font.pixelSize: Theme.small
    color: area.containsMouse ? Theme.brightForeground : Theme.darkForeground
    MouseArea {
      id: area
      anchors.fill: parent
      hoverEnabled: true
      cursorShape: Qt.PointingHandCursor
      onClicked: details.open = !details.open
    }
  }
  Column {
    id: inner
    visible: details.open
    width: parent.width
    spacing: 6
  }
}
