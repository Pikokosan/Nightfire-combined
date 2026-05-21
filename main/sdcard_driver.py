# sdcard_driver.py
#
# Updated:  03 July 2025
#
# Copyright (C) 2025 KW Services.
# MIT License
# LVGL (9.2.2) MicroPython (1.24.1) Binding compiled on 2025-06-13; Generic ESP32S3 module with Octal-SPIRAM with ESP32S3
# Waveshare ESP32-S3-Touch-LCD-4.3
# LVGL 9.2.2
#
import ch422g
import machine

from i2c import I2C


i2c_bus = I2C.Bus(
    host=0,
    scl=9,
    sda=8,
    freq=400000,
    use_locks=False
)

io_expander_device = I2C.Device(i2c_bus, dev_id=ch422g.I2C_ADDR, reg_bits=ch422g.BITS)
ch422g.Pin.set_device(io_expander_device)

tp_reset_pin = ch422g.Pin(
    ch422g.EXIO1,
    mode=ch422g.Pin.OUT,
    value=0
)

tp_lcdbl_pin = ch422g.Pin(
    ch422g.EXIO2,
    mode=ch422g.Pin.OUT,
    value=0
)

tp_sd_cs_pin = ch422g.Pin(
    ch422g.EXIO4,
    mode=ch422g.Pin.OUT,
    value=0
)

# These expander states are required before SD initialization.
tp_reset_pin.low()
tp_lcdbl_pin.low()
tp_sd_cs_pin.high()

spi_bus = machine.SPI.Bus(
    host=2,
    mosi=11,
    miso=13,
    sck=12
)

sd = None

try:
    sd = machine.SDCard(
        spi_bus=spi_bus,
        cs=2,
        freq=20000000
    )
except Exception as e:
    print("SDCard error:", e)
