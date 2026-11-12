import QtQuick
import ".."

// A QR code from the service: `matrix` is its rows of 0/1 (1 = dark), quiet border included. Drawn
// as whole-pixel squares, black on white whatever the theme (a camera reads dark on light), each
// module `module` pixels: the smallest whole size that makes the code at least `least` pixels.
// Rows of dark modules are drawn as runs (one rectangle per run), so a code is a few hundred
// rectangles, not thousands.
Rectangle {
  id: qr
  property var matrix: []
  property int least: 225
  readonly property int count: matrix && matrix.length ? matrix.length : 0
  readonly property int module: count ? Math.max(3, Math.ceil(least / count)) : 0
  // [{x, y, n}]: n dark modules from (x, y) to the right.
  readonly property var runs: {
    var out = []
    for (var y = 0; y < count; y++) {
      var row = matrix[y]
      for (var x = 0; x < row.length; x++) {
        if (!row[x]) continue
        var n = 1
        while (x + n < row.length && row[x + n]) n++
        out.push({ x: x, y: y, n: n })
        x += n - 1
      }
    }
    return out
  }

  width: count * module
  height: width
  color: "#ffffff"

  Repeater {
    model: qr.runs
    Rectangle {
      required property var modelData
      x: modelData.x * qr.module
      y: modelData.y * qr.module
      width: modelData.n * qr.module
      height: qr.module
      color: "#000000"
      antialiasing: false
    }
  }
}
