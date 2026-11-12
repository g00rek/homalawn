import QtQuick
import "components"
import "Status.js" as Status

// The Homa Lawn window's content: a rail on the left, a page header ("<Section>  <phone>") and the
// selected page below it.
//
// The rail, top to bottom: the phone picker (one button naming the phone the pages work on, with
// its status; a click lists the paired phones and "Add phone…"), a divider, that phone's sections
// (Overview … Agents), and at the bottom the global Service page. It opens labelled and pushes the
// content; ☰ collapses it to icons (tooltips; the picker shows the phone's initial). A window too
// narrow for both keeps it collapsed, and ☰ then lays the labelled rail over the content until a
// pick. A section holding changes not applied yet carries an accent dot.
//
// States the view owns for every page: the service not running (one block, one Start), a phone
// offline (one line; a page that needs the phone shows only that line), and toasts (dropped when
// the section or phone changes). The view holds no connection of its own; the host passes
// `backend` (see Backend.qml) and the text of the active Omarchy colors.toml in `themeText`.
Rectangle {
  id: view

  property var backend: null
  property string themeText: ""
  // The phone the sections work on, by its id (fingerprint). Kept while that phone exists; otherwise the first
  // connected phone, otherwise the first one.
  property string phone: ""
  // The page shown: one of the keys in `sections`.
  property string section: "overview"
  // The person's choice: the rail labelled (true) or collapsed to icons.
  property bool railOpen: true
  // A narrow window: the labelled rail laid over the content for now (☰), closed by a pick.
  property bool railPeek: false
  readonly property bool railRoomy: width >= 1100
  readonly property bool railWide: railOpen && railRoomy
  readonly property bool railExpanded: railWide || railPeek
  readonly property int railCollapsed: 64
  readonly property int railLabelled: 224

  readonly property bool serviceUp: !!backend && backend.connected
  readonly property bool serviceDown: !!backend && backend.attempted && !backend.connected
  readonly property var phones: serviceUp ? (backend.phones || []) : []
  readonly property var phoneState: phones.find(p => p.id === view.phone) || null
  readonly property var busyEntry: serviceUp
    ? ((backend.busy || []).find(b => b.phone === view.phone) || null) : null
  readonly property bool phoneOnline: !!phoneState && !!phoneState.connected

  readonly property var phoneSections: [
    { key: "overview", label: "Overview", code: Icons.overview },
    { key: "home", label: "Home screen", code: Icons.home },
    { key: "look", label: "Wallpaper & style", code: Icons.look },
    { key: "search", label: "Search & gestures", code: Icons.search },
    { key: "history", label: "History", code: Icons.history },
    { key: "agents", label: "Agents", code: Icons.agents }
  ]
  // The Service page keeps the key "settings" (summon payloads, the test harness); "service" is
  // taken as the same page.
  readonly property var bottomSections: [
    { key: "settings", label: "Service", code: Icons.settings }
  ]
  readonly property var sections: phoneSections.concat(bottomSections).concat([{ key: "add", label: "Add phone" }])
  readonly property var aliases: ({ service: "settings" })
  function sectionOf(key) { return sections.find(s => s.key === key) || null }
  function isPhoneSection(key) { return phoneSections.some(s => s.key === key) }

  // What the body actually shows: a phone section needs a phone; without one, Add phone (the
  // window's first screen then). Agents says so itself, with its coding agents beside it.
  readonly property string shown: {
    if (isPhoneSection(section) && section !== "agents" && serviceUp && phones.length === 0) return "add"
    return section
  }

  // Pages that cannot show anything without the phone (they read it live); the others work on
  // what the service keeps and say in one line that the phone is offline.
  readonly property var needsPhone: ["home", "search"]
  readonly property bool offlineHere: serviceUp && !!phoneState && !phoneOnline && isPhoneSection(shown)
    && shown !== "agents"
  readonly property string offlineText: {
    if (!phoneState) return ""
    var who = phoneState.name + " is offline. Homa Lawn keeps trying; "
    if (needsPhone.indexOf(shown) >= 0) return who + "this page needs the phone connected."
    if (shown === "history") return who + "restoring needs the phone connected."
    return who + "changes apply when it is back."
  }

  // Changes not applied yet, per section, for the selected phone (the pages keep their drafts on
  // the connection).
  function draftIn(key) {
    if (!backend || !phone) return false
    var all = key === "look" ? backend.lookDrafts : key === "home" ? backend.homeDrafts
      : key === "search" ? backend.searchDrafts : key === "overview" ? backend.addressDrafts : null
    return !!all && !!all[phone]
  }

  // phone.info of the selected phone (address, paired date, app version, permissions).
  property var info: null
  property string infoError: ""
  property int infoAsks: 0

  // The Omarchy themes (themes.list), read once while the window is open; the Look page asks.
  property var themes: []
  property bool themesAsked: false
  readonly property var lookPage: shown === "look" ? pageLoader.item : null
  readonly property var page: pageLoader.item

  function loadThemes() {
    if (themesAsked || !serviceUp) return
    themesAsked = true
    backend.call("themes.list", {}, function (err, result) {
      if (err) {
        view.themesAsked = false
        toast.show("Could not read the Omarchy themes: " + (err.message || err.code))
        return
      }
      view.themes = Array.isArray(result) ? result : []
    })
  }

  // Ask the service about the selected phone again (a reply for another phone is dropped).
  function loadInfo() {
    if (!serviceUp || !phone) {
      info = null
      infoError = ""
      return
    }
    var who = phone
    infoAsks += 1
    var ask = infoAsks
    backend.call("phone.info", { phone: who }, function (err, result) {
      if (ask !== view.infoAsks || who !== view.phone) return
      view.info = err ? null : result
      view.infoError = err ? (err.message || err.code) : ""
    })
  }

  // ── History words, shared by History, Agents and Undo ───────────────────────────────────
  // A snapshot's kind (the service names the window section it came from) in the section's words.
  readonly property var kindNames: ({
    home: "Home screen", search: "Search & gestures", style: "Wallpaper & style", wallpaper: "Wallpaper",
    manual: "Saved by hand", before_restore: "Before a restore", other: "Settings"
  })
  function kindLabel(kind) {
    var k = String(kind || "other")
    return kindNames[k] || (k.charAt(0).toUpperCase() + k.slice(1)).replace(/_/g, " ")
  }
  // What a snapshot holds, in words: the service's `title` when it sends one; else the kind and
  // when it was made.
  function changeLabel(row) {
    if (!row) return ""
    if (row.title) return String(row.title)
    return kindLabel(row.kind) + (row.time ? ", " + Theme.when(row.time) : "")
  }

  // The newest snapshot of the selected phone: what Undo would put back.
  property var lastChange: null
  property int lastAsks: 0
  function loadLastChange() {
    if (!serviceUp || !phone) { lastChange = null; return }
    var who = phone
    lastAsks += 1
    var ask = lastAsks
    backend.call("snapshots", { phone: who }, function (err, result) {
      if (ask !== view.lastAsks || who !== view.phone) return
      var rows = !err && result && Array.isArray(result.data) ? result.data : []
      view.lastChange = rows.length ? rows[0] : null
    })
  }

  // Select a section by key; false for an unknown key. Used by the panel's summon payload.
  function showPage(key) {
    var k = aliases[key] || key
    if (!sectionOf(k)) return false
    section = k
    return true
  }

  function choosePhone() {
    if (phones.length === 0) return
    if (phones.some(p => p.id === phone)) return
    var live = phones.find(p => p.connected)
    phone = (live || phones[0]).id
  }

  // The picker's choice: that phone, on the page shown if it is a phone's page, else on Overview.
  function pickPhone(id) {
    if (!phones.some(p => p.id === id)) return false
    phone = id
    if (!isPhoneSection(section)) section = "overview"
    railPeek = false
    return true
  }

  // The picker's rows, one per paired phone.
  readonly property var phoneRows: phones.map(p => ({ id: p.id, name: p.name, status: phoneStateText(p),
                                                       brief: phoneStateBrief(p), dot: phoneDot(p) }))
  readonly property alias phonePicker: phonePicker

  // Escape: close what is open over the window (the picker's list); false when nothing was.
  function dismiss() {
    if (!phonePicker.open) return false
    phonePicker.open = false
    return true
  }

  function phoneStateText(p) {
    if (!p) return ""
    var why = Status.problemOf(p)
    if (why) return "needs attention: " + why
    return p.connected ? "connected" : "offline"
  }

  // The same in a word or two, for the picker's button (what needs attention is in its list).
  function phoneStateBrief(p) {
    if (!p) return ""
    return Status.problemOf(p) ? "needs attention" : (p.connected ? "connected" : "offline")
  }

  // The phone's dot: green connected, grey offline, the warning colour while it needs attention
  // (as the bar mark: Status.js) or waits.
  function phoneDot(p) {
    if (!p) return Theme.muted
    if (Status.problemOf(p) || (p.connected && p.waiting)) return Theme.yellow
    return p.connected ? Theme.green : Theme.muted
  }

  // Undo the newest change on the selected phone (the service's undo); the toast says what it
  // put back.
  property bool undoing: false
  signal undone()
  function undoNow() {
    if (!serviceUp || !phoneState || !phoneState.connected || undoing || busyEntry) return false
    undoing = true
    var what = changeLabel(lastChange)
    backend.call("undo", { phone: phone }, function (err, result) {
      view.undoing = false
      var notes = result && result.notes && result.notes.length ? " " + result.notes.join(" ") : ""
      toast.show(err ? (err.message || err.code)
                 : (what ? "Undone: " + what + "." : (result && result.text ? result.text : "Undone.")) + notes)
      view.undone()
      view.loadLastChange()
    })
    return true
  }

  function busyText(what) {
    if (what === "look") return "Applying wallpaper & style…"
    if (what === "home") return "Applying the home screen…"
    if (what === "search") return "Applying search & gestures…"
    if (what === "undo") return "Undoing…"
    if (what === "restore") return "Restoring…"
    if (what === "update") return "Updating Homa Lawn on the phone…"
    return what + "…"
  }

  function showToast(text) { toast.show(text) }

  onPhonesChanged: choosePhone()
  onPhoneChanged: { toast.clear(); loadInfo(); loadLastChange() }
  onShownChanged: { toast.clear(); railPeek = false; phonePicker.open = false }
  onServiceUpChanged: { loadInfo(); loadLastChange() }
  onPhoneOnlineChanged: { loadInfo(); loadLastChange() }
  onBusyEntryChanged: if (!busyEntry) loadLastChange()
  onRailRoomyChanged: if (railRoomy) railPeek = false
  onThemeTextChanged: Theme.load(themeText)
  Component.onCompleted: {
    Theme.load(themeText)
    choosePhone()
    loadInfo()
    loadLastChange()
  }

  color: Theme.background

  Connections {
    target: view.backend
    function onToast(text) { toast.show(text) }
    // A pairing finished: the new phone is already in the list, so select it.
    function onPairStateChanged() {
      var p = view.backend ? view.backend.pairState : null
      if (!p || p.stage !== "done" || !p.phone) return
      view.phone = p.phone
      var name = (view.phones.find(x => x.id === p.phone) || {}).name
      toast.show("Paired " + (name || "the phone") + ".")
    }
  }

  // ── Page header ─────────────────────────────────────────────────────────────────────────
  Rectangle {
    id: header
    anchors.left: rail.right
    anchors.right: parent.right
    anchors.top: parent.top
    height: 56
    color: Theme.background

    Rectangle {
      anchors.left: parent.left
      anchors.right: parent.right
      anchors.bottom: parent.bottom
      height: 1
      color: Theme.selection
    }

    Row {
      anchors.left: parent.left
      anchors.leftMargin: Theme.margin
      anchors.right: actions.left
      anchors.rightMargin: 16
      anchors.verticalCenter: parent.verticalCenter
      spacing: 12
      clip: true
      Txt {
        id: title
        anchors.verticalCenter: parent.verticalCenter
        text: {
          var s = view.sectionOf(view.shown)
          return s ? s.label : ""
        }
        color: Theme.brightForeground
        font.pixelSize: Theme.title
        font.bold: true
      }
      Txt {
        anchors.baseline: title.baseline
        visible: view.isPhoneSection(view.shown) && !!view.phoneState
        text: view.phoneState ? view.phoneState.name : ""
        color: Theme.darkForeground
        font.pixelSize: Theme.small
      }
    }

    Row {
      id: actions
      anchors.right: parent.right
      anchors.rightMargin: Theme.margin
      anchors.verticalCenter: parent.verticalCenter
      spacing: 14
      Txt {
        anchors.verticalCenter: parent.verticalCenter
        visible: !!view.busyEntry && view.isPhoneSection(view.shown)
        text: view.busyEntry ? view.busyText(view.busyEntry.what) : ""
        color: Theme.yellow
        font.pixelSize: Theme.small
      }
      // Home screen, Wallpaper & style and Search & gestures: put the phone back as it was before
      // the last change (one click; the tooltip and the toast say what that was).
      OutlineButton {
        id: undoButton
        anchors.verticalCenter: parent.verticalCenter
        visible: (view.shown === "look" || view.shown === "home" || view.shown === "search") && view.phoneOnline
        text: view.undoing ? "Undoing…" : "Undo"
        active: !view.undoing && !view.busyEntry && !!view.lastChange
        tip: view.lastChange ? "Undo: " + view.changeLabel(view.lastChange) : "Nothing to undo yet"
        onClicked: view.undoNow()
      }
    }
  }

  // ── Body ────────────────────────────────────────────────────────────────────────────────
  Item {
    id: body
    anchors.left: rail.right
    anchors.right: parent.right
    anchors.top: header.bottom
    anchors.bottom: parent.bottom

    ServiceDown {
      anchors.centerIn: parent
      visible: view.serviceDown
      backend: view.backend
      onSaid: text => toast.show(text)
    }

    // The phone is offline: one line; a page that needs the phone shows nothing else.
    StatusLine {
      id: offlineLine
      visible: view.offlineHere
      anchors.top: parent.top
      width: parent.width
      tone: Theme.yellow
      text: view.offlineText
    }

    // The page. Loaded only while shown, so a hidden page sends nothing to the service.
    Loader {
      id: pageLoader
      anchors.left: parent.left
      anchors.right: parent.right
      anchors.top: offlineLine.bottom
      anchors.bottom: parent.bottom
      readonly property string want: {
        if (!view.serviceUp) return ""
        if (view.offlineHere && view.needsPhone.indexOf(view.shown) >= 0) return ""
        return view.shown
      }
      active: want !== ""
      source: {
        switch (want) {
        case "overview": return Qt.resolvedUrl("pages/OverviewPage.qml")
        case "look": return Qt.resolvedUrl("pages/LookPage.qml")
        case "history": return Qt.resolvedUrl("pages/HistoryPage.qml")
        case "add": return Qt.resolvedUrl("pages/AddPhonePage.qml")
        case "settings": return Qt.resolvedUrl("pages/SettingsPage.qml")
        case "home": return Qt.resolvedUrl("pages/HomePage.qml")
        case "search": return Qt.resolvedUrl("pages/SearchPage.qml")
        case "agents": return Qt.resolvedUrl("pages/AgentsPage.qml")
        }
        return ""
      }
      onLoaded: {
        if (item.hasOwnProperty("kind")) item.kind = want
        item.backend = Qt.binding(() => view.backend)
        item.phone = Qt.binding(() => view.phone)
        if (item.hasOwnProperty("phoneName")) item.phoneName = Qt.binding(() => view.phoneState ? view.phoneState.name : "")
        item.phoneState = Qt.binding(() => view.phoneState)
        item.host = view
      }
    }
  }

  // ── Rail ────────────────────────────────────────────────────────────────────────────────
  // Over the content only while peeking in a narrow window.
  Rectangle {
    id: scrim
    anchors.fill: parent
    anchors.leftMargin: view.railCollapsed
    color: Theme.withAlpha(Theme.darkerBackground, 0.45)
    visible: view.railPeek
    z: 9
    MouseArea {
      anchors.fill: parent
      onClicked: view.railPeek = false
    }
  }

  // Holds the content's left edge: the rail's width while it pushes, the icons' width otherwise.
  Item {
    id: rail
    anchors.left: parent.left
    anchors.top: parent.top
    anchors.bottom: parent.bottom
    width: view.railWide ? view.railLabelled : view.railCollapsed
  }

  Rectangle {
    id: railFace
    anchors.left: parent.left
    anchors.top: parent.top
    anchors.bottom: parent.bottom
    width: view.railExpanded ? view.railLabelled : view.railCollapsed
    color: Theme.darkBackground
    z: 10

    Rectangle {
      anchors.right: parent.right
      anchors.top: parent.top
      anchors.bottom: parent.bottom
      width: 1
      color: Theme.selection
    }

    Column {
      anchors.left: parent.left
      anchors.right: parent.right
      anchors.top: parent.top
      anchors.topMargin: 8
      spacing: 2

      Item {
        width: parent.width
        height: 48
        RailButton {
          anchors.verticalCenter: parent.verticalCenter
          code: Icons.menu
          label: ""
          tip: view.railExpanded ? "" : "Show labels"
          expanded: false
          onClicked: {
            if (view.railRoomy) view.railOpen = !view.railOpen
            else view.railPeek = !view.railPeek
          }
        }
        Row {
          visible: view.railExpanded
          x: 64
          anchors.verticalCenter: parent.verticalCenter
          spacing: 8
          Image {
            anchors.verticalCenter: parent.verticalCenter
            source: "assets/homalawn-icon.png"
            height: 20
            width: height
            sourceSize: Qt.size(Math.round(width * Screen.devicePixelRatio), Math.round(height * Screen.devicePixelRatio))
            fillMode: Image.PreserveAspectFit
            smooth: true
          }
          Txt {
            anchors.verticalCenter: parent.verticalCenter
            text: "Homa Lawn"
            color: Theme.brightForeground
            font.bold: true
          }
        }
      }

      Item { width: 1; height: 4 }

      PhonePicker {
        id: phonePicker
        rows: view.phoneRows
        current: view.phone
        expanded: view.railExpanded
        active: view.serviceUp
        idleText: view.serviceDown ? "service not running" : ""
        overlay: view
        onPicked: id => view.pickPhone(id)
        onAddPhone: { view.section = "add"; view.railPeek = false }
      }

      Item {
        visible: view.phones.length > 0
        width: parent.width
        height: 17
        Rectangle {
          x: 16
          anchors.verticalCenter: parent.verticalCenter
          width: parent.width - 32
          height: 1
          color: Theme.selection
        }
      }

      Repeater {
        model: view.phones.length > 0 ? view.phoneSections : []
        RailButton {
          required property var modelData
          code: modelData.code
          label: modelData.label
          expanded: view.railExpanded
          selected: view.shown === modelData.key
          draft: view.draftIn(modelData.key)
          onClicked: { view.section = modelData.key; view.railPeek = false }
        }
      }
    }

    Column {
      anchors.left: parent.left
      anchors.right: parent.right
      anchors.bottom: parent.bottom
      anchors.bottomMargin: 10
      spacing: 2
      Repeater {
        model: view.bottomSections
        RailButton {
          required property var modelData
          code: modelData.code
          label: modelData.label
          tip: modelData.label + (view.serviceDown ? " · not running" : "")
          expanded: view.railExpanded
          selected: view.shown === modelData.key
          showDot: view.serviceDown
          dot: Theme.red
          onClicked: { view.section = modelData.key; view.railPeek = false }
        }
      }
    }
  }

  Toast {
    id: toast
    anchors.right: parent.right
    anchors.top: parent.top
    anchors.margins: 16
    anchors.topMargin: header.height + 12
    z: 20
  }
}
