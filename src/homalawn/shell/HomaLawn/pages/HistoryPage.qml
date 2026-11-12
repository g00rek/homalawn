import QtQuick
import "../components"
import ".."

// The selected phone's History, newest first: Homa Lawn saves a snapshot before every change it
// makes. Each row: when, which section it came from, and the service's `title` for it (the
// snapshot's name from a service that sends no title). "Undo last change" puts back the newest;
// "Restore" on a row puts back that one after the window's confirm step (the state before it is
// saved first, so a restore can be undone too). Offline, the list stays readable and the buttons
// go (the view says why in one line).
Item {
  id: page

  // Given by HomaLawnView.
  property var backend: null
  property string phone: ""
  property string phoneName: ""
  property var phoneState: null
  property var host: null

  property var rows: []
  property bool loading: false
  property string loadError: ""
  property string confirming: ""     // the row waiting for its confirm
  property string restoring: ""      // the row being restored
  property int asks: 0

  readonly property bool online: !!(phoneState && phoneState.connected)
  readonly property var busy: host ? host.busyEntry : null

  function load() {
    if (!backend || !phone) return
    var who = phone
    asks += 1
    var ask = asks
    loading = true
    backend.call("snapshots", { phone: who }, function (err, result) {
      if (ask !== page.asks) return
      page.loading = false
      page.loadError = err ? (err.message || err.code) : ""
      page.rows = !err && result && Array.isArray(result.data) ? result.data : []
    })
  }

  function kindLabel(kind) { return host ? host.kindLabel(kind) : String(kind || "") }
  // The row's words beside its kind: the service's title without the kind it starts with
  // ("Home screen: 3 changes" under "Home screen" reads "3 changes"); the snapshot's name from a
  // service that sends no title.
  function rowText(row) {
    if (!row.title) return String(row.name)
    var t = String(row.title), k = kindLabel(row.kind)
    if (t === k) return ""
    return t.indexOf(k + ": ") === 0 ? t.slice(k.length + 2) : t
  }

  // Restore one snapshot by name; the answer is a toast, and the list is read again.
  function restore(name) {
    if (!backend || !online || restoring || busy) return false
    confirming = ""
    restoring = name
    backend.call("snapshot.restore", { phone: phone, name: name }, function (err, result) {
      page.restoring = ""
      if (page.host) page.host.showToast(err ? (err.message || err.code) : (result && result.text ? result.text : "Restored."))
      page.load()
    })
    return true
  }

  onPhoneChanged: { confirming = ""; load() }
  onBackendChanged: load()
  Connections {
    target: page.host
    function onUndone() { page.load() }
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

      Item {
        width: parent.width
        height: Math.max(intro.implicitHeight, undoButton.implicitHeight)
        Txt {
          id: intro
          anchors.left: parent.left
          anchors.right: undoButton.visible ? undoButton.left : parent.right
          anchors.rightMargin: 16
          anchors.verticalCenter: parent.verticalCenter
          wrapMode: Text.Wrap
          font.pixelSize: Theme.small
          color: Theme.darkForeground
          text: "Homa Lawn saves the phone's state before every change it makes. Restore puts one back; the state before it is saved first."
        }
        OutlineButton {
          id: undoButton
          visible: page.online
          anchors.right: parent.right
          anchors.verticalCenter: parent.verticalCenter
          text: page.host && page.host.undoing ? "Undoing…" : "Undo last change"
          active: !page.busy && !page.restoring && !!page.host && !page.host.undoing && page.rows.length > 0
          tip: page.rows.length ? "Undo: " + page.host.changeLabel(page.rows[0]) : ""
          onClicked: page.host.undoNow()
        }
      }

      Card {
        spacing: 0
        Txt {
          visible: page.rows.length === 0
          width: parent.width
          wrapMode: Text.Wrap
          text: page.loading ? "Reading the history…"
                : (page.loadError ? "Could not read the history: " + page.loadError : "Nothing in the history of " + page.phoneName + " yet.")
          color: page.loadError ? Theme.red : Theme.darkForeground
          font.pixelSize: Theme.small
          bottomPadding: 4
        }

        Repeater {
          model: page.rows
          Item {
            id: row
            required property var modelData
            required property int index
            readonly property bool asking: page.confirming === modelData.name
            width: parent.width
            height: asking ? ask.height + 16 : 46

            Rectangle {
              visible: row.index > 0
              width: parent.width
              height: 1
              color: Theme.selection
            }
            Txt {
              id: stamp
              visible: !row.asking
              width: 150
              anchors.verticalCenter: parent.verticalCenter
              text: Theme.when(row.modelData.time) || "—"
              color: Theme.brightForeground
              font.pixelSize: Theme.small
            }
            Rectangle {
              id: kindPill
              visible: !row.asking
              anchors.left: stamp.right
              anchors.verticalCenter: parent.verticalCenter
              width: 150
              height: 22
              radius: 11
              color: Theme.lighterBackground
              border.width: 1
              border.color: Theme.muted
              Txt {
                anchors.centerIn: parent
                width: parent.width - 12
                horizontalAlignment: Text.AlignHCenter
                elide: Text.ElideRight
                text: page.kindLabel(row.modelData.kind)
                font.pixelSize: Theme.tiny
              }
            }
            Txt {
              visible: !row.asking
              anchors.left: kindPill.right
              anchors.leftMargin: 14
              anchors.right: restoreButton.left
              anchors.rightMargin: 12
              anchors.verticalCenter: parent.verticalCenter
              elide: Text.ElideRight
              text: page.rowText(row.modelData)
              color: row.modelData.title ? Theme.foreground : Theme.darkForeground
              font.pixelSize: Theme.small
            }
            OutlineButton {
              id: restoreButton
              visible: !row.asking && page.online
              anchors.right: parent.right
              anchors.verticalCenter: parent.verticalCenter
              text: page.restoring === row.modelData.name ? "Restoring…" : "Restore"
              active: !page.busy && !page.restoring
              onClicked: page.confirming = row.modelData.name
            }
            ConfirmBar {
              id: ask
              visible: row.asking
              y: 8
              text: "Restore " + page.phoneName + " to this point?"
              detail: (Theme.when(row.modelData.time) ? Theme.when(row.modelData.time) + " · " : "")
                + page.kindLabel(row.modelData.kind) + ". The state before it is saved first, so Undo can bring it back."
              action: "Restore"
              active: page.online && !page.busy && !page.restoring
              onConfirmed: page.restore(row.modelData.name)
              onCancelled: page.confirming = ""
            }
          }
        }
      }
    }
  }
}
