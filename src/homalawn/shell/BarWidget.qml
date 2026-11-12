import QtQuick
import Quickshell
import qs.Commons
import qs.Ui
import "HomaLawn" as HomaLawnTheme
import "HomaLawn/components" as HomaLawnUi
import "HomaLawn/Status.js" as Status

// The Homa Lawn mark (the phone's outline and its app square) in the bar: the outline in the theme's
// accent and the app square in its red while at least one phone is connected, both in the theme's
// muted colour otherwise, dimmed while the service is down. A phone that needs attention (the
// service reports an error for it, or it is connected and has not allowed the home app, running
// in background or notifications) turns the whole mark the warning colour. Hover lists the phones
// (and which one needs attention and why, and which has an update available; Status.js), a left
// click opens or closes the window.
BarWidget {
  id: mark
  moduleName: "homalawn"

  readonly property var host: bar ? bar.shell : null
  readonly property var homalawn: host && typeof host.serviceFor === "function" ? host.serviceFor("homalawn") : null
  readonly property bool serviceUp: !!homalawn && homalawn.backend.connected
  readonly property var phones: serviceUp ? homalawn.backend.phones : []
  // The palette the service keeps (the same text the window paints with); the mark takes its
  // colours from it.
  readonly property string themeText: homalawn ? homalawn.themeText : ""
  onThemeTextChanged: HomaLawnTheme.Theme.load(themeText)
  Component.onCompleted: HomaLawnTheme.Theme.load(themeText)

  readonly property var homaStatus: Status.summary(phones, serviceUp, !!homalawn)
  readonly property bool anyConnected: homaStatus.connected
  readonly property bool problem: homaStatus.problem
  readonly property string tooltip: homaStatus.tooltip

  implicitWidth: slot.implicitWidth
  implicitHeight: slot.implicitHeight

  WidgetButton {
    id: slot
    anchors.fill: parent
    bar: mark.bar
    labelVisible: false
    hasVisualContent: true
    dimmed: !mark.serviceUp
    tooltipText: mark.tooltip
    fixedWidth: mark.vertical ? -1 : glyph.width + 2 * scaledHorizontalMargin
    fixedHeight: mark.vertical ? glyph.height + 2 * scaledVerticalPadding : -1
    onPressed: function (which) {
      if (which === Qt.LeftButton && mark.host && typeof mark.host.toggle === "function")
        mark.host.toggle("homalawn", "{}")
    }

    HomaLawnUi.Mark {
      id: glyph
      anchors.centerIn: parent
      size: 16
      outlineColor: mark.problem ? HomaLawnTheme.Theme.yellow
        : mark.anyConnected ? HomaLawnTheme.Theme.accent : HomaLawnTheme.Theme.muted
      appColor: mark.problem ? HomaLawnTheme.Theme.yellow
        : mark.anyConnected ? HomaLawnTheme.Theme.red : HomaLawnTheme.Theme.muted
    }
  }
}
