import QtQuick
import QtTest
import "../../../modules/common/ConfigPersistence.js" as Persistence

TestCase {
    name: "ConfigPersistence"

    function test_values_are_normalized_once_for_every_representation() {
        compare(Persistence.normalize("true"), true)
        compare(Persistence.normalize("false"), false)
        compare(Persistence.normalize("42"), 42)
        compare(Persistence.normalize("3.5"), 3.5)
        compare(Persistence.normalize(""), "")
        compare(Persistence.normalize("hello"), "hello")
    }

    function test_synced_empty_dynamic_bucket_is_authoritative() {
        const oldData = { old: { label: "stale" } }
        compare(JSON.stringify(Persistence.dynamicData({}, true, oldData)), "{}")
        compare(JSON.stringify(Persistence.dynamicData({}, false, oldData)), JSON.stringify(oldData))
    }

    function test_dynamic_buckets_replace_stale_mirror_values() {
        const mirror = { background: { widgets: {
            custom: { old: true }, mascotInstances: { last: true }
        } } }
        const result = Persistence.withDynamicBuckets(mirror, {}, true, {}, true)
        compare(JSON.stringify(result.background.widgets.custom), "{}")
        compare(JSON.stringify(result.background.widgets.mascotInstances), "{}")
        compare(JSON.stringify(mirror.background.widgets.custom), JSON.stringify({ old: true }))
    }

    function test_failed_write_restores_inflight_mutations_without_losing_newer_changes() {
        const flight = Persistence.beginMutationFlight({ "audio.volume": 20, "bar.enable": true })
        compare(JSON.stringify(flight.pending), "{}")
        const restored = Persistence.restoreFailedMutations(
            flight.inFlight,
            { "audio.volume": 80, "appearance.style": "inir" }
        )
        compare(restored["audio.volume"], 80)
        compare(restored["bar.enable"], true)
        compare(restored["appearance.style"], "inir")
    }
}
