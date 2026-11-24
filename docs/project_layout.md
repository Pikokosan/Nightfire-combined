# Project layout

Where every kind of file lives, and where new ones should go. For building and
programming devices, see `docs/building_devices.md`.

## `main/`: code that runs on the boards

A board stores its files in one flat folder, so `main/` is flat too. The
install scripts pick the files each device needs and give some of them their
on-board name (for example the entry point becomes `main.py`).

**USB bridge** (dongle for the Android app)
- `nrf_usb_monitor_main.py`: entry point (installed as `main.py`, or bundled
  into `usb_master_bundle.mpy` by `scripts/build/build_usb_master_bundle.py`).
- `usb_bridge_transport.py`: the `NFUSB1` USB line envelope.
- `usb_monitor_nfcp.py`: which module commands the bridge passes on.
- `nfcp_frame.py`: NFCP frame encoding and decoding, and the command
  numbers, for the bridge (`nfcp.py` is too big for it; a test keeps the two
  the same).
- `usb_master_control.py`: live ARM/FIRE/script control and the RF keepalive.
- `usb_master_maintenance.py`: radio maintenance (channel scan and so on).
- `command_interface.py`: SAFE/ARM switch and status LEDs.

**Field module**
- `field_main.py`: firmware; `field_boot.py` is the `main.py` that loads the
  compiled bundle.
- `field_measurement.py`, `adc_scaling.py`: continuity and battery measurement.
- `shift_register74hc595.py`, `analog_mux74hc4051.py`: output and mux drivers.
- `field_hardware_test.py`: stand-alone hardware test (no radio).

**Dummy module**
- `nrf_dummy_main.py`: firmware; `nrf_dummy_boot.py` is the `main.py` that
  loads it.

**Shared by the bridge and the modules**
- `nfcp.py`: the Nightfire command protocol.
- `mesh.py`: mesh radio layer (frame header, duplicate filter, send queue,
  bridge route table).
- `bridge_radio.py`: bridge frame format and CRC (USB and UART).
- `module_link.py`, `module_script.py`, `module_lcd.py`, `module_ota.py`,
  `nrf_module_common.py`: module radio link, script engine, LCD menu, file
  updates, common helpers.
- `lcd1602_i2c.py`: 16x2 I2C LCD driver.
- `lcd_button_ui_test.py`: stand-alone LCD and button test.

**ESP32 master** (older touchscreen controller, see `docs/esp32_master.md`)
- `master_main.py`, `nrf_bridge_main.py` (its UART radio bridge),
  `display_driver.py`, `ch422g.py`, `sdcard_driver.py`, `adafruit_trellis.py`,
  `trellis_hardware_test.py`.

`main/main.py` is a placeholder; each install script supplies the real one.

## `main/configs/`: device configs

Installed on a board as `nrf_node_config.py` (modules) or `nrf_bridge_config.py`
(bridges).

| File | Device |
|---|---|
| `bridge_usb.py` | USB bridge for the app; defaults for a new bridge |
| `bridge_esp32_master.py` | The ESP32 master's UART bridge |
| `field_ms88sfa.py` | Field module, MS88SFA PCB |
| `field_promicro.py` | Field module, Pro Micro nRF52840 test board |
| `field_supermini_bench.py` | Older SuperMini 18-channel bench fixture |
| `dummy_lcd.py` | Dummy with LCD and buttons |
| `dummy_plain.py` | Dummy without LCD |
| `lcd_ui_test.py` | LCD and button test app |

## `firmware/`

- `nrf52840/`: the Nightfire MicroPython image (`.uf2` and `.hex`) for every
  board, and `bootloader/` for the nice!nano UF2 bootloader.
- `micropython/`: source for rebuilding the image: the `NICENANO` board
  definition and the `nrf_radio` C module (radio, RSSI, LED driver). Built by
  `scripts/build/build_nrf52840_micropython.ps1`.

## `scripts/`

Run every script from the repository root. See `scripts/README.md` for the
list.

| Folder | Contents |
|---|---|
| `install/` | Put firmware on boards, change bridge LEDs |
| `build/` | Bundles, update packages, MicroPython image |
| `tests/` | Offline unit tests (`test_*.py`), no hardware |
| `bench/` | Tests that drive real boards |
| `diagnostics/` | Probes and one-off checks |
| `sim/` | Mesh simulator, firmware trace harnesses, PC module emulator |
| `android/` | App build, tablet emulator, icons |
| `lib/` | Helpers the other scripts import |

## `apps/nightfire_controller/`

The Android controller app (Flutter). See its `README.md`.

## `docs/`

| File | About |
|---|---|
| `building_devices.md` | Step-by-step build and programming guide |
| `bridge_status_leds.md` | Bridge LED types and settings |
| `command_interface_wiring.md` | Bridge switch and LED wiring, USB status protocol |
| `field_module_pinout.md`, `field_module_lcd.md` | Field-module pins, LCD and buttons |
| `mesh_radio_design.md` | Mesh radio design and test results |
| `audio_sync_design.md` | Audio box and timecode sync design (draft) |
| `android_command_module.md`, `android_master_port.md` | App design notes |
| `script_workspace.md`, `ui_backlog.md` | App scripting and UI plans |
| `esp32_master.md` | The older ESP32 touchscreen master |

## Not in git

`build/`, `dist/`, `tools/` and `hardware/` are local only. `dist/` holds
generated packages and the `bridge_backup_*` folders the bridge installers
write; delete old backups when you no longer need them.

## Where new files go

- New board code: `main/`. If a device needs it, add it to that device's
  install script in `scripts/install/`.
- A new kind of device config: `main/configs/`.
- A test that needs no hardware: `scripts/tests/test_<thing>.py`. One that
  needs boards: `scripts/bench/`.
- When you move a script, update `.vscode/tasks.json` and the docs that name it.
