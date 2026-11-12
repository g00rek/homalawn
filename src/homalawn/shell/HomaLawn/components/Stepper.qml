import QtQuick
import ".."

// A number with − and + beside its name: "Columns  − 5 +". `value` is what to show; stepped(v)
// asks for a new value (one `step` away, kept within from..to); the owner decides what changes.
Item {
  id: stepper
  property string text: ""
  property real value: 0
  property real step: 1
  property real from: 0
  property real to: 10
  property int decimals: 0
  property string suffix: ""
  property bool active: true
  signal stepped(real value)

  implicitWidth: name.implicitWidth + 8 + box.width
  implicitHeight: 28
  opacity: active ? 1 : Theme.disabledOpacity

  function shown(v) {
    var t = Number(v).toFixed(decimals)
    if (Number(t) < 0) t = "−" + t.slice(1)
    return t + suffix
  }
  function ask(dir) {
    var v = Math.round((value + dir * step) * 1000) / 1000
    v = Math.max(from, Math.min(to, v))
    if (v !== value) stepped(v)
  }

  Txt {
    id: name
    anchors.left: parent.left
    anchors.verticalCenter: parent.verticalCenter
    text: stepper.text
    font.pixelSize: Theme.small
    color: Theme.darkForeground
  }

  Rectangle {
    id: box
    anchors.right: parent.right
    anchors.verticalCenter: parent.verticalCenter
    width: minus.width + valueText.width + plus.width + 2
    height: 26
    radius: 5
    color: Theme.darkBackground
    border.width: 1
    border.color: Theme.muted

    component Btn: Rectangle {
      id: btn
      property string glyph: ""
      property bool can: true
      signal hit()
      width: 24
      height: box.height - 2
      y: 1
      radius: 4
      color: area.containsMouse && can && stepper.active ? Theme.selection : "transparent"
      Txt {
        anchors.centerIn: parent
        text: btn.glyph
        font.bold: true
        color: btn.can ? Theme.brightForeground : Theme.muted
      }
      MouseArea {
        id: area
        anchors.fill: parent
        hoverEnabled: true
        cursorShape: btn.can && stepper.active ? Qt.PointingHandCursor : Qt.ArrowCursor
        onClicked: if (btn.can && stepper.active) btn.hit()
      }
    }

    Btn {
      id: minus
      x: 1
      glyph: "−"
      can: stepper.value > stepper.from
      onHit: stepper.ask(-1)
    }
    Txt {
      id: valueText
      anchors.left: minus.right
      anchors.verticalCenter: parent.verticalCenter
      width: Math.max(50, implicitWidth + 8)
      horizontalAlignment: Text.AlignHCenter
      text: stepper.shown(stepper.value)
      font.pixelSize: Theme.small
      font.bold: true
      color: Theme.brightForeground
    }
    Btn {
      id: plus
      anchors.left: valueText.right
      glyph: "+"
      can: stepper.value < stepper.to
      onHit: stepper.ask(1)
    }
  }
}
