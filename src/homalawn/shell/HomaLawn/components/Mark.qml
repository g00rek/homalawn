import QtQuick
import QtQuick.Window
import ".."

// The Homa Lawn mark: the phone from Homa Lawn's picture, upright, as its rounded outline and the one
// app square near its top right corner (assets/homalawn-outline-SIZE.png and homalawn-app-SIZE.png,
// one white mask each). Each part is filled with its own colour: `outlineColor` and `appColor`, which
// both default to `color`. `size` is its square in logical pixels.
//
// The masks come pre-scaled at 16, 32, 64, 128 and 256 px; the smallest one at least as
// large as the physical size is drawn, so at bar size (16 px, or 32 px at scale 2) it lands
// pixel for pixel. A Canvas paints them and keeps only the part's colour where it is opaque: a
// Canvas, not MultiEffect, so it draws the same with the software renderer the offscreen tests use.
Item {
  id: mark
  property real size: 16
  property color color: Theme.brightForeground
  property color outlineColor: color
  property color appColor: color

  readonly property real dpr: Window.window ? Window.window.devicePixelRatio : Screen.devicePixelRatio
  readonly property int physical: Math.max(1, Math.round(size * dpr))
  readonly property int side: [16, 32, 64, 128].find(s => s >= physical) || 256
  readonly property var sources: ["outline", "app"].map(n => Qt.resolvedUrl("../assets/homalawn-" + n + "-" + side + ".png"))

  width: size
  height: size

  // One Canvas per part, stacked: each keeps only its own colour where its mask is opaque.
  Repeater {
    model: 2
    Canvas {
      id: canvas
      required property int index
      readonly property url src: mark.sources[index]
      readonly property color fill: [mark.outlineColor, mark.appColor][index]
      anchors.fill: parent
      canvasSize: Qt.size(mark.physical, mark.physical)
      onPaint: {
        var ctx = getContext("2d")
        ctx.reset()
        if (!isImageLoaded(src)) return
        ctx.drawImage(src, 0, 0, mark.physical, mark.physical)
        ctx.globalCompositeOperation = "source-in"
        ctx.fillStyle = fill
        ctx.fillRect(0, 0, mark.physical, mark.physical)
      }
      onImageLoaded: requestPaint()
      onFillChanged: requestPaint()
      onSrcChanged: load()
      function load() {
        if (isImageLoaded(src)) requestPaint()
        else loadImage(src)
      }
      Component.onCompleted: load()
    }
  }
}
