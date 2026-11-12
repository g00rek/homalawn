import QtQuick
import ".."

// The window's one confirm step: a question bar with a yellow edge, Cancel and the action. Placed
// where the action was pressed (in a row, a card, or under a page's header). `destructive`: the
// action cannot be taken back, so its button is a red outline instead of the accent.
Rectangle {
  id: bar
  property string text: ""          // the question, e.g. "Unpair Pixel 8?"
  property string detail: ""        // one line on what happens
  property string action: ""        // the action button's word
  property bool destructive: false
  property bool working: false
  property bool active: true        // the action can run now
  signal confirmed()
  signal cancelled()

  width: parent ? parent.width : 0
  implicitHeight: Math.max(texts.implicitHeight, buttons.implicitHeight) + 18
  height: visible ? implicitHeight : 0
  radius: 4
  color: Theme.withAlpha(Theme.yellow, 0.12)
  border.width: 1
  border.color: Theme.withAlpha(Theme.yellow, 0.35)
  Rectangle { width: 3; height: parent.height; radius: 1.5; color: Theme.yellow }

  Column {
    id: texts
    anchors.left: parent.left
    anchors.leftMargin: 16
    anchors.right: buttons.left
    anchors.rightMargin: 12
    anchors.verticalCenter: parent.verticalCenter
    spacing: 2
    Txt {
      width: parent.width
      text: bar.text
      wrapMode: Text.Wrap
      font.bold: true
      color: Theme.brightForeground
    }
    Txt {
      visible: bar.detail !== ""
      width: parent.width
      text: bar.detail
      wrapMode: Text.Wrap
      font.pixelSize: Theme.small
      color: Theme.darkForeground
    }
  }
  Row {
    id: buttons
    anchors.right: parent.right
    anchors.rightMargin: 12
    anchors.verticalCenter: parent.verticalCenter
    spacing: 8
    OutlineButton {
      text: "Cancel"
      active: !bar.working
      onClicked: bar.cancelled()
    }
    OutlineButton {
      visible: bar.destructive
      danger: true
      text: bar.working ? bar.action + "…" : bar.action
      active: bar.active && !bar.working
      onClicked: bar.confirmed()
    }
    AccentButton {
      visible: !bar.destructive
      text: bar.action
      working: bar.working
      active: bar.active
      onClicked: bar.confirmed()
    }
  }
}
