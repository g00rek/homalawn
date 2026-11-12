import QtQuick
import Quickshell
import "HomaLawn"

// The Homa Lawn window. The shell keeps this object alive (keepLoaded) and calls open() / close();
// the view inside is built only while the window is on screen.
Item {
  id: panel

  // Given by the shell.
  property var shell: null
  property var service: null
  property var manifest: null

  // The shell reads this to decide what `toggle` does.
  readonly property bool opened: window.visible
  // The Homa Lawn service object, looked up again on every open in case it loaded after us.
  property var homalawn: null
  readonly property var view: viewLoader.item
  // The window itself (the test harness resizes it).
  readonly property alias window: window

  // What the person last chose, kept across openings while the view itself is thrown away.
  property string lastSection: "overview"
  property string lastPhone: ""
  property bool lastRailOpen: true
  // Every page a summon payload may name; "service" is the Service page's other name ("settings").
  readonly property var sectionKeys: ["overview", "home", "look", "search", "history", "add", "agents", "settings", "service"]

  property bool hiding: false

  function findService() {
    if (service) return service
    if (shell && typeof shell.serviceFor === "function") return shell.serviceFor("homalawn")
    return null
  }

  function requestedPage(payloadJson) {
    if (!payloadJson) return ""
    try {
      var payload = JSON.parse(String(payloadJson))
      var page = payload && payload.page
      if (sectionKeys.indexOf(page) < 0) return ""
      return page === "service" ? "settings" : page
    } catch (e) {
      return ""
    }
  }

  function open(payloadJson) {
    homalawn = findService()
    if (homalawn && typeof homalawn.refresh === "function") homalawn.refresh()
    var page = requestedPage(payloadJson)
    if (page) {
      lastSection = page
      if (view) view.showPage(page)
    }
    window.visible = true
  }

  // Escape: first closes what is open over the window (the phone picker's list), else the window.
  function pressEscape() {
    if (view && view.dismiss()) return "list"
    window.visible = false
    return "window"
  }

  // The shell is hiding us; it already knows, so do not tell it back.
  function close() {
    hiding = true
    window.visible = false
    hiding = false
  }

  FloatingWindow {
    id: window
    title: "Homa Lawn"
    visible: false
    implicitWidth: 1180
    implicitHeight: 760
    minimumSize: Qt.size(900, 640)
    color: Theme.background

    // Closed by the person (Escape, the compositor's close): let the shell forget we are open.
    onVisibleChanged: {
      if (!visible && !panel.hiding && panel.shell && typeof panel.shell.hide === "function")
        panel.shell.hide("homalawn")
    }

    Shortcut {
      sequence: "Escape"
      onActivated: panel.pressEscape()
    }

    Loader {
      id: viewLoader
      anchors.fill: parent
      active: window.visible
      sourceComponent: HomaLawnView {
        backend: panel.homalawn ? panel.homalawn.backend : null
        themeText: panel.homalawn ? panel.homalawn.themeText : ""
        // Start from the remembered choice; the view takes over once the person clicks.
        section: panel.lastSection
        phone: panel.lastPhone
        railOpen: panel.lastRailOpen
        onSectionChanged: panel.lastSection = section
        onPhoneChanged: if (phone) panel.lastPhone = phone
        onRailOpenChanged: panel.lastRailOpen = railOpen
      }
    }
  }
}
