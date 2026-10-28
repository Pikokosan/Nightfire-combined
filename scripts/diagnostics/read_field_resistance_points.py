"""Standalone device-side probe for field resistance calibration points."""

import time
from machine import ADC, Pin

try:
    import nrf_node_config as config
except Exception:
    config = None


CUES = tuple(range(1, 9))
PASSES = 5
PRE_TEST_PIN = getattr(config, "PRE_TEST_PIN", 2)
SHORT_CHECK_PIN = getattr(config, "SHORT_CHECK_PIN", 31)
DATA_PIN = getattr(config, "HC595_DATA_PIN", 6)
CLOCK_PIN = getattr(config, "HC595_CLOCK_PIN", 8)
LATCH_PIN = getattr(config, "HC595_LATCH_PIN", 17)
OE_PIN = getattr(config, "HC595_OE_PIN", 20)
SRCLR_PIN = getattr(config, "HC595_SRCLR_PIN", 22)
SAMPLES = getattr(config, "PRETEST_ADC_SAMPLES", 16)


data = Pin(DATA_PIN, Pin.OUT, value=0)
clock = Pin(CLOCK_PIN, Pin.OUT, value=0)
latch = Pin(LATCH_PIN, Pin.OUT, value=0)
oe = Pin(OE_PIN, Pin.OUT, value=1)
srclr = Pin(SRCLR_PIN, Pin.OUT, value=1)
pre_test = Pin(PRE_TEST_PIN, Pin.OUT, value=0)


def pulse(pin):
    pin.value(1)
    time.sleep_us(1)
    pin.value(0)
    time.sleep_us(1)


def write_mask(mask):
    for bit in range(23, -1, -1):
        data.value(1 if mask & (1 << bit) else 0)
        pulse(clock)
    pulse(latch)


def clear_outputs():
    oe.value(1)
    write_mask(0)
    pre_test.value(0)


def read_adc():
    total = 0
    for _ in range(SAMPLES):
        try:
            adc = ADC(Pin(SHORT_CHECK_PIN))
        except Exception:
            adc = ADC(SHORT_CHECK_PIN)
        total += int(adc.read_u16())
        time.sleep_us(150)
    raw = total // SAMPLES
    return raw, (raw * 3300 + 32767) // 65535


def read_cue(cue):
    oe.value(1)
    write_mask(0)
    pre_test.value(1)
    time.sleep_ms(5)
    write_mask(1 << (cue - 1))
    oe.value(0)
    time.sleep_us(750)
    try:
        return read_adc()
    finally:
        clear_outputs()


def run():
    print("RES_CAL pins pre=%d sense=%d" % (PRE_TEST_PIN, SHORT_CHECK_PIN))
    try:
        for cue in CUES:
            raw_total = 0
            adc_mv_total = 0
            for pass_index in range(PASSES):
                raw, adc_mv = read_cue(cue)
                raw_total += raw
                adc_mv_total += adc_mv
                print(
                    "RES_CAL cue=%02d pass=%d raw=%d adc_mv=%d" %
                    (cue, pass_index + 1, raw, adc_mv)
                )
            print(
                "RES_CAL_AVG cue=%02d raw=%d adc_mv=%d" %
                (cue, raw_total // PASSES, adc_mv_total // PASSES)
            )
    finally:
        clear_outputs()


run()
