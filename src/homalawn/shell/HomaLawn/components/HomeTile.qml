import QtQuick
import ".."
import "../pages/HomeDraft.js" as D

// One home-screen item as the phone draws it: an app (its icon and label), a folder (a tile of
// its first four icons), a widget (a block with its preview) or a shortcut. The parent sizes it
// to the cells it covers. `m` holds the phone's metrics in pixels: {icon, label, gap, labels, top?}.
Item {
  id: tile
  property var item: null
  property var icons: ({})           // component -> picture file
  property var widgets: ({})         // provider -> {label, preview}
  property var m: ({ icon: 32, label: 9, gap: 3, labels: true })
  property bool mini: false          // a screen thumbnail: no labels, plainer
  property string problem: ""        // what the phone said is wrong with it (red frame)
  property bool lifted: false        // being dragged: a faint outline stays behind
  // {plate, color}: the phone's monochrome icons (a preview of the setting): the icon's shape in
  // `color` on a round plate of `plate`. null: the icons as they are.
  property var mono: null

  readonly property string type: item ? item.type : ""
  readonly property bool showLabel: m.labels && !mini && type !== "widget"
  readonly property string label: !item ? "" : (item.title || (type === "widget" ? widgetInfo.label || "" : item.package || ""))
  readonly property var widgetInfo: type === "widget" ? (widgets[D.provider(item.provider)] || {}) : ({})

  function iconOf(it) { return it && it.type === "app" ? (icons[D.component(it)] || "") : "" }

  // The icon and its label as one block: `m.top` from the cell's top where the phone says where
  // it puts the icon, else centred in the cell.
  Item {
    id: block
    visible: tile.type !== "widget"
    opacity: tile.lifted ? 0.25 : 1
    width: tile.width
    height: tile.m.icon + (tile.showLabel ? Math.max(0, tile.m.gap) + caption.height : 0)
    x: 0
    y: tile.m.top !== undefined && !tile.mini ? tile.m.top : (tile.height - height) / 2

    Item {
      id: face
      anchors.horizontalCenter: parent.horizontalCenter
      width: tile.m.icon
      height: tile.m.icon

      // Monochrome icons: a plate, and the icon's shape in one colour on it.
      Rectangle {
        anchors.fill: parent
        visible: !!tile.mono && tile.type === "app"
        radius: width / 2
        color: tile.mono ? tile.mono.plate : "transparent"
        Canvas {
          id: shape
          anchors.centerIn: parent
          width: parent.width * 0.62
          height: width
          readonly property string src: tile.mono && tile.type === "app" ? Paths.url(tile.iconOf(tile.item)) : ""
          readonly property color fill: tile.mono ? tile.mono.color : "transparent"
          // The icon's symbol in one colour, as Lawnchair themes an icon without a monochrome
          // layer of its own: an icon on a transparent ground keeps its whole shape; a full-bleed
          // icon (an opaque plate behind the symbol) keeps what differs from its plate's colour.
          onPaint: {
            var ctx = getContext("2d")
            ctx.reset()
            if (!src || !isImageLoaded(src)) return
            var w = Math.max(1, Math.round(width)), h = Math.max(1, Math.round(height))
            ctx.drawImage(src, 0, 0, w, h)
            var img = ctx.getImageData(0, 0, w, h)
            var px = img.data, n = w * h
            var counts = {}, opaque = 0, best = "", bestN = 0
            for (var i = 0; i < n; i++) {
              if (px[i * 4 + 3] < 200) continue
              opaque++
              var key = (px[i * 4] >> 4) + "," + (px[i * 4 + 1] >> 4) + "," + (px[i * 4 + 2] >> 4)
              counts[key] = (counts[key] || 0) + 1
              if (counts[key] > bestN) { bestN = counts[key]; best = key }
            }
            var plate = opaque > n * 0.6 && best ? best.split(",").map(v => Number(v) * 16 + 8) : null
            // Drawn as one square per pixel (this Canvas does not take putImageData back).
            ctx.clearRect(0, 0, w, h)
            var c = fill
            for (var j = 0; j < n; j++) {
              var a = px[j * 4 + 3]
              if (plate) {
                var d = Math.abs(px[j * 4] - plate[0]) + Math.abs(px[j * 4 + 1] - plate[1]) + Math.abs(px[j * 4 + 2] - plate[2])
                a = a * Math.min(1, d / 120)
              }
              if (a < 8) continue
              ctx.fillStyle = Qt.rgba(c.r, c.g, c.b, a / 255)
              ctx.fillRect(j % w, Math.floor(j / w), 1, 1)
            }
          }
          onImageLoaded: requestPaint()
          onFillChanged: requestPaint()
          onWidthChanged: requestPaint()
          onSrcChanged: if (src) { if (isImageLoaded(src)) requestPaint(); else loadImage(src) }
          Component.onCompleted: if (src) { if (isImageLoaded(src)) requestPaint(); else loadImage(src) }
        }
        // No picture: its first letter.
        Text {
          anchors.centerIn: parent
          visible: !tile.iconOf(tile.item)
          text: tile.label ? tile.label.charAt(0).toUpperCase() : "?"
          color: tile.mono ? tile.mono.color : "#ffffff"
          font.pixelSize: Math.max(5, parent.height * 0.45)
          font.bold: true
        }
      }
      // App: its picture; without one, its first letter on a plain tile.
      Image {
        anchors.fill: parent
        visible: !tile.mono && tile.type === "app" && status === Image.Ready
        source: Paths.url(tile.iconOf(tile.item))
        sourceSize: Qt.size(128, 128)
        smooth: true
        mipmap: true
        asynchronous: true
      }
      Rectangle {
        anchors.fill: parent
        visible: (!tile.mono && tile.type === "app" && !tile.iconOf(tile.item)) || tile.type === "shortcut"
        radius: width / 2
        color: Theme.withAlpha("#ffffff", 0.25)
        Text {
          anchors.centerIn: parent
          text: tile.label ? tile.label.charAt(0).toUpperCase() : "?"
          color: "#ffffff"
          font.pixelSize: Math.max(7, parent.height * 0.45)
          font.bold: true
        }
      }
      // Folder: a round tile with its first four icons.
      Rectangle {
        anchors.fill: parent
        visible: tile.type === "folder"
        radius: width / 2
        color: Theme.withAlpha("#ffffff", 0.3)
        Grid {
          anchors.centerIn: parent
          columns: 2
          spacing: parent.width * 0.06
          Repeater {
            model: tile.type === "folder" ? (tile.item.items || []).slice(0, 4) : []
            Image {
              required property var modelData
              width: face.width * 0.34
              height: width
              source: Paths.url(tile.iconOf(modelData))
              sourceSize: Qt.size(64, 64)
              smooth: true
              asynchronous: true
            }
          }
        }
      }
    }

    Text {
      id: caption
      visible: tile.showLabel
      anchors.top: face.bottom
      // With the phone's `cap` (where its label's capitals start below the label line's top,
      // patch 0025) the caption's own capitals are put there: this font's first baseline is its
      // ascent below the top, its capitals rise `capRise` above it.
      anchors.topMargin: tile.m.cap !== undefined ? tile.m.gap + tile.m.cap - (capMetrics.ascent - capMetrics.capRise) : tile.m.gap
      anchors.horizontalCenter: parent.horizontalCenter
      width: tile.width - 2
      horizontalAlignment: Text.AlignHCenter
      text: tile.label
      elide: Text.ElideRight
      textFormat: Text.PlainText
      color: "#ffffff"
      font.pixelSize: Math.max(6, tile.m.label)
      style: Text.Raised
      styleColor: Theme.withAlpha("#000000", 0.55)
    }
    FontMetrics {
      id: capMetrics
      font: caption.font
      readonly property real capRise: { var f = font; return -tightBoundingRect("H").y }
    }
  }

  // Widget: a rounded block with its preview picture and name.
  Rectangle {
    visible: tile.type === "widget"
    anchors.fill: parent
    anchors.margins: tile.mini ? 1 : Math.max(2, Math.min(tile.width, tile.height) * 0.04)
    radius: tile.mini ? 2 : 10
    color: Theme.withAlpha(Theme.darkerBackground, tile.mini ? 0.55 : 0.45)
    border.width: tile.mini ? 0 : 1
    border.color: Theme.withAlpha("#ffffff", 0.18)
    opacity: tile.lifted ? 0.25 : 1
    clip: true
    Image {
      id: preview
      anchors.fill: parent
      anchors.margins: tile.mini ? 1 : 6
      anchors.bottomMargin: tile.mini ? 1 : widgetName.height + 8
      source: Paths.url(tile.widgetInfo.preview || "")
      sourceSize: Qt.size(256, 256)
      fillMode: Image.PreserveAspectFit
      asynchronous: true
      smooth: true
    }
    Text {
      id: widgetName
      visible: !tile.mini
      anchors.bottom: parent.bottom
      anchors.bottomMargin: 4
      anchors.horizontalCenter: parent.horizontalCenter
      width: parent.width - 8
      horizontalAlignment: Text.AlignHCenter
      elide: Text.ElideRight
      textFormat: Text.PlainText
      text: tile.label + (tile.item && tile.item.state && tile.item.state !== "ready" ? " · " + tile.item.state : "")
      color: "#ffffff"
      font.pixelSize: Math.max(7, tile.m.label)
    }
  }

  // The phone said no to this one.
  Rectangle {
    visible: tile.problem !== ""
    anchors.fill: parent
    anchors.margins: 1
    radius: 8
    color: Theme.withAlpha(Theme.red, 0.18)
    border.width: 2
    border.color: Theme.red
  }
}
