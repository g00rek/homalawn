import QtQuick
import ".."

// The main action of a screen: accent fill, dark bold text. Unusable now (`active` false): a flat
// grey button with grey text, so it never reads as the accent.
Rectangle {
  id: button
  property string text: ""
  property bool active: true
  property bool working: false   // shows "…" and ignores clicks while an action runs
  property string tip: ""
  signal clicked()

  readonly property bool usable: active && !working
  implicitWidth: label.implicitWidth + 28
  implicitHeight: label.implicitHeight + 12
  radius: 4
  color: !active ? Theme.selection
    : hover.containsMouse && !working ? Theme.blend(Theme.accent, Theme.brightForeground, 0.15) : Theme.accent

  Txt {
    id: label
    anchors.centerIn: parent
    text: button.working ? button.text + "…" : button.text
    color: button.active ? Theme.darkerBackground : Theme.darkForeground
    opacity: button.active ? 1 : 0.8
    font.pixelSize: Theme.small
    font.bold: true
  }

  MouseArea {
    id: hover
    anchors.fill: parent
    hoverEnabled: true
    cursorShape: button.usable ? Qt.PointingHandCursor : Qt.ArrowCursor
    onClicked: if (button.usable) button.clicked()
  }

  Tip { text: button.tip; shown: hover.containsMouse }
}
