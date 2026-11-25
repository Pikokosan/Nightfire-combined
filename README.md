# Nightfire

Wireless firing system: an Android controller app talks through a USB radio
bridge to battery-powered field modules over a 2.4 GHz mesh radio. All the
radio boards are nRF52840s running a custom MicroPython build.

```text
Android app ──USB── bridge (dongle) ~~radio~~ field modules / dummy modules
                                      (modules relay for each other, 2 hops)
```

## Start here

| I want to... | Read |
|---|---|
| Build and program a bridge, field module or dummy | [docs/building_devices.md](docs/building_devices.md) |
| Change the bridge's status LEDs (WS2811, SK6812...) | [docs/bridge_status_leds.md](docs/bridge_status_leds.md) |
| Wire the bridge's SAFE/ARM switch and LEDs | [docs/command_interface_wiring.md](docs/command_interface_wiring.md) |
| Look up field-module pins | [docs/field_module_pinout.md](docs/field_module_pinout.md), [docs/field_module_lcd.md](docs/field_module_lcd.md) |
| Build or work on the Android app | The [nightfire-app](https://github.com/Pikokosan/nightfire-app) repo (a sibling folder, `../../nightfire-app`) |
| Understand the mesh radio | [docs/mesh_radio_design.md](docs/mesh_radio_design.md) |
| Sync a show to an audio box (design) | [audio_box/design.md](audio_box/design.md) |
| Use the older ESP32 touchscreen master | [docs/esp32_master.md](docs/esp32_master.md) |
| See what changed | [CHANGELOG.md](CHANGELOG.md) |
| Find a file | [docs/project_layout.md](docs/project_layout.md) |

## Folders

| Folder | Contents |
|---|---|
| `main/` | Code that runs on the boards, flat, as it is stored on a board |
| `main/configs/` | One config per kind of device; installed on the board as its config file |
| `firmware/` | MicroPython images and bootloaders (`nrf52840/`) and the source to rebuild them (`micropython/`) |
| `scripts/install/` | Put firmware on boards, change a bridge's LEDs |
| `scripts/build/` | Build bundles, update packages and the MicroPython image |
| `scripts/tests/` | Offline tests, no hardware needed |
| `scripts/bench/` | Tests that drive real boards |
| `scripts/diagnostics/` | Probes and one-off checks |
| `scripts/sim/` | Mesh simulator and firmware trace harness |
| `scripts/android/` | Tablet emulator, USB passthrough, app test fixtures |
| `scripts/lib/` | Shared helpers the other scripts import (raw REPL upload, bridge requests) |
| `docs/` | Guides and design notes |

## Common commands

Run from the repository root.

```powershell
# Program a USB bridge
py -3 scripts/install/install_usb_bridge.py --port COM12 --master --compiled

# Program a field module (MS88SFA board)
powershell -ExecutionPolicy Bypass -File .\scripts\install\upload_field_module.ps1 -Port COM10 -Pinout ms88sfa

# Program a dummy module
powershell -ExecutionPolicy Bypass -File .\scripts\install\upload_nrf_dummy.ps1 -Port COM18 -ConfigPath .\main\configs\dummy_lcd.py

# Run every offline test
Get-ChildItem scripts\tests\test_*.py | ForEach-Object { py -3 $_.FullName }

# Run the mesh simulator
py -3 scripts/sim/mesh_sim.py --list
```

## Notes

- The shared command protocol is `main/nfcp.py`, and the bridge's frame
  protocol is `main/bridge_radio.py`. The USB bridge uses the smaller
  `main/nfcp_frame.py` for NFCP frames; `scripts/tests/test_nfcp_frame.py`
  keeps it in step with `nfcp.py`. Don't duplicate packet logic elsewhere.
- The mesh layer shared by the bridge and the modules is `main/mesh.py`.
- Add a `CHANGELOG.md` entry with every commit.
