.pragma library

// What the bar mark says about the paired phones, shared by the bar widget and the test harness.
//
// A phone needs attention when the service reports an error for it, or when it is connected and
// one of the permissions Homa Lawn needs (home app, run in background, notifications) is not
// allowed. Screen control and installing updates are optional and never count.

var REQUIRED = [
  { key: "home", words: "home app" },
  { key: "background", words: "run in background" },
  { key: "notifications", words: "notifications" }
]

// "a", "a and b", "a, b and c"
function joinWords(list) {
  if (list.length <= 1) return list.join("")
  return list.slice(0, -1).join(", ") + " and " + list[list.length - 1]
}

// The required permissions a connected phone has not allowed, in words ([] when none or unknown).
function missing(phone) {
  var p = phone && phone.connected ? phone.permissions : null
  if (!p) return []
  return REQUIRED.filter(r => p[r.key] === false).map(r => r.words)
}

// What is wrong with one phone, as the words after "needs attention: " ("" when nothing).
function problemOf(phone) {
  if (!phone) return ""
  if (phone.error) return String(phone.error).trim()     // the service's own sentence
  var m = missing(phone)
  if (!m.length) return ""
  return joinWords(m) + (m.length === 1 ? " is" : " are") + " not allowed"
}

// The newer app the service offers a phone, or "".
function updateOf(phone) {
  var l = phone ? phone.app_latest : null
  return l && l.newer ? String(l.version || "") || "a newer version" : ""
}

// {problem, connected, updates, tooltip} for the bar. `up`: the service answers.
function summary(phones, up, present) {
  if (!present) return { problem: false, connected: false, updates: [], tooltip: "Homa Lawn" }
  if (!up) return { problem: false, connected: false, updates: [], tooltip: "Homa Lawn service is not running" }
  phones = phones || []
  if (!phones.length) return { problem: false, connected: false, updates: [], tooltip: "Homa Lawn · no paired phones" }
  var lines = []
  var problem = false
  var ordered = phones.filter(p => p.connected).concat(phones.filter(p => !p.connected))
  ordered.forEach(function (p) {
    var why = problemOf(p)
    if (why) {
      problem = true
      lines.push(p.name + " · needs attention: " + why)
    } else {
      lines.push(p.name + " · " + (p.connected ? "connected" : "offline"))
    }
  })
  var updates = phones.filter(p => updateOf(p) !== "")
  updates.forEach(p => lines.push(p.name + " · update available: " + updateOf(p)))
  return {
    problem: problem,
    connected: phones.some(p => p.connected),
    updates: updates.map(p => p.name),
    tooltip: lines.join("\n")
  }
}

// For the unit tests (node).
if (typeof module !== "undefined") module.exports = { missing: missing, problemOf: problemOf, updateOf: updateOf, summary: summary }
