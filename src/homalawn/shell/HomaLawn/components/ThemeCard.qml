import QtQuick
import ".."

// One Omarchy theme in the "A chosen theme" grid: its preview picture when it has one, a strip
// of three swatches (background, accent, a third colour) and its name. A theme without a
// palette (older format) gets a neutral strip and a "wallpaper only" note: only its wallpaper
// can go to the phone.
Rectangle {
  id: card
  property var theme: null          // an entry of themes.list
  property bool selected: false
  signal clicked()

  readonly property var colors: theme && theme.colors ? theme.colors : ({})
  readonly property bool hasColors: !!colors.background || !!colors.accent
  readonly property var swatches: hasColors
    ? [colors.background || Theme.darkBackground,
       colors.accent || colors.blue || colors.foreground || Theme.muted,
       colors.magenta || colors.bright_magenta || colors.foreground || Theme.muted]
    : [Theme.selection, Theme.muted, Theme.darkForeground]

  implicitHeight: 70
  radius: 5
  color: Theme.lighterBackground
  border.width: selected ? 2 : 1
  border.color: selected ? Theme.accent : (hover.containsMouse ? Theme.foreground : Theme.muted)
  clip: true

  Item {
    id: picture
    anchors.left: parent.left
    anchors.right: parent.right
    anchors.top: parent.top
    anchors.margins: card.border.width
    height: 46

    Image {
      id: preview
      anchors.fill: parent
      visible: status === Image.Ready
      source: card.theme && card.theme.preview ? Paths.url(card.theme.preview) : ""
      sourceSize.width: 240
      fillMode: Image.PreserveAspectCrop
      asynchronous: true
      autoTransform: true
      cache: true
    }

    // Swatches: the whole picture area without a preview, a strip along its bottom with one.
    Row {
      id: strip
      anchors.left: parent.left
      anchors.right: parent.right
      anchors.bottom: parent.bottom
      // With a preview: a thin strip, a taller one when it carries the "wallpaper only" note.
      height: preview.visible ? (card.hasColors ? 9 : 15) : parent.height
      Repeater {
        model: card.swatches
        Rectangle {
          required property var modelData
          width: parent.width / 3
          height: parent.height
          color: modelData
        }
      }
    }

    // In the swatch strip, never over the preview picture.
    Txt {
      visible: !card.hasColors
      anchors.centerIn: strip
      text: "wallpaper only"
      font.pixelSize: preview.visible ? 9 : 10
      color: Theme.brightForeground
    }
  }

  Txt {
    anchors.left: parent.left
    anchors.right: parent.right
    anchors.top: picture.bottom
    anchors.bottom: parent.bottom
    anchors.leftMargin: 7
    anchors.rightMargin: 7
    verticalAlignment: Text.AlignVCenter
    text: card.theme ? card.theme.name : ""
    elide: Text.ElideRight
    font.pixelSize: Theme.tiny
    color: card.selected ? Theme.brightForeground : Theme.foreground
  }

  MouseArea {
    id: hover
    anchors.fill: parent
    hoverEnabled: true
    cursorShape: Qt.PointingHandCursor
    onClicked: card.clicked()
  }
}
