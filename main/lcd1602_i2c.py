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
        self._send(value, 0)

    def write_char(self, char_value):
        self._send(char_value, self.rs_bit)

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
        for ch in str(text):
            self.write_char(ord(ch))

    def _fit_line(self, text):
        text = str(text)[:self.cols]
        if len(text) < self.cols:
            text += " " * (self.cols - len(text))
        return text

    def show_lines(self, line1="", line2=""):
        lines = (
            self._fit_line(line1),
            self._fit_line(line2),
        )
        if lines == self._last_lines:
            return

        self.move_to(0, 0)
        self.putstr(lines[0])
        if self.rows > 1:
            self.move_to(0, 1)
            self.putstr(lines[1])
        self._last_lines = lines
