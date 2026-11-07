# Nightfire Project Layout

This project is organized around the firmware that runs on each device, the tools
used to upload or test that firmware, and generated output packages.

## Main Firmware

- `main/master_main.py` - Touchscreen master controller firmware.
- `main/nrf_dummy_main.py` - nRF52840 dummy module firmware used for bench and RF testing.
- `main/field_main.py` - nRF52840 field module firmware.
- `main/nrf_bridge_main.py` - nRF52840 USB-to-radio bridge firmware.
- `main/nfcp.py` - Shared Nightfire command protocol.
- `main/bridge_radio.py` - Local UART/USB framing between the master or app and the bridge; table-driven CRC16 and a bulk frame parser (`feed_bytes`, tested by `scripts/test_bridge_frame_parser.py`).
- `main/module_ota.py` - Shared module OTA transfer helper.
- `main/mesh.py` - Mesh radio layer v1 shared by modules and both bridges: frame header, duplicate cache, non-blocking send queue, and the bridge-side `MeshBridge`.
- `main/module_link.py` - Shared module radio link: NFCP frames over mesh frames, relaying, duplicate suppression, replies, and the ping/info/settings/OTA commands.
- `main/module_script.py` - Shared module script engine: time sync, upload, start/stop/heartbeat, step mode and scheduler.
- `main/module_lcd.py` - Shared module 16x2 LCD menu and debounced buttons.
- `scripts/mesh_sim.py` + `scripts/mesh_scenarios.py` - Multi-node radio simulator: runs the real bridge, field and dummy firmware on a shared virtual radio and clock to test mesh behaviour (coverage, latency, collisions, relay failure) on a PC.
- `scripts/diagnostics/mesh_cpu_benchmark.py` - Times the real receive path on a module over the raw REPL and fits the simulator's CPU model (re-run after changing the receive path or MicroPython build).
- `scripts/diagnostics/mesh_hw_smoke.py` - Hardware smoke test for mesh firmware: drives the USB bridge like the app (no ARM/FIRE) and checks a field module answers over the mesh.
- `scripts/firmware_trace_harness.py` - Runs the field or dummy firmware on a PC against fake hardware and writes a behaviour trace; compare traces before and after a firmware change.

## Design Notes

- `docs/mesh_radio_design.md` - Draft design for the 2-hop self-healing mesh radio layer (branch `Mesh`).

## Device Configs

- `main/nrf_node_config_*.py` - Per-device nRF module configs.
- `main/nrf_bridge_config_master.py` - Bridge config used by the master radio bridge.

## Hardware Helpers

- `main/field_measurement.py` - Field module continuity and measurement helpers.
- `main/adc_scaling.py` - ADC conversion helpers.
- `main/analog_mux74hc4051.py` - Analog mux helper.
- `main/shift_register74hc595.py` - Shift register helper.
- `main/lcd1602_i2c.py` - 2x16 I2C LCD driver; writes only changed characters, one I2C transaction per row, so drawing barely interrupts the radio (tested by `scripts/test_lcd1602_driver.py`).
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
