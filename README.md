# Nightfire Combined

This project keeps one shared NFCP protocol implementation:

- `main/nfcp.py`

Device-specific entrypoints live beside it:

- `main/master_main.py`
- `main/slave_main.py`
- `main/dummy_main.py`

Before uploading to a device, use the matching device entrypoint as `main.py`.
The device-specific files import the same shared `nfcp.py`, so packet framing,
CRC, escaping, commands, FIRE masks, and continuity response parsing stay in
one place.

`nfcp_core.py` has been removed. The master-only UART, database, discovery,
continuity scan, arm/disarm, fire, and set-group helpers now live in
`master_main.py`.

## E77 radio firmware update

The E77 radio firmware can enter the STM32WL ROM bootloader from its normal
UART command port. After the radio firmware with bootloader support has been
installed once, update over UART with:

```powershell
python3 scripts\update_e77_radio_firmware.py --port COM14 C:\Users\hog28\OneDrive\Documents\NightfirePY\Nightfire-E77\build\debug\e77_radio.elf
```

Use `COM15` for the second radio. The script sends the radio bootloader command,
programs with `STM32_Programmer_CLI.exe`, verifies the image, then starts the
new application.
