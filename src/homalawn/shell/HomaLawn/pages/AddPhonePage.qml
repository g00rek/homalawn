import QtQuick
import "../components"
import ".."

// Add phone, two paths:
//  - New phone: (1) a QR code the phone's camera scans to download Homa Lawn: a release from its
//    HTTPS address on GitHub (no time limit, `expires_at` null), a build made here from this
//    computer (app.serve / app.serve_stop; state.serving says while it is offered, with the
//    server's own lines in `message`), (2) the taps on the phone, (3) pairing.
//  - This phone already has Homa Lawn: pairing straight away.
// Pairing is one flow on both paths: Find phones / an address / Pair, then the four words. It runs
// in the service; this page shows its progress (Backend.pairState) and Cancel. With no phone paired
// yet this page is the window's first screen and opens with a one-sentence welcome.
Item {
  id: page

  // Given by HomaLawnView.
  property var backend: null
  property string phone: ""
  property var phoneState: null
  property var host: null

  // ── pairing ──
  // Phones the service found on the network (pair.find): [{host, name}]; null until Find phones ran.
  property var found: null
  property bool finding: false
  // An error from pair.find / pair.start themselves (busy, a bad address); the pairing's own
  // failures arrive as the "failed" stage.
  property string askError: ""
  readonly property var pairState: backend ? backend.pairState : null
  readonly property string stage: pairState && pairState.stage ? pairState.stage : ""
  readonly property bool pairing: stage === "opening" || stage === "words" || stage === "waiting"
  readonly property var words: pairState && Array.isArray(pairState.words) ? pairState.words : []
  readonly property string message: pairState && pairState.message ? pairState.message : ""

  // ── which path: "new" or "existing" ──
  property string path: ""
  readonly property bool welcome: !!host && host.serviceUp && host.phones.length === 0

  // ── serving the app (New phone, step 1) ──
  readonly property var serving: backend && backend.state ? backend.state.serving || null : null
  // app.serve's answer: {url, urls, expires_at, qr, version, source, file}; null while not serving.
  // `expires_at` is null for a release (the phone downloads it from GitHub; no countdown).
  property var offer: null
  property bool serveAsking: false
  property bool stopping: false
  property string serveError: ""
  // Why the last QR code is gone: "expired", "stopped" or "".
  property string serveEnded: ""
  property real now: Date.now() / 1000
  readonly property bool timed: !!offer && offer.expires_at !== null && offer.expires_at !== undefined
  readonly property int remaining: timed ? Math.max(0, Math.round(offer.expires_at - now)) : 0
  readonly property string serveMessage: serving && serving.message ? String(serving.message) : ""
  readonly property var presence: ({ here: true })
  Component.onDestruction: presence.here = false

  Component.onCompleted: {
    path = serving ? "new" : pairing ? "existing" : "new"
    if (serving) serve()
  }

  Timer {
    running: page.timed
    interval: 1000
    repeat: true
    triggeredOnStart: true
    onTriggered: page.now = Date.now() / 1000
  }

  onServingChanged: {
    if (!serving && offer) {
      now = Date.now() / 1000
      serveEnded = stopping || !timed || now < offer.expires_at - 3 ? "stopped" : "expired"
      offer = null
      stopping = false
    } else if (serving && !offer && !serveAsking) {
      serve()          // served already (the page was reopened): the same answer, with its QR code
    }
  }

  function choose(which) {
    if (which !== "new" && which !== "existing") return false
    path = which
    return true
  }

  function serve() {
    if (!backend || serveAsking) return false
    serveAsking = true
    serveError = ""
    serveEnded = ""
    var presence = page.presence
    backend.call("app.serve", {}, function (err, result) {
      if (!presence.here) return
      page.serveAsking = false
      if (err) { page.serveError = err.message || err.code; return }
      page.now = Date.now() / 1000
      page.offer = result
    })
    return true
  }

  function stopServing() {
    if (!backend || !offer || stopping) return false
    stopping = true
    var presence = page.presence
    backend.call("app.serve_stop", {}, function (err) {
      if (!presence.here) return
      if (err) { page.stopping = false; page.serveError = err.message || err.code }
    })
    return true
  }

  function find() {
    if (!backend || finding || pairing) return false
    finding = true
    askError = ""
    backend.call("pair.find", {}, function (err, result) {
      page.finding = false
      if (err) { page.askError = err.message || err.code; return }
      page.found = Array.isArray(result) ? result : []
    })
    return true
  }

  function start(address) {
    var to = String(address || "").trim()
    if (!backend || pairing || to === "") return false
    askError = ""
    backend.call("pair.start", { host: to }, function (err) {
      if (err) page.askError = err.message || err.code
    })
    return true
  }

  function cancel() {
    if (!backend || !pairing) return false
    backend.call("pair.cancel", {}, function () {})
    return true
  }

  // "9:41"
  function clock(seconds) {
    var m = Math.floor(seconds / 60), s = seconds % 60
    return m + ":" + (s < 10 ? "0" : "") + s
  }

  // ── pieces ──
  // A numbered line: the number in a ring, the words beside it.
  component Step: Row {
    id: step
    property int n: 0
    property string text: ""
    width: parent ? parent.width : 0
    spacing: 12
    Rectangle {
      width: 22; height: 22; radius: 11
      color: "transparent"
      border.width: 1
      border.color: Theme.accent
      Txt { anchors.centerIn: parent; text: String(step.n); font.pixelSize: Theme.small; color: Theme.accent }
    }
    Txt {
      width: step.width - 34
      topPadding: 2
      wrapMode: Text.Wrap
      text: step.text
      color: Theme.brightForeground
    }
  }

  // A command to type, in the terminal's colours.
  component Command: Rectangle {
    property string text: ""
    width: cmd.implicitWidth + 16
    height: cmd.implicitHeight + 6
    radius: 4
    color: Theme.darkerBackground
    border.width: 1
    border.color: Theme.selection
    Txt { id: cmd; anchors.centerIn: parent; text: parent.text; color: Theme.green; font.pixelSize: Theme.small }
  }

  // A card for one step of the new phone's path: the number, the heading, a line, then the content.
  component StepCard: Rectangle {
    id: stepCard
    property int n: 0
    property string title: ""
    property string subtitle: ""
    default property alias content: stepInner.data
    property alias spacing: stepInner.spacing
    width: parent ? parent.width : 0
    height: stepInner.y + stepInner.implicitHeight + 18
    radius: 6
    color: Theme.darkBackground
    border.width: 1
    border.color: Theme.selection
    Rectangle {
      id: badge
      x: 18; y: 16
      width: 26; height: 26; radius: 13
      color: Theme.accent
      Txt { anchors.centerIn: parent; text: String(stepCard.n); font.bold: true; font.pixelSize: Theme.small; color: Theme.darkerBackground }
    }
    Column {
      id: stepHead
      anchors.left: badge.right
      anchors.leftMargin: 12
      anchors.right: parent.right
      anchors.rightMargin: 18
      y: 18
      spacing: 4
      Heading { width: parent.width; wrapMode: Text.Wrap; text: stepCard.title }
      Txt {
        visible: stepCard.subtitle !== ""
        width: parent.width
        wrapMode: Text.Wrap
        text: stepCard.subtitle
        font.pixelSize: Theme.small
        color: Theme.darkForeground
      }
    }
    Column {
      id: stepInner
      x: stepHead.x
      y: stepHead.y + stepHead.implicitHeight + 14
      width: stepHead.width
      spacing: 10
    }
  }

  // One of the two paths, as a large choice.
  component PathChoice: Rectangle {
    id: choice
    property string key: ""
    property string title: ""
    property string text: ""
    property var owner: null           // the page
    readonly property bool chosen: !!owner && owner.path === key
    height: Math.max(choiceText.implicitHeight + 28, 72)
    radius: 6
    color: chosen ? Theme.withAlpha(Theme.accent, 0.10) : (choiceHover.containsMouse ? Theme.lighterBackground : Theme.darkBackground)
    border.width: chosen ? 2 : 1
    border.color: chosen ? Theme.accent : Theme.selection
    Rectangle {
      id: radio
      x: 16
      y: 17
      width: 16; height: 16; radius: 8
      color: "transparent"
      border.width: 2
      border.color: choice.chosen ? Theme.accent : Theme.muted
      Rectangle { anchors.centerIn: parent; width: 8; height: 8; radius: 4; color: Theme.accent; visible: choice.chosen }
    }
    Column {
      id: choiceText
      anchors.left: radio.right
      anchors.leftMargin: 12
      anchors.right: parent.right
      anchors.rightMargin: 16
      y: 14
      spacing: 4
      Txt { width: parent.width; wrapMode: Text.Wrap; text: choice.title; font.bold: true; color: Theme.brightForeground }
      Txt { width: parent.width; wrapMode: Text.Wrap; text: choice.text; font.pixelSize: Theme.small; color: Theme.darkForeground }
    }
    MouseArea {
      id: choiceHover
      anchors.fill: parent
      hoverEnabled: true
      cursorShape: Qt.PointingHandCursor
      onClicked: choice.owner.choose(choice.key)
    }
  }

  // The pairing: Find phones, or an address, then Pair; the four words while it runs.
  component PairFlow: Column {
    id: flow
    property var owner: null           // the page
    width: parent ? parent.width : 0
    spacing: 12

    Row {
      spacing: 8
      OutlineButton {
        text: flow.owner.finding ? "Looking…" : "Find phones"
        active: !flow.owner.finding && !flow.owner.pairing
        onClicked: flow.owner.find()
      }
      Rectangle {
        width: 220
        height: addressInput.implicitHeight + 12
        radius: 4
        color: Theme.darkerBackground
        border.width: 1
        border.color: addressInput.activeFocus ? Theme.accent : Theme.muted
        TextInput {
          id: addressInput
          anchors.left: parent.left
          anchors.right: parent.right
          anchors.verticalCenter: parent.verticalCenter
          anchors.margins: 8
          clip: true
          color: Theme.brightForeground
          font.family: Theme.font
          font.pixelSize: Theme.small
          selectByMouse: true
          enabled: !flow.owner.pairing
          onAccepted: flow.owner.start(text)
          Txt {
            visible: addressInput.text === "" && !addressInput.activeFocus
            text: "Phone address"
            font.pixelSize: Theme.small
            color: Theme.darkForeground
          }
        }
      }
      AccentButton {
        text: "Pair"
        active: !flow.owner.pairing && addressInput.text.trim() !== ""
        onClicked: flow.owner.start(addressInput.text)
      }
    }

    Txt {
      visible: flow.owner.found !== null && flow.owner.found.length === 0
      width: parent.width
      wrapMode: Text.Wrap
      font.pixelSize: Theme.small
      color: Theme.darkForeground
      text: "No phone with Homa Lawn found on this network. Open Homa Lawn on the phone and try again, or type its address."
    }
    Repeater {
      model: flow.owner.found || []
      delegate: Row {
        spacing: 12
        Txt {
          anchors.verticalCenter: parent.verticalCenter
          text: modelData.name + "   " + modelData.host
          color: Theme.brightForeground
        }
        AccentButton {
          text: "Pair"
          active: !flow.owner.pairing
          onClicked: flow.owner.start(modelData.host)
        }
      }
    }

    Txt {
      visible: flow.owner.stage === "opening" || (flow.owner.stage === "waiting" && flow.owner.words.length === 0)
      width: parent.width
      wrapMode: Text.Wrap
      color: Theme.brightForeground
      text: flow.owner.message
    }
    Txt {
      visible: (flow.owner.stage === "words" || flow.owner.stage === "waiting") && flow.owner.words.length > 0
      text: flow.owner.words.join("   ")
      font.pixelSize: Theme.title
      font.bold: true
      color: Theme.accent
    }
    Txt {
      visible: (flow.owner.stage === "words" || flow.owner.stage === "waiting") && flow.owner.words.length > 0
      width: parent.width
      wrapMode: Text.Wrap
      text: "Accept on the phone only if it shows the same four words."
      color: Theme.brightForeground
    }
    Txt {
      visible: flow.owner.stage === "waiting" && flow.owner.words.length > 0 && flow.owner.message !== ""
      width: parent.width
      wrapMode: Text.Wrap
      font.pixelSize: Theme.small
      color: Theme.darkForeground
      text: flow.owner.message
    }
    Txt {
      visible: flow.owner.stage === "done"
      width: parent.width
      wrapMode: Text.Wrap
      color: Theme.green
      text: "Paired. The phone is in the list on the left."
    }
    Txt {
      visible: flow.owner.stage === "failed" || flow.owner.stage === "cancelled" || flow.owner.askError !== ""
      width: parent.width
      wrapMode: Text.Wrap
      color: flow.owner.askError === "" && flow.owner.stage === "cancelled" ? Theme.darkForeground : Theme.red
      text: flow.owner.askError !== "" ? flow.owner.askError : flow.owner.message
    }
    OutlineButton {
      visible: flow.owner.pairing
      text: "Cancel"
      onClicked: flow.owner.cancel()
    }
  }

  Flickable {
    id: flick
    anchors.fill: parent
    contentWidth: width
    contentHeight: content.height + 2 * Theme.margin
    clip: true
    boundsBehavior: Flickable.StopAtBounds
    interactive: contentHeight > height

    Column {
      id: content
      x: Theme.margin
      y: Theme.margin
      width: Math.min(flick.width - 2 * Theme.margin, 820)
      spacing: Theme.gap

      // No phone yet: the window's first screen.
      Column {
        visible: page.welcome
        width: parent.width
        spacing: 6
        Txt {
          text: "Welcome to Homa Lawn"
          font.pixelSize: Theme.title
          font.bold: true
          color: Theme.brightForeground
        }
        Txt {
          width: parent.width
          wrapMode: Text.Wrap
          color: Theme.foreground
          text: "Homa Lawn runs your Android phone's home screen from this computer. Add your phone to begin."
        }
      }

      Row {
        id: choices
        width: parent.width
        spacing: 12
        readonly property real half: (width - spacing) / 2
        PathChoice {
          owner: page
          width: choices.half
          key: "new"
          title: "New phone"
          text: "Homa Lawn is not on it yet. Scan a QR code to download it, then pair."
        }
        PathChoice {
          owner: page
          width: choices.half
          key: "existing"
          title: "This phone already has Homa Lawn"
          text: "Find it on your network or type its address, then pair."
        }
      }

      // ── New phone ──
      StepCard {
        visible: page.path === "new"
        n: 1
        title: "Download Homa Lawn on the phone"
        subtitle: page.offer ? "Scan the code with the phone's camera, or type the address in its browser."
          : "Show a QR code the phone's camera scans to download Homa Lawn."

        Row {
          visible: !page.offer
          spacing: 12
          AccentButton {
            text: "Show QR code"
            working: page.serveAsking
            onClicked: page.serve()
          }
          Txt {
            anchors.verticalCenter: parent.verticalCenter
            visible: page.serveEnded !== "" && page.serveError === ""
            text: (page.serveEnded === "expired" ? "The last code expired." : "Stopped.")
              + " Show a new one when the phone is ready."
            font.pixelSize: Theme.small
            color: Theme.darkForeground
          }
        }
        StatusLine {
          visible: page.serveError !== ""
          tone: Theme.red
          text: page.serveError
        }

        Row {
          visible: !!page.offer
          width: parent.width
          spacing: 20
          QrCode {
            id: qrCode
            objectName: "qrCode"
            matrix: page.offer ? page.offer.qr : []
            least: 225
          }
          Column {
            width: parent.width - qrCode.width - parent.spacing
            spacing: 10
            Txt {
              text: "Address"
              font.pixelSize: Theme.small
              color: Theme.darkForeground
            }
            Rectangle {
              width: parent.width
              height: urlText.implicitHeight + 14
              radius: 4
              color: Theme.darkerBackground
              border.width: 1
              border.color: Theme.selection
              TextEdit {
                id: urlText
                x: 8
                y: 7
                width: parent.width - 16
                readOnly: true
                selectByMouse: true
                wrapMode: TextEdit.WrapAnywhere
                text: page.offer ? page.offer.url : ""
                color: Theme.brightForeground
                selectionColor: Theme.accent
                font.family: Theme.font
                font.pixelSize: Theme.small
              }
            }
            Txt {
              width: parent.width
              wrapMode: Text.Wrap
              font.pixelSize: Theme.small
              color: Theme.foreground
              text: !page.offer ? "" : page.offer.source === "release"
                ? "The phone downloads Homa Lawn " + page.offer.version + " from GitHub."
                : "Homa Lawn " + page.offer.version + ", built on this computer"
            }
            Txt {
              width: parent.width
              wrapMode: Text.Wrap
              font.pixelSize: Theme.small
              visible: page.timed
              color: page.remaining <= 60 ? Theme.yellow : Theme.foreground
              text: page.remaining > 0 ? "The code works for " + page.clock(page.remaining) + " more." : "The code is expiring…"
            }
            Txt {
              visible: page.serveMessage !== "" && !!page.offer && page.offer.source !== "release"   // else the line above says it
              width: parent.width
              wrapMode: Text.Wrap
              color: Theme.green
              text: page.serveMessage
            }
            OutlineButton {
              text: page.stopping ? "Stopping…" : "Stop"
              active: !page.stopping
              tip: "Stop offering the app now"
              onClicked: page.stopServing()
            }
          }
        }
      }

      StepCard {
        visible: page.path === "new"
        n: 2
        title: "On the phone"
        spacing: 12
        Step {
          n: 1
          text: "Open the download and allow the install. If Android asks, allow installs from your browser; if it warns about an unknown app, choose Install anyway."
        }
        Step { n: 2; text: "Open Homa Lawn and choose it as the home app." }
        Step { n: 3; text: "Allow notifications." }
      }

      StepCard {
        visible: page.path === "new"
        n: 3
        title: "Pair"
        subtitle: "With Homa Lawn open on the phone, find it here or type its address. Both screens show four words; accept on the phone only if they match."
        PairFlow { owner: page }
      }

      Flow {
        visible: page.path === "new"
        width: parent.width
        spacing: 6
        Txt { height: terminalCmd.height; verticalAlignment: Text.AlignVCenter; text: "Phone with Wireless debugging?"; font.pixelSize: Theme.small; color: Theme.darkForeground }
        Command { id: terminalCmd; text: "homalawn setup --new" }
        Txt { height: terminalCmd.height; verticalAlignment: Text.AlignVCenter; text: "in a terminal does all of this without taps."; font.pixelSize: Theme.small; color: Theme.darkForeground }
      }

      // ── This phone already has Homa Lawn ──
      Card {
        visible: page.path === "existing"
        title: "Pair a phone"
        subtitle: "Open Homa Lawn on the phone, then find it here or type its address. Both screens show four words; accept on the phone only if they match."
        spacing: 12
        PairFlow { owner: page }
      }

      Flow {
        visible: page.path === "existing"
        width: parent.width
        spacing: 6
        Txt { height: pairCmd.height; verticalAlignment: Text.AlignVCenter; text: "Or in a terminal:"; font.pixelSize: Theme.small; color: Theme.darkForeground }
        Command { id: pairCmd; text: "homalawn pair" }
      }
    }
  }
}
