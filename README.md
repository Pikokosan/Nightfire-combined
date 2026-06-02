# Nightfire Combined

Nightfire Combined contains the MicroPython firmware for the NFCP master,
slave, and dummy devices. The devices share the same NFCP packet protocol and
can run over either the older RS-485 wiring or the newer E77 radio UART bridge.

Current firmware is set up for the E77 radio transport.

## Project Layout

- `main/nfcp.py` - shared NFCP framing, escaping, CRC, commands, and packet builders.
- `main/e77_radio.py` - local UART protocol used to send NFCP frames through the E77 radio.
- `main/module_transport.py` - shared dummy/slave UART transport and radio update helper.
- `main/module_ota.py` - shared dummy/slave staged-file OTA update helper.
- `main/e77_bootloader.py` - device-side E77 radio firmware updater.
- `main/master_main.py` - Waveshare ESP32-S3-LCD-4.3 master controller GUI.
- `main/slave_main.py` - real 16-cue module firmware using the MC33996 driver.
- `main/dummy_main.py` - simulated module firmware for testing.
- `scripts/upload_master_to_com7.ps1` - installs master firmware as `main.py`.
- `scripts/upload_slave_to_com10.ps1` - installs slave firmware as `main.py`.
- `scripts/upload_dummy_to_com11.ps1` - installs dummy firmware as `main.py`.
- `scripts/build_module_ota_package.ps1` - builds the customer update package.
- `scripts/install_master_update.ps1` - installs packaged master firmware over USB.
- `scripts/update_e77_radio_firmware.py` - PC-side E77 radio firmware updater for a radio connected directly to the computer.

## Default Ports

- Master device: `COM7`
- Slave device: `COM10`
- Dummy device: `COM11`

Each upload script accepts a different port:

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\upload_master_to_com7.ps1 -Port COM7
powershell -ExecutionPolicy Bypass -File .\scripts\upload_slave_to_com10.ps1 -Port COM10
powershell -ExecutionPolicy Bypass -File .\scripts\upload_dummy_to_com11.ps1 -Port COM11
```

The VS Code build tasks run the same scripts:

- `Master device: upload`
- `Slave device: upload`
- `Dummy device: upload`
- `Nightfire update package: build`

## Device Firmware Updates

Use the matching upload task or script when changing MicroPython firmware.
Each script copies the correct device entrypoint to the board as `main.py` plus
the shared modules that device needs.

Master upload includes:

- `master_main.py` as `main.py`
- `nfcp.py`
- `e77_radio.py`
- `e77_bootloader.py`
- display and SD card support files

Slave upload includes:

- `slave_main.py` as `main.py`
- `nfcp.py`
- `e77_radio.py`
- `module_transport.py`
- `module_ota.py`
- `e77_bootloader.py`
- `mc33996_driver.py`

Dummy upload includes:

- `dummy_main.py` as `main.py`
- `nfcp.py`
- `e77_radio.py`
- `module_transport.py`
- `module_ota.py`
- `e77_bootloader.py`

## Customer Update Package

Build a full customer package with the VS Code task
`Nightfire update package: build`, or run:

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\build_module_ota_package.ps1
```

The task writes:

- `dist/nightfire_update_package/`
- `dist/nightfire_update_<version>.zip`

The package contains:

- `master_update/` - master controller firmware files and `master_update.json`
- root-level module OTA files - copied to the master SD card for module updates
- `install_master_update.ps1` - PC-side master updater
- `raw_micropython_upload.py` - upload helper used by the master updater

To update the master from the package:

```powershell
powershell -ExecutionPolicy Bypass -File .\install_master_update.ps1 -Port COM7
```

To update modules, copy the package root files to the master SD card, then use
Settings > Module OTA on the master. Keep `module_ota.json`, and when present
`e77_radio.bin`, `latest.json`, and `radio_update.flag`, at the SD card root.

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

## Module Firmware Versioning And OTA Updates

Slave and dummy modules report their module firmware name/version and attached
E77 radio firmware version through NFCP `INFO_REQ` / `INFO_RSP`. The master reads
this information during Discover and Refresh and shows it in the module detail
screen.

The master also has a Settings > Module OTA option. It reads
`/sd/module_ota.json` or `/sd/firmware/module_ota.json` and sends each listed
file to matching discovered modules over NFCP. The module writes to a temporary
`.ota` file, verifies the simple 32-bit additive checksum, then renames it into
place.

Example `module_ota.json`:

```json
{
  "files": [
    {
      "source": "slave_main.py",
      "target": "/main.py",
      "device_type": "0x20"
    },
    {
      "source": "dummy_main.py",
      "target": "/main.py",
      "device_type": "0x10"
    },
    {
      "source": "module_transport.py",
      "target": "/module_transport.py"
    },
    {
      "source": "module_ota.py",
      "target": "/module_ota.py"
    }
  ]
}
```

Customer OTA packages can be built with the VS Code task
`Nightfire update package: build`. The task writes:

- `dist/nightfire_update_package/`
- `dist/nightfire_update_<version>.zip`

Copy the package folder contents to the root of the master's SD card. If the
E77 radio firmware build is available, the package also includes the radio
firmware update alongside the module app update:

- `e77_radio.bin`
- `latest.json`
- `radio_update.flag`

Those files are sent to each module with the app update. After updating
`main.py`, reset the module. On the next boot the module sees
`radio_update.flag`, flashes the attached E77 radio using `latest.json`, clears
the flag after success, and then returns to normal operation.

The module talks to the radio app at the configured app baud rate, while the
STM32 ROM bootloader update step still uses `115200`.

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

## Notes

- The shared protocol lives in `main/nfcp.py`; avoid duplicating NFCP packet
  logic in device files.
- The radio updater erases the radio before writing. Make sure the firmware
  image is correct before starting an update.
- If a dummy or slave radio update fails, the flag file remains in place so the
  device can retry on the next reset.
