import QtQuick
import ".."

// One home screen's grid: its items at their cells, the search/date bar across the reserved top
// row, and (while dragging) the cells a drop would take, accent when it may, red when it may not.
// With an `editor` (the Home screen page) items can be dragged and folders opened; without one
// (a thumbnail) it only draws.
Item {
  id: grid
  property var screen: null          // {index, items}
  property int cols: 4
  property int rows: 5
  property bool reservedTop: false
  property var m: ({ icon: 32, label: 9, gap: 3, labels: true })
  property var icons: ({})
  property var widgets: ({})
  property bool mini: false
  property var mono: null            // {plate, color}: draw the phone's monochrome icons (HomeTile)
  property var editor: null
  property var problems: ({})        // item key -> message
  property string liftedKey: ""
  property var highlight: []         // [{x, y, ok}]

  readonly property real cellW: width / Math.max(1, cols)
  readonly property real cellH: height / Math.max(1, rows)

  // The search/date bar Lawnchair keeps on the first screen's top row.
  Rectangle {
    visible: grid.reservedTop
    x: grid.cellW * 0.12
    y: grid.cellH * 0.28
    width: grid.width - grid.cellW * 0.24
    height: grid.cellH * 0.44
    radius: height / 2
    color: Theme.withAlpha("#ffffff", grid.mini ? 0.35 : 0.2)
    Text {
      visible: !grid.mini
      anchors.verticalCenter: parent.verticalCenter
      x: parent.height * 0.5
      text: Qt.formatDate(new Date(), "ddd, MMM d")
      color: "#ffffff"
      font.pixelSize: Math.max(7, grid.m.label * 1.15)
      textFormat: Text.PlainText
    }
    Text {
      visible: !grid.mini
      anchors.verticalCenter: parent.verticalCenter
      anchors.right: parent.right
      anchors.rightMargin: parent.height * 0.5
      text: "search & date bar"
      color: Theme.withAlpha("#ffffff", 0.7)
      font.pixelSize: Math.max(6, grid.m.label * 0.9)
      textFormat: Text.PlainText
    }
  }

  Repeater {
    model: grid.screen ? grid.screen.items : []
    HomeTile {
      id: tile
      required property var modelData
      readonly property var span: modelData.type === "widget" ? [modelData.w || 1, modelData.h || 1] : [1, 1]
      x: (modelData.x || 0) * grid.cellW
      y: (modelData.y || 0) * grid.cellH
      width: span[0] * grid.cellW
      height: span[1] * grid.cellH
      item: modelData
      icons: grid.icons
      widgets: grid.widgets
      m: grid.m
      mini: grid.mini
      mono: grid.mono
      problem: grid.problems[modelData._k] || ""
      lifted: grid.liftedKey !== "" && grid.liftedKey === modelData._k
      DragSource {
        anchors.fill: parent
        enabled: !!grid.editor
        editor: grid.editor
        src: ({ item: tile.modelData._k })
        onTapped: if (tile.modelData.type === "folder") grid.editor.openFolderAt(tile.modelData._k)
      }
    }
  }

  Repeater {
    model: grid.highlight
    Rectangle {
      required property var modelData
      visible: modelData.x >= 0 && modelData.y >= 0 && modelData.x < grid.cols && modelData.y < grid.rows
      x: modelData.x * grid.cellW + 2
      y: modelData.y * grid.cellH + 2
      width: grid.cellW - 4
      height: grid.cellH - 4
      radius: 8
      color: Theme.withAlpha(modelData.ok ? Theme.accent : Theme.red, 0.28)
      border.width: 2
      border.color: modelData.ok ? Theme.accent : Theme.red
    }
  }
}
