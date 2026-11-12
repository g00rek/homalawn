import QtQuick
import ".."

// One entry of the window's left rail: a glyph in a 48 px square, with its label beside it when
// the rail is expanded. `dot` (a colour) marks a state, e.g. the service not running; `draft` puts
// an accent dot after the label
// (on the glyph's corner when collapsed): this section holds changes not applied yet. Collapsed,
// hovering shows the label as a tooltip.
Item {
  id: button
  property int code: 0
  property string label: ""
  property string tip: label
  property bool expanded: false
  property bool selected: false
  property bool active: true
  property color dot: "transparent"
  property bool showDot: false
  property bool draft: false
  signal clicked()

  width: parent ? parent.width : 48
  height: 40

  Rectangle {
    anchors.fill: parent
    anchors.leftMargin: 8
    anchors.rightMargin: 8
    radius: 6
    color: button.selected ? Theme.selection
      : (hover.containsMouse && button.active ? Theme.withAlpha(Theme.selection, 0.55) : "transparent")
  }

  Rectangle {
    // the accent bar of the selected entry
    visible: button.selected
    x: 8
    width: 3
    height: 18
    radius: 1.5
    anchors.verticalCenter: parent.verticalCenter
    color: Theme.accent
  }

  readonly property color ink: button.selected ? Theme.brightForeground : (button.active ? Theme.foreground : Theme.darkForeground)

  Glyph {
    id: glyph
    x: 8 + Math.round((48 - width) / 2)
    anchors.verticalCenter: parent.verticalCenter
    code: button.code
    size: 18
    color: button.ink
  }
  Rectangle {
    visible: button.showDot
    x: glyph.x + glyph.width - 7
    y: glyph.y + glyph.height - 9
    width: 9
    height: 9
    radius: 4.5
    color: button.dot
    border.width: 2
    border.color: Theme.darkBackground
  }

  Txt {
    id: name
    visible: button.expanded
    x: 56
    anchors.verticalCenter: parent.verticalCenter
    width: Math.min(implicitWidth, parent.width - x - (button.draft ? 30 : 16))
    text: button.label
    elide: Text.ElideRight
    color: button.ink
    font.bold: button.selected
  }

  // Changes not applied yet: right after the label, or on the glyph's corner when collapsed.
  Rectangle {
    visible: button.draft
    x: button.expanded ? name.x + name.width + 8 : glyph.x + glyph.width - 4
    y: button.expanded ? (parent.height - height) / 2 : glyph.y - 1
    width: 8
    height: 8
    radius: 4
    color: Theme.accent
  }

  MouseArea {
    id: hover
    anchors.fill: parent
    hoverEnabled: true
    cursorShape: button.active ? Qt.PointingHandCursor : Qt.ArrowCursor
    onClicked: if (button.active) button.clicked()
  }

  // Collapsed: the label (and state) as a tooltip to the right of the rail.
  Tip {
    side: true
    text: button.tip + (button.draft ? " · not applied yet" : "")
    shown: !button.expanded && hover.containsMouse && button.tip !== ""
  }
}
