import QtQuick
import Quickshell
import qs.Commons
import qs.Ui

// The bar icon toggles AI Playground: closes it when it is the window in front, else starts it or
// brings it to the front (a normal app window; the app itself keeps one instance).
BarWidget {
  id: root

  moduleName: "ubruckhaus.ai-playground"

  readonly property string launcherPath: Qt.resolvedUrl("ai-playground").toString().replace(/^file:\/\//, "")

  implicitWidth: button.implicitWidth
  implicitHeight: button.implicitHeight

  BarIconButton {
    id: button
    anchors.fill: parent
    bar: root.bar
    text: "\u{F0297}"
    tooltipText: "AI Playground"

    onPressed: function(mouseButton) {
      if (!root.bar) return
      Quickshell.execDetached([root.launcherPath, "--toggle"])
    }
  }
}
