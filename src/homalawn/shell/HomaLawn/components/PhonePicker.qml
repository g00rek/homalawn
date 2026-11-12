import QtQuick
import ".."

// The rail's phone picker: one bordered button that says which phone the pages below work on (its
// name, and under it its status with the status dot) with a ▾; a click opens the list of paired
// phones (the chosen one checked), a divider and "Add phone…". `rows` is a list of
// {id, name, status, brief, dot} (`brief`: the status in a word or two, for the button; the list
// and the tooltip give `status` in full), `current` the chosen id. Choosing a phone emits picked(id), the last
// row addPhone(); the owner decides what changes. With no phone paired the button says so and a
// click emits addPhone() at once (no list). `active: false` (the service is not running) leaves
// nothing to choose. Collapsed (`expanded: false`) it is a square with the phone's initial and its
// dot, a tooltip, and the same list to the right of the rail. The list is drawn on `overlay` (the
// window's content), above the rail and the page; a click anywhere else closes it.
Item {
  id: picker
  property var rows: []
  property string current: ""
  property bool expanded: true
  property bool active: true
  property string idleText: ""         // under the title while not active, e.g. why
  property Item overlay: null
  property bool open: false
  signal picked(string id)
  signal addPhone()

  readonly property var chosen: rows.find(r => r.id === current) || null
  readonly property bool empty: rows.length === 0
  // What the button says: the chosen phone, "No phone yet", or (not active) just "Phone".
  readonly property string title: !active ? "Phone" : chosen ? chosen.name : empty ? "No phone yet" : "Choose a phone"
  readonly property string status: !active ? idleText : chosen ? (chosen.brief || chosen.status) : ""
  readonly property string tip: !active ? "" : chosen ? chosen.name + " · " + chosen.status : empty ? "Add phone" : title
  readonly property int listWidth: 288

  // The button's click.
  function press() {
    if (!active) return false
    if (empty) { addPhone(); return true }
    open = !open
    return true
  }
  // A phone's row in the open list.
  function choose(id) {
    if (!open || !rows.some(r => r.id === id)) return false
    open = false
    picked(id)
    return true
  }
  // The list's last row.
  function add() {
    if (!open) return false
    open = false
    addPhone()
    return true
  }

  onActiveChanged: if (!active) open = false
  onEmptyChanged: if (empty) open = false
  onExpandedChanged: open = false

  width: parent ? parent.width : 224
  height: expanded ? 52 : 48

  Rectangle {
    id: face
    x: 8
    width: parent.width - 16
    height: parent.height
    radius: 6
    color: hover.containsMouse && picker.active ? Theme.selection : Theme.darkerBackground
    border.width: 1
    border.color: picker.open ? Theme.accent : Theme.muted
    opacity: picker.active ? 1 : Theme.disabledOpacity

    // Expanded: the glyph where the page rows have theirs; collapsed without a phone: the same.
    Glyph {
      id: glyph
      visible: picker.expanded || !picker.chosen
      x: Math.round((48 - width) / 2)
      anchors.verticalCenter: parent.verticalCenter
      code: picker.active && picker.empty ? Icons.add : Icons.phone
      size: 18
      color: picker.chosen ? Theme.brightForeground : Theme.foreground
    }
    // Collapsed: the chosen phone's initial.
    Rectangle {
      id: letter
      visible: !glyph.visible
      anchors.centerIn: parent
      width: 26
      height: 26
      radius: 13
      color: "transparent"
      border.width: 2
      border.color: Theme.accent
      Txt {
        anchors.centerIn: parent
        text: picker.chosen ? String(picker.chosen.name || "?").trim().charAt(0).toUpperCase() : ""
        font.bold: true
        color: Theme.brightForeground
      }
    }
    Rectangle {
      visible: letter.visible
      x: letter.x + letter.width - 7
      y: letter.y + letter.height - 9
      width: 9
      height: 9
      radius: 4.5
      color: picker.chosen ? picker.chosen.dot : "transparent"
      border.width: 2
      border.color: face.color
    }

    Column {
      visible: picker.expanded
      anchors.left: parent.left
      anchors.leftMargin: 48
      anchors.right: chevron.left
      anchors.rightMargin: 4
      anchors.verticalCenter: parent.verticalCenter
      spacing: 3
      Txt {
        width: parent.width
        elide: Text.ElideRight
        text: picker.title
        color: picker.chosen ? Theme.brightForeground : Theme.foreground
        font.bold: !!picker.chosen
      }
      Row {
        visible: picker.status !== ""
        width: parent.width
        spacing: 6
        Rectangle {
          visible: !!picker.chosen
          anchors.verticalCenter: parent.verticalCenter
          width: 7
          height: 7
          radius: 3.5
          color: picker.chosen ? picker.chosen.dot : "transparent"
        }
        Txt {
          width: parent.width - (picker.chosen ? 13 : 0)
          elide: Text.ElideRight
          text: picker.status
          font.pixelSize: Theme.tiny
          color: Theme.darkForeground
        }
      }
    }
    Glyph {
      id: chevron
      visible: picker.expanded && picker.active && !picker.empty
      anchors.right: parent.right
      anchors.rightMargin: 8
      anchors.verticalCenter: parent.verticalCenter
      width: visible ? Math.round(size * 1.3) : 0
      code: picker.open ? Icons.chevronUp : Icons.chevronDown
      size: 16
      color: Theme.darkForeground
    }

    MouseArea {
      id: hover
      anchors.fill: parent
      hoverEnabled: true
      cursorShape: picker.active ? Qt.PointingHandCursor : Qt.ArrowCursor
      onClicked: picker.press()
    }
  }

  // Collapsed: the phone and its state as a tooltip to the right of the rail.
  Tip {
    side: true
    text: picker.tip
    shown: !picker.expanded && hover.containsMouse && !picker.open
  }

  // A click anywhere else closes the list.
  MouseArea {
    parent: picker.overlay || picker.parent
    anchors.fill: parent
    visible: picker.open
    z: 99
    onClicked: picker.open = false
  }

  Rectangle {
    id: list
    parent: picker.overlay || picker.parent
    visible: picker.open
    z: 100
    readonly property point at: {
      // re-read when it opens or the rail changes
      var o = picker.open, e = picker.expanded, w = picker.width
      return e ? picker.mapToItem(parent, face.x, picker.height + 4) : picker.mapToItem(parent, picker.width + 4, 0)
    }
    readonly property real room: parent ? parent.height - at.y - 8 : 400
    x: at.x
    y: at.y
    width: picker.listWidth
    height: Math.min(column.height + 8, Math.max(room, 120))
    radius: 6
    color: Theme.darkBackground
    border.width: 1
    border.color: Theme.accent
    clip: true

    // Clicks between the rows stay in the list.
    MouseArea { anchors.fill: parent }

    Flickable {
      anchors.fill: parent
      anchors.margins: 4
      contentHeight: column.height
      boundsBehavior: Flickable.StopAtBounds
      interactive: contentHeight > height
      clip: true
      Column {
        id: column
        width: parent.width
        Repeater {
          model: picker.rows
          Rectangle {
            id: row
            required property var modelData
            readonly property bool on: modelData.id === picker.current
            width: column.width
            height: Math.max(44, rowWords.height + 14)
            radius: 4
            color: on ? Theme.withAlpha(Theme.accent, 0.22) : (rowHover.containsMouse ? Theme.selection : "transparent")
            Glyph {
              visible: row.on
              x: 6
              anchors.verticalCenter: parent.verticalCenter
              code: Icons.check
              size: 16
              color: Theme.accent
            }
            Column {
              id: rowWords
              x: 36
              width: parent.width - x - 10
              anchors.verticalCenter: parent.verticalCenter
              spacing: 3
              Txt {
                width: parent.width
                elide: Text.ElideRight
                text: row.modelData.name
                font.bold: row.on
                color: row.on ? Theme.brightForeground : Theme.foreground
              }
              Row {
                width: parent.width
                spacing: 6
                Rectangle {
                  y: 4
                  width: 7
                  height: 7
                  radius: 3.5
                  color: row.modelData.dot
                }
                Txt {
                  width: parent.width - 13
                  wrapMode: Text.Wrap
                  maximumLineCount: 2
                  elide: Text.ElideRight
                  text: row.modelData.status
                  font.pixelSize: Theme.tiny
                  color: Theme.darkForeground
                }
              }
            }
            MouseArea {
              id: rowHover
              anchors.fill: parent
              hoverEnabled: true
              cursorShape: Qt.PointingHandCursor
              onClicked: picker.choose(row.modelData.id)
            }
          }
        }

        Item {
          width: column.width
          height: 9
          Rectangle {
            x: 6
            anchors.verticalCenter: parent.verticalCenter
            width: parent.width - 12
            height: 1
            color: Theme.selection
          }
        }

        Rectangle {
          width: column.width
          height: 36
          radius: 4
          color: addHover.containsMouse ? Theme.selection : "transparent"
          Glyph {
            x: 6
            anchors.verticalCenter: parent.verticalCenter
            code: Icons.add
            size: 16
          }
          Txt {
            x: 36
            anchors.verticalCenter: parent.verticalCenter
            text: "Add phone…"
          }
          MouseArea {
            id: addHover
            anchors.fill: parent
            hoverEnabled: true
            cursorShape: Qt.PointingHandCursor
            onClicked: picker.add()
          }
        }
      }
    }
  }
}
