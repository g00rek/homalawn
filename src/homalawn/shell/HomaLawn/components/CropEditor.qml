import QtQuick
import ".."

// The wallpaper with an upright frame in the phone's proportions. What is inside the frame is
// what the phone gets. The frame slides along the side with spare room (sideways for a
// landscape picture) and reports its place as `offset`: 0 = flush with the left/top edge,
// 0.5 = centred, 1 = flush with the right/bottom edge — the same numbers the service cuts with.
//
// `other` (optional, -1 = none) draws a second, thin frame (another screen's crop).
// Parallax: with `span` 2 the frame is that many screens wide (the home screen's wide picture)
// and faint lines inside it mark where each of `pageCount` pages starts.
Item {
  id: editor
  property string source: ""          // file path of the wallpaper
  property real screenW: 1080
  property real screenH: 2400
  property real offset: 0.5
  property real other: -1
  property string frameLabel: "phone screen"
  property real span: 1
  property int pageCount: 4
  property int maxHeight: 320
  signal moved(real offset)

  readonly property real srcW: picture.implicitWidth
  readonly property real srcH: picture.implicitHeight
  readonly property bool ready: picture.status === Image.Ready && srcW > 0 && srcH > 0

  // The picture shown as large as fits in our width and maxHeight, kept in proportion.
  readonly property real shownScale: ready ? Math.min(width / srcW, maxHeight / srcH) : 1
  readonly property real shownW: ready ? srcW * shownScale : width
  readonly property real shownH: ready ? srcH * shownScale : Math.min(maxHeight, width * 9 / 16)

  // The frame inside the shown picture (the cover box of wallcrop.cover_box, scaled).
  readonly property real cutW: screenW * Math.max(1, span)
  readonly property real coverScale: Math.max(cutW / shownW, screenH / shownH)
  readonly property real frameW: cutW / coverScale
  readonly property real frameH: screenH / coverScale
  readonly property bool sideways: shownW - frameW >= shownH - frameH
  readonly property real slack: sideways ? shownW - frameW : shownH - frameH

  function clamp(v) { return Math.max(0, Math.min(1, v)) }
  function frameX(o) { return sideways ? slack * clamp(o) : 0 }
  function frameY(o) { return sideways ? 0 : slack * clamp(o) }

  implicitHeight: shownH

  Item {
    id: stage
    width: editor.shownW
    height: editor.shownH
    // flush left, in line with the thumbnails and texts above it

    Rectangle {
      anchors.fill: parent
      radius: 6
      color: Theme.darkerBackground
      visible: !editor.ready
      Txt {
        anchors.centerIn: parent
        text: editor.source === "" ? "No wallpaper" : (picture.status === Image.Error ? "Cannot read this wallpaper" : "Loading…")
        color: Theme.darkForeground
        font.pixelSize: Theme.small
      }
    }

    Image {
      id: picture
      anchors.fill: parent
      source: Paths.url(editor.source)
      // Large enough to judge a crop, small enough to stay light; the previews ask for the
      // same size so the picture is read once.
      sourceSize.width: 1280
      asynchronous: true
      autoTransform: true
      fillMode: Image.Stretch
      visible: editor.ready
    }

    // The part outside the frame, dimmed.
    Rectangle {
      visible: editor.ready
      color: "#000000"; opacity: 0.55
      x: 0; y: 0
      width: editor.sideways ? frame.x : parent.width
      height: editor.sideways ? parent.height : frame.y
    }
    Rectangle {
      visible: editor.ready
      color: "#000000"; opacity: 0.55
      x: editor.sideways ? frame.x + frame.width : 0
      y: editor.sideways ? 0 : frame.y + frame.height
      width: parent.width - x
      height: parent.height - y
    }

    // The other screen's frame, when home and lock are cropped separately.
    Rectangle {
      visible: editor.ready && editor.other >= 0
      x: editor.frameX(editor.other)
      y: editor.frameY(editor.other)
      width: editor.frameW
      height: editor.frameH
      color: "transparent"
      border.width: 1
      border.color: Qt.rgba(1, 1, 1, 0.7)    // on the picture, so white whatever the theme
    }

    Rectangle {
      id: frame
      visible: editor.ready
      x: editor.frameX(editor.offset)
      y: editor.frameY(editor.offset)
      width: editor.frameW
      height: editor.frameH
      color: "transparent"
      border.width: 2
      border.color: Theme.accent

      // Parallax: where each page's slice starts (i / (max(N, 4) - 1) of the spare width).
      Repeater {
        model: editor.span > 1 ? Math.max(1, editor.pageCount) - 1 : 0
        Rectangle {
          required property int index
          x: Math.round(frame.width * (index + 1) / (Math.max(editor.pageCount, 4) - 1) * (editor.span - 1) / editor.span)
          width: 1
          height: frame.height
          color: Qt.rgba(1, 1, 1, 0.45)    // on the picture, so white whatever the theme
        }
      }

      Txt {
        anchors.horizontalCenter: parent.horizontalCenter
        y: 6
        visible: implicitWidth + 8 <= frame.width    // never wider than the frame it names
        text: editor.frameLabel
        font.pixelSize: Theme.tiny
        color: "#ffffff"
        style: Text.Outline
        styleColor: "#000000"
      }

      // Grips on the two sides it moves along.
      Repeater {
        model: 2
        Rectangle {
          required property int index
          width: editor.sideways ? 10 : 28
          height: editor.sideways ? 28 : 10
          radius: 4
          color: Theme.accent
          x: editor.sideways ? (index === 0 ? -width / 2 : frame.width - width / 2) : (frame.width - width) / 2
          y: editor.sideways ? (frame.height - height) / 2 : (index === 0 ? -height / 2 : frame.height - height / 2)
        }
      }
    }

    // Press inside the frame and drag; press outside it to move the frame's centre there.
    MouseArea {
      anchors.fill: parent
      enabled: editor.ready && editor.slack > 0.5
      preventStealing: true
      cursorShape: editor.sideways ? Qt.SizeHorCursor : Qt.SizeVerCursor
      property real grab: 0
      function along(m) { return editor.sideways ? m.x : m.y }
      function report(o) {
        o = editor.clamp(o)
        if (Math.abs(o - editor.offset) > 0.0005) editor.moved(Math.round(o * 1000) / 1000)
      }
      onPressed: mouse => {
        var start = editor.sideways ? frame.x : frame.y
        var size = editor.sideways ? frame.width : frame.height
        var at = along(mouse)
        if (at < start || at > start + size) {
          grab = size / 2
          report((at - grab) / editor.slack)
        } else {
          grab = at - start
        }
      }
      onPositionChanged: mouse => { if (pressed) report((along(mouse) - grab) / editor.slack) }
    }
  }
}
