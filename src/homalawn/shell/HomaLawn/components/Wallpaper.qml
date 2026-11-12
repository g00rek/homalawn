import QtQuick
import ".."

// A phone wallpaper as the service cuts it: the picture covers the item, framed by `offset`
// (0 = left/top end, 1 = right/bottom end), with the colour layer over it (black for darker,
// white for lighter, at |amount| percent). Without a picture: the window's darkest colour.
//
// Parallax: with `span` 2 the picture is cut `span` times as wide as the item (the home screen's
// wide picture) and the item shows one screen-wide slice of it, `pan` along the spare width
// (0 = the left edge, page 0; 1 = the right edge).
Item {
  id: wall
  property string source: ""
  property real offset: 0.5
  property int amount: 0     // colour layer, -100 (white) .. 100 (black)
  property real span: 1
  property real pan: 0
  clip: true

  Rectangle {
    anchors.fill: parent
    gradient: Gradient {
      GradientStop { position: 0.0; color: Theme.blend(Theme.darkerBackground, Theme.accent, 0.18) }
      GradientStop { position: 1.0; color: Theme.darkerBackground }
    }
  }

  // The cut: span x the item's width, the item's height, slid by `pan`.
  Item {
    id: cut
    width: wall.width * Math.max(1, wall.span)
    height: wall.height
    x: -(width - wall.width) * Math.max(0, Math.min(1, wall.pan))

    Image {
      id: picture
      source: Paths.url(wall.source)
      sourceSize.width: 1280
      asynchronous: true
      autoTransform: true
      readonly property real srcW: implicitWidth
      readonly property real srcH: implicitHeight
      readonly property real k: srcW > 0 && srcH > 0 ? Math.max(cut.width / srcW, cut.height / srcH) : 1
      width: srcW * k
      height: srcH * k
      x: -(width - cut.width) * Math.max(0, Math.min(1, wall.offset))
      y: -(height - cut.height) * Math.max(0, Math.min(1, wall.offset))
      visible: status === Image.Ready
    }
  }

  Rectangle {
    anchors.fill: parent
    color: wall.amount > 0 ? "#000000" : "#ffffff"
    opacity: Math.abs(wall.amount) / 100
  }
}
