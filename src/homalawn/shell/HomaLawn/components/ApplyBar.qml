import QtQuick
import ".."

// The window's one Apply pattern, at the bottom of every editing page: on the left what the draft
// holds ("No changes", "2 changes — not applied yet", "Applying…", "Applied"), on the right
// Discard and Apply. Nothing reaches the phone until Apply.
Rectangle {
  id: bar
  property int changes: 0           // how many things differ from the phone
  property bool applying: false
  property bool canApply: true      // Apply may run now (the phone connected, nothing else busy)
  property bool failed: false       // the last Apply went wrong (the page says how): no "Applied"
  signal apply()
  signal discard()

  // "Applied" for a few seconds once an Apply has left no changes behind (the page may read the
  // phone back first, so the count can reach 0 a moment after Apply returns).
  property bool justApplied: false
  property bool watching: false
  function applied() {
    watching = false
    if (failed) return
    justApplied = true
    appliedClock.restart()
  }
  onFailedChanged: if (failed) justApplied = false
  onApplyingChanged: {
    justApplied = false
    if (applying) return
    if (changes === 0) applied()
    else { watching = true; watchClock.restart() }
  }
  onChangesChanged: {
    if (changes > 0) justApplied = false
    else if (watching) applied()
  }
  Timer { id: appliedClock; interval: 4000; onTriggered: bar.justApplied = false }
  Timer { id: watchClock; interval: 5000; onTriggered: bar.watching = false }

  anchors.left: parent ? parent.left : undefined
  anchors.right: parent ? parent.right : undefined
  anchors.bottom: parent ? parent.bottom : undefined
  height: 52
  color: Theme.darkBackground
  Rectangle { width: parent.width; height: 1; color: Theme.selection }

  Row {
    anchors.left: parent.left
    anchors.leftMargin: Theme.margin
    anchors.right: buttons.left
    anchors.rightMargin: 12
    anchors.verticalCenter: parent.verticalCenter
    spacing: 8
    Rectangle {
      visible: bar.changes > 0 && !bar.applying
      anchors.verticalCenter: parent.verticalCenter
      width: 8; height: 8; radius: 4
      color: Theme.accent
    }
    Txt {
      anchors.verticalCenter: parent.verticalCenter
      width: parent.width - 16
      elide: Text.ElideRight
      text: bar.applying ? "Applying…"
        : bar.changes === 0 ? (bar.justApplied ? "Applied" : "No changes")
        : (bar.changes === 1 ? "1 change" : bar.changes + " changes")
          + " — not applied yet"
      color: bar.justApplied && bar.changes === 0 ? Theme.green
        : bar.changes > 0 || bar.applying ? Theme.brightForeground : Theme.darkForeground
      font.pixelSize: Theme.small
      font.bold: bar.changes > 0 || bar.justApplied
    }
  }
  Row {
    id: buttons
    anchors.right: parent.right
    anchors.rightMargin: Theme.margin
    anchors.verticalCenter: parent.verticalCenter
    spacing: 10
    OutlineButton {
      text: "Discard"
      active: bar.changes > 0 && !bar.applying
      onClicked: { bar.watching = false; bar.discard() }
    }
    AccentButton {
      text: "Apply"
      active: bar.changes > 0 && bar.canApply
      working: bar.applying
      onClicked: bar.apply()
    }
  }
}
