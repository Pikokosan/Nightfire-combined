import sys
import unittest
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "main"))
from command_interface import CommandInterface


class InputPin:
    def __init__(self):
        self.raw = 1
        self.failed = False

    def value(self):
        if self.failed:
            raise OSError("pin failure")
        return self.raw


class InterfaceTests(unittest.TestCase):
    def make(self, **settings):
        self.now = 0
        self.pin = InputPin()
        self.writes = []
        return CommandInterface(SimpleNamespace(**settings), pin_factory=lambda number: self.pin,
                                led_write=lambda *values: self.writes.append(values),
                                clock=lambda: self.now, difference=lambda current, previous: current - previous)

    def test_unconfigured_pins_never_write_and_arm_stays_unknown(self):
        interface = self.make()
        interface.poll(True)
        self.assertEqual(interface.switch, 0)
        self.assertEqual(interface.payload()[10:12], b"\xff\xff")
        self.assertFalse(self.writes)

    def test_switch_debounce_and_input_failure(self):
        interface = self.make(ARM_SWITCH_PIN=6)
        interface.poll(True)
        self.now = 50
        interface.poll(True)
        self.assertEqual(interface.switch, 1)
        self.pin.raw = 0
        interface.poll(True)
        self.assertEqual(interface.switch, 0)
        self.now = 100
        interface.poll(True)
        self.assertEqual(interface.switch, 2)
        self.pin.failed = True
        interface.poll(True)
        self.assertEqual(interface.switch, 0)
        self.assertTrue(interface.faults & 1)

    def settle(self, interface, raw):
        self.pin.raw = raw
        interface.poll(True)
        self.now += 60
        interface.poll(True)

    def test_key_on_at_power_on_latches_until_turned_to_safe(self):
        interface = self.make(ARM_SWITCH_PIN=6, STATUS_LED_PIN=8, STATUS_LED_TIMING_NS=(150, 1100, 550, 700))
        self.settle(interface, 0)                       # ARM at power-on
        self.assertEqual(interface.switch, 2)
        self.assertTrue(interface.key_latched())
        self.assertEqual(interface.arm_switch(), 1)     # ARM/FIRE refused
        self.assertTrue(interface.payload()[5] & 32)
        # Pixel 1 flashes yellow; the fault pixel stays dark for the latch.
        self.now = 1000
        interface.poll(True)
        self.assertEqual(self.writes[-1][1][:3], bytes((10, 9, 0)))
        self.assertEqual(self.writes[-1][1][9:], b"\x00\x00\x00")
        self.now = 1500
        interface.poll(True)
        self.assertEqual(self.writes[-1][1][:3], b"\x00\x00\x00")
        # SAFE clears the latch; turning the key back to ARM then counts.
        self.settle(interface, 1)
        self.assertFalse(interface.key_latched())
        self.assertFalse(interface.payload()[5] & 32)
        self.settle(interface, 0)
        self.assertEqual(interface.arm_switch(), 2)
        self.assertFalse(interface.key_latched())

    def test_key_on_when_the_app_connects_latches(self):
        interface = self.make(ARM_SWITCH_PIN=6)
        self.settle(interface, 1)
        self.settle(interface, 0)                       # ARM before any app session
        self.assertEqual(interface.arm_switch(), 2)
        interface.heartbeat(b"1234")                    # the app connects
        interface.poll(True)
        self.assertTrue(interface.key_latched())
        self.assertEqual(interface.arm_switch(), 1)
        # Further heartbeats of the same session don't re-latch after SAFE.
        self.settle(interface, 1)
        self.settle(interface, 0)
        interface.heartbeat(b"1234")
        interface.poll(True)
        self.assertEqual(interface.arm_switch(), 2)
        # A different app session within the 3 s timeout is a new connection.
        interface.heartbeat(b"9999")
        interface.poll(True)
        self.assertEqual(interface.arm_switch(), 1)
        self.settle(interface, 1)
        self.settle(interface, 0)
        self.assertEqual(interface.arm_switch(), 2)
        # So is a session that expired and comes back.
        self.now += 3001
        interface.poll(True)
        interface.heartbeat(b"9999")
        interface.poll(True)
        self.assertEqual(interface.arm_switch(), 1)

    def test_key_safe_at_power_on_does_not_latch(self):
        interface = self.make(ARM_SWITCH_PIN=6)
        self.settle(interface, 1)
        interface.heartbeat(b"1234")
        interface.poll(True)
        self.assertFalse(interface.key_latched())
        self.settle(interface, 0)
        self.assertEqual(interface.arm_switch(), 2)

    def test_no_switch_means_no_latch(self):
        interface = self.make()
        interface.heartbeat(b"1234")
        interface.poll(True)
        self.assertIsNone(interface.arm_switch())
        self.assertFalse(interface.payload()[5] & 32)

    def test_heartbeat_expires_and_invalid_message_does_not_renew_it(self):
        interface = self.make()
        self.assertTrue(interface.heartbeat(b"1234"))
        self.now = 3001
        self.assertFalse(interface.heartbeat(b"123"))
        interface.poll(True)
        self.assertEqual(interface.session_state(), 2)
        self.assertTrue(interface.faults & 4)
        interface.heartbeat(b"5678")
        interface.poll(True)
        self.assertEqual(interface.session_state(), 1)
        self.assertFalse(interface.faults & 4)

    def test_status_pixel_colors_and_no_redundant_writes(self):
        interface = self.make(STATUS_LED_PIN=8, STATUS_LED_TIMING_NS=(300, 950, 900, 350))
        interface.heartbeat(b"1234")
        interface.record_rx()
        interface.poll(True)
        # No switch configured: off. Arm state unknown: amber. Session: blue.
        self.assertEqual(self.writes[0][1], bytes([0, 0, 0, 10, 5, 0, 0, 0, 12, 0, 0, 0]))
        interface.poll(True)
        self.assertEqual(len(self.writes), 1)
        self.now = 4000
        interface.poll(False)
        self.assertEqual(self.writes[-1][1][-3:], b"\x0c\x00\x00")

    def pixels(self, interface):
        interface.poll(True)
        return [tuple(self.writes[-1][1][i:i + 3]) for i in range(0, 12, 3)]

    def test_pixel_one_shows_the_arm_switch(self):
        interface = self.make(ARM_SWITCH_PIN=6, STATUS_LED_PIN=8,
                              STATUS_LED_TIMING_NS=(300, 950, 900, 350))
        self.now = 500  # Unknown while settling; flash phase is off here.
        self.assertEqual(self.pixels(interface)[0], (0, 0, 0))
        self.now = 600
        self.assertEqual(self.pixels(interface)[0], (0, 10, 0))  # SAFE
        self.pin.raw = 0
        self.pixels(interface)
        self.now = 700
        self.assertEqual(self.pixels(interface)[0], (12, 0, 0))  # ARM
        self.pin.failed = True
        self.now = 2000
        self.assertEqual(self.pixels(interface)[0], (10, 5, 0))  # fault flashes amber

    def test_pixel_two_shows_field_arm_state(self):
        interface = self.make(STATUS_LED_PIN=8, STATUS_LED_TIMING_NS=(300, 950, 900, 350))
        for state, color in ((0, (0, 10, 0)), (1, (12, 0, 0)), (2, (10, 5, 0)), (None, (10, 5, 0))):
            interface.module_arm = state
            self.assertEqual(self.pixels(interface)[1], color, state)

    def test_pixel_three_shows_heartbeats(self):
        interface = self.make(STATUS_LED_PIN=8, STATUS_LED_TIMING_NS=(300, 950, 900, 350))
        self.assertEqual(self.pixels(interface)[2], (0, 0, 0))  # no app session
        interface.heartbeat(b"1234")
        self.assertEqual(self.pixels(interface)[2], (0, 0, 12))  # app heartbeat
        interface.keepalive_at = self.now = 500
        self.assertEqual(self.pixels(interface)[2], (0, 10, 0))  # RF keepalive blink
        self.now = 700
        self.assertEqual(self.pixels(interface)[2], (0, 0, 12))

    def test_brightness_scales_colours_and_rewrites_pixels(self):
        interface = self.make(STATUS_LED_PIN=8, STATUS_LED_TIMING_NS=(300, 950, 900, 350))
        self.assertEqual(interface.brightness, 12)
        self.assertTrue(interface.payload()[1] & 64)      # brightness command
        interface.heartbeat(b"1234")
        self.assertEqual(self.pixels(interface)[2], (0, 0, 12))
        self.assertTrue(interface.set_brightness(255))
        self.assertEqual(self.pixels(interface)[1], (212, 106, 0))   # amber
        self.assertEqual(self.pixels(interface)[2], (0, 0, 255))
        writes = len(self.writes)
        self.assertTrue(interface.set_brightness(1))      # dimmest: never fully off
        self.assertEqual(self.pixels(interface)[1], (1, 1, 0))
        self.assertEqual(len(self.writes), writes + 1)
        for bad in (0, 256, -1, "12", None):
            self.assertFalse(interface.set_brightness(bad))
        self.assertEqual(interface.brightness, 1)

    def test_brightness_from_config_and_invalid_falls_back(self):
        self.assertEqual(self.make(STATUS_LED_BRIGHTNESS=80).brightness, 80)
        self.assertEqual(self.make(STATUS_LED_BRIGHTNESS=0).brightness, 12)
        self.assertEqual(self.make(STATUS_LED_BRIGHTNESS="x").brightness, 12)

    def test_invalid_configuration_and_output_fault_do_not_escape(self):
        for settings in ({"STATUS_LED_PIN": 8, "STATUS_LED_ORDER": None},
                         {"ARM_SWITCH_PIN": 6, "STATUS_LED_PIN": 6},
                         {"STATUS_LED_PIN": 99}, {"ARM_SWITCH_PIN": -1}):
            interface = self.make(**settings)
            interface.poll(True)
            self.assertNotEqual(interface.faults, 0)
            self.assertEqual(len(interface.payload()), 15)
            self.assertFalse(self.writes)
        interface = self.make(STATUS_LED_PIN=8, STATUS_LED_TIMING_NS=(300, 950, 900, 350))
        def failed_write(*values):
            raise OSError("LED failure")
        interface.led_write = failed_write
        interface.poll(True)
        self.assertEqual(interface.led_status, 2)

    def make_powered(self, **settings):
        self.outputs = []
        self.now = 0
        self.pin = InputPin()
        self.writes = []
        return CommandInterface(SimpleNamespace(**settings), pin_factory=lambda number: self.pin,
                                led_write=lambda *values: self.writes.append(values),
                                clock=lambda: self.now, difference=lambda current, previous: current - previous,
                                output_factory=lambda number, value: self.outputs.append((number, value)))

    def test_led_power_pin_is_driven_on_only_with_valid_led_output(self):
        led = dict(STATUS_LED_PIN=8, STATUS_LED_TIMING_NS=(250, 1000, 800, 450))
        interface = self.make_powered(STATUS_LED_POWER_PIN=13, **led)
        self.assertEqual(self.outputs, [(13, 1)])
        self.assertEqual(interface.led_status, 1)
        self.make_powered(STATUS_LED_POWER_PIN=13, STATUS_LED_POWER_ON_VALUE=0, **led)
        self.assertEqual(self.outputs, [(13, 0)])
        # Invalid LED settings, or a power pin shared with another signal,
        # leave the power pin untouched.
        for settings in ({"STATUS_LED_PIN": 8, "STATUS_LED_POWER_PIN": 13},
                         dict(led, STATUS_LED_POWER_PIN=8),
                         dict(led, ARM_SWITCH_PIN=6, STATUS_LED_POWER_PIN=6),
                         dict(led, STATUS_LED_POWER_PIN=48)):
            interface = self.make_powered(**settings)
            self.assertEqual(self.outputs, [], settings)
            self.assertTrue(interface.faults & 2, settings)

    def test_installer_master_config(self):
        sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "install"))
        from install_usb_bridge import prepare_master_config
        repository = (Path(__file__).resolve().parents[2] / "main/configs/bridge_usb.py").read_bytes()
        device = (b"NETWORK_ID = 0x0101\r\nARM_SWITCH_PIN = None\r\nSTATUS_LED_PIN = None\r\n"
                  b"STATUS_LED_ORDER = \"BRG\"\r\nMASTER_ENABLED = True\r\n\r\nMASTER_ENABLED = True\r\n")
        prepared = prepare_master_config(device, repository)
        settings, expected = {}, {}
        exec(prepared.decode(), settings)
        exec(repository.decode(), expected)
        self.assertEqual(settings["NETWORK_ID"], 0x0101)
        # A bridge without pins takes the repository's whole switch/LED block.
        for name in ("STATUS_LED_ORDER", "STATUS_LED_TIMING_NS", "STATUS_LED_POWER_PIN"):
            self.assertEqual(settings[name], expected[name], name)
        self.assertEqual(prepared.count(b"MASTER_ENABLED"), 1)
        # A bridge with its own pins keeps its block.
        own = b"ARM_SWITCH_PIN = 9\nSTATUS_LED_PIN = None\nSTATUS_LED_ORDER = 'RGB'\n"
        settings = {}
        exec(prepare_master_config(own, repository).decode(), settings)
        self.assertEqual((settings["ARM_SWITCH_PIN"], settings["STATUS_LED_ORDER"]), (9, "RGB"))
        self.assertNotIn("STATUS_LED_POWER_PIN", settings)


if __name__ == "__main__":
    unittest.main()
