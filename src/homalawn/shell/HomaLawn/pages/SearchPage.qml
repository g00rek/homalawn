import QtQuick
import "../components"
import ".."

// The Search & gestures tab: what each home-screen gesture does, and how the app search behaves
// (its search bar, the keyboard, the search algorithm, the web suggestion provider and which kinds
// of results it shows). Names and choices are Lawnchair's own, sent by the service (search.get).
//
// Like the other tabs, the page edits a draft: nothing reaches the phone until Apply, which sends
// what differs with `search.apply` (one undo step); Discard drops it. A draft is kept per phone on
// the connection (backend.searchDrafts) until it is applied or discarded. A locked phone takes the
// Apply at once. The page needs the phone: offline, the view shows only its one line instead.
Item {
  id: page

  // Given by HomaLawnView.
  property var backend: null
  property string phone: ""
  property string phoneName: ""
  property var phoneState: null
  property var host: null

  property var info: null          // search.get's answer: handlers, fields, deck, gesture apps
  property var saved: null         // {gestures: {name: type}, search: {name: value}} on the phone
  property var draft: null
  property string searchPhone: ""
  property string loadError: ""
  property string applyError: ""
  property var problems: ({})      // setting name -> what the phone said is wrong with it
  property bool applying: false
  readonly property var presence: ({ here: true })

  readonly property bool online: !!(phoneState && phoneState.connected)
  readonly property bool busyHere: !!backend && (backend.busy || []).some(b => b.phone === page.phone)
  readonly property var diff: delta(draft, saved)
  readonly property int changes: diff ? Object.keys(diff.gestures).length + Object.keys(diff.search).length : 0
  readonly property bool changed: changes > 0
  readonly property bool wide: width >= 1040

  function toast(text) { if (backend && text) backend.toast(text) }
  function copy(o) { return o ? JSON.parse(JSON.stringify(o)) : null }

  // What differs between two drafts: {gestures: {name: type}, search: {name: value}} of `d`.
  function delta(d, s) {
    if (!d || !s) return null
    var out = { gestures: {}, search: {} }
    for (var g in d.gestures) if (d.gestures[g] !== s.gestures[g]) out.gestures[g] = d.gestures[g]
    for (var k in d.search) if (d.search[k] !== s.search[k]) out.search[k] = d.search[k]
    return out
  }

  function fromInfo(r) {
    var g = {}
    for (var n in r.gestures) g[n] = r.gestures[n].type
    return { gestures: g, search: Object.assign({}, r.search) }
  }

  function keepDraft() {
    if (!backend || !searchPhone || !saved || !draft) return
    var all = Object.assign({}, backend.searchDrafts || {})
    if (changed) all[searchPhone] = copy(draft)
    else delete all[searchPhone]
    backend.searchDrafts = all
  }

  function load() {
    searchPhone = phone
    info = null
    saved = null
    draft = null
    problems = ({})
    applyError = ""
    loadError = ""
    if (!backend || !phone) return
    refresh(true)
  }

  // Read the phone's settings again; the draft follows them unless one is being edited.
  function refresh(first) {
    var who = searchPhone
    var presence = page.presence
    backend.call("search.get", { phone: who }, function (err, result) {
      if (!presence.here || page.searchPhone !== who) return
      if (err) {
        page.loadError = "Could not read the search and gesture settings of " + page.phoneName + ": " + (err.message || err.code)
        return
      }
      page.loadError = ""
      page.info = result
      page.saved = page.fromInfo(result)
      var kept = backend.searchDrafts ? backend.searchDrafts[who] : null
      if (first && kept) page.draft = page.copy(kept)
      else if (first || !kept) page.draft = page.copy(page.saved)
      page.keepDraft()
    })
  }

  function setGesture(name, type) {
    if (!draft) return false
    var d = copy(draft)
    d.gestures[name] = type
    draft = d
    keepDraft()
    return true
  }
  function setSearch(name, value) {
    if (!draft) return false
    var d = copy(draft)
    d.search[name] = value
    draft = d
    keepDraft()
    return true
  }

  function applyNow() {
    if (!changed || applying || !backend || !searchPhone || !online || busyHere) return false
    applying = true
    problems = ({})
    applyError = ""
    var who = searchPhone
    var params = { phone: who, gestures: diff.gestures, search: diff.search }
    var presence = page.presence
    backend.call("search.apply", params, function (err, result) {
      if (!presence.here) return
      page.applying = false
      if (page.searchPhone !== who) return
      if (err) {
        if (err.code === "invalid" && err.errors && err.errors.length) {
          var p = {}
          err.errors.forEach(e => { p[e.setting || ""] = e.message })
          page.problems = p
        }
        page.applyError = err.message || err.code
        return
      }
      var said = result && result.text ? result.text : "Search & gestures applied."
      page.toast(result && result.notes && result.notes.length ? said + " " + result.notes.join(" ") : said)
      var all = Object.assign({}, backend.searchDrafts || {})
      delete all[who]
      backend.searchDrafts = all
      page.refresh(false)
    })
    return true
  }

  function discard() {
    if (!changed) return false
    draft = copy(saved)
    problems = ({})
    applyError = ""
    keepDraft()
    return true
  }

  // The options of one gesture's list: what the phone offers, plus its current value when that
  // cannot be chosen here ("Open <app>", or an option the Lawn deck hides).
  function gestureOptions(name) {
    if (!info) return []
    var out = info.handlers.map(h => ({ key: h.type, label: h.label }))
    var g = info.gestures[name]
    var now = draft ? draft.gestures[name] : ""
    if (g && g.type === "openApp")
      out.push({ key: "openApp", label: "Open " + (g.app || "an app") + " (set on the phone)", disabled: true })
    else if (now && !out.some(o => o.key === now))
      out.push({ key: now, label: now, disabled: true })
    return out
  }
  function choiceOf(name) {
    var all = info && info.fields ? info.fields.choices : []
    return all.find(c => c.name === name) || null
  }
  function switchLabel(name) {
    var all = info && info.fields ? info.fields.switches : []
    var f = all.find(s => s.name === name)
    return f ? f.label : name
  }

  onPhoneChanged: load()
  onBackendChanged: if (searchPhone && !draft) load()
  Connections {
    target: page.host
    ignoreUnknownSignals: true
    function onUndone() { if (page.searchPhone) page.refresh(false) }
  }
  Component.onDestruction: presence.here = false

  // ── Status lines ────────────────────────────────────────────────────────────────────────
  Column {
    id: status
    anchors.left: parent.left
    anchors.right: parent.right
    anchors.top: parent.top
    StatusLine { visible: page.loadError !== ""; tone: Theme.red; text: page.loadError }
    StatusLine { visible: page.applyError !== ""; tone: Theme.red; text: "Apply failed: " + page.applyError }
  }

  ApplyBar {
    id: bottomBar
    changes: page.changes
    applying: page.applying
    canApply: !page.busyHere && page.online
    failed: page.applyError !== ""
    onApply: page.applyNow()
    onDiscard: page.discard()
  }

  // One labelled setting: the label (accent while the draft differs), a line under it, the control.
  component Setting: Item {
    id: setting
    property string label: ""
    property string detail: ""
    property string name: ""
    property bool differs: false
    property bool dim: false
    default property alias control: slot.data
    width: parent ? parent.width : 0
    height: Math.max(texts.implicitHeight, slot.childrenRect.height) + 14
    Column {
      id: texts
      opacity: setting.dim ? Theme.disabledOpacity : 1
      anchors.left: parent.left
      anchors.right: slot.left
      anchors.rightMargin: 14
      anchors.verticalCenter: parent.verticalCenter
      spacing: 2
      Row {
        spacing: 8
        Txt {
          id: settingLabel
          text: setting.label
          font.pixelSize: Theme.body
          font.bold: setting.differs
          color: Theme.brightForeground
        }
        // Not applied yet (the window's draft mark).
        Rectangle {
          visible: setting.differs
          anchors.verticalCenter: settingLabel.verticalCenter
          width: 8; height: 8; radius: 4
          color: Theme.accent
        }
      }
      Txt {
        visible: setting.detail !== ""
        width: parent.width
        text: setting.detail
        wrapMode: Text.Wrap
        font.pixelSize: Theme.tiny
        color: Theme.darkForeground
      }
      Txt {
        visible: !!page.problems[setting.name]
        width: parent.width
        text: page.problems[setting.name] || ""
        wrapMode: Text.Wrap
        font.pixelSize: Theme.tiny
        color: Theme.red
      }
    }
    Item {
      id: slot
      anchors.right: parent.right
      anchors.verticalCenter: parent.verticalCenter
      width: childrenRect.width
      height: childrenRect.height
    }
  }

  // ── The settings ────────────────────────────────────────────────────────────────────────
  Txt {
    anchors.centerIn: flick
    visible: !page.draft && page.loadError === ""
    text: "Reading the settings…"
    color: Theme.darkForeground
  }

  Flickable {
    id: flick
    anchors.left: parent.left
    anchors.right: parent.right
    anchors.top: status.bottom
    anchors.bottom: bottomBar.top
    contentWidth: width
    contentHeight: content.height + 2 * Theme.margin
    clip: true
    boundsBehavior: Flickable.StopAtBounds
    interactive: contentHeight > height
    visible: !!page.draft

    Grid {
      id: content
      x: Theme.margin
      y: Theme.margin
      readonly property real colW: page.wide ? (Math.min(flick.width - 2 * Theme.margin, 1100) - spacing) / 2 : Math.min(flick.width - 2 * Theme.margin, 640)
      columns: page.wide ? 2 : 1
      spacing: Theme.gap

      // Gestures
      Card {
        width: content.colW
        title: "Gestures"
        subtitle: "What a gesture on the home screen does."
        spacing: 0
        Repeater {
          model: page.info ? page.info.fields.gestures : []
          Setting {
            id: gestureRow
            required property var modelData
            name: modelData.name
            label: modelData.label
            differs: !!page.diff && page.diff.gestures[modelData.name] !== undefined
            Dropdown {
              width: Math.min(250, content.colW * 0.52)
              overlay: page
              options: page.gestureOptions(gestureRow.modelData.name)
              current: page.draft ? page.draft.gestures[gestureRow.modelData.name] || "" : ""
              marked: gestureRow.differs
              onPicked: key => page.setGesture(gestureRow.modelData.name, key)
            }
          }
        }
        Txt {
          visible: !!page.info && page.info.deck
          width: parent.width
          topPadding: 6
          wrapMode: Text.Wrap
          text: "The Lawn deck layout is on: it has no app drawer, so the drawer and app search options are not offered."
          font.pixelSize: Theme.tiny
          color: Theme.darkForeground
        }
      }

      // Search
      Card {
        id: searchCard
        width: content.colW
        title: "Search"
        subtitle: "The app drawer's search: what it shows and how it opens."
        spacing: 0
        readonly property var s: page.draft ? page.draft.search : ({})
        readonly property bool barOn: !!s.search_bar
        readonly property bool local: s.algorithm === "localSearch"

        Setting {
          name: "search_bar"
          label: page.switchLabel("search_bar")
          differs: !!page.diff && page.diff.search.search_bar !== undefined
          Toggle {
            checked: !!searchCard.s.search_bar
            onToggled: c => page.setSearch("search_bar", c)
          }
        }
        Setting {
          id: keyboardRow
          name: "auto_keyboard"
          label: page.switchLabel("auto_keyboard")
          detail: "When the app drawer opens."
          dim: !searchCard.barOn
          differs: !!page.diff && page.diff.search.auto_keyboard !== undefined
          Toggle {
            active: searchCard.barOn
            checked: !!searchCard.s.auto_keyboard
            onToggled: c => page.setSearch("auto_keyboard", c)
          }
        }
        Repeater {
          model: ["algorithm", "web_provider"]
          Setting {
            id: choiceRow
            required property string modelData
            readonly property var field: page.choiceOf(modelData)
            readonly property var card: searchCard
            name: modelData
            label: field ? field.label : modelData
            detail: modelData === "web_provider" ? "Used by Web suggestions; with Global search (on-device)." : ""
            dim: !card.barOn || (modelData === "web_provider" && !card.local)
            differs: !!page.diff && page.diff.search[modelData] !== undefined
            Dropdown {
              width: Math.min(250, content.colW * 0.52)
              overlay: page
              active: choiceRow.card.barOn && (choiceRow.modelData !== "web_provider" || choiceRow.card.local)
              options: choiceRow.field ? choiceRow.field.options.map(o => ({ key: o.value, label: o.label })) : []
              current: page.draft ? String(page.draft.search[choiceRow.modelData] || "") : ""
              marked: choiceRow.differs
              onPicked: key => page.setSearch(choiceRow.modelData, key)
            }
          }
        }

        Heading { text: "Show in search results"; topPadding: 10; bottomPadding: 4 }
        Txt {
          visible: !searchCard.local
          width: parent.width
          wrapMode: Text.Wrap
          bottomPadding: 4
          text: "Only Global search (on-device) searches more than apps."
          font.pixelSize: Theme.tiny
          color: Theme.yellow
        }
        Repeater {
          model: [
            { name: "apps", detail: "" },
            { name: "web", detail: "" },
            { name: "people", detail: "Contacts and more · needs the Contacts permission on the phone" },
            { name: "files", detail: "Media, files, and more · needs file access on the phone" },
            { name: "settings", detail: "" },
            { name: "history", detail: "" },
            { name: "calculator", detail: "" }
          ]
          Setting {
            id: sourceRow
            required property var modelData
            readonly property var card: searchCard
            // Apps can be switched off unless App Search is the algorithm; the others only matter
            // with Global search (on-device), as on the phone.
            readonly property bool usable: card.barOn && (modelData.name === "apps"
              ? card.s.algorithm !== "appSearch" && card.s.algorithm !== "globalSearch" : card.local)
            name: modelData.name
            label: page.switchLabel(modelData.name)
            detail: modelData.name === "web" && page.choiceOf("web_provider")
              ? "Via " + ((page.choiceOf("web_provider").options.find(o => o.value === card.s.web_provider) || {}).label || card.s.web_provider)
              : modelData.detail
            dim: !usable
            differs: !!page.diff && page.diff.search[modelData.name] !== undefined
            Toggle {
              active: sourceRow.usable
              checked: !!sourceRow.card.s[sourceRow.modelData.name]
              onToggled: c => page.setSearch(sourceRow.modelData.name, c)
            }
          }
        }
      }
    }
  }
}
