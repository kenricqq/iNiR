#!/usr/bin/env python3
"""Exercise the actual service QML with deterministic process/desktop doubles.

No real sharing process, notifications, shell reloads or user configuration.
Qt's event loop, bindings, timers and signal delivery remain real.
"""
import os
from pathlib import Path
import shutil
import subprocess
import tempfile

ROOT = Path(__file__).resolve().parents[3]
HERE = Path(__file__).resolve().parent


def main():
    runner = os.environ.get("QMLTESTRUNNER") or shutil.which("qmltestrunner6")
    if not runner and Path("/usr/lib/qt6/bin/qmltestrunner").exists():
        runner = "/usr/lib/qt6/bin/qmltestrunner"
    runner = runner or shutil.which("qmltestrunner")
    if not runner:
        raise SystemExit("Qt 6 qmltestrunner is required for service tests")
    with tempfile.TemporaryDirectory(prefix="inir-localsend-qml-") as temp:
        directory = Path(temp)

        def write(name, content):
            path = directory / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content)

        write("qs/qmldir", "module qs\nsingleton GlobalStates 1.0 GlobalStates.qml\n")
        write("qs/GlobalStates.qml", '''pragma Singleton
import QtQuick
QtObject {
    property bool sidebarRightOpen: false
    property bool screenLocked: false
    function openSidebarRight(output) { sidebarRightOpen = true }
}''')
        write("qs/modules/common/qmldir", "module qs.modules.common\nsingleton Directories 1.0 Directories.qml\nsingleton Appearance 1.0 Appearance.qml\n")
        write("qs/modules/common/Directories.qml", '''pragma Singleton
import QtQuick
QtObject { property string stateUserPath: "/unused" }''')
        write("qs/modules/common/Appearance.qml", '''pragma Singleton
import QtQuick
QtObject {
    property var rounding: ({ normal: 12 })
    property var font: ({ pixelSize: { smaller: 12 } })
    property var colors: ({ colPrimary: "blue", colOnLayer1: "black", colSubtext: "gray", colError: "red" })
}''')
        # Widget doubles preserve public control behavior. These tests exercise
        # real panel QML and Qt controls, not iNiR's theme/animation rendering.
        widgets = {
            "WindowDialog": '''Rectangle {
                property bool show: false
                property real backgroundWidth: 350
                property real backgroundHeight: 500
                default property alias contentData: content.data
                signal dismiss()
                ColumnLayout { id: content; anchors.fill: parent }
            }''',
            "WindowDialogTitle": "Text {}",
            "WindowDialogButtonRow": "RowLayout {}",
            "StyledText": "Text {}",
            "StyledSwitch": "Switch {}",
            "MaterialSymbol": "Text { property int iconSize: 24 }",
            "MaterialTextField": "TextField { property bool enableSettingsSearch: true }",
            "RippleButton": "Button { property real buttonRadius: 0 }",
            "DialogButton": "Button { property alias buttonText: button.text; id: button }",
        }
        write("qs/modules/common/widgets/qmldir", "module qs.modules.common.widgets\n" +
              "\n".join(f"{name} 1.0 {name}.qml" for name in widgets))
        for name, body in widgets.items():
            write(f"qs/modules/common/widgets/{name}.qml", "import QtQuick\nimport QtQuick.Controls\nimport QtQuick.Layouts\n" + body)
        shutil.copytree(ROOT / "modules/sidebarRight/localsend", directory / "qs/modules/sidebarRight/localsend")
        write("qs/services/qmldir", "module qs.services\nsingleton LocalSend 1.0 LocalSend.qml\nsingleton Translation 1.0 Translation.qml\n")
        shutil.copyfile(ROOT / "services/LocalSend.qml", directory / "qs/services/LocalSend.qml")
        write("qs/services/Translation.qml", '''pragma Singleton
import QtQuick
QtObject { function tr(text) { return text } }''')
        write("Quickshell/qmldir", "module Quickshell\nSingleton 1.0 Singleton.qml\nsingleton Quickshell 1.0 Quickshell.qml\n")
        write("Quickshell/Singleton.qml", "import QtQuick\nQtObject { default property list<QtObject> data }")
        write("Quickshell/Quickshell.qml", '''pragma Singleton
import QtQuick
QtObject {
    property var notifications: []
    function shellPath(path) { return "/unused/" + path }
    function execDetached(args) { notifications = notifications.concat([args]) }
}''')
        write("Quickshell/Io/qmldir", "module Quickshell.Io\nProcess 1.0 Process.qml\nSplitParser 1.0 SplitParser.qml\nsingleton Harness 1.0 Harness.qml\n")
        write("Quickshell/Io/Harness.qml", '''pragma Singleton
import QtQuick
QtObject { property var process: null }''')
        write("Quickshell/Io/Process.qml", '''import QtQuick
Item {
    id: root
    property bool running: false
    property bool stdinEnabled: false
    property var command: []
    property var stdout
    property var writes: []
    signal exited(int code, int status)
    function write(data) { writes = writes.concat([JSON.parse(data)]) }
    function output(value) { stdout.read(JSON.stringify(value)) }
    function finish() { running = false; exited(0, 0) }
    Component.onCompleted: Harness.process = root
}''')
        write("Quickshell/Io/SplitParser.qml", "import QtQuick\nQtObject { signal read(string data) }")
        environment = {**os.environ, "QT_QPA_PLATFORM": "offscreen", "QT_QUICK_BACKEND": "software",
                       "QT_QPA_PLATFORMTHEME": "", "QT_QUICK_CONTROLS_STYLE": "Basic"}
        return subprocess.run([runner, "-import", temp, "-input", str(HERE / "qml")], env=environment, timeout=45).returncode


if __name__ == "__main__":
    raise SystemExit(main())
