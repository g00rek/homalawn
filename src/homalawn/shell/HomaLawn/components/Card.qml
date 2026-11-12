import QtQuick
import ".."

// A content card: a titled panel on the darker background. Children go in a column below the
// title (or right under the top edge when there is no title).
Rectangle {
  id: card
  property string title: ""
  property string subtitle: ""
  default property alias content: inner.data
  property alias spacing: inner.spacing

  width: parent ? parent.width : 0
  implicitHeight: inner.y + inner.implicitHeight + 18
  height: implicitHeight
  radius: 6
  color: Theme.darkBackground
  border.width: 1
  border.color: Theme.selection

  Column {
    id: head
    x: 18
    y: 16
    width: card.width - 36
    spacing: 4
    visible: card.title !== ""
    Heading {
      width: parent.width
      wrapMode: Text.Wrap
      text: card.title
    }
    Txt {
      visible: card.subtitle !== ""
      width: parent.width
      text: card.subtitle
      font.pixelSize: Theme.small
      color: Theme.darkForeground
      wrapMode: Text.Wrap
    }
  }

  Column {
    id: inner
    x: 18
    y: card.title !== "" ? head.y + head.implicitHeight + 14 : 18
    width: card.width - 36
    spacing: 10
  }
}
