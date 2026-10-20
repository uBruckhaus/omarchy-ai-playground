import QtQuick
import Quickshell
import qs.Commons
import qs.Ui

// The bar icon: starts AI Playground, or brings its window to the front (it is a normal app
// window; the app itself keeps one instance).
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
      Quickshell.execDetached([root.launcherPath])
    }
  }
}
