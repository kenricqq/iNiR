# Nearby sharing with LocalSend

The right sidebar's quick controls include a **LocalSend** row in both full and
compact layouts, with either classic or Android quick toggles. Its switch turns
sharing on or off; click the row to open nearby devices and transfers.

1. Turn on LocalSend in the sidebar. Open LocalSend on the other device, enable
   encryption there, and connect both devices to the same local network.
2. To send, select a nearby device, choose files, and press **Send**. Enter the
   receiver's PIN if it requires one. The receiver approves the transfer.
3. To receive, review the sender and file names, then choose **Accept** or
   **Decline**. Requests expire after two minutes. Closing the sidebar does not
   accept, decline, or stop a transfer.
4. Completed files appear in the XDG Downloads directory under `LocalSend/`.
   **Open received files** opens that folder. Existing files are preserved;
   duplicates receive numbered names.

Sharing starts **off** each shell session. While on, anyone on the reachable
local network can discover this computer and request a transfer. There is no
contacts database or automatic acceptance. Turning off cancels unfinished
transfers and closes the endpoint. Already completed files remain available.
Incoming requests do not open their details over the lock screen.

## Troubleshooting

- **No devices:** open LocalSend on the other device and use **Refresh**. If
  multicast is blocked, enter its local IPv4 address and press **Find**. This
  probes the standard LocalSend port, 53317. Guest Wi-Fi and client isolation can
  block device-to-device traffic entirely. Firewall rules must permit TCP and UDP
  53317 on the intended local network; iNiR does not change firewall rules.
- **Port already in use:** close the separate LocalSend app (including its tray
  process) before enabling the sidebar endpoint. iNiR does not kill other apps.
- **Cannot start:** Python 3 and the OpenSSL executable are required. Packaged
  dependencies include them. Errors appear in the LocalSend panel; toggle on to
  retry after resolving the cause.
- **Device identity changed:** refresh discovery and confirm the intended device
  before resending. Device names are self-reported, not verified identities.
- **PIN required:** enter the receiver's PIN and press Send again.
- **Cancelled or failed:** incomplete files are removed on normal cancellation,
  disconnect, and shutdown. Retry the transfer. Forced termination or power loss
  can leave hidden `.inir-part-*` files; these are never treated as completed
  downloads and can be removed once sharing is off.

## Compatibility and boundaries

This is a native iNiR endpoint implementing the
[LocalSend v2 protocol](https://github.com/localsend/protocol), rather than a
controller for the separate Flutter app. It does not speak Apple's AirDrop
protocol; other devices need a LocalSend-compatible client.

- IPv4 multicast discovery, registration, direct-IP discovery, HTTPS uploads,
  explicit receive approval, progress, cancellation, optional receiver PIN, and
  partial acceptance by a receiver are implemented.
- Outgoing selection supports regular files. Archive folders before sending.
  Incoming relative folder paths are preserved without following symlinks.
- Up to 1,000 files and 100 GiB per transfer; one transfer at a time. Accepted
  transfers expire after five minutes without progress.
- Unencrypted HTTP peers, IPv6-only networks, browser-download mode, contacts,
  text-message composition, resume, and automatic receive are not implemented.
- TLS uses a persistent self-signed certificate in
  `Directories.stateUserPath/localsend/`. Outgoing connections check the
  discovered certificate fingerprint before sending metadata or bytes. Discovery
  itself is unauthenticated; verify the recipient and incoming sender yourself.
  Incoming tokens are bound to the approving session, file, and sender IP.
- The service owns transfers independently of sidebar lifetime. There is no
  background service or LAN listener while off. The helper accepts local control
  only through its parent process's stdin, not a network control endpoint.

## Development and tests

```sh
make test-localsend
```

The Python suite uses temporary directories and loopback TLS servers. It needs
permission to create local sockets but never announces on the LAN or writes to
the user's Downloads. It covers real file round trips, approval, protocol
errors, spoofed tokens, identity changes, checksum failures, traversal and
symlink attempts, atomic collision handling, interruption, cancellation, and
limits. QML tests require Qt 6 `qmltestrunner` and exercise the actual service
with process/desktop doubles, so they cannot launch sharing or desktop commands.
Panel tests use Qt controls with widget doubles; they validate interaction and
bindings rather than the shell's theme rendering or compositor behavior.

For a real-device acceptance check, test sending and receiving between this
sidebar and a current LocalSend app, reject a request, cancel a large transfer,
then toggle off and confirm the device is no longer reachable. Check both
sidebar layouts, the native file picker, and at least one mobile client. Local
protocol tests are not a substitute for that device and compositor check.
