import qs.services
import QtQuick

Loader {
    anchors.fill: parent
    z: 100
    active: LocalSend.panelOpen
    sourceComponent: LocalSendDialog {
        show: true
        onDismiss: LocalSend.panelOpen = false
        Component.onCompleted: forceActiveFocus()
    }
}
