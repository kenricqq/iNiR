import QtQuick
import QtTest
import qs
import qs.services
import Quickshell
import Quickshell.Io

TestCase {
    name: "LocalSendService"

    function init() {
        LocalSend.phase = "off"
        LocalSend.error = ""
        LocalSend.peers = []
        LocalSend.transfer = null
        LocalSend.sendRequested = false
        LocalSend.panelOpen = false
        GlobalStates.sidebarRightOpen = false
        GlobalStates.screenLocked = false
        Harness.process.running = false
        Harness.process.writes = []
        Quickshell.notifications = []
    }

    function cleanup() { Harness.process.finish() }

    function enable() {
        LocalSend.setEnabled(true)
        Harness.process.output({ event: "ready", alias: "Laptop", directory: "/tmp/received" })
        verify(LocalSend.enabled)
    }

    function test_off_has_no_process_or_commands() {
        verify(!LocalSend.enabled)
        verify(!Harness.process.running)
        LocalSend.command({ action: "refresh" })
        compare(Harness.process.writes.length, 0)
    }

    function test_start_requires_readiness() {
        LocalSend.setEnabled(true)
        compare(LocalSend.phase, "starting")
        verify(!LocalSend.enabled)
        verify(LocalSend.transitioning)
        verify(Harness.process.running)
        Harness.process.output({ event: "ready", alias: "Laptop", directory: "/tmp/received" })
        verify(LocalSend.enabled)
        compare(LocalSend.alias, "Laptop")
    }

    function test_rapid_toggles_do_not_spawn_or_reenable_during_stop() {
        enable()
        LocalSend.setEnabled(false)
        LocalSend.setEnabled(true)
        compare(LocalSend.phase, "stopping")
        compare(Harness.process.writes.length, 1)
        compare(Harness.process.writes[0].action, "stop")
        Harness.process.output({ event: "ready", alias: "Late", directory: "/tmp" })
        compare(LocalSend.phase, "stopping")
        Harness.process.finish()
        compare(LocalSend.phase, "off")
        compare(LocalSend.error, "")
    }

    function test_failure_preserves_reason_and_allows_retry() {
        LocalSend.setEnabled(true)
        Harness.process.output({ event: "error", message: "Port is in use" })
        Harness.process.finish()
        compare(LocalSend.error, "Port is in use")
        compare(LocalSend.phase, "off")
        enable()
        compare(LocalSend.error, "")
    }

    function test_stale_peers_are_ignored_after_stop() {
        enable()
        Harness.process.output({ event: "peers", peers: [{ id: "peer", alias: "Phone" }] })
        compare(LocalSend.peers.length, 1)
        LocalSend.setEnabled(false)
        Harness.process.output({ event: "peers", peers: [{ id: "late" }] })
        compare(LocalSend.peers.length, 0)
    }

    function test_request_opens_once_and_closing_does_not_reject() {
        enable()
        const event = { event: "transfer", transfer: { id: "one", status: "pending", peer: "Phone" } }
        Harness.process.output(event)
        verify(LocalSend.panelOpen)
        verify(GlobalStates.sidebarRightOpen)
        verify(LocalSend.busy)
        compare(Quickshell.notifications.length, 1)
        GlobalStates.sidebarRightOpen = false
        verify(!LocalSend.panelOpen)
        verify(LocalSend.enabled)
        verify(LocalSend.pending)
        compare(Harness.process.writes.length, 0)
        Harness.process.output(event)
        compare(Quickshell.notifications.length, 1)
        verify(!LocalSend.panelOpen)
    }

    function test_locked_screen_does_not_open_private_transfer_details() {
        enable()
        GlobalStates.screenLocked = true
        Harness.process.output({ event: "transfer", transfer: { id: "one", status: "pending" } })
        verify(!LocalSend.panelOpen)
        verify(!GlobalStates.sidebarRightOpen)
        verify(LocalSend.pending)
    }

    function test_crash_clears_peers_and_cancels_transfer() {
        enable()
        Harness.process.output({ event: "transfer", transfer: { id: "one", status: "transferring" } })
        Harness.process.output({ event: "peers", peers: [{ id: "peer" }] })
        Harness.process.finish()
        compare(LocalSend.phase, "off")
        compare(LocalSend.peers.length, 0)
        compare(LocalSend.transfer.status, "cancelled")
        verify(LocalSend.error.length > 0)
    }

    function test_malformed_helper_output_is_visible() {
        enable()
        Harness.process.stdout.read("invalid JSON")
        verify(LocalSend.error.length > 0)
        verify(LocalSend.enabled)
    }

    function test_double_send_is_blocked_before_helper_responds() {
        enable()
        LocalSend.command({ action: "send", peer: "peer", paths: ["file:///tmp/a"] })
        LocalSend.command({ action: "send", peer: "peer", paths: ["file:///tmp/a"] })
        compare(Harness.process.writes.length, 1)
        verify(LocalSend.busy)
        Harness.process.output({ event: "error", message: "Missing file" })
        verify(!LocalSend.busy)
    }
}
