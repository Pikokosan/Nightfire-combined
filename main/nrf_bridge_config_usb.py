NETWORK_ID = 0x04D2
RADIO_CHANNEL = 90
RADIO_ADDRESS = 0x67676767
RADIO_TX_POWER = 8
# Command interface, wired and checked on the COM12 bench bridge 2026-11-08
# (docs/command_interface_wiring.md). Set the pins to None on a bridge
# without the switch and LEDs.
ARM_SWITCH_PIN = 6            # P0.06, maintained switch to GND: closed = ARM
STATUS_LED_PIN = 8            # P0.08, WS2812B data (direct wire, LEDs on 3.3 V VCC)
STATUS_LED_COUNT = 4
STATUS_LED_ORDER = "GRB"
# (T0H, T0L, T1H, T1L) ns. A 400 ns T0H read every bit as 1 (all white) on
# these LEDs; 250 ns decodes correctly.
STATUS_LED_TIMING_NS = (250, 1000, 800, 450)
STATUS_LED_POWER_PIN = 13     # P0.13 switches the nice!nano VCC pin
STATUS_LED_POWER_ON_VALUE = 1 # high = LED power on (measured on the bench)
