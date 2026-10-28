# Scripts

Run scripts from the repository root unless the script says otherwise.

## Upload

- `upload_master_to_com7.ps1` - Upload master firmware. Accepts `-Port`.
- `upload_field_module_to_com10.ps1` - Upload field module firmware. Accepts `-Port`, `-Pinout promicro|ms88sfa`, or a custom `-ConfigPath`.
- `upload_nrf_dummy.ps1` - Upload nRF dummy firmware. Accepts `-Port` and `-ConfigPath`.
- `upload_nrf_bridge.ps1` - Upload nRF bridge firmware. Accepts `-Port`.
- `upload_lcd_ui_test_to_com10.ps1` - Upload the standalone LCD/button UI test.
- `upload_field_hardware_test_to_com10.ps1` - Upload the standalone field hardware test; accepts the same field-module pinout selection.

## Build And Flash

- `build_module_ota_package.ps1` - Build SD-card/module OTA packages.
- `build_bridge_update_package.ps1` - Build the SD-card folder used by the master to update its attached bridge.
- `build_nrf52840_micropython.ps1` - Build the custom nRF52840 MicroPython image.
- `flash_nrf52840_uf2.ps1` - Copy a UF2 image to a mounted bootloader drive.

## Device Helpers

- `enter_nrf_bootloader.py` - Put a directly connected nRF board into bootloader mode.
- `enter_nrf_bridge_bootloader_via_master.py` - Ask the master to put its attached bridge into bootloader mode.
- `exec_micropython_code.py` - Run a short MicroPython snippet over raw REPL.
- `read_micropython_file.py` - Read a file from a MicroPython device.
- `raw_micropython_upload.py` - Shared raw REPL upload utility.

## Tests

- `test_master_*.py` - Master-side radio, module, range, and script tests.
- `test_master_script_timed_stress.py` - Upload a tight timed script to discovered modules, start it from the master, maintain heartbeat, and report module script status.
- `test_nrf_*.py` - nRF bridge/module radio tests.
- `test_nfcp_*.py` - Shared NFCP protocol tests.

## Diagnostics

- `diagnostics/` - One-off or focused debug probes kept out of the main tool list.
