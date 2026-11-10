# nice!nano command interface: wiring preparation

Status: wired and checked on the COM12 bench bridge (nice!nano) on 2026-11-08.
The switch reads SAFE open / ARM closed, the four WS2812B pixels show the status
below, and the mesh smoke test passes 11/11 with the LEDs running (ping times
are the same with LED output switched off).

As built on the bench:

| Function | GPIO | Connection |
| --- | --- | --- |
| SAFE/ARM switch | P0.06 | Switch to GND; reads 1 open (SAFE), 0 closed (ARM) |
| LED data | P0.08 | Direct wire to the first WS2812B's DIN (no buffer) |
| LED power | P0.13 | The nice!nano's VCC switch; **high = on** (low left the LEDs dark) |

The LEDs run from the board's 3.3 V VCC pin, so a 3.3 V data line drives them
directly. Bench findings: colour order is GRB, and the WS2812B datasheet
timing with a 400 ns "0" pulse read every bit as 1 (all pixels white); a
250 ns "0" pulse decodes correctly.

On 2026-11-09 the bench bridge's LEDs were changed to WS2811s, which need RGB
order and timing `(150, 1100, 550, 700)` (250 ns read as all white). For any
other LED type, see `docs/bridge_status_leds.md`.

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

## ARM key safety latch (usb-master-0.4.3+)

The ARM key must be turned to SAFE before it can arm anything:

- **At power-on:** if the key is already on ARM when the bridge starts, the
  bridge latches. Pixel 1 flashes yellow.
- **When the app connects:** a new app session latches the same way if the
  key is on ARM. A new session is a new heartbeat tag (the app picks one on
  every connect, so a quick reconnect counts) or a session that expired
  (more than 3 s without app heartbeats) and comes back.
- **While latched,** the bridge treats the key as SAFE: it refuses ARM, FIRE,
  START and STEP, and reports fault bit 32. The app shows a "Turn the ARM key
  off" popup and disables its Arm buttons. The popup can be hidden, but arming
  stays blocked.
- **Turning the key to SAFE** (a reading steady for 50 ms) clears the latch.
  Turning it back to ARM then works normally.

Check it on a bridge with
`py -3 scripts/diagnostics/probe_arm_key_latch.py --port COM12 --try-arm`. It
holds an app-style session, prints the switch and latch, and asks the bridge to
ARM a module UID that doesn't exist, to show whether ARM is accepted or refused.

The app also latches by itself when its first status after connecting shows
the key on ARM, so older bridge firmware gets the popup and the app-side
block too (but not the yellow light or the bridge-side refusal).

## Four status pixels

| Pixel | Purpose | Behavior |
| --- | --- | --- |
| 1 | ARM switch | Green = SAFE; red = ARM; **flashing yellow = ARM key was on at power-on or when the app connected (turn it to SAFE)**; flashing amber = switch fault or unsettled reading; off = no switch configured |
| 2 | Field module arm state | Green = nothing armed through this bridge, or every armed module ACKed a unique DISARM; red = a module may be armed; amber = DISARM sent without per-module confirmation (STOP broadcast), or the bridge is holding the RF keepalive so modules can self-disarm after host loss; amber without master firmware |
| 3 | Heartbeats | Steady blue while the app's host heartbeat is current; brief green blink on each RF keepalive sent to the field (every 2.5 s); off without an app session |
| 4 | Fault | Flashing red for session expiry, radio failure, RF keepalive failure or interface faults |

Pixel 2 reflects what the bridge sent and what modules acknowledged; it is not a
live readback of each module. A green pixel 2 after host loss means the field's
self-disarm window has passed, not that each module reported DISARM. Extra
configured pixels are kept off. Brightness is limited in firmware; do not use this software
limit to determine the power supply's required capacity.

## Firmware setup

`main/configs/bridge_usb.py` now contains the bench wiring:

```python
ARM_SWITCH_PIN = 6
STATUS_LED_PIN = 8
STATUS_LED_COUNT = 4
STATUS_LED_ORDER = "RGB"                       # WS2811; "GRB" for WS2812B
STATUS_LED_TIMING_NS = (150, 1100, 550, 700)   # WS2811; (250, 1000, 800, 450) for WS2812B
STATUS_LED_POWER_PIN = 13
STATUS_LED_POWER_ON_VALUE = 1
```

Set the pins to `None` for a bridge without a switch or LEDs. The power pin is
driven to its on value only when the LED settings are valid, and it must not
share a GPIO with the switch or LED data. `install_usb_bridge.py --master`
keeps a bridge's own config; a bridge whose switch and LED pins are both unset
takes this whole switch/LED block from the repository config. For other LEDs,
check the model, pixel count, colour order and bit timings first.
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
| 5 | Fault bits: switch=1, LED=2, host expired=4, radio unavailable=8, RF heartbeat TX failure=16, ARM key latched=32 (usb-master-0.4.3+, see below) |
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
receipt by any field module. `scripts/diagnostics/probe_master_keepalive.py --port COM12`
checks the cadence and verifies that the count stops advancing after host loss.

Firmware 0.3.2 permits only the unique-address SET_GROUP (0x31) configuration
command with exactly two group bytes, while the USB host session is active.
Other configuration and actuation commands remain blocked. Field firmware
requires disarmed state and persists the group before acknowledging it. The app
waits for that command's ACK from the target UID and confirms discovery readback.
