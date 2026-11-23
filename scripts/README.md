# Scripts

Run every script from the repository root, for example
`py -3 scripts/install/install_usb_bridge.py --port COM12`. Every upload
script takes `-Port` (PowerShell) or `--port` (Python).

## `install/`: put firmware on boards

- `flash_nrf52840_uf2.ps1`: copy the Nightfire MicroPython image to a board in
  UF2 bootloader mode (finds the drive by itself).
- `enter_nrf_bootloader.py`: switch a running board into the UF2 bootloader.
- `install_usb_bridge.py`: install the USB bridge (`--master --compiled` for
  the app's bridge).
- `set_bridge_leds.py`: test and change a bridge's LED type, colour order and
  timing (`docs/bridge_status_leds.md`).
- `upload_field_module.ps1`: field module (`-Pinout ms88sfa|promicro`, or
  `-ConfigPath`).
- `upload_nrf_dummy.ps1`: dummy module (`-ConfigPath`).
- `upload_field_hardware_test.ps1`, `upload_lcd_ui_test.ps1`,
  `upload_trellis_test.ps1`: stand-alone hardware tests.
- `upload_esp32_master.ps1`, `upload_esp32_master_bridge.ps1`: the older
  ESP32 master and its UART bridge.
- `enter_nrf_bridge_bootloader_via_master.py`: put the ESP32 master's bridge
  into bootloader mode.
- `update_field_firmware_over_usb_master.py`: replace one file on a field
  module over the radio, through the USB bridge.

## `build/`

- `build_usb_master_bundle.py`: compiled bridge firmware for app updates
  (`dist/android/usb-master-bundle/`).
- `build_field_mpy_bundle.py`: the single-file field firmware (used by
  `upload_field_module.ps1`).
- `build_nrf52840_micropython.ps1`: rebuild the MicroPython image from
  `firmware/micropython/`.
- `build_module_ota_package.ps1`, `build_bridge_update_package.ps1`: SD-card
  update packages for the ESP32 master.

## `tests/`: offline, no hardware

```powershell
Get-ChildItem scripts\tests\test_*.py | ForEach-Object { py -3 $_.FullName }
```

## `bench/`: tests that drive real boards

`test_master_*` need the ESP32 master (`--master COMx`); `test_nrf_*` drive
nRF boards directly.

## `diagnostics/`

Probes and focused checks. The everyday one is `mesh_hw_smoke.py`
(discover and ping modules through a USB bridge, without arming).
`reliable_ping_load.py` A/B tests two bridge firmwares: retries and round
trips of reliable pings to one module.
`micropython_*.py`, `probe_radio_io_api.py` and `read_field_resistance_points.py`
run on a board, not on the PC.

## `sim/`

- `mesh_sim.py` (+ `mesh_scenarios.py`): multi-node radio simulator running
  the real firmware. `--list` shows the scenarios.
- `firmware_trace_harness.py`: run the field or dummy firmware on the PC and
  write a trace to compare before and after a change.
- `bridge_trace_harness.py`: the same for the USB bridge (`--mode master` or
  `monitor`): every USB command, sessions, arming, pairing and maintenance.
- `PC_multi_module_emulator.py`: fake modules on a serial port.

## `android/`

App build (`build_android_app.ps1`), tablet emulator and USB passthrough,
the emulator serial relay, app icons.

## `lib/`: shared helpers

Imported by the other scripts; most also run on their own.

- `raw_micropython_upload.py`: raw-REPL file upload used by every installer.
- `exec_micropython_code.py`, `read_micropython_file.py`,
  `direct_exec_micropython_code.py`: run code on or read a file from a board.
- `probe_usb_bridge.py`, `probe_usb_modules.py`: talk to a USB bridge
  (ping, info, discover).
