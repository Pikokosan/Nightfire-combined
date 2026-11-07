# HD44780 16x2 LCD behind a PCF8574 I2C backpack.
#
# Drawing is built for a radio node that must keep listening: show_lines()
# writes only the changed span of each row, and each span goes out as one
# I2C transaction (4 backpack bytes per LCD byte) instead of one transaction
# and a sleep per nibble. A full redraw used to block ~90 ms on the module;
# a changed digit now costs a few ms. With max_rows=1 a caller can spread a
# redraw over several main-loop passes.
#
# Timing: each backpack byte takes ~90 us at 100 kHz (~23 us at 400 kHz), so
# the enable pulse and the HD44780's 37 us per-command time are met by the
# bus itself. Only clear/home (1.5 ms) and init still sleep.

import time


class I2cLcd1602:
    LCD_CLEARDISPLAY = 0x01
    LCD_RETURNHOME = 0x02
    LCD_ENTRYMODESET = 0x04
    LCD_DISPLAYCONTROL = 0x08
    LCD_FUNCTIONSET = 0x20
    LCD_SETDDRAMADDR = 0x80

    LCD_ENTRYLEFT = 0x02
    LCD_2LINE = 0x08
    LCD_5X8DOTS = 0x00
    LCD_DISPLAYON = 0x04

    def __init__(self, i2c, address, cols=16, rows=2, backlight=True, mapping="upper"):
        self.i2c = i2c
        self.address = int(address)
        self.cols = int(cols)
        self.rows = int(rows)
        self.mapping = str(mapping or "upper")
        self.backlight_on = bool(backlight)
        self._last_lines = None
        self.rs_bit = 0
        self.enable_bit = 0
        self.backlight_bit = 0
        self.data_shift = 4
        self._set_mapping(self.mapping)
        self._init_lcd()

    @staticmethod
    def find_address(i2c, preferred=None):
        if preferred is not None:
            return int(preferred)

        found = i2c.scan()
        for address in (0x27, 0x3F):
            if address in found:
                return address

        if found:
            return int(found[0])
        return None

    def _set_mapping(self, mapping):
        if mapping == "lower":
            self.rs_bit = 0x10
            self.enable_bit = 0x40
            self.backlight_bit = 0x80
            self.data_shift = 0
        else:
            self.rs_bit = 0x01
            self.enable_bit = 0x04
            self.backlight_bit = 0x08
            self.data_shift = 4

    def _backlight(self):
        return self.backlight_bit if self.backlight_on else 0

    def _write(self, value):
        self.i2c.writeto(self.address, bytes([value & 0xFF]))

    def _pulse(self, value):
        self._write(value | self.enable_bit)
        time.sleep_us(3)
        self._write(value & ~self.enable_bit)
        time.sleep_us(120)

    def _write4(self, nibble, mode=0):
        if self.data_shift == 4:
            data = nibble & 0xF0
        else:
            data = (nibble >> 4) & 0x0F
        value = data | self._backlight() | mode
        self._pulse(value)

    def _send(self, value, mode=0):
        self._write4(value & 0xF0, mode)
        self._write4((value << 4) & 0xF0, mode)

    def _encode(self, value, mode, out):
        """Append the 4 backpack bytes that clock one byte into the LCD:
        high nibble with E high, E low, then the low nibble the same way."""
        base = self._backlight() | mode
        enable = self.enable_bit
        if self.data_shift == 4:
            high = (value & 0xF0) | base
            low = ((value << 4) & 0xF0) | base
        else:
            high = ((value >> 4) & 0x0F) | base
            low = (value & 0x0F) | base
        out.extend(bytes((high | enable, high, low | enable, low)))

    def _write_run(self, col, row, text):
        """Move to (col, row) and write text, all in one I2C transaction."""
        row_offsets = (0x00, 0x40, 0x14, 0x54)
        if row >= len(row_offsets):
            row = len(row_offsets) - 1
        out = bytearray()
        self._encode(self.LCD_SETDDRAMADDR | (int(col) + row_offsets[int(row)]), 0, out)
        base = self._backlight() | self.rs_bit
        enable = self.enable_bit
        upper = self.data_shift == 4
        for ch in text:
            value = ord(ch)
            if upper:
                high = (value & 0xF0) | base
                low = ((value << 4) & 0xF0) | base
            else:
                high = ((value >> 4) & 0x0F) | base
                low = (value & 0x0F) | base
            out.extend(bytes((high | enable, high, low | enable, low)))
        self.i2c.writeto(self.address, out)

    def _init_lcd(self):
        time.sleep_ms(50)
        self._write(self._backlight())
        time.sleep_ms(20)

        self._write4(0x30)
        time.sleep_ms(5)
        self._write4(0x30)
        time.sleep_ms(5)
        self._write4(0x30)
        time.sleep_ms(5)
        self._write4(0x20)
        time.sleep_ms(5)

        self.command(self.LCD_FUNCTIONSET | self.LCD_2LINE | self.LCD_5X8DOTS)
        self.command(self.LCD_DISPLAYCONTROL)
        self.command(self.LCD_CLEARDISPLAY)
        time.sleep_ms(3)
        self.command(self.LCD_ENTRYMODESET | self.LCD_ENTRYLEFT)
        self.command(self.LCD_DISPLAYCONTROL | self.LCD_DISPLAYON)

    def command(self, value):
        out = bytearray()
        self._encode(value, 0, out)
        self.i2c.writeto(self.address, out)

    def write_char(self, char_value):
        out = bytearray()
        self._encode(char_value, self.rs_bit, out)
        self.i2c.writeto(self.address, out)

    def clear(self):
        self.command(self.LCD_CLEARDISPLAY)
        time.sleep_ms(2)
        self._last_lines = None

    def move_to(self, col, row):
        row_offsets = (0x00, 0x40, 0x14, 0x54)
        if row >= len(row_offsets):
            row = len(row_offsets) - 1
        self.command(self.LCD_SETDDRAMADDR | (int(col) + row_offsets[int(row)]))

    def putstr(self, text):
        """Write text at the cursor in one I2C transaction."""
        out = bytearray()
        for ch in str(text):
            self._encode(ord(ch), self.rs_bit, out)
        if out:
            self.i2c.writeto(self.address, out)

    def _fit_line(self, text):
        text = str(text)[:self.cols]
        if len(text) < self.cols:
            text += " " * (self.cols - len(text))
        return text

    def show_lines(self, line1="", line2="", max_rows=None):
        """Bring the display to these lines, writing only what changed.

        With max_rows, at most that many rows are written now; the result is
        True once the display shows the lines (call again until it is)."""
        lines = (self._fit_line(line1), self._fit_line(line2))
        if self._last_lines is None:
            self._last_lines = [None] * self.rows
        written = 0
        for row in range(min(self.rows, 2)):
            new = lines[row]
            old = self._last_lines[row]
            if new == old:
                continue
            if max_rows is not None and written >= max_rows:
                return False
            if old is None or len(old) != len(new):
                first, last = 0, len(new) - 1
            else:
                first = 0
                while new[first] == old[first]:
                    first += 1
                last = len(new) - 1
                while new[last] == old[last]:
                    last -= 1
            self._write_run(first, row, new[first:last + 1])
            self._last_lines[row] = new
            written += 1
        return True
