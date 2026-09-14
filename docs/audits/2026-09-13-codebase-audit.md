# iNiR codebase audit and improvement plan

Date: 2026-09-13. Reviewed checkout: `76312f8a4b685880157e135c610ba24f89b85892`.

## Assessment

The highest-value work is fixing lifecycle and persistence contracts, adding automated regression gates, then measuring rendering costs. A rewrite would discard useful existing work: lazy panel loading, shared settings navigation, demand-driven window previews, GPU power-state guards, a common runtime-payload policy, and LocalSend's separation of transport, storage, session state, and UI.

Several defects are reproducible without touching the desktop: resource polling defeats its idle timeout; configuration can restore deleted data and serialize inconsistent types; Nix's blanket path rewrite changes executable-check semantics; custom-widget removal can escape its intended directory. These should precede cosmetic refactoring or speculative performance tuning.

This is a broad, risk-directed audit, not a line-by-line review of every file or an exhaustive security assessment. No implementation changes, package installation, shell restart, configuration edits, remote changes, or push were performed for this audit. This report is the only repository addition.

## Scope and evidence

The tracked-source inventory contains approximately 402,000 lines in 1,361 files: 1,109 QML, 140 `.sh`, 53 Python, 45 JavaScript, four Go, eight Nix, and two extensionless shell entrypoints. Counts include comments and tests. Static declarations include 546 `Timer`, 405 `Process`, 307 `execDetached`, 298 `layer.enabled`, and 90 `MultiEffect` occurrences. These are search counts, **not concurrently active objects or evidence of a performance defect**.

Inspected areas include shell startup and panel loaders; configuration and settings ownership; compositor sockets; resource, network, media, preview, and memory-monitoring services; common rendering and launch helpers; custom widgets; LocalSend; installation/update paths; Nix packaging; distribution tests; and contributor documentation. All explicitly listed QML file targets in tracked `qmldir` files exist.

### Live baseline has a different revision

The installed repo-copy runtime reports version 2.30.0, commit `dcba34ee`, from `/home/kenrict/.local/src/inir`. It is **not** the audited checkout with the LocalSend commit. Its source remote remains the original upstream; the sandbox checkout points at the fork.

One four-second observation of the installed `inir.service` found:

| Measurement | Result | Interpretation |
| --- | --- | --- |
| Main process RSS | 1,316,052 KiB, about 1.26 GiB | Resident pages, including shared pages |
| Main process PSS | 1,193,713 KiB, about 1.14 GiB | Shared pages apportioned across processes |
| Service cgroup memory | 1,765,064,704 bytes, about 1.64 GiB | Broader than the main-process measurement |
| CPU | 4.0% of one core | Short observation, not a controlled idle benchmark |
| Main-process threads | 30 | Descriptive only |
| Automatic service restarts | 0 | Current unit counter |

The sampled profile used the ii family, `inir` style, wallpaper blur, and a 3-second resource interval with GPU monitoring enabled. Descendants included `nmcli`, Python, `swayidle`, and mpv. A subsequent query of the last two hours returned 48 journal records with no matches for the selected ReferenceError, TypeError, binding-loop, failed-start, assignment-error, or loading-error categories. This does not establish error-free operation.

The sample does not identify a leak, a regression, a particular expensive feature, or expected memory savings. Establish a controlled baseline on the same revision before comparing changes.

### Verification performed

| Check | Result |
| --- | --- |
| `bash scripts/test-local-distribution.sh` without runtime mode | Passed, including payload, migration/recovery, package, launcher, and IPC-registry checks |
| Payload tests invoked by distribution suite | 10 passed |
| `python3 scripts/test-detect-sensors.py` | 6 passed |
| LocalSend Python suite | 51 passed with local socket permission |
| LocalSend Qt service/panel suite | 18 passed, including QtTest setup/cleanup entries |
| `go test ./...` with an isolated cache, network disabled | Four packages compiled; no Go test files |
| Python AST parse | All 53 tracked Python files parsed |
| Bash syntax pass | 141 of 142 candidate files passed; the remaining file embeds non-shell emoji data after an unconditional `exit` |
| Temporary defect probes | Configuration, packaging, removal, and polling behavior reproduced |

The first LocalSend run failed because the sandbox prohibited test sockets; the permitted rerun passed. The emoji file's raw `bash -n` failure is not counted as a runtime defect: [emoji-data.sh](/home/kenrict/Projects/Sandbox/iNiR/scripts/emoji/emoji-data.sh:25) intentionally appends data after the executable section. A general syntax gate needs to recognize that format.

Reproduction script: [probe.py](/tmp/inir-audit-2026-09-13/probe.py). Results: [probe-results.txt](/tmp/inir-audit-2026-09-13/probe-results.txt). These temporary artifacts extract production JavaScript functions, exercise disposable filesystem fixtures, and use production resource-timer bodies in a QtTest harness with scaled intervals and mocked I/O. They demonstrate current behavior; they are not yet permanent regression tests or full application integration tests.

## Findings

Priorities: **P1** = fix before expanding the affected feature; **P2** = next reliability/performance tranche; **P3** = incremental maintenance. Confidence distinguishes reproduced behavior, direct source evidence, and opportunities awaiting profiling.

### F1 — P1: custom-widget deletion is not confined to the widget directory

**Reproduced.** [CustomWidgets.qml](/home/kenrict/Projects/Sandbox/iNiR/services/CustomWidgets.qml:199) accepts any nonempty widget ID; its IPC handler exposes the same operation at line 237. The removal process at line 394 interpolates that ID into a shell command containing `rm -rf`.

An ID of `../unrelated` deletes a sibling directory. The temporary fixture confirmed this with a disposable sentinel. Shell metacharacters in interpolated names are another unsafe input path. Creation at line 245 also embeds the name into shell source and generated files. [scan-widgets.sh](/home/kenrict/Projects/Sandbox/iNiR/scripts/scan-widgets.sh:15) builds JSON with unescaped directory names, so quotes or backslashes can break discovery.

This is a destructive-operation boundary failure, not a claim of remote access or privilege escalation: the exposed IPC and custom code run as the desktop user.

**Change:** put create/list/remove in a small filesystem helper. Accept one validated ID segment, validate containment and symlink policy, pass arguments as data, serialize manifests with a JSON library, and return structured success/failure. Define whether invalid legacy directory names remain listable but non-removable through this API.

**Acceptance:** valid removal affects only the selected widget; reject empty, dot, parent, absolute, slash-containing, shell-metacharacter, and symlink-escape inputs; preserve siblings and existing widgets when creation fails; surface permission errors without reporting success.

### F2 — P1: configuration has competing representations and write paths

**Reproduced helper-level defects.** [Config.qml](/home/kenrict/Projects/Sandbox/iNiR/modules/common/Config.qml:32) writes through a typed adapter, an independent JSON mirror, and separately injected dynamic buckets.

Three concrete disagreements:

1. `_applyNestedKey` converts a string such as `"true"` into a boolean, while `_applyToMirror` stores the original string. Adapter and mirror serialization can therefore disagree. Evidence: lines 62–74 and 224–254.
2. `_customDataForWrite` at line 412 falls back to old disk data when the current custom-widget bucket is empty, even after synchronization. The neighboring mascot helper explicitly guards against this resurrection; the custom helper does not.
3. `_writeMirrorToDisk` at line 466 only replaces dynamic buckets when they are nonempty. Deleting the last mascot, then taking the explicit flush/fallback path, can preserve its old entry in the mirror. The probe confirmed stale output for this state. `flushWrites` directly calls that path.

**Additional source-backed reliability risks:** pending mutations are cleared before save acknowledgment (lines 40 and 543); `onSaveFailed` uses the normal flight-release path rather than retaining a retryable journal (lines 373 and 604). Separate settings and shell processes can write full snapshots. Deferring reloads during a write avoids some races but is not a cross-process transaction. The precise concurrent-write interleavings still need integration tests.

**Change:** first fix the reproduced cases within the current API. Then establish one canonical normalized snapshot and explicit replace/delete semantics for dynamic buckets. Separate schema projection from persistence; retain mutations until durable acknowledgment. Prefer one writer owned by the shell, with a defined standalone-settings fallback, or a serialized helper that merges changes under a lock. Choose one ownership model before implementing it.

Do not casually replace the typed adapter with a dynamic object: the source documents Qt/JsonAdapter crash workarounds and many consumers rely on typed property notifications. Quickshell already defaults to atomic file replacement, but atomic replacement alone does not merge concurrent writers. [FileView documentation](https://quickshell.org/docs/v0.2.1/types/Quickshell.Io/FileView/)

**Acceptance:** clear the last custom/mascot entry, flush, reload, and verify it stays deleted; test string/boolean/number normalization consistently across every write path; preserve unrelated concurrent edits; inject permission and disk-write failures; test missing directory, corrupt JSON, interrupted writes, external reload, and shutdown flush.

### F3 — P1: Nix packaging changes executable checks into relative-path checks

**Reproduced transformation.** [nix/package.nix](/home/kenrict/Projects/Sandbox/iNiR/nix/package.nix:171) removes `/usr/bin/` throughout packaged QML, JS, shell, and Python bodies.

In [ShellExec.qml](/home/kenrict/Projects/Sandbox/iNiR/modules/common/functions/ShellExec.qml:13), this transforms the fish check into `test -x fish`, the manager check into `[ -x systemctl ]`, and the scope check into `[ -x systemd-run ]`. Those checks inspect paths relative to the working directory; they do not search `PATH`. The fixture confirmed failure despite an executable being available in a separate binary directory.

This can disable fish detection, user-manager environment restoration, and systemd scope launching in the Nix package. The full Nix build was not run; the failed source transformation itself is established.

**Change:** remove blanket source rewriting. Use an explicit dependency map/targeted substitutions for absolute executables, or resolve commands through `PATH` and test the resolved path. Retain argument-array execution and the existing application-environment policy.

**Acceptance:** exercise the **packaged** ShellExec command, with fake executables on PATH and no matching files in cwd; verify environment import, fish fallback, scope ownership, and spaces in arguments. Run a Nix package build and smoke test in an environment without an Arch `/usr/bin` layout.

### F4 — P2: resource polling continually renews its own idle lease

**Reproduced in JavaScript and Qt's timer loop.** [ResourceUsage.qml](/home/kenrict/Projects/Sandbox/iNiR/services/ResourceUsage.qml:242) restarts `autoStopTimer` inside every `_pollSensors` call. The polling timer repeats every 3 seconds by default; the idle deadline is 15 seconds. Polling itself keeps the service alive after a transient consumer is gone.

The Qt probe used the production timer bodies with 30 ms polling and a 150 ms idle deadline. It remained running after 400 ms; once polling stopped, the idle timer expired. A deterministic 60-second simulation moved the deadline out to 75 seconds without a new consumer request.

**Change:** renew demand only from consumers. Keep visible, persistent consumers registered; release them when hidden/destroyed. Review transient consumers before removing the renewal: some currently call `ensureRunning()` only once and may expect a visible graph to update indefinitely. The fix must not make an open system monitor freeze after 15 seconds.

**Acceptance:** no sensor/disk activity after the last consumer's idle deadline; visible graphs continue updating; acquire/release is balanced across family switches and Loader destruction; reopening primes values; suspended GPUs stay asleep; disabling GPU monitoring cancels outstanding GPU activity where supported.

### F5 — P2: compositor connection intent is exposed as readiness

**Direct source evidence; disconnect integration test outstanding.** [DankSocket.qml](/home/kenrict/Projects/Sandbox/iNiR/services/DankSocket.qml:9) exposes `connected` as the desired state, separate from the internal Socket's actual state. [NiriService.qml](/home/kenrict/Projects/Sandbox/iNiR/services/NiriService.qml:24) treats that property as `actionReady`; action dispatch at line 801 checks the same desired-state flag.

When the transport disconnects, requested connection can remain true, so controls can remain enabled and dispatch into an unavailable socket. A scheduled reconnect also unconditionally sets the socket connected again (DankSocket lines 37–44), without checking whether the owner disabled it during the delay. The underlying Socket reports actual connectivity and ignores writes while disconnected. [Quickshell Socket contract](https://quickshell.org/docs/v0.2.1/types/Quickshell.Io/Socket/)

**Change:** expose distinct `enabled`, actual `connected`, and optionally `connecting`/`lastError` states. Cancel retries when disabled and guard delayed callbacks. Keep the existing bounded exponential backoff. Make `send` report rejection while disconnected; avoid replaying destructive compositor actions after reconnection.

**Acceptance:** connection failure disables dependent actions; disconnect/reconnect updates readiness; disabling during backoff prevents reconnection; teardown cancels callbacks; stale requests are not replayed against a new compositor session.

### F6 — P2: Network owns processes by command pattern instead of instance

**Direct source evidence.** [Network.qml](/home/kenrict/Projects/Sandbox/iNiR/services/Network.qml:225) runs `pkill -f "nmcli monitor"` whenever this singleton initializes. That can kill another shell instance's monitor or an unrelated same-user diagnostic process. Its subscriber immediately restarts whenever `running` becomes false, without delay or a retry limit (line 238).

**Change:** stop only the child this service owns; rely on explicit lifetime management rather than a global pattern kill. Use capped backoff with reset after a healthy connection, and publish degraded state. Initially keep nmcli as the backend; a direct NetworkManager interface is an optional later change if measurements justify it.

**Acceptance:** a separate monitor survives service startup; repeated child failure has a bounded launch rate; teardown prevents restart; connectivity events remain coalesced; recovery after suspend and NetworkManager restart succeeds.

### F7 — P2: mpv status polling repeatedly spawns shells and socat

**Direct source evidence; aggregate CPU savings unmeasured.** [YtMusic.qml](/home/kenrict/Projects/Sandbox/iNiR/services/YtMusic.qml:414) polls every 500 ms whenever a track ID exists. Depending on MPRIS availability and duration state, it requests one to four subprocess queries per tick. Each query is a shell pipeline using socat (lines 450–491). Pausing does not disable the timer.

**Change:** own one persistent mpv IPC connection; subscribe to position, pause, duration, and EOF changes; separate transport recovery from playlist policy. Keep MPRIS integration where it serves desktop interoperability. mpv's documented `observe_property` mechanism requires the connection to stay open. [mpv JSON IPC documentation](https://mpv.io/manual/master/#json-ipc)

**Acceptance:** no recurring shell/socat launches for status in steady playback or pause; correct seek/duration updates; exactly one advance at EOF; reconnect resubscribes; stale events from the previous track cannot advance the new one; missing MPRIS remains supported.

### F8 — P2: repeated full-screen wallpaper effects are a strong profiling target

**Structural evidence; optimization value requires measurement.** [GlassBackground.qml](/home/kenrict/Projects/Sandbox/iNiR/modules/common/widgets/GlassBackground.qml:62) gives each active instance a mask layer and a screen-sized wallpaper image with its own MultiEffect layer at line 102. Decoded image caching does not mean those per-instance effect outputs are shared. A small card can therefore own a large effect input.

Qt documents offscreen allocation and batching costs for layers. A single 1920×1080 RGBA texture is about 7.9 MiB; a 3840×2160 texture is about 31.6 MiB before extra passes, padding, and device-scale effects. These are illustrative texture sizes, **not measured savings or total GPU memory**. [Qt Quick Item layers](https://doc.qt.io/qt-6/qml-qtquick-item.html#memory-and-performance)

**Change only after profiling:** compare a cached preblurred wallpaper asset, per-window shared effect sources, and cropped/downsampled effect inputs. Share results only where Qt's window/scene-graph ownership permits it; do not assume a live texture can be shared across independent panel windows. Cache keys must include wallpaper version, output dimensions/scale, crop, blur, and saturation. Retain explicit invalidation and bounded cache lifetime.

**Acceptance:** matched screenshots for enabled styles; multi-monitor/fractional-scale correctness; wallpaper changes invalidate old output; hidden windows release resources; sustained p95/p99 frame times and GPU allocations improve in the same scenario. Keep a simple solid fallback.

### F9 — P2: current diagnostics overstate what they measure

**Direct source evidence.** [shell.qml](/home/kenrict/Projects/Sandbox/iNiR/shell.qml:118) labels a 0/200 ms timer firing as “first frame.” This measures a scheduled startup milestone, not presentation. [MemoryPressureService.qml](/home/kenrict/Projects/Sandbox/iNiR/services/MemoryPressureService.qml:90) converts a mapping count into MB using a fixed 0.5 multiplier, then says a restart would free that amount. The collector reads mapping counts, not resident/reclaimable bytes.

**Change:** name timer milestones honestly and add an actual presentation-related measurement through the supported window/backend interface. Qt's `frameSwapped` is a presentation-queue boundary; compositor-visible latency requires additional compositor evidence. Measure RSS/PSS and their trend separately from mapping counts; describe mapping counts as diagnostic signals, not established leak bytes. [QQuickWindow frameSwapped](https://doc.qt.io/qt-6/qquickwindow.html#frameSwapped)

**Acceptance:** startup output distinguishes config-ready, UI construction, frame queued, and deferred-services-ready; memory reports identify units and source; unavailable measurements remain unavailable; restart advice does not promise unmeasured reclamation.

### F10 — P2: regression checks exist but are not automatically enforced in this checkout

**Repository evidence.** There are no tracked `.github/workflows` files. The local distribution script has useful behavioral fixtures, but many other guards check source strings rather than runtime contracts. [.gitignore](/home/kenrict/Projects/Sandbox/iNiR/.gitignore:154) ignores all `test_*.py` except the new LocalSend test directory, which can silently hide future tests from commits.

**Change:** add a bounded CI gate using the existing tests first; make dependency versions explicit; remove or narrowly scope the broad test ignore. Expand the QtTest fixture approach around Config, ResourceUsage, process ownership, and socket lifetimes. Add packaged-artifact tests and a separate controlled compositor acceptance job/checklist. Do not impose a noisy repository-wide lint failure wall without classifying the existing baseline.

**Acceptance:** new tests are tracked; CI discovers and executes them; deliberately breaking each reproduced contract fails a behavioral test; package smoke checks inspect transformed output, not just source; failure logs identify dependency/environment failures separately from code regressions.

### F11 — P2: update metadata and code can come from different repositories

**Direct source evidence, relevant to this fork.** [versioning.sh](/home/kenrict/Projects/Sandbox/iNiR/sdata/lib/versioning.sh:38) hardcodes `snowarch/inir` for GitHub API metadata, while [ShellUpdates.qml](/home/kenrict/Projects/Sandbox/iNiR/services/ShellUpdates.qml:794) fetches `origin`. Changing an installed origin to the fork does not align every update/version source.

**Change:** define one explicit update-source descriptor with code remote, branch/channel, and release/assets origin. Default sensibly from supported remotes, while allowing a fork to intentionally use upstream assets. Handle forks with no releases and SSH/HTTPS URLs. Keep the installed checkout's remote change a separate explicit action; this audit did not change it.

**Acceptance:** stock upstream, fork with releases, fork without releases, offline mode, and branch-only updates report the source consistently; update previews match the repository actually fetched; provenance remains visible after installation.

### F12 — P3: architecture should deepen existing modules rather than add wrapper layers

**Maintainability evidence.** Large files combine UI composition, state transitions, persistence, and external commands: DesktopWidgetsConfig ~5,936 lines, Config ~3,725, NiriConfig ~3,272, Background ~3,211, and YtMusic ~2,263. Size alone is not a defect, but these are expensive places to change several contracts at once.

Good starting points already exist: [SettingsPageRegistry.qml](/home/kenrict/Projects/Sandbox/iNiR/modules/settings/SettingsPageRegistry.qml:8) shares navigation between overlay and standalone settings; [WindowPreviewService.qml](/home/kenrict/Projects/Sandbox/iNiR/services/WindowPreviewService.qml:63) defers capture until requested; Appearance already has semantic color/effect helpers.

**Change:** extract along stable responsibilities: configuration persistence; compositor transport; media transport; widget filesystem operations; feature-specific settings models. Keep public UI behavior stable while moving one contract at a time. Reuse existing semantic appearance tokens before introducing more style-specific ternaries. Keep the ii and Waffle layouts distinct where they serve different interactions; share state and policy.

Documentation also drifts: [CONTRIBUTING.md](/home/kenrict/Projects/Sandbox/iNiR/CONTRIBUTING.md:88) requires every default in defaults/config.json, whereas [ARCHITECTURE.md](/home/kenrict/Projects/Sandbox/iNiR/ARCHITECTURE.md:135) describes curated overrides; the documented style lists omit newer styles present in Appearance. Reconcile these rules and validate registry/schema references automatically.

**Acceptance:** a small feature change has one state/persistence owner; public-contract tests remain unchanged through extraction; both settings presentations find the same page/key; documented styles and config rules agree with the source. Avoid a full schema generator until persistence semantics are stable.

## Proposed delivery plan

Each row should be a reviewable change or small sequence of changes. Effort labels are relative, not delivery promises.

| Order | Deliverable | Main files/areas | Effort | Completion gate |
| --- | --- | --- | --- | --- |
| 1 | Capture defects as permanent tests; establish CI | Existing Python/Qt test runners, `.github/workflows`, `.gitignore` | Medium | Clean CI baseline and tests that detect the reproduced failures |
| 2 | Constrain widget filesystem operations | CustomWidgets, scan-widgets, new focused helper | Small–medium | Traversal/symlink/quoting/error fixtures pass |
| 3 | Repair config deletion, normalization, and failed-save handling | Config and a persistence test harness | Medium | Every existing write path preserves the same data contract |
| 4 | Repair Nix executable resolution | nix/package.nix, ShellExec, package tests | Small–medium | Packaged launch behavior passes with isolated PATH/cwd |
| 5 | Repair demand and connection lifetimes | ResourceUsage and consumers, DankSocket/NiriService, Network | Medium | Idle/disconnect/retry/teardown tests pass |
| 6 | Make performance diagnostics trustworthy | shell boot reporting, MemoryPressureService, measurement harness | Medium | Reproducible same-revision baseline with clearly named metrics |
| 7 | Replace mpv subprocess polling | YtMusic transport and fake mpv integration fixture | Medium | Zero recurring status subprocesses; playback transition tests pass |
| 8 | Prototype and measure wallpaper-effect reuse | GlassBackground, wallpaper/cache ownership | Medium–large | Measured improvement without visual or output-scale regressions |
| 9 | Establish one configuration writer and consistent update provenance | Config/settings entrypoints, versioning/ShellUpdates | Large; split into separate changes | Concurrent-writer tests and fork update matrix pass |
| 10 | Extract settings/domain modules and reconcile docs | Largest settings files, existing registries/tokens, architecture docs | Incremental | Smaller change surfaces with equivalent behavior |

Start with steps 1–5. They address established defects and provide the foundation for safe optimization. Steps 6–8 should be driven by measurements, not by file size or static timer counts. Configuration writer ownership needs its own design review because standalone settings must continue to work when the shell is unavailable.

## Performance and acceptance matrix

Use the same commit, output topology, refresh rate, power mode, wallpaper, and enabled widgets before/after each change. Record cold and warm runs separately; repeat samples and report medians plus tail behavior. Do not use the older installed-runtime sample as the “before” for a newer checkout.

| Scenario | Measure | Required behavior |
| --- | --- | --- |
| Settled desktop, features closed | 60-second CPU samples, process-launch count, RSS/PSS | No work attributed to disabled features; explain remaining periodic work |
| Open/close right sidebar repeatedly | Open latency, frame times, memory at settled checkpoints | No continuing growth across repeated equivalent cycles; polling stops after demand expires |
| Switch ii/Waffle and styles | Loader/child-process counts, bindings, visual parity | No duplicate service children; released consumers do not retain resources indefinitely |
| Music playing, paused, EOF, seek | Process launches, event latency, advancement count | No subprocess status polling; one EOF advancement |
| Blur with one/multiple outputs | GPU allocations and p95/p99 frame time | Meet display frame budget where feasible: 16.7 ms at 60 Hz, 8.3 ms at 120 Hz; compare tails, not just average FPS |
| Compositor/network disconnect or suspend | Retry rate, readiness, recovery latency | Bounded retry, accurate unavailable state, recovery without restart loops |
| Concurrent settings and sidebar changes | Persisted values after reload | Preserve disjoint edits; deterministic policy for same-key conflicts |
| LocalSend off/on/transfer/off | Listener/process lifetime, transfer outcomes, UI state | No listener while off; sidebar closure preserves transfers; stop closes owned work |

For LocalSend, retain the existing 51 backend tests and 18 Qt checks. A real mobile/desktop client round trip, native file picker, compact/full sidebar, lock-screen request handling, port conflict, cancellation, and toggle-off reachability remain acceptance work. The tests use local peers and UI doubles; they do not prove real-device interoperability or compositor rendering.

Graphics profiling, controlled endurance testing, a complete Nix build, and destructive real-session failure injection were not performed. Run those in an isolated test session or a planned acceptance window rather than using the user's active desktop as a test harness.
