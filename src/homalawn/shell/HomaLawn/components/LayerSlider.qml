import QtQuick
import ".."

// The colour layer over the wallpaper: −100 (white at full strength, "lighter") through 0 to
// +100 (black, "darker"). `value` is what to show; moved(value) reports every change while
// dragging, in whole steps of 1. Without `text` the slider starts at the left edge.
Item {
  id: slider
  property string text: ""
  property int value: 0
  property bool active: true
  signal moved(int value)

  implicitHeight: 28
  opacity: active ? 1 : Theme.disabledOpacity

  function label(v) {
    if (v < 0) return "−" + (-v) + "%"
    return v + "%"
  }

  function valueAt(x) {
    var f = Math.max(0, Math.min(1, x / track.width))
    return Math.round(f * 200 - 100)
  }

  Txt {
    id: name
    anchors.left: parent.left
    anchors.verticalCenter: parent.verticalCenter
    width: slider.text !== "" ? 100 : 0
    text: slider.text
    elide: Text.ElideRight
  }
  Txt {
    id: lighter
    anchors.left: name.right
    anchors.verticalCenter: parent.verticalCenter
    text: "lighter"
    font.pixelSize: Theme.small
    color: Theme.darkForeground
  }
  Txt {
    id: amount
    anchors.right: parent.right
    anchors.verticalCenter: parent.verticalCenter
    width: 48
    horizontalAlignment: Text.AlignRight
    text: slider.label(slider.value)
    font.bold: true
    color: Theme.brightForeground
  }
  Txt {
    id: darker
    anchors.right: amount.left
    anchors.rightMargin: 4
    anchors.verticalCenter: parent.verticalCenter
    text: "darker"
    font.pixelSize: Theme.small
    color: Theme.darkForeground
  }

  Item {
    id: area
    anchors.left: lighter.right
    anchors.leftMargin: 12
    anchors.right: darker.left
    anchors.rightMargin: 12
    anchors.verticalCenter: parent.verticalCenter
    height: 22

    Rectangle {
      id: track
      anchors.left: parent.left
      anchors.right: parent.right
      anchors.verticalCenter: parent.verticalCenter
      height: 6
      radius: 3
      gradient: Gradient {
        orientation: Gradient.Horizontal
        GradientStop { position: 0.0; color: "#ffffff" }
        GradientStop { position: 0.5; color: Theme.muted }
        GradientStop { position: 1.0; color: "#000000" }
      }
      border.width: 1
      border.color: Theme.withAlpha(Theme.muted, 0.6)
    }
    // Zero mark.
    Rectangle {
      x: track.width / 2 - 1
      anchors.verticalCenter: parent.verticalCenter
      width: 2
      height: 12
      radius: 1
      color: Theme.darkForeground
    }
    Rectangle {
      id: knob
      x: (slider.value + 100) / 200 * track.width - width / 2
      anchors.verticalCenter: parent.verticalCenter
      width: 16
      height: 16
      radius: 8
      color: Theme.accent
      border.width: 2
      border.color: Theme.darkerBackground
    }
    MouseArea {
      anchors.fill: parent
      anchors.leftMargin: -8
      anchors.rightMargin: -8
      cursorShape: slider.active ? Qt.PointingHandCursor : Qt.ArrowCursor
      enabled: slider.active
      preventStealing: true
      function report(mx) {
        var v = slider.valueAt(mx - 8)
        if (v !== slider.value) slider.moved(v)
      }
      onPressed: mouse => report(mouse.x)
      onPositionChanged: mouse => { if (pressed) report(mouse.x) }
    }
  }
}
