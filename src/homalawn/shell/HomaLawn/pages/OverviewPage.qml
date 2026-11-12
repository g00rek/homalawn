import QtQuick
import "../components"
import ".."
import "../Status.js" as Status

// Overview of the selected phone: who it is (paired date, connection, the address it answers at,
// the Homa Lawn app on it and whether a newer one is on offer, the last wallpaper & style
// applied), what it has allowed Homa Lawn to do, its addresses, and how to unpair it. The facts
// come from the service's phone.info, which the view keeps for the selected phone, and from the
// phone's state: `app_latest` ({version, newer, source}: Update app is offered only when `newer`)
// and `permissions` (home, background, notifications, screen_control, install_updates; null from
// an older phone app, which then reports only phone.info's home app and screen control).
//
// The addresses are a draft like every other page's settings: adding or removing one changes only
// the draft (kept per phone on the connection, backend.addressDrafts); the Apply bar at the bottom
// saves it with phone.addresses_set. Update app and Unpair ask first (the window's confirm bar).
Item {
  id: page

  // Given by HomaLawnView.
  property var backend: null
  property string phone: ""
  property string phoneName: ""
  property var phoneState: null
  property var host: null

  readonly property var info: host && host.info && host.info.id === phone ? host.info : null
  readonly property bool online: !!(phoneState && phoneState.connected)
  readonly property var busy: host ? host.busyEntry : null
  readonly property var app: info ? info.app : null
  // phone.info's permissions (older phone apps report only these): {default_home, screen_control, …}
  readonly property var perms: info ? info.permissions : null
  // The phone's own report (newer phone apps): {home, background, notifications, screen_control, install_updates}
  readonly property var permissions: phoneState && phoneState.permissions ? phoneState.permissions : null
  readonly property var latest: phoneState && phoneState.app_latest ? phoneState.app_latest : null
  readonly property bool updateOffered: online && !!latest && latest.newer === true
  // The wallpaper on the phone now was picked on the phone (its Wallpaper & style screen).
  readonly property bool pickedOnPhone: {
    var a = phoneState && phoneState.applied, look = phoneState && phoneState.look
    if (!a || !a.theme || !a.background || !look || !look.picks) return false
    var p = look.picks[Paths.slug(a.theme)]
    return !!p && p.by === "phone" && (p.home === a.background || p.lock === a.background)
  }
  property bool updating: false
  property bool askingUpdate: false
  // Unpair asks once, inline, before it does anything.
  property bool askingUnpair: false
  property bool unpairing: false

  // The addresses: what the service lists (phone.addresses: every address in dial order with its
  // kind), and the draft of the added ones, which only Apply saves.
  property var addrRows: []
  property bool addrRead: false
  property string addrError: ""
  property bool addrSaving: false
  readonly property var addrSaved: addrRows.filter(r => r.removable).sort((a, b) => a.saved - b.saved).map(r => r.address)
  readonly property var addrDraft: {
    var all = backend && backend.addressDrafts ? backend.addressDrafts : {}
    return all[phone] || addrSaved
  }
  readonly property bool addrChanged: JSON.stringify(addrDraft) !== JSON.stringify(addrSaved)
  readonly property int addrChanges: {
    if (!addrChanged) return 0
    var n = addrDraft.filter(a => addrSaved.indexOf(a) < 0).length + addrSaved.filter(a => addrDraft.indexOf(a) < 0).length
    return Math.max(1, n)
  }
  readonly property var addrOthers: addrRows.filter(r => !r.removable)
  readonly property string addrAnswered: { var r = addrRows.find(x => x.answered); return r ? r.address : "" }
  // An address without its port ("192.168.1.42:47100" -> "192.168.1.42"; "[fd00::1]:47100" -> "fd00::1").
  function hostOf(a) {
    var t = String(a || "")
    var v6 = t.match(/^\[([^\]]+)\](?::\d+)?$/)
    if (v6) return v6[1]
    return (t.match(/:/g) || []).length === 1 ? t.replace(/:\d+$/, "") : t
  }
  // How Homa Lawn came to know an address, in plain words.
  function kindWords(kind) {
    switch (String(kind || "")) {
    case "paired through": return "where it was paired"
    case "last LAN": return "home network"
    case "learned (Tailscale)": return "Tailscale"
    case "last answered": return "answered last"
    case "added": return "added by you"
    }
    return String(kind || "")
  }
  // The addresses Homa Lawn found by itself, once each, without ports: "192.168.1.42 (home network)".
  readonly property var addrFound: {
    var seen = {}, out = []
    addrOthers.forEach(r => {
      var h = hostOf(r.address)
      if (seen[h]) return
      seen[h] = true
      out.push(h + " (" + kindWords(r.kind) + ")")
    })
    return out
  }

  onPhoneChanged: { askingUnpair = false; askingUpdate = false; addrError = ""; loadAddresses() }
  Component.onCompleted: loadAddresses()
  Connections {
    target: page.backend
    function onConnectedChanged() { if (page.backend && page.backend.connected) page.loadAddresses() }
  }

  function loadAddresses() {
    if (!backend || !phone || !backend.connected) { addrRows = []; addrRead = false; return }
    var who = phone
    backend.call("phone.addresses", { phone: who }, function (err, result) {
      if (who !== page.phone) return
      page.addrError = err ? (err.message || err.code) : ""
      if (!err) { page.addrRows = result.addresses; page.addrRead = true; page.keepDraft(page.addrDraft) }
    })
  }
  // The draft stays on the connection while it differs from what is saved.
  function keepDraft(list) {
    if (!backend || !phone) return
    var all = Object.assign({}, backend.addressDrafts || {})
    if (addrRead && JSON.stringify(list) === JSON.stringify(addrSaved)) delete all[phone]
    else all[phone] = list.slice()
    backend.addressDrafts = all
  }
  // The text in the box joins the draft; the service says what is wrong with it when Apply is pressed.
  function addAddress(text) {
    var t = String(text).trim()
    if (t === "") return false
    addrError = ""
    if (addrDraft.indexOf(t) < 0) keepDraft(addrDraft.concat([t]))
    return true
  }
  function removeAddress(i) {
    var d = addrDraft.slice()
    d.splice(i, 1)
    keepDraft(d)
  }
  function discardAddresses() {
    addrError = ""
    keepDraft(addrSaved)
  }
  function applyAddresses() {
    if (!backend || !addrChanged || addrSaving) return false
    addrSaving = true
    var who = phone
    var sent = addrDraft.slice()
    backend.call("phone.addresses_set", { phone: who, addresses: sent }, function (err, result) {
      page.addrSaving = false
      if (who !== page.phone) return
      if (err) { page.addrError = err.message || err.code; return }
      page.addrError = ""
      page.addrRows = result.addresses
      page.keepDraft(page.addrSaved)
      if (host) host.showToast(result.message)
    })
    return true
  }

  // Unpair the selected phone (phone.unpair): the first press asks, the confirm does it. The
  // service tells the phone, forgets it here and says in its own words what happened (a toast).
  function unpairNow() {
    if (!backend || unpairing) return false
    if (!askingUnpair) { askingUnpair = true; return true }
    askingUnpair = false
    unpairing = true
    backend.call("phone.unpair", { phone: phone }, function (err, result) {
      page.unpairing = false
      if (host) host.showToast(err ? (err.message || err.code) : result.message)
    })
    return true
  }

  onHostChanged: if (host) host.loadInfo()

  readonly property string stateText: {
    if (!phoneState) return ""
    var base = online ? "Connected" : "Offline"
    return Status.problemOf(phoneState) ? base + " · needs attention" : base
  }

  // Send the newest Homa Lawn build on this computer to the phone (homalawn update), after the
  // confirm.
  function updateNow() {
    if (!backend || !online || updating || busy) return false
    askingUpdate = false
    updating = true
    backend.call("app.update", { phone: phone }, function (err, result) {
      page.updating = false
      if (host) {
        host.showToast(err ? (err.message || err.code) : (result && result.text ? result.text : "Updated."))
        host.loadInfo()
      }
    })
    return true
  }

  component Fact: Row {
    id: fact
    property string label: ""
    property string value: ""
    property color valueColor: Theme.foreground
    width: parent ? parent.width : 0
    spacing: 10
    Txt { id: factLabel; width: 190; text: fact.label; color: Theme.darkForeground; font.pixelSize: Theme.small }
    Txt {
      width: fact.width - factLabel.width - fact.spacing
      wrapMode: Text.Wrap
      text: fact.value
      color: fact.valueColor
      font.pixelSize: Theme.small
    }
  }

  // One permission: a state mark, the name, and below it what it is for or how to grant it.
  component Permission: Item {
    id: perm
    property string label: ""
    property var granted: null          // true, false, or null (not known)
    property string why: ""
    property string grant: ""
    property string unknownText: "Unknown"
    property bool optional: false      // the phone works without it
    width: parent ? parent.width : 0
    height: permCol.implicitHeight + 14
    Glyph {
      id: mark
      y: 2
      size: 15
      code: perm.granted === true ? Icons.check : (perm.granted === false ? Icons.close : 0)
      text: perm.granted === null ? "?" : String.fromCodePoint(code)
      color: perm.granted === true ? Theme.green
        : perm.granted === false ? (perm.optional ? Theme.darkForeground : Theme.red) : Theme.yellow
    }
    Column {
      id: permCol
      anchors.left: mark.right
      anchors.leftMargin: 10
      anchors.right: parent.right
      spacing: 3
      Row {
        spacing: 10
        Txt { id: permLabel; text: perm.label; color: Theme.brightForeground }
        Txt {
          visible: perm.optional
          anchors.baseline: permLabel.baseline
          text: "optional"
          font.pixelSize: Theme.tiny
          color: Theme.darkForeground
        }
        Txt {
          anchors.baseline: permLabel.baseline
          text: perm.granted === true ? "Allowed" : (perm.granted === false ? "Not allowed" : perm.unknownText)
          font.pixelSize: Theme.small
          color: perm.granted === true ? Theme.green
            : perm.granted === false ? (perm.optional ? Theme.foreground : Theme.red) : Theme.yellow
        }
      }
      Txt {
        width: parent.width
        text: perm.granted === true || perm.grant === "" ? perm.why : perm.grant
        visible: text !== ""
        wrapMode: Text.Wrap
        font.pixelSize: Theme.small
        color: Theme.darkForeground
      }
    }
  }

  ApplyBar {
    id: applyBar
    changes: page.addrChanges
    applying: page.addrSaving
    failed: page.addrError !== ""
    onApply: page.applyAddresses()
    onDiscard: page.discardAddresses()
  }

  Flickable {
    id: flick
    anchors.left: parent.left
    anchors.right: parent.right
    anchors.top: parent.top
    anchors.bottom: applyBar.top
    contentWidth: width
    contentHeight: cards.height + 2 * Theme.margin
    clip: true
    boundsBehavior: Flickable.StopAtBounds
    interactive: contentHeight > height

    Column {
      id: cards
      x: Theme.margin
      y: Theme.margin
      width: Math.min(flick.width - 2 * Theme.margin, 900)
      spacing: Theme.gap

      // ── The phone ──
      Card {
        Item {
          width: parent.width
          height: Math.max(who.implicitHeight, updateBox.implicitHeight)
          Glyph {
            id: big
            code: Icons.phone
            size: 38
            color: page.online ? Theme.accent : Theme.muted
          }
          Column {
            id: who
            anchors.left: big.right
            anchors.leftMargin: 18
            anchors.right: updateBox.left
            anchors.rightMargin: 16
            spacing: 6
            Txt {
              text: page.phoneName
              font.pixelSize: Theme.title
              font.bold: true
              color: Theme.brightForeground
            }
            Row {
              spacing: 8
              Rectangle {
                anchors.verticalCenter: parent.verticalCenter
                width: 8; height: 8; radius: 4
                color: page.host ? page.host.phoneDot(page.phoneState) : Theme.muted
              }
              Txt { text: page.stateText; font.pixelSize: Theme.small }
            }
            Item { width: 1; height: 4 }
            Fact { label: "Paired"; value: page.info && page.info.paired ? Theme.day(page.info.paired) : "Unknown" }
            Fact { label: "Address"; value: page.info && page.info.address ? page.hostOf(page.info.address) : "Unknown" }
            Fact {
              visible: page.online
              label: "Homa Lawn on the phone"
              value: page.app && page.app.versionName ? page.app.versionName
                : page.info ? (page.info.busy ? "Busy — ask again later" : "Not reported")
                : page.host && page.host.infoError ? "Could not ask: " + page.host.infoError
                : "Reading…"
            }
            Fact {
              visible: page.online && !!page.latest
              label: ""
              value: page.updateOffered ? "Update available: " + page.latest.version : "Up to date"
              valueColor: page.updateOffered ? Theme.accent : Theme.green
            }
            Fact {
              label: "Wallpaper & style"
              value: page.phoneState && page.phoneState.applied && page.phoneState.applied.theme
                ? page.phoneState.applied.theme
                  + (page.phoneState.applied.at ? " · " + Theme.when(page.phoneState.applied.at) : "")
                  + (page.pickedOnPhone ? " (chosen on the phone)" : "")
                : "Not applied yet"
            }
            Fact {
              visible: !!(page.phoneState && page.phoneState.error)
              label: "Last error"
              value: page.phoneState && page.phoneState.error ? String(page.phoneState.error) : ""
              valueColor: Theme.red
            }
          }
          Column {
            id: updateBox
            visible: page.updateOffered || page.updating || (!!page.busy && page.busy.what === "update")
            anchors.right: parent.right
            width: visible ? 200 : 0
            spacing: 6
            AccentButton {
              anchors.right: parent.right
              text: page.updating || (page.busy && page.busy.what === "update") ? "Updating…" : "Update app"
              active: !page.updating && !page.busy && !page.askingUpdate
              onClicked: page.askingUpdate = true
            }
            Txt {
              anchors.right: parent.right
              width: 200
              horizontalAlignment: Text.AlignRight
              wrapMode: Text.Wrap
              text: page.latest && page.latest.source === "checkout" ? "The build on this computer." : "The newest release."
              font.pixelSize: Theme.tiny
              color: Theme.darkForeground
            }
          }
        }
        ConfirmBar {
          visible: page.askingUpdate && page.online
          text: "Update Homa Lawn on " + page.phoneName + "?"
          detail: "Its home screen restarts."
          action: "Update"
          active: !page.busy
          onConfirmed: page.updateNow()
          onCancelled: page.askingUpdate = false
        }
      }

      // ── Permissions (read from the phone, so only while it is connected) ──
      Card {
        visible: page.online
        title: "Permissions"
        subtitle: "What " + page.phoneName + " has allowed Homa Lawn."
        spacing: 2
        // A newer phone app reports all five.
        Permission {
          visible: !!page.permissions
          label: "Home app"
          granted: page.permissions ? page.permissions.home : null
          why: "Your computer reaches the phone through the home screen."
        }
        Permission {
          visible: !!page.permissions
          label: "Run in background"
          granted: page.permissions ? page.permissions.background : null
          why: "Lets your computer reach the phone while it sleeps."
        }
        Permission {
          visible: !!page.permissions
          label: "Notifications"
          granted: page.permissions ? page.permissions.notifications : null
          why: "For pairing requests and updates that need a tap."
        }
        Permission {
          visible: !!page.permissions
          label: "Screen control"
          optional: true
          granted: page.permissions ? page.permissions.screen_control : null
          why: "Lets this computer or its agents see and operate the screen."
        }
        Permission {
          visible: !!page.permissions
          label: "Install updates"
          optional: true
          granted: page.permissions ? page.permissions.install_updates : null
          why: "Lets updates from your computer install."
        }
        Txt {
          visible: !!page.permissions
          width: parent.width
          topPadding: 6
          wrapMode: Text.Wrap
          font.pixelSize: Theme.small
          color: Theme.foreground
          text: "To change one: on the phone, open Homa Lawn's settings › Permissions."
        }
        // An older phone app: what phone.info says (home app and screen control only).
        Txt {
          visible: !page.permissions && !page.perms
          font.pixelSize: Theme.small
          color: Theme.darkForeground
          text: page.info ? "The phone did not report them." : "Reading…"
        }
        Permission {
          visible: !page.permissions && !!page.perms
          label: "Home app"
          granted: page.perms ? page.perms.default_home : null
          why: "Homa Lawn is the home screen, so layout and wallpaper & style changes show."
          grant: "On the phone: Settings › Apps › Default apps › Home app › Homa Lawn."
        }
        Permission {
          visible: !page.permissions && !!page.perms
          label: "Screen control"
          optional: true
          granted: page.perms ? page.perms.screen_control : null
          why: "Homa Lawn on this computer can read and operate the phone's screen (for agents)."
          grant: "On the phone: turn on Homa Lawn in Settings › Accessibility. If Android says the setting "
               + "is restricted: Settings › Apps › Homa Lawn › ⋮ › Allow restricted settings, then try again."
        }
        Txt {
          visible: !page.permissions && !!page.perms
          width: parent.width
          topPadding: 6
          wrapMode: Text.Wrap
          font.pixelSize: Theme.small
          color: Theme.darkForeground
          text: "This Homa Lawn version on the phone reports only these two."
        }
      }

      // ── Addresses ──
      Card {
        title: "Addresses"
        subtitle: "Where Homa Lawn looks for " + page.phoneName + ". Add its Tailscale name to reach it away from home."
        spacing: 8
        Repeater {
          model: page.addrDraft
          delegate: Row {
            spacing: 10
            Txt { anchors.verticalCenter: parent.verticalCenter; text: modelData; color: Theme.brightForeground }
            Txt {
              anchors.verticalCenter: parent.verticalCenter
              visible: page.addrSaved.indexOf(modelData) < 0
              text: "Not applied yet"
              font.pixelSize: Theme.tiny
              color: Theme.accent
            }
            OutlineButton {
              text: "Remove"
              onClicked: page.removeAddress(index)
            }
          }
        }
        Txt {
          visible: page.addrFound.length > 0
          width: parent.width
          wrapMode: Text.Wrap
          font.pixelSize: Theme.small
          color: Theme.foreground
          text: "Found by Homa Lawn: " + page.addrFound.join(", ") + "."
        }
        Txt {
          visible: page.addrRows.length === 0 && page.addrDraft.length === 0
          font.pixelSize: Theme.small
          color: Theme.darkForeground
          text: page.phoneName + " has no addresses yet."
        }
        Row {
          spacing: 8
          Rectangle {
            width: 260
            height: addrInput.implicitHeight + 12
            radius: 4
            color: Theme.darkerBackground
            border.width: 1
            border.color: addrInput.activeFocus ? Theme.accent : Theme.muted
            TextInput {
              id: addrInput
              objectName: "addressInput"
              anchors.left: parent.left
              anchors.right: parent.right
              anchors.verticalCenter: parent.verticalCenter
              anchors.margins: 8
              clip: true
              color: Theme.brightForeground
              font.family: Theme.font
              font.pixelSize: Theme.small
              selectByMouse: true
              onAccepted: if (page.addAddress(text)) text = ""
              Txt {
                visible: addrInput.text === "" && !addrInput.activeFocus
                text: "Name or address"
                font.pixelSize: Theme.small
                color: Theme.darkForeground
              }
            }
          }
          OutlineButton {
            text: "Add"
            active: addrInput.text.trim() !== ""
            onClicked: if (page.addAddress(addrInput.text)) addrInput.text = ""
          }
        }
        Txt {
          visible: page.addrError !== ""
          width: parent.width
          wrapMode: Text.Wrap
          font.pixelSize: Theme.small
          color: Theme.red
          text: page.addrError
        }
        // Every address as the service keeps it: with its port, how it was learned, in dial order.
        Details {
          visible: page.addrRows.length > 0
          Repeater {
            model: page.addrRows
            delegate: Txt {
              text: modelData.address + "  · " + modelData.kind + (modelData.answered ? " · answered last" : "")
              font.pixelSize: Theme.small
              color: Theme.darkForeground
            }
          }
        }
      }

      // ── Unpair ──
      Card {
        Item {
          visible: !page.askingUnpair
          width: parent.width
          height: visible ? Math.max(unpairText.implicitHeight, unpairButton.implicitHeight) : 0
          Column {
            id: unpairText
            anchors.left: parent.left
            anchors.right: unpairButton.left
            anchors.rightMargin: 16
            spacing: 4
            Heading { text: "Unpair " + page.phoneName }
            Txt {
              width: parent.width
              wrapMode: Text.Wrap
              font.pixelSize: Theme.small
              color: Theme.darkForeground
              text: "It forgets this computer too. You can pair it again later."
            }
          }
          OutlineButton {
            id: unpairButton
            anchors.right: parent.right
            anchors.verticalCenter: parent.verticalCenter
            danger: true
            text: page.unpairing ? "Unpairing…" : "Unpair"
            active: !page.unpairing
            onClicked: page.unpairNow()
          }
        }
        ConfirmBar {
          visible: page.askingUnpair
          text: "Unpair " + page.phoneName + "?"
          detail: "It forgets this computer too. You can pair it again later."
          action: "Unpair"
          destructive: true
          working: page.unpairing
          onConfirmed: page.unpairNow()
          onCancelled: page.askingUnpair = false
        }
      }
    }
  }
}
