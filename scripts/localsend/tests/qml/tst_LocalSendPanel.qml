import QtQuick
import QtTest
import qs
import qs.services
import qs.modules.sidebarRight.localsend
import Quickshell.Io

TestCase {
    name: "LocalSendPanel"
    when: windowShown
    width: 400
    height: 700
    Component { id: panelComponent; LocalSendDialog { width: 380; height: 650; show: true } }
    Component { id: controlComponent; LocalSendControl { width: 350; height: 52 } }

    function init() {
        LocalSend.phase = "on"
        LocalSend.error = ""
        LocalSend.peers = []
        LocalSend.transfer = null
        LocalSend.sendRequested = false
        LocalSend.panelOpen = false
        Harness.process.writes = []
    }

    function cleanup() { Harness.process.finish() }

    function test_switch_sends_stop_and_disables_during_transition() {
        const control = createTemporaryObject(controlComponent, this)
        verify(control !== null)
        const toggle = findChild(control, "localSendSwitch")
        verify(toggle.checked)
        toggle.clicked()
        compare(Harness.process.writes[0].action, "stop")
        compare(LocalSend.phase, "stopping")
        verify(!toggle.enabled)
    }

    function test_send_requires_files_and_current_device() {
        const panel = createTemporaryObject(panelComponent, this)
        verify(panel !== null)
        const send = findChild(panel, "sendFiles")
        verify(!send.enabled)
        LocalSend.peers = [{ id: "peer", alias: "Phone", ip: "192.168.1.2" }]
        panel.selectedPeer = "peer"
        verify(!send.enabled)
        panel.selectedFiles = ["file:///tmp/a.txt"]
        verify(send.enabled)
        LocalSend.peers = []
        verify(!send.enabled)
    }

    function test_send_preserves_literal_file_urls() {
        const panel = createTemporaryObject(panelComponent, this)
        LocalSend.peers = [{ id: "peer", alias: "Phone", ip: "192.168.1.2" }]
        panel.selectedPeer = "peer"
        panel.selectedFiles = ["file:///tmp/hello%20world.txt", "file:///tmp/%24(value).txt"]
        findChild(panel, "sendFiles").clicked()
        compare(Harness.process.writes[0].action, "send")
        compare(Harness.process.writes[0].peer, "peer")
        compare(Harness.process.writes[0].paths, panel.selectedFiles)
    }

    function test_approval_targets_the_displayed_request_and_uses_plain_text() {
        LocalSend.transfer = { id: "request", status: "pending", peer: "<b>Phone</b>", count: 1, names: ["<b>filename</b>"], bytes: 0, total: 5 }
        const panel = createTemporaryObject(panelComponent, this)
        const names = findChild(panel, "transferFileNames")
        compare(names.textFormat, Text.PlainText)
        compare(names.text, "<b>filename</b>")
        findChild(panel, "acceptTransfer").clicked()
        compare(Harness.process.writes[0], { action: "decide", id: "request", accept: true })
        findChild(panel, "declineTransfer").clicked()
        compare(Harness.process.writes[1], { action: "decide", id: "request", accept: false })
    }
}
