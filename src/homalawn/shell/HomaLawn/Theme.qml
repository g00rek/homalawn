pragma Singleton
import QtQuick

// The Homa Lawn window's visual system: colours named after the keys of an Omarchy colors.toml,
// five type sizes, one page margin, the disabled look and one date format. load() takes the
// colors.toml text; anything missing is derived, and an empty text means Tokyo Night.
//
// Two colour rules hold for every theme: secondary text (darkForeground) stays readable on the
// window's backgrounds, and the state colours (green, yellow, red) stay coloured and apart from
// the accent. A palette whose own green/yellow/red are grey, missing, too close to the accent or
// too faint on its background gets fixed, readable ones instead (one set for dark themes, one for
// light).
QtObject {
  id: theme

  property color background: "#1a1b26"
  property color darkBackground: "#13141c"
  property color darkerBackground: "#0e0e14"
  property color lighterBackground: "#24283b"
  property color selection: "#292e42"
  property color muted: "#414868"
  property color foreground: "#a9b1d6"
  property color darkForeground: "#7a83ae"
  property color brightForeground: "#c0caf5"
  property color accent: "#7aa2f7"
  property color green: "#9ece6a"
  property color red: "#f7768e"
  property color yellow: "#e0af68"
  property bool light: false

  readonly property string font: "monospace"
  // The five type sizes.
  readonly property int title: 20      // page titles
  readonly property int heading: 15    // card titles and every other heading
  readonly property int body: 13       // ordinary text
  readonly property int small: 12      // secondary lines, buttons
  readonly property int tiny: 11       // hints under a control
  readonly property int size: body
  // The one page margin (left, right and top of every page's content) and the gap between cards.
  readonly property int margin: 24
  readonly property int gap: 16
  // A control that cannot be used now.
  readonly property real disabledOpacity: 0.38

  property string loadedText: "\u0000"

  readonly property var tokyoNight: ({
    background: "#1a1b26", darkBackground: "#13141c", darkerBackground: "#0e0e14",
    lighterBackground: "#24283b", selection: "#292e42", muted: "#414868",
    foreground: "#a9b1d6", darkForeground: "#7a83ae", brightForeground: "#c0caf5",
    accent: "#7aa2f7", green: "#9ece6a", red: "#f7768e", yellow: "#e0af68", light: false
  })
  // Fixed state colours for palettes without usable ones.
  readonly property var darkStates: ({ green: "#8ccf6a", yellow: "#e5b25d", red: "#f2727f" })
  readonly property var lightStates: ({ green: "#2f7d32", yellow: "#9a6400", red: "#c4283a" })

  // A colour part way from a to b (t = 0 is a, t = 1 is b).
  function blend(a, b, t) {
    return Qt.rgba(a.r + (b.r - a.r) * t, a.g + (b.g - a.g) * t,
                   a.b + (b.b - a.b) * t, a.a + (b.a - a.a) * t)
  }

  function withAlpha(c, a) { return Qt.rgba(c.r, c.g, c.b, a) }

  // WCAG relative luminance and contrast ratio.
  function luminance(c) {
    function lin(v) { return v <= 0.03928 ? v / 12.92 : Math.pow((v + 0.055) / 1.055, 2.4) }
    return 0.2126 * lin(c.r) + 0.7152 * lin(c.g) + 0.0722 * lin(c.b)
  }
  function contrast(a, b) {
    var la = luminance(a), lb = luminance(b)
    return (Math.max(la, lb) + 0.05) / (Math.min(la, lb) + 0.05)
  }
  // `c` moved toward `to` just far enough to reach `ratio` against every colour in `on`.
  function readable(c, to, on, ratio) {
    for (var t = 0; t <= 1.0001; t += 0.05) {
      var x = blend(c, to, t)
      if (on.every(b => contrast(x, b) >= ratio)) return x
    }
    return to
  }
  function chroma(c) { return Math.max(c.r, c.g, c.b) - Math.min(c.r, c.g, c.b) }
  function distance(a, b) { return Math.abs(a.r - b.r) + Math.abs(a.g - b.g) + Math.abs(a.b - b.b) }

  function useTokyoNight() {
    for (var k in tokyoNight) theme[k] = tokyoNight[k]
  }

  function load(text) {
    text = text || ""
    if (text === loadedText) return
    loadedText = text
    var v = {}
    var rows = text.split("\n")
    for (var i = 0; i < rows.length; i++) {
      var hit = rows[i].match(/^\s*([a-z_]+)\s*=\s*["'](#[0-9a-fA-F]{6}(?:[0-9a-fA-F]{2})?)["']/)
      if (hit) v[hit[1]] = Qt.color(hit[2])
    }
    // Without these two there is nothing sensible to build on.
    if (!v.background || !v.foreground) {
      useTokyoNight()
      return
    }
    var bg = v.background, fg = v.foreground
    var black = Qt.rgba(0, 0, 0, 1), white = Qt.rgba(1, 1, 1, 1)
    var lightTheme = luminance(bg) > 0.4
    var away = lightTheme ? black : white   // "lighter" means away from the background's end
    light = lightTheme
    background = bg
    foreground = fg
    accent = v.accent || v.blue || fg
    darkBackground = v.dark_background || blend(bg, black, 0.15)
    darkerBackground = v.darker_background || blend(bg, black, 0.3)
    lighterBackground = v.lighter_background || blend(bg, away, 0.06)
    selection = v.selection || blend(bg, fg, 0.15)
    muted = v.muted || blend(bg, fg, 0.3)
    brightForeground = v.bright_foreground || fg
    // Secondary text: the theme's own, made readable (4.5:1) on the page and on cards.
    var surfaces = [bg, darkBackground, lighterBackground]
    darkForeground = readable(v.dark_foreground || blend(bg, fg, 0.5), fg, surfaces, 4.5)
    // State colours: the theme's own when they are colours, apart from the accent and one
    // another, and readable (3:1) on the page and cards; otherwise the fixed set.
    var fixed = lightTheme ? lightStates : darkStates
    var picked = {}
    ;["green", "yellow", "red"].forEach(function (k) {
      var c = v[k]
      var usable = !!c && chroma(c) >= 0.25 && distance(c, accent) >= 0.25
        && Object.keys(picked).every(o => distance(c, picked[o]) >= 0.25)
      var base = usable ? c : Qt.color(fixed[k])
      picked[k] = readable(base, lightTheme ? black : white, surfaces, 3)
    })
    green = picked.green
    yellow = picked.yellow
    red = picked.red
  }

  // ── Dates: one format everywhere ("10 Nov, 21:45"; with the year when not this year) ────────
  readonly property var monthNames: ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]
  function two(n) { return (n < 10 ? "0" : "") + n }
  // The day of a unix time: "10 Nov", or "10 Nov 2025" in another year; "" without one.
  function day(ts) {
    if (!ts) return ""
    var d = new Date(ts * 1000)
    var s = d.getDate() + " " + monthNames[d.getMonth()]
    return d.getFullYear() === new Date().getFullYear() ? s : s + " " + d.getFullYear()
  }
  // The day and time of a unix time: "10 Nov, 21:45" (or "10 Nov 2025, 21:45"); "" without one.
  function when(ts) {
    if (!ts) return ""
    var d = new Date(ts * 1000)
    return day(ts) + ", " + two(d.getHours()) + ":" + two(d.getMinutes())
  }
}
