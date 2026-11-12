import QtQuick
import ".."

// A secondary action: an outline on the lighter background. `danger`: a red outline and red text,
// for an action that cannot be taken back. Unusable now (`active` false): no fill, a faint border
// and grey text.
Rectangle {
  id: button
  property string text: ""
  property bool active: true
  property bool danger: false
  property string tip: ""
  signal clicked()

  implicitWidth: label.implicitWidth + 26
  implicitHeight: label.implicitHeight + 12
  radius: 4
  color: !active ? "transparent"
    : hover.containsMouse ? (danger ? Theme.withAlpha(Theme.red, 0.14) : Theme.selection)
    : (danger ? "transparent" : Theme.lighterBackground)
  border.width: 1
  border.color: !active ? Theme.selection : (danger ? Theme.red : Theme.muted)

  Txt {
    id: label
    anchors.centerIn: parent
    text: button.text
    color: !button.active ? Theme.darkForeground : (button.danger ? Theme.red : Theme.brightForeground)
    opacity: button.active ? 1 : 0.8
    font.pixelSize: Theme.small
    font.bold: button.danger && button.active
  }

  MouseArea {
    id: hover
    anchors.fill: parent
    hoverEnabled: true
    cursorShape: button.active ? Qt.PointingHandCursor : Qt.ArrowCursor
    onClicked: if (button.active) button.clicked()
  }

  Tip { text: button.tip; shown: hover.containsMouse }
}
