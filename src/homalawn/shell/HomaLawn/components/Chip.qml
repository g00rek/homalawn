import QtQuick
import ".."

// A phone in the header: a pill with a status dot and the phone's name. The selected one gets
// an accent outline; an offline phone has a grey dot and dimmed text.
Rectangle {
  id: chip
  property string text: ""
  property bool online: false
  property bool selected: false
  signal clicked()

  implicitWidth: row.implicitWidth + 22
  implicitHeight: 26
  radius: height / 2
  color: hover.containsMouse && !selected ? Theme.selection : Theme.lighterBackground
  border.width: 1
  border.color: selected ? Theme.accent : Theme.muted

  Row {
    id: row
    anchors.centerIn: parent
    spacing: 7
    Rectangle {
      anchors.verticalCenter: parent.verticalCenter
      width: 8
      height: 8
      radius: 4
      color: chip.online ? Theme.green : Theme.muted
    }
    Txt {
      anchors.verticalCenter: parent.verticalCenter
      text: chip.text
      font.pixelSize: Theme.small
      color: chip.selected ? Theme.brightForeground : (chip.online ? Theme.foreground : Theme.darkForeground)
    }
  }

  MouseArea {
    id: hover
    anchors.fill: parent
    hoverEnabled: true
    cursorShape: Qt.PointingHandCursor
    onClicked: chip.clicked()
  }
}
