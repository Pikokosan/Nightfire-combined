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
packet = nrf_radio.recv(100)
rssi = nrf_radio.last_rssi()
```

The first driver uses Nordic proprietary 1 Mbps mode, hardware packet length,
hardware CRC, whitening, and +8 dBm TX power by default.

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
