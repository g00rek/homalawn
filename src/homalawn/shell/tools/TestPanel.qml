import QtQuick
import Quickshell
import ".."
import "../HomaLawn/pages/HomeDraft.js" as D
import "../HomaLawn/Status.js" as Status

// The Homa Lawn window as the offscreen harness loads it, with three extra functions the harness
// reaches through `qs ipc call shell call homalawn FUNCTION ARG`. Results that take time are
// written to the shell log as lines starting with "homalawn test:".
Panel {
  id: harness

  // "look:/path/shot.png" — show that section, let it settle, save the window's content as PNG.
  property var shot: null

  function snapshot(arg) {
    var text = String(arg || "")
    var colon = text.indexOf(":")
    var tab = colon > 0 ? text.slice(0, colon) : ""
    var file = colon > 0 ? text.slice(colon + 1) : ""
    if (sectionKeys.indexOf(tab) < 0 || !file) return "usage: " + sectionKeys.join("|") + ":FILE.png"
    if (!opened) open(JSON.stringify({ page: tab }))
    else if (view) view.showPage(tab)
    shot = { tab: tab, file: file, waited: 0 }
    shotDelay.restart()
    return "pending"
  }

  function takeShot() {
    var wanted = shot
    if (!wanted) return
    if (!view || view.width <= 0) {
      wanted.waited += 1
      if (wanted.waited < 20) shotDelay.restart()
      else console.log("homalawn test: snapshot failed (no view) " + wanted.file)
      return
    }
    shot = null
    view.grabToImage(function (picture) {
      var saved = picture.saveToFile(wanted.file)
      console.log("homalawn test: snapshot " + (saved ? "saved " : "failed ") + wanted.file)
    })
  }

  Timer {
    id: shotDelay
    interval: 800
    onTriggered: harness.takeShot()
  }

  // What the window currently shows, as JSON.
  function facts() {
    var b = homalawn ? homalawn.backend : null
    return JSON.stringify({
      opened: opened,
      section: view ? view.section : lastSection,
      shown: view ? view.shown : "",
      railOpen: view ? view.railOpen : lastRailOpen,
      railExpanded: view ? view.railExpanded : lastRailOpen,
      drafts: view ? ["overview", "home", "look", "search"].filter(k => view.draftIn(k)) : [],
      offline: view ? view.offlineHere : false,
      phone: view ? view.phone : lastPhone,
      serviceUp: view ? view.serviceUp : !!(b && b.connected),
      serviceDown: view ? view.serviceDown : !!(b && b.attempted && !b.connected),
      phones: b ? b.phones.map(p => ({ id: p.id, name: p.name, connected: !!p.connected })) : [],
      serving: b && b.state ? b.state.serving || null : null,
      themeChars: homalawn ? homalawn.themeText.length : 0
    })
  }

  // Send one request with no parameters through the panel's connection; the answer goes to
  // the log ("homalawn test: probe METHOD ok …" or "… CODE message").
  function probe(method) {
    var target = homalawn || findService()
    if (!target) return "no service"
    var name = String(method || "")
    target.backend.call(name, {}, function (err, result) {
      if (err) console.log("homalawn test: probe " + name + " " + err.code + " " + err.message)
      else console.log("homalawn test: probe " + name + " ok " + JSON.stringify(result).slice(0, 300))
    })
    return "sent"
  }

  // "open" / "closed" — the ☰ button: the rail with labels, or icons only. In a window too narrow
  // for both, "open" lays the labelled rail over the content (as ☰ does there).
  function rail(arg) {
    if (!view) return "window closed"
    var open = String(arg) === "open"
    if (view.railRoomy || !open) { view.railOpen = open; view.railPeek = false }
    else view.railPeek = true
    return view.railExpanded ? "open" : "closed"
  }

  // Raise a confirm question without answering it (for a picture): "restore NAME" on History,
  // "update" on Overview, "restart" on Service; "cancel" closes whichever is open.
  function ask(arg) {
    var p = view ? view.page : null
    if (!p) return "nothing shown"
    var text = String(arg || "")
    if (text === "cancel") {
      if (p.hasOwnProperty("confirming")) p.confirming = ""
      if (p.hasOwnProperty("askingUpdate")) p.askingUpdate = false
      if (p.hasOwnProperty("askingUnpair")) p.askingUnpair = false
      if (p.hasOwnProperty("askingRestart")) p.askingRestart = false
      return "ok"
    }
    if (text.indexOf("restore ") === 0 && p.hasOwnProperty("confirming")) { p.confirming = text.slice(8); return "ok" }
    if (text === "update" && p.hasOwnProperty("askingUpdate")) { p.askingUpdate = true; return "ok" }
    if (text === "restart" && p.hasOwnProperty("askingRestart")) { p.askingRestart = true; return "ok" }
    return "refused"
  }

  // Choose a phone in the rail by id.
  function pick(id) {
    if (!view) return "window closed"
    view.phone = String(id || "")
    return view.phone
  }

  // What the Overview knows of the selected phone (phone.info), as JSON in the log.
  function info() {
    if (!view) return "window closed"
    view.loadInfo()
    infoDelay.restart()
    return "pending"
  }
  Timer {
    id: infoDelay
    interval: 1500
    onTriggered: console.log("homalawn test: info " + JSON.stringify(harness.view ? harness.view.info : null))
  }

  // History: the listed snapshots ("rows") or press Restore and its confirm on one row by name
  // ("restore NAME"). The answer goes to the log ("homalawn test: history …").
  function history(arg) {
    var p = view && view.shown === "history" ? view.page : null
    if (!p) return "History not shown"
    var text = String(arg || "rows")
    if (text === "rows") {
      console.log("homalawn test: history rows " + JSON.stringify(p.rows))
      return String(p.rows.length)
    }
    var name = text.replace(/^restore /, "")
    p.confirming = name
    var ok = p.restore(name)
    console.log("homalawn test: history restore " + name + " " + (ok ? "started" : "refused"))
    return ok ? "started" : "refused"
  }

  // Press the header's Undo button; the answer goes to the log ("homalawn test: undo started|refused").
  function undo() {
    if (!view) return "window closed"
    var ok = view.undoNow()
    console.log("homalawn test: undo " + (ok ? "started" : "refused"))
    return ok ? "started" : "refused"
  }

  // Press the Look tab's Retry (on the error line); the log says "homalawn test: retry started|refused".
  function retry() {
    var p = view ? view.lookPage : null
    if (!p) return "Look tab not shown"
    var ok = p.retryNow()
    console.log("homalawn test: retry " + (ok ? "started" : "refused"))
    return ok ? "started" : "refused"
  }

  // Answer the Look tab's "Replace current wallpaper?" question: "yes" presses Replace, "no"
  // presses Cancel; the log says "homalawn test: overwrite yes|no started|refused".
  function overwrite(arg) {
    var p = view ? view.lookPage : null
    if (!p) return "Look tab not shown"
    var yes = String(arg || "yes") !== "no"
    var ok = yes ? p.overwriteNow() : p.cancelOverwrite()
    console.log("homalawn test: overwrite " + (yes ? "yes " : "no ") + (ok ? "started" : "refused"))
    return ok ? "started" : "refused"
  }

  // "900x640" — resize the window (the view follows); "" or "default" — back to 1180x760.
  function size(arg) {
    var m = String(arg || "").match(/^(\d+)x(\d+)$/)
    var w = m ? Number(m[1]) : 1180, h = m ? Number(m[2]) : 760
    window.implicitWidth = w
    window.implicitHeight = h
    // Offscreen, the window keeps its first size; size the view's loader instead (the view is
    // laid out and photographed at exactly this size either way).
    if (view && view.parent) {
      var holder = view.parent
      holder.anchors.fill = undefined
      holder.width = w
      holder.height = h
    }
    return w + "x" + h
  }

  // The Look tab's own state, as JSON: its settings copy, what each screen shows and what it
  // waits for. pictures: the file each screen gets ("" kept / off); crops: the frame's place.
  function lookFacts() {
    var p = view ? view.lookPage : null
    if (!p) return JSON.stringify({ look: null, why: view ? "Look tab not shown" : "window closed" })
    var both = f => ({ home: f("home"), lock: f("lock") })
    return JSON.stringify({
      phone: p.lookPhone, source: p.source, basis: p.basis,
      theme: p.shownTheme ? p.shownTheme.name : null, parallax: p.parallax, parallaxOn: p.parallaxOn,
      pageCount: p.pageCount,
      icons: { choice: p.iconChoice, palette: p.iconPalette, plate: p.iconPlate, color: p.iconColor },
      screens: both(w => p.screenOf(w)), pictures: both(w => p.pictureFor(w)),
      crops: both(w => p.cropFor(w)), layers: both(w => p.layerFor(w)),
      gradients: { screen: p.gradientsFor(false).length, wide: p.gradientsFor(true).length },
      changed: p.changed, applying: p.applying,
      canRetry: p.canRetry, retrying: p.retrying, askOverwrite: p.askOverwrite,
      overwriting: p.overwriting, errorCode: p.phoneState ? p.phoneState.error_code || null : null,
      applyActive: view.lookPage === p && p.changed && !view.busyEntry,
      error: p.phoneState ? p.phoneState.error : null,
      themes: p.themes.length, size: [view.width, view.height], look: p.look, saved: p.saved,
      previewHome: p.homeLayout ? { items: p.homeLayout.screen ? p.homeLayout.screen.items.length : 0,
                                    dock: p.homeLayout.hotseat.length } : null
    })
  }

  // Change the Look tab's draft the way its controls do, e.g.
  //   {"source":"follow"|"chosen"|"off"} {"theme":"Tokyo Night"}
  //   {"kind":{"home":"gradient"}} {"style":{"lock":"glow"}} {"picture":{"home":PATH}}
  //   {"crop":{"home":0.2}} {"layer":{"lock":30}} {"parallax":true} {"sync":{"icons":true}}
  //   {"scroll":400} (move the settings column)  {"allThemes":true}
  //   {"iconColor":"#9ece6a"} (a swatch under Monochrome icons)
  // Edits only change the draft;
  // nothing is sent until the Apply bar's Apply is pressed:
  //   {"apply":true} — press Apply (the log says "homalawn test: apply started|refused")
  //   {"discard":true} — press Discard
  function lookEdit(arg) {
    var p = view ? view.lookPage : null
    if (!p || !p.look) return "Look tab not ready"
    var e
    try { e = JSON.parse(String(arg || "{}")) } catch (err) { return "not JSON" }
    console.log("homalawn test: lookEdit " + JSON.stringify(e))
    if (e.source) p.chooseSource(e.source)
    if (e.theme) p.chooseTheme(p.themeByName(e.theme) || { name: e.theme })
    if (e.kind) for (var k in e.kind) p.setKind(k, e.kind[k])
    if (e.style) for (var st in e.style) p.setStyle(st, e.style[st])
    if (e.picture) for (var pi in e.picture) p.choosePicture(pi, e.picture[pi])
    if (e.crop) for (var c in e.crop) p.setCrop(c, Number(e.crop[c]))
    if (e.layer) for (var l in e.layer) p.setLayer(l, Number(e.layer[l]))
    if (e.parallax !== undefined) p.setParallax(!!e.parallax)
    if (e.sync) for (var sk in e.sync) p.setSync(sk, !!e.sync[sk])
    if (e.iconColor) p.chooseIconColor(e.iconColor)
    if (e.allThemes !== undefined) p.allThemes = !!e.allThemes
    if (e.scroll !== undefined) p.scrollTo(e.scroll)
    if (e.discard) {
      var d = p.discard()
      console.log("homalawn test: discard " + (d ? "done" : "refused"))
      return d ? "discarded" : "refused"
    }
    if (e.apply) {
      // Only what the Apply bar's Apply would take: enabled while the draft differs.
      var ok = !view.busyEntry && p.applyNow()
      console.log("homalawn test: apply " + (ok ? "started" : "refused"))
      return ok ? "started" : "refused"
    }
    return "ok"
  }

  // ── Home screen tab ─────────────────────────────────────────────────────────────────────
  function homePage() { return view && view.shown === "home" ? view.page : null }

  // The Home screen tab's state, as JSON: the draft (items without their pictures), what changed,
  // what it waits for and what the phone refused.
  function homeFacts() {
    var p = homePage()
    if (!p) return JSON.stringify({ draft: null, why: view ? "Home screen tab not shown" : "window closed" })
    function brief(i) {
      var o = { k: i._k, id: i.id === undefined ? null : i.id, type: i.type, title: i.title || null, x: i.x }
      if (i.y !== undefined) o.y = i.y
      if (i.type === "app") o.component = D.component(i)
      if (i.type === "widget") { o.provider = i.provider; o.w = i.w; o.h = i.h }
      if (i.type === "folder") o.items = (i.items || []).map(brief)
      return o
    }
    var d = p.draft
    return JSON.stringify({
      phone: p.homePhone, loaded: !!d, changes: p.changes, applying: p.applying, screen: p.screenIndex,
      grid: d ? d.grid : null, hotseat_count: d ? d.hotseat_count : null,
      settings: d ? { icon_scale: d.icon_scale, label_scale: d.label_scale, label_gap_dp: d.label_gap_dp,
                      show_labels: d.show_labels } : null,
      screens: d ? d.screens.map(s => ({ index: s.index, items: s.items.map(brief) })) : [],
      hotseat: d ? d.hotseat.map(brief) : [],
      problems: p.problemLines, problemKeys: Object.keys(p.problems), applyError: p.applyError,
      loadError: p.loadError,
      apps: p.apps.length, widgets: p.widgetList.length, icons: Object.keys(p.icons).length,
      folder: p.openFolder ? brief(p.openFolder) : null, tab: p.tab, query: p.query,
      drag: p.dragSrc ? { target: p.dragTarget, verdict: p.dragVerdict } : null,
      size: [view.width, view.height], preview: d ? p.previewFacts() : null
    })
  }

  // Change the Home screen tab's draft the way its controls and drags do, e.g.
  //   {"drop":{"src":S,"target":T}}  — a whole drag; {"pose":{"src":S,"target":T}} — a drag held
  //   still over T (for a picture; {"pose":null} lets go without dropping)
  //     S: {"at":[screen,x,y]} | {"dock":x} | {"kid":[screen,x,y,n]} (folder app n) | {"app":"Label"}
  //        | {"widget":"Label"}       T: {"cell":[screen,x,y]} | {"dock":x} | {"trash":true} | {"thumb":screen}
  //   {"grid":[cols,rows]} {"dock":n} {"set":{"icon_scale":1.2,"label_gap_dp":-4}} {"screen":n}
  //   {"addScreen":true} {"open":[screen,x,y]} {"rename":"Name"} {"close":true} {"tab":"widgets"}
  //   {"search":"cal"}
  // Only the draft changes until {"apply":true} (log: "homalawn test: home apply started|refused");
  // {"discard":true}.
  function homeEdit(arg) {
    var p = homePage()
    if (!p || !p.draft) return "Home screen tab not ready"
    var e
    try { e = JSON.parse(String(arg || "{}")) } catch (err) { return "not JSON" }
    console.log("homalawn test: homeEdit " + JSON.stringify(e))
    function keyAt(screen, x, y) {
      var it = D.itemAt(p.draft, screen, x, y, null)
      return it ? it._k : null
    }
    function source(s) {
      if (s.at) return { item: keyAt(s.at[0], s.at[1], s.at[2]) }
      if (s.dock !== undefined) { var di = D.dockAt(p.draft, s.dock, null); return { item: di ? di._k : null } }
      if (s.kid) {
        var f = D.itemAt(p.draft, s.kid[0], s.kid[1], s.kid[2], null)
        return { item: f && f.items && f.items[s.kid[3]] ? f.items[s.kid[3]]._k : null }
      }
      if (s.app) return { app: p.apps.find(a => a.label === s.app || a.component === s.app) || null }
      if (s.widget) return { widget: p.widgetList.find(w => w.label === s.widget || w.provider === s.widget) || null }
      return {}
    }
    function target(t) {
      if (t.cell) return { screen: t.cell[0], x: t.cell[1], y: t.cell[2] }
      if (t.dock !== undefined) return { dock: t.dock }
      if (t.trash) return { trash: true }
      if (t.thumb !== undefined) return { thumb: t.thumb }
      return null
    }
    var out = "ok"
    if (e.tab) p.tab = e.tab
    if (e.search !== undefined) p.query = String(e.search)
    if (e.screen !== undefined) p.screenIndex = Number(e.screen)
    if (e.addScreen) p.addScreen()
    if (e.grid) out = p.setGrid(e.grid[0], e.grid[1]) ? "ok" : "refused"
    if (e.dock !== undefined) out = p.setDock(e.dock) ? "ok" : "refused"
    if (e.set) for (var k in e.set) p.setSetting(k, e.set[k])
    if (e.open) { var fk = keyAt(e.open[0], e.open[1], e.open[2]); if (fk) p.openFolderAt(fk); else out = "refused" }
    if (e.rename !== undefined) out = p.folderKey && p.take(D.rename(p.draft, p.folderKey, e.rename)) ? "ok" : "refused"
    if (e.close) p.folderKey = ""
    if (e.drop) {
      var src = source(e.drop.src), tgt = target(e.drop.target)
      var r = D.drop(p.draft, src, tgt)
      if (r.ok) p.setDraft(r.draft)
      out = r.ok ? "ok" : "refused: " + r.message
    }
    if (e.pose !== undefined) {
      if (e.pose === null) { p.dragCancel(); return "ok" }
      p.folderKey = ""
      p.dragSrc = source(e.pose.src)
      var pt = p.pointOf(target(e.pose.target))
      p.dragMove(pt.x, pt.y)
      out = JSON.stringify({ target: p.dragTarget, ok: p.dragVerdict ? p.dragVerdict.ok : null })
    }
    if (e.discard) out = p.discard() ? "discarded" : "refused"
    if (e.apply) {
      var ok = !view.busyEntry && p.online && p.applyNow()
      console.log("homalawn test: home apply " + (ok ? "started" : "refused"))
      out = ok ? "started" : "refused"
    }
    return out
  }

  // ── Search & gestures tab ───────────────────────────────────────────────────────────────
  function searchPage() { return view && view.shown === "search" ? view.page : null }

  // The Search & gestures tab's state, as JSON: the draft, the phone's values, what differs.
  function searchFacts() {
    var p = searchPage()
    if (!p) return JSON.stringify({ draft: null, why: view ? "Search & gestures tab not shown" : "window closed" })
    return JSON.stringify({
      phone: p.searchPhone, loaded: !!p.draft, draft: p.draft, saved: p.saved, diff: p.diff, changes: p.changes,
      applying: p.applying, applyError: p.applyError,
      loadError: p.loadError, problems: p.problems,
      handlers: p.info ? p.info.handlers.map(h => h.type) : [], deck: p.info ? p.info.deck : null,
      size: [view.width, view.height]
    })
  }

  // Change the draft the way the controls do, e.g.
  //   {"gesture":{"swipe_down":"homarSearch"}} {"search":{"calculator":true,"web_provider":"kagi"}}
  //   {"open":"swipe_down"} (open that gesture's list, for a picture; {"open":""} closes them)
  //   {"scroll":400}
  // Only the draft changes until {"apply":true} (log: "homalawn test: search apply started|refused");
  // {"discard":true}.
  function searchEdit(arg) {
    var p = searchPage()
    if (!p || !p.draft) return "Search & gestures tab not ready"
    var e
    try { e = JSON.parse(String(arg || "{}")) } catch (err) { return "not JSON" }
    console.log("homalawn test: searchEdit " + JSON.stringify(e))
    var out = "ok"
    if (e.gesture) for (var g in e.gesture) if (!p.setGesture(g, e.gesture[g])) out = "refused"
    if (e.search) for (var k in e.search) if (!p.setSearch(k, e.search[k])) out = "refused"
    if (e.open !== undefined) out = openDropdown(p, String(e.open)) ? "ok" : "refused"
    if (e.scroll !== undefined) {
      var f = findChild(p, o => o.hasOwnProperty("contentY") && o.hasOwnProperty("contentHeight"))
      if (f) f.contentY = Math.max(0, Math.min(Number(e.scroll), f.contentHeight - f.height))
    }
    if (e.discard) out = p.discard() ? "discarded" : "refused"
    if (e.apply) {
      var ok = !view.busyEntry && p.applyNow()
      console.log("homalawn test: search apply " + (ok ? "started" : "refused"))
      out = ok ? "started" : "refused"
    }
    return out
  }

  // Every Dropdown under `root` is closed, then the one in the Setting called `name` opened.
  function openDropdown(root, name) {
    var hit = false
    function walk(o) {
      if (!o) return
      if (o.hasOwnProperty("options") && o.hasOwnProperty("overlay") && o.hasOwnProperty("open")) {
        var row = o.parent ? o.parent.parent : null
        var mine = !!row && row.hasOwnProperty("name") && row.name === name
        o.open = mine
        if (mine) hit = true
      }
      var kids = o.children || []
      for (var i = 0; i < kids.length; i++) walk(kids[i])
    }
    walk(root)
    return hit || name === ""
  }

  function findChild(o, pred) {
    if (!o) return null
    if (pred(o)) return o
    var kids = o.children || []
    for (var i = 0; i < kids.length; i++) {
      var r = findChild(kids[i], pred)
      if (r) return r
    }
    return null
  }

  // ── Agents section ──────────────────────────────────────────────────────────────────────
  function agentsPage() { return view && view.shown === "agents" ? view.page : null }

  // The Agents section's state, as JSON: the chat (who answers, messages, running), the agent
  // changes and recipes it shows.
  function agentsFacts() {
    var p = agentsPage()
    if (!p) return JSON.stringify({ chat: null, why: view ? "Agents not shown" : "window closed" })
    var box = findChild(p, o => o.hasOwnProperty("cursorRectangle") && !o.readOnly)
    return JSON.stringify({
      loaded: p.loaded, agent: p.agent, canChat: p.canChat, running: p.running, sending: p.sending,
      messages: p.chat ? p.chat.messages.map(m => ({ role: m.role, text: m.text })) : [],
      input: box ? box.text : null,
      recipes: p.recipeRows.map(r => r.name), shownRecipes: p.shownRecipes.map(r => r.title || r.name),
      changes: p.changeRows, confirming: p.confirming, restoring: p.restoring, noPhone: p.noPhone,
      codingAgents: p.agentRows.map(r => ({ id: r.id, state: p.agentState(r).text })),
      errors: { recipes: p.recipesError, changes: p.changesError, agents: p.agentsError }
    })
  }

  // Use the section as a person would: {"type":"TEXT"} (into the message box), {"send":"TEXT"}
  // (or {"send":true} for what is typed), {"stop":true}, {"new":true}, {"ask":"PHONE/NAME"} (a
  // change's Restore: its confirm shows), {"restore":"PHONE/NAME"}, {"reload":true},
  // {"scroll":400} (the side column). The log says "homalawn test: agents send|stop|new|restore
  // started|refused".
  function agentsEdit(arg) {
    var p = agentsPage()
    if (!p) return "Agents not shown"
    var e
    try { e = JSON.parse(String(arg || "{}")) } catch (err) { return "not JSON" }
    console.log("homalawn test: agentsEdit " + JSON.stringify(e))
    var out = "ok"
    function said(what, ok) {
      console.log("homalawn test: agents " + what + " " + (ok ? "started" : "refused"))
      out = ok ? "started" : "refused"
    }
    if (e.reload) p.load()
    if (e.ask) p.confirming = String(e.ask)
    if (e.type !== undefined) {
      var box = findChild(p, o => o.hasOwnProperty("cursorRectangle") && !o.readOnly)
      if (box) box.text = String(e.type)
    }
    if (e.scroll !== undefined) {
      var f = findChild(p, o => o.hasOwnProperty("contentY") && o.hasOwnProperty("interactive") && o.width < p.width / 2)
      if (f) f.contentY = Math.max(0, Math.min(Number(e.scroll), f.contentHeight - f.height))
    }
    if (e.send !== undefined) said("send", e.send === true ? p.send() : p.send(String(e.send)))
    if (e.stop) said("stop", p.stop())
    if (e.new) said("new", p.newChat())
    if (e.restore) {
      var row = p.changeRows.find(r => r.phone + "/" + r.name === String(e.restore))
      said("restore", !!row && p.restore(row))
    }
    return out
  }

  // ── Add phone (pairing) and Overview (unpair) ───────────────────────────────────────────
  function addPage() { return view && view.shown === "add" ? view.page : null }

  // The pairing as the Add phone page shows it, and Overview's Unpair question, as JSON.
  function pairFacts() {
    var a = addPage(), o = view && view.shown === "overview" ? view.page : null
    var texts = []
    if (a) findChild(a, t => { if (t.hasOwnProperty("text") && t.hasOwnProperty("textFormat") && t.visible && t.text !== "") texts.push(t.text); return false })
    return JSON.stringify({
      add: a ? { found: a.found, stage: a.stage, words: a.words, message: a.message, pairing: a.pairing,
                 askError: a.askError, texts: texts } : null,
      unpair: o ? { asking: o.askingUnpair, unpairing: o.unpairing } : null,
      phone: view ? view.phone : null, shown: view ? view.shown : ""
    })
  }

  // {"find":true} {"start":"HOST"} {"cancel":true} on the Add phone page; {"unpair":true} (press
  // Unpair; the first press asks, the second confirms) {"cancelUnpair":true} on Overview. The log
  // says "homalawn test: pair find|start|cancel|unpair started|refused".
  function pairEdit(arg) {
    var e
    try { e = JSON.parse(String(arg || "{}")) } catch (err) { return "not JSON" }
    console.log("homalawn test: pairEdit " + JSON.stringify(e))
    var out = "ok"
    function said(what, ok) {
      console.log("homalawn test: pair " + what + " " + (ok ? "started" : "refused"))
      out = ok ? "started" : "refused"
    }
    var a = addPage(), o = view && view.shown === "overview" ? view.page : null
    if (e.find) said("find", !!a && a.find())
    if (e.start) said("start", !!a && a.start(String(e.start)))
    if (e.cancel) said("cancel", !!a && a.cancel())
    if (e.unpair) said("unpair", !!o && o.unpairNow())
    if (e.cancelUnpair) { if (o) o.askingUnpair = false; said("cancelUnpair", !!o) }
    return out
  }

  // The Add phone page as JSON: the path shown ("new" / "existing"), the welcome (no phone yet),
  // and the new phone's QR code: what app.serve answered (`offer`, its `qr` as the module count),
  // the seconds left, why the last one ended ("expired" / "stopped"), the service's `serving`
  // with its message, and an error.
  function addFacts() {
    var a = addPage()
    if (!a) return JSON.stringify({ add: null, why: view ? "Add phone not shown" : "window closed" })
    var o = a.offer
    var qr = findChild(a, x => x.objectName === "qrCode")
    return JSON.stringify({
      path: a.path, welcome: a.welcome,
      offer: o ? { url: o.url, urls: o.urls, version: o.version, source: o.source, expires_at: o.expires_at,
                   qr: o.qr ? o.qr.length : 0 } : null,
      qrShown: !!qr && qr.visible && qr.width > 0, qrPixels: qr ? qr.width : 0, qrModule: qr ? qr.module : 0,
      remaining: a.remaining, ended: a.serveEnded, asking: a.serveAsking, stopping: a.stopping,
      error: a.serveError, serving: a.serving, message: a.serveMessage
    })
  }

  // The Add phone page as a person uses it: {"path":"new"|"existing"} (the two choices),
  // {"serve":true} (Show QR code: app.serve), {"stop":true} (Stop: app.serve_stop); the log says
  // "homalawn test: add serve|stop started|refused". Pairing: pairEdit.
  function addEdit(arg) {
    var a = addPage()
    if (!a) return "Add phone not shown"
    var e
    try { e = JSON.parse(String(arg || "{}")) } catch (err) { return "not JSON" }
    console.log("homalawn test: addEdit " + JSON.stringify(e))
    var out = "ok"
    function said(what, ok) {
      console.log("homalawn test: add " + what + " " + (ok ? "started" : "refused"))
      out = ok ? "started" : "refused"
    }
    if (e.path !== undefined) out = a.choose(String(e.path)) ? "ok" : "refused"
    if (e.serve) said("serve", a.serve())
    if (e.stop) said("stop", a.stopServing())
    return out
  }

  // What the bar mark shows now, worked out as BarWidget does (Status.js): problem (the warning
  // colour), connected (accent), the phones with an update, the tooltip.
  function barFacts() {
    var target = homalawn || findService()
    var b = target ? target.backend : null
    return JSON.stringify(Status.summary(b ? b.phones : [], !!b && b.connected, !!target))
  }

  // Overview's phone facts as JSON: the app line, whether Update app is offered, the permission
  // rows shown (from the phone's own report, or phone.info's two on an older phone app).
  function overviewFacts() {
    var o = overviewPage()
    if (!o) return JSON.stringify({ overview: null, why: view ? "Overview not shown" : "window closed" })
    return JSON.stringify({
      online: o.online, version: o.app ? o.app.versionName || null : null, latest: o.latest,
      updateOffered: o.updateOffered, askingUpdate: o.askingUpdate,
      permissions: o.permissions, oldPermissions: o.permissions ? null : o.perms,
      found: o.addrFound
    })
  }

  // ── Overview's Addresses card ───────────────────────────────────────────────────────────
  function overviewPage() { return view && view.shown === "overview" ? view.page : null }

  // The Addresses card as JSON: the draft, what is saved, the read-only rows, the error.
  function addrFacts() {
    var o = overviewPage()
    if (!o) return JSON.stringify({ addresses: null, why: view ? "Overview not shown" : "window closed" })
    return JSON.stringify({ draft: o.addrDraft, saved: o.addrSaved, others: o.addrOthers.map(r => r.address + " " + r.kind),
                            answered: o.addrAnswered, changed: o.addrChanged, saving: o.addrSaving, error: o.addrError })
  }

  // {"add":"HOST"} (as typed in the box and Add) {"remove":N} {"apply":true} {"discard":true}.
  function addrEdit(arg) {
    var e
    try { e = JSON.parse(String(arg || "{}")) } catch (err) { return "not JSON" }
    console.log("homalawn test: addrEdit " + JSON.stringify(e))
    var o = overviewPage()
    if (!o) return "refused"
    if (e.add !== undefined) o.addAddress(e.add)
    if (e.remove !== undefined) o.removeAddress(e.remove)
    if (e.discard) o.discardAddresses()
    if (e.apply) return o.applyAddresses() ? "started" : "refused"
    return "ok"
  }

  // ── Service page, and the coding agents (on the Agents page) ────────────────────────────
  function settingsPage() { return view && view.shown === "settings" ? view.page : null }

  // JSON: the Service page's service facts (`service`), or, on the Agents page, the coding agents
  // it lists (`agents`; null until read, and while neither page is shown).
  function settingsFacts() {
    var s = settingsPage(), a = agentsPage()
    if (!s && !a) return JSON.stringify({ agents: null, service: null, why: view ? "Neither Service nor Agents shown" : "window closed" })
    return JSON.stringify({ agents: a && a.agentsRead ? a.agentRows : null, connecting: a ? a.connecting : "",
                            error: a ? a.agentsError : "", service: s ? s.service : null })
  }

  // {"connect":"claude"}: an agent's Connect / Reconnect on the Agents page (only with temp agent
  // configs). The log says "homalawn test: settings connect started|refused".
  function settingsEdit(arg) {
    var p = agentsPage()
    if (!p) return "Agents not shown"
    var e
    try { e = JSON.parse(String(arg || "{}")) } catch (err) { return "not JSON" }
    if (!e.connect) return "nothing to do"
    var c = p.connectAgent(String(e.connect))
    console.log("homalawn test: settings connect " + (c ? "started" : "refused"))
    return c ? "started" : "refused"
  }
}
