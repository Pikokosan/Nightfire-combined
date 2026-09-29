from machine import I2C, Pin
import time


SDA_PIN = 36
SCL_PIN = 38
ADDRESS = 0x3F


MAPPINGS = (
    # name, data_shift, rs, enable, backlight
    ("upper-rs0-e2-bl3", 4, 0x01, 0x04, 0x08),
    ("upper-rs0-e1-bl3", 4, 0x01, 0x02, 0x08),
    ("upper-rs1-e2-bl3", 4, 0x02, 0x04, 0x08),
    ("upper-rs0-e2-bl7", 4, 0x01, 0x04, 0x80),
    ("upper-rs0-e1-bl7", 4, 0x01, 0x02, 0x80),
    ("lower-rs4-e6-bl7", 0, 0x10, 0x40, 0x80),
    ("lower-rs4-e5-bl7", 0, 0x10, 0x20, 0x80),
    ("lower-rs5-e6-bl7", 0, 0x20, 0x40, 0x80),
)


try:
    i2c = I2C(0, sda=Pin(SDA_PIN), scl=Pin(SCL_PIN), freq=100000)
except TypeError:
    i2c = I2C(0, sda=Pin(SDA_PIN), scl=Pin(SCL_PIN))


def write(value):
    i2c.writeto(ADDRESS, bytes([value & 0xFF]))


def pulse(value, enable):
    write(value | enable)
    time.sleep_us(3)
    write(value & ~enable)
    time.sleep_us(120)


def write4(nibble, data_shift, rs, enable, backlight, mode=0):
    if data_shift == 4:
        data = nibble & 0xF0
    else:
        data = (nibble >> 4) & 0x0F
    pulse(data | backlight | mode, enable)


def send(value, data_shift, rs, enable, backlight, mode=0):
    write4(value & 0xF0, data_shift, rs, enable, backlight, mode)
    write4((value << 4) & 0xF0, data_shift, rs, enable, backlight, mode)


def command(value, data_shift, rs, enable, backlight):
    send(value, data_shift, rs, enable, backlight, 0)


def putstr(text, data_shift, rs, enable, backlight):
    for ch in text:
        send(ord(ch), data_shift, rs, enable, backlight, rs)


def fit16(text):
    text = str(text)[:16]
    if len(text) < 16:
        text += " " * (16 - len(text))
    return text


def init_lcd(data_shift, rs, enable, backlight):
    write(backlight)
    time.sleep_ms(50)
    write4(0x30, data_shift, rs, enable, backlight)
    time.sleep_ms(5)
    write4(0x30, data_shift, rs, enable, backlight)
    time.sleep_ms(5)
    write4(0x30, data_shift, rs, enable, backlight)
    time.sleep_ms(5)
    write4(0x20, data_shift, rs, enable, backlight)
    time.sleep_ms(5)
    command(0x28, data_shift, rs, enable, backlight)
    command(0x08, data_shift, rs, enable, backlight)
    command(0x01, data_shift, rs, enable, backlight)
    time.sleep_ms(3)
    command(0x06, data_shift, rs, enable, backlight)
    command(0x0C, data_shift, rs, enable, backlight)


def show_text(label, index, data_shift, rs, enable, backlight):
    init_lcd(data_shift, rs, enable, backlight)
    command(0x80, data_shift, rs, enable, backlight)
    putstr("MAP %02d TEST     " % index, data_shift, rs, enable, backlight)
    command(0xC0, data_shift, rs, enable, backlight)
    putstr(fit16(label), data_shift, rs, enable, backlight)


found = i2c.scan()
print("scan:", ["0x%02X" % a for a in found])
print("watch the LCD; each mapping stays up for 4 seconds")

while True:
    for index, item in enumerate(MAPPINGS, 1):
        label, data_shift, rs, enable, backlight = item
        print("trying", index, label)
        try:
            show_text(label, index, data_shift, rs, enable, backlight)
        except Exception as e:
            print("error", label, e)
        time.sleep_ms(4000)
