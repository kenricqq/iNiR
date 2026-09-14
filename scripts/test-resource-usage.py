#!/usr/bin/env python3
"""Exercise ResourceUsage's public demand-lifetime interface with QtTest."""

import os
from pathlib import Path
import shutil
import subprocess
import tempfile


ROOT = Path(__file__).resolve().parents[1]
TEST = ROOT / "scripts/resource/tests/tst_ResourceUsage.qml"


def main():
    runner = shutil.which("qmltestrunner6")
    if not runner and Path("/usr/lib/qt6/bin/qmltestrunner").exists():
        runner = "/usr/lib/qt6/bin/qmltestrunner"
    runner = runner or shutil.which("qmltestrunner")
    if not runner:
        raise SystemExit("Qt 6 qmltestrunner is required")

    with tempfile.TemporaryDirectory(prefix="inir-resource-qml-") as temp:
        directory = Path(temp)

        def write(name, content):
            path = directory / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content)

        write("qs/services/qmldir", "module qs.services\nsingleton ResourceUsage 1.0 ResourceUsage.qml\nResourceUsageLease 1.0 ResourceUsageLease.qml\n")
        shutil.copyfile(ROOT / "services/ResourceUsage.qml", directory / "qs/services/ResourceUsage.qml")
        shutil.copyfile(ROOT / "services/ResourceUsageLease.qml", directory / "qs/services/ResourceUsageLease.qml")
        write("qs/modules/common/qmldir", "module qs.modules.common\nsingleton Config 1.0 Config.qml\n")
        write("qs/modules/common/Config.qml", '''pragma Singleton
import QtQuick
QtObject {
    property var options: ({ resources: {
        autoStopDelay: 150, updateInterval: 30, historyLength: 4, monitorGpu: false
    } })
}''')
        write("Quickshell/qmldir", "module Quickshell\nSingleton 1.0 Singleton.qml\nsingleton Quickshell 1.0 Quickshell.qml\n")
        write("Quickshell/Singleton.qml", "import QtQuick\nQtObject { default property list<QtObject> data }\n")
        write("Quickshell/Quickshell.qml", '''pragma Singleton
import QtQuick
QtObject { function shellPath(path) { return "/unused/" + path } }
''')
        write("Quickshell/Io/qmldir", "module Quickshell.Io\nProcess 1.0 Process.qml\nFileView 1.0 FileView.qml\nSplitParser 1.0 SplitParser.qml\nStdioCollector 1.0 StdioCollector.qml\n")
        write("Quickshell/Io/Process.qml", '''import QtQuick
QtObject {
    property var command: []
    property var environment: ({})
    property bool running: false
    property QtObject stdout
    property QtObject stderr
    signal exited(int exitCode, int exitStatus)
}''')
        write("Quickshell/Io/FileView.qml", '''import QtQuick
QtObject {
    property string path: ""
    property bool preload: true
    signal loaded()
    signal loadFailed(int error)
    function reload() {}
    function text() { return "" }
}''')
        write("Quickshell/Io/SplitParser.qml", "import QtQuick\nQtObject { signal read(string data) }\n")
        write("Quickshell/Io/StdioCollector.qml", "import QtQuick\nQtObject { property string text: \"\"; signal streamFinished() }\n")
        shutil.copyfile(TEST, directory / "tst_ResourceUsage.qml")
        environment = {
            **os.environ,
            "QT_QPA_PLATFORM": "offscreen",
            "QT_QPA_PLATFORMTHEME": "",
            "QT_QUICK_BACKEND": "software",
        }
        return subprocess.run(
            [runner, "-import", temp, "-input", temp],
            env=environment,
            timeout=30,
        ).returncode


if __name__ == "__main__":
    raise SystemExit(main())
