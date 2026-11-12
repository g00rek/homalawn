import QtQuick
import "../components"
import ".."
import "HomeDraft.js" as D

// The Home screen tab: the phone's home screen as it looks (wallpaper, real icons and labels at the
// phone's sizes, widgets, folders, the dock), the apps and widgets to put on it, every screen as a
// thumbnail, and the grid and icon settings.
//
// Like Wallpaper & style, the page edits a draft (HomeDraft.js holds the rules): drags and
// settings change only the draft; Apply sends it with `home.apply` (one undo step); Discard drops
// it. A draft is kept per phone on the connection (backend.homeDrafts) until it is applied or
// discarded, so leaving the page or switching phone loses nothing and sends nothing.
//
// Drags: onto an empty cell places or moves; onto an app makes a folder; onto a folder adds to it;
// onto the dock places it there; onto the trash (shown while dragging) removes it; onto a screen
// thumbnail moves it to that screen's first free cell. A drop that does not fit is refused, the
// cells it would take shown red.
Item {
  id: page

  // Given by HomaLawnView.
  property var backend: null
  property string phone: ""
  property string phoneName: ""
  property var phoneState: null
  property var host: null

  // The draft, the phone's saved home screen (both HomeDraft drafts) and the phone they belong to.
  property var draft: null
  property var saved: null
  property string homePhone: ""
  property var apps: []
  property var widgetList: []
  property var icons: ({})            // component -> picture file (home.get and home.apps)
  property var geo: null              // where the phone draws its home screen (home.get's geometry); with the draft
  property string loadError: ""
  property var listErrors: ({})       // "apps" / "widgets" -> why the list could not be read
  property bool applying: false
  property var problems: ({})         // item key -> what the phone said is wrong with it
  property var problemLines: []       // every message of the last refused Apply
  property string applyError: ""
  property int screenIndex: 0         // the screen shown on the phone
  property string tab: "apps"
  property string query: ""
  property string folderKey: ""       // the open folder
  readonly property var presence: ({ here: true })

  // A drag on its way: what it carries, where the pointer is (page coordinates), what is under it.
  property var dragSrc: null
  property point dragPos: Qt.point(0, 0)
  property var dragTarget: null
  property var dragVerdict: null

  readonly property int changes: D.changes(draft, saved)
  readonly property bool changed: changes > 0
  readonly property bool online: !!(phoneState && phoneState.connected)
  readonly property bool busyHere: !!backend && (backend.busy || []).some(b => b.phone === page.phone)
  readonly property var shownScreen: draft ? (D.screenAt(draft, screenIndex) || draft.screens[0]) : null
  readonly property var openFolder: {
    if (!draft || !folderKey) return null
    var at = D.locate(draft, folderKey)
    return at && at.item.type === "folder" ? at.item : null
  }
  readonly property var widgetMap: {
    var out = {}
    widgetList.forEach(w => { out[D.provider(w.provider)] = { label: w.label, preview: w.preview } })
    return out
  }
  // The phone's screen; the picture and its thumbnails are drawn only with the phone's geometry.
  readonly property var screenSize: geo ? geo.screen : ({ w: 1, h: 2 })

  // The home wallpaper as Wallpaper & style sets it; none (neutral) when the look is not synced.
  readonly property var look: phoneState && phoneState.look ? phoneState.look : null
  // The home screen's Screen setting: a theme wallpaper shows its pick for the theme (else the
  // default: following, Omarchy's background; a fixed theme, its first wallpaper); keep and
  // gradient show the neutral fill.
  readonly property var homeLook: look && look.wallpaper && look.wallpaper.home ? look.wallpaper.home : null
  readonly property string wallpaper: {
    if (!look || look.source === "off" || !homeLook || homeLook.kind !== "wallpaper") return ""
    var omarchy = backend && backend.omarchy ? backend.omarchy : {}
    var name = look.source === "follow" ? omarchy.theme || "" : look.theme || ""
    var pick = look.picks ? look.picks[Paths.slug(name)] : null
    if (pick && typeof pick.home === "string" && pick.home) return pick.home
    if (look.source === "follow") return omarchy.background || ""
    var themes = host && host.themes ? host.themes : []
    var t = themes.find(t => Paths.slug(t.name) === Paths.slug(look.theme))
    return t && t.backgrounds && t.backgrounds.length ? t.backgrounds[0] : ""
  }
  readonly property real wallOffset: {
    var c = look && look.crops && look.crops[wallpaper] ? look.crops[wallpaper] : null
    return c && typeof c.home === "number" ? c.home : 0.5
  }
  readonly property int wallLayer: homeLook && wallpaper ? homeLook.layer || 0 : 0
  // Parallax: the phone's home picture is two screens wide; page 0 shows its left slice.
  readonly property real wallSpan: wallpaper && look.wallpaper.parallax ? 2 : 1

  function toast(text) { if (backend && text) backend.toast(text) }

  // ── The draft ───────────────────────────────────────────────────────────────────────────
  function keepDraft() {
    if (!backend || !homePhone || !saved || !draft) return
    var all = Object.assign({}, backend.homeDrafts || {})
    if (changed) all[homePhone] = D.copy(draft)
    else delete all[homePhone]
    backend.homeDrafts = all
  }
  function setDraft(d) {
    // The label gap stays what the phone can draw: a larger icon or label, or more rows, lower
    // the most its cell has room for (patch 0025), and the draft follows.
    var lg = D.labelGap(geo, d, saved)
    if (lg && d.label_gap_dp !== lg.gapDp) {
      d = D.copy(d)
      d.label_gap_dp = lg.gapDp
    }
    draft = d
    // The phone's complaints stay on the items still there (the line stays until the next Apply).
    var left = {}
    for (var k in problems) if (D.locate(d, k)) left[k] = problems[k]
    problems = left
    if (folderKey && !openFolder) folderKey = ""
    if (!D.screenAt(draft, screenIndex)) screenIndex = draft.screens[0].index
    keepDraft()
  }
  // Apply a HomeDraft answer {ok, draft, message}; a refusal or a note becomes a toast.
  function take(r) {
    if (r.ok) setDraft(r.draft)
    if (r.message) toast(r.message)
    return r.ok
  }

  function load() {
    homePhone = phone
    draft = null
    saved = null
    geo = null
    problems = ({})
    problemLines = []
    applyError = ""
    loadError = ""
    folderKey = ""
    listErrors = ({})
    if (!backend || !phone) return
    refresh(true)
    var who = phone
    var presence = page.presence
    backend.call("home.apps", { phone: who }, function (err, result) {
      if (!presence.here || page.homePhone !== who) return
      if (err) { page.listErrors = Object.assign({}, page.listErrors, { apps: err.message || err.code }); return }
      page.apps = result || []
      var all = Object.assign({}, page.icons)
      page.apps.forEach(a => { if (a.icon && !all[a.component]) all[a.component] = a.icon })
      page.icons = all
    })
    backend.call("home.widgets", { phone: who }, function (err, result) {
      if (!presence.here || page.homePhone !== who) return
      if (err) { page.listErrors = Object.assign({}, page.listErrors, { widgets: err.message || err.code }); return }
      page.widgetList = result || []
    })
  }

  // Read the phone's home screen again; the draft follows it unless one is being edited.
  function refresh(first) {
    var who = homePhone
    var presence = page.presence
    backend.call("home.get", { phone: who }, function (err, result) {
      if (!presence.here || page.homePhone !== who) return
      if (err) {
        page.loadError = "Could not read the home screen of " + page.phoneName + ": " + (err.message || err.code)
        return
      }
      var g = result.geometry
      if (!(g && g.screen && g.screen.w > 0 && g.screen.h > 0 && g.workspace && g.hotseat && g.icon && g.insets)) {
        page.loadError = "Could not read the home screen of " + page.phoneName + ": the phone sent no geometry of it"
        return
      }
      page.loadError = ""
      page.icons = Object.assign({}, page.icons, result.icons || {})
      page.geo = g
      page.saved = D.fromHome(result)
      var kept = backend.homeDrafts ? backend.homeDrafts[who] : null
      if (first && kept) page.draft = D.copy(kept)
      else if (first || !kept) page.draft = D.copy(page.saved)
      if (!D.screenAt(page.draft, page.screenIndex)) page.screenIndex = page.draft.screens[0].index
      page.keepDraft()
    })
  }

  // What the phone picture draws, for the offscreen test: lengths as fractions of the picture's
  // width, heights and vertical positions as fractions of its height.
  function previewFacts() {
    var w = scr.width, h = scr.height
    function box(it) { return { x: it.x / w, y: it.y / h, w: it.width / w, h: it.height / h } }
    return { size: [w, h],
             icon: scr.metrics.icon / w, label: scr.metrics.label / w, gap: scr.metrics.gap / h,
             top: scr.metrics.top === undefined ? null : scr.metrics.top / h,
             grid: box(mainGrid), dock: box(dock), dockIcon: scr.dockMetrics.icon / w }
  }

  function applyNow() {
    if (!changed || applying || !backend || !homePhone) return false
    applying = true
    problems = ({})
    problemLines = []
    applyError = ""
    var who = homePhone
    var sent = D.copy(draft)
    var params = D.toApply(sent, saved)
    params.phone = who
    var presence = page.presence
    backend.call("home.apply", params, function (err, result) {
      if (!presence.here) return
      page.applying = false
      if (page.homePhone !== who) return
      if (err) {
        if (err.code === "invalid" && err.errors && err.errors.length) {
          page.problems = D.problemKeys(page.draft, err.errors)
          page.problemLines = err.errors.map(e => e.message)
        } else {
          page.applyError = err.message || err.code
        }
        return
      }
      // a locked phone takes it at once; a note may say its home screen shows it at the unlock
      var said = result && result.text ? result.text : "Home screen applied."
      page.toast(result && result.notes && result.notes.length ? said + " " + result.notes.join(" ") : said)
      var all = Object.assign({}, backend.homeDrafts || {})
      delete all[who]
      backend.homeDrafts = all
      page.refresh(false)
    })
    return true
  }

  function discard() {
    if (!changed) return false
    draft = D.copy(saved)
    problems = ({})
    problemLines = []
    applyError = ""
    folderKey = ""
    if (!D.screenAt(draft, screenIndex)) screenIndex = draft.screens[0].index
    keepDraft()
    return true
  }

  function setGrid(cols, rows) { return take(D.setGrid(draft, cols, rows)) }
  function setDock(n) { return take(D.setDock(draft, n)) }
  function setSetting(name, value) {
    var d = D.copy(draft)
    d[name] = value
    setDraft(d)
  }
  function addScreen() {
    var r = D.addScreen(draft)
    setDraft(r.draft)
    screenIndex = r.index
  }
  // Open a folder (HomeScreen calls this when one is clicked).
  function openFolderAt(k) { folderKey = k }

  // ── Dragging (DragSource calls these) ───────────────────────────────────────────────────
  // A drag out of an open folder hides the folder but keeps it alive until the drop: closing it at
  // the start destroyed the tile that holds the mouse, so the release never came and the drag hung.
  function dragStart(src, x, y) {
    dragSrc = src
    dragMove(x, y)
  }
  function dragMove(x, y) {
    dragPos = Qt.point(x, y)
    dragTarget = targetAt(x, y)
    dragVerdict = dragTarget ? D.verdict(draft, dragSrc, dragTarget) : null
  }
  function dragEnd() {
    var src = dragSrc, target = dragTarget
    dragCancel()
    if (!src || !target) return false
    var r = D.drop(draft, src, target)
    if (r.ok) {
      setDraft(r.draft)
      if (target.thumb !== undefined) toast("Moved to screen " + (screenNumber(target.thumb)) + ".")
    } else if (r.message) {
      toast(r.message)
    }
    return r.ok
  }
  function dragCancel() {
    if (dragSrc) folderKey = ""
    dragSrc = null
    dragTarget = null
    dragVerdict = null
  }

  function carriedItem(src) {
    if (!src || !draft) return null
    if (src.item) { var at = D.locate(draft, src.item); return at ? at.item : null }
    if (src.app) return { type: "app", package: src.app.package, activity: src.app.activity, title: src.app.label }
    if (src.widget) return { type: "widget", provider: src.widget.provider, w: src.widget.cells[0], h: src.widget.cells[1] }
    return null
  }

  function screenNumber(index) {
    if (!draft) return 1
    for (var i = 0; i < draft.screens.length; i++) if (draft.screens[i].index === index) return i + 1
    return 1
  }

  // What lies under a point of the page: a cell, a dock place, the trash, a thumbnail, or null.
  function targetAt(px, py) {
    if (!draft) return null
    var t = trash.mapFromItem(page, px, py)
    if (trash.visible && t.x >= 0 && t.y >= 0 && t.x < trash.width && t.y < trash.height) return { trash: true }
    for (var i = 0; i < thumbs.count; i++) {
      var th = thumbs.itemAt(i)
      if (!th) continue
      var q = th.mapFromItem(page, px, py)
      if (q.x >= 0 && q.y >= 0 && q.x < th.width && q.y < th.height) return { thumb: th.screenIdx }
    }
    var g = mainGrid.mapFromItem(page, px, py)
    if (g.x >= 0 && g.y >= 0 && g.x < mainGrid.width && g.y < mainGrid.height) {
      var it = carriedItem(dragSrc)
      var wh = D.size(it)
      var cx = Math.floor(g.x / mainGrid.cellW - (wh[0] - 1) / 2)
      var cy = Math.floor(g.y / mainGrid.cellH - (wh[1] - 1) / 2)
      cx = Math.max(0, Math.min(cx, draft.grid[0] - 1))
      cy = Math.max(0, Math.min(cy, draft.grid[1] - 1))
      return { screen: shownScreen.index, x: cx, y: cy }
    }
    var dk = dock.mapFromItem(page, px, py)
    if (dk.x >= 0 && dk.y >= 0 && dk.x < dock.width && dk.y < dock.height)
      return { dock: Math.max(0, Math.min(draft.hotseat_count - 1, Math.floor(dk.x / dock.slot))) }
    return null
  }

  // The centre of a target in page coordinates (the test harness poses a drag with it).
  function pointOf(target) {
    if (target.trash) return trash.mapToItem(page, trash.width / 2, trash.height / 2)
    if (target.thumb !== undefined) {
      for (var i = 0; i < thumbs.count; i++) {
        var th = thumbs.itemAt(i)
        if (th && th.screenIdx === target.thumb) return th.mapToItem(page, th.width / 2, th.height / 2)
      }
      return Qt.point(-1, -1)
    }
    if (target.dock !== undefined) return dock.mapToItem(page, (target.dock + 0.5) * dock.slot, dock.height / 2)
    var wh = D.size(carriedItem(dragSrc))
    return mainGrid.mapToItem(page, (target.x + wh[0] / 2) * mainGrid.cellW, (target.y + wh[1] / 2) * mainGrid.cellH)
  }

  onPhoneChanged: load()
  onBackendChanged: if (homePhone && !draft) load()
  onHostChanged: if (host && typeof host.loadThemes === "function") host.loadThemes()
  Connections {
    target: page.host
    ignoreUnknownSignals: true
    function onUndone() { if (page.homePhone) page.refresh(false) }
  }
  Component.onDestruction: presence.here = false

  // ── Status lines ────────────────────────────────────────────────────────────────────────
  Column {
    id: status
    anchors.left: parent.left
    anchors.right: parent.right
    anchors.top: parent.top
    StatusLine { visible: page.loadError !== ""; tone: Theme.red; text: page.loadError }
    StatusLine {
      visible: page.problemLines.length > 0
      tone: Theme.red
      text: "The phone would not take this home screen (marked red): " + page.problemLines.slice(0, 3).join("; ")
            + (page.problemLines.length > 3 ? " … and " + (page.problemLines.length - 3) + " more" : "")
    }
    StatusLine { visible: page.applyError !== ""; tone: Theme.red; text: "Apply failed: " + page.applyError }
  }

  ApplyBar {
    id: bottomBar
    changes: page.changes
    applying: page.applying
    canApply: !page.busyHere && page.online
    failed: page.applyError !== "" || page.problemLines.length > 0
    onApply: page.applyNow()
    onDiscard: page.discard()
  }

  // ── Side panel: Apps | Widgets ──────────────────────────────────────────────────────────
  Rectangle {
    id: side
    anchors.left: parent.left
    anchors.top: status.bottom
    anchors.bottom: bottomBar.top
    width: page.width >= 1100 ? 270 : 236
    color: Theme.darkBackground
    Rectangle { anchors.right: parent.right; width: 1; height: parent.height; color: Theme.selection }

    Segmented {
      id: tabs
      x: 14
      y: 14
      options: [{ key: "apps", label: "Apps" }, { key: "widgets", label: "Widgets" }]
      current: page.tab
      onPicked: key => page.tab = key
    }

    Rectangle {
      id: searchBox
      x: 14
      anchors.top: tabs.bottom
      anchors.topMargin: 10
      width: side.width - 28
      height: 30
      radius: 5
      color: Theme.darkerBackground
      border.width: 1
      border.color: search.activeFocus ? Theme.accent : Theme.muted
      TextInput {
        id: search
        anchors.fill: parent
        anchors.leftMargin: 10
        anchors.rightMargin: 10
        verticalAlignment: TextInput.AlignVCenter
        color: Theme.brightForeground
        selectionColor: Theme.accent
        font.family: Theme.font
        font.pixelSize: Theme.small
        clip: true
        text: page.query
        onTextEdited: page.query = text
      }
      Txt {
        visible: search.text === ""
        anchors.left: parent.left
        anchors.leftMargin: 10
        anchors.verticalCenter: parent.verticalCenter
        text: page.tab === "apps" ? "Search apps" : "Search widgets"
        font.pixelSize: Theme.small
        color: Theme.darkForeground
      }
    }

    Txt {
      id: sideHint
      x: 14
      anchors.top: searchBox.bottom
      anchors.topMargin: 8
      width: side.width - 28
      text: "Drag onto the phone"
      font.pixelSize: Theme.tiny
      color: Theme.darkForeground
    }

    ListView {
      id: list
      anchors.top: sideHint.bottom
      anchors.topMargin: 6
      anchors.left: parent.left
      anchors.right: parent.right
      anchors.bottom: parent.bottom
      anchors.rightMargin: 1
      clip: true
      boundsBehavior: Flickable.StopAtBounds
      readonly property string q: page.query.trim().toLowerCase()
      model: {
        var all = page.tab === "apps" ? page.apps : page.widgetList
        if (!q) return all
        return all.filter(a => String(a.label || "").toLowerCase().indexOf(q) >= 0
                               || String(a.package || a.provider || "").toLowerCase().indexOf(q) >= 0)
      }
      delegate: Rectangle {
        id: row
        required property var modelData
        readonly property bool isWidget: page.tab === "widgets"
        width: list.width
        height: isWidget ? 64 : 40
        color: drag.containsMouse ? Theme.selection : "transparent"
        Image {
          id: rowIcon
          x: 14
          anchors.verticalCenter: parent.verticalCenter
          width: row.isWidget ? 64 : 28
          height: row.isWidget ? 48 : 28
          source: Paths.url(row.isWidget ? row.modelData.preview : row.modelData.icon)
          sourceSize: Qt.size(128, 128)
          fillMode: Image.PreserveAspectFit
          smooth: true
          asynchronous: true
        }
        Column {
          anchors.left: rowIcon.right
          anchors.leftMargin: 10
          anchors.right: parent.right
          anchors.rightMargin: 12
          anchors.verticalCenter: parent.verticalCenter
          spacing: 2
          Txt {
            width: parent.width
            text: row.modelData.label || ""
            elide: Text.ElideRight
            font.pixelSize: Theme.small
            color: Theme.brightForeground
          }
          Txt {
            visible: row.isWidget
            text: row.isWidget ? row.modelData.cells[0] + " × " + row.modelData.cells[1] + " cells" : ""
            font.pixelSize: Theme.tiny
            color: Theme.darkForeground
          }
        }
        DragSource {
          id: drag
          anchors.fill: parent
          editor: page
          src: row.isWidget ? { widget: row.modelData } : { app: row.modelData }
        }
      }
      Txt {
        anchors.centerIn: parent
        visible: list.count === 0
        width: parent.width - 28
        horizontalAlignment: Text.AlignHCenter
        wrapMode: Text.Wrap
        text: page.query ? "Nothing matches."
          : page.listErrors[page.tab] ? "Could not read the " + page.tab + ": " + page.listErrors[page.tab]
          : "Reading…"
        font.pixelSize: Theme.small
        color: Theme.darkForeground
      }
    }
  }

  // ── Screens strip ───────────────────────────────────────────────────────────────────────
  Rectangle {
    id: strip
    anchors.right: parent.right
    anchors.top: status.bottom
    anchors.bottom: bottomBar.top
    width: 118
    color: Theme.darkBackground
    Rectangle { width: 1; height: parent.height; color: Theme.selection }

    Heading {
      id: stripTitle
      anchors.horizontalCenter: parent.horizontalCenter
      y: 14
      text: "Screens"
    }

    Flickable {
      anchors.top: stripTitle.bottom
      anchors.topMargin: 10
      anchors.bottom: parent.bottom
      anchors.left: parent.left
      anchors.right: parent.right
      contentHeight: thumbColumn.height + 16
      clip: true
      boundsBehavior: Flickable.StopAtBounds
      interactive: contentHeight > height

      Column {
        id: thumbColumn
        anchors.horizontalCenter: parent.horizontalCenter
        spacing: 12
        Repeater {
          id: thumbs
          model: page.draft ? page.draft.screens : []
          Rectangle {
            id: thumb
            required property var modelData
            readonly property int screenIdx: modelData.index
            readonly property bool current: page.shownScreen && page.shownScreen.index === modelData.index
            readonly property bool aimed: !!page.dragTarget && page.dragTarget.thumb === modelData.index
            width: 78
            height: width * page.screenSize.h / page.screenSize.w + 18   // the phone's own proportions
            color: "transparent"
            Rectangle {
              id: thumbFace
              width: parent.width
              height: parent.height - 18
              radius: 6
              color: "#000000"
              border.width: 2
              border.color: thumb.aimed ? (page.dragVerdict && page.dragVerdict.ok ? Theme.accent : Theme.red)
                : thumb.current ? Theme.accent : Theme.muted
              clip: true
              Wallpaper {
                anchors.fill: parent
                anchors.margins: 2
                source: page.wallpaper
                offset: page.wallOffset
                amount: page.wallLayer
                span: page.wallSpan
              }
              HomeScreen {
                x: 5
                y: 6
                width: parent.width - 10
                height: parent.height - 12
                screen: thumb.modelData
                cols: page.draft ? page.draft.grid[0] : 4
                rows: page.draft ? page.draft.grid[1] : 5
                reservedTop: !!page.draft && page.draft.top_row_reserved && D.searchScreen(page.draft, thumb.modelData.index)
                m: ({ icon: Math.min(width / cols, height / rows) * 0.62, label: 0, gap: 0, labels: false })
                icons: page.icons
                widgets: page.widgetMap
                mini: true
              }
              MouseArea {
                anchors.fill: parent
                cursorShape: Qt.PointingHandCursor
                onClicked: { page.folderKey = ""; page.screenIndex = thumb.modelData.index }
              }
            }
            Txt {
              anchors.horizontalCenter: parent.horizontalCenter
              anchors.bottom: parent.bottom
              text: String(page.screenNumber(thumb.modelData.index))
              font.pixelSize: Theme.tiny
              color: thumb.current ? Theme.accent : Theme.darkForeground
            }
          }
        }
        // + : a new, empty screen.
        Rectangle {
          width: 78
          height: 44
          radius: 6
          color: plusArea.containsMouse ? Theme.selection : "transparent"
          border.width: 1
          border.color: Theme.muted
          Txt {
            anchors.centerIn: parent
            text: "+ Screen"
            font.pixelSize: Theme.small
            color: Theme.brightForeground
          }
          MouseArea {
            id: plusArea
            anchors.fill: parent
            hoverEnabled: true
            cursorShape: Qt.PointingHandCursor
            onClicked: if (page.draft) page.addScreen()
          }
        }
      }
    }
  }

  // ── Centre: Grid & icons, the phone ─────────────────────────────────────────────────────
  Item {
    id: centre
    anchors.left: side.right
    anchors.right: strip.left
    anchors.top: status.bottom
    anchors.bottom: bottomBar.top

    Txt {
      anchors.centerIn: parent
      visible: !page.draft && page.loadError === ""
      text: "Reading the home screen…"
      color: Theme.darkForeground
    }

    // Grid & icons, beside the phone (the phone and this column are centred together).
    Column {
      id: controls
      visible: !!page.draft
      x: phoneBody.x + phoneBody.width + 28
      anchors.verticalCenter: phoneBody.verticalCenter
      width: 216
      spacing: 10
      Heading {
        text: "Grid & icons"
      }
      readonly property var d: page.draft || ({ grid: [4, 5], hotseat_count: 4, icon_scale: 1, label_scale: 1, label_gap_dp: 0, show_labels: true })
      Stepper {
        width: parent.width
        text: "Columns"
        value: controls.d.grid[0]
        from: 3
        to: 10
        onStepped: v => page.setGrid(v, controls.d.grid[1])
      }
      Stepper {
        width: parent.width
        text: "Rows"
        value: controls.d.grid[1]
        from: 3
        to: 10
        onStepped: v => page.setGrid(controls.d.grid[0], v)
      }
      Stepper {
        width: parent.width
        text: "Dock icons"
        value: controls.d.hotseat_count
        from: 3
        to: 10
        onStepped: v => page.setDock(v)
      }
      Stepper {
        width: parent.width
        text: "Icon size"
        value: Math.round(controls.d.icon_scale * 100)
        from: 50
        to: 150
        step: 5
        suffix: "%"
        onStepped: v => page.setSetting("icon_scale", v / 100)
      }
      Stepper {
        width: parent.width
        text: "Label size"
        value: Math.round(controls.d.label_scale * 100)
        from: 50
        to: 150
        step: 5
        suffix: "%"
        active: controls.d.show_labels
        onStepped: v => page.setSetting("label_scale", v / 100)
      }
      // The visible gap from the icon's shape to the label's capitals, 0 (touching) up to the
      // most the phone's cell has room for, as the phone reports it (patch 0025). A phone that does
      // not report it cannot be set from here.
      Stepper {
        readonly property var lg: D.labelGap(page.geo, controls.d, page.saved)
        width: parent.width
        text: "Label gap"
        value: lg ? lg.gapDp : controls.d.label_gap_dp
        from: 0
        to: lg ? lg.maxDp : 0
        suffix: " dp"
        active: controls.d.show_labels && !!lg
        onStepped: v => { if (lg) page.setSetting("label_gap_dp", v) }
      }
      Item {
        width: parent.width
        height: 28
        Txt {
          id: labelsText
          anchors.verticalCenter: parent.verticalCenter
          text: "Labels"
          font.pixelSize: Theme.small
          color: Theme.darkForeground
        }
        Toggle {
          id: labelsToggle
          anchors.right: parent.right
          anchors.verticalCenter: parent.verticalCenter
          checked: controls.d.show_labels
          onToggled: checked => page.setSetting("show_labels", checked)
        }
      }
    }

    // The trash, over the controls while something from the phone is dragged.
    Rectangle {
      id: trash
      visible: !!page.dragSrc && !!page.dragSrc.item
      x: controls.x - 8
      y: controls.y - 8
      width: controls.width + 16
      height: controls.height + 16
      radius: 8
      readonly property bool aimed: !!page.dragTarget && !!page.dragTarget.trash
      color: Theme.blend(Theme.darkBackground, Theme.red, aimed ? 0.35 : 0.12)
      border.width: 2
      border.color: Theme.red
      z: 5
      MouseArea { anchors.fill: parent }   // the controls under it take no clicks meanwhile
      Column {
        anchors.centerIn: parent
        spacing: 6
        Txt {
          anchors.horizontalCenter: parent.horizontalCenter
          text: "✕  Remove"
          font.pixelSize: Theme.heading
          font.bold: true
          color: trash.aimed ? Theme.brightForeground : Theme.red
        }
        Txt {
          anchors.horizontalCenter: parent.horizontalCenter
          text: "from the home screen"
          font.pixelSize: Theme.tiny
          color: Theme.darkForeground
        }
      }
    }

    // The phone.
    Rectangle {
      id: phoneBody
      visible: !!page.draft && !!page.geo
      readonly property real bezel: 6
      readonly property real roomH: centre.height - 84
      readonly property real roomW: centre.width - controls.width - 28 - 40
      readonly property real innerH: Math.max(200, Math.min(roomH - 2 * bezel, (roomW - 2 * bezel) * page.screenSize.h / page.screenSize.w))
      readonly property real innerW: innerH * page.screenSize.w / page.screenSize.h
      x: Math.max(20, (centre.width - width - 28 - controls.width) / 2)
      y: 16
      width: innerW + 2 * bezel
      height: innerH + 2 * bezel
      radius: 22
      color: "#000000"
      border.width: 1
      border.color: Theme.muted

      Item {
        id: scr
        x: phoneBody.bezel
        y: phoneBody.bezel
        width: phoneBody.innerW
        height: phoneBody.innerH
        clip: true

        // The phone's own sizes, in this picture's pixels: home.get's geometry (the phone's pixels)
        // times k. The picture shows only once the geometry is in (it comes with the draft); until
        // then nothing here is drawn and every length is 0.
        readonly property var g: page.geo
        readonly property real k: g ? width / g.screen.w : 0
        readonly property real dp: g ? g.screen.density * k : 0
        readonly property real statusH: g ? g.insets.top * k : 0

        Wallpaper {
          anchors.fill: parent
          source: page.wallpaper
          offset: page.wallOffset
          amount: page.wallLayer
          span: page.wallSpan
        }

        // Icon, label and the gap between them, and where the icon sits in its cell, following
        // the draft from what the phone draws now (its settings are `saved`): the icon and label
        // scale by the draft's factor over the phone's, the gap moves by the change of the label
        // gap, and the icon and its label stay centred in the cell as the phone centres them (the
        // label's room is what the phone keeps for it).
        readonly property var metrics: {
          var d = page.draft
          if (!d || !g) return { icon: 0, label: 0, gap: 0, labels: false }
          var cellW = mainGrid.width / d.grid[0], cellH = mainGrid.height / d.grid[1]
          var labels = d.show_labels
          var s = page.saved || d
          var ic = g.icon
          var iconP = ic.size * k
          var textP = ic.text_size > 0 ? ic.text_size * k : 12.5 * dp * s.label_scale
          var padP = ic.drawable_padding * k      // may be negative: the label overlaps the icon's foot
          var blockP = ic.text_size > 0 && s.show_labels
            ? Math.max(textP * 1.2, g.workspace.cell_h * k - 2 * ic.top * k - iconP - padP) : textP * 2
          var icon = iconP * d.icon_scale / Math.max(0.01, s.icon_scale)
          var lbl = textP * d.label_scale / Math.max(0.01, s.label_scale)
          var gap = padP + (d.label_gap_dp - s.label_gap_dp) * dp
          var block = blockP * lbl / Math.max(0.01, textP)
          // The phone's own label gap (patch 0025): its padding, its capitals' place in the label
          // line (`cap`, so the caption's capitals land where the phone's do) and the icon's top,
          // by the phone's sums.
          var lg = D.labelGap(g, d, s)
          if (lg && labels) {
            return { icon: Math.max(8, Math.min(lg.icon * k, cellW, cellH)), label: lbl, gap: lg.pad * k,
                     cap: lg.cap * k, labels: true, top: lg.top * k }
          }
          icon = Math.max(8, Math.min(icon, cellW, cellH))   // the phone draws it whole in its cell
          var used = icon + (labels ? gap + block : 0)
          return { icon: icon, label: lbl, gap: gap, labels: labels, top: Math.max(0, (cellH - used) / 2) }
        }

        // The dock's icon: the phone's dock icon scaled by the draft's icon factor, kept where
        // the phone centres it.
        readonly property var dockMetrics: {
          var d = page.draft
          if (!d || !g) return { icon: 0, label: 0, gap: 0, labels: false }
          var s = page.saved || d
          var p = g.hotseat.icon_size * k
          var icon = Math.max(8, Math.min(p * d.icon_scale / Math.max(0.01, s.icon_scale), dock.slot, dock.height))
          return { icon: icon, label: 0, gap: 0, labels: false, top: Math.max(0, g.hotseat.icon_top * k + (p - icon) / 2) }
        }

        // Status bar.
        Text {
          x: 16 * scr.dp
          y: (scr.statusH - height) / 2
          text: "12:48"
          color: "#ffffff"
          font.pixelSize: Math.max(7, 12 * scr.dp)
        }

        HomeScreen {
          id: mainGrid
          x: scr.g ? scr.g.workspace.x * scr.k : 0
          y: scr.g ? scr.g.workspace.y * scr.k : 0
          width: scr.g ? scr.g.workspace.w * scr.k : 0
          height: scr.g ? scr.g.workspace.h * scr.k : 0
          screen: page.shownScreen
          cols: page.draft ? page.draft.grid[0] : 4
          rows: page.draft ? page.draft.grid[1] : 5
          reservedTop: !!page.draft && page.draft.top_row_reserved && !!page.shownScreen
                       && D.searchScreen(page.draft, page.shownScreen.index)
          m: scr.metrics
          icons: page.icons
          widgets: page.widgetMap
          editor: page
          problems: page.problems
          liftedKey: page.dragSrc && page.dragSrc.item ? page.dragSrc.item : ""
          highlight: page.dragTarget && page.dragTarget.screen !== undefined && page.dragVerdict ? page.dragVerdict.cells : []
        }

        // The dock.
        Item {
          id: dock
          x: scr.g ? scr.g.hotseat.x * scr.k : 0
          y: scr.g ? scr.g.hotseat.y * scr.k : 0
          width: scr.g ? scr.g.hotseat.w * scr.k : 0
          height: scr.g ? scr.g.hotseat.h * scr.k : 0
          readonly property real slot: width / Math.max(1, page.draft ? page.draft.hotseat_count : 4)
          Rectangle {
            anchors.fill: parent
            anchors.margins: 4 * scr.dp
            radius: height / 2.6
            color: Theme.withAlpha("#ffffff", 0.12)
          }
          Repeater {
            model: page.draft ? page.draft.hotseat : []
            HomeTile {
              id: dockTile
              required property var modelData
              x: (modelData.x || 0) * dock.slot
              width: dock.slot
              height: dock.height
              item: modelData
              icons: page.icons
              widgets: page.widgetMap
              m: scr.dockMetrics
              problem: page.problems[modelData._k] || ""
              lifted: !!page.dragSrc && page.dragSrc.item === modelData._k
              DragSource {
                anchors.fill: parent
                editor: page
                src: ({ item: dockTile.modelData._k })
                onTapped: if (dockTile.modelData.type === "folder") page.openFolderAt(dockTile.modelData._k)
              }
            }
          }
          Rectangle {
            visible: !!page.dragTarget && page.dragTarget.dock !== undefined
            x: (page.dragTarget && page.dragTarget.dock !== undefined ? page.dragTarget.dock : 0) * dock.slot + 2
            y: 2
            width: dock.slot - 4
            height: dock.height - 4
            radius: 8
            readonly property bool ok: !!page.dragVerdict && page.dragVerdict.ok
            color: Theme.withAlpha(ok ? Theme.accent : Theme.red, 0.28)
            border.width: 2
            border.color: ok ? Theme.accent : Theme.red
          }
        }

        // An open folder: its apps (drag them out) and its name (click to rename).
        MouseArea {
          anchors.fill: parent
          visible: !!page.openFolder && !page.dragSrc
          onClicked: page.folderKey = ""
          Rectangle { anchors.fill: parent; color: Theme.withAlpha("#000000", 0.35) }
        }
        Rectangle {
          id: folderPanel
          visible: !!page.openFolder
          opacity: page.dragSrc ? 0 : 1   // out of the way while one of its apps is dragged
          readonly property int cols: 3
          readonly property var kids: page.openFolder ? page.openFolder.items || [] : []
          readonly property real cell: (width - 24) / cols
          anchors.horizontalCenter: parent.horizontalCenter
          y: parent.height * 0.22
          width: parent.width * 0.86
          height: 52 + Math.ceil(Math.max(1, kids.length) / cols) * cell + 12
          radius: 18
          color: Theme.withAlpha(Theme.darkBackground, 0.94)
          border.width: 1
          border.color: Theme.muted
          MouseArea { anchors.fill: parent }   // clicks inside keep it open
          Rectangle {
            x: 14
            y: 12
            width: parent.width - 28
            height: 30
            radius: 6
            color: folderTitle.activeFocus ? Theme.darkerBackground : "transparent"
            border.width: 1
            border.color: folderTitle.activeFocus ? Theme.accent : Theme.withAlpha(Theme.muted, 0.6)
            TextInput {
              id: folderTitle
              anchors.fill: parent
              anchors.leftMargin: 10
              anchors.rightMargin: 10
              verticalAlignment: TextInput.AlignVCenter
              horizontalAlignment: TextInput.AlignHCenter
              color: Theme.brightForeground
              selectionColor: Theme.accent
              font.family: Theme.font
              font.pixelSize: Theme.body
              font.bold: true
              clip: true
              text: page.openFolder ? page.openFolder.title || "" : ""
              onTextEdited: if (page.folderKey) page.take(D.rename(page.draft, page.folderKey, text))
            }
          }
          Grid {
            x: 12
            y: 52
            columns: folderPanel.cols
            Repeater {
              model: folderPanel.kids
              HomeTile {
                id: kidTile
                required property var modelData
                width: folderPanel.cell
                height: folderPanel.cell
                item: modelData
                icons: page.icons
                widgets: page.widgetMap
                m: ({ icon: Math.min(scr.metrics.icon, folderPanel.cell * 0.55), label: Math.max(8, scr.metrics.label),
                      gap: Math.max(2, scr.metrics.gap), labels: true })
                problem: page.problems[modelData._k] || ""
                DragSource {
                  anchors.fill: parent
                  editor: page
                  src: ({ item: kidTile.modelData._k })
                }
              }
            }
          }
        }
      }
    }

    // What a drop here would do, or why it cannot.
    Txt {
      visible: !!page.draft
      anchors.horizontalCenter: parent.horizontalCenter
      y: phoneBody.y + phoneBody.height + 10
      width: centre.width - 40
      horizontalAlignment: Text.AlignHCenter
      wrapMode: Text.Wrap
      maximumLineCount: 2
      elide: Text.ElideRight
      text: page.dragVerdict ? (page.dragVerdict.message || (page.dragVerdict.ok ? "Drop to place it here" : ""))
        : page.openFolder ? "Drag an app out of the folder to move or remove it. Click outside to close."
        : "Drag to move · drop on an app to make a folder · click a folder to open it"
      color: page.dragVerdict && !page.dragVerdict.ok ? Theme.red : Theme.darkForeground
      font.pixelSize: Theme.small
    }
  }

  // What a drag carries, under the pointer.
  HomeTile {
    id: ghost
    visible: !!page.dragSrc
    z: 50
    readonly property var carried: page.carriedItem(page.dragSrc)
    readonly property var span: D.size(carried)
    width: span[0] * mainGrid.cellW
    height: span[1] * mainGrid.cellH
    x: page.dragPos.x - width / 2
    y: page.dragPos.y - height / 2
    item: carried
    icons: page.icons
    widgets: page.widgetMap
    m: scr.metrics
    opacity: 0.85
  }
}
