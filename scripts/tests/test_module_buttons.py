"""LCD buttons (main/module_lcd.py DebouncedButton) and the field module's
NFC pin release (main/field_main.py release_nfc_pins).

Run: py -3 scripts/tests/test_module_buttons.py
"""
import ast
import unittest
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[2]


def load(source, names, namespace):
    tree = ast.parse((ROOT / "main" / source).read_text(encoding="utf-8"))
    body = [n for n in tree.body
            if isinstance(n, (ast.ClassDef, ast.FunctionDef)) and n.name in names]
    exec(compile(ast.Module(body=body, type_ignores=[]), source, "exec"), namespace)
    return namespace


class FakePin:
    def __init__(self, value=0):
        self.level = value
        self.handler = None

    def value(self):
        return self.level

    def irq(self, handler=None, trigger=None):
        self.handler = handler


class ButtonTests(unittest.TestCase):
    def setUp(self):
        self.clock = SimpleNamespace(ms=1000)
        fake_time = SimpleNamespace(
            ticks_ms=lambda: self.clock.ms,
            ticks_diff=lambda a, b: a - b,
            ticks_add=lambda a, b: a + b,
            sleep_ms=lambda ms: setattr(self.clock, "ms", self.clock.ms + ms),
        )
        import sys
        sys.modules["machine"] = SimpleNamespace(
            Pin=SimpleNamespace(IRQ_RISING=1, IRQ_FALLING=2))
        self.addCleanup(sys.modules.pop, "machine", None)
        ns = load("module_lcd.py", ("DebouncedButton",), {"time": fake_time})
        self.Button = ns["DebouncedButton"]
        self.pin = FakePin()
        self.button = self.Button(self.pin, 80, 1)

    def update(self, step=50):
        self.clock.ms += step
        return self.button.update(self.clock.ms)

    def test_press_is_reported_on_the_first_reading(self):
        self.pin.level = 1
        self.assertTrue(self.update())
        self.assertFalse(self.update())        # still held: once per press

    def test_a_tap_between_two_readings_is_latched_by_the_interrupt(self):
        self.pin.handler(self.pin)              # pressed and released already
        self.assertTrue(self.update())

    def test_release_bounce_is_not_a_second_press(self):
        self.pin.level = 1
        self.assertTrue(self.update())
        self.pin.level = 0
        self.pin.handler(self.pin)              # bounce edge on release
        self.assertFalse(self.update(20))
        self.assertFalse(self.update(100))      # now released and settled
        self.pin.level = 1
        self.assertTrue(self.update(100))       # the next real press

    def test_presses_closer_than_the_debounce_count_once(self):
        self.pin.handler(self.pin)
        self.assertTrue(self.update())
        self.pin.handler(self.pin)
        self.assertFalse(self.update(20))

    def test_power_up_hold_check_leaves_the_interrupt_alone(self):
        handler = self.pin.handler
        hold = self.Button(self.pin, 80, 1, irq=False)
        self.assertIs(self.pin.handler, handler)
        self.pin.level = 1
        self.assertTrue(hold.held_for(1000))
        self.pin.level = 0
        self.assertFalse(hold.held_for(1000))


class NfcPinTests(unittest.TestCase):
    UICR = 0x1000120C

    def make(self, nfc_enabled, write_works=True):
        calls = []

        class Mem(dict):
            def __setitem__(self, address, value):
                calls.append((address, value))
                if address != NfcPinTests.UICR or write_works:
                    dict.__setitem__(self, address, value)

        mem = Mem({self.UICR: 0xFFFFFFFF if nfc_enabled else 0xFFFFFFFE,
                   0x4001E400: 1, 0x4001E504: 0})
        machine = SimpleNamespace(mem32=mem, reset=lambda: calls.append("reset"))
        faults = []
        import sys
        sys.modules["machine"] = machine
        ns = load("field_main.py", ("release_nfc_pins",), {
            "machine": machine,
            "time": SimpleNamespace(sleep_ms=lambda ms: None),
            "record_startup_fault": faults.append,
        })
        return ns["release_nfc_pins"], calls, faults

    def tearDown(self):
        import sys
        sys.modules.pop("machine", None)

    def test_clears_the_nfc_bit_once_and_restarts(self):
        release, calls, faults = self.make(nfc_enabled=True)
        release((9, 10, 45))
        self.assertIn((self.UICR, 0xFFFFFFFE), calls)
        self.assertEqual(calls[-1], "reset")
        self.assertEqual(faults, [])

    def test_nothing_to_do(self):
        for pins, enabled in (((45, 47), True), ((9, 10), False)):
            release, calls, _ = self.make(nfc_enabled=enabled)
            release(pins)
            self.assertEqual(calls, [])

    def test_failed_write_reports_a_fault_instead_of_restarting(self):
        release, calls, faults = self.make(nfc_enabled=True, write_works=False)
        release((10,))
        self.assertNotIn("reset", calls)
        self.assertEqual(len(faults), 1)


if __name__ == "__main__":
    unittest.main()
