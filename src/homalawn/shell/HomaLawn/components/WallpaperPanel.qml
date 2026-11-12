import QtQuick
import ".."

// One wallpaper panel of Wallpaper & style, for one screen (`panel` "home" or "lock"). In order:
// where the picture comes from (Keep current · Theme wallpapers · Gradients), the theme's
// wallpapers with the crop frame, or the six gradients; Darken / lighten; and on the home panel,
// only while it shows a theme wallpaper, the Parallax switch.
//
// The panel holds no settings of its own: it shows what `owner` (the Look page) says and asks
// it to change the draft (setKind, choosePicture, setStyle, setCrop, setLayer, setParallax).
Rectangle {
  id: panelRoot
  property var owner: null
  property string panel: "home"
  property string title: ""
  property bool showParallax: panel === "home"
  property bool framed: false          // a thin box, when two panels stand side by side
  property int cropHeight: 260
  property bool compact: false         // tighter source tabs, for two panels side by side
  // The narrowest this panel can be with its source tabs in one row (compact tabs).
  readonly property real minWidth: measure.implicitWidth + (framed ? 28 : 0)

  readonly property var conf: owner ? owner.screenOf(panel) : ({ kind: "keep", style: null, layer: 0 })
  readonly property string kind: conf.kind || "keep"
  readonly property bool wide: panel === "home" && !!owner && owner.parallaxOn
  readonly property string picture: owner ? owner.pictureFor(panel) : ""

  implicitHeight: body.implicitHeight + (framed ? 28 : 0)
  height: implicitHeight
  radius: 6
  color: "transparent"
  border.width: framed ? 1 : 0
  border.color: Theme.selection

  readonly property var sources: [{ key: "keep", label: "Keep current" },
                                  { key: "wallpaper", label: "Theme wallpapers" },
                                  { key: "gradient", label: "Gradients" }]
  // Never shown: measures the compact source tabs for minWidth.
  Segmented {
    id: measure
    visible: false
    pad: 14
    options: panelRoot.sources
  }

  component Small: Txt {
    width: parent ? parent.width : implicitWidth
    font.pixelSize: Theme.small
    color: Theme.darkForeground
    wrapMode: Text.Wrap
  }

  Column {
    id: body
    x: panelRoot.framed ? 14 : 0
    y: panelRoot.framed ? 14 : 0
    width: panelRoot.width - 2 * x
    spacing: 10

    Txt {
      visible: panelRoot.title !== ""
      text: panelRoot.title
      font.bold: true
      color: Theme.brightForeground
    }

    // 1. Where the picture comes from.
    Segmented {
      id: tabs
      pad: panelRoot.compact ? 14 : 28
      options: panelRoot.sources
      current: panelRoot.kind
      onPicked: key => panelRoot.owner.setKind(panelRoot.panel, key)
    }

    Small {
      visible: panelRoot.kind === "keep"
      text: "This screen keeps the wallpaper it has now."
    }

    // Theme wallpapers: thumbnails, then the crop.
    Flow {
      id: thumbs
      visible: panelRoot.kind === "wallpaper"
      width: parent.width
      spacing: 8
      Repeater {
        model: panelRoot.kind === "wallpaper" && panelRoot.owner ? panelRoot.owner.wallpaperPaths : []
        Rectangle {
          id: thumb
          required property var modelData
          readonly property bool on: !!panelRoot.owner && panelRoot.owner.samePicture(modelData, panelRoot.picture)
          width: 92
          height: 56
          radius: 5
          color: Theme.darkerBackground
          border.width: 2
          border.color: on ? Theme.accent : (thumbHover.containsMouse ? Theme.muted : "transparent")
          Image {
            anchors.fill: parent
            anchors.margins: 2
            source: Paths.url(thumb.modelData)
            sourceSize.width: 184
            fillMode: Image.PreserveAspectCrop
            asynchronous: true
            autoTransform: true
          }
          MouseArea {
            id: thumbHover
            anchors.fill: parent
            hoverEnabled: true
            cursorShape: Qt.PointingHandCursor
            onClicked: if (!thumb.on) panelRoot.owner.choosePicture(panelRoot.panel, thumb.modelData)
          }
        }
      }
    }
    Small {
      visible: panelRoot.kind === "wallpaper" && !!panelRoot.owner && panelRoot.owner.wallpaperPaths.length === 0
      text: panelRoot.owner && panelRoot.owner.basis === "follow" ? "Omarchy reports no current background."
        : "This theme has no wallpapers."
    }
    Small {
      visible: panelRoot.kind === "wallpaper" && !!panelRoot.owner && panelRoot.owner.pickedOnPhone(panelRoot.panel)
      text: "Chosen on the phone."
    }

    Txt {
      visible: panelRoot.kind === "wallpaper" && panelRoot.picture !== ""
      text: "Crop"
      font.pixelSize: Theme.small
      font.bold: true
      color: Theme.foreground
    }
    Small {
      visible: panelRoot.kind === "wallpaper" && panelRoot.picture !== ""
      text: panelRoot.wide ? "Slide the frame: it covers every home screen page; the faint lines are where each page starts."
        : "Slide the upright frame: what is inside goes to the phone."
    }
    CropEditor {
      visible: panelRoot.kind === "wallpaper" && panelRoot.picture !== ""
      width: parent.width
      maxHeight: panelRoot.cropHeight
      source: visible ? panelRoot.picture : ""
      screenW: panelRoot.owner ? panelRoot.owner.screen.w : 1080
      screenH: panelRoot.owner ? panelRoot.owner.screen.h : 2400
      span: panelRoot.wide ? 2 : 1
      pageCount: panelRoot.owner ? panelRoot.owner.pageCount : 4
      offset: panelRoot.owner ? panelRoot.owner.cropFor(panelRoot.panel) : 0.5
      frameLabel: panelRoot.wide ? "home screen pages" : panelRoot.panel === "lock" ? "lock screen"
        : panelRoot.title === "Home screen" ? "home screen" : "phone screen"
      onMoved: offset => panelRoot.owner.setCrop(panelRoot.panel, offset)
    }

    // Gradients: the six, each its real rendered picture.
    Flow {
      id: grads
      visible: panelRoot.kind === "gradient"
      width: parent.width
      spacing: 8
      Repeater {
        model: panelRoot.kind === "gradient" && panelRoot.owner ? panelRoot.owner.gradientsFor(panelRoot.wide) : []
        Item {
          id: grad
          required property var modelData
          readonly property bool on: panelRoot.conf.style === modelData.style
          width: 64
          height: face.height + 4 + name.implicitHeight
          Rectangle {
            id: face
            width: parent.width
            height: Math.round(width * 1.35)
            radius: 5
            color: Theme.darkerBackground
            border.width: 2
            border.color: grad.on ? Theme.accent : (gradHover.containsMouse ? Theme.muted : "transparent")
            Image {
              anchors.fill: parent
              anchors.margins: 2
              source: Paths.url(grad.modelData.path)
              sourceSize.width: 120
              fillMode: Image.PreserveAspectCrop
              asynchronous: true
            }
          }
          Txt {
            id: name
            anchors.top: face.bottom
            anchors.topMargin: 4
            anchors.horizontalCenter: parent.horizontalCenter
            text: grad.modelData.name
            font.pixelSize: Theme.tiny
            font.bold: grad.on
            color: grad.on ? Theme.brightForeground : Theme.foreground
          }
          MouseArea {
            id: gradHover
            anchors.fill: parent
            hoverEnabled: true
            cursorShape: Qt.PointingHandCursor
            onClicked: if (!grad.on) panelRoot.owner.setStyle(panelRoot.panel, grad.modelData.style)
          }
        }
      }
    }
    Small {
      visible: panelRoot.kind === "gradient" && !!panelRoot.owner && panelRoot.owner.gradientsFor(panelRoot.wide).length === 0
      text: panelRoot.owner && panelRoot.owner.gradientsLoading(panelRoot.wide) ? "Making the gradients…"
        : "This theme has no colors to make gradients from."
    }

    // 3. Darken / lighten.
    Txt {
      text: "Darken / lighten"
      font.pixelSize: Theme.small
      font.bold: true
      color: Theme.foreground
      opacity: panelRoot.kind === "keep" ? Theme.disabledOpacity : 1
    }
    LayerSlider {
      width: parent.width
      value: panelRoot.conf.layer || 0
      active: panelRoot.kind !== "keep"
      onMoved: value => panelRoot.owner.setLayer(panelRoot.panel, value)
    }

    // 4. Parallax (the home screen only, and only with a theme wallpaper: a gradient never
    // scrolls and a kept wallpaper is left alone).
    Item {
      visible: panelRoot.showParallax && panelRoot.kind === "wallpaper"
      width: parent.width
      height: Math.max(parallaxText.implicitHeight, parallaxSwitch.implicitHeight) + 4
      Txt {
        id: parallaxText
        anchors.left: parent.left
        anchors.right: parallaxSwitch.left
        anchors.rightMargin: 12
        anchors.verticalCenter: parent.verticalCenter
        text: "Parallax — the wallpaper moves as you swipe between pages"
        wrapMode: Text.Wrap
        font.pixelSize: Theme.small
        color: Theme.brightForeground
      }
      Toggle {
        id: parallaxSwitch
        anchors.right: parent.right
        anchors.verticalCenter: parent.verticalCenter
        checked: !!panelRoot.owner && panelRoot.owner.parallax
        onToggled: checked => panelRoot.owner.setParallax(checked)
      }
    }
  }
}
