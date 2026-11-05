import sys
import unittest
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "main"))
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
        self.assertEqual(self.writes[0][1], bytes([0, 0, 12, 0, 10, 0, 10, 5, 0, 0, 0, 0]))
        interface.poll(True)
        self.assertEqual(len(self.writes), 1)
        self.now = 4000
        interface.poll(False)
        self.assertEqual(self.writes[-1][1][-3:], b"\x0c\x00\x00")

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


if __name__ == "__main__":
    unittest.main()
