import QtQuick
import ".."

// A row of joined buttons, one of them chosen: `options` is a list of {key, label}, `current`
// the chosen key. Clicking another one emits picked(key); the owner decides what changes.
Rectangle {
  id: seg
  property var options: []
  property string current: ""
  property bool active: true
  property int pad: 28              // room around each label (a narrow panel asks for less)
  signal picked(string key)

  implicitWidth: row.implicitWidth + 2
  implicitHeight: 28
  radius: 5
  color: "transparent"
  border.width: 1
  border.color: Theme.muted
  clip: true
  opacity: active ? 1 : Theme.disabledOpacity

  Row {
    id: row
    anchors.left: parent.left
    anchors.leftMargin: 1
    anchors.verticalCenter: parent.verticalCenter
    Repeater {
      model: seg.options
      Rectangle {
        required property var modelData
        required property int index
        readonly property bool on: seg.current === modelData.key
        width: label.implicitWidth + seg.pad
        height: seg.height - 2
        radius: 4
        color: on ? Theme.accent : (hover.containsMouse && seg.active ? Theme.selection : "transparent")
        // Thin divider between unselected neighbours.
        Rectangle {
          visible: index > 0 && !parent.on
          anchors.left: parent.left
          anchors.verticalCenter: parent.verticalCenter
          width: 1
          height: parent.height - 10
          color: Theme.selection
        }
        Txt {
          id: label
          anchors.centerIn: parent
          text: modelData.label
          font.pixelSize: Theme.small
          font.bold: parent.on
          color: parent.on ? Theme.darkerBackground : Theme.foreground
        }
        MouseArea {
          id: hover
          anchors.fill: parent
          hoverEnabled: true
          cursorShape: seg.active ? Qt.PointingHandCursor : Qt.ArrowCursor
          onClicked: if (seg.active && !parent.on) seg.picked(modelData.key)
        }
      }
    }
  }
}
