NETWORK_ID = 0x04D2
RADIO_CHANNEL = 90
RADIO_ADDRESS = 0x67676767
RADIO_TX_POWER = 8
# Command interface, wired and checked on the COM12 bench bridge 2026-11-08
# (docs/command_interface_wiring.md). Set the pins to None on a bridge
# without the switch and LEDs.
ARM_SWITCH_PIN = 6            # P0.06, maintained switch to GND: closed = ARM
# Status LEDs. These are the defaults for a NEW bridge; an installed bridge
# keeps its own copy. Other LED types (WS2811, SK6812...) need other order and
# timing values: see docs/bridge_status_leds.md and
# scripts/install/set_bridge_leds.py.
STATUS_LED_PIN = 8            # P0.08, LED data (direct wire)
STATUS_LED_COUNT = 4
# WS2811 (COM12 bench bridge, 2026-11-09): RGB order. 250 ns T0H read every
# bit as 1 (all white); 150 ns decodes. For WS2812B use "GRB" and
# (250, 1000, 800, 450).
STATUS_LED_ORDER = 'RGB'
STATUS_LED_TIMING_NS = (150, 1100, 550, 700)   # (T0H, T0L, T1H, T1L) ns
STATUS_LED_POWER_PIN = 13     # P0.13 switches the nice!nano VCC pin
STATUS_LED_POWER_ON_VALUE = 1 # high = LED power on (measured on the bench)
STATUS_LED_BRIGHTNESS = 12    # 1-255; also adjustable in the app's Settings
