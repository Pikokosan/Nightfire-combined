# Android master controller 0.4

The Android implementation extends `apps/nightfire_controller`, rather than
trying to run ESP32 MicroPython hardware modules on Android. The original
`main/master_main.py` remains unchanged by this port. Android USB host transport
replaces ESP32 UART, Flutter replaces LVGL, and native file selection replaces
the SD-card browser. The existing diagnostics and offline demonstrations remain.

## Feature mapping

| Original master feature | Android implementation |
| --- | --- |
| Discovery, module information, battery and continuity | Existing Modules dashboard, USB/RF query correlation and resistance fallback |
| Manual ARM/DISARM and FIRE | Modules → Master controller; unique module ACK required, cues 1–64 |
| Global STOP / DISARM | Modules and Scripts; three STOP/DISARM broadcast pairs; broadcast acceptance does not claim remote confirmation |
| Group settings | Module Settings with ACK and discovery readback |
| Module unique ID, network, channel | Master controller → Module diagnostics and maintenance; ACK then rediscovery required |
| Speed/range tests | One or 20 nonce-bearing pings, received count and mean round-trip time |
| Raw ADC and bank calibration | Module maintenance; explicit ADC mV/deci-ohm pairs, up to eight per bank |
| Script import and hardware matching | Existing COBRA CSV workspace and continuity findings |
| Script clear/load | Three entries per chunk, up to 192 combined events per field module; ACK and INFO ID/count readback |
| Timed script execution | Shared absolute master start, clock synchronization, offset selection and 200 ms heartbeats |
| Step execution | Unique SCRIPT_STEP per module with that timestamp; each event consumed before transmission |
| Module OTA files and reboot | Native file picker, original additive checksum, acknowledged offset chunks and abort on failure; reboot is separate |
| Local bridge network/channel, channel scan, counters/reset | Master controller → Radio settings and maintenance |
| Local bridge file updates | Native picker, CRC16, offset checking, temporary file and `.bak` replacement |
| ESP32 LCD/SD/Trellis hardware | Android touchscreen and file picker; direct ESP32 I2C/GPIO/Trellis control is not available through the USB radio |

This is a functional port, not a pixel-identical LVGL reconstruction. Range tests
report aggregate results rather than the ESP32 history plot. ARM state refers to
ACKs in the current USB session; the protocol does not provide a continuously
authenticated arm-state stream. A changed module address/network/channel must be
rediscovered at its new settings. No ARM or FIRE is automatically retried.

## Firmware mode

Live controls require the exact `usb-master-0.4.0` bridge identity. Existing
`usb-monitor-0.2`/`0.3` firmware continues to support its current diagnostic path,
and the normal bridge installer still installs monitor mode by default.

To explicitly install master mode, with Python 3 and pyserial:

```powershell
python scripts/install/install_usb_bridge.py --port COM12 --master
```

The connected nice!nano has a 64 KiB filesystem. Its verified deployment uses
the compact form instead, with the already-installed MicroPython 1.29 `mpy_cross`
compiler matching its MicroPython 1.29 preview firmware:

```powershell
python scripts/install/install_usb_bridge.py --port COM12 --master --compiled
python scripts/diagnostics/probe_usb_master_status.py --port COM12
```

`build_usb_master_bundle.py` combines the owned modules while preserving the
`bridge_radio` namespace, compiles a 13,845-byte `.mpy` image, and uses a small
`main.py` import stub. The installer backs up all source and compiled destinations
before reclaiming those owned files; unrelated files remain. Match the compiler
to the target MicroPython before using this option on a different radio. For this
compiled deployment, replace the bundle through this installer when updating
bridge code; individual source-module updates in the APK do not alter code
already included in the compiled bundle. The APK itself is unchanged.

The installer backs up every existing destination to a new
`dist/bridge_backup_<timestamp>` folder before replacing files. It preserves the
radio's existing configuration and appends `MASTER_ENABLED = True`. The two new
policy/maintenance modules are included, every file is read back, and failures
restore the backup. The installer prints the exact backup folder; retain it for
restoration using `scripts/lib/raw_micropython_upload.py`.

ARM, FIRE, script START and STEP require an active three-second host session. If
an ARM switch is configured, its debounced position must be ARM. Software arming
is available when the existing config has no physical switch. Maintenance is
inhibited after an ARM request until disarm acknowledgement or watchdog recovery.
Host expiry, radio failure, a configured switch moving away from ARM, or a host
session takeover schedules repeated RF STOP/DISARM pairs. A failed RF send remains
pending. This is a best-effort radio stop, not proof that field outputs changed.

The app clears acknowledged arm state and script preparation on disconnect or
heartbeat loss. Backgrounding disconnects the USB session, apart from the existing
file-picker lifecycle exemption. The STOP action interrupts pending queries and
uploads; it remains available when normal controls are busy. Resume through an
explicit per-module DISARM or a fresh connection before rearming.

Script LOAD is deliberately not retried: the current field firmware appends each
chunk and does not deduplicate its chunk index. After any uncertain chunk, clear
and upload the entire script again. The current SCRIPT_STEP protocol provides no
field acknowledgement; the UI labels it as transmitted and never replays an
uncertain event. OTA chunks are 80 bytes so even all-`FF` data fits the escaped
218-byte NFCP bridge limit. Bridge update chunks retain the original 48-byte size.

## Build

```powershell
./scripts/android/build_android_app.ps1 -JdkPath 'C:/Program Files/Java/jdk-23' -Check
./scripts/android/build_android_app.ps1 -JdkPath 'C:/Program Files/Java/jdk-23' -TargetPlatform android-x64
```

The helper resolves dependencies from the existing workspace cache offline and
uses workspace-local Flutter telemetry and Gradle caches. The Android SDK remains
under `tools/android-sdk`. Output is
`apps/nightfire_controller/build/app/outputs/flutter-apk/app-debug.apk`.
These are development APKs with a debug signing key, not Play Store releases.

## Evidence and validation

Source authority: `master_main.py` SHA256
`e0a293572d7249fa31c4bd73ee32b910229d3007580a71654e58db38f2ce00f5`,
and `nfcp.py` SHA256
`3e97bcb4f09f4fc8f84d508ba9cc9721dadd863e2d95c180907134a886a2ca3d`.
REA does not provide a Python-to-Android conversion engine; the available Python
source is the authority for the port and generated wire fixtures.

REA inspected the existing ARM64 APK as an archive. Retained parent Evidence:
`ev_f47fe5f2c98439b91f1e2475db3325263f61745bc88c2776af8b0cca0741cf9b`.
Its SHA256 is `5cc4ddc6e601761eea3957505ce3ef376524e0ed39863a83828aee64a9af14e3`.
The inventory contains 147 entries and no integrity contradictions; this establishes
package contents, not runtime behavior or Python feature equivalence. REA device
inventory found no attached Android devices (Evidence
`ev_5f8df98149f22c27613749b735d2f7123f669d573a2af2231ec7f24f40623a11`).

The final development APK is `dist/android/nightfire-master-0.4.0-arm64-debug.apk`
(77,489,032 bytes), SHA256
`00542358f529f9d9bcece4175a0248c94cf27d9e21187eb9cf464853171ab452`.
APK v2 signature verification passed. Package ID is
`com.nightfire.nightfire_controller`, version `0.4.0`/code 4, minimum Android
API 24, target API 36, ARM64 only. REA inspected all 147 archive entries with no
integrity contradictions; parent Evidence:
`ev_65d4b4a770d8aad994ae4dcaae07b2b6ad0c2f0434fed1c2fd439cab3ba8aa56`.
Flutter analysis is clean; all 108 Flutter tests and 57 Python tests passed.
The build/test transcript is `dist/android/master-build.log`.

The original NFCP builders generate 21 golden frame fixtures, including cue 64,
all 64 cue bits, escaped bytes, scripts, settings and OTA. Regenerate with
`python scripts/android/export_master_android_goldens.py`. Tests cover firmware command
allowlists, CRC and malformed-frame rejection, interlock and session gating,
watchdog retries, disarm ACK handling, offset/CRC file validation, matching arm
ACKs, fault NACKs, cancellation, uncertain uploads, no repeated FIRE/LOAD, script
timing/stepping, OTA abort and worst-case escaping, and phone/tablet text scaling.

The attached COM12 radio replied with `usb-monitor-0.3.2`, network 1234 and channel
90. RF discovery returned module `0xD96E30DD`, 24 cues, firmware `0.2.0`. Its INFO
response reported `fault=595 chain no q7s`; resistance returned NACK `24 04` and
legacy continuity timed out. This is a field-side startup fault observed before
master firmware installation. No hardware ARM, FIRE or script execution was
performed. Do not interpret the unit tests as physical output validation.

## Completed COM12 master installation

The first source installation hit `OSError: 28` (filesystem full) and automatically
restored the monitor. PONG and `usb-monitor-0.3.2` were verified after rollback.
That original backup is `dist/bridge_backup_20261104_182725`.

The compact installation then succeeded with complete byte-for-byte readback.
Its pre-install backup is `dist/bridge_backup_20261104_183039`; it contains the
original monitor files and configuration. Keep both backup folders. Current
firmware is `usb-master-0.4.0`, network 1234, channel 90, with the existing switch
and LED configuration preserved (both unconfigured).

`scripts/diagnostics/probe_usb_master_status.py` passed PONG/identity/status, four discovery
rounds, module INFO, RF keepalive telemetry, three-second host-session expiry,
and heartbeat recovery. Field module `0xD96E30DD` replied as group 1 with 24 cues,
firmware `0.2.0`, battery 7805 mV/2S. Its latest INFO script status was
`id=-- entries=0 run=0 next=0 hb=-1 idle`, without the earlier startup-fault text.
Measurements and physical outputs were not tested in this installation check.
No ARM, FIRE, script execution or settings writes were sent by the probe.

Transcripts: `dist/android/master-firmware-compiled-install.log` and
`dist/android/master-live-status-discovery.log`. The bundle namespace/watchdog
regression test brings the Python test count to 58; the APK's 108 Flutter tests
and signature remain unchanged.
