# Nightfire nRF52840 Prototype

This folder holds the nRF52840 Pro Micro proof-of-concept pieces.

The goal is to test Nightfire/NFCP over the nRF52840 proprietary 2.4 GHz RADIO
peripheral without BLE and without direct `machine.mem32` register work in
Python.

## Native radio module

`native/nrf_radio/` is a MicroPython user C module. It exposes a small Python
API:

```python
import nrf_radio

nrf_radio.init(channel=40, tx_power=8, address=0xE7E7E7E7)
nrf_radio.send(b"payload")
nrf_radio.send(b"payload", cca_ms=2, busy_dbm=-85, backoff_ms=5)
packet = nrf_radio.recv(100)
rssi = nrf_radio.last_rssi()
device_uid = nrf_radio.device_id32()
device_id = nrf_radio.device_id()
noise = nrf_radio.noise_sample(74, duration_ms=45, interval_ms=3, busy_dbm=-85)
stats = nrf_radio.stats()
nrf_radio.clear_stats()
```

The first driver uses Nordic proprietary 1 Mbps mode, hardware packet length,
hardware CRC, whitening, and +8 dBm TX power by default.

### Prepared WS2811 output extension

`nrf_radio.write_ws2811(gpio, rgb_bytes, timing_ns)` sends 1–16 RGB pixels using
four explicit bit timing values `(T0H, T0L, T1H, T1L)` in nanoseconds. Pixel order
is handled by the caller. The extension bounds the transfer length, uses the
Cortex cycle counter, masks interrupts during the data waveform and leaves data
low for 300 μs afterward. It is intended for status indicators between radio
receive windows, not large animated strips.

The nice!nano build with this extension compiles successfully. It has not been
flashed to the connected radio or verified on actual LEDs. The Python command
interface leaves LED and switch GPIOs disabled until wiring is checked. See
`docs/command_interface_wiring.md` for the interface protocol, proposed connections,
and required waveform/USB/radio bench checks.

`last_rssi()` is the RSSI of the last received packet. While `recv()` is
listening, the radio's `ADDRESS_RSSISTART` / `DISABLED_RSSISTOP` shortcuts
sample it during the packet itself. Before 2026-11-06 the driver sampled
as soon as the receiver was ready, so it always returned the noise floor
(about -100 dBm), and CCA / `noise_sample()` overwrote it too. Those
checks now keep their samples to themselves.

`stats()` returns driver counters and current configuration values:

- `tx_ok`
- `tx_timeout`
- `tx_busy`
- `rx_ok`
- `rx_timeout`
- `rx_crc_error`
- `rx_bad_len`
- `cca_ok`
- `cca_busy`
- `last_rssi`
- `channel`
- `tx_power`
- `address`
- `max_payload`

Use `clear_stats()` before a test run when you want clean counters.

The bridge exposes the same counters to the master over the local UART:

- `CMD_GET_STATS` returns packed TX/RX/error/config counters.
- `CMD_CLEAR_STATS` clears the counters before a focused test.

The optional `cca_ms` send argument performs a clear-channel check before
transmitting. If the channel RSSI is at or above `busy_dbm`, `send()` returns
`False` without transmitting and increments `tx_busy` / `cca_busy`. Existing
`send(payload)` calls do not perform CCA and keep their original behavior.

`noise_sample()` temporarily tunes the radio to the requested channel, samples
RSSI, then restores the previous channel. It returns `avg`, `min`, `max`,
`count`, and `busy_count`, where `busy_count` is the number of samples at or
above `busy_dbm`.

`device_id32()` returns the low 32-bit word from the nRF FICR `DEVICEID` block
and is used as the automatic module UID. `device_id()` returns both 32-bit FICR
words for diagnostics.

## USB bootloader helper

Direct USB-connected nRF boards can be placed into UF2 bootloader mode from the
PC. The helper interrupts the running MicroPython app, enters raw REPL, and
executes `machine.bootloader()`:

```powershell
python .\scripts\enter_nrf_bootloader.py --port COM16
```

Use the VS Code task `nRF dummy: USB bootloader` for the current COM16 dummy.

## nRF dummy identity

The nRF dummy config files use `UNIQUE_ID = 0x00000000` by default. A zero UID
means the runtime derives the module UID from the nRF FICR device ID through
`nrf_radio.device_id32()`. Set `UNIQUE_ID` to any nonzero 32-bit value to force a
manual override; setting it back to zero returns the module to hardware-derived
identity.

## Building and flashing (current setup)

`scripts/build_nrf52840_micropython.ps1` builds the NICENANO board with this
module and copies the result to `firmware/nrf52840/`. It uses the
MicroPython checkout and ARM toolchain in `NightfirePY/nrf-micropython` or
`NightfirePY/e77-micropython` (commit `be9a819`). Don't pipe its output
through `*>&1` in Windows PowerShell 5.1: the linker's warnings go to
stderr and abort the script, even though the build is fine.

To flash a board:
1. Put it in UF2 mode: `py -3 scripts\enter_nrf_bootloader.py --port COMx`.
2. Copy `firmware/nrf52840/nightfire_nicenano_micropython.uf2` to the drive
   that appears (`INFO_UF2.TXT` names the board).

Boards on the 2026-11-05 build or later keep their files. A board on an
older build (for example 2026-09-16) has a different filesystem layout and
comes back empty, so back it up first and reinstall its app afterwards.

## Build direction

Create or use a MicroPython `ports/nrf` checkout and build the pca10056/nRF52840
target with:

```text
USER_C_MODULES=<repo>/nrf52840/native/nrf_radio
```

The connected Pro Micro currently appears as a UF2 bootloader with:

```text
Board-ID: nRF52840-pca10056-v1
Model: Nordic nRF52840 DK
```

So the first firmware target should be the pca10056-style nRF52840 board unless
we discover the Pro Micro needs a custom pin map.

## Prototype app

`main/nrf_dummy_main.py` is the dummy module test app. It wraps NFCP frames in a
simple radio payload:

```text
network_id(2 bytes, big-endian) | nfcp_frame
```

That mirrors the current bridge payload shape so the master-side transport can
stay small and predictable.
