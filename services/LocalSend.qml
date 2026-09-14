pragma Singleton
pragma ComponentBehavior: Bound

import qs
import qs.modules.common
import QtQuick
import Quickshell
import Quickshell.Io

// The service, not a sidebar Loader, owns the endpoint and transfer lifetime.
Singleton {
    id: root

    property string phase: "off"
    readonly property bool enabled: phase === "on"
    readonly property bool transitioning: phase === "starting" || phase === "stopping"
    property bool panelOpen: false
    property bool selectingFiles: false
    property string alias: ""
    property string directory: ""
    property string error: ""
    property var peers: []
    property var transfer: null
    property bool sendRequested: false
    readonly property bool pending: transfer?.status === "pending"
    readonly property bool busy: sendRequested || ["pending", "waiting", "transferring"].indexOf(transfer?.status) >= 0

    function openPanel(): void {
        GlobalStates.openSidebarRight("")
        root.panelOpen = true
    }

    function setEnabled(value): void {
        if (root.transitioning || value === root.enabled) return
        root.error = ""
        if (value) {
            root.phase = "starting"
            root.peers = []
            root.transfer = null
            root.sendRequested = false
            endpoint.running = true
            watchdog.interval = 25000
            watchdog.restart()
        } else {
            root.phase = "stopping"
            root.peers = []
            endpoint.write(JSON.stringify({ action: "stop" }) + "\n")
            watchdog.interval = 4000
            watchdog.restart()
        }
    }

    function command(value): void {
        if (!root.enabled) return
        if (value.action === "send") {
            if (root.busy) return
            root.sendRequested = true
        }
        root.error = ""
        endpoint.write(JSON.stringify(value) + "\n")
    }

    function receiveEvent(event): void {
        if (event.event === "ready" && root.phase === "starting") {
            watchdog.stop()
            root.alias = event.alias
            root.directory = event.directory
            root.phase = "on"
        } else if (event.event === "peers" && root.enabled) {
            root.peers = event.peers
        } else if (event.event === "transfer" && root.phase !== "off") {
            root.sendRequested = false
            const previous = root.transfer
            root.transfer = event.transfer
            if (root.pending && previous?.id !== root.transfer.id) {
                if (!GlobalStates.screenLocked) root.openPanel()
                // Untrusted device/file names stay in plain-text QML, never
                // notification markup or shell command strings.
                Quickshell.execDetached(["notify-send", "-a", "iNiR", "LocalSend",
                    Translation.tr("Incoming files. Accept or decline in the right sidebar.")])
            }
            if (root.transfer?.status === "complete" && previous?.status !== "complete" && !root.panelOpen)
                Quickshell.execDetached(["notify-send", "-a", "iNiR", "LocalSend", Translation.tr("Transfer complete")])
        } else if (event.event === "error") {
            root.sendRequested = false
            root.error = event.message
        }
    }

    Connections {
        target: GlobalStates
        function onSidebarRightOpenChanged() {
            if (!GlobalStates.sidebarRightOpen) root.panelOpen = false
        }
    }

    Timer {
        id: watchdog
        onTriggered: {
            if (root.phase === "starting") root.error = Translation.tr("LocalSend could not start. Check Python 3 and OpenSSL.")
            endpoint.running = false
            root.phase = "off"
        }
    }

    Process {
        id: endpoint
        command: ["python3", "-u", Quickshell.shellPath("scripts/localsend/bridge.py"),
            "--state-dir", Directories.stateUserPath + "/localsend"]
        stdinEnabled: true
        stdout: SplitParser {
            onRead: data => {
                try { root.receiveEvent(JSON.parse(data)) }
                catch (error) { root.error = Translation.tr("Invalid response from LocalSend helper") }
            }
        }
        onExited: {
            watchdog.stop()
            if (root.phase !== "stopping" && root.phase !== "off" && root.error === "")
                root.error = Translation.tr("LocalSend stopped unexpectedly. Turn it on to retry.")
            root.phase = "off"
            root.peers = []
            root.sendRequested = false
            if (root.busy) root.transfer = Object.assign({}, root.transfer, { status: "cancelled" })
        }
    }
}
