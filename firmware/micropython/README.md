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

This extension is flashed on the COM12 bench bridge and drives its four
WS2812B status pixels (GRB, timing `(250, 1000, 800, 450)`; a 400 ns T0H
read as all-ones on these LEDs). See
`docs/command_interface_wiring.md` for the interface protocol, proposed connections,
and required waveform/USB/radio bench checks.

### Background receive (2026-11-23 build and later)

The radio listens all the time after `init()`, not only while Python waits
in `recv()`. It goes back into RX right after every `recv()`, `send()`,
`channel()` and `noise_sample()`. A packet that arrives while Python is busy
(reading USB, handling another packet, sleeping) is kept by the RADIO
interrupt in a ring of 4, and the next `recv()` returns it at once, oldest
first. `recv(timeout)` only waits when nothing is held. The Python API is
unchanged.

- When 4 packets are already held, a new one is dropped and counted in
  `stats()["rx_dropped"]`. `stats()["rx_held"]` is how many are held now.
- `rx_ok` counts packets `recv()` returned; `rx_crc_error` and `rx_bad_len`
  count bad packets as they arrive. A CRC error no longer ends a `recv()`
  early: it keeps waiting for a good packet until its timeout.
- `init()` and `channel()` drop held packets. `noise_sample()` keeps them.
- `send()` with `cca_ms` samples the channel with the listening receiver,
  and a packet on the air counts as busy. Without CCA, `send()` lets a
  packet that is already on the air finish (at most about 2 ms), keeps it,
  then transmits.
- `last_rssi()` is the RSSI of the last packet `recv()` returned, as before.

Older builds enable the receiver only inside `recv()` and turn it off on
return, so a board is deaf whenever Python is doing anything else.

`last_rssi()` is the RSSI of the last received packet. While the receiver
is listening, the radio's `ADDRESS_RSSISTART` / `DISABLED_RSSISTOP` shortcuts
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
- `rx_dropped` (2026-11-23 build: good packets dropped, receive ring full)
- `rx_held` (2026-11-23 build: packets held right now)
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
python .\scripts\install\enter_nrf_bootloader.py --port COM16
```

Use the VS Code task `nRF dummy: USB bootloader` for the current COM16 dummy.

## nRF dummy identity

The nRF dummy config files use `UNIQUE_ID = 0x00000000` by default. A zero UID
means the runtime derives the module UID from the nRF FICR device ID through
`nrf_radio.device_id32()`. Set `UNIQUE_ID` to any nonzero 32-bit value to force a
manual override; setting it back to zero returns the module to hardware-derived
identity.

## Building and flashing (current setup)

`scripts/build/build_nrf52840_micropython.ps1` builds the `NIGHTFIRE` board with this
module, once per name (`-Device RADIO|FIELD|AUDIO`, default all three),
and copies each to `firmware/nrf52840/nightfire_<device>_micropython.uf2`
and `.hex`. The board header picks the name from `NIGHTFIRE_DEVICE`:
Nightfire Radio (bridge), Nightfire Field (field modules and dummies),
Nightfire Audio (audio box). The USB VID/PID stay 239A:00B3; the app finds
the bridge by those, not by name. It uses the
MicroPython checkout and ARM toolchain in `NightfirePY/nrf-micropython` or
`NightfirePY/e77-micropython` (commit `be9a819`). Don't pipe its output
through `*>&1` in Windows PowerShell 5.1: the linker's warnings go to
stderr and abort the script, even though the build is fine.

To flash a board:
1. Put it in UF2 mode: `py -3 scripts\install\enter_nrf_bootloader.py --port COMx`.
2. `powershell -ExecutionPolicy Bypass -File scripts\install\flash_nrf52840_uf2.ps1 -Device radio|field|audio`
   copies that image to the drive that appears (`INFO_UF2.TXT` names the
   bootloader, which still says nice!nano).

### Flashing the COM12 bench bridge (nice!nano, UF2 bootloader)

The bridge's files (its app, `nrf_node_config.py`, LED settings) stay on
the board: the 2026-11-23 build has the same filesystem layout
(`FS_SIZE = 0x80000`, `nightfire_1M_256k.ld`, called `nicenano_1M_256k.ld` before 2026-11-25)
as the 2026-11-05/06 builds.

1. Close anything that holds COM12 (the Android app, a serial monitor).
2. Put the bridge in UF2 mode, either way:
   - `py -3 scripts\install\enter_nrf_bootloader.py --port COM12`
     (stops the bridge app over USB and runs `machine.bootloader()`), or
   - double-tap the nice!nano's reset pad (RST to GND twice, quickly).

   A drive appears whose `INFO_UF2.TXT` names the nice!nano bootloader,
   and COM12 goes away.
3. Copy the image:
   `powershell -ExecutionPolicy Bypass -File scripts\install\flash_nrf52840_uf2.ps1 -Device radio`
   (copies `firmware\nrf52840\nightfire_radio_micropython.uf2`; pass
   `-Uf2 <file>` for another image). The drive disappears and the board
   restarts on its own, back on COM12, running the bridge app.
4. Check it is the image you meant: in a REPL, `import os; os.uname()`
   shows the build date, and on the background-receive build
   `nrf_radio.stats()` has an `rx_dropped` key.

To go back, flash `firmware\nrf52840\nightfire_micropython_2026-11-06_baseline.uf2`
the same way (`-Uf2` with that path). That is the image built 2026-11-06,
before background receive. It still calls itself nice!nano.

### Bench plan: background receive A/B (bridge only)

Radio-timing changes ship only after an A/B on hardware. Old = the
2026-11-06 baseline image, new = the 2026-11-23 image; same bridge app
(usb-master-0.4.6) on both, field module `D96E30DD` powered and in place,
everything in one session (radio conditions drift over minutes).

1. Before flashing anything, note the bridge app bundle size on COM12
   (bridge app updates have rolled back silently before) and that
   `os.uname()` shows the 2026-11-06 build.
2. Flash the new image (steps above). Smoke test:
   `py -3 scripts/diagnostics/mesh_hw_smoke.py --bridge COM12`.
   Must pass as it does on the old image. Stop here if it does not.
3. Alternate, 300 reliable pings per run, at least 3 runs each:
   new, old, new, old, new, old. Each run:
   `py -3 scripts/diagnostics/reliable_ping_load.py --bridge COM12 --uid D96E30DD --count 300`.
   Reflash between runs (about a minute each). Write down retries, median
   and p95 round trip.
4. After the last run on the new image, read `nrf_radio.stats()` in a REPL
   (`py -3 scripts/diagnostics/read_nrf_radio_stats.py COM12`): `rx_dropped`
   should be 0 or near it; a steady count means the ring of 4 is too small
   for the traffic.
5. Leave the bridge on whichever image wins. Ship the new one only if its
   retries are lower than 0.4% (the 0.4.6 figure) or at least no worse,
   with no smoke-test failures. Field modules and dummies come after, one
   board at a time.

Expected: retries well under 0.4%, and the median round trip a few ms
shorter, because a module's first reply no longer lands while the bridge
reads USB.

Boards on the 2026-11-05 build or later keep their files. A board on an
older build (for example 2026-09-16) has a different filesystem layout and
comes back empty, so back it up first and reinstall its app afterwards.

## Build direction

Create or use a MicroPython `ports/nrf` checkout and build the pca10056/nRF52840
target with:

```text
USER_C_MODULES=<repo>/firmware/micropython/native/nrf_radio
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
