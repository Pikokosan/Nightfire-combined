"""Standalone 4x4 Trellis keypad and LED hardware test."""

from machine import I2C, Pin
import time

from adafruit_trellis import Trellis


I2C_ID = 0
SDA_PIN = 8
SCL_PIN = 9
I2C_FREQ = 400000
TRELLIS_ADDRESS = 0x70
POLL_MS = 4
LED_SWEEP_MS = 60


def show_pressed_leds(trellis):
    trellis.clear()
    pressed = []

    for key in range(16):
        if trellis.is_key_pressed(key):
            trellis.set_led(key)
            pressed.append(key)

    trellis.write_display()
    return pressed


def sweep_leds(trellis):
    print("Sweeping LEDs 1-16...")
    for key in range(16):
        trellis.clear()
        trellis.set_led(key)
        trellis.write_display()
        time.sleep_ms(LED_SWEEP_MS)

    trellis.clear()
    for key in range(16):
        trellis.set_led(key)
    trellis.write_display()
    time.sleep_ms(300)
    trellis.clear()
    trellis.write_display()


def main():
    print("\nTrellis hardware test")
    print("I2C%d SDA=%d SCL=%d address=0x%02X" % (
        I2C_ID,
        SDA_PIN,
        SCL_PIN,
        TRELLIS_ADDRESS,
    ))

    i2c = I2C(
        I2C_ID,
        sda=Pin(SDA_PIN),
        scl=Pin(SCL_PIN),
        freq=I2C_FREQ,
    )
    devices = i2c.scan()
    print("I2C devices:", ["0x%02X" % address for address in devices])

    if TRELLIS_ADDRESS not in devices:
        raise RuntimeError("Trellis not found at 0x%02X" % TRELLIS_ADDRESS)

    trellis = Trellis(i2c, TRELLIS_ADDRESS)
    trellis.begin()
    trellis.read_switches()
    time.sleep_ms(10)
    trellis.read_switches()

    sweep_leds(trellis)
    print("Press and release buttons. A held button lights its matching LED.")
    print("Press Ctrl-C to stop.\n")

    event_count = 0
    read_count = 0

    try:
        while True:
            changed = trellis.read_switches()
            read_count += 1

            if changed:
                now = time.ticks_ms()
                for key in range(16):
                    if trellis.just_pressed(key):
                        event_count += 1
                        print("%8d ms  PRESS   key %02d  event %d" % (
                            now,
                            key + 1,
                            event_count,
                        ))
                    elif trellis.just_released(key):
                        print("%8d ms  RELEASE key %02d" % (now, key + 1))

                show_pressed_leds(trellis)

            if read_count % 1250 == 0:
                print("%8d ms  alive: %d scans, %d presses" % (
                    time.ticks_ms(),
                    read_count,
                    event_count,
                ))

            time.sleep_ms(POLL_MS)
    finally:
        trellis.clear()
        trellis.write_display()
        print("Trellis test stopped")


main()
