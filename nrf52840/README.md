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

That mirrors the current E77 payload shape so the master-side transport can be
adapted cleanly later.
