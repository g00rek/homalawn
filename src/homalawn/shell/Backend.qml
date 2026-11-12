import QtQuick
import Quickshell
import Quickshell.Io

// One connection to the Homa Lawn service (`homalawn serve`) over its unix socket.
//
// Wire format: one JSON object per line. We send {id, method, params}; the service answers
// {id, result} or {id, error: {code, message}}, and after `subscribe` it also pushes
// {event: "state", state} whenever something changes, and {event: "chat", chat} while the
// Agents page's chat is answered, and {event: "pair", pair} while a phone is being paired.
Item {
  id: client
  visible: false

  // Public surface (HomaLawnView, BarWidget and the panel read these).
  property bool connected: false
  property bool attempted: false
  property var state: null
  readonly property var phones: state && Array.isArray(state.phones) ? state.phones : []
  readonly property var busy: state && Array.isArray(state.busy) ? state.busy : []
  readonly property var omarchy: state && state.omarchy ? state.omarchy : null
  // The Agents page's chat (chat.history, then every {event: "chat"}): who answers, the
  // messages, whether an answer is being written. Kept here so it survives leaving the page.
  property var chat: null
  // The pairing the window started (pair.start, then every {event: "pair"}): stage (opening,
  // words, waiting, done, failed, cancelled), words, message, phone (the new phone's id on done).
  // Kept here so it survives leaving the Add phone page; null when none ran on this connection.
  property var pairState: null
  signal toast(string text)
  // The Wallpaper & style page's unapplied drafts, phone name -> settings. Kept here (the
  // connection lives as long as the shell) so a draft survives switching phone, leaving the
  // page and closing the window; nothing in it is ever sent without Apply.
  property var lookDrafts: ({})
  // The Home screen page's unapplied drafts, phone name -> {draft, saved}; same rules.
  property var homeDrafts: ({})
  // The Search & gestures page's unapplied drafts, phone name -> {gestures, search}; same rules.
  property var searchDrafts: ({})
  // Overview's unapplied address lists, phone id -> [address, …] (the added ones, in order);
  // same rules.
  property var addressDrafts: ({})

  readonly property int maxLineChars: 8 * 1024 * 1024
  readonly property int firstPauseMs: 2000
  readonly property int longestPauseMs: 60000

  property string userId: ""
  readonly property string socketPath: {
    var given = Quickshell.env("HOMALAWN_SOCKET") || Quickshell.env("HOMAR_SOCKET")
    if (given) return given
    var runtime = Quickshell.env("XDG_RUNTIME_DIR")
    if (runtime) return runtime + "/homalawn/homalawnd.sock"
    return userId ? "/run/user/" + userId + "/homalawn/homalawnd.sock" : ""
  }

  // The window talks to the systemd unit's service (no HOMALAWN_SOCKET given): only then may it
  // start or restart that unit.
  readonly property bool ownService: !(Quickshell.env("HOMALAWN_SOCKET") || Quickshell.env("HOMAR_SOCKET"))

  property int pauseMs: firstPauseMs
  property int lastRequestId: 0
  // Request id -> callback still waiting for its answer.
  property var waiting: ({})
  // A fresh socket for every attempt: a socket object that failed once does not reliably dial
  // again, and a new one leaves no doubt about which connection a message came from.
  property var link: null

  function offline() {
    return { code: "offline", message: "Homa Lawn service is not running" }
  }

  // Ask the service something. `done(err, result)` runs exactly once: with the reply, or with
  // the offline error when there is no connection or it closes before the answer arrives.
  function call(method, params, done) {
    var reply = typeof done === "function" ? done : function () {}
    if (!connected) {
      Qt.callLater(function () { client.deliver(reply, client.offline(), null) })
      return
    }
    lastRequestId += 1
    var id = lastRequestId
    waiting[id] = reply
    link.write(JSON.stringify({ id: id, method: String(method), params: params || {} }) + "\n")
    link.flush()
  }

  function deliver(reply, err, result) {
    try {
      reply(err, result)
    } catch (e) {
      console.warn("homalawn: a reply handler failed:", e)
    }
  }

  // Start the systemd user unit, then try the socket straight away.
  // `done(ok, message)`: message explains a failure in plain words.
  function startService(done) {
    var report = typeof done === "function" ? done : function () {}
    if (starter.running) {
      report(false, "Homa Lawn is already being started")
      return
    }
    starter.report = report
    starter.running = true
  }

  // Restart the systemd user unit (Settings). Refused for a service given by HOMALAWN_SOCKET.
  function restartService(done) {
    var report = typeof done === "function" ? done : function () {}
    if (!ownService) {
      report(false, "This service does not run under systemd; restart it where it runs")
      return
    }
    if (restarter.running) {
      report(false, "Homa Lawn is already being restarted")
      return
    }
    restarter.report = report
    restarter.running = true
  }

  // Called when the window opens: forget the long pause and try now if we are down.
  function wake() {
    pauseMs = firstPauseMs
    if (!link) {
      retry.stop()
      dial()
    }
  }

  function dial() {
    if (link || !socketPath) return
    link = socketMaker.createObject(client, { path: socketPath })
    link.connected = true
  }

  function onUp(sock) {
    if (sock !== link || connected) return
    retry.stop()
    connected = true
    attempted = true
    pauseMs = firstPauseMs
    call("subscribe", {}, function (err) {
      if (err && err.code !== "offline") client.toast("Homa Lawn did not send its state: " + err.message)
    })
  }

  function onDown(sock) {
    if (sock !== link) return
    link = null
    sock.destroy()
    var wasUp = connected
    connected = false
    attempted = true
    if (wasUp) {
      state = null
      pairState = null
      var orphans = waiting
      waiting = ({})
      for (var id in orphans) deliver(orphans[id], offline(), null)
    }
    if (!retry.running) {
      retry.interval = pauseMs
      pauseMs = Math.min(pauseMs * 2, longestPauseMs)
      retry.start()
    }
  }

  function receive(line) {
    if (line.length > maxLineChars) {
      console.warn("homalawn: dropped a message of " + line.length + " characters from the service")
      return
    }
    if (!line.trim()) return
    var msg
    try {
      msg = JSON.parse(line)
    } catch (e) {
      console.warn("homalawn: the service sent something that is not JSON")
      return
    }
    if (!msg || typeof msg !== "object") return
    if (msg.event === "state") {
      state = msg.state || null
      return
    }
    if (msg.event === "chat") {
      chat = msg.chat || null
      return
    }
    if (msg.event === "pair") {
      pairState = msg.pair || null
      return
    }
    var reply = waiting[msg.id]
    if (!reply) return
    delete waiting[msg.id]
    if (msg.error) {
      // Extra fields come along (home.apply's located `errors`).
      var err = Object.assign({}, msg.error)
      err.code = String(msg.error.code || "error")
      err.message = String(msg.error.message || "")
      deliver(reply, err, null)
    }
    else
      deliver(reply, null, msg.result)
  }

  Component {
    id: socketMaker
    Socket {
      id: sock
      parser: SplitParser {
        onRead: data => { if (sock === client.link) client.receive(data) }
      }
      onConnectionStateChanged: sock.connected ? client.onUp(sock) : client.onDown(sock)
      onError: client.onDown(sock)
    }
  }

  Timer {
    id: retry
    repeat: false
    onTriggered: client.dial()
  }

  Process {
    id: starter
    property var report: null
    command: ["systemctl", "--user", "start", "homalawnd"]
    stderr: StdioCollector { id: starterErrors }
    onExited: function (exitCode) {
      var report = starter.report
      starter.report = null
      client.wake()
      if (!report) return
      if (exitCode === 0) report(true, "")
      else report(false, "Could not start the Homa Lawn service" + (starterErrors.text.trim() ? ": " + starterErrors.text.trim() : ""))
    }
  }

  Process {
    id: restarter
    property var report: null
    command: ["systemctl", "--user", "restart", "homalawnd"]
    stderr: StdioCollector { id: restarterErrors }
    onExited: function (exitCode) {
      var report = restarter.report
      restarter.report = null
      client.wake()
      if (!report) return
      if (exitCode === 0) report(true, "")
      else report(false, "Could not restart the Homa Lawn service" + (restarterErrors.text.trim() ? ": " + restarterErrors.text.trim() : ""))
    }
  }

  // Without HOMALAWN_SOCKET or XDG_RUNTIME_DIR the path needs our numeric user id.
  Process {
    id: whoami
    command: ["id", "-u"]
    stdout: StdioCollector {
      onStreamFinished: {
        client.userId = text.trim()
        client.dial()
      }
    }
  }

  Component.onCompleted: {
    if (socketPath) dial()
    else whoami.running = true
  }
}
