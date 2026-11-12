pragma Singleton
import QtQuick

// Small helpers for the file paths and theme names the Homa Lawn service sends.
QtObject {
  // A local file as a URL an Image can load; each path part is escaped, so a space or a '#'
  // in a file name stays part of the name.
  function url(path) {
    if (!path) return ""
    return "file://" + String(path).split("/").map(encodeURIComponent).join("/")
  }

  // The file name at the end of a path.
  function base(path) {
    var s = String(path || "")
    return s.slice(s.lastIndexOf("/") + 1)
  }

  // Omarchy's folder name for a theme: "Tokyo Night" -> "tokyo-night" (theme.name uses it).
  function slug(name) {
    return String(name || "").trim().toLowerCase().split(/\s+/).filter(p => p !== "").join("-")
  }
}
