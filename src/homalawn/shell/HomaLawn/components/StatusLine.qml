import QtQuick
import ".."

// One full-width line under a page's header: a coloured edge, the sentence, and at most one
// action on the right (`action` text; clicked() when pressed).
Rectangle {
  id: line
  property string text: ""
  property color tone: Theme.yellow
  property string action: ""
  property bool actionActive: true
  property bool actionWorking: false
  signal clicked()

  width: parent ? parent.width : 0
  height: visible ? Math.max(words.implicitHeight, action !== "" ? button.implicitHeight : 0) + 16 : 0
  color: Theme.withAlpha(tone, 0.12)
  Rectangle { width: 3; height: parent.height; color: line.tone }
  Txt {
    id: words
    anchors.left: parent.left
    anchors.leftMargin: Theme.margin
    anchors.right: line.action !== "" ? button.left : parent.right
    anchors.rightMargin: line.action !== "" ? 12 : Theme.margin
    anchors.verticalCenter: parent.verticalCenter
    text: line.text
    wrapMode: Text.Wrap
    font.pixelSize: Theme.small
    color: Theme.brightForeground
  }
  OutlineButton {
    id: button
    visible: line.action !== ""
    anchors.right: parent.right
    anchors.rightMargin: Theme.margin
    anchors.verticalCenter: parent.verticalCenter
    text: line.actionWorking ? line.action + "…" : line.action
    active: line.actionActive && !line.actionWorking
    onClicked: line.clicked()
  }
}
