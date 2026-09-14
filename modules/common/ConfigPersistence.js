.pragma library

function cloneObject(value) {
    try {
        return JSON.parse(JSON.stringify(value ?? {}));
    } catch (error) {
        return {};
    }
}

function hasObjectKeys(value) {
    return value && typeof value === "object" && Object.keys(value).length > 0;
}

function normalize(value) {
    if (typeof value !== "string")
        return value;
    const trimmed = value.trim();
    if (trimmed === "true" || trimmed === "false" || (trimmed.length > 0 && !isNaN(Number(trimmed)))) {
        try {
            return JSON.parse(trimmed);
        } catch (error) {}
    }
    return value;
}

function dynamicData(current, synced, fallback) {
    if (hasObjectKeys(current) || synced)
        return cloneObject(current);
    return cloneObject(fallback);
}

function withDynamicBuckets(mirror, custom, customSynced, mascot, mascotSynced) {
    const result = cloneObject(mirror);
    const replaceCustom = customSynced || hasObjectKeys(custom);
    const replaceMascot = mascotSynced || hasObjectKeys(mascot);
    if (!replaceCustom && !replaceMascot)
        return result;
    if (!result.background) result.background = {};
    if (!result.background.widgets) result.background.widgets = {};
    if (replaceCustom) result.background.widgets.custom = cloneObject(custom);
    if (replaceMascot) result.background.widgets.mascotInstances = cloneObject(mascot);
    return result;
}

function beginMutationFlight(pending) {
    return { pending: {}, inFlight: cloneObject(pending) };
}

function restoreFailedMutations(inFlight, pending) {
    return Object.assign({}, cloneObject(inFlight), cloneObject(pending));
}
