#!/usr/bin/env python3
"""Exercise DankSocket's public connectivity contract with QtTest."""

import os
from pathlib import Path
import shutil
import subprocess
import tempfile


ROOT = Path(__file__).resolve().parents[1]
TEST = ROOT / "scripts/socket/tests/tst_DankSocket.qml"


def main():
    runner = shutil.which("qmltestrunner6")
    if not runner and Path("/usr/lib/qt6/bin/qmltestrunner").exists():
        runner = "/usr/lib/qt6/bin/qmltestrunner"
    runner = runner or shutil.which("qmltestrunner")
    if not runner:
        raise SystemExit("Qt 6 qmltestrunner is required")

    with tempfile.TemporaryDirectory(prefix="inir-socket-qml-") as temp:
        directory = Path(temp)

        def write(name, content):
            path = directory / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content)

        write("qs/services/qmldir", "module qs.services\nDankSocket 1.0 DankSocket.qml\n")
        shutil.copyfile(ROOT / "services/DankSocket.qml", directory / "qs/services/DankSocket.qml")
        write("Quickshell/Io/qmldir", "module Quickshell.Io\nSocket 1.0 Socket.qml\nsingleton Harness 1.0 Harness.qml\n")
        write("Quickshell/Io/Harness.qml", '''pragma Singleton
import QtQuick
QtObject { property var socket: null }
''')
        write("Quickshell/Io/Socket.qml", '''import QtQuick
QtObject {
    id: root
    property string path: ""
    property QtObject parser
    property bool connected: false
    property var writes: []
    property int flushCount: 0
    signal connectionStateChanged()
    signal error(int error)
    onConnectedChanged: connectionStateChanged()
    function write(data) { writes = writes.concat([data]) }
    function flush() { flushCount++ }
    function disconnectFromPeer() { connected = false }
    Component.onCompleted: Harness.socket = root
}''')
        shutil.copyfile(TEST, directory / "tst_DankSocket.qml")
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
