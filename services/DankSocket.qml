import QtQuick
import Quickshell.Io

Item {
    id: root

    property alias path: socket.path
    property alias parser: socket.parser
    property bool enabled: false
    readonly property bool connected: socket.connected
    readonly property bool reconnecting: enabled && !connected && reconnectTimer.running
    property string lastError: ""

    property int reconnectBaseMs: 400
    property int reconnectMaxMs: 15000

    property int _reconnectAttempt: 0

    signal connectionStateChanged()

    onEnabledChanged: {
        reconnectTimer.stop()
        root._reconnectAttempt = 0
        if (!enabled) {
            socket.connected = false
        } else if (root.path.length > 0) {
            socket.connected = true
        }
    }

    onPathChanged: {
        if (root.enabled && !socket.connected && root.path.length > 0)
            socket.connected = true
    }

    Socket {
        id: socket

        onConnectionStateChanged: {
            root.connectionStateChanged()
            if (connected) {
                root.lastError = ""
                root._reconnectAttempt = 0
                return
            }
            if (root.enabled) {
                root._scheduleReconnect()
            }
        }

        onError: error => root.lastError = String(error)
    }

    Timer {
        id: reconnectTimer
        interval: 0
        repeat: false
        onTriggered: {
            if (root.enabled && root.path.length > 0)
                socket.connected = true
        }
    }

    function send(data): bool {
        if (!root.connected)
            return false
        const json = typeof data === "string" ? data : JSON.stringify(data)
        const message = json.endsWith("\n") ? json : json + "\n"
        socket.write(message)
        socket.flush()
        return true
    }

    function _scheduleReconnect() {
        if (!root.enabled || reconnectTimer.running)
            return
        const pow = Math.min(_reconnectAttempt, 10)
        const base = Math.min(reconnectBaseMs * Math.pow(2, pow), reconnectMaxMs)
        const jitter = Math.floor(Math.random() * Math.floor(base / 4))
        reconnectTimer.interval = base + jitter
        reconnectTimer.restart()
        _reconnectAttempt++
    }

    Component.onCompleted: {
        if (root.enabled && root.path.length > 0)
            socket.connected = true
    }
}
