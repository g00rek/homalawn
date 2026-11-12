import QtQuick
import ".."

// A small phone showing what one screen will look like: the wallpaper cut as the service will
// cut it (cover, framed by `offset`; a gradient is its own rendered picture) and the colour layer
// over it (black for darker, white for lighter, at |amount| percent). Drawn here, so it follows a
// drag at once with no round trip. A view, not a control.
//
// `kept`: the look leaves this screen's wallpaper alone, so the phone shows a neutral "Current
// wallpaper" fill (we cannot see the phone's picture) with `keptNote` under it.
// Parallax (`span` 2): the screen shows page 0's slice (the left screen-width of the wide
// picture) and a strip under the phone shows the whole wide picture with a faint mark where each
// of `pageCount` pages starts, page 0's slice outlined and the part no page reaches dimmed.
// The home screen shows the phone's own first page and dock when `layout` holds them (home.get's
// answer: screens, hotseat, grid, icons, geometry; the service reads it from a connected phone),
// with `mono` as the phone's monochrome icons (the icon's shape in `iconColor` on a plate of
// `iconPlate`). Without a layout it shows the wallpaper alone: no made-up apps.
Item {
  id: phone
  property string source: ""
  property real screenW: 1080
  property real screenH: 2400
  property real offset: 0.5
  property int amount: 0     // colour layer, -100 (white) .. 100 (black)
  property bool lock: false          // lock screen: a clock instead of icons and a dock
  property bool kept: false
  property string keptNote: "Kept as it is"
  property real span: 1
  property int pageCount: 4
  property color accent: Theme.accent
  property color dockColor: Theme.withAlpha(Theme.background, 0.6)
  property bool mono: false
  property color iconColor: Theme.accent
  property color iconPlate: Theme.lighterBackground
  // The phone's home screen: {screen (its first page: {index, items}), grid: [cols, rows],
  // top_row_reserved, hotseat, hotseat_count, icons, geometry}; null: not known.
  property var layout: null
  readonly property var geo: layout && layout.geometry && layout.geometry.screen && layout.geometry.workspace
    && layout.geometry.hotseat && layout.geometry.icon ? layout.geometry : null
  readonly property var monoLook: mono ? { plate: iconPlate, color: iconColor } : null
  property string caption: ""
  // Leave the strip's room empty (the phone beside a wide one), so both captions line up.
  property bool stripRoom: false

  readonly property real bezel: 5
  readonly property real innerW: width - bezel * 2
  readonly property real innerH: innerW * screenH / Math.max(1, screenW)
  readonly property bool wide: span > 1 && !kept
  // Where page i's slice starts, as a part of the wide picture's width (Lawnchair's rule:
  // i / (max(N, 4) - 1) of the spare width, one screen wide).
  readonly property int spanPages: Math.max(pageCount, 4)
  function pageStart(i) { return i / (spanPages - 1) * (span - 1) / span }
  readonly property real reached: Math.min(1, pageStart(Math.max(1, pageCount) - 1) + 1 / span)

  implicitWidth: 140
  implicitHeight: body.height + (wide || stripRoom ? strip.height + 6 : 0) + captionText.implicitHeight + 6

  Rectangle {
    id: body
    width: phone.width
    height: phone.innerH + phone.bezel * 2
    radius: 18
    color: "#000000"
    border.width: 1
    border.color: Theme.muted

    Item {
      id: screen
      x: phone.bezel
      y: phone.bezel
      width: phone.innerW
      height: phone.innerH
      clip: true

      Wallpaper {
        anchors.fill: parent
        visible: !phone.kept
        source: phone.source
        offset: phone.offset
        amount: phone.amount
        span: phone.wide ? phone.span : 1
        pan: 0
      }

      // Kept: the phone's own wallpaper, which the window cannot show.
      Rectangle {
        anchors.fill: parent
        visible: phone.kept
        color: Theme.lighterBackground
        Column {
          anchors.centerIn: parent
          width: parent.width - 12
          spacing: 3
          Txt {
            width: parent.width
            horizontalAlignment: Text.AlignHCenter
            wrapMode: Text.Wrap
            text: "Current wallpaper"
            font.pixelSize: Theme.tiny
            color: Theme.foreground
          }
          Txt {
            visible: phone.keptNote !== ""
            width: parent.width
            horizontalAlignment: Text.AlignHCenter
            wrapMode: Text.Wrap
            text: phone.keptNote
            font.pixelSize: 9
            color: Theme.darkForeground
          }
        }
      }

      // Lock screen: the clock.
      Text {
        visible: phone.lock
        anchors.horizontalCenter: parent.horizontalCenter
        y: parent.height * 0.17
        text: "12:48"
        // white on a picture; on the neutral "kept" fill the theme's text colour, so it reads in light themes
        color: phone.kept ? Theme.brightForeground : "#ffffff"
        font.family: Theme.font
        font.pixelSize: Math.round(parent.width * 0.24)
        style: phone.kept ? Text.Normal : Text.Raised
        styleColor: Theme.withAlpha("#000000", 0.6)
      }

      // Home screen: the phone's own first page and dock, scaled from where the phone draws them.
      Item {
        id: real
        visible: !phone.lock && !!phone.geo
        anchors.fill: parent
        readonly property real k: phone.geo ? width / phone.geo.screen.w : 0
        readonly property real iconScale: phone.layout && phone.layout.icon_scale ? phone.layout.icon_scale : 1

        HomeScreen {
          x: phone.geo ? phone.geo.workspace.x * real.k : 0
          y: phone.geo ? phone.geo.workspace.y * real.k : 0
          width: phone.geo ? phone.geo.workspace.w * real.k : 0
          height: phone.geo ? phone.geo.workspace.h * real.k : 0
          screen: phone.layout ? phone.layout.screen : null
          cols: phone.layout && phone.layout.grid ? phone.layout.grid[0] : 4
          rows: phone.layout && phone.layout.grid ? phone.layout.grid[1] : 5
          reservedTop: !!phone.layout && phone.layout.top_row_reserved !== false && !!phone.layout.screen && phone.layout.screen.id === 0
          mini: true
          mono: phone.monoLook
          m: ({ icon: phone.geo ? Math.max(4, phone.geo.icon.size * real.k) : 0, label: 0, gap: 0, labels: false })
          icons: phone.layout && phone.layout.icons ? phone.layout.icons : ({})
        }
        Item {
          id: dock
          x: phone.geo ? phone.geo.hotseat.x * real.k : 0
          y: phone.geo ? phone.geo.hotseat.y * real.k : 0
          width: phone.geo ? phone.geo.hotseat.w * real.k : 0
          height: phone.geo ? phone.geo.hotseat.h * real.k : 0
          readonly property real slot: width / Math.max(1, phone.layout && phone.layout.hotseat_count ? phone.layout.hotseat_count : 4)
          Rectangle {
            anchors.fill: parent
            anchors.margins: 2
            radius: height / 2.6
            color: phone.dockColor
          }
          Repeater {
            model: phone.layout && phone.layout.hotseat ? phone.layout.hotseat : []
            HomeTile {
              required property var modelData
              x: (modelData.x || 0) * dock.slot
              width: dock.slot
              height: dock.height
              item: modelData
              mini: true
              mono: phone.monoLook
              icons: phone.layout && phone.layout.icons ? phone.layout.icons : ({})
              m: ({ icon: phone.geo ? Math.max(4, Math.min(phone.geo.hotseat.icon_size * real.k, dock.slot, dock.height)) : 0,
                    label: 0, gap: 0, labels: false })
            }
          }
        }
      }
    }
  }

  // Parallax: the whole wide picture as a thin band, page marks, page 0's slice outlined.
  Item {
    id: strip
    visible: phone.wide
    anchors.top: body.bottom
    anchors.topMargin: 6
    width: phone.width
    height: 22
    clip: true

    Wallpaper {
      // the wide picture at its own shape, centred on the band (the band shows its middle)
      width: strip.width
      height: strip.width * phone.screenH / Math.max(1, phone.screenW * phone.span)
      y: (strip.height - height) / 2
      source: phone.source
      offset: phone.offset
      amount: phone.amount
    }
    // No page reaches this part.
    Rectangle {
      x: strip.width * phone.reached
      width: strip.width - x
      height: strip.height
      color: "#000000"
      opacity: 0.6
    }
    Repeater {
      model: phone.wide ? Math.max(1, phone.pageCount) : 0
      Rectangle {
        required property int index
        visible: index > 0
        x: Math.round(strip.width * phone.pageStart(index))
        width: 1
        height: strip.height
        color: Qt.rgba(1, 1, 1, 0.45)     // on the picture, so white whatever the theme
      }
    }
    Rectangle {
      width: strip.width / phone.span
      height: strip.height
      color: "transparent"
      border.width: 2
      border.color: phone.accent
    }
    Rectangle {
      anchors.fill: parent
      color: "transparent"
      border.width: 1
      border.color: Theme.muted
    }
  }

  Txt {
    id: captionText
    anchors.top: phone.wide || phone.stripRoom ? strip.bottom : body.bottom
    anchors.topMargin: 6
    anchors.horizontalCenter: parent.horizontalCenter
    text: phone.caption
    font.pixelSize: Theme.small
    color: Theme.darkForeground
  }
}
