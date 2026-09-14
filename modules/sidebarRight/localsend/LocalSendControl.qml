import qs.services
import qs.modules.common
import qs.modules.common.widgets
import QtQuick
import QtQuick.Layouts

RowLayout {
    id: root
    spacing: 8
    implicitHeight: 52

    RippleButton {
        Layout.fillWidth: true
        Layout.fillHeight: true
        buttonRadius: Appearance.rounding.normal
        onClicked: LocalSend.openPanel()
        Accessible.name: Translation.tr("LocalSend nearby sharing")
        contentItem: RowLayout {
            spacing: 10
            MaterialSymbol {
                Layout.leftMargin: 10
                text: "nearby_share"
                iconSize: 24
                color: LocalSend.enabled ? Appearance.colors.colPrimary : Appearance.colors.colOnLayer1
            }
            ColumnLayout {
                Layout.fillWidth: true
                spacing: 1
                StyledText { text: "LocalSend"; font.bold: true }
                StyledText {
                    Layout.fillWidth: true
                    font.pixelSize: Appearance.font.pixelSize.smaller
                    color: Appearance.colors.colSubtext
                    elide: Text.ElideRight
                    text: LocalSend.pending ? Translation.tr("Incoming files · Review")
                        : LocalSend.busy ? Translation.tr("Transfer in progress")
                        : LocalSend.error ? Translation.tr("Needs attention")
                        : LocalSend.phase === "starting" ? Translation.tr("Turning on…")
                        : LocalSend.phase === "stopping" ? Translation.tr("Turning off…")
                        : LocalSend.enabled ? Translation.tr("Visible to nearby devices") : Translation.tr("Receiving off")
                }
            }
            MaterialSymbol { text: "chevron_right"; iconSize: 20 }
        }
    }
    StyledSwitch {
        objectName: "localSendSwitch"
        Layout.rightMargin: 8
        checked: LocalSend.enabled || LocalSend.phase === "starting"
        enabled: !LocalSend.transitioning
        Accessible.name: Translation.tr("Enable LocalSend")
        onClicked: LocalSend.setEnabled(!LocalSend.enabled)
    }
}
