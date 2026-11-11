# Changelog

What changed, newest first, grouped by day and by part of the system. Each
entry names the commit it landed in. Add a line with every commit; put
work that isn't committed yet under **Unreleased**.

Parts: **Bridge** (USB radio dongle, `usb-master-*`), **Field** (field
module firmware), **Dummy** (`nrf-dummy`), **Mesh** (radio layer shared by
all of them), **App** (Android controller), **Tools** (scripts, simulator,
installers), **Hardware**.

## Current versions

| Part | Version | Note |
|---|---|---|
| Bridge | usb-master-0.4.3 | ARM key safety latch. Bundle 24,654 bytes. |
| Field | field-module 0.3.0 | |
| Dummy | nrf-dummy 0.2.0 | |
| App | 0.4.0 (build 4) | |

## Unreleased

## 2026-11-10

### Field — `PENDING`
- **Changed:** buttons are read active high with no internal pull-up: on
  the production boards they pass through an inverting 74HC14
  (`BUTTON_ACTIVE_VALUE`, `BUTTON_INTERNAL_PULL_UP` in both field configs).
- **Added:** arm voltage check settings in both field configs (enabled,
  50 ms settle, 500 mV minimum, 16 samples).
- **Changed:** a cue counts as shorted only at 0.0 ohm
  (`RESISTANCE_SHORT_MAX_DECI_OHM = 0`, field configs and ESP32 master).
- **Changed:** `upload_field_module.ps1` compiles with `mpy-cross -O3`,
  installs the node config as `nrf_node_config.mpy`, and removes a leftover
  `field_hardware_test.py`.

### ESP32 master — `PENDING`
- **Added:** the manual fire grid shows every cue of the largest module and
  scrolls; the Trellis keypad and its LEDs follow the visible 16 cues.
- **Changed:** manual fire debounce is per module and cue. Trellis presses
  rely on the key scanner's release-gated debounce, so quick presses on
  different keys are no longer dropped; LVGL and LED updates stay out of
  the key-read path.
- **Changed:** the module database is kept in memory only, and the old
  `nfcp_devices.json` is deleted at boot.
- **Changed:** the continuity page shows NO COMM for a module that has
  gone offline instead of its last readings.

### Tools — `PENDING`
- **Added:** `main/trellis_hardware_test.py` and
  `scripts/install/upload_trellis_test.ps1` (standalone 4x4 Trellis
  keypad/LED test); `scripts/diagnostics/capture_field_console.py` and
  `probe_radio_io_api.py`; offline tests for the manual fire viewport,
  offline cues and the in-memory device database; more field resistance
  tests (one, the armed scan, still fails: `script` isn't set up in the
  test namespace).

### Docs — `PENDING`
- **Changed:** field LCD and pinout docs describe the 74HC14 button inputs;
  UI backlog notes the battery settings that are done; removed a reference
  to an example script file that doesn't exist.

### App — `506fea1`
- **Changed:** the bar at the top shows the whole system's arm state instead
  of the app's arm acknowledgements: SYSTEM SAFE (green), COMMAND MODULE ARMED
  (orange, key on ARM), SYSTEM ARMED • n modules (red, key on ARM and modules
  confirmed ARM), or ARM STATE UNKNOWN. Errors take over the bar with a
  reason underneath: ARM KEY ERROR (key on at start-up), COMMAND MODULE FAULT
  (switch fault, radio unavailable, RF heartbeat failing, app heartbeat
  expired), ARM KEY UNKNOWN, MODULES ARMED, KEY SAFE, STOPPING / STOPPED, and
  the last arm/fire operation that failed. Checked SAFE and COMMAND MODULE
  ARMED on the emulator with the bench bridge.

### Bridge usb-master-0.4.3 — `552617a`
- **Added:** ARM key safety latch. If the key is on ARM when the bridge
  powers up or when the app connects, the bridge refuses ARM/FIRE/START/STEP
  until the key has been turned to SAFE. Pixel 1 flashes yellow meanwhile,
  and status fault bit 32 reports it (the red fault pixel ignores this bit).
  A reconnect with a new session tag counts as a new connection, even within
  the 3 s timeout. Checked on the COM12 bench bridge: power-on on ARM
  (yellow, ARM refused), key to SAFE then ARM (ARM accepted), quick
  reconnect on ARM (latched again).

### Tools — `552617a`
- **Added:** `scripts/diagnostics/probe_arm_key_latch.py`: watch the latch and
  test whether the bridge accepts ARM (to a UID that doesn't exist).

### App — `552617a`
- **Added:** "Turn the ARM key off" popup while the latch is set; Arm buttons
  are disabled and arming refuses with the reason. It closes by itself when
  the key reaches SAFE, and can be hidden (arming stays blocked). The app
  also latches from its own first status, covering older bridge firmware.

## 2026-11-09

### Bridge — `52d2964`
- **Changed:** the COM12 bench bridge's LEDs are now WS2811s. They showed all
  white with the WS2812B settings (250 ns T0H read as 1). Now RGB order and
  timing (150, 1100, 550, 700); the test pattern was correct. The repository
  default (`main/configs/bridge_usb.py`) is the same.

### Tools — `52d2964`
- **Added:** `scripts/install/set_bridge_leds.py`: shows a bridge's LED
  settings, lights a red/green/blue/white test pattern with chosen colour
  order and timing, and saves them on the bridge. Presets for WS2812B,
  WS2811 (800 kHz and 400 kHz) and SK6812.
- **Changed:** `flash_nrf52840_uf2.ps1` now flashes the repository's prebuilt
  image by default and finds the bootloader drive by itself.
- **Changed:** files reorganised (no firmware behaviour change; offline tests,
  simulator runs and firmware traces are identical before and after):
  - `scripts/` split into `install/`, `build/`, `tests/` (offline),
    `bench/` (real hardware), `diagnostics/`, `sim/`, `android/`, `lib/`;
  - upload scripts lost their COM numbers: `upload_field_module.ps1`,
    `upload_esp32_master.ps1`, `upload_esp32_master_bridge.ps1` (was
    `upload_nrf_bridge.ps1`), `upload_lcd_ui_test.ps1`,
    `upload_field_hardware_test.ps1`;
  - device configs moved to `main/configs/` with plain names
    (`bridge_usb.py`, `field_ms88sfa.py`, `dummy_lcd.py`...);
  - `nrf52840/` (MicroPython board and `nrf_radio` source) moved to
    `firmware/micropython/`;
  - removed: unused `nrf_node_config_com15*.py`, `1v8broken.hex`.

### Docs — `52d2964`
- **Added:** `docs/building_devices.md`, step-by-step build and programming
  guide for the bridge, field module, dummy and app.
- **Added:** `docs/bridge_status_leds.md`: LED settings, presets, and how to
  fix all-white or wrong-colour LEDs.
- **Changed:** README rewritten as an index; ESP32 master material moved to
  `docs/esp32_master.md`, field LCD material to `docs/field_module_lcd.md`;
  `docs/project_layout.md` and `scripts/README.md` rewritten.

## 2026-11-08

### Mesh — `072b095`
- **Fixed:** a far module flipped between a relay and a marginal direct
  link. The route that delivered now sticks until a delivery on it fails.
  A direct copy at -60 dBm or stronger drops a relay preference.
- **Fixed:** deliveries reported the path of the first reply copy, so a
  module sent through a relay often showed "Direct". They now report the
  route the bridge chose.
- **Fixed:** a module heard directly at -81 to -88 dBm was sent DIRECT,
  which stops relays forwarding it. "Strong enough for direct" is now
  -80 dBm, and a weak link with no known relay floods.
- **Changed:** retries go to a flood after one failed attempt (was: the
  same route twice, then a flood).
- **Added:** `MESH_BENCH_DEAF_TO`, a bench-only setting that makes a node
  ignore copies sent straight from listed nodes, to force a relay on a
  desk.
- Checked on the tablet: 73 of 73 deliveries to the far dummy went through
  the field module on the first attempt.

### Bridge — `072b095`
- **Added:** SAFE/ARM switch on P0.06, four WS2812B status pixels on P0.08
  and LED power on P0.13, on the COM12 bench bridge.
- **Fixed:** LEDs all white: 400 ns T0H read as all ones on these
  WS2812Bs; 250 ns decodes. Colour order is GRB.
- **Fixed:** LEDs dark: the power pin is now driven high, and only when the
  LED settings are valid.

### Dummy 0.2.0 — `072b095`
- **Added:** simulated resistance scans (0x24) and output status (0x27), so
  the app's automatic continuity checks no longer mark dummies red.

### App — `cacc1ba`
- **Added:** experimental Mesh page: topology of how each module was last
  reached, with per-module route counts.
- **Added:** automatic continuity checks after discovery (one random module
  per heartbeat while not armed), with a reply LED that flashes green and
  stays red on no reply. Can be turned off in Settings.
- **Added:** RSSI next to each module's battery.
- **Added:** Settings tab with per-firmware battery settings; script
  runner; new app icons.
- **Fixed:** the group number field in a module's Settings crashed
  (PageStorage key collision).
- **Changed:** modules start collapsed. Module diagnostics moved into each
  module's Settings and radio maintenance onto the Radio page. The Master
  controller tile is gone; its live controls are on Fire and Scripts.

### Tools — `072b095`
- **Fixed:** `install_usb_bridge.py --master` gave a bridge with no
  switch/LED settings none at all; it now takes them from the repository
  config.
- **Added:** `scripts/tests/test_mesh_routes.py` (route choice unit tests).

## 2026-11-07

### Mesh — `e5fac00`
- **Added:** mesh v1, built in phases (see `docs/mesh_radio_design.md`):
  - multi-node simulator running the real firmware (`scripts/sim/mesh_sim.py`);
  - shared `main/mesh.py`: mesh header, duplicate filter, flooding relays
    up to 2 hops, non-blocking send queue, new radio address;
  - RSSI-weighted relay wait with suppression, and an age field so relayed
    time code stays accurate;
  - reliable unicast: bridge route cache, `CMD_SEND_NFCP_RELIABLE` (0x1A),
    and a module reply cache so retries never run a command twice.
- **Fixed:** RSSI read the noise floor (about -100 dBm); it is now sampled
  during the packet.

### Bridge — `e5fac00`
- **Changed:** faster USB frame handling (command hold time 42.5 ms to
  12.5 ms).

### Field / Dummy — `e5fac00`
- **Fixed:** the LCD blocked the radio; it now writes only changed
  characters, one I2C transaction per row.

## 2026-11-06

### Field / Dummy — `a1d2523`
- **Changed:** radio link, script engine and LCD menu shared between the
  two firmwares (`module_link.py`, `module_script.py`, `module_lcd.py`).
- **Field 0.3.0:** arm voltage check, button polarity, short threshold.
- **Added:** `scripts/sim/firmware_trace_harness.py` to run either firmware on
  a PC.

## 2026-11-05

### Bridge 0.4.1, App — `e41f6d9`
- **Added:** Firmware tab: bridge updates over USB, field updates over RF.
- **Added:** a new bridge bundle boots on trial and is restored from `.bak`
  unless an app session confirms it.
- **Fixed:** Android's file picker renamed `.mpy` to `.bin`.

### Hardware — `9aff6dd`
- **Changed:** nice!nano filesystem grown to 512 KiB. Reflashing moves the
  filesystem, so module files must be reinstalled.

## 2026-11-04

### App, Bridge — `5f57fe0`
- **Added:** Android controller app and the nice!nano USB master bridge.
- **Fixed:** Android USB reads dropped replies on the Galaxy Tab A9+;
  they now use a queued UsbRequest.
- **Fixed:** expansion tiles read the page scroll offset as their state.
- **Fixed:** after a STOP and reconnect the RF keepalive stayed off until
  something was re-armed.

## Earlier history

Summarised from commit messages; see `git log` for details.

- **2026-10:** field resistance calibration and persistence (`912bb61`);
  master script upload and module card layout fixes (`7ad2a60`).
- **2026-09:** nRF support: channel scan, FICR unique IDs, persisted
  bridge channel, battery telemetry, no-ack script step firing, more
  stable script upload chunks, master bridge update flow, nRF discovery
  and LCD work. Legacy E77 and RP2040 code removed (`9e309dc`).
- **2026-06:** wireless E77 modules: known-good script upload, MC33996
  continuity fixes, unique-ACK arm flow, OTA updates, script fire timing.
- **2026-05:** NFCP upload tooling, wired RS485 version archived, first
  wireless E77 transport and network pairing.
