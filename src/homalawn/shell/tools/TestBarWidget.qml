import QtQuick
import Quickshell
import ".."

// The bar mark as the offscreen harness loads it. With HOMALAWN_TEST_SHOTS set it photographs
// itself every few seconds (shots/bar-1.png, bar-2.png …) and logs what a hover would say.
BarWidget {
  id: testMark
  readonly property string shotsDir: Quickshell.env("HOMALAWN_TEST_SHOTS") || ""
  property int count: 0

  function record() {
    count += 1
    var file = shotsDir + "/bar-" + count + ".png"
    console.log("homalawn test: bar " + JSON.stringify({ tooltip: tooltip, accent: anyConnected, problem: problem, up: serviceUp }))
    grabToImage(function (picture) { picture.saveToFile(file) })
  }

  Timer {
    running: testMark.shotsDir.length > 0
    interval: 3000
    repeat: true
    onTriggered: testMark.record()
  }
}
