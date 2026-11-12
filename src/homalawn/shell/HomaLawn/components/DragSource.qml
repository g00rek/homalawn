import QtQuick

// Press, move a few pixels and this starts a drag on `editor` (the Home screen page): it calls
// editor.dragStart(src, x, y), dragMove(x, y) and dragEnd() with points in the editor's own
// coordinates. A press without moving is a click: tapped().
MouseArea {
  id: handle
  property var editor: null
  property var src: null
  property bool dragging: false
  property point start: Qt.point(0, 0)
  signal tapped()

  hoverEnabled: true
  preventStealing: true
  cursorShape: dragging ? Qt.ClosedHandCursor : Qt.PointingHandCursor

  function here(mouse) { return handle.mapToItem(editor, mouse.x, mouse.y) }

  onPressed: mouse => { start = Qt.point(mouse.x, mouse.y); dragging = false }
  onPositionChanged: mouse => {
    if (!pressed || !editor || !src) return
    var p = here(mouse)
    if (!dragging) {
      if (Math.abs(mouse.x - start.x) + Math.abs(mouse.y - start.y) < 6) return
      dragging = true
      editor.dragStart(src, p.x, p.y)
    }
    editor.dragMove(p.x, p.y)
  }
  onReleased: mouse => {
    if (dragging) {
      dragging = false
      editor.dragEnd()
    } else {
      tapped()
    }
  }
  onCanceled: if (dragging) { dragging = false; editor.dragCancel() }
  // A source that goes away mid-drag (its screen or folder redrawn) still ends the drag.
  Component.onDestruction: if (dragging && editor) editor.dragCancel()
}
