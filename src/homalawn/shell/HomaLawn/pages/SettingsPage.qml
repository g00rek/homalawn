import QtQuick
import "../components"
import ".."

// The Service page (key "settings"): the Homa Lawn service in plain words (running since when,
// version, phones) with Restart behind the window's confirm step, its socket and build under
// Details. (The coding agents are on the Agents page.) While the service is not running the view
// shows its own block instead.
Item {
  id: page

  // Given by HomaLawnView.
  property var backend: null
  property string phone: ""
  property var phoneState: null
  property var host: null

  property var service: null
  property bool restarting: false
  property bool askingRestart: false
  readonly property bool up: !!backend && backend.connected
  readonly property var presence: ({ here: true })
  Component.onDestruction: presence.here = false

  function load() {
    if (!up) {
      service = null
      return
    }
    var presence = page.presence
    backend.call("service.info", {}, function (err, result) {
      if (presence.here) page.service = err ? null : result
    })
  }

  onUpChanged: load()
  onBackendChanged: load()

  function restartNow() {
    if (!backend || restarting || !backend.ownService) return false
    askingRestart = false
    restarting = true
    backend.restartService(function (ok, why) {
      page.restarting = false
      if (page.host) page.host.showToast(ok ? "Homa Lawn service restarted." : why)
    })
    return true
  }

  readonly property int phoneCount: page.host ? page.host.phones.length : 0
  readonly property int onlineCount: page.host ? page.host.phones.filter(p => p.connected).length : 0

  component Fact: Row {
    property string label: ""
    property string value: ""
    property color valueColor: Theme.foreground
    spacing: 10
    Txt { width: 120; text: parent.label; color: Theme.darkForeground; font.pixelSize: Theme.small }
    Txt { text: parent.value; color: parent.valueColor; font.pixelSize: Theme.small }
  }

  Flickable {
    id: flick
    anchors.fill: parent
    contentWidth: width
    contentHeight: content.height + 2 * Theme.margin
    clip: true
    boundsBehavior: Flickable.StopAtBounds
    interactive: contentHeight > height

    Column {
      id: content
      x: Theme.margin
      y: Theme.margin
      width: Math.min(flick.width - 2 * Theme.margin, 900)
      spacing: Theme.gap

      Card {
        title: "Homa Lawn service"
        subtitle: "Runs in the background: keeps your phones connected and follows the Omarchy theme, also with this window closed."
        Item {
          width: parent.width
          height: Math.max(stateCol.implicitHeight, restartButton.implicitHeight)
          Column {
            id: stateCol
            anchors.left: parent.left
            anchors.right: restartButton.left
            anchors.rightMargin: 16
            spacing: 4
            Row {
              spacing: 8
              Rectangle {
                anchors.verticalCenter: parent.verticalCenter
                width: 8; height: 8; radius: 4
                color: page.up ? Theme.green : Theme.yellow
              }
              Txt {
                text: !page.up ? "Not running"
                  : page.service && page.service.started ? "Running since " + Theme.when(page.service.started) : "Running"
                color: Theme.brightForeground
              }
            }
            Txt {
              text: (page.service ? "Version " + String(page.service.version).split("+")[0] + " · " : "")
                + (page.phoneCount === 0 ? "No phone paired"
                   : page.phoneCount + (page.phoneCount === 1 ? " phone paired" : " phones paired")
                     + ", " + page.onlineCount + " connected")
              font.pixelSize: Theme.small
              color: Theme.darkForeground
            }
          }
          OutlineButton {
            id: restartButton
            visible: !!page.backend && page.backend.ownService && !page.askingRestart
            anchors.right: parent.right
            anchors.verticalCenter: parent.verticalCenter
            text: page.restarting ? "Restarting…" : "Restart"
            active: page.up && !page.restarting
            onClicked: page.askingRestart = true
          }
        }
        ConfirmBar {
          visible: page.askingRestart
          text: "Restart the Homa Lawn service?"
          detail: "Phones reconnect within a few seconds; an Apply on its way is stopped."
          action: "Restart"
          working: page.restarting
          onConfirmed: page.restartNow()
          onCancelled: page.askingRestart = false
        }
        Txt {
          visible: !!page.backend && !page.backend.ownService
          width: parent.width
          wrapMode: Text.Wrap
          text: "This window talks to a service started by hand (HOMALAWN_SOCKET); restart it where it runs."
          font.pixelSize: Theme.small
          color: Theme.darkForeground
        }
        Details {
          Fact { label: "Build"; value: page.service ? String(page.service.version) : "—" }
          Fact { label: "Socket"; value: page.service ? page.service.socket : (page.backend ? page.backend.socketPath : "—") }
          Fact { label: "Started"; value: page.service ? Theme.when(page.service.started) : "—" }
        }
      }
    }
  }
}
