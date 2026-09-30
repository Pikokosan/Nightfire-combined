# Nightfire Combined

Nightfire Combined contains the MicroPython firmware and support scripts for the
NFCP master controller, nRF52840 field modules, nRF52840 dummy modules, and the
nRF52840 radio bridge/prototype modules.

## Project Layout

See `docs/project_layout.md` for the fuller folder map and where new files
should go.

- `main/master_main.py` - Waveshare ESP32-S3-LCD-4.3 master controller GUI.
- `main/field_main.py` - nRF52840 field module firmware for the 18-channel 74HC595/74HC4051 prototype.
- `main/nrf_dummy_main.py` - nRF52840 dummy module firmware.
- `main/nrf_bridge_main.py` - nRF52840 USB/UART bridge firmware for the master.
- `main/bridge_radio.py` - local UART framing protocol used by the master and nRF bridge.
- `main/nfcp.py` - shared NFCP packet protocol.
- `main/module_ota.py` - module-side OTA file receiver.
- `main/shift_register74hc595.py` - 24-bit output-bank driver for three 74HC595 devices.
- `main/analog_mux74hc4051.py` - 18-channel scanner for three 74HC4051 analog multiplexers.
- `main/lcd1602_i2c.py` - optional 2x16 HD44780/PCF8574 I2C LCD driver for field-module status screens.
- `main/adc_scaling.py` and `main/field_measurement.py` - ADC scaling, battery/channel measurement, calibration, and continuity classification.

## Default Ports

- Master device: `COM7` or current test master `COM21`
- Field module: `COM10`
- nRF dummy device: `COM16`
- nRF bridge: usually `COM15` or `COM22`

Each upload script accepts a `-Port` override.

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\upload_master_to_com7.ps1 -Port COM21
powershell -ExecutionPolicy Bypass -File .\scripts\upload_field_module_to_com10.ps1 -Port COM10
powershell -ExecutionPolicy Bypass -File .\scripts\upload_lcd_ui_test_to_com10.ps1 -Port COM10
powershell -ExecutionPolicy Bypass -File .\scripts\upload_nrf_bridge.ps1 -Port COM15
powershell -ExecutionPolicy Bypass -File .\scripts\upload_nrf_dummy.ps1 -Port COM16
```

To replace the field firmware with the standalone 18-channel hardware test app:

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\upload_field_hardware_test_to_com10.ps1 -Port COM10
```

The hardware test app targets the SuperMini NRF52840 pinout in
`docs/field_module_pinout.md`, runs no Nightfire radio/protocol code, keeps
74HC595 `/OE` disabled by default, drives `P0.13` high to power the 74HC4051s
from the board `VCC` pin, repeatedly scans all 18 muxed ADC channels, and only
walks the outputs if `ENABLE_OUTPUT_WALK = True` is supplied in
`nrf_node_config.py`.

VS Code tasks are provided for the common uploads:

- `Master device: upload`
- `Field module: upload`
- `LCD UI test: upload`
- `nRF52840 dummy: upload`
- `nRF dummy: USB bootloader`

## nRF52840 Radio

The current wireless path uses nRF52840 boards running MicroPython with the
custom `nrf_radio` native module. BLE is not used.

The bridge speaks the local UART protocol in `bridge_radio.py`:

- `PING` / `PONG`
- `GET_INFO`
- `GET_STATS` / `CLEAR_STATS`
- `SET_NETWORK`
- `SET_CHANNEL`
- `SCAN_CHANNELS`
- `SEND_NFCP`
- `NFCP_RX`
- `BOOTLOADER`
- `RESET`

Over the air, bridge and modules wrap NFCP frames as:

```text
network_id(2 bytes) | sequence(1 byte) | nfcp_frame
```

The master stores its selected network ID and radio channel in
`nfcp_settings.json`. Modules store their paired network/channel in
`nfcp_cfg.bin`.

### Master Bridge Pinout

Default bridge UART settings:

- Baud: `256000`
- nice!nano UART: `UART0`
- nice!nano TX: `P6`
- nice!nano RX: `P8`
- Default radio channel: `90`
- Default network ID: `0x04D2`

Wire the bridge UART crossed to the master:

```text
Waveshare master 3V3  -> nice!nano VCC/3V3
Waveshare master GND  -> nice!nano GND
Waveshare master TX15 -> nice!nano P8 / RX
Waveshare master RX16 -> nice!nano P6 / TX
```

Do not connect 5 V logic to the nice!nano UART pins.

## nRF52840 MicroPython Build

Build the custom nRF52840 MicroPython firmware with:

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\build_nrf52840_micropython.ps1
```

The build script uses these optional environment variables:

- `MICROPYTHON_ROOT`
- `ARM_TOOLCHAIN_DIR`

Output artifacts are copied to:

- `firmware/nrf52840/nightfire_nicenano_micropython.uf2`
- `firmware/nrf52840/nightfire_nicenano_micropython.hex`

To enter UF2 bootloader from a running nRF MicroPython board:

```powershell
python .\scripts\enter_nrf_bootloader.py --port COM16
```

To flash a UF2 drive:

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\flash_nrf52840_uf2.ps1 -BootloaderDrive F:
```

## Module OTA Updates

The master can push file updates to discovered modules over NFCP. Build an
SD-card package with:

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\build_module_ota_package.ps1 -Target field
powershell -ExecutionPolicy Bypass -File .\scripts\build_module_ota_package.ps1 -Target nrf-dummy
```

The package is written to `dist/<target>_ota_package/` and contains
`ota_update.json` plus the files needed by that target. Copy the package
contents to the master SD card root, then open `Settings > OTA` on the master.

The module must be disarmed and not running a script during update.

## Bridge Updates From The Master

The master can update its attached nRF bridge over the internal UART. This avoids
opening the master enclosure for normal bridge firmware updates.

Build the SD-card bridge update folder with:

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\build_bridge_update_package.ps1
```

Copy the generated `dist\bridge_update` folder to the SD card root so the master
sees:

```text
/sd/bridge_update/main.py
/sd/bridge_update/bridge_radio.py
/sd/bridge_update/nrf_bridge_config.py
```

On the master, open `Settings` and press `BRIDGE UPD`. The master copies the
files to the bridge, verifies each file, and resets the bridge.

## Field Module LCD

The field module can drive an optional 2x16 I2C LCD. If the display is not
present, the firmware logs that and continues normally.

Default MS88SFA LCD and button mapping:

- LCD SDA: MicroPython pin `26` / nRF `P0.26`
- LCD SCL: MicroPython pin `27` / nRF `P0.27`
- Up / pair button: MicroPython pin `45` / nRF `P1.13`
- Down button: MicroPython pin `47` / nRF `P1.15`
- OK / forward button: MicroPython pin `15` / nRF `P0.15`
- Back button: MicroPython pin `20` / nRF `P0.20`

UP and DOWN move between pages, BACK returns to the home page, and OK refreshes
continuity when viewing continuity pages. The LCD shows field-module system
status, UID/network/radio channel, battery/firmware information, script state,
and continuity summary/detail pages.

For LCD and button-only bench testing, upload the standalone UI test app:

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\upload_lcd_ui_test_to_com10.ps1 -Port COM10
```

That app does not use the radio or field outputs. It simulates system,
network, battery, and continuity pages so the LCD/button flow can be tested
while a separate dummy module handles any master communication tests.

Standalone nRF52840 Pro Micro LCD test wiring:

- LCD SDA: board `D8` / nRF `P1.04` / MicroPython pin `36`
- LCD SCL: board `D9` / nRF `P1.06` / MicroPython pin `38`
- UP button: board `D15` / nRF `P1.13` / MicroPython pin `45`
- DOWN button: board `D18` / nRF `P1.15` / MicroPython pin `47`
- OK button: board `D14` / nRF `P1.11` / MicroPython pin `43`
- BACK button: board `D16` / nRF `P0.10` / MicroPython pin `10`

Buttons are read with internal pull-ups, so wire each button between its GPIO
pin and `GND`. Use 3.3 V-safe I2C wiring for the LCD.

## Channel Scan And Pairing

The master Settings screen can scan radio channels, show the best results, and
apply a selected channel to discovered modules plus the local bridge. The scan
intentionally avoids the 11 common Wi-Fi channel centers.

For bench testing:

```powershell
python .\scripts\test_nrf_channel_scan.py --master COM21
python .\scripts\test_nrf_channel_apply.py --master COM21 --current-channel 90 --channel 84
python .\scripts\test_nrf_channel_apply.py --master COM21 --current-channel 84 --channel 90
```

If a migration is interrupted, restore all modules and the bridge to the same
channel before running discovery again.

## Range Testing

The master Settings screen includes a range-test view. Select a discovered
module, start the test, and the master repeatedly pings that module while
showing packet loss, RSSI, and timing information.

## Script Upload And Playback

The master can upload script chunks to discovered modules before playback.
Timed playback uses module-side scheduling plus heartbeat and time-sync packets
so closely spaced cues do not depend on per-cue ACKs.

## Trellis Board

The master checks the Adafruit Trellis board during boot. It verifies I2C,
checks that no buttons are held, and sweeps the LEDs before continuing.

Default Trellis settings in `main/master_main.py`:

- I2C bus: `0`
- SCL: GPIO `9`
- SDA: GPIO `8`
- Address: `0x70`

## Notes

- The shared protocol lives in `main/nfcp.py`; avoid duplicating NFCP packet logic.
- The nRF bridge protocol lives in `main/bridge_radio.py`.
- Use `scripts/enter_nrf_bootloader.py` when a directly connected nRF board needs
  to be put into UF2 bootloader mode for testing.
