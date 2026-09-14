import QtQuick
import QtTest
import Quickshell.Io
import qs.services

TestCase {
    id: root
    name: "DankSocketContract"

    DankSocket {
        id: client
        path: "/tmp/inir-test.sock"
        reconnectBaseMs: 30
        reconnectMaxMs: 30
    }

    function init() {
        client.enabled = false
        wait(1)
        Harness.socket.writes = []
        Harness.socket.flushCount = 0
    }

    function test_send_reports_connectivity_and_frames_json() {
        compare(client.send({ Action: "Test" }), false)
        compare(Harness.socket.writes.length, 0)

        client.enabled = true
        tryCompare(client, "connected", true)
        compare(client.send({ Action: "Test" }), true)
        compare(Harness.socket.writes, ['{"Action":"Test"}\n'])
        compare(Harness.socket.flushCount, 1)
    }

    function test_disconnect_updates_readiness_then_reconnects() {
        client.enabled = true
        tryCompare(client, "connected", true)
        Harness.socket.disconnectFromPeer()
        compare(client.connected, false)
        tryCompare(client, "connected", true, 300)
    }

    function test_disabling_during_backoff_cancels_reconnect() {
        client.enabled = true
        tryCompare(client, "connected", true)
        Harness.socket.disconnectFromPeer()
        client.enabled = false
        wait(100)
        compare(client.connected, false)
        compare(Harness.socket.connected, false)
    }
}
