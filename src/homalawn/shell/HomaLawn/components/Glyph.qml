import QtQuick
import ".."

// An icon: one Material Design glyph of the Nerd Font, drawn as text in the window's monospace
// font — the way Omarchy's own bar and panels draw theirs. `code` is the glyph's code point.
Text {
  property int code: 0
  property int size: 18

  text: code > 0 ? String.fromCodePoint(code) : ""
  color: Theme.foreground
  font.family: Theme.font
  font.pixelSize: size
  textFormat: Text.PlainText
  horizontalAlignment: Text.AlignHCenter
  verticalAlignment: Text.AlignVCenter
  width: Math.round(size * 1.3)
  height: Math.round(size * 1.3)
}
