# Building and programming Nightfire devices

This guide takes a blank nRF52840 board to a working Nightfire device. Follow
the steps in order. Every command runs in PowerShell from the repository root
(the folder that holds `README.md`).

## What you are building

| Device | What it does | Board | Firmware entry point |
|---|---|---|---|
| **USB bridge** (dongle) | Plugs into the Android tablet; radio link to every module | nice!nano v2 (nRF52840) | `main/nrf_usb_monitor_main.py` |
| **Field module** | Fires cues, checks continuity | MS88SFA field-module PCB, or a Pro Micro nRF52840 test board | `main/field_main.py` |
| **Dummy module** | Behaves like a field module with no outputs; for bench and range tests | Any nice!nano / Pro Micro nRF52840 | `main/nrf_dummy_main.py` |
| **Controller app** | The Android app that runs the show | Android 7+ tablet with USB host (OTG) | [nightfire-app](https://github.com/Pikokosan/nightfire-app) repo |

All the boards run the same custom MicroPython build. Only the files copied
onto each board differ.

## 1. Set up the PC (once)

1. **Python 3** for Windows, from python.org, with the `py` launcher.
2. **pyserial** and **mpy-cross**:

   ```powershell
   py -3 -m pip install pyserial mpy-cross
   mpy-cross --version
   ```

   `mpy-cross --version` must say `mpy v6.3`. A different version builds
   files the boards reject ("incompatible .mpy file").
3. A **USB data cable**. Many charge-only cables carry no data; if no COM port
   appears in step 2, try another cable.

To find a board's COM port, plug it in and run:

```powershell
[System.IO.Ports.SerialPort]::GetPortNames()
```

Run it before and after plugging the board in. The new name is the board.
Only one program can use a COM port at a time, so close serial terminals,
Thonny, the emulator relay, and so on before running these scripts.

## 2. Put Nightfire MicroPython on each board (once per board)

Every nRF52840 board needs this before anything else. The image is
`firmware/nrf52840/nightfire_nicenano_micropython.uf2`.

1. **Enter the bootloader.** Double-tap the board's reset button (on a
   nice!nano without a button, briefly short RST to GND twice). A USB drive
   appears (named `NICENANO` or similar, with `INFO_UF2.TXT` on it).
   A board that already runs Nightfire can be switched from the PC instead:

   ```powershell
   py -3 scripts/install/enter_nrf_bootloader.py --port COM5
   ```

2. **Copy the image** onto that drive:

   ```powershell
   powershell -ExecutionPolicy Bypass -File .\scripts\install\flash_nrf52840_uf2.ps1
   ```

   The script finds the bootloader drive by itself (pass
   `-BootloaderDrive E:` if more than one is plugged in). Dragging the `.uf2`
   file onto the drive in Explorer works too. The drive disappears and the
   board restarts with a new COM port.

Notes:

- The board needs the UF2 bootloader. nice!nano boards ship with it. A bare
  module without it must be programmed over SWD first; the bootloader used on
  the nice!nano boards is in `firmware/nrf52840/bootloader/`.
- Boards flashed before 2026-11-05 used a smaller filesystem. Flashing this
  image erases their files, so do step 2 first and then install the device
  files below.

## 3. Install a device

Pick the section for the device you are making. The scripts back up and
replace only Nightfire's own files on the board.

### USB bridge (dongle)

1. Optional hardware: the SAFE/ARM switch and status LEDs. Wiring:
   `docs/command_interface_wiring.md`. LED types and settings:
   `docs/bridge_status_leds.md`.
2. Check `main/configs/bridge_usb.py`. For a bridge **without** the switch or
   LEDs, set `ARM_SWITCH_PIN`, `STATUS_LED_PIN` and `STATUS_LED_POWER_PIN` to
   `None`. Network ID `0x04D2` and channel `90` match the modules' defaults.
3. Install:

   ```powershell
   py -3 scripts/install/install_usb_bridge.py --port COM12 --master --compiled
   ```

   This builds the compiled bridge firmware, saves the board's existing files
   to `dist/bridge_backup_<time>/`, copies the new ones and checks each one.
   On a new board it installs `main/configs/bridge_usb.py` as the bridge's
   config. On a bridge that already has switch or LED pins, it keeps the
   bridge's own switch and LED settings.
4. Check it answers:

   ```powershell
   py -3 scripts/lib/probe_usb_bridge.py --port COM12
   ```

   You should see `PONG` and the bridge firmware version
   (`usb-master-...`).
5. If you fitted LEDs, they should show the status colours (pixel 1 green
   with the switch on SAFE). If they show all white or wrong colours, follow
   `docs/bridge_status_leds.md`.

### Field module

1. Pick the pin map for the board:
   - `-Pinout ms88sfa`: the MS88SFA field-module PCB
     (`main/configs/field_ms88sfa.py`);
   - `-Pinout promicro`: the Pro Micro nRF52840 test board
     (`main/configs/field_promicro.py`).

   Pin tables for both are in `docs/field_module_pinout.md`.
2. Install:

   ```powershell
   powershell -ExecutionPolicy Bypass -File .\scripts\install\upload_field_module.ps1 -Port COM10 -Pinout ms88sfa
   ```

   The script builds one compiled file from the field firmware, clears the
   old image, and copies the new one with the chosen config. Always pass
   `-Pinout` on a new board. Leave it out on later updates to keep the
   config already on the board.
3. The board restarts. With an LCD fitted, it shows the module status screen.

### Dummy module

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\install\upload_nrf_dummy.ps1 -Port COM18 -ConfigPath .\main\configs\dummy_lcd.py
```

Use `main/configs/dummy_lcd.py` for a dummy with the 16x2 LCD and four buttons,
or `main/configs/dummy_plain.py` for a bare board. A missing LCD is fine; the
dummy logs it and carries on.

### Module identity

Every module takes its ID from the nRF52840 chip's factory ID, so no two are
the same and nothing needs setting. (`UNIQUE_ID = 0` in a config means "use
the chip ID"; any other value overrides it.) All modules start on network
`0x04D2`, channel 90, group 1. The app can change the channel and group later.

## 4. Check the radio link (optional, on the PC)

With the bridge plugged into the PC and a module powered nearby:

```powershell
py -3 scripts/diagnostics/mesh_hw_smoke.py --bridge COM12
```

It discovers modules and pings them the way the app does, without arming or
firing anything. Add `--module-port COM10` if the module is also plugged into
the PC, to see its side of the conversation.

## 5. Install the app and connect

1. Build the app (needs Flutter and the Android SDK; see the
   [nightfire-app](https://github.com/Pikokosan/nightfire-app) README). From the nightfire-app folder:

   ```powershell
   ./tool/build_android_app.ps1 -JdkPath 'C:/Program Files/Java/jdk-23' -Check
   ```

   The APK is `build/app/outputs/flutter-apk/app-debug.apk` in nightfire-app.
2. Install it on the tablet: copy the APK over and open it, or with USB
   debugging on, `adb install -r <apk>`.
3. Plug the bridge into the tablet through a USB OTG adapter or hub.
4. In the app, open **Radio**, press **Scan USB**, then **Connect**, and allow
   USB access when Android asks.
5. **Discover** finds the powered modules. Each module card shows its
   battery, signal and continuity.

## Updating devices later

| Device | From the app | From the PC |
|---|---|---|
| Bridge | Firmware page, bridge update. It installs on trial; see below | Repeat step 3 |
| Field module | Firmware page, over the radio: send `field_main.mpy` (built into `build/field_module_upload/` by the PC upload script) | `upload_field_module.ps1` without `-Pinout` |
| Dummy | — | `upload_nrf_dummy.ps1` without `-ConfigPath` |

An app bridge update installs on trial: if no app session confirms it, the
bridge goes back to the previous firmware the next time it starts. If a bridge
change seems to have had no effect, reinstall it from the PC (step 3), which is
permanent.

The bridge files for an app update are made by
`py -3 scripts/build/build_usb_master_bundle.py` and land in
`dist/android/usb-master-bundle/`.

## Troubleshooting

| Problem | Fix |
|---|---|
| No COM port appears | Try another (data) cable or USB port. A board in bootloader mode shows a drive, not a COM port. |
| "Access is denied" / port busy | Another program has the port open. Close it (or the emulator relay). |
| `mpy-cross was not found` | Install it (step 1) or open a new PowerShell so `PATH` updates. |
| Board prints "incompatible .mpy file" | `mpy-cross` is the wrong version. It must emit `mpy v6.3`. |
| Board came back empty after flashing | Expected on boards from before 2026-11-05; reinstall the device (step 3). |
| Bridge LEDs all white or wrong colours | `docs/bridge_status_leds.md` |
| Module not found by Discover | Same channel and network as the bridge? Check the module's LCD info page or `mesh_hw_smoke.py`. |

## Where things are

`docs/project_layout.md` maps the folders. In short: device code is in
`main/`, device configs in `main/configs/`, MicroPython images in
`firmware/`, and the PC scripts in `scripts/` (`install/` for everything in
this guide).
