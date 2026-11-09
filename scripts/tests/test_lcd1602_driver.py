"""Checks main/lcd1602_i2c.py against an emulated PCF8574 backpack + HD44780.

The emulator decodes every enable-pin falling edge in the bytes written over
I2C, follows the 8-bit -> 4-bit init, and keeps the controller's DDRAM. The
tests compare what the screen shows after show_lines() calls, for both pin
mappings, and check that partial updates write fewer bytes.

    py -3 scripts/tests/test_lcd1602_driver.py
"""

import os
import sys
import types
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(ROOT, "main"))

fake_time = types.ModuleType("time")
fake_time.sleep_ms = lambda ms: None
fake_time.sleep_us = lambda us: None
sys.modules["time"] = fake_time

import lcd1602_i2c  # noqa: E402

MAPPINGS = {
    # mapping: (rs, enable, data bits are high nibble?)
    "upper": (0x01, 0x04, True),
    "lower": (0x10, 0x40, False),
}


class FakeHd44780(object):
    def __init__(self, mapping):
        self.rs_bit, self.enable_bit, self.upper = MAPPINGS[mapping]
        self.prev = 0
        self.four_bit = False
        self.pending = None
        self.address = 0
        self.ddram = [" "] * 0x68
        self.bytes_written = 0
        self.transactions = 0

    def writeto(self, address, data):
        self.transactions += 1
        for value in bytes(data):
            self.bytes_written += 1
            if self.prev & self.enable_bit and not value & self.enable_bit:
                self._latch(self.prev)
            self.prev = value

    def _latch(self, value):
        nibble = (value >> 4) & 0x0F if self.upper else value & 0x0F
        rs = bool(value & self.rs_bit)
        if not self.four_bit:
            # 8-bit phase: only D7-D4 are wired; 0x2_ switches to 4-bit.
            if nibble == 0x2:
                self.four_bit = True
            return
        if self.pending is None:
            self.pending = (nibble, rs)
            return
        high, rs_high = self.pending
        self.pending = None
        self._byte((high << 4) | nibble, rs_high)

    def _byte(self, value, rs):
        if rs:
            if 0 <= self.address < len(self.ddram):
                self.ddram[self.address] = chr(value)
            self.address += 1
        elif value & 0x80:
            self.address = value & 0x7F
        elif value == 0x01:
            self.ddram = [" "] * 0x68
            self.address = 0

    def row(self, row, cols=16):
        base = (0x00, 0x40)[row]
        return "".join(self.ddram[base:base + cols])


def make(mapping="upper"):
    bus = FakeHd44780(mapping)
    lcd = lcd1602_i2c.I2cLcd1602(bus, 0x27, 16, 2, mapping=mapping)
    return lcd, bus


class DriverTests(unittest.TestCase):
    def test_full_and_partial_updates_both_mappings(self):
        for mapping in MAPPINGS:
            lcd, bus = make(mapping)
            lcd.show_lines("Nightfire Field", "Booting...")
            self.assertEqual(bus.row(0), "Nightfire Field ")
            self.assertEqual(bus.row(1), "Booting...      ")
            lcd.show_lines("7.89V      G0001", "M:OK D96E30DD")
            self.assertEqual(bus.row(0), "7.89V      G0001")
            self.assertEqual(bus.row(1), "M:OK D96E30DD   ")
            before = bus.bytes_written
            lcd.show_lines("7.88V      G0001", "M:OK D96E30DD")
            self.assertEqual(bus.row(0), "7.88V      G0001")
            # One changed digit: a move plus one character (8 backpack bytes).
            self.assertEqual(bus.bytes_written - before, 8, mapping)
            before = bus.bytes_written
            lcd.show_lines("7.88V      G0001", "M:OK D96E30DD")
            self.assertEqual(bus.bytes_written, before, "unchanged lines must not write")

    def test_one_transaction_per_row(self):
        lcd, bus = make()
        lcd.show_lines("a", "b")
        start = bus.transactions
        lcd.show_lines("ARMED  12345678", "Cont 24/24 OK")
        self.assertEqual(bus.transactions - start, 2)

    def test_max_rows_spreads_a_redraw(self):
        lcd, bus = make()
        lcd.show_lines("", "")
        self.assertFalse(lcd.show_lines("row one", "row two", max_rows=1))
        self.assertEqual(bus.row(0), "row one         ")
        self.assertEqual(bus.row(1), "                ")
        self.assertTrue(lcd.show_lines("row one", "row two", max_rows=1))
        self.assertEqual(bus.row(1), "row two         ")

    def test_clear_forces_full_redraw(self):
        lcd, bus = make()
        lcd.show_lines("hello", "world")
        lcd.clear()
        self.assertEqual(bus.row(0), " " * 16)
        lcd.show_lines("hello", "world")
        self.assertEqual(bus.row(0), "hello           ")
        self.assertEqual(bus.row(1), "world           ")

    def test_long_text_is_cut_to_width(self):
        lcd, bus = make()
        lcd.show_lines("0123456789ABCDEFGHIJ", "x")
        self.assertEqual(bus.row(0), "0123456789ABCDEF")

    def test_putstr_and_move_to_still_work(self):
        lcd, bus = make()
        lcd.move_to(3, 1)
        lcd.putstr("abc")
        self.assertEqual(bus.row(1)[3:6], "abc")


if __name__ == "__main__":
    unittest.main()
