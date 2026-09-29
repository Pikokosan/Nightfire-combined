# Nightfire Project Layout

This project is organized around the firmware that runs on each device, the tools
used to upload or test that firmware, and generated output packages.

## Main Firmware

- `main/master_main.py` - Touchscreen master controller firmware.
- `main/nrf_dummy_main.py` - nRF52840 dummy module firmware used for bench and RF testing.
- `main/field_main.py` - nRF52840 field module firmware.
- `main/nrf_bridge_main.py` - nRF52840 USB-to-radio bridge firmware.
- `main/nfcp.py` - Shared Nightfire command protocol.
- `main/bridge_radio.py` - Local UART framing between the master and bridge.
- `main/module_ota.py` - Shared module OTA transfer helper.

## Device Configs

- `main/nrf_node_config_*.py` - Per-device nRF module configs.
- `main/nrf_bridge_config_master.py` - Bridge config used by the master radio bridge.

## Hardware Helpers

- `main/field_measurement.py` - Field module continuity and measurement helpers.
- `main/adc_scaling.py` - ADC conversion helpers.
- `main/analog_mux74hc4051.py` - Analog mux helper.
- `main/shift_register74hc595.py` - Shift register helper.
- `main/lcd1602_i2c.py` - 2x16 I2C LCD driver.
- `main/adafruit_trellis.py` - Trellis button/LED helper for the master.

## Upload And Build Tools

- `scripts/upload_*.ps1` - Firmware upload tasks.
- `scripts/build_*.ps1` - Build and package tasks.
- `scripts/enter_*.py` - Bootloader entry helpers.
- `scripts/raw_micropython_upload.py` - Raw REPL upload utility used by the upload scripts.

## Tests And Diagnostics

- `scripts/test_*.py` - Repeatable protocol and hardware tests.
- `scripts/diagnostics/` - Short-lived or focused debug probes.

## Generated Or Local-Only Folders

These folders are ignored by Git and can be regenerated or are local working
copies:

- `build/`
- `dist/`
- `tools/`
- `hardware/`
- `.codex/`

Do not move active upload scripts without also updating `.vscode/tasks.json` and
the command examples in `README.md`.
