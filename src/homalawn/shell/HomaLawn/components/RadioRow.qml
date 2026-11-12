import QtQuick
import ".."

// One choice of a group: a round mark, a bold title with an optional muted aside, a line of
// explanation, and anything placed inside it (shown under the explanation).
Item {
  id: radio
  property string title: ""
  property string aside: ""        // shown after the title, e.g. "— now: tokyo-night"
  property string detail: ""
  property bool checked: false
  signal chosen()
  default property alias content: extra.data

  implicitHeight: texts.implicitHeight + 12
  width: parent ? parent.width : implicitWidth

  Rectangle {
    id: mark
    x: 0
    y: 8
    width: 16
    height: 16
    radius: 8
    color: "transparent"
    border.width: 2
    border.color: radio.checked ? Theme.accent : Theme.muted
    Rectangle {
      anchors.centerIn: parent
      visible: radio.checked
      width: 6
      height: 6
      radius: 3
      color: Theme.accent
    }
  }

  // The title and explanation are the click target; the content below handles its own clicks.
  MouseArea {
    anchors.left: parent.left
    anchors.right: parent.right
    anchors.top: parent.top
    height: head.y + head.height + 6
    cursorShape: Qt.PointingHandCursor
    onClicked: if (!radio.checked) radio.chosen()
  }

  Column {
    id: texts
    anchors.left: mark.right
    anchors.leftMargin: 10
    anchors.right: parent.right
    y: 6
    spacing: 2

    Item {
      id: head
      width: parent.width
      height: Math.max(titleText.implicitHeight, 18)
      Row {
        spacing: 6
        Txt {
          id: titleText
          text: radio.title
          font.bold: true
          color: radio.checked ? Theme.brightForeground : Theme.foreground
        }
        Txt {
          anchors.baseline: titleText.baseline
          visible: radio.aside !== ""
          text: radio.aside
          font.pixelSize: Theme.small
          color: Theme.darkForeground
        }
      }
    }
    Txt {
      width: parent.width
      visible: radio.detail !== ""
      text: radio.detail
      font.pixelSize: Theme.small
      color: Theme.darkForeground
      wrapMode: Text.Wrap
    }
    Item {
      id: extra
      width: parent.width
      implicitHeight: childrenRect.height
      height: implicitHeight
      visible: children.length > 0
    }
  }
}
