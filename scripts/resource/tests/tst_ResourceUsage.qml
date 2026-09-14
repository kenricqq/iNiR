import QtQuick
import QtTest
import qs.services

TestCase {
    id: root
    name: "ResourceUsageLifetime"

    ResourceUsageLease { id: lease }

    function init() {
        lease.active = false
        ResourceUsage.stop()
    }

    function test_transient_request_expires_without_self_renewal() {
        ResourceUsage.ensureRunning()
        verify(ResourceUsage.running)
        tryCompare(ResourceUsage, "running", false, 500)
    }

    function test_keepalive_runs_until_last_consumer_releases() {
        ResourceUsage.keepAlive()
        ResourceUsage.keepAlive()
        wait(300)
        verify(ResourceUsage.running)

        ResourceUsage.releaseKeepAlive()
        wait(300)
        verify(ResourceUsage.running)

        ResourceUsage.releaseKeepAlive()
        tryCompare(ResourceUsage, "running", false, 500)
    }

    function test_lease_maps_visibility_to_keepalive() {
        lease.active = true
        verify(ResourceUsage.running)
        wait(300)
        verify(ResourceUsage.running)
        lease.active = false
        tryCompare(ResourceUsage, "running", false, 500)
    }
}
