import QtQml

QtObject {
    id: root

    property bool active: false
    property bool _held: false

    function _sync(): void {
        if (root.active && !root._held) {
            root._held = true
            ResourceUsage.keepAlive()
        } else if (!root.active && root._held) {
            root._held = false
            ResourceUsage.releaseKeepAlive()
        }
    }

    onActiveChanged: root._sync()
    Component.onCompleted: root._sync()
    Component.onDestruction: {
        if (root._held) {
            root._held = false
            ResourceUsage.releaseKeepAlive()
        }
    }
}
