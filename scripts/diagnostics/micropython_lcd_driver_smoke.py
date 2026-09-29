from machine import I2C, Pin
import time

from lcd1602_i2c import I2cLcd1602


i2c = I2C(0, sda=Pin(36), scl=Pin(38), freq=100000)
print("scan:", ["0x%02X" % a for a in i2c.scan()])

lcd = I2cLcd1602(i2c, 0x3F, 16, 2, mapping="upper")
lcd.show_lines("DRIVER TEST", "0x3F upper")
print("driver wrote test text")

while True:
    time.sleep_ms(1000)
