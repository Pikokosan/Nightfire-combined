# ESP32 master controller

The Waveshare ESP32-S3-LCD-4.3 touchscreen master (`main/master_main.py`) is
the original Nightfire controller. It reaches the field through an nRF52840
bridge on its internal UART (`main/nrf_bridge_main.py`, config
`main/configs/bridge_esp32_master.py`). The Android app with the USB bridge
(`docs/building_devices.md`) is the current controller; this page covers the
master.

Install the master and its bridge:

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\install\upload_esp32_master.ps1 -Port COM21
powershell -ExecutionPolicy Bypass -File .\scripts\install\upload_esp32_master_bridge.ps1 -Port COM15
```

## Bench COM ports

- Master device: `COM7` or current test master `COM21`
- Field module: `COM10`
- nRF dummy device: `COM16`
- nRF bridge: usually `COM15` or `COM22`

Each upload script accepts a `-Port` override.

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\install\upload_esp32_master.ps1 -Port COM21
powershell -ExecutionPolicy Bypass -File .\scripts\install\upload_field_module.ps1 -Port COM10 -Pinout promicro
powershell -ExecutionPolicy Bypass -File .\scripts\install\upload_lcd_ui_test.ps1 -Port COM10
powershell -ExecutionPolicy Bypass -File .\scripts\install\upload_esp32_master_bridge.ps1 -Port COM15
powershell -ExecutionPolicy Bypass -File .\scripts\install\upload_nrf_dummy.ps1 -Port COM16
```

To replace the field firmware with the standalone 18-channel hardware test app:

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\install\upload_field_hardware_test.ps1 -Port COM10
```

The field and hardware-test upload scripts accept `-Pinout promicro` for the
nRF52840 Pro Micro test fixture or `-Pinout ms88sfa` for the production module
mapping documented in `docs/field_module_pinout.md`. The hardware test runs no
Nightfire radio/protocol code, keeps 74HC595 `/OE` disabled by default, and only
walks the outputs if `ENABLE_OUTPUT_WALK = True` is supplied in
`nrf_node_config.py`. Omit `-Pinout` to retain the configuration already stored
on a board.

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

## Module OTA Updates

The master can push file updates to discovered modules over NFCP. Build an
SD-card package with:

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\build\build_module_ota_package.ps1 -Target field
powershell -ExecutionPolicy Bypass -File .\scripts\build\build_module_ota_package.ps1 -Target nrf-dummy
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
powershell -ExecutionPolicy Bypass -File .\scripts\build\build_bridge_update_package.ps1
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

## Channel Scan And Pairing

The master Settings screen can scan radio channels, show the best results, and
apply a selected channel to discovered modules plus the local bridge. The scan
intentionally avoids the 11 common Wi-Fi channel centers.

For bench testing:

```powershell
python .\scripts\bench\test_nrf_channel_scan.py --master COM21
python .\scripts\bench\test_nrf_channel_apply.py --master COM21 --current-channel 90 --channel 84
python .\scripts\bench\test_nrf_channel_apply.py --master COM21 --current-channel 84 --channel 90
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
