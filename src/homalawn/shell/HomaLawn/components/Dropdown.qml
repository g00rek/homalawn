import QtQuick
import ".."

// A closed list: the chosen option's label and a ▾; a click opens the options under it. `options`
// is a list of {key, label} (an option with `disabled: true` is shown but cannot be chosen),
// `current` the chosen key. Choosing another one emits picked(key); the owner decides what
// changes. The open list is drawn on `overlay` (the page) so a scrolling, clipped column does not
// cut it off; without one it opens inside the dropdown's own parent.
Rectangle {
  id: dd
  property var options: []
  property string current: ""
  property bool active: true
  property bool marked: false          // the draft differs from the phone here
  property Item overlay: null
  property bool open: false
  signal picked(string key)

  readonly property var chosen: options.find(o => o.key === current) || null

  implicitWidth: 240
  implicitHeight: 30
  radius: 5
  color: hover.containsMouse && active ? Theme.selection : Theme.darkerBackground
  border.width: 1
  border.color: open ? Theme.accent : (marked ? Theme.accent : Theme.muted)
  opacity: active ? 1 : Theme.disabledOpacity

  Txt {
    anchors.left: parent.left
    anchors.leftMargin: 10
    anchors.right: chevron.left
    anchors.rightMargin: 6
    anchors.verticalCenter: parent.verticalCenter
    elide: Text.ElideRight
    text: dd.chosen ? dd.chosen.label : dd.current
    font.pixelSize: Theme.small
    color: Theme.brightForeground
  }
  Txt {
    id: chevron
    anchors.right: parent.right
    anchors.rightMargin: 10
    anchors.verticalCenter: parent.verticalCenter
    text: dd.open ? "▴" : "▾"
    font.pixelSize: Theme.small
    color: Theme.darkForeground
  }
  MouseArea {
    id: hover
    anchors.fill: parent
    hoverEnabled: true
    cursorShape: dd.active ? Qt.PointingHandCursor : Qt.ArrowCursor
    onClicked: if (dd.active) dd.open = !dd.open
  }

  onActiveChanged: if (!active) open = false

  // A click anywhere else closes the list.
  MouseArea {
    parent: dd.overlay || dd.parent
    anchors.fill: parent
    visible: dd.open
    z: 99
    onClicked: dd.open = false
  }

  Rectangle {
    id: list
    parent: dd.overlay || dd.parent
    visible: dd.open
    z: 100
    readonly property point at: {
      // re-read when it opens (the column may have scrolled meanwhile)
      var o = dd.open
      return dd.mapToItem(parent, 0, dd.height + 2)
    }
    readonly property real room: parent ? parent.height - at.y - 8 : 400
    x: at.x
    // Opens upwards when there is no room below.
    y: room < Math.min(column.height, 160) ? dd.mapToItem(parent, 0, 0).y - height - 2 : at.y
    width: dd.width
    height: Math.min(column.height + 8, Math.max(room, 160))
    radius: 5
    color: Theme.darkBackground
    border.width: 1
    border.color: Theme.accent
    clip: true

    Flickable {
      anchors.fill: parent
      anchors.margins: 4
      contentHeight: column.height
      boundsBehavior: Flickable.StopAtBounds
      interactive: contentHeight > height
      Column {
        id: column
        width: parent.width
        Repeater {
          model: dd.options
          Rectangle {
            id: opt
            required property var modelData
            readonly property bool on: modelData.key === dd.current
            readonly property bool can: !modelData.disabled
            width: column.width
            height: 28
            radius: 4
            color: on ? Theme.withAlpha(Theme.accent, 0.22) : (optHover.containsMouse && can ? Theme.selection : "transparent")
            Txt {
              anchors.left: parent.left
              anchors.leftMargin: 8
              anchors.right: parent.right
              anchors.rightMargin: 8
              anchors.verticalCenter: parent.verticalCenter
              elide: Text.ElideRight
              text: opt.modelData.label
              font.pixelSize: Theme.small
              font.bold: opt.on
              color: !opt.can ? Theme.darkForeground : opt.on ? Theme.accent : Theme.foreground
            }
            MouseArea {
              id: optHover
              anchors.fill: parent
              hoverEnabled: true
              cursorShape: opt.can ? Qt.PointingHandCursor : Qt.ArrowCursor
              onClicked: {
                if (!opt.can) return
                dd.open = false
                if (!opt.on) dd.picked(opt.modelData.key)
              }
            }
          }
        }
      }
    }
  }
}
