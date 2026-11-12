.pragma library

// The Home screen page's draft: a copy of home.get that the editor changes, and the rules for
// every change (drop on a cell, on the dock, on the trash, on a screen thumbnail; a new grid or
// dock width). Plain functions on plain objects: each change works on a copy and answers
// {ok, draft, message}; nothing here talks to the service. tests/test_home_draft.py runs these
// under node.
//
// A draft: {grid: [cols, rows], hotseat_count, top_row_reserved, icon_scale, label_scale,
// label_gap_dp, show_labels, screens: [{index, id, items}], hotseat: [items]}. `index` is a
// page's position on the phone, `id` its screen id (none on a page the editor added). Every item carries a
// local key `_k` ("i<id>" for items on the phone, "n<number>" for new ones) so the page can follow
// it while it moves; toApply() strips the keys.

var SETTINGS = ["icon_scale", "label_scale", "label_gap_dp", "show_labels"]
var LIMITS = { cols: [3, 10], rows: [3, 10], dock: [3, 10] }

var newCount = 0

function copy(o) { return o === undefined ? undefined : JSON.parse(JSON.stringify(o)) }

function key(item) {
  if (item.id !== undefined && item.id !== null) return "i" + item.id
  newCount += 1
  return "n" + newCount
}

function keyAll(items) {
  (items || []).forEach(function (i) {
    if (!i._k) i._k = key(i)
    if (i.type === "folder") keyAll(i.items)
  })
}

// home.get's answer -> a draft.
function fromHome(home) {
  var d = {
    grid: [home.grid[0], home.grid[1]],
    hotseat_count: home.hotseat_count,
    top_row_reserved: home.top_row_reserved !== false,
    icon_scale: num(home.icon_scale, 1), label_scale: num(home.label_scale, 1),
    label_gap_dp: num(home.label_gap_dp, 0), show_labels: home.show_labels !== false,
    screens: copy(home.screens || []),
    hotseat: copy(home.hotseat || [])
  }
  if (d.screens.length === 0) d.screens.push({ index: 0, items: [] })
  d.screens.sort(function (a, b) { return a.index - b.index })
  d.screens.forEach(function (s) { keyAll(s.items) })
  keyAll(d.hotseat)
  return d
}

function num(v, dflt) { return typeof v === "number" && isFinite(v) ? v : dflt }

function strip(items) {
  return items.map(function (i) {
    var o = {}
    for (var k in i) if (k !== "_k") o[k] = i[k]
    if (o.type === "folder") o.items = strip(o.items || [])
    return o
  })
}

function same(a, b) { return JSON.stringify(a) === JSON.stringify(b) }

// What home.apply takes for this draft, compared with the phone's saved one.
function toApply(d, saved) {
  var out = {
    layout: {
      grid: { cols: d.grid[0], rows: d.grid[1] },
      screens: d.screens.map(function (s) {
        var o = { index: s.index, items: strip(s.items) }
        if (s.id !== undefined && s.id !== null) o.id = s.id
        return o
      }),
      hotseat: strip(d.hotseat)
    }
  }
  if (!saved || !same(d.grid, saved.grid)) out.grid = [d.grid[0], d.grid[1]]
  if (!saved || d.hotseat_count !== saved.hotseat_count) out.hotseat_count = d.hotseat_count
  var look = {}
  var any = false
  SETTINGS.forEach(function (n) {
    if (!saved || d[n] !== saved[n]) { look[n] = d[n]; any = true }
  })
  if (any) out.look_prefs = look
  return out
}

// "package/full.Activity": the key home.get's icons use.
function component(item) {
  if (!item || !item.package) return ""
  var act = item.activity || ""
  if (act.charAt(0) === ".") act = item.package + act
  return item.package + "/" + act
}

// A widget provider as "package/full.Class", so a layout's and the provider list's spellings meet.
function provider(p) {
  var s = String(p || "")
  var slash = s.indexOf("/")
  if (slash < 0) return s
  var pkg = s.slice(0, slash), cls = s.slice(slash + 1)
  return pkg + "/" + (cls.charAt(0) === "." ? pkg + cls : cls)
}

function size(item) {
  if (item && item.type === "widget") return [Math.max(1, item.w || 1), Math.max(1, item.h || 1)]
  return [1, 1]
}

function screenAt(d, index) {
  for (var i = 0; i < d.screens.length; i++) if (d.screens[i].index === index) return d.screens[i]
  return null
}

// The page with the search/date bar: screen id 0, wherever the phone shows it.
function searchScreen(d, index) {
  var s = screenAt(d, index)
  return !!s && s.id === 0
}

function reserved(d, index, y) { return d.top_row_reserved && y === 0 && searchScreen(d, index) }

// The top-level item covering a cell of a screen (other than `except`), or null.
function itemAt(d, index, x, y, except) {
  var s = screenAt(d, index)
  if (!s) return null
  for (var i = 0; i < s.items.length; i++) {
    var it = s.items[i]
    if (it._k === except) continue
    var wh = size(it)
    if (x >= it.x && x < it.x + wh[0] && y >= it.y && y < it.y + wh[1]) return it
  }
  return null
}

function dockAt(d, x, except) {
  for (var i = 0; i < d.hotseat.length; i++)
    if (d.hotseat[i].x === x && d.hotseat[i]._k !== except) return d.hotseat[i]
  return null
}

// Cells a w x h item at (x, y) would cover, each {x, y, ok}: inside the grid, not the reserved
// row, not under another item.
function cells(d, index, x, y, w, h, except) {
  var out = []
  for (var cy = y; cy < y + h; cy++)
    for (var cx = x; cx < x + w; cx++) {
      var inside = cx >= 0 && cy >= 0 && cx < d.grid[0] && cy < d.grid[1]
      out.push({ x: cx, y: cy, ok: inside && !reserved(d, index, cy) && !itemAt(d, index, cx, cy, except) })
    }
  return out
}

function fits(d, index, x, y, w, h, except) {
  return cells(d, index, x, y, w, h, except).every(function (c) { return c.ok })
}

function firstFree(d, index, w, h, except) {
  for (var y = 0; y + h <= d.grid[1]; y++)
    for (var x = 0; x + w <= d.grid[0]; x++)
      if (fits(d, index, x, y, w, h, except)) return { x: x, y: y }
  return null
}

// Where an item is: {list, index, item, where: "screen"|"hotseat"|"folder", screen, folder}.
function locate(d, k) {
  function look(list, where, screen, folder) {
    for (var i = 0; i < list.length; i++) {
      var it = list[i]
      if (it._k === k) return { list: list, index: i, item: it, where: where, screen: screen, folder: folder }
      if (it.type === "folder") {
        var hit = look(it.items || [], "folder", screen, it)
        if (hit) return hit
      }
    }
    return null
  }
  for (var s = 0; s < d.screens.length; s++) {
    var r = look(d.screens[s].items, "screen", d.screens[s].index, null)
    if (r) return r
  }
  return look(d.hotseat, "hotseat", null, null)
}

// Take an item out of the draft. A folder left with one app turns back into that app, in the
// folder's place; an empty folder goes.
function takeOut(d, k) {
  var at = locate(d, k)
  if (!at) return null
  at.list.splice(at.index, 1)
  if (at.where === "folder") {
    var f = at.folder
    rank(f)
    if (f.items.length <= 1) {
      var home = locate(d, f._k)
      if (f.items.length === 1) {
        var last = f.items[0]
        var put = { x: f.x }
        if (home.where !== "hotseat") put.y = f.y
        home.list[home.index] = placed(last, put)
      } else {
        home.list.splice(home.index, 1)
      }
    }
  }
  return at.item
}

// A copy of an item at a new place: screen items have x and y, dock items only x.
function placed(item, pos) {
  var o = copy(item)
  delete o.x
  delete o.y
  o.x = pos.x
  if (pos.y !== undefined) o.y = pos.y
  return o
}

// Folder apps by their order (Lawnchair lays a folder out from the rank; cellX/cellY follow it).
function rank(folder) {
  (folder.items || []).forEach(function (a, n) { a.x = n % 3; a.y = Math.floor(n / 3) })
}

function newApp(app) {
  newCount += 1
  return { type: "app", package: app.package, activity: app.activity, title: app.label || app.title || app.package,
           _k: "n" + newCount }
}

function newWidget(w) {
  newCount += 1
  var c = w.cells || [1, 1]
  return { type: "widget", provider: w.provider, w: Math.max(1, c[0]), h: Math.max(1, c[1]), _k: "n" + newCount }
}

// The item a drag carries: {item: key} (already in the draft), {app} or {widget} (from the lists).
function carried(d, src) {
  if (src.item) {
    var at = locate(d, src.item)
    return at ? at.item : null
  }
  if (src.app) return { type: "app", package: src.app.package, activity: src.app.activity, title: src.app.label }
  if (src.widget) { var c = src.widget.cells || [1, 1]; return { type: "widget", w: c[0], h: c[1] } }
  return null
}

function fresh(src) { return src.app ? newApp(src.app) : src.widget ? newWidget(src.widget) : null }

function newFolder(first, second, pos) {
  newCount += 1
  var f = { type: "folder", title: "Folder", items: [copy(first), copy(second)], _k: "n" + newCount }
  f.items.forEach(function (a) { delete a.x; delete a.y })
  rank(f)
  return placed(f, pos)
}

// What dropping `src` on `target` would do, without doing it:
// {ok, action ("place" | "folder" | "add" | "remove" | "none"), message, cells (for the highlight)}.
// target: {screen, x, y} | {dock: x} | {trash: true} | {thumb: screenIndex}.
function verdict(d, src, target) {
  var it = carried(d, src)
  if (!it) return { ok: false, action: "none", message: "Nothing to move.", cells: [] }
  var self = src.item || null
  var wh = size(it)
  if (target.trash) {
    return self ? { ok: true, action: "remove", message: "Remove from the home screen", cells: [] }
                : { ok: false, action: "none", message: "", cells: [] }
  }
  if (target.thumb !== undefined) {
    var spot = firstFree(d, target.thumb, wh[0], wh[1], self)
    if (!spot) return { ok: false, action: "none", message: "No room for it on that screen.", cells: [] }
    return { ok: true, action: "place", message: "Move to that screen", cells: [], spot: spot }
  }
  if (target.dock !== undefined) {
    var x = target.dock
    if (it.type === "widget") return { ok: false, action: "none", message: "Widgets can't go in the dock.", cells: [] }
    if (x < 0 || x >= d.hotseat_count) return { ok: false, action: "none", message: "", cells: [] }
    var there = dockAt(d, x, self)
    return onto(it, there, "dock", [{ x: x, y: 0, ok: true }])
  }
  var index = target.screen
  var tx = target.x, ty = target.y
  var occ = itemAt(d, index, tx, ty, self)
  if (occ && wh[0] === 1 && wh[1] === 1 && (occ.type === "app" || occ.type === "folder"))
    return onto(it, occ, "screen", [{ x: tx, y: ty, ok: true }])
  var cs = cells(d, index, tx, ty, wh[0], wh[1], self)
  var ok = cs.every(function (c) { return c.ok })
  var why = ""
  if (!ok) {
    var out = cs.some(function (c) { return c.x >= d.grid[0] || c.y >= d.grid[1] })
    var top = cs.some(function (c) { return reserved(d, index, c.y) })
    why = out ? "It doesn't fit there." : top ? "The top row holds the search bar." : "Those cells are taken."
  }
  return { ok: ok, action: ok ? "place" : "none", message: why, cells: cs }
}

// Dropping an app on another item: an app makes a folder, a folder takes it in.
function onto(it, occ, where, cs) {
  if (!occ) return { ok: true, action: "place", message: "", cells: cs }
  var refuse = function (m) { cs.forEach(function (c) { c.ok = false }); return { ok: false, action: "none", message: m, cells: cs } }
  if (it.type === "folder") return refuse("A folder can't go into another item.")
  if (it.type !== "app") return refuse("That place is taken.")
  if (occ.type === "app") return { ok: true, action: "folder", message: "Make a folder", cells: cs }
  if (occ.type === "folder") return { ok: true, action: "add", message: "Add to " + (occ.title || "the folder"), cells: cs }
  return refuse("That place is taken.")
}

// Do the drop. {ok, draft, message, key (the moved or new item, or the folder it went into)}.
function drop(d0, src, target) {
  var v = verdict(d0, src, target)
  if (!v.ok) return { ok: false, draft: d0, message: v.message }
  var d = copy(d0)
  var self = src.item || null
  if (self && target.trash) {
    takeOut(d, self)
    return { ok: true, draft: d, message: "" }
  }
  // An item dropped where it already is: nothing changes.
  if (self) {
    var at0 = locate(d, self)
    if (target.dock !== undefined && at0.where === "hotseat" && at0.item.x === target.dock) return { ok: true, draft: d0, message: "" }
    if (target.screen !== undefined && at0.where === "screen" && at0.screen === target.screen
        && at0.item.x === target.x && at0.item.y === target.y) return { ok: true, draft: d0, message: "" }
  }
  var it = self ? takeOut(d, self) : fresh(src)
  // Taking the item out can turn a folder back into an app; look again at the place.
  var pos, list, occ
  if (target.thumb !== undefined) {
    var spot = firstFree(d, target.thumb, size(it)[0], size(it)[1], null)
    if (!spot) return { ok: false, draft: d0, message: "No room for it on that screen." }
    screenAt(d, target.thumb).items.push(placed(it, spot))
    return { ok: true, draft: d, message: "", key: it._k }
  }
  if (target.dock !== undefined) {
    list = d.hotseat
    pos = { x: target.dock }
    occ = dockAt(d, target.dock, null)
  } else {
    list = screenAt(d, target.screen).items
    pos = { x: target.x, y: target.y }
    occ = itemAt(d, target.screen, target.x, target.y, null)
  }
  if (!occ) {
    list.push(placed(it, pos))
    return { ok: true, draft: d, message: "", key: it._k }
  }
  if (occ.type === "folder") {
    var kid = copy(it)
    delete kid.x
    delete kid.y
    occ.items.push(kid)
    rank(occ)
    return { ok: true, draft: d, message: "", key: occ._k }
  }
  if (occ.type === "app" && it.type === "app") {
    var f = newFolder(occ, it, { x: occ.x, y: occ.y })
    if (target.dock !== undefined) delete f.y
    list[list.indexOf(occ)] = f
    return { ok: true, draft: d, message: "", key: f._k }
  }
  return { ok: false, draft: d0, message: "That place is taken." }
}

function describe(item) {
  if (!item) return "item"
  if (item.type === "widget") return "a " + size(item)[0] + "x" + size(item)[1] + " widget"
  return '"' + (item.title || item.package || item.type) + '"'
}

// Put items without a place somewhere free: their own screen first, then the next ones, then a
// new screen. Answers false (and leaves `d` half done) when one cannot fit any screen.
function rehome(d, homeless) {
  for (var n = 0; n < homeless.length; n++) {
    var h = homeless[n]
    var wh = size(h.item)
    if (wh[0] > d.grid[0] || wh[1] > d.grid[1]) return h.item
    var order = d.screens.map(function (s) { return s.index })
    var from = Math.max(0, order.indexOf(h.screen))
    var done = false
    for (var i = from; i < order.length && !done; i++) {
      var spot = firstFree(d, order[i], wh[0], wh[1], null)
      if (spot) { d.screens[i].items.push(placed(h.item, spot)); done = true }
    }
    if (!done) {
      var idx = order.length ? order[order.length - 1] + 1 : 0
      d.screens.push({ index: idx, items: [] })
      var s2 = firstFree(d, idx, wh[0], wh[1], null)
      if (!s2) return h.item
      d.screens[d.screens.length - 1].items.push(placed(h.item, s2))
    }
  }
  return null
}

// A new grid: items that still fit stay where they are; the others move to the first free cells
// (their own screen, then the next, then a new one). Refused when an item (a wide widget) cannot
// fit the new grid at all.
function setGrid(d0, cols, rows) {
  if (cols < LIMITS.cols[0] || cols > LIMITS.cols[1] || rows < LIMITS.rows[0] || rows > LIMITS.rows[1])
    return { ok: false, draft: d0, message: "Columns and rows go from 3 to 10." }
  var d = copy(d0)
  d.grid = [cols, rows]
  var homeless = []
  d.screens.forEach(function (s) {
    var keep = s.items
    s.items = []
    keep.forEach(function (it) {
      var wh = size(it)
      if (fits(d, s.index, it.x, it.y, wh[0], wh[1], null)) s.items.push(it)
      else homeless.push({ item: it, screen: s.index })
    })
  })
  var stuck = rehome(d, homeless)
  if (stuck)
    return { ok: false, draft: d0, message: "A " + cols + "x" + rows + " grid has no room for " + describe(stuck)
             + ". Move or remove it first." }
  var n = homeless.length
  return { ok: true, draft: d, moved: n,
           message: n ? n + (n === 1 ? " item moved" : " items moved") + " to make room." : "" }
}

// A new dock width: dock items past the end move to free dock places, else to the home screen.
function setDock(d0, count) {
  if (count < LIMITS.dock[0] || count > LIMITS.dock[1]) return { ok: false, draft: d0, message: "The dock holds 3 to 10 icons." }
  var d = copy(d0)
  d.hotseat_count = count
  var out = d.hotseat.filter(function (i) { return i.x >= count })
  d.hotseat = d.hotseat.filter(function (i) { return i.x < count })
  var toScreen = []
  out.forEach(function (it) {
    var x = -1
    for (var c = 0; c < count; c++) if (!dockAt(d, c, null)) { x = c; break }
    if (x >= 0) d.hotseat.push(placed(it, { x: x }))
    else toScreen.push({ item: it, screen: d.screens[0].index })
  })
  var stuck = rehome(d, toScreen)
  if (stuck) return { ok: false, draft: d0, message: "No room on the home screen for " + describe(stuck) + "." }
  return { ok: true, draft: d, moved: toScreen.length,
           message: toScreen.length ? toScreen.length + " dock " + (toScreen.length === 1 ? "icon" : "icons")
                                      + " moved to the home screen." : "" }
}

function addScreen(d0) {
  var d = copy(d0)
  var last = d.screens.length ? d.screens[d.screens.length - 1].index : -1
  d.screens.push({ index: last + 1, items: [] })
  return { ok: true, draft: d, message: "", index: last + 1 }
}

function rename(d0, k, title) {
  var d = copy(d0)
  var at = locate(d, k)
  if (!at) return { ok: false, draft: d0, message: "" }
  at.item.title = String(title)
  return { ok: true, draft: d, message: "" }
}

// How many changes the draft holds against the phone's saved home screen: every item added,
// removed, moved or renamed, and every setting (the grid counts once).
function changes(d, saved) {
  if (!d || !saved) return 0
  var n = 0
  if (!same(d.grid, saved.grid)) n++
  if (d.hotseat_count !== saved.hotseat_count) n++
  SETTINGS.forEach(function (s) { if (d[s] !== saved[s]) n++ })
  var a = signatures(d), b = signatures(saved)
  for (var k in a) if (!(k in b) || a[k] !== b[k]) n++
  for (var k2 in b) if (!(k2 in a)) n++
  return n
}

function signatures(d) {
  var out = {}
  function walk(items, where) {
    items.forEach(function (i, n) {
      var pos = where.charAt(0) === "f" ? n : [i.x, i.y === undefined ? null : i.y].join(",")
      out[i._k] = [where, pos, i.title || "", i.type === "widget" ? i.w + "x" + i.h : ""].join("|")
      if (i.type === "folder") walk(i.items || [], "f" + i._k)
    })
  }
  d.screens.forEach(function (s) { walk(s.items, "s" + s.index) })
  walk(d.hotseat, "dock")
  return out
}

// home.apply's located problems -> the keys of the items they are about (a new item has no id:
// found by its cell instead).
function problemKeys(d, errors) {
  var keys = {}
  ;(errors || []).forEach(function (e) {
    var hit = null
    if (e.id !== undefined && e.id !== null) {
      var at = locate(d, "i" + e.id)
      if (at) hit = at.where === "folder" ? at.folder : at.item
    }
    if (!hit && e.where === "screen" && e.x !== null && e.x !== undefined)
      hit = itemAt(d, e.screen, e.x, e.y, null)
    if (!hit && e.where === "hotseat" && e.x !== null && e.x !== undefined) hit = dockAt(d, e.x, null)
    if (hit) keys[hit._k] = e.message
  })
  return keys
}

// The label gap as the phone lays it out (patch 0025), in the phone's pixels, for draft `d` drawn
// from geometry `g` that the phone measured with settings `s` (the saved draft). The setting is the
// visible distance from the icon's shape to the label's capitals; the phone holds it at the most
// its cell has room for (maxDp). Same sums as HomarLabelGap.fitFixedCell and
// ShortcutAndWidgetContainer: padding = round(gap * density) - shape_margin - cap_inset, the block
// icon_block + padding + line_height centred in the cell (top never above it). Exact (`exact`)
// while the draft keeps the phone's icon size, label size and rows; otherwise the phone's numbers
// scaled by the change. Null when the phone does not report it (no patch 0025, or not the fixed
// grid). -> {icon, pad, cap, line, top, cell, maxDp, gapDp, exact}
function labelGap(g, d, s) {
  var lg = g && g.icon ? g.icon.label_gap : null
  if (!lg) return null
  s = s || d
  var ri = d.icon_scale / Math.max(0.01, s.icon_scale)
  var rl = d.label_scale / Math.max(0.01, s.label_scale)
  var exact = ri === 1 && rl === 1 && d.grid[1] === s.grid[1]
  var density = g.screen.density
  var icon = exact ? g.icon.size : Math.round(g.icon.size * ri)
  var margin = exact ? lg.shape_margin : Math.floor((icon - Math.round(0.92 * icon)) / 2)
  var block = exact ? lg.icon_block : Math.ceil(lg.icon_block * ri)
  var line = exact ? lg.line_height : Math.ceil(lg.line_height * rl)
  var cap = exact ? lg.cap_inset : Math.round(lg.cap_inset * rl)
  var rows = d.grid[1]
  var cell = d.grid[1] === s.grid[1] ? g.workspace.cell_h
    : Math.floor((g.workspace.h - g.workspace.gap_y * (rows - 1)) / rows)
  var maxDp = exact ? lg.max_dp : Math.max(0, Math.floor((cell - icon - line + margin + cap) / density))
  var gapDp = Math.max(0, Math.min(d.label_gap_dp, maxDp))
  var pad = Math.round(gapDp * density) - margin - cap
  var top = Math.max(0, Math.floor((cell - (block + pad + line)) / 2))
  return { icon: icon, pad: pad, cap: cap, line: line, top: top, cell: cell, maxDp: maxDp, gapDp: gapDp,
           exact: exact }
}

if (typeof module !== "undefined") module.exports = {
  fromHome: fromHome, toApply: toApply, component: component, provider: provider, size: size,
  itemAt: itemAt, dockAt: dockAt, cells: cells, fits: fits, firstFree: firstFree, locate: locate,
  verdict: verdict, drop: drop, setGrid: setGrid, setDock: setDock, addScreen: addScreen, rename: rename,
  changes: changes, problemKeys: problemKeys, screenAt: screenAt, copy: copy, labelGap: labelGap
}
