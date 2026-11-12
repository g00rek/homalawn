import QtQuick
import ".."

// Plain text in the window's font and colour. Plain on purpose: a phone or app name can never
// turn into markup.
Text {
  color: Theme.foreground
  font.family: Theme.font
  font.pixelSize: Theme.size
  textFormat: Text.PlainText
}
