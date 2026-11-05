# Android command module proposal

Status: USB hardwiring selected. The Flutter application and compact USB
monitor firmware are in `apps/nightfire_controller` and
`main/nrf_usb_monitor_main.py`. The radio handshake has been exercised on COM12.
Module discovery, information and continuity/resistance diagnostics are implemented.
The physical switch and 5 V WS2811 LEDs will connect directly to the nice!nano.
Switch telemetry, host-session monitoring and a native LED extension are prepared;
GPIOs remain disabled, wiring/electrical validation and actuation remain future work.
See `docs/command_interface_wiring.md` for proposed pins and the status protocol, and
`apps/nightfire_controller/README.md` for build, setup and platform limitations.

## System arrangement

```text
Android phone/tablet
    | USB host connection and versioned USB envelope
nRF52840 radio bridge: physical SAFE/ARM switch, 5 V WS2811 status LEDs, watchdog
    | existing proprietary 2.4 GHz radio and NFCP
Existing field modules
```

Android replaces the touchscreen master UI and application logic. The nice!nano
will read the physical switch and drive the LEDs. The existing radio
uses Nordic proprietary radio rather than Bluetooth, so the phone cannot
communicate directly with the field modules through its Bluetooth API.

The selected design integrates the interface into the nRF52840. USB diagnostics
have been validated; LED timing alongside radio reception, GPIO availability and
watchdog behavior still need implementation and hardware testing. The conventional
`nrf_bridge_main.py` profile uses UART bridge frames and USB CDC as its console.
The new `nrf_usb_monitor_main.py` profile carries application frames in an ASCII
USB envelope and accepts the diagnostic query allowlist. Firmware 0.3.2 also
permits unique SET_GROUP while the USB host session is current; other configuration
and actuation commands remain blocked. Per-module Settings waits for ACK and
discovery readback, and field firmware rejects group changes while armed.

Android USB host documentation:
https://developer.android.com/develop/connectivity/usb/host

Confirm USB host support, device permissions, serial interface compatibility,
and simultaneous charging requirements on the intended phone/tablet. A wireless
phone connection is a separate transport option requiring additional firmware
and testing; the current bridge does not expose a BLE service.

## Existing code to port

| Source | Android responsibility |
| --- | --- |
| `main/master_main.py` | Discovery, device database, cue UI, continuity/resistance, script management, settings |
| `main/nfcp.py` | NFCP encoding/decoding, escaping, CRC, addressing and response handling |
| `main/bridge_radio.py` | Serial framing, stream parsing, sequence matching and radio status |
| `main/nrf_bridge_main.py` | Retained radio implementation; extend or front with an interface controller |
| `main/field_main.py` | Compatibility reference for command acceptance, acknowledgements and timeouts |

Implementation: Flutter/Dart for shared UI, protocol and session logic, with a
native Kotlin Android USB adapter behind `RadioTransport`. iOS, Windows and macOS
runner projects are included, but their transports have not been implemented.
The Dart bridge codec has a golden packet test against the Python implementation.

The existing master sends script heartbeats every 200 ms and keepalives every
2500 ms. The field firmware stops a running script after 1500 ms without its
script heartbeat and disarms on master link timeout (default 15000 ms, configurable).
Script heartbeat loss stops the script; it does not itself disarm the module.
These are existing behaviors, not validated Android timing guarantees.

## Physical SAFE/ARM switch

The switch must be read and enforced by the interface firmware. An app toggle
alone cannot implement the physical interlock. ARM permits an explicit arm
request; moving the switch to ARM must not arm automatically.

On boot, disconnect, watchdog expiry, or switch SAFE, clear local arm permission
and invalidate pending actuation requests. While SAFE, block ARM, FIRE,
SCRIPT_START and SCRIPT_STEP, including any addressed/broadcast forms supported
by the protocol. Permit STOP and DISARM regardless of switch position. The gate
must parse complete, CRC-valid NFCP packets rather than inspecting arbitrary
serial bytes, and must not expose an alternate raw-send path that bypasses it.

On a transition to SAFE or a lost app session, prioritize STOP/DISARM messages
and stop forwarding actuation traffic. Reconnection requires an explicit new arm
action. A bridge cannot recall a radio packet already sent or guarantee that a
disarm reaches an unreachable module; retain field-side timeout enforcement and
show unconfirmed states clearly. Continued bridge-generated keepalives must not
mask the loss of the phone session.

The v0.3 local interface exposes heartbeat and versioned status commands reporting
switch position, session expiry, LED/configuration faults and rejected commands.
The app detects this firmware before enabling the heartbeat/status workflow.
Firmware 0.3.1 adds the master's 2500 ms RF keepalive, gated by the live USB
host session. Missing USB heartbeats stop RF keepalives within the 3000 ms
host timeout; field-side link timeout enforcement remains unchanged. RF transmit
counts/failures appear separately in Interface. Script heartbeats remain disabled.
The capability packet explicitly identifies this as a diagnostics-only stage;
it does not claim that a physical position is remote arm confirmation.

## WS2811 status indicators

Proposed four labeled indicators, driven from controller state:

| Indicator | Suggested display |
| --- | --- |
| APP | Blue when the app session is current; off when disconnected |
| RADIO | Green when expected module replies are current; amber when stale or partial |
| ARM | Green for confirmed safe; amber for permission/pending/unknown; red for confirmed armed |
| FAULT | Flashing red for watchdog, interlock or communication faults |

Labels and flash patterns supplement color. Physical ARM permission and remote
armed confirmation are separate states. For multiple modules, retain per-module
state in the app; any armed module prevents an aggregate SAFE indication, and
missing acknowledgements produce an unknown/partial state. A successful bridge
send is not confirmation that a field module changed state.

Select the exact WS2811 product before assigning power, data level conversion,
pinout, pixel order, timing, and current budget. LEDs require their specified
supply and a common ground with the controller. Treat LEDs as status displays,
not as the interlock itself. Validate that LED updates do not delay radio or
watchdog processing.

## Implementation sequence

1. Select USB or wireless transport, Android device, interface controller,
   physical switch and exact WS2811 assembly; then assign pins and power.
2. Build an Android connection/status application with a simulator and matching
   Python/Kotlin packet fixtures. Start with discovery and read-only telemetry.
3. Implement the controller switch gate, session watchdog, capability handshake
   and LED states. Exercise faults using dummy modules.
4. Port manual operation and script management, retaining module-side scheduling
   rather than using Android UI timers for individual timed cues.
5. Validate switch SAFE during a script, app termination, unplug/replug, USB
   permission denial, malformed/fragmented serial packets, stale responses,
   partial module replies, and controller/module resets with dummy loads.

The first APK should establish connection and report real bridge/module status.
A complete replacement also needs the master's script workflows, persistence,
calibration and update features, followed by bench and hardware validation.
