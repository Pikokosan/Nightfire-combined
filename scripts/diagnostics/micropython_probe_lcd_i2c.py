from machine import I2C, Pin
import time


PAIRS = (
    ("configured D8/D9", 36, 38),
    ("left D0/D1", 8, 6),
    ("right D20/D21", 29, 31),
    ("right D10/D16", 9, 10),
    ("right D14/D15", 43, 45),
    ("left D5/D6", 24, 32),
)


def scan_pair(label, sda_pin, scl_pin):
    for bus_id in (0, 1):
        try:
            try:
                i2c = I2C(bus_id, sda=Pin(sda_pin), scl=Pin(scl_pin), freq=100000)
            except TypeError:
                i2c = I2C(bus_id, sda=Pin(sda_pin), scl=Pin(scl_pin))

            time.sleep_ms(50)
            found = i2c.scan()
            print("%s bus=%d SDA=P%s SCL=P%s -> %s" % (
                label,
                bus_id,
                sda_pin,
                scl_pin,
                ["0x%02X" % addr for addr in found],
            ))
        except Exception as e:
            print("%s bus=%d SDA=P%s SCL=P%s ERROR %s" % (
                label,
                bus_id,
                sda_pin,
                scl_pin,
                str(e),
            ))


for item in PAIRS:
    scan_pair(item[0], item[1], item[2])
