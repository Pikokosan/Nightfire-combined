# Nightfire Combined

Nightfire Combined contains the MicroPython firmware for the NFCP master,
slave, dummy, and E77 module devices. The current working build is focused on
the E77 radio transport.

## Project Layout

- `main/nfcp.py` - shared NFCP framing, escaping, CRC, commands, and packet builders.
- `main/master_main.py` - Waveshare ESP32-S3-LCD-4.3 master controller GUI.
- `main/slave_main.py` - real 16-cue module firmware using the MC33996 driver.
- `main/dummy_main.py` - simulated 16-cue module firmware for testing.
- `main/e77_main.py` - E77 MicroPython NFCP radio module app.
- `main/e77_radio.py` - local UART protocol for the E77 radio bridge.
- `main/e77_gfsk.py` - STM32WL SUBGHZ GFSK radio driver used by `e77_main.py`.
- `main/e77_bootloader.py` - device-side E77 radio firmware updater.
- `main/module_transport.py` - shared dummy/slave UART and radio transport helper.
- `main/mc33996_driver.py` - MC33996 output driver helper.
- `main/nrf_dummy_main.py` - nRF52840 proprietary-radio dummy prototype.
- `scripts/upload_master_to_com7.ps1` - installs master firmware as `main.py`.
- `scripts/upload_slave_to_com10.ps1` - installs slave firmware as `main.py`.
- `scripts/upload_dummy_to_com11.ps1` - installs dummy firmware as `main.py`.
- `scripts/upload_e77_micropython_app.ps1` - installs the E77 MicroPython app as `main.mpy`.
- `scripts/build_e77_ota_package.ps1` - builds the E77 MicroPython OTA package for the master SD card.
- `scripts/update_e77_radio_firmware.py` - PC-side E77 radio firmware updater.

## Default Ports

- Master device: `COM7`
- Slave device: `COM10`
- Dummy device: `COM11`
- E77 MicroPython module: `COM17`

Each upload script accepts a port override:

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\upload_master_to_com7.ps1 -Port COM7
powershell -ExecutionPolicy Bypass -File .\scripts\upload_slave_to_com10.ps1 -Port COM10
powershell -ExecutionPolicy Bypass -File .\scripts\upload_dummy_to_com11.ps1 -Port COM11
powershell -ExecutionPolicy Bypass -File .\scripts\upload_e77_micropython_app.ps1 -Port COM17
```

E77 MicroPython builds include hardware safety support by default:

- `A11` controls VPWR enable.
- `A0` reads the battery divider.
- `A1` reads the short/drop divider.
- `B3` enables the pre-arm test path.
- `B6/B7` are reserved for future LCD I2C as `I2C(1)`.

For bench/debug builds that should not use those pins or ADC checks, add
`-HardwareSafetyOff`:

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\upload_e77_micropython_app.ps1 -Port COM17 -HardwareSafetyOff
```

The VS Code build tasks run the same upload scripts:

- `Master device: upload`
- `Slave device: upload`
- `Dummy device: upload`
- `E77 MicroPython app: upload`
- `E77 OTA package: build`
- `nRF52840 dummy: upload`

## nRF52840 Prototype

The nRF52840 Pro Micro path is a MicroPython prototype for a future proprietary
2.4 GHz transport. It intentionally does not use BLE.

- Native radio module scaffold: `nrf52840/native/nrf_radio/`
- Master-side local radio bridge: `main/nrf_bridge_main.py`
- Dummy module app: `main/nrf_dummy_main.py`
- Master bridge upload helper: `scripts/upload_nrf_bridge.ps1`
- Master bridge UF2 bootloader helper:
  `scripts/enter_nrf_bridge_bootloader_via_master.py`
- Upload helper: `scripts/upload_nrf_dummy.ps1`

The native module exposes `nrf_radio.init()`, `send()`, `recv()`,
`last_rssi()`, and `channel()`. It is intended to be compiled into an nRF52840
MicroPython firmware build using MicroPython user C modules. The Python app
wraps NFCP frames in:

```text
network_id(2 bytes) | nfcp_frame
```

The master-side nice!nano does not run as a module. It runs
`nrf_bridge_main.py`, which speaks the same local UART protocol as
`e77_radio.py`:

- `PING` / `PONG`
- `GET_INFO`
- `SET_NETWORK`
- `SEND_NFCP`
- `NFCP_RX`

This keeps the master GUI transport path compatible with the existing E77
radio bridge while the over-air side uses the nRF52840 proprietary radio.

### nRF52840 Master Bridge Pinout

Use the nice!nano running `nrf_bridge_main.py` as the master radio bridge.
The default bridge UART settings are:

- Baud: `256000`
- nice!nano UART: `UART0`
- nice!nano TX: `P6`
- nice!nano RX: `P8`
- Radio channel: `40`
- Network ID: `0x04D2`

Wire it to the Waveshare ESP32-S3 master UART as a crossed UART:

```text
Waveshare master 3V3  -> nice!nano VCC/3V3
Waveshare master GND  -> nice!nano GND
Waveshare master TX15 -> nice!nano P8 / RX
Waveshare master RX16 -> nice!nano P6 / TX
```

Do not connect 5 V logic to the nice!nano UART pins.

To update the nice!nano bridge app itself, connect the bridge USB port to the
computer and run:

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\upload_nrf_bridge.ps1 -Port COM15
```

To enter UF2 bootloader from a running nRF MicroPython app over USB, interrupt
the REPL and issue `machine.bootloader()` with:

```powershell
python .\scripts\enter_nrf_bootloader.py --port COM15
```

After the UF2 drive appears, copy the built nRF52840 MicroPython firmware to it:

```powershell
Copy-Item .\firmware\nrf52840\nightfire_nicenano_micropython.uf2 F:\firmware.uf2
```

Bridge firmware also supports channel scanning. The master Settings screen asks
the bridge to scan Wi-Fi-avoiding nRF channels, currently channel `0` plus
`74-100`, scores the best 10 by RSSI margin and busy-sample count, and applies a
selected channel to every discovered module. Modules ACK the
`CMD_SET_RADIO_CHANNEL` command and save the selected channel before the master
switches the local bridge.

For bench testing without using the touchscreen UI:

```powershell
python .\scripts\test_nrf_channel_scan.py --master COM21
python .\scripts\test_nrf_channel_apply.py --master COM21 --current-channel 40 --channel 84
python .\scripts\test_nrf_channel_apply.py --master COM21 --current-channel 84 --channel 40
```

The apply helper updates the master `nfcp_settings.json` radio channel after a
successful module and bridge migration. If a channel migration is interrupted,
restore all modules and the bridge to the same channel before running discovery.

The currently connected test board reports as:

```text
Board-ID: nRF52840-pca10056-v1
Model: Nordic nRF52840 DK
```

## Device Firmware Uploads

Use the matching upload task or script when changing MicroPython firmware.
Each script copies the correct device entrypoint to the board plus the shared
modules that device needs.

Master upload includes:

- `master_main.py` as `main.py`
- `nfcp.py`
- `e77_radio.py`
- `e77_bootloader.py`
- `adafruit_trellis.py`
- display and SD card support files

Slave upload includes:

- `slave_main.py` as `main.py`
- `nfcp.py`
- `e77_radio.py`
- `module_transport.py`
- `e77_bootloader.py`
- `mc33996_driver.py`

Dummy upload includes:

- `dummy_main.py` as `main.py`
- `nfcp.py`
- `e77_radio.py`
- `module_transport.py`
- `e77_bootloader.py`

E77 MicroPython app upload includes:

- `main/e77_main.py` compiled and installed as device `main.mpy`
- `main/nfcp.py` compiled as `nfcp.mpy`
- `main/e77_gfsk.py` compiled as `e77_gfsk.mpy`
- `main/mc33996_driver.py` compiled as `mc33996_driver.mpy`
- a tiny device `boot.py` stub that imports `main.mpy` on startup

## Module OTA Updates

The master can send OTA updates to modules over NFCP through the radio link.
Packages are target-specific so the master does not flash slave firmware onto a
dummy module or the nRF dummy test firmware onto a production module.

Build an SD-card package with:

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\build_e77_ota_package.ps1
```

To build an OTA package without the E77 hardware safety layer:

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\build_e77_ota_package.ps1 -HardwareSafetyOff
```

For slave, dummy, and nRF dummy module updates, build a target-specific package:

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\build_module_ota_package.ps1 -Target slave
powershell -ExecutionPolicy Bypass -File .\scripts\build_module_ota_package.ps1 -Target dummy
powershell -ExecutionPolicy Bypass -File .\scripts\build_module_ota_package.ps1 -Target nrf-dummy
```

The task writes `dist/e77_ota_package/`. Copy the contents of that folder to the
root of the master SD card. The package includes:

- `e77_update.json`
- `nfcp.mpy`
- `e77_gfsk.mpy`
- `mc33996_driver.mpy`
- `main.mpy`
- `boot.py`

On the master, open `Settings > OTA`. The master reads `/sd/e77_update.json`,
sends each listed file only to discovered modules matching the manifest target,
verifies size/checksum on each module, commits the new files, and then asks each
updated module to reboot.

The module must be disarmed and not running a script during update.

Modules running firmware older than the first E77 updater build must be updated
once with `E77 MicroPython app: upload` before they can accept OTA updates.

Radio firmware updates are still supported separately by the radio update flows
below.

## Module Script Timing

Module-auto script playback uses a heartbeat failsafe and a short time-sync
burst before start. The master sends `CMD_TIME_SYNC` packets with its current
tick value, then script start packets include a shared absolute master start
tick. Modules with a fresh sync schedule from that shared clock; modules without
sync fall back to the older start-delay behavior.

## Radio Firmware Updates From The Master

The master has a GUI option for updating the radio attached to the master UART.

1. Put the E77 radio firmware `.bin` on the master filesystem or SD card.
2. Open `Settings`.
3. Tap `RADIO UPDATE`.
4. If firmware is found, tap `START UPDATE`.

Supported firmware locations:

- `/sd/latest.json`
- `/sd/firmware/latest.json`
- `/sd/e77_radio.bin`
- `/sd/e77_radio_latest.bin`
- `/sd/firmware/e77_radio.bin`
- `/sd/firmware/e77_radio_latest.bin`
- `/e77_radio.bin`

Example `latest.json`:

```json
{
  "target": "E77_STM32WLE5CCU6",
  "version": "0.1.0",
  "filename": "e77_radio.bin",
  "address": "0x08000000",
  "crc32": "0x00000000"
}
```

The `crc32` field is optional. If present, the updater verifies the `.bin`
before writing it.

## Radio Firmware Updates From Dummy Or Slave Devices

Dummy and slave devices do not have a GUI, so they use a boot-time update flag.

1. Copy the E77 `.bin` and optional `latest.json` to the device filesystem.
2. Add a file named `radio_update.flag`.
3. Reset the device.

The device will:

- check for `radio_update.flag`
- find the firmware image
- ask the attached E77 radio to enter the STM32 ROM bootloader
- erase and write the radio firmware
- clear `radio_update.flag` after a successful update
- restore the UART and continue normal NFCP startup

Supported flag locations:

- `/sd/radio_update.flag`
- `/sd/firmware/radio_update.flag`
- `/radio_update.flag`
- `radio_update.flag`

Example using `mpremote` on the dummy device:

```powershell
mpremote connect COM11 fs cp C:\path\to\e77_radio.bin :e77_radio.bin
mpremote connect COM11 exec "open('radio_update.flag', 'w').close()"
mpremote connect COM11 reset
```

Use `COM10` for the slave device when it is connected.

## Direct PC Radio Firmware Update

If an E77 radio is connected directly to the computer over UART, use the PC-side
updater:

```powershell
python .\scripts\update_e77_radio_firmware.py --port COM15 C:\Users\hog28\OneDrive\Documents\NightfirePY\Nightfire-E77\build\release\e77_radio.elf
```

That script sends the E77 bootloader command, runs `STM32_Programmer_CLI.exe`,
verifies the image, and starts the new firmware. The updater talks to the
running E77 app at `256000` baud, then programs through the STM32 ROM
bootloader at `115200` baud. It supports `.elf`, `.hex`, and `.bin` images.

The device-side updater only writes raw `.bin` images.

## E77 Radio Transport

The E77 radio firmware exposes a local UART protocol:

- `PING` / `PONG` for local radio checks
- `SEND_NFCP` to transmit an NFCP frame over radio
- `NFCP_RX` when a radio packet is received
- `SET_NETWORK` to change the local E77 app network ID
- `BOOTLOADER` to jump into the STM32WL ROM UART bootloader

Current device UART settings for E77 mode:

- App baud: `256000`
- Radio bootloader baud: `115200`
- Master UART: `UART2`, TX `15`, RX `16`
- Slave/Dummy UART: `UART0`, TX `0`, RX `1`

The master stores its selected network ID in `nfcp_settings.json`. Dummy and
slave modules store their paired radio network ID in `nfcp_cfg.bin` and apply it
to the attached E77 radio during boot. New modules ship on the default E77
network; use Settings > Pair on the master to scan that default network and move
responding modules to the master's selected network.

## Master Trellis Board

The master checks the Adafruit Trellis board during boot. It verifies the board
is reachable over I2C, reads all 16 keys, and fails boot into a visible Trellis
error screen if any key is held down or the board cannot be reached. If the key
check passes, the master sweeps through the 16 Trellis LEDs before continuing.

Default Trellis settings in `main/master_main.py`:

- I2C bus: `0`
- SCL: GPIO `9`
- SDA: GPIO `8`
- Address: `0x70`

## Notes

- The shared protocol lives in `main/nfcp.py`; avoid duplicating NFCP packet
  logic in device files.
- The radio updater erases the radio before writing. Make sure the firmware
  image is correct before starting an update.
- If a dummy or slave radio update fails, the flag file remains in place so the
  device can retry on the next reset.
