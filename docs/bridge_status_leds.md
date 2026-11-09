# Bridge status LEDs

The USB bridge (the dongle plugged into the tablet) can drive a short chain of
addressable RGB LEDs (WS2812B, WS2811, SK6812 and similar) to show its state.
What each pixel means is in `docs/command_interface_wiring.md`.

Different LED types need different settings. If you change the LEDs, change
the settings to match.

## Where the settings live

| Copy | File | Used for |
|---|---|---|
| On the bridge | `nrf_bridge_config.py` on the board | What the bridge actually uses |
| In the repository | `main/configs/bridge_usb.py` | The starting config for a **new** bridge |

Reinstalling or updating the bridge firmware keeps the bridge's own
`nrf_bridge_config.py`, so **editing the repository file does not change a
bridge that is already set up.** Use `scripts/install/set_bridge_leds.py` (below)
to change a bridge that is already set up.

The settings:

```python
STATUS_LED_PIN = 8            # LED data GPIO (P0.08). None = no LEDs.
STATUS_LED_COUNT = 4          # pixels on the data line, 4 to 16 (extras stay off)
STATUS_LED_ORDER = "GRB"      # the order the LED expects its colour bytes
STATUS_LED_TIMING_NS = (250, 1000, 800, 450)   # (T0H, T0L, T1H, T1L) in ns
STATUS_LED_POWER_PIN = 13     # GPIO that switches LED power (nice!nano VCC). None = not used.
STATUS_LED_POWER_ON_VALUE = 1 # level that turns LED power on
```

Each bit sent to the LEDs is a high pulse followed by a low gap. A short high
pulse (`T0H`) means 0 and a long one (`T1H`) means 1. The bridge's driver makes
the high pulses somewhat longer than the values asked for. On the bench
WS2812Bs, asking for the datasheet's 400 ns `T0H` already read as a 1. So the
right values are usually below the datasheet figures, and the test below is
how to find them.

## Presets

| Preset | Order | Timing (ns) | Status |
|---|---|---|---|
| `ws2812b` | GRB | 250, 1000, 800, 450 | Tested on the COM12 bench bridge (until 2026-11-08) |
| `ws2811` | RGB | 150, 1100, 550, 700 | Tested on the COM12 bench bridge, 2026-11-09 (the repository default now) |
| `ws2811-400k` | RGB | 350, 2000, 1100, 1300 | WS2811 chips wired for slow (400 kHz) mode |
| `sk6812` | GRB | 200, 1000, 600, 650 | Starting point; not yet tested |

WS2811 strings and modules vary: many are RGB, some are BRG or GRB. The test
pattern shows which.

## Changing the LEDs on a bridge

Plug the bridge into the PC, close the app's relay and any serial terminal
that has its port open, and find its COM port (Device Manager, or
`[System.IO.Ports.SerialPort]::GetPortNames()` in PowerShell).

1. See the current settings:

   ```powershell
   py -3 scripts/install/set_bridge_leds.py --port COM12
   ```

2. Light the test pattern with a preset (nothing is saved yet):

   ```powershell
   py -3 scripts/install/set_bridge_leds.py --port COM12 --preset ws2811 --test
   ```

   You should see **pixel 1 red, pixel 2 green, pixel 3 blue, pixel 4 white**.

   | What you see | What it means | Try |
   |---|---|---|
   | All pixels white | Every bit reads as 1: the 0 pulse is too long | A smaller first timing value, e.g. `--timing 120,1100,550,700` |
   | Dark, dim or missing colours | 1s read as 0: the 1 pulse is too short | A larger third value, e.g. `--timing 150,1100,700,600` |
   | Right brightness, wrong colours | The colour order is wrong | Another `--order`, e.g. `GRB` or `BRG` |
   | Nothing at all | Wiring, power or data voltage | See wiring below |
   | Only the first pixel works | The pixels are fine but the chain is broken, or `--count` is too small | Check DOUT→DIN between pixels |

   Any preset value can be overridden: `--order`, `--timing T0H,T0L,T1H,T1L`
   (each 100 to 5000 ns), `--count`, `--pin`, `--power-pin`.

3. When the pattern is right, save the same options with `--save` instead of
   `--test`:

   ```powershell
   py -3 scripts/install/set_bridge_leds.py --port COM12 --preset ws2811 --timing 120,1100,550,700 --save
   ```

   The bridge restarts and shows its status colours. The previous config is
   kept in `dist/bridge_backup_<time>/`. Add `--repo` to also write the
   settings into `main/configs/bridge_usb.py`, so new bridges start with them.

## Wiring notes for other LEDs

- **Data voltage.** The nice!nano's data pin is 3.3 V. LEDs run from the
  board's 3.3 V VCC (as on the bench bridge) read it directly. LEDs on a 5 V
  supply usually need a level shifter (for example a 74AHCT125) between P0.08
  and DIN. Without one they may flicker or stay dark.
- **12 V WS2811 strings** need their own 12 V supply. Join the supply's ground
  to the bridge's ground, and never connect 12 V or 5 V to a nice!nano pin.
- **Power pin.** `STATUS_LED_POWER_PIN = 13` switches only the nice!nano's own
  3.3 V VCC output. With LEDs on a separate supply, pass `--power-pin none`
  unless something else on VCC needs it.
- Put a 330 Ω resistor in the data line near the first LED, and a capacitor
  across the LED supply.

See `docs/command_interface_wiring.md` for the switch, the full pin table and
what each pixel shows.
