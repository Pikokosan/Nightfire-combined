try:
    from machine import I2C
except ImportError:
    I2C = object


_HT16K33_BLINK_CMD = 0x80
_HT16K33_BLINK_DISPLAYON = 0x01
_HT16K33_CMD_BRIGHTNESS = 0xE0

HT16K33_BLINK_OFF = 0
HT16K33_BLINK_2HZ = 1
HT16K33_BLINK_1HZ = 2
HT16K33_BLINK_HALFHZ = 3

_led_lut = (
    0x3A, 0x37, 0x35, 0x34,
    0x28, 0x29, 0x23, 0x24,
    0x16, 0x1B, 0x11, 0x10,
    0x0E, 0x0D, 0x0C, 0x02,
)

_button_lut = (
    0x07, 0x04, 0x02, 0x22,
    0x05, 0x06, 0x00, 0x01,
    0x03, 0x10, 0x30, 0x21,
    0x13, 0x12, 0x11, 0x31,
)


def _bv(n):
    return 1 << n


class Trellis:
    def __init__(self, i2c, addr=0x70):
        self.i2c = i2c
        self.i2c_addr = addr
        self.displaybuffer = [0] * 8
        self.keys = bytearray(6)
        self.lastkeys = bytearray(6)

    def _write_cmd(self, *bytes_out):
        self.i2c.writeto(self.i2c_addr, bytes(bytes_out))

    def _write_then_read(self, write_bytes, read_len):
        self.i2c.writeto(self.i2c_addr, write_bytes)
        return self.i2c.readfrom(self.i2c_addr, read_len)

    def begin(self, addr=None):
        if addr is not None:
            self.i2c_addr = addr

        self._write_cmd(0x21)
        self.blink_rate(HT16K33_BLINK_OFF)
        self.set_brightness(15)
        self._write_cmd(0xA1)

    def is_key_pressed(self, k):
        if k < 0 or k > 15:
            return False
        kk = _button_lut[k]
        return bool(self.keys[kk >> 4] & _bv(kk & 0x0F))

    def was_key_pressed(self, k):
        if k < 0 or k > 15:
            return False
        kk = _button_lut[k]
        return bool(self.lastkeys[kk >> 4] & _bv(kk & 0x0F))

    def just_pressed(self, k):
        return self.is_key_pressed(k) and not self.was_key_pressed(k)

    def just_released(self, k):
        return not self.is_key_pressed(k) and self.was_key_pressed(k)

    def read_switches(self):
        self.lastkeys[:] = self.keys
        self.keys[:] = self._write_then_read(bytes((0x40,)), 6)

        for i in range(6):
            if self.lastkeys[i] != self.keys[i]:
                return True
        return False

    def set_led(self, x):
        if x < 0 or x > 15:
            return
        xx = _led_lut[x]
        self.displaybuffer[xx >> 4] |= _bv(xx & 0x0F)

    def clr_led(self, x):
        if x < 0 or x > 15:
            return
        xx = _led_lut[x]
        self.displaybuffer[xx >> 4] &= ~_bv(xx & 0x0F)

    def clear(self):
        for i in range(8):
            self.displaybuffer[i] = 0

    def write_display(self):
        out = bytearray(17)
        out[0] = 0x00

        idx = 1
        for value in self.displaybuffer:
            value &= 0xFFFF
            out[idx] = value & 0xFF
            out[idx + 1] = (value >> 8) & 0xFF
            idx += 2

        self.i2c.writeto(self.i2c_addr, out)

    def set_brightness(self, brightness):
        if brightness < 0:
            brightness = 0
        elif brightness > 15:
            brightness = 15
        self._write_cmd(_HT16K33_CMD_BRIGHTNESS | brightness)

    def blink_rate(self, rate):
        if rate < 0 or rate > 3:
            rate = 0
        self._write_cmd(_HT16K33_BLINK_CMD | _HT16K33_BLINK_DISPLAYON | (rate << 1))
