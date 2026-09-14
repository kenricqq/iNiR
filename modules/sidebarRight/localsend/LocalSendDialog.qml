pragma ComponentBehavior: Bound

import qs.services
import qs.modules.common
import qs.modules.common.widgets
import QtQuick
import QtQuick.Controls
import QtQuick.Dialogs
import QtQuick.Layouts

WindowDialog {
    id: root
    backgroundWidth: Math.min(380, Math.max(260, width - 20))
    backgroundHeight: Math.min(620, Math.max(280, height - 24))
    property var selectedFiles: []
    property string selectedPeer: ""
    Component.onDestruction: LocalSend.selectingFiles = false

    function formatBytes(bytes): string {
        if (bytes < 1024) return bytes + " B"
        if (bytes < 1048576) return (bytes / 1024).toFixed(1) + " KiB"
        if (bytes < 1073741824) return (bytes / 1048576).toFixed(1) + " MiB"
        return (bytes / 1073741824).toFixed(1) + " GiB"
    }

    FileDialog {
        id: chooser
        objectName: "localSendFilePicker"
        title: Translation.tr("Choose files to send")
        fileMode: FileDialog.OpenFiles
        onVisibleChanged: LocalSend.selectingFiles = visible
        onAccepted: root.selectedFiles = selectedFiles.map(file => file.toString())
    }

    WindowDialogTitle { text: "LocalSend" }
    LocalSendControl { Layout.fillWidth: true }
    ScrollView {
        id: scroll
        Layout.fillWidth: true
        Layout.fillHeight: true
        clip: true
        contentWidth: availableWidth
        ScrollBar.horizontal.policy: ScrollBar.AlwaysOff

        ColumnLayout {
            width: scroll.availableWidth
            spacing: 12

            StyledText {
                Layout.fillWidth: true
                wrapMode: Text.Wrap
                textFormat: Text.PlainText
                text: LocalSend.enabled
                    ? Translation.tr("Discoverable as %1. Keep LocalSend open on the other device and use the same network.").arg(LocalSend.alias)
                    : Translation.tr("Share files with nearby devices. Turn on to send or receive; every incoming transfer asks for approval.")
            }
            StyledText {
                Layout.fillWidth: true
                visible: LocalSend.error.length > 0
                text: LocalSend.error
                textFormat: Text.PlainText
                wrapMode: Text.Wrap
                color: Appearance.colors.colError
            }

            ColumnLayout {
                Layout.fillWidth: true
                visible: LocalSend.transfer !== null
                spacing: 8
                StyledText {
                    Layout.fillWidth: true
                    font.bold: true
                    textFormat: Text.PlainText
                    wrapMode: Text.Wrap
                    text: LocalSend.pending ? Translation.tr("%1 wants to share %2 files").arg(LocalSend.transfer?.peer ?? "").arg(LocalSend.transfer?.count ?? 0)
                        : LocalSend.transfer?.status === "waiting" ? Translation.tr("Waiting for the other device…")
                        : LocalSend.transfer?.status === "transferring" ? (LocalSend.transfer?.direction === "send" ? Translation.tr("Sending…") : Translation.tr("Receiving…"))
                        : LocalSend.transfer?.status === "complete" ? Translation.tr("Transfer complete")
                        : LocalSend.transfer?.status === "declined" ? Translation.tr("Transfer declined")
                        : LocalSend.transfer?.status === "expired" ? Translation.tr("Transfer expired")
                        : LocalSend.transfer?.status === "cancelled" ? Translation.tr("Transfer cancelled")
                        : Translation.tr("Transfer failed")
                }
                StyledText {
                    Layout.fillWidth: true
                    objectName: "transferFileNames"
                    textFormat: Text.PlainText
                    wrapMode: Text.WrapAnywhere
                    maximumLineCount: 5
                    elide: Text.ElideRight
                    text: (LocalSend.transfer?.names ?? []).slice(0, 5).join("\n")
                        + ((LocalSend.transfer?.count ?? 0) > 5 ? "\n…" : "")
                }
                StyledText {
                    Layout.fillWidth: true
                    text: root.formatBytes(LocalSend.transfer?.bytes ?? 0) + " / " + root.formatBytes(LocalSend.transfer?.total ?? 0)
                }
                ProgressBar {
                    Layout.fillWidth: true
                    visible: LocalSend.transfer?.status === "transferring"
                    value: (LocalSend.transfer?.total ?? 0) > 0 ? LocalSend.transfer.bytes / LocalSend.transfer.total : 0
                    Accessible.name: Translation.tr("Transfer progress")
                }
                StyledText {
                    Layout.fillWidth: true
                    visible: (LocalSend.transfer?.message ?? "").length > 0
                    text: LocalSend.transfer?.message ?? ""
                    textFormat: Text.PlainText
                    wrapMode: Text.Wrap
                }
                RowLayout {
                    visible: LocalSend.pending
                    DialogButton {
                        objectName: "declineTransfer"
                        buttonText: Translation.tr("Decline")
                        onClicked: LocalSend.command({ action: "decide", id: LocalSend.transfer.id, accept: false })
                    }
                    DialogButton {
                        objectName: "acceptTransfer"
                        buttonText: Translation.tr("Accept")
                        onClicked: LocalSend.command({ action: "decide", id: LocalSend.transfer.id, accept: true })
                    }
                }
                DialogButton {
                    visible: LocalSend.busy && !LocalSend.pending
                    buttonText: Translation.tr("Cancel transfer")
                    onClicked: LocalSend.command({ action: "cancel", id: LocalSend.transfer.id })
                }
            }

            ColumnLayout {
                Layout.fillWidth: true
                visible: LocalSend.enabled && !LocalSend.busy
                spacing: 10
                RowLayout {
                    Layout.fillWidth: true
                    StyledText { Layout.fillWidth: true; text: Translation.tr("Nearby devices"); font.bold: true }
                    DialogButton {
                        buttonText: Translation.tr("Refresh")
                        onClicked: LocalSend.command({ action: "refresh" })
                    }
                }
                StyledText {
                    Layout.fillWidth: true
                    visible: LocalSend.peers.length === 0
                    text: Translation.tr("No devices found yet. Open LocalSend on the other device with encryption enabled. Check that your network allows local discovery.")
                    wrapMode: Text.Wrap
                }
                Repeater {
                    model: LocalSend.peers
                    delegate: RadioButton {
                        id: device
                        required property var modelData
                        Layout.fillWidth: true
                        checked: root.selectedPeer === modelData.id
                        onClicked: root.selectedPeer = modelData.id
                        Accessible.name: modelData.alias + " " + modelData.ip
                        contentItem: StyledText {
                            leftPadding: 30
                            text: device.modelData.alias + "\n" + device.modelData.ip
                            textFormat: Text.PlainText
                            elide: Text.ElideRight
                        }
                    }
                }
                RowLayout {
                    Layout.fillWidth: true
                    MaterialTextField {
                        id: manualAddress
                        Layout.fillWidth: true
                        enableSettingsSearch: false
                        placeholderText: Translation.tr("Device IP address")
                        maximumLength: 15
                        Accessible.name: placeholderText
                        onAccepted: LocalSend.command({ action: "discover", address: text.trim() })
                    }
                    DialogButton {
                        buttonText: Translation.tr("Find")
                        enabled: manualAddress.text.trim().length > 0
                        onClicked: LocalSend.command({ action: "discover", address: manualAddress.text.trim() })
                    }
                }
                DialogButton {
                    buttonText: root.selectedFiles.length ? Translation.tr("%1 files selected · Change").arg(root.selectedFiles.length) : Translation.tr("Choose files…")
                    onClicked: chooser.open()
                }
                MaterialTextField {
                    id: pin
                    enableSettingsSearch: false
                    Layout.fillWidth: true
                    placeholderText: Translation.tr("Receiver PIN (if required)")
                    echoMode: TextInput.Password
                    maximumLength: 32
                    Accessible.name: placeholderText
                }
                DialogButton {
                    objectName: "sendFiles"
                    buttonText: Translation.tr("Send")
                    enabled: root.selectedFiles.length > 0 && LocalSend.peers.some(peer => peer.id === root.selectedPeer)
                    onClicked: {
                        LocalSend.command({ action: "send", peer: root.selectedPeer, paths: root.selectedFiles, pin: pin.text })
                        pin.clear()
                    }
                }
            }

            StyledText {
                Layout.fillWidth: true
                text: Translation.tr("Files are saved in Downloads/LocalSend. Turning off cancels unfinished transfers.")
                wrapMode: Text.Wrap
                color: Appearance.colors.colSubtext
            }
            DialogButton {
                visible: LocalSend.directory.length > 0
                buttonText: Translation.tr("Open received files")
                onClicked: Qt.openUrlExternally("file://" + LocalSend.directory.split("/").map(encodeURIComponent).join("/"))
            }
        }
    }
    WindowDialogButtonRow {
        Item { Layout.fillWidth: true }
        DialogButton { buttonText: Translation.tr("Done"); onClicked: root.dismiss() }
    }
}
