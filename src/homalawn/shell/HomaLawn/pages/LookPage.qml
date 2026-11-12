import QtQuick
import "../components"
import ".."
import "HomeDraft.js" as D

// The Wallpaper & style tab: where this phone's look comes from (follow Omarchy / a fixed theme /
// off), what the lock and home screens show (each set on its own panel: keep the current
// wallpaper, one of the theme's wallpapers with its crop, or a gradient made from the theme's
// colours; darken / lighten; parallax on the home screen, only with a theme wallpaper), what else
// to sync, and a live preview of both screens.
//
// The page edits a draft: every change updates the draft (so the preview moves at once) and
// nothing else. Apply in the bar at the bottom sends the draft with `look.set` (applied, one undo
// step); Discard puts the phone's settings back. A draft is kept per phone on the connection
// (backend.lookDrafts) until it is applied or discarded, so switching phone or leaving the page
// loses nothing and sends nothing. The saved settings (`saved`) follow what the service reports,
// also while a draft is open.
//
// The settings are lookconf's shape: {source, theme, wallpaper: {parallax, home: {kind, style,
// layer}, lock: {...}}, crops: {path: {home, lock}}, picks: {theme key: {home, lock, by}},
// sync: {colors, mode, icons}, mapping, icon_colors: {theme key: "#rrggbb"}}. A crop's lock null:
// the lock screen uses the home one. icon_colors: the monochrome icons' symbol colour picked for a
// theme; none means the theme's accent.
// (Before 2026-10-17 `wallpaper.same` made the home screen stand for both; a draft that still has
// it is read as two equal screens.)
Item {
  id: page

  // Given by HomaLawnView.
  property var backend: null
  property string phone: ""
  property string phoneName: ""
  property var phoneState: null
  property var host: null            // the view: holds the theme list, read once per window

  // The draft (what the page and preview show), the phone's saved settings and the phone they
  // belong to.
  property var look: null
  property var saved: null
  property string lookPhone: ""
  // Replies can arrive after the page is gone (the window closed); they check this first.
  readonly property var presence: ({ here: true })
  property bool applying: false      // look.set (Apply) on its way
  property bool retrying: false      // look.apply (Retry) on its way
  property bool overwriting: false   // look.apply with the consent to replace an unsaved wallpaper
  // Cancel on the overwrite question: hidden until that failure clears or Apply runs again.
  property bool overwriteDismissed: false
  // The theme grid shows two rows until asked for all.
  property bool allThemes: false
  // Scroll the settings column (the test harness uses it), kept within the content.
  function scrollTo(y) {
    flick.contentY = Math.max(0, Math.min(Number(y) || 0, flick.contentHeight - flick.height))
  }

  // The draft differs from the phone's saved settings: Apply and Discard are offered.
  readonly property bool changed: !!look && !!saved && same(look, saved) === false
  // How many settings differ (each leaf value once; who made a wallpaper pick is not a setting).
  readonly property int changes: changed ? Math.max(1, countDiff(look, saved)) : 0
  function countDiff(a, b) {
    var objA = a && typeof a === "object", objB = b && typeof b === "object"
    if (!objA && !objB) return canon(a) === canon(b) ? 0 : 1
    if (Array.isArray(a) || Array.isArray(b)) return canon(a) === canon(b) ? 0 : 1
    var keys = {}
    if (objA) for (var k in a) keys[k] = true
    if (objB) for (var k2 in b) keys[k2] = true
    var n = 0
    for (var key in keys) {
      if (key === "by") continue
      n += countDiff(objA ? a[key] : undefined, objB ? b[key] : undefined)
    }
    return n
  }
  readonly property var themes: host && host.themes ? host.themes : []
  readonly property var omarchy: backend && backend.omarchy ? backend.omarchy : ({})
  readonly property string source: look ? look.source : "off"
  // What the wallpaper and preview are built from; with the look off, what would be used.
  readonly property string basis: source !== "off" ? source : (look && look.theme ? "chosen" : "follow")
  readonly property var shownTheme: basis === "follow" ? themeBySlug(omarchy.theme) : themeByName(look ? look.theme : "")
  readonly property string shownThemeName: shownTheme ? shownTheme.name
    : (basis === "follow" ? (omarchy.theme || "") : (look && look.theme ? look.theme : ""))
  // The shown theme's key in `picks` (Omarchy's folder name).
  readonly property string themeKey: Paths.slug(shownThemeName)
  readonly property var screen: phoneState && phoneState.screen && phoneState.screen.w > 0
    ? phoneState.screen : ({ w: 1080, h: 2400 })
  readonly property var wallpaper: look && look.wallpaper ? look.wallpaper : defaultWallpaper()
  readonly property bool parallax: !!wallpaper.parallax
  // Parallax only counts while the home screen shows one of the theme's wallpapers: a gradient
  // goes at the screen's size and a kept wallpaper is left alone.
  readonly property bool parallaxOn: parallax && screenOf("home").kind === "wallpaper"
  readonly property var sync: look && look.sync ? look.sync : ({ colors: true, mode: true, icons: false })
  // ── Monochrome icons: one theme colour for the symbol, on a plate from the theme ─────────
  readonly property var themeColors: shownTheme && shownTheme.colors ? shownTheme.colors : ({})
  // The colours offered for the symbol, accent first (the service sorts and filters them; a
  // service from before them: the accent alone).
  readonly property var iconPalette: shownTheme && Array.isArray(shownTheme.icon_palette)
    ? shownTheme.icon_palette : (themeColors.accent ? [String(themeColors.accent).toLowerCase()] : [])
  // The plate behind the symbol (dark theme: its lighter background; light: the lighter of its
  // background and lighter background).
  readonly property string iconPlate: shownTheme && shownTheme.icon_plate ? shownTheme.icon_plate
    : String(themeColors.lighter_background || themeColors.background || "")
  // The symbol colour: the one picked for the shown theme, else its accent.
  readonly property string iconColor: {
    var v = look && look.icon_colors && themeKey ? look.icon_colors[themeKey] : ""
    return typeof v === "string" && v ? v : (iconPalette.length ? iconPalette[0] : "")
  }
  readonly property bool iconChoice: !!sync.icons && iconPalette.length > 0 && iconPlate !== ""
  // The picture a screen gets without a pick of its own: following, the desktop's background;
  // a fixed theme, its first wallpaper.
  readonly property string defaultPicture: basis === "follow" ? (omarchy.background || "")
    : (shownTheme && shownTheme.backgrounds && shownTheme.backgrounds.length ? shownTheme.backgrounds[0] : "")
  // The theme's wallpapers to choose from; following, Omarchy's background first (it may live
  // outside the theme), and a picture picked on the phone is always among them. One entry per
  // file name: Omarchy reports its background under its own state folder, the same picture as
  // the theme folder's copy, so that copy stands for it (moved to the front).
  readonly property var wallpaperPaths: {
    var list = shownTheme && shownTheme.backgrounds ? shownTheme.backgrounds.slice() : []
    var extra = [pickOf("lock"), pickOf("home")]
    if (basis === "follow") extra.push(omarchy.background)
    extra.forEach(b => {
      if (!b) return
      var at = list.findIndex(p => Paths.base(p) === Paths.base(b))
      var entry = at >= 0 ? list.splice(at, 1)[0] : b
      list.unshift(entry)
    })
    return list
  }
  // Two paths are the same picture: following, by file name (Omarchy's state folder holds a copy
  // of the theme's picture under the same name); a fixed theme, by path.
  function samePicture(a, b) {
    if (!a || !b) return false
    return basis === "follow" ? Paths.base(a) === Paths.base(b) : a === b
  }
  // How many home screen pages the phone has, as far as the window knows (the Home screen tab's
  // draft), else Lawnchair's smallest span, 4.
  readonly property int pageCount: {
    var d = backend && backend.homeDrafts && phone ? backend.homeDrafts[phone] : null
    return d && d.screens && d.screens.length ? d.screens.length : 4
  }

  readonly property bool busyHere: !!backend && (backend.busy || []).some(b => b.phone === page.phone)
  readonly property bool otherSource: !!phoneState && phoneState.id === phone
    && !!lookSource && !lookSource.self
  readonly property var lookSource: phoneState ? phoneState.look_source : null
  readonly property string sourceText: !lookSource ? "" : phoneName + " shows " + (lookSource.name || "another computer") + "'s wallpaper & style ("
    + (lookSource.scope === "chosen" ? "chosen on the phone" : "it changed last")
    + ")." + (phoneState.kept ? " This computer's is kept on the phone for later." : "")
  readonly property bool online: !!(phoneState && phoneState.connected)
  // The last apply to this phone failed (say, no photo access yet): the error line offers Retry,
  // which asks the service to apply the saved look again (follow: Omarchy's current one).
  readonly property bool canRetry: !!saved && saved.source !== "off" && !!phoneState
    && phoneState.id === phone && !!phoneState.error && !unsavedWallpaper
  // The last apply (Apply, or following Omarchy) stopped because the phone's current wallpaper
  // cannot be saved first: instead of an error the page asks whether to replace it.
  readonly property bool unsavedWallpaper: !!phoneState && phoneState.id === phone
    && phoneState.error_code === "wallpaper_unsaved"
  readonly property bool askOverwrite: unsavedWallpaper && !overwriteDismissed

  function themeBySlug(s) {
    if (!s) return null
    var want = Paths.slug(s)
    return themes.find(t => Paths.slug(t.name) === want || Paths.base(t.dir) === want) || null
  }
  function themeByName(n) { return n ? themeBySlug(n) : null }
  function copy(o) { return o ? JSON.parse(JSON.stringify(o)) : null }
  function toast(text) { if (backend) backend.toast(text) }
  // Settings compared by content, whatever order their keys come in.
  function canon(o) {
    if (Array.isArray(o)) return "[" + o.map(canon).join(",") + "]"
    if (o && typeof o === "object")
      return "{" + Object.keys(o).sort().map(k => JSON.stringify(k) + ":" + canon(o[k])).join(",") + "}"
    return JSON.stringify(o === undefined ? null : o)
  }
  function same(a, b) { return canon(a) === canon(b) }

  // ── What each screen shows ──────────────────────────────────────────────────────────────
  function defaultWallpaper() {
    var one = () => ({ kind: "wallpaper", style: null, layer: 0 })
    return { parallax: false, home: one(), lock: one() }
  }
  // The Screen settings `which` shows ({kind, style, layer}).
  function screenOf(which) {
    return wallpaper[which] || { kind: "keep", style: null, layer: 0 }
  }
  // The picture picked for the shown theme on that screen, if any.
  function pickOf(which) {
    var e = look && look.picks && themeKey ? look.picks[themeKey] : null
    var v = e ? e[which] : null
    return typeof v === "string" ? v : ""
  }
  function pickedOnPhone(which) {
    var e = look && look.picks && themeKey ? look.picks[themeKey] : null
    return !!e && e.by === "phone" && pickOf(which) !== ""
  }
  // The picture the phone's `which` screen gets: its pick or the default, a gradient's rendered
  // file (always the screen's size), or "" when the screen is kept or the look off.
  function pictureFor(which) {
    if (!look || source === "off") return ""
    var c = screenOf(which)
    if (c.kind === "gradient") return gradientPath(c.style, false)
    if (c.kind !== "wallpaper") return ""
    return pickOf(which) || defaultPicture
  }
  // Where that screen's frame sits on its picture (0..1, 0.5 centred), as the service cuts it.
  function cropFor(which) {
    if (screenOf(which).kind !== "wallpaper") return 0.5
    var path = pictureFor(which)
    var c = path && look && look.crops ? look.crops[path] : null
    if (!c) return 0.5
    if (which === "lock" && typeof c.lock === "number") return c.lock
    return typeof c.home === "number" ? c.home : 0.5
  }
  function layerFor(which) { return screenOf(which).layer || 0 }

  // ── Gradients (look.gradients), cached by theme|w|h ──────────────────────────────────────
  property var gradCache: ({})
  property var gradAsked: ({})
  function gradKey(wide) {
    if (!shownThemeName) return ""
    return shownThemeName + "|" + (wide ? 2 * screen.w : screen.w) + "|" + screen.h
  }
  function gradientsFor(wide) {
    var k = gradKey(wide)
    return k && gradCache[k] ? gradCache[k] : []
  }
  function gradientsLoading(wide) {
    var k = gradKey(wide)
    return !!k && !!gradAsked[k] && !gradCache[k]
  }
  function gradientPath(style, wide) {
    var hit = gradientsFor(wide).find(g => g.style === style)
    return hit ? hit.path : ""
  }
  function fetchGradients(key) {
    if (!key || gradAsked[key] || gradCache[key] || !backend) return
    var parts = key.split("|")
    var asked = Object.assign({}, gradAsked)
    asked[key] = true
    gradAsked = asked
    var presence = page.presence
    backend.call("look.gradients", { theme: parts[0], w: Number(parts[1]), h: Number(parts[2]) }, function (err, result) {
      if (!presence.here) return
      var cache = Object.assign({}, page.gradCache)
      cache[key] = err || !Array.isArray(result) ? [] : result
      page.gradCache = cache
      if (err) {
        var again = Object.assign({}, page.gradAsked)
        delete again[key]
        page.gradAsked = again
      }
    })
  }
  // The answer the page needs now: the screen's size (a gradient never scrolls).
  readonly property string wantedGradients: look && source !== "off" && backend && backend.connected
    ? gradKey(false) : ""
  onWantedGradientsChanged: Qt.callLater(() => {
    if (!page.presence.here) return
    page.wantedGradients.split("\n").forEach(k => page.fetchGradients(k))
  })

  // ── The draft ───────────────────────────────────────────────────────────────────────────
  function draftOf(who) {
    var all = backend && backend.lookDrafts ? backend.lookDrafts : {}
    return all[who] || null
  }
  // Keep the draft on the connection while it differs from the saved settings; drop it when not.
  function keepDraft() {
    if (!backend || !lookPhone || !saved || !look) return   // no baseline yet: leave it be
    var all = Object.assign({}, backend.lookDrafts || {})
    if (changed) all[lookPhone] = copy(look)
    else delete all[lookPhone]
    backend.lookDrafts = all
  }

  // Change the draft with fn(settings). Nothing is sent: Apply does that.
  function edit(fn) {
    if (!look) return
    var s = copy(look)
    fn(s)
    look = s
    keepDraft()
  }

  // New saved settings from the service: without a draft the page shows them; with one the
  // draft stays and only the baseline Apply and Discard compare against moves.
  function takeSaved(settings) {
    settings = twoScreens(copy(settings))
    if (!settings || (saved && same(settings, saved))) return
    var drafting = !!draftOf(lookPhone)
    saved = copy(settings)
    if (!drafting || !look) look = copy(settings)
    keepDraft()
  }

  // The phone's home screen for the preview (home.get; only a connected phone can be read): its
  // first page, dock, grid, icons and geometry. null while not known: the preview then shows the
  // wallpaper alone.
  property var homeLayout: null
  readonly property bool phoneOnline: !!phoneState && phoneState.id === phone && !!phoneState.connected
  // Offline: no layout (the page never shows an old read as the phone's).
  onPhoneOnlineChanged: if (phoneOnline) loadLayout(); else homeLayout = null
  function loadLayout() {
    homeLayout = null
    if (!backend || !phone || !phoneOnline) return
    var who = phone
    var presence = page.presence
    backend.call("home.get", { phone: who }, function (err, result) {
      if (!presence.here || page.phone !== who || err || !result) return
      var g = result.geometry
      if (!(g && g.screen && g.screen.w > 0 && g.workspace && g.hotseat && g.icon)) return
      var d = D.fromHome(result)
      page.homeLayout = {
        screen: d.screens[0], grid: d.grid, top_row_reserved: d.top_row_reserved, hotseat: d.hotseat,
        hotseat_count: d.hotseat_count, icon_scale: d.icon_scale, icons: result.icons || {}, geometry: g
      }
    })
  }

  function load() {
    loadLayout()
    lookPhone = phone
    overwriteDismissed = false
    saved = phoneState && phoneState.id === phone && phoneState.look ? twoScreens(copy(phoneState.look)) : null
    look = twoScreens(copy(draftOf(phone))) || copy(saved)
    keepDraft()
    if (!backend || !phone) return
    var who = phone
    var whoName = phoneName
    var presence = page.presence
    backend.call("look.get", { phone: who }, function (err, result) {
      if (!presence.here) return
      if (err) {
        page.toast("Could not read the wallpaper & style of " + whoName + ": " + (err.message || err.code))
        return
      }
      if (page.lookPhone === who && result) page.takeSaved(result)
    })
  }

  // Apply: save the draft on the service, which applies it to the phone (one undo step).
  function applyNow() {
    if (!changed || applying || !backend || !lookPhone) return false
    applying = true
    overwriteDismissed = false
    var who = lookPhone
    var whoName = phoneName
    var sent = copy(look)
    var presence = page.presence
    backend.call("look.set", { phone: who, settings: sent }, function (err, result) {
      if (!presence.here) return
      page.applying = false
      if (err) {
        if (err.code !== "wallpaper_unsaved") page.toast(err.message || err.code)   // the page asks instead
        return
      }
      if (page.lookPhone === who && result && result.look) {
        // The service's own copy of what was sent becomes both the saved settings and the draft.
        if (page.same(page.look, sent)) page.look = page.twoScreens(page.copy(result.look))
        page.saved = page.twoScreens(page.copy(result.look))
        page.keepDraft()
      }
      var text = result && result.text ? result.text
        : sent.source === "off" ? "Applied. Homa Lawn leaves " + whoName + "'s wallpaper & style alone."
        : "Applied. " + whoName + " gets it when it connects."
      page.toast(text)
    })
    return true
  }

  // Discard: the draft goes back to the phone's saved settings.
  function discard() {
    if (!changed) return false
    look = copy(saved)
    keepDraft()
    return true
  }

  // The error line's Retry: apply this phone's saved look once more.
  function retryNow() {
    if (!canRetry || retrying || busyHere) return false
    retrying = true
    var who = lookPhone
    var presence = page.presence
    backend.call("look.apply", { phone: who }, function (err, result) {
      if (!presence.here) return
      page.retrying = false
      if (err && err.code === "wallpaper_unsaved") return   // the page asks instead
      page.toast(err ? (err.message || err.code) : (result && result.text ? result.text : "Applied."))
    })
    return true
  }

  // The overwrite question's Replace: apply the saved look again, replacing the current
  // wallpaper without saving it first (the look's undo step still holds the colours and mode).
  function overwriteNow() {
    if (!unsavedWallpaper || overwriting || busyHere || !backend) return false
    overwriting = true
    var who = lookPhone
    var presence = page.presence
    backend.call("look.apply", { phone: who, overwrite_wallpaper: true }, function (err, result) {
      if (!presence.here) return
      page.overwriting = false
      page.toast(err ? (err.message || err.code) : (result && result.text ? result.text : "Applied."))
    })
    return true
  }

  // Its Cancel: leave the phone's wallpaper as it is.
  function cancelOverwrite() {
    if (!askOverwrite) return false
    overwriteDismissed = true
    return true
  }

  // ── Changes to the draft (the controls and the harness call these) ───────────────────────
  // Settings from before 2026-10-17's "Same on both" (`wallpaper.same` true; a kept draft, or a
  // service not yet restarted) as two equal screens, as the service reads them: the lock screen
  // gets the home screen's choice, picks and crops; a lock-only pick goes.
  function twoScreens(s) {
    if (!s || !s.wallpaper || s.wallpaper.same === undefined) return s
    if (s.wallpaper.same === true) {
      s.wallpaper.lock = copy(s.wallpaper.home)
      for (var t in s.picks || {}) {
        if (!s.picks[t]) continue
        s.picks[t].lock = s.picks[t].home
        if (!s.picks[t].home) delete s.picks[t]
      }
      for (var k in s.crops || {}) if (s.crops[k]) s.crops[k].lock = null
    }
    delete s.wallpaper.same
    return s
  }
  // A draft from before the wallpaper settings existed gets them.
  function ensure(s) {
    if (!s.wallpaper) s.wallpaper = defaultWallpaper()
    if (!s.picks) s.picks = {}
    if (!s.crops) s.crops = {}
    if (!s.sync) s.sync = { colors: true, mode: true, icons: false }
    if (!s.icon_colors || typeof s.icon_colors !== "object") s.icon_colors = {}
  }
  function editWall(fn) {
    edit(s => { ensure(s); twoScreens(s); fn(s) })
  }

  function chooseSource(key) {
    if (["follow", "chosen", "off"].indexOf(key) < 0) return
    if (key === "chosen" && !(look && look.theme)) {
      var start = themeBySlug(omarchy.theme) || themes[0]
      if (!start) {
        toast("No Omarchy theme was found to choose from.")
        return
      }
      edit(s => { s.source = "chosen"; s.theme = start.name })
      return
    }
    edit(s => { s.source = key })
  }

  function chooseTheme(t) {
    if (!t || !t.name) return
    edit(s => { s.source = "chosen"; s.theme = t.name })
  }

  function panelOf(which) { return which === "lock" ? "lock" : "home" }

  // What a panel's screen shows: keep, wallpaper or gradient.
  function setKind(which, kind) {
    if (["keep", "wallpaper", "gradient"].indexOf(kind) < 0) return
    editWall(s => {
      var c = s.wallpaper[panelOf(which)]
      c.kind = kind
      c.style = kind === "gradient" ? (c.style || "fade") : null
    })
  }
  function setStyle(which, style) {
    if (!style) return
    editWall(s => {
      var c = s.wallpaper[panelOf(which)]
      c.kind = "gradient"
      c.style = String(style)
    })
  }

  // One of the theme's wallpapers for a panel's screen, for this phone only (the same pick the
  // phone's own screen makes). The default picture is no pick: following, the desktop's own
  // background then goes on deciding.
  function choosePicture(which, path) {
    if (!themeKey || !path) return
    var key = themeKey
    var fallback = defaultPicture
    editWall(s => {
      var at = panelOf(which)
      s.wallpaper[at].kind = "wallpaper"
      s.wallpaper[at].style = null
      var e = s.picks[key] ? s.picks[key] : { home: null, lock: null, by: "computer" }
      e[at] = page.samePicture(path, fallback) ? null : String(path)
      e.by = "computer"
      if (!e.home && !e.lock) delete s.picks[key]
      else s.picks[key] = e
    })
  }

  // Move a panel's frame on the picture it shows now (remembered per picture and per screen).
  function setCrop(which, offset) {
    if (screenOf(which).kind !== "wallpaper") return
    var path = pictureFor(which)
    var v = Math.max(0, Math.min(1, Number(offset)))
    if (!path || isNaN(v)) return
    editWall(s => {
      var c = s.crops[path] || { home: 0.5, lock: null }
      if (typeof c.home !== "number") c.home = 0.5
      if (c.lock === undefined) c.lock = null
      if (which !== "lock") {
        if (c.lock === null) c.lock = c.home   // the lock screen stays put
        c.home = v
      } else {
        c.lock = v
      }
      s.crops[path] = c
    })
  }

  function setLayer(which, value) {
    var v = Math.max(-100, Math.min(100, Math.round(Number(value) || 0)))
    editWall(s => { s.wallpaper[panelOf(which)].layer = v })
  }

  function setParallax(on) { editWall(s => { s.wallpaper.parallax = !!on }) }

  function setSync(key, on) {
    if (["colors", "mode", "icons"].indexOf(key) < 0) return
    edit(s => { ensure(s); s.sync[key] = !!on })
  }

  // The monochrome icons' symbol colour for the shown theme (a draft, as everything here). The
  // accent is the default and is stored as no choice, so picking it again leaves nothing changed.
  function chooseIconColor(hex) {
    var c = String(hex || "").toLowerCase()
    if (!themeKey || !/^#[0-9a-f]{6}$/.test(c)) return
    var key = themeKey
    var accent = iconPalette.length ? iconPalette[0] : ""
    edit(s => {
      ensure(s)
      if (c === accent) delete s.icon_colors[key]
      else s.icon_colors[key] = c
    })
  }

  // HomaLawnView sets backend, phone, phoneState and host one by one after the page is made.
  onPhoneChanged: load()
  onBackendChanged: if (lookPhone && !look) load()
  onUnsavedWallpaperChanged: if (!unsavedWallpaper) overwriteDismissed = false
  onPhoneStateChanged: {
    if (phoneState && phoneState.id === lookPhone && phoneState.look) {
      if (!look) {
        saved = twoScreens(copy(phoneState.look))
        look = twoScreens(copy(draftOf(lookPhone))) || copy(saved)
        keepDraft()
      } else {
        takeSaved(phoneState.look)
      }
    }
  }
  onHostChanged: if (host && typeof host.loadThemes === "function") host.loadThemes()
  Component.onDestruction: presence.here = false

  // ── Pieces ──────────────────────────────────────────────────────────────────────────────
  component Small: Txt {
    width: parent ? parent.width : implicitWidth
    font.pixelSize: Theme.small
    color: Theme.darkForeground
    wrapMode: Text.Wrap
  }

  component SyncRow: Item {
    id: row
    property string text: ""
    property string hint: ""
    property string key: ""
    property bool last: false
    width: parent ? parent.width : 0
    height: 34
    Row {
      anchors.left: parent.left
      anchors.right: sw.left
      anchors.rightMargin: 12
      anchors.verticalCenter: parent.verticalCenter
      spacing: 8
      clip: true
      Txt { id: rowText; text: row.text; color: Theme.brightForeground }
      Txt {
        visible: row.hint !== ""
        anchors.baseline: rowText.baseline
        text: row.hint
        font.pixelSize: Theme.small
        color: Theme.darkForeground
      }
    }
    Toggle {
      id: sw
      anchors.right: parent.right
      anchors.verticalCenter: parent.verticalCenter
      checked: !!page.sync[row.key]
      onToggled: checked => page.setSync(row.key, checked)
    }
    Rectangle {
      visible: !row.last
      anchors.left: parent.left
      anchors.right: parent.right
      anchors.bottom: parent.bottom
      height: 1
      color: Theme.selection
    }
  }

  // ── Status lines under the header ─────────────────────────────────────────────────────
  Column {
    id: status
    anchors.left: parent.left
    anchors.right: parent.right
    anchors.top: parent.top

    // The current wallpaper cannot be backed up first: ask before replacing it.
    Item {
      visible: page.askOverwrite
      width: status.width
      height: visible ? overwriteBar.height + 16 : 0
      ConfirmBar {
        id: overwriteBar
        x: Theme.margin
        y: 8
        width: parent.width - 2 * Theme.margin
        text: "Replace current wallpaper?"
        detail: "The wallpaper set on " + page.phoneName + " can't be backed up, so Undo can't bring it back."
        action: "Replace"
        destructive: true
        working: page.overwriting
        active: !page.busyHere && page.online
        onConfirmed: page.overwriteNow()
        onCancelled: page.cancelOverwrite()
      }
    }
    StatusLine {
      visible: !!(page.phoneState && page.phoneState.error) && !page.unsavedWallpaper
      tone: Theme.red
      text: page.phoneState && page.phoneState.error ? String(page.phoneState.error) : ""
      action: page.canRetry && page.online ? "Retry" : ""
      actionWorking: page.retrying
      actionActive: !page.applying && !page.busyHere
      onClicked: page.retryNow()
    }
    StatusLine { visible: page.otherSource; tone: Theme.yellow; text: page.sourceText }
  }

  ApplyBar {
    id: applyBar
    visible: !!page.look
    changes: page.changes
    applying: page.applying || page.overwriting
    canApply: !page.busyHere
    failed: page.unsavedWallpaper || !!(page.phoneState && page.phoneState.error)
    onApply: page.applyNow()
    onDiscard: page.discard()
  }

  // ── Settings column ─────────────────────────────────────────────────────────────────────
  Flickable {
    id: flick
    anchors.left: parent.left
    anchors.right: previewPane.left
    anchors.top: status.bottom
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
      width: flick.width - 2 * x
      spacing: Theme.gap

      Txt {
        visible: !page.look
        text: "Reading the wallpaper & style of " + page.phoneName + "…"
        color: Theme.darkForeground
      }

      // 1. Theme.
      Card {
        visible: !!page.look
        title: "Source"
        Segmented {
          options: [{ key: "follow", label: "Follow Omarchy" }, { key: "chosen", label: "Fixed theme" },
                    { key: "off", label: "Off" }]
          current: page.source
          onPicked: key => page.chooseSource(key)
        }
        Small {
          text: page.source === "follow"
            ? "The phone takes the theme and wallpaper you use on this computer, even with this window closed."
            : page.source === "chosen" ? "The phone keeps the theme you pick here, whatever the computer uses."
            : "Homa Lawn leaves this phone's wallpaper & style alone."
        }

        // Follow: what Omarchy uses now.
        Rectangle {
          id: nowCard
          visible: page.source === "follow"
          width: Math.min(parent.width, 320)
          height: visible ? 64 : 0
          radius: 5
          color: Theme.lighterBackground
          border.width: 1
          border.color: Theme.muted
          readonly property var colors: page.shownTheme && page.shownTheme.colors ? page.shownTheme.colors : ({})
          readonly property var swatches: ["background", "foreground", "accent", "red", "yellow", "green",
                                           "cyan", "blue", "magenta"].map(k => colors[k]).filter(c => !!c)
          Txt {
            x: 12
            y: 10
            width: parent.width - 24
            elide: Text.ElideRight
            text: "Now: " + (page.shownTheme ? page.shownTheme.name : (page.omarchy.theme || "no theme reported"))
            font.bold: true
            color: Theme.brightForeground
          }
          Row {
            x: 12
            y: 36
            width: parent.width - 24
            height: 16
            Repeater {
              model: nowCard.swatches
              Rectangle {
                required property var modelData
                width: (nowCard.width - 24) / Math.max(1, nowCard.swatches.length)
                height: 16
                color: modelData
              }
            }
            Txt {
              visible: nowCard.swatches.length === 0
              text: "This theme has no color palette."
              font.pixelSize: Theme.tiny
              color: Theme.darkForeground
            }
          }
        }

        // Fixed: the theme gallery.
        Item {
          visible: page.source === "chosen"
          width: parent.width
          height: visible ? themeGrid.height + (moreThemes.visible ? moreThemes.height + 6 : 0) : 0
          Txt {
            id: moreThemes
            visible: page.themes.length > themeGrid.cols * 2
            anchors.top: themeGrid.bottom
            anchors.topMargin: 6
            text: page.allThemes ? "Show fewer themes" : "Show all " + page.themes.length + " themes"
            font.pixelSize: Theme.small
            color: moreHover.containsMouse ? Theme.brightForeground : Theme.accent
            MouseArea {
              id: moreHover
              anchors.fill: parent
              hoverEnabled: true
              cursorShape: Qt.PointingHandCursor
              onClicked: page.allThemes = !page.allThemes
            }
          }
          Txt {
            visible: page.themes.length === 0
            text: "Loading the themes…"
            font.pixelSize: Theme.small
            color: Theme.darkForeground
          }
          Grid {
            id: themeGrid
            width: parent.width
            readonly property int cols: Math.max(2, Math.min(5, Math.floor((width + 8) / 128)))
            // Two rows, unless all are asked for; the chosen theme is always among them.
            readonly property var shown: {
              var all = page.themes
              var room = cols * 2
              if (page.allThemes || all.length <= room) return all
              var first = all.slice(0, room)
              var pick = page.source === "chosen" && page.shownTheme ? page.shownTheme.name : ""
              if (pick && !first.some(t => t.name === pick)) first[room - 1] = page.shownTheme
              return first
            }
            columns: cols
            spacing: 8
            Repeater {
              model: themeGrid.shown
              ThemeCard {
                required property var modelData
                width: (themeGrid.width - (themeGrid.cols - 1) * 8) / themeGrid.cols
                theme: modelData
                selected: page.source === "chosen" && !!page.shownTheme && page.shownTheme.name === modelData.name
                onClicked: page.chooseTheme(modelData)
              }
            }
          }
        }
      }

      // 2. Wallpaper.
      Card {
        id: wallCard
        visible: !!page.look && page.source !== "off"
        title: "Wallpaper"
        // Two panels side by side from a 720 px column on, while their source tabs still fit.
        readonly property bool sideBySide: cards.width >= 720 && (cards.width - 36 - 12) / 2 >= lockPanel.minWidth
        // Lock screen left (as in the previews), home screen right; stacked when narrow.
        Flow {
          width: parent.width
          spacing: 12
          readonly property real panelW: wallCard.sideBySide ? (width - spacing) / 2 : width
          WallpaperPanel {
            id: lockPanel
            width: parent.panelW
            owner: page
            panel: "lock"
            title: "Lock screen"
            showParallax: false
            framed: true
            compact: wallCard.sideBySide
            cropHeight: wallCard.sideBySide ? 220 : 260
          }
          WallpaperPanel {
            width: parent.panelW
            owner: page
            panel: "home"
            title: "Home screen"
            showParallax: true
            framed: true
            compact: wallCard.sideBySide
            cropHeight: wallCard.sideBySide ? 220 : 260
          }
        }
      }

      // 3. Colors & icons.
      Card {
        visible: !!page.look && page.source !== "off"
        title: "Colors & icons"
        SyncRow { text: "Launcher colors"; hint: "(accent, folders, dock, app list)"; key: "colors" }
        SyncRow { text: "Light / dark from the theme"; key: "mode" }
        SyncRow { text: "Monochrome icons"; hint: "(one theme color on a plain plate)"; key: "icons"; last: !iconRow.visible }
        // The symbol's colour, from the theme's palette; the plate comes with the theme.
        Item {
          id: iconRow
          visible: page.iconChoice
          width: parent.width
          height: visible ? iconCol.implicitHeight + 6 : 0
          Column {
            id: iconCol
            width: parent.width
            spacing: 8
            Row {
              spacing: 10
              // What one icon will look like.
              Rectangle {
                width: 30
                height: 30
                radius: 15
                color: page.iconPlate || "transparent"
                border.width: 1
                border.color: Theme.withAlpha(Theme.muted, 0.8)
                Glyph {
                  anchors.centerIn: parent
                  code: 0xF03F2           // phone
                  size: 16
                  color: page.iconColor || Theme.foreground
                }
              }
              Column {
                anchors.verticalCenter: parent.verticalCenter
                spacing: 1
                Txt { text: "Icon color"; color: Theme.brightForeground }
                Txt {
                  text: page.iconColor === (page.iconPalette[0] || "") ? "The theme's accent" : page.iconColor
                  font.pixelSize: Theme.tiny
                  color: Theme.darkForeground
                }
              }
            }
            Flow {
              id: swatches
              width: parent.width
              spacing: 6
              Repeater {
                model: page.iconPalette
                Rectangle {
                  id: swatch
                  required property var modelData
                  required property int index
                  readonly property bool chosen: page.iconColor === modelData
                  width: 26
                  height: 26
                  radius: 13
                  color: "transparent"
                  border.width: 2
                  border.color: chosen ? Theme.brightForeground : (swatchHover.containsMouse ? Theme.muted : "transparent")
                  Rectangle {
                    anchors.centerIn: parent
                    width: 18
                    height: 18
                    radius: 9
                    color: swatch.modelData
                    border.width: 1
                    border.color: Theme.withAlpha("#000000", 0.25)
                  }
                  MouseArea {
                    id: swatchHover
                    anchors.fill: parent
                    hoverEnabled: true
                    cursorShape: Qt.PointingHandCursor
                    onClicked: page.chooseIconColor(swatch.modelData)
                  }
                }
              }
            }
            Small {
              text: "The plate behind it comes from the theme. Remembered for " + (page.shownThemeName || "this theme") + "."
            }
          }
        }
        Small {
          visible: !!page.shownTheme && !(page.shownTheme.colors && (page.shownTheme.colors.background || page.shownTheme.colors.accent))
          text: "This theme has no color palette: only its wallpaper goes to the phone."
          color: Theme.yellow
        }
      }
    }
  }

  // A thin scroll position mark.
  Rectangle {
    visible: flick.interactive
    x: flick.x + flick.width - 7
    y: flick.y + flick.visibleArea.yPosition * flick.height
    width: 4
    height: flick.visibleArea.heightRatio * flick.height
    radius: 2
    color: Theme.withAlpha(Theme.muted, 0.8)
  }

  // ── Preview ──────────────────────────────────────────────────────────────────────────────
  Rectangle {
    id: previewPane
    anchors.right: parent.right
    anchors.top: status.bottom
    anchors.bottom: applyBar.top
    width: page.width >= 1100 ? 340 : 280
    color: Theme.darkBackground

    readonly property bool off: page.source === "off"
    // Off: the phone keeps its own colours, so the previews use the window's neutral ones.
    readonly property var colors: !off && page.shownTheme && page.shownTheme.colors && page.sync.colors ? page.shownTheme.colors : ({})
    readonly property bool homeKept: off || page.screenOf("home").kind === "keep"
    readonly property bool lockKept: off || page.screenOf("lock").kind === "keep"
    readonly property bool wide: !off && page.parallaxOn
    readonly property bool mono: !off && page.iconChoice
    // Light or dark as the phone will be: the theme's, with "Light / dark from the theme" on;
    // else the phone keeps its own, drawn dark.
    readonly property bool light: !off && !!page.sync.mode && page.themeColors.mode === "light"
    // The dock: the theme's background (launcher colours), else the launcher's own light or dark one.
    readonly property color dockColor: colors.background ? Theme.withAlpha(Qt.color(colors.background), 0.6)
      : light ? Qt.rgba(1, 1, 1, 0.55) : Qt.rgba(0, 0, 0, 0.35)

    Rectangle { width: 1; height: parent.height; color: Theme.selection }

    Column {
      id: previewColumn
      anchors.horizontalCenter: parent.horizontalCenter
      y: 18
      width: parent.width - 36
      spacing: 12

      Heading {
        anchors.horizontalCenter: parent.horizontalCenter
        text: "Preview"
      }

      Row {
        id: phones
        anchors.horizontalCenter: parent.horizontalCenter
        spacing: 12
        // Two phones side by side, as tall as the pane allows (room left for the captions, the
        // parallax strip and the line under them).
        readonly property real fitW: (previewColumn.width - spacing) / 2
        readonly property real fitH: (previewPane.height - 190) * page.screen.w / page.screen.h
        readonly property real phoneW: Math.max(80, Math.min(140, fitW, fitH))
        // Lock screen left, home screen right: the phone's own order.
        PhonePreview {
          width: phones.phoneW
          caption: "Lock screen"
          lock: true
          kept: previewPane.lockKept
          keptNote: previewPane.off ? "" : "Kept as it is"
          source: page.pictureFor("lock")
          screenW: page.screen.w
          screenH: page.screen.h
          offset: page.cropFor("lock")
          amount: page.layerFor("lock")
          stripRoom: previewPane.wide
        }
        PhonePreview {
          width: phones.phoneW
          caption: "Home screen"
          kept: previewPane.homeKept
          keptNote: previewPane.off ? "" : "Kept as it is"
          source: page.pictureFor("home")
          screenW: page.screen.w
          screenH: page.screen.h
          offset: page.cropFor("home")
          amount: page.layerFor("home")
          span: previewPane.wide ? 2 : 1
          pageCount: page.pageCount
          accent: previewPane.colors.accent || Theme.accent
          dockColor: previewPane.dockColor
          mono: previewPane.mono
          layout: page.homeLayout
          iconColor: page.iconColor || Theme.accent
          iconPlate: page.iconPlate || Theme.lighterBackground
        }
      }

      // Off: the Theme card already says so in its one line; nothing to add here.
      Txt {
        visible: !previewPane.off
        width: parent.width
        horizontalAlignment: Text.AlignHCenter
        wrapMode: Text.Wrap
        font.pixelSize: Theme.small
        color: Theme.darkForeground
        text: (previewPane.wide ? "The home screen shows its first page; the strip is the whole wide picture, a line where each page starts. " : "")
            + "Changes show here at once and reach the phone when you press Apply."
      }
    }
  }
}
