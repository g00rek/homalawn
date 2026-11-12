import QtQuick
import Quickshell.Io
import "../components"
import ".."

// The Agents section: a chat with the user's Omarchy default coding agent, which answers about
// the selected phone through Homa Lawn's tools (the service runs it: chat.send / chat.cancel /
// chat.new; the answer streams in as {event: "chat"}). Beside it, the changes agents made, newest
// first, each with Restore (a confirm step first; the state before it is saved, as in History),
// the coding agents installed here with Connect / Reconnect (whether Homa Lawn's tool server is in
// each one's settings: agents.status / agents.connect), and the recipes agents follow (their
// titles; recipes not marked verified are left out). With no phone paired there is no chat: one
// sentence and Add phone.
Item {
  id: page

  // Given by HomaLawnView.
  property var backend: null
  property string phone: ""
  property string phoneName: ""
  property var phoneState: null
  property var host: null

  property var recipeRows: []
  property var changeRows: []
  property string recipesError: ""
  property string changesError: ""
  property bool loaded: false
  property string confirming: ""     // the change waiting for its confirm ("phone/name")
  property string restoring: ""
  property bool sending: false       // chat.send asked, no answer yet
  property bool wasRunning: false
  // The coding agents installed here (agents.status) and the one being connected.
  property var agentRows: []
  property string agentsError: ""
  property bool agentsRead: false
  property string connecting: ""
  readonly property var presence: ({ here: true })

  readonly property bool up: !!backend && backend.connected
  readonly property var phones: up ? (backend.phones || []) : []
  readonly property bool noPhone: up && phones.length === 0
  // The recipes a person sees: by title, without the ones not marked verified.
  readonly property var shownRecipes: recipeRows.filter(r => r && r.verified !== false)
  readonly property var chat: up ? backend.chat : null
  readonly property var agent: chat ? chat.agent : null
  readonly property bool running: !!chat && !!chat.running
  readonly property bool canChat: !!agent && agent.supported && agent.installed
  readonly property string agentName: agent && agent.name ? agent.name : "Your agent"
  function kindLabel(kind) { return host ? host.kindLabel(kind) : String(kind || "") }
  readonly property var examples: [
    "What apps are in the dock?",
    "Put my most used apps on the first screen",
    "Make a folder called Work with Slack and Gmail"
  ]

  function toast(text) { if (host && text) host.showToast(text) }

  function load() {
    if (!up) return
    var presence = page.presence
    backend.call("chat.history", {}, function (err, result) {
      if (!presence.here) return
      page.loaded = true
      if (err) page.toast("Could not read the chat: " + (err.message || err.code))
      else page.backend.chat = result
    })
    backend.call("recipes.list", {}, function (err, result) {
      if (!presence.here) return
      page.recipesError = err ? (err.message || err.code) : ""
      page.recipeRows = err ? [] : (result || [])
    })
    loadAgents()
    loadChanges()
  }
  function loadAgents() {
    if (!up) return
    var presence = page.presence
    backend.call("agents.status", {}, function (err, result) {
      if (!presence.here) return
      page.agentsError = err ? (err.message || err.code) : ""
      if (!err) { page.agentRows = result.agents || []; page.agentsRead = true }
    })
  }
  // (Re)register `homalawn mcp` in one agent; the answer is a toast and the row follows.
  function connectAgent(id) {
    if (!up || connecting) return false
    connecting = id
    var presence = page.presence
    backend.call("agents.connect", { agent: id }, function (err, result) {
      if (!presence.here) return
      page.connecting = ""
      page.toast(err ? (err.message || err.code) : result.text)
      if (!err && result.agents) page.agentRows = result.agents
      else page.loadAgents()
    })
    return true
  }
  function agentState(a) {
    if (a.error) return { text: "Unreadable", color: Theme.red }
    if (a.registered && a.enabled === false) return { text: "Turned off", color: Theme.yellow }
    if (a.registered) return { text: "Connected", color: Theme.green }
    return { text: "Not connected", color: Theme.yellow }
  }
  function loadChanges() {
    if (!up) return
    var presence = page.presence
    backend.call("agents.changes", {}, function (err, result) {
      if (!presence.here) return
      page.changesError = err ? (err.message || err.code) : ""
      page.changeRows = err ? [] : (result || [])
    })
  }

  // ── the chat ───────────────────────────────────────────────────────────────────────────
  function send(text) {
    var words = String(text === undefined ? input.text : text).trim()
    if (!up || !canChat || running || sending || !words) return false
    sending = true
    var presence = page.presence
    var params = { text: words }
    if (page.phone) params.phone = page.phone
    input.text = ""
    backend.call("chat.send", params, function (err, result) {
      if (!presence.here) return
      page.sending = false
      if (err) {
        page.toast(err.message || err.code)
        if (!input.text) input.text = words
      } else {
        page.backend.chat = result
      }
    })
    return true
  }
  function stop() {
    if (!up || !running) return false
    backend.call("chat.cancel", {}, function (err) { if (err) page.toast(err.message || err.code) })
    return true
  }
  function newChat() {
    if (!up) return false
    var presence = page.presence
    backend.call("chat.new", {}, function (err, result) {
      if (!presence.here) return
      if (err) page.toast(err.message || err.code)
      else page.backend.chat = result
    })
    return true
  }
  // Omarchy's own picker for the default agent (its menu route setup.default.agent).
  function changeAgent() {
    if (menu.running) return false
    menu.running = true
    return true
  }

  // The ListModel follows chat.messages in place, so streamed text never rebuilds the list.
  ListModel { id: messages }
  function sync() {
    var list = chat && Array.isArray(chat.messages) ? chat.messages : []
    var atEnd = view.atYEnd || view.contentHeight <= view.height
    if (list.length < messages.count) messages.clear()
    for (var i = 0; i < list.length; i++) {
      var m = list[i]
      var row = { role: String(m.role || "note"), text: String(m.text || ""), error: !!m.error }
      if (i < messages.count) {
        var old = messages.get(i)
        if (old.role !== row.role) messages.set(i, row)
        else if (old.text !== row.text || old.error !== row.error) {
          messages.setProperty(i, "text", row.text)
          messages.setProperty(i, "error", row.error)
        }
      } else {
        messages.append(row)
      }
    }
    if (atEnd) Qt.callLater(function () { view.positionViewAtEnd() })
  }
  onChatChanged: sync()
  onRunningChanged: {
    if (wasRunning && !running) loadChanges()   // what the agent changed shows on the right
    wasRunning = running
  }

  function phoneOnline(name) {
    var p = phones.find(p => p.name === name)
    return !!p && !!p.connected
  }
  function busy(name) { return !!backend && (backend.busy || []).some(b => b.name === name) }

  function restore(row) {
    if (!up || restoring || !phoneOnline(row.phone) || busy(row.phone)) return false
    confirming = ""
    restoring = row.phone + "/" + row.name
    var presence = page.presence
    backend.call("snapshot.restore", { phone: row.phone, name: row.name }, function (err, result) {
      if (!presence.here) return
      page.restoring = ""
      page.toast(err ? (err.message || err.code) : (result && result.text ? result.text : "Restored."))
      page.loadChanges()
    })
    return true
  }

  onUpChanged: load()
  onBackendChanged: load()
  Component.onCompleted: sync()
  Connections {
    target: page.host
    ignoreUnknownSignals: true
    function onUndone() { page.loadChanges() }
  }
  Component.onDestruction: presence.here = false

  Process {
    id: menu
    command: ["omarchy-menu", "summon", "setup.default.agent"]
    onExited: function (exitCode) {
      if (exitCode !== 0) page.toast("Could not open Omarchy's menu. Choose the agent with `omarchy default agent` in a terminal.")
      recheck.restart()
    }
  }
  // A new default agent shows up here without reopening the page.
  Timer {
    id: recheck
    interval: 4000
    repeat: true
    running: page.up && !page.running
    onTriggered: {
      var presence = page.presence
      page.backend.call("chat.history", {}, function (err, result) {
        if (presence.here && !err && !page.running) page.backend.chat = result
      })
    }
  }

  // ── chat pane ────────────────────────────────────────────────────────────────────────────
  // ── no phone paired: no chat ───────────────────────────────────────────────────────────────
  Column {
    visible: page.noPhone
    anchors.centerIn: pane
    width: Math.min(pane.width - 64, 440)
    spacing: 14
    Glyph { anchors.horizontalCenter: parent.horizontalCenter; code: Icons.agents; size: 34; color: Theme.muted }
    Txt {
      width: parent.width
      horizontalAlignment: Text.AlignHCenter
      wrapMode: Text.Wrap
      text: "Agents work on a paired phone. Add your phone first, then ask here."
      color: Theme.brightForeground
    }
    AccentButton {
      anchors.horizontalCenter: parent.horizontalCenter
      text: "Add phone"
      onClicked: if (page.host) page.host.showPage("add")
    }
  }

  Rectangle {
    id: pane
    visible: page.up && !page.noPhone
    anchors.left: parent.left
    anchors.top: parent.top
    anchors.bottom: parent.bottom
    anchors.right: side.left
    anchors.margins: Theme.margin
    anchors.rightMargin: Theme.gap
    radius: 6
    color: Theme.darkBackground
    border.width: 1
    border.color: Theme.selection

    // header: who answers, change it, new chat
    Item {
      id: header
      x: 16
      width: parent.width - 32
      height: Math.max(52, headCol.implicitHeight + 18)
      Glyph {
        id: robot
        anchors.verticalCenter: parent.verticalCenter
        code: Icons.agents
        size: 18
        color: page.canChat ? Theme.accent : Theme.muted
      }
      Column {
        id: headCol
        anchors.left: robot.right
        anchors.leftMargin: 8
        anchors.right: newButton.left
        anchors.rightMargin: 12
        anchors.verticalCenter: parent.verticalCenter
        spacing: 2
        // One line: "<agent> about <phone>", cut short when narrow, and Change right after it.
        Item {
          width: parent.width
          height: who.implicitHeight
          Txt {
            id: who
            width: Math.min(implicitWidth, parent.width - changeLink.implicitWidth - 12)
            elide: Text.ElideRight
            text: (page.agent && page.agent.name ? page.agent.name : "No default agent")
              + (page.phoneName ? " about " + page.phoneName : "")
            font.bold: true
            color: Theme.brightForeground
          }
          Txt {
            id: changeLink
            x: who.width + 12
            y: who.baselineOffset - baselineOffset
            text: "Change"
            font.pixelSize: Theme.small
            font.underline: changeHover.containsMouse
            color: Theme.accent
            MouseArea {
              id: changeHover
              anchors.fill: parent
              hoverEnabled: true
              cursorShape: Qt.PointingHandCursor
              onClicked: page.changeAgent()
            }
          }
        }
        Txt {
          width: parent.width
          wrapMode: Text.Wrap
          font.pixelSize: Theme.tiny
          color: Theme.darkForeground
          text: page.running ? page.agentName + " is working on " + (page.chat.phone || "your phone") + "…"
            : "Your Omarchy default agent. It uses Homa Lawn's tools only."
        }
      }
      OutlineButton {
        id: newButton
        anchors.right: parent.right
        anchors.verticalCenter: parent.verticalCenter
        text: "New chat"
        active: messages.count > 0
        onClicked: page.newChat()
      }
    }
    Rectangle { id: rule; anchors.top: header.bottom; width: parent.width; height: 1; color: Theme.selection }

    ListView {
      id: view
      anchors.top: rule.bottom
      anchors.bottom: composer.top
      anchors.left: parent.left
      anchors.right: parent.right
      anchors.margins: 1
      clip: true
      spacing: 10
      topMargin: 14
      bottomMargin: 14
      boundsBehavior: Flickable.StopAtBounds
      model: messages
      visible: page.canChat || messages.count > 0
      readonly property real maxBubble: Math.min(width * 0.78, 640)

      delegate: Item {
        id: line
        required property string role
        required property string text
        required property bool error
        width: view.width
        height: role === "user" || role === "agent" ? bubble.height : (role === "step" ? 18 : noteText.implicitHeight)

        // The agent writes Markdown (lists, bold); the user's own words stay plain text.
        readonly property bool markdown: role === "agent"
        Txt {
          id: measure
          visible: false
          text: line.text
          font.pixelSize: Theme.body
          textFormat: line.markdown ? Text.MarkdownText : Text.PlainText
        }
        Rectangle {
          id: bubble
          visible: line.role === "user" || line.role === "agent"
          x: line.role === "user" ? line.width - width - 16 : 16
          width: Math.min(measure.implicitWidth + 2, view.maxBubble) + 24
          height: body.implicitHeight + 16
          radius: 8
          color: line.role === "user" ? Theme.withAlpha(Theme.accent, 0.16) : Theme.lighterBackground
          border.width: 1
          border.color: line.role === "user" ? Theme.withAlpha(Theme.accent, 0.45) : Theme.selection
          TextEdit {
            id: body
            x: 12
            y: 8
            width: parent.width - 24
            text: line.text
            readOnly: true
            selectByMouse: true
            wrapMode: TextEdit.Wrap
            textFormat: line.markdown ? TextEdit.MarkdownText : TextEdit.PlainText
            color: Theme.brightForeground
            selectionColor: Theme.accent
            font.family: Theme.font
            font.pixelSize: Theme.body
          }
        }
        Row {
          visible: line.role === "step"
          x: 28
          spacing: 6
          Glyph { code: Icons.check; size: 11; color: Theme.green; anchors.verticalCenter: parent.verticalCenter }
          Txt { text: "used: " + line.text; font.pixelSize: Theme.tiny; color: Theme.darkForeground; anchors.verticalCenter: parent.verticalCenter }
        }
        Txt {
          id: noteText
          visible: line.role === "note"
          x: 24
          width: line.width - 48
          horizontalAlignment: Text.AlignHCenter
          wrapMode: Text.Wrap
          text: line.text
          font.pixelSize: Theme.tiny
          color: line.error ? Theme.red : Theme.darkForeground
        }
      }

      footer: Item {
        width: view.width
        height: working.visible ? 34 : 0
        Row {
          id: working
          visible: {
            var l = page.chat && Array.isArray(page.chat.messages) ? page.chat.messages : []
            return page.running && (l.length === 0 || l[l.length - 1].role !== "agent")
          }
          x: 28
          y: 12
          spacing: 8
          Repeater {
            model: 3
            Rectangle {
              required property int index
              width: 6; height: 6; radius: 3
              color: Theme.muted
              anchors.verticalCenter: parent.verticalCenter
              SequentialAnimation on opacity {
                running: working.visible
                loops: Animation.Infinite
                PauseAnimation { duration: index * 160 }
                NumberAnimation { from: 0.3; to: 1; duration: 320 }
                NumberAnimation { from: 1; to: 0.3; duration: 320 }
                PauseAnimation { duration: (2 - index) * 160 }
              }
            }
          }
          Txt { text: page.agentName + " is working…"; font.pixelSize: Theme.tiny; color: Theme.darkForeground }
        }
      }
    }

    // empty chat: what to ask
    Column {
      visible: page.canChat && messages.count === 0
      anchors.centerIn: view
      width: Math.min(view.width - 64, 460)
      spacing: 12
      Glyph { anchors.horizontalCenter: parent.horizontalCenter; code: Icons.agents; size: 34; color: Theme.muted }
      Txt {
        width: parent.width
        horizontalAlignment: Text.AlignHCenter
        wrapMode: Text.Wrap
        text: "Ask " + page.agentName + " to change " + (page.phoneName || "your phone") + "'s home screen"
        color: Theme.brightForeground
        font.pixelSize: Theme.heading
        font.bold: true
      }
      Txt {
        width: parent.width
        horizontalAlignment: Text.AlignHCenter
        wrapMode: Text.Wrap
        text: "It works only through Homa Lawn's tools. Every change is saved first, so Restore on the right puts it back."
        color: Theme.darkForeground
        font.pixelSize: Theme.small
      }
      Repeater {
        model: page.examples
        Rectangle {
          required property string modelData
          anchors.horizontalCenter: parent.horizontalCenter
          width: exampleText.implicitWidth + 28
          height: 30
          radius: 15
          color: exampleHover.containsMouse ? Theme.selection : Theme.lighterBackground
          border.width: 1
          border.color: Theme.muted
          Txt { id: exampleText; anchors.centerIn: parent; text: parent.modelData; font.pixelSize: Theme.small }
          MouseArea {
            id: exampleHover
            anchors.fill: parent
            hoverEnabled: true
            cursorShape: Qt.PointingHandCursor
            onClicked: { input.text = parent.modelData; input.forceActiveFocus(); input.cursorPosition = input.length }
          }
        }
      }
    }

    // the default agent cannot chat here (not supported, not installed or none chosen)
    Column {
      visible: !!page.agent && !page.canChat && messages.count === 0
      anchors.centerIn: view
      width: Math.min(view.width - 64, 480)
      spacing: 12
      Glyph { anchors.horizontalCenter: parent.horizontalCenter; code: Icons.agents; size: 34; color: Theme.yellow }
      Txt {
        width: parent.width
        horizontalAlignment: Text.AlignHCenter
        wrapMode: Text.Wrap
        text: !page.agent || !page.agent.id ? "Choose a default coding agent"
          : !page.agent.supported ? page.agentName + " can't chat here yet"
          : page.agent.setup === "connect" ? page.agentName + " can't reach your phone yet"
          : page.agentName + " is not installed yet"
        color: Theme.brightForeground
        font.pixelSize: Theme.heading
        font.bold: true
      }
      Txt {
        width: parent.width
        horizontalAlignment: Text.AlignHCenter
        wrapMode: Text.Wrap
        text: (page.agent && page.agent.why ? page.agent.why : "")
              + (page.agent && page.agent.id && !page.agent.supported ? " The chat works with Claude Code, Codex and Gemini." : "")
        color: Theme.foreground
        font.pixelSize: Theme.small
      }
      Row {
        anchors.horizontalCenter: parent.horizontalCenter
        spacing: 10
        AccentButton {
          visible: !!page.agent && page.agent.setup === "connect" && !!page.agent.id
          text: page.connecting === (page.agent ? page.agent.id : "") ? "Connecting…" : "Connect " + page.agentName
          active: !page.connecting
          onClicked: page.connectAgent(page.agent.id)
        }
        OutlineButton {
          visible: !!page.agent && page.agent.setup === "connect"
          text: "Change default agent"
          onClicked: page.changeAgent()
        }
        AccentButton {
          visible: !page.agent || page.agent.setup !== "connect"
          text: "Change default agent"
          onClicked: page.changeAgent()
        }
      }
    }

    // composer
    Item {
      id: composer
      anchors.left: parent.left
      anchors.right: parent.right
      anchors.bottom: parent.bottom
      height: page.canChat ? box.height + 28 : (messages.count > 0 ? 44 : 0)
      clip: true
      Txt {
        visible: !page.canChat && messages.count > 0
        anchors.centerIn: parent
        text: page.agent && page.agent.why ? page.agent.why : ""
        width: parent.width - 32
        elide: Text.ElideRight
        horizontalAlignment: Text.AlignHCenter
        font.pixelSize: Theme.tiny
        color: Theme.yellow
      }
      Rectangle {
        id: box
        visible: page.canChat
        x: 16
        y: 12
        width: parent.width - 32
        height: Math.min(Math.max(input.contentHeight + 18, 40), 132)
        radius: 6
        color: Theme.darkerBackground
        border.width: 1
        border.color: input.activeFocus ? Theme.accent : Theme.muted
        Flickable {
          id: inputFlick
          anchors.left: parent.left
          anchors.right: actions.left
          anchors.top: parent.top
          anchors.bottom: parent.bottom
          anchors.margins: 9
          anchors.leftMargin: 12
          contentHeight: input.contentHeight
          clip: true
          boundsBehavior: Flickable.StopAtBounds
          TextEdit {
            id: input
            width: inputFlick.width
            wrapMode: TextEdit.Wrap
            textFormat: TextEdit.PlainText
            color: Theme.brightForeground
            selectionColor: Theme.accent
            font.family: Theme.font
            font.pixelSize: Theme.body
            Keys.onReturnPressed: function (event) {
              if (event.modifiers & Qt.ShiftModifier) { event.accepted = false; return }
              event.accepted = true
              page.send()
            }
            onCursorRectangleChanged: {
              if (cursorRectangle.y < inputFlick.contentY) inputFlick.contentY = cursorRectangle.y
              else if (cursorRectangle.y + cursorRectangle.height > inputFlick.contentY + inputFlick.height)
                inputFlick.contentY = cursorRectangle.y + cursorRectangle.height - inputFlick.height
            }
          }
          Txt {
            visible: input.text === "" && !input.activeFocus
            text: "Ask " + page.agentName + " about " + (page.phoneName || "your phone") + "…"
            color: Theme.darkForeground
          }
        }
        Row {
          id: actions
          anchors.right: parent.right
          anchors.rightMargin: 6
          anchors.bottom: parent.bottom
          anchors.bottomMargin: 6
          spacing: 6
          AccentButton {
            visible: !page.running
            text: "Send"
            working: page.sending
            active: input.text.trim() !== ""
            onClicked: page.send()
          }
          OutlineButton {
            visible: page.running
            text: "Stop"
            onClicked: page.stop()
          }
        }
      }
    }
  }

  // ── side column: what agents changed, recipes ──────────────────────────────────────────────
  Flickable {
    id: side
    visible: page.up
    anchors.right: parent.right
    anchors.top: parent.top
    anchors.bottom: parent.bottom
    anchors.topMargin: Theme.margin
    anchors.bottomMargin: Theme.margin
    anchors.rightMargin: Theme.margin
    width: Math.max(300, Math.min(380, parent.width * 0.32))
    contentWidth: width
    contentHeight: sideColumn.height
    clip: true
    boundsBehavior: Flickable.StopAtBounds
    interactive: contentHeight > height

    Column {
      id: sideColumn
      width: side.width
      spacing: Theme.gap

      Card {
        title: "Recent agent changes"
        subtitle: "Newest first. Restore puts the phone back to just before that change."
        spacing: 0
        Txt {
          visible: page.changeRows.length === 0
          width: parent.width
          wrapMode: Text.Wrap
          text: page.changesError ? "Could not read the changes: " + page.changesError
            : "No changes by agents yet."
          color: page.changesError ? Theme.red : Theme.darkForeground
          font.pixelSize: Theme.small
          bottomPadding: 4
        }
        Repeater {
          model: page.changeRows
          Item {
            id: change
            required property var modelData
            required property int index
            readonly property string key: modelData.phone + "/" + modelData.name
            readonly property bool asking: page.confirming === key
            readonly property bool can: page.phoneOnline(modelData.phone) && !page.busy(modelData.phone) && !page.restoring
            width: parent.width
            height: asking ? changeAsk.height + 16 : 54
            Rectangle { visible: change.index > 0; width: parent.width; height: 1; color: Theme.selection }
            ConfirmBar {
              id: changeAsk
              visible: change.asking
              y: 8
              text: "Restore " + change.modelData.phone + " to before this change?"
              detail: (Theme.when(change.modelData.time) ? Theme.when(change.modelData.time) + " · " : "") + page.kindLabel(change.modelData.kind)
              action: "Restore"
              active: change.can
              onConfirmed: page.restore(change.modelData)
              onCancelled: page.confirming = ""
            }
            Column {
              visible: !change.asking
              anchors.left: parent.left
              anchors.right: changeActions.left
              anchors.rightMargin: 8
              anchors.verticalCenter: parent.verticalCenter
              spacing: 3
              Txt {
                width: parent.width
                elide: Text.ElideRight
                text: change.modelData.title ? String(change.modelData.title) : page.kindLabel(change.modelData.kind)
                font.pixelSize: Theme.small
                color: Theme.brightForeground
              }
              Txt {
                width: parent.width
                elide: Text.ElideRight
                text: (Theme.when(change.modelData.time) || "—") + " · " + change.modelData.phone
                font.pixelSize: Theme.tiny
                color: Theme.darkForeground
              }
            }
            OutlineButton {
              id: changeActions
              visible: !change.asking
              anchors.right: parent.right
              anchors.verticalCenter: parent.verticalCenter
              text: page.restoring === change.key ? "Restoring…" : "Restore"
              active: change.can
              onClicked: page.confirming = change.key
            }
          }
        }
      }

      Card {
        title: "Coding agents"
        subtitle: "Agents installed here and whether they can reach your phones. Installing Homa Lawn connects them; Connect does it again."
        spacing: 0
        Txt {
          visible: page.agentRows.length === 0
          width: parent.width
          wrapMode: Text.Wrap
          text: page.agentsError ? "Could not read the agents: " + page.agentsError
            : page.agentsRead ? "No coding agent Homa Lawn works with is installed. Choose one with `omarchy default agent`; Homa Lawn adds itself to it."
            : "Reading…"
          color: page.agentsError ? Theme.red : Theme.darkForeground
          font.pixelSize: Theme.small
          bottomPadding: 4
        }
        Repeater {
          model: page.agentRows
          Item {
            id: agentRow
            required property var modelData
            required property int index
            readonly property var st: page.agentState(modelData)
            width: parent.width
            height: Math.max(agentText.implicitHeight, agentButton.implicitHeight) + 18
            Rectangle { visible: agentRow.index > 0; width: parent.width; height: 1; color: Theme.selection }
            Column {
              id: agentText
              anchors.left: parent.left
              anchors.right: agentButton.left
              anchors.rightMargin: 10
              anchors.verticalCenter: parent.verticalCenter
              spacing: 2
              Txt {
                width: parent.width
                elide: Text.ElideRight
                text: agentRow.modelData.name + (agentRow.modelData.default ? " · default" : "")
                color: Theme.brightForeground
                font.pixelSize: Theme.small
              }
              Txt {
                width: parent.width
                wrapMode: Text.Wrap
                text: agentRow.st.text + (agentRow.modelData.error ? ": " + agentRow.modelData.error : "")
                color: agentRow.st.color
                font.pixelSize: Theme.tiny
              }
            }
            OutlineButton {
              id: agentButton
              anchors.right: parent.right
              anchors.verticalCenter: parent.verticalCenter
              text: page.connecting === agentRow.modelData.id ? "Connecting…"
                : agentRow.modelData.registered ? "Reconnect" : "Connect"
              active: !page.connecting
              onClicked: page.connectAgent(agentRow.modelData.id)
            }
          }
        }
      }

      Card {
        title: "Recipes"
        subtitle: "Steps agents follow for jobs inside other apps."
        spacing: 10
        Txt {
          visible: page.shownRecipes.length === 0
          width: parent.width
          wrapMode: Text.Wrap
          text: page.recipesError ? "Could not read the recipes: " + page.recipesError : "No recipes."
          color: page.recipesError ? Theme.red : Theme.darkForeground
          font.pixelSize: Theme.small
        }
        Repeater {
          model: page.shownRecipes
          Column {
            required property var modelData
            width: parent.width
            spacing: 2
            Txt {
              width: parent.width
              wrapMode: Text.Wrap
              text: modelData.title || modelData.name
              font.bold: true
              font.pixelSize: Theme.small
              color: Theme.brightForeground
            }
            Txt {
              width: parent.width
              wrapMode: Text.Wrap
              visible: text !== ""
              text: modelData.description || ""
              font.pixelSize: Theme.tiny
              color: Theme.darkForeground
            }
          }
        }
      }
    }
  }
}
