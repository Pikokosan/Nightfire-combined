# Nightfire controller

The Android master port is now version 0.4.0. See
[`docs/android_master_port.md`](../../docs/android_master_port.md) for live
ARM/DISARM/FIRE, script upload/timed/step execution, diagnostics, calibration,
module/bridge file updates, the opt-in `usb-master-0.4.0` firmware, and validation
limits. The milestone notes below describe the earlier monitor-only versions.

Flutter application with shared Dart UI, bridge codec and session logic. Native
runner projects are included for Android, iOS, Windows and macOS. Only the Android
USB CDC adapter is implemented; other platforms currently display an explanatory
screen. An iOS runner does not establish iPhone USB accessory compatibility.

## Current milestone: command interface v0.3

- Separate Modules, Scripts, Radio and Interface views, with a side navigation rail on
  wide displays and bottom navigation on phones. Connection and unknown arm state
  remain visible above every view. Page changes preserve the radio session,
  dashboard search, cue filters and scroll position.
- Import COBRA-style CSV scripts using a native file picker, view paged events,
  and compare script channels/cues with discovered module groups and continuity.
  Missing/duplicate groups, out-of-range cues, invalid files and continuity faults
  are reported. Script upload and execution remain disabled. See
  `docs/script_workspace.md` for format, limits and snapshot semantics.
  Saved continuity does not automatically expire after 30 seconds. Event rows
  display measurement timestamps; use Check connected modules to refresh readings.
  Tap a check finding to open Modules with the referenced module/group expanded
  and its cue highlighted. Connected cues absent from the loaded script appear
  orange with a Not in script label; unloading restores normal continuity colors.
  Module-header status counts stay visible when collapsed, aligned right on
  tablets and wrapping below the module name on narrow screens.
  An orange Not in script count follows Short in the overall and per-module
  summaries when a script is loaded, including zero when all connected cues are used.
- Each expanded module has Status and Settings tabs. Settings changes its group
  number (0–65535), rejects duplicate discovered groups, and waits for a matching
  ACK plus discovery readback. Firmware 0.3.2 and a current USB host session are
  required; the field rejects settings while armed. Unconfirmed writes require
  rediscovery and never report success optimistically.
  Opening or reopening a module selects Status. Module details starts collapsed
  and is reset to collapsed when returning from Settings or following a script finding.
  The module header shows Script: Loaded beside its group when the module reports
  a script ID, otherwise Script: Not loaded. Importing a local CSV alone does not
  mark the field module as loaded.
- Module headers show a battery icon and estimated percentage on the requested
  6.4–8.4 V scale, clamped to 0–100%. The tooltip includes actual voltage; missing
  readings show an unknown icon. Battery state stays visible when collapsed.
- List the nice!nano USB radio (`239A:00B3`) and request Android USB permission.
- Open CDC at 115200 baud with DTR asserted; verify a correlated PONG response.
- Read bridge identity, firmware, network, channel and radio counters.
- Display timestamped snapshots and invalidate readiness on unplug or errors.
- Discover modules, deduplicate their replies, and fetch firmware/battery information.
- Read per-cue resistance and distinguish connected, short, open, invalid and raw
  ADC readings. Show measurement timestamps and battery/ADC/fault warnings.
- Check continuity for an individual module or all discovered modules. The all-module
  check runs sequentially, shows progress and reports updated/failed counts. A
  failed module retains its error while the check continues to the next module;
  disconnecting stops the batch. Previously discovered modules remain included
  even if they did not reply in the latest scan.
- Fall back to continuity-only requests after a resistance timeout. Unreported
  legacy cues stay invalid rather than being treated as good connections.
- Keep every cue tile at 145 × 104 logical pixels across unknown, open, short,
  connected, invalid and raw states. Long labels use ellipsis and a full tooltip;
  unmeasured cues retain their boxes. Rows wrap without stretching the boxes.
- Show dashboard totals and per-module counts for connected, open, short, invalid,
  raw and unmeasured cues. Search modules by ID, app or group and filter cues by
  status without renumbering them. Counts cover all modules, including those hidden
  by search. Technical information is under Module details. Saved readings are
  explicitly marked when disconnected; counts do not indicate arm/readiness state.
- Monitor the command interface with one-second host heartbeats and a three-second
  firmware session timeout. Heartbeats continue during field queries.
- Display physical switch position and LED configuration/fault status separately
  from remote arm confirmation.
- Keep ARM state unknown: no physical interlock or remote arm confirmation exists
  in this milestone. ARM, FIRE and scripts remain disabled. The hardware interface
  and native LED extension are prepared; switch/LED pins remain unconfigured.

Module diagnostics require `usb-monitor-0.2` or `0.3`; older monitors retain bridge status
but cannot discover modules. The firmware checks complete NFCP framing, CRC,
escaping, address type, command and payload length before permitting broadcast
discovery or unique info/continuity/resistance requests. It rejects other caller
commands except the exact unique SET_GROUP request with an active host session
on firmware 0.3.2+. Firmware `usb-monitor-0.3.1` and later generate RF keepalives every 2500 ms while
the app's USB heartbeat is current, stopping when the three-second host session
expires. Interface shows RF TX counts/failures separately from the USB heartbeat.
These broadcasts do not confirm field reception or send script heartbeats.
Receive polling uses the original bridge's
50 ms window rather than the initial status-only monitor's 1 ms window.

### Validated v0.3 build

- `dist/android/nightfire-command-interface-0.3.0-arm64-debug.apk`
- `dist/android/nightfire-command-interface-0.3.0-x64-emulator-debug.apk`
- `dist/android/nightfire-nicenano-ws2811-prepared.uf2` (compiled, not flashed)

Flutter analysis is clean. The 66 Flutter tests include fixed cue sizes at normal
and doubled text scale, transitions between cue states, hardware status decoding,
heartbeat failure, heartbeats during pending field queries, batch timeout recovery
and disconnect cancellation, dashboard search, cue filtering, phone/tablet
navigation, dashboard state across page changes, script parsing, hardware matching,
import cancellation/failure, group acknowledgement/readback and battery scaling.
Module bodies and Status/Settings controls align left. Expandable sections have
their own saved-state keys so scroll positions cannot overwrite expansion flags.
Nineteen Python
tests cover the interface state machine, debounce, session expiry, LED fault
handling, command allowlist, USB transport and RF keepalive scheduling/framing.

The connected radio passed a real three-second heartbeat expiry and recovery
test with both GPIOs disabled. Native WS2811 timing is compiled but not electrically
verified. See `docs/command_interface_wiring.md` before wiring or enabling GPIOs.
Local heartbeat monitoring does not yet implement remote disarm on timeout.
The 0.3.1 radio passed a live RF keepalive check: TX count advanced with zero
reported failures, remained unchanged after host timeout, and advanced again
after renewal. The tablet's Interface view also showed increasing RF counts
during discovery while the USB heartbeat stayed active. This verifies radio TX,
not per-module acknowledgement of the broadcast.
The live tablet test through the development relay discovered module `0xD96E30DD`
and retrieved its 24-cue measurement snapshot while heartbeats continued. Cue boxes measured
290 × 208 screen pixels both before and after measurement (145 × 104 logical
pixels at the tablet's 2× density).
Non-protocol radio console lines are retained in Flutter's debug log, and bridge
timeouts identify the command that did not reply.

### Validated v0.2 build

Artifacts at the repository root:

- `dist/android/nightfire-module-diagnostics-0.2.0-arm64-debug.apk`
- `dist/android/nightfire-module-diagnostics-0.2.0-x64-emulator-debug.apk`
- `dist/android/nightfire-module-diagnostics.png`

Flutter analysis is clean; 22 Flutter tests and eight Python transport/query-gate
tests pass. The live tablet test discovered `0xD96E30DD`, read field firmware
`0.2.0` and its 2S battery around 7.9 V, then displayed all 24 cues as open with
measurement status zero. A subsequent information request also succeeded with
the field console closed. No field-module firmware changes were needed.

Tests cover Python/Dart golden packets, CRC/escaping rejection, a complete
firmware command allowlist, duplicate discovery, nonce mismatches, wrong-source
replies, missing/rejected readings, legacy fallback, disconnect cancellation,
and 64-cue layouts on phone and tablet viewports.

`scripts/probe_usb_modules.py --port COM12` provides a PC-side discovery/info/
resistance/legacy-continuity diagnostic when the emulator has released the radio.
An unsupported legacy request may time out on current field firmware. The radio's
RSSI value is a driver-reported sample, not a validated per-packet range metric.

The native Android adapter uses the Android USB host API directly, without an
external USB driver package. The Dart `RadioTransport` interface is the extension
point for Windows/macOS serial adapters and any eventual iPhone accessory transport.
Keep platform code out of the packet codec and session logic.

## Build and test

Install Flutter stable matching the Dart SDK constraint in `pubspec.yaml`, an
Android SDK with API 36, and a compatible JDK. This checkout has workspace-local
Flutter/Android tools under the ignored `tools/` directory. Flutter's generated
Android project specifies its Gradle and Android plugin versions.

From this folder:

```powershell
flutter pub get
flutter analyze
flutter test
flutter build apk --debug --target-platform android-arm64
```

With this checkout's local tools, the equivalent from the repository root is:

```powershell
./scripts/build_android_app.ps1 -JdkPath 'C:/Program Files/Java/jdk-23' -Check
```

The debug APK is `build/app/outputs/flutter-apk/app-debug.apk`. It is a development
build for ARM64 Android devices, not a signed production release. Runner code for
Apple platforms must be built on macOS using Xcode; desktop adapters remain future work.

The first verified build is also copied to
`dist/android/nightfire-usb-monitor-0.1.0-arm64-debug.apk` at the repository root.
It requires Android 7.0/API 24 or newer and USB host support. APK signature
verification passed. Flutter analysis, nine Flutter tests, and three Python USB
transport tests passed. COM12 returned PONG, network `0x04D2`, firmware
`usb-monitor-0.1`, and channel `90`. No physical Android device was available for
an end-to-end Android USB test.

The Pixel Tablet emulator has subsequently completed an end-to-end USB test with
the real radio: USB permission, PONG, identity, statistics and Refresh status all
worked. This verifies the emulator path; a physical Android device remains untested.

## Windows tablet emulator with the real radio

### Current development connection

Windows USB passthrough completed earlier checks, but subsequently became
intermittent during repeated requests. The current tablet test uses a local
development relay. This tests the application and real radio/field protocol;
it does not validate Android USB permissions or physical-device USB behavior.

From the repository root, start the tablet without USB passthrough, then keep
the relay running in a separate terminal:

```powershell
./scripts/start_android_tablet.ps1
python scripts/emulator_serial_relay.py --port COM12
```

In the debug Android app's **Radio** view, enable **Local emulator relay**, select the relay and
connect. The relay listens only on `127.0.0.1:8765`; the Android emulator reaches
the host at `10.0.2.2:8765`. The screen labels this development connection.
Stop the relay before opening COM12 with another PC tool. For a physical Android
device, leave relay mode off and connect the radio directly over USB. The relay
selector is omitted from release builds.

### Native USB passthrough setup

The workspace AVD is `Nightfire_Tablet`, Android 16/API 36 x86-64, with a
2560 x 1600 tablet display. Its APK is copied to
`dist/android/nightfire-usb-monitor-0.1.0-x64-emulator-debug.apk`.

Google's bundled `Android USB Assistant` and `UsbAssist` drivers have been installed,
and the `UsbAssist` service is running. The installation transcript is
`dist/android/usb-driver-install.log`. To install them on another Windows setup,
run `scripts/install_android_usb_passthrough.ps1` as administrator.

Close any existing tablet emulator and serial terminals, then run from the root:

```powershell
./scripts/start_android_tablet.ps1 -UsbRadio
```

The launcher derives the radio's physical USB port from its serial number and
adds that port to `-usb-passthrough vendorid=0x239a,productid=0x00b3`, together
with a writable development system. This avoids also capturing a connected field
board with the same USB VID/PID. Override `-RadioSerial` for another radio board.
Windows relinquishes the radio while it is captured, so
COM12 is not available for PC tools at the same time. In Nightfire, select
**Scan USB**, **Connect**, and allow USB access if prompted.

The stock emulator image did not declare `android.hardware.usb.host`, even though
the radio appeared under `/sys/bus/usb/devices`. A development overlay was enabled
with `adb root` / `adb remount` and the required reboot, then
`scripts/android_usb_host_feature.xml` was installed as
`/vendor/etc/permissions/android.hardware.usb.host.xml`, with mode 644 and the
vendor file context restored. Another reboot activated the USB host manager.
These changes apply to the virtual tablet only. A recreated or reset system
overlay may need the feature declaration reapplied. Verify with
`adb -s emulator-5554 shell pm has-feature android.hardware.usb.host`.

The connected screen is saved as
`dist/android/nightfire-tablet-usb-connected.png`.

## Radio setup

The existing UART bridge exposes a MicroPython console on USB, not raw application
frames. The compact `main/nrf_usb_monitor_main.py` replaces that role for this
milestone. It accepts PING, GET_INFO, GET_STATS and restricted SEND_NFCP diagnostic
queries. Incoming response frames on the configured network are forwarded to the app.
It initializes the existing radio and polls received packets to collect statistics.
Counters and RSSI are not a test of field-module reachability.

From the repository root, using a Python installation with `pyserial`:

```powershell
python scripts/install_usb_bridge.py --port COM12
python scripts/probe_usb_bridge.py --port COM12
```

The installer saves existing target files under `dist/bridge_backup_<timestamp>`
before replacing them. File replacement is needed on this board's small internal
filesystem. Do not delete the backup. The original pre-installation COM12 firmware
from this session is in `dist/bridge_backup_20261102_231444`; later backup folders
contain subsequent monitor/partial-install states.

To restore that original UART role using the existing uploader:

```powershell
python scripts/raw_micropython_upload.py --port COM12 --delete main.py --delete bridge_radio.py --delete nrf_bridge_config.py --delete usb_bridge_transport.py dist/bridge_backup_20261102_231444/main.py:main.py dist/bridge_backup_20261102_231444/bridge_radio.py:bridge_radio.py dist/bridge_backup_20261102_231444/nrf_bridge_config.py:nrf_bridge_config.py
```

After installing the APK, move the radio's USB cable from the PC to the Android
device through a data-capable USB host connection. Choose **Scan USB**, select the
radio, **Connect**, and grant USB access. **Refresh status** obtains another
snapshot. The Android app-to-radio connection still needs testing on a physical
phone/tablet; a PC serial handshake does not validate Android USB behavior.

## USB envelope

USB carries one ASCII line per existing CRC-protected bridge frame:

```text
NFUSB1 <lowercase hex of complete AA55 bridge frame>\n
```

ASCII avoids MicroPython console control characters and newline rewriting while
retaining the existing sequence, CRC, command and response semantics. Receivers
discard console messages, malformed hex, invalid CRCs and oversized lines. The
payload ceiling remains 220 bytes. This is a versioned USB transport envelope;
the existing UART protocol remains binary and unchanged. Ctrl-C is still available
for maintenance through the MicroPython console.

For iPhone, Apple's External Accessory API targets MFi accessories. Do not assume
the Android CDC path can be reused on iOS; review the intended accessory design
before committing hardware. References:

- https://developer.apple.com/documentation/externalaccessory/
- https://developer.android.com/develop/connectivity/usb/host
- https://docs.flutter.dev/platform-integration/platform-channels
