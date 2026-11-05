# nice!nano command interface: wiring preparation

Status: firmware and app interface prepared. The switch and LEDs are not wired.
The deployed configuration leaves both GPIO assignments as `None`; no LED data
output is enabled. This stage continues to reject ARM/FIRE/script commands even
if a switch later reports its ARM position.

## Proposed connections

These are nRF GPIO numbers used by MicroPython, not numbered Pro Micro pads.
The existing bridge profile already identifies P0.06 and P0.08 as its UART pins;
the USB-only profile leaves that UART unused. Verify the matching pads on the
specific board before soldering.

| Function | Proposed GPIO | Connection |
| --- | --- | --- |
| SAFE/ARM switch input | P0.06 (`Pin(6)`) | Maintained switch to GND; open is SAFE, closed is ARM position |
| LED data | P0.08 (`Pin(8)`) | 3.3 V GPIO to a 5 V-powered AHCT buffer input, then buffer output to LED DIN |
| LED/buffer power | Separate regulated 5 V | LED +5 V and buffer VCC; size the supply for the exact LED assembly |
| Common reference | GND | Connect nice!nano, buffer and LED grounds |

Use the input's internal pull-up plus an external pull-up (for example 10 kΩ) to
the board's 3.3 V rail. An open switch or broken switch wire then reads SAFE.
The firmware reports unknown during transitions and requires 50 ms of a stable
reading. A single input cannot distinguish every wiring fault, such as a short
to ground. A reported switch position does not confirm the remote modules' arm state.

The nice!nano VCC output is 3.3 V, not the LED's 5 V supply. Keep the 5 V rail off
the GPIOs and VCC pin. Use a separate regulated supply with a common ground;
avoid joining independently powered 5 V sources. Confirm the specific LED
assembly's current requirement before choosing the supply.

A suitable data buffer is SN74AHCT125: a 5 V supply with TTL-compatible input
thresholds accepts the GPIO signal and provides the LED data voltage. Enable the
chosen channel by tying its active-low OE to GND; follow the manufacturer's
decoupling and unused-input guidance. Verify the package pinout. Place a series
data resistor near the first LED (for example 330 Ω) and supply decoupling at the
LED assembly. These are proposed bench connections, not a validated final PCB.

References:

- https://nicekeyboards.com/docs/nice-nano/pinout-schematic/
- https://nicekeyboards.com/docs/nice-nano/
- https://www.ti.com/lit/ds/symlink/sn74ahct125.pdf

## Four status pixels

| Pixel | Purpose | Behavior |
| --- | --- | --- |
| 1 | ARM switch | Green = SAFE; red = ARM; flashing amber = switch fault or unsettled reading; off = no switch configured |
| 2 | Field module arm state | Green = nothing armed through this bridge, or every armed module ACKed a unique DISARM; red = a module may be armed; amber = DISARM sent without per-module confirmation (STOP broadcast), or the bridge is holding the RF keepalive so modules can self-disarm after host loss; amber without master firmware |
| 3 | Heartbeats | Steady blue while the app's host heartbeat is current; brief green blink on each RF keepalive sent to the field (every 2.5 s); off without an app session |
| 4 | Fault | Flashing red for session expiry, radio failure, RF keepalive failure or interface faults |

Pixel 2 reflects what the bridge sent and what modules acknowledged; it is not a
live readback of each module. A green pixel 2 after host loss means the field's
self-disarm window has passed, not that each module reported DISARM. Extra
configured pixels are kept off. Brightness is limited in firmware; do not use this software
limit to determine the power supply's required capacity.

## Firmware setup after wiring

`main/nrf_bridge_config_usb.py` currently contains:

```python
ARM_SWITCH_PIN = None
STATUS_LED_PIN = None
STATUS_LED_COUNT = 4
STATUS_LED_ORDER = "RGB"
STATUS_LED_TIMING_NS = None
```

After wiring is verified, the proposed GPIO values are 6 and 8. The exact LED
model, pixel count, color order and its bit timings must be checked first.
`STATUS_LED_TIMING_NS` is `(T0H, T0L, T1H, T1L)` in nanoseconds. No timing is
guessed from the supply voltage. RGB and other channel permutations are accepted.
The native driver supports up to 16 RGB pixels and a 300 μs low reset interval;
validate that interval against the chosen product too.

The current radio's MicroPython build has no `neopixel`, `machine.bitstream`, or
SoftSPI driver. A native `nrf_radio.write_ws2811(pin, bytes, timing)` extension has
been added to this repository. Build it with the existing nRF MicroPython build
script; the prepared binary must be flashed before enabling actual LED output.
The native LED binary is not automatically flashed by the Python file installer.
If the extension is missing, an enabled LED pin reports a driver fault and is
not driven. Configuring the same GPIO for switch and LEDs is rejected.

The native function bounds the pixel count and timing values and uses the Cortex
cycle counter while interrupts are masked during data transmission. This is
prepared source, not verified electrical timing: inspect the waveform and test
USB/radio reception under LED updates before enabling an actuation workflow.

## Local USB status protocol v1

Existing AA55/CRC bridge frames remain inside the `NFUSB1` USB envelope.

- `0x18 HOST_HEARTBEAT`: four-byte host session tag; reply has no payload.
- `0x17 GET_INTERFACE_STATUS`: empty request; returns the 15-byte status below.
- `0x19 GET_KEEPALIVE_STATUS`: empty request; RF heartbeat telemetry (0.3.1+).

| Byte(s) | Meaning |
| --- | --- |
| 0 | Status version, currently 1 |
| 1 | Capability bits: switch=1, LED output ready=2, actuation-disabled interface=8, RF heartbeat telemetry=16, group settings=32 |
| 2 | Switch: unknown=0, SAFE position=1, ARM position=2 |
| 3 | LEDs: disabled=0, ready=1, config/output fault=2, native driver missing=3 |
| 4 | Host session: none=0, active=1, expired=2 |
| 5 | Fault bits: switch=1, LED=2, host expired=4, radio unavailable=8, RF heartbeat TX failure=16 |
| 6–7 | Host heartbeat age, big-endian milliseconds; 65535 if absent/saturated |
| 8–9 | Host timeout, big-endian milliseconds (3000) |
| 10 | Switch GPIO, 255 if unconfigured |
| 11 | LED GPIO, 255 if unconfigured |
| 12 | Pixel count |
| 13–14 | Saturating rejected-command count, big-endian |

The app sends heartbeats every second, including while waiting for field replies.
Pausing/disconnecting the app stops them. Firmware `usb-monitor-0.3.1` generates
the original master's NFCP `0x54` broadcast keepalive every 2500 ms while the
host session is active. It sends immediately when a session becomes active and
stops after the host session expires (3000 ms). RF keepalives are also stopped
while the radio is unavailable. They do not generate script heartbeats, ARM or
FIRE commands. The USB query allowlist still rejects caller-supplied keepalives.
The session tag is not authentication or remote arm confirmation.
The three-second watchdog is session monitoring; this diagnostics-only stage
does not claim to disarm field modules on timeout.

`GET_KEEPALIVE_STATUS` returns 14 bytes: version=1, state (0 stopped, 1 active,
2 last TX failed), interval (2 bytes), age since last successful TX (2 bytes,
65535 if never sent), successful TX count (4 bytes), failure count (4 bytes).
All multibyte values are big-endian. Broadcast TX success does not acknowledge
receipt by any field module. `scripts/probe_master_keepalive.py --port COM12`
checks the cadence and verifies that the count stops advancing after host loss.

Firmware 0.3.2 permits only the unique-address SET_GROUP (0x31) configuration
command with exactly two group bytes, while the USB host session is active.
Other configuration and actuation commands remain blocked. Field firmware
requires disarmed state and persists the group before acknowledging it. The app
waits for that command's ACK from the target UID and confirms discovery readback.
