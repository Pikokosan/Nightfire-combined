import time

import machine
from analog_mux74hc4051 import HC4051Scanner
from field_measurement import (
    BatteryVoltageSensor,
    ChannelCalibration,
    ChannelClassifier,
    ChannelSenseSensor,
    PretestCueSenseSensor,
)
from shift_register74hc595 import ShiftRegister74HC59524

try:
    import nrf_node_config as node_config
except Exception:
    node_config = None


APP_NAME = "field-hardware-test"
APP_VERSION = "0.1.0"

# Keep this test firmware inert by default. Set this True only on a dummy-load
# bench when you want to verify the 74HC595-to-MOSFET gate path.
ENABLE_OUTPUT_WALK = getattr(node_config, "ENABLE_OUTPUT_WALK", False)
OUTPUT_WALK_MS = getattr(node_config, "OUTPUT_WALK_MS", 150)
ENABLE_595_CHAIN_TEST = getattr(
    node_config,
    "ENABLE_595_CHAIN_TEST",
    getattr(node_config, "STARTUP_595_CHAIN_TEST_ENABLED", False),
)
HC595_EXPECTED_CHAIN_BITS = getattr(node_config, "HC595_EXPECTED_CHAIN_BITS", 0)
HC595_CHAIN_TEST_MAX_BITS = getattr(node_config, "HC595_CHAIN_TEST_MAX_BITS", 64)

CHANNEL_COUNT = int(getattr(node_config, "CUE_COUNT", 18))
if CHANNEL_COUNT < 1:
    CHANNEL_COUNT = 1
if CHANNEL_COUNT > 18:
    CHANNEL_COUNT = 18

BATTERY_MV_FALLBACK = getattr(node_config, "BATTERY_MV", 0)

# SuperMini NRF52840 defaults. The local MicroPython build addresses nRF pins
# as integers: P0.nn is nn, and P1.nn is 32 + nn.
EXTERNAL_VCC_CONTROL_ENABLED = getattr(node_config, "EXTERNAL_VCC_CONTROL_ENABLED", True)
EXTERNAL_VCC_CONTROL_PIN = getattr(node_config, "EXTERNAL_VCC_CONTROL_PIN", 13)  # P0.13 board VCC control
EXTERNAL_VCC_ENABLE_VALUE = getattr(node_config, "EXTERNAL_VCC_ENABLE_VALUE", 1)
EXTERNAL_VCC_SETTLE_MS = getattr(node_config, "EXTERNAL_VCC_SETTLE_MS", 50)

HC595_DATA_PIN = getattr(node_config, "HC595_DATA_PIN", 6)     # P0.06 / D1
HC595_CLOCK_PIN = getattr(node_config, "HC595_CLOCK_PIN", 8)   # P0.08 / D0
HC595_LATCH_PIN = getattr(node_config, "HC595_LATCH_PIN", 17)  # P0.17 / D2
HC595_OE_PIN = getattr(node_config, "HC595_OE_PIN", 20)        # P0.20 / D3
HC595_SRCLR_PIN = getattr(node_config, "HC595_SRCLR_PIN", 22)  # P0.22 / D4
HC595_Q7S_PIN = getattr(node_config, "HC595_Q7S_PIN", None)
HC595_CLOCK_DELAY_US = getattr(node_config, "HC595_CLOCK_DELAY_US", 1)

PRE_TEST_PIN = getattr(node_config, "PRE_TEST_PIN", 9)

MUX_S0_PIN = getattr(node_config, "MUX_S0_PIN", 24)            # P0.24 / D5
MUX_S1_PIN = getattr(node_config, "MUX_S1_PIN", 32)            # P1.00 / D6
MUX_S2_PIN = getattr(node_config, "MUX_S2_PIN", 36)            # P1.04 / D8
MUX1_EN_PIN = getattr(node_config, "MUX1_EN_PIN", 38)          # P1.06 / D9
MUX2_EN_PIN = getattr(node_config, "MUX2_EN_PIN", 47)          # P1.15 / D18
MUX3_EN_PIN = getattr(node_config, "MUX3_EN_PIN", 45)          # P1.13 / D15
SENSE_SELECTED_ADC_PIN = getattr(node_config, "SENSE_SELECTED_ADC_PIN", 31)  # P0.31 / D21 / AIN7
MUX_SETTLE_US = getattr(node_config, "MUX_SETTLE_US", 750)
MEASUREMENT_METHOD = getattr(node_config, "MEASUREMENT_METHOD", "mux4051")
SHORT_CHECK_PIN = getattr(node_config, "SHORT_CHECK_PIN", SENSE_SELECTED_ADC_PIN)
PRETEST_SETTLE_MS = getattr(node_config, "PRETEST_SETTLE_MS", 5)
PRETEST_OUTPUT_SETTLE_US = getattr(node_config, "PRETEST_OUTPUT_SETTLE_US", MUX_SETTLE_US)
PRETEST_ADC_SAMPLES = getattr(node_config, "PRETEST_ADC_SAMPLES", 8)
PRETEST_ADC_SAMPLE_DELAY_US = getattr(node_config, "PRETEST_ADC_SAMPLE_DELAY_US", 150)

BATT_CHECK_PIN = getattr(node_config, "BATT_CHECK_PIN", 29)    # P0.29 / D20 / AIN5
BATTERY_ADC_ENABLED = getattr(node_config, "BATTERY_ADC_ENABLED", True)
ADC_FULL_SCALE_MV = getattr(node_config, "ADC_FULL_SCALE_MV", 3300)
BATTERY_ADC_FULL_SCALE_MV = getattr(node_config, "BATTERY_ADC_FULL_SCALE_MV", 16800)
BATTERY_ADC_DIVIDER_RATIO = getattr(
    node_config,
    "BATTERY_ADC_DIVIDER_RATIO",
    float(ADC_FULL_SCALE_MV) / float(BATTERY_ADC_FULL_SCALE_MV),
)
CHANNEL_SENSE_DIVIDER_RATIO = getattr(node_config, "CHANNEL_SENSE_DIVIDER_RATIO", 22.0 / 122.0)
ADC_SAMPLES = getattr(node_config, "ADC_SAMPLES", 8)
ADC_SAMPLE_DELAY_US = getattr(node_config, "ADC_SAMPLE_DELAY_US", 150)
SCAN_INTERVAL_MS = getattr(node_config, "SCAN_INTERVAL_MS", 2000)
CHANNEL_OPEN_MAX_FRACTION = getattr(
    node_config,
    "CHANNEL_OPEN_MAX_FRACTION",
    getattr(node_config, "CHANNEL_FAULT_MAX_FRACTION", 0.05),
)
CHANNEL_SHORT_MIN_FRACTION = getattr(
    node_config,
    "CHANNEL_SHORT_MIN_FRACTION",
    getattr(node_config, "CHANNEL_NORMAL_MIN_FRACTION", getattr(node_config, "CHANNEL_OPEN_MIN_FRACTION", 0.80)),
)
MUX_RAW_SWEEP_ENABLED = getattr(node_config, "MUX_RAW_SWEEP_ENABLED", True)


def make_adc(pin, enabled=True):
    if not enabled:
        return None
    try:
        return machine.ADC(machine.Pin(pin))
    except Exception as e:
        try:
            return machine.ADC(pin)
        except Exception as e2:
            print("ADC init failed on P%d: %s / %s" % (pin, e, e2))
            return None


def set_external_vcc(enabled):
    if not EXTERNAL_VCC_CONTROL_ENABLED:
        return None

    value = EXTERNAL_VCC_ENABLE_VALUE if enabled else 1 - EXTERNAL_VCC_ENABLE_VALUE
    pin = machine.Pin(EXTERNAL_VCC_CONTROL_PIN, machine.Pin.OUT, value=value)
    if enabled and EXTERNAL_VCC_SETTLE_MS:
        time.sleep_ms(EXTERNAL_VCC_SETTLE_MS)
    return pin


external_vcc = set_external_vcc(True)
pre_test = machine.Pin(PRE_TEST_PIN, machine.Pin.OUT, value=0)

outputs = ShiftRegister74HC59524(
    data_pin=HC595_DATA_PIN,
    clock_pin=HC595_CLOCK_PIN,
    latch_pin=HC595_LATCH_PIN,
    oe_pin=HC595_OE_PIN,
    srclr_pin=HC595_SRCLR_PIN,
    q7s_pin=HC595_Q7S_PIN,
    bit_count=24,
    used_outputs=CHANNEL_COUNT,
    clock_delay_us=HC595_CLOCK_DELAY_US,
)

scanner = None
if MEASUREMENT_METHOD != "pretest_selected_cue_short_check":
    scanner = HC4051Scanner(
        s0_pin=MUX_S0_PIN,
        s1_pin=MUX_S1_PIN,
        s2_pin=MUX_S2_PIN,
        en_pins=(MUX1_EN_PIN, MUX2_EN_PIN, MUX3_EN_PIN),
        channel_count=CHANNEL_COUNT,
        settle_us=MUX_SETTLE_US,
    )

battery_adc = make_adc(BATT_CHECK_PIN, BATTERY_ADC_ENABLED)
sense_adc_pin = SHORT_CHECK_PIN if MEASUREMENT_METHOD == "pretest_selected_cue_short_check" else SENSE_SELECTED_ADC_PIN
sense_adc = make_adc(sense_adc_pin, True)

battery_sensor = BatteryVoltageSensor(
    battery_adc,
    adc_full_scale_mv=ADC_FULL_SCALE_MV,
    divider_ratio=BATTERY_ADC_DIVIDER_RATIO,
    samples=ADC_SAMPLES,
    sample_delay_us=ADC_SAMPLE_DELAY_US,
)
if MEASUREMENT_METHOD == "pretest_selected_cue_short_check":
    channel_sensor = PretestCueSenseSensor(
        outputs,
        pre_test,
        sense_adc,
        adc_full_scale_mv=ADC_FULL_SCALE_MV,
        samples=PRETEST_ADC_SAMPLES,
        sample_delay_us=PRETEST_ADC_SAMPLE_DELAY_US,
        pretest_settle_ms=PRETEST_SETTLE_MS,
        output_settle_us=PRETEST_OUTPUT_SETTLE_US,
    )
else:
    channel_sensor = ChannelSenseSensor(
        scanner,
        sense_adc,
        ChannelCalibration(CHANNEL_SENSE_DIVIDER_RATIO),
        adc_full_scale_mv=ADC_FULL_SCALE_MV,
        samples=ADC_SAMPLES,
        sample_delay_us=ADC_SAMPLE_DELAY_US,
    )
classifier = ChannelClassifier(
    open_max_fraction=CHANNEL_OPEN_MAX_FRACTION,
    short_min_fraction=CHANNEL_SHORT_MIN_FRACTION,
    invalid_is_open=False,
)


def read_battery_mv():
    reading = battery_sensor.read()
    if reading.get("valid"):
        return int(reading.get("battery_mv") or 0), reading
    return int(BATTERY_MV_FALLBACK or 0), reading


def disable_measurement_frontend():
    if scanner is not None:
        scanner.disable_all()


def print_banner():
    print("")
    print("%s v%s" % (APP_NAME, APP_VERSION))
    print("Dummy-load circuit test only. No Nightfire radio/protocol code is running.")
    print("Channels: %d" % CHANNEL_COUNT)
    print(
        "74HC595 DATA=P%d CLOCK=P%d LATCH=P%d OE=P%d SRCLR=P%d Q7S=%s output_walk=%d chain_test=%d" %
        (
            HC595_DATA_PIN,
            HC595_CLOCK_PIN,
            HC595_LATCH_PIN,
            HC595_OE_PIN,
            HC595_SRCLR_PIN,
            "--" if HC595_Q7S_PIN is None else "P%d" % HC595_Q7S_PIN,
            1 if ENABLE_OUTPUT_WALK else 0,
            1 if ENABLE_595_CHAIN_TEST else 0,
        )
    )
    if EXTERNAL_VCC_CONTROL_ENABLED:
        print(
            "External VCC control P%d value=%d settle=%dms" %
            (EXTERNAL_VCC_CONTROL_PIN, EXTERNAL_VCC_ENABLE_VALUE, EXTERNAL_VCC_SETTLE_MS)
        )
    if MEASUREMENT_METHOD == "pretest_selected_cue_short_check":
        print(
            "Pre-test measurement PRE_TEST=P%d SHORT_CHECK=P%d settle=%dms output_settle=%dus" %
            (PRE_TEST_PIN, SHORT_CHECK_PIN, PRETEST_SETTLE_MS, PRETEST_OUTPUT_SETTLE_US)
        )
    else:
        print(
            "74HC4051 S0=P%d S1=P%d S2=P%d EN=P%d/%d/%d SENSE_ADC=P%d" %
            (MUX_S0_PIN, MUX_S1_PIN, MUX_S2_PIN, MUX1_EN_PIN, MUX2_EN_PIN, MUX3_EN_PIN, SENSE_SELECTED_ADC_PIN)
        )
    print("Battery ADC=P%d divider=%.6f channel_divider=%.6f" % (
        BATT_CHECK_PIN,
        BATTERY_ADC_DIVIDER_RATIO,
        CHANNEL_SENSE_DIVIDER_RATIO,
    ))


def output_walk_once():
    print("")
    print("Output walk requested. Enabling /OE only for the walk.")
    outputs.clear()
    outputs.set_enabled(True)
    try:
        for channel in range(1, CHANNEL_COUNT + 1):
            mask = 1 << (channel - 1)
            print("OUT ch=%02d mask=0x%06X" % (channel, mask))
            outputs.digitalWriteMask(mask)
            time.sleep_ms(OUTPUT_WALK_MS)
        outputs.clear()
    finally:
        outputs.set_enabled(False)
        outputs.clear()
        print("Output walk complete. /OE disabled and outputs cleared.")


def chain_detect_once():
    print("")
    print("595 chain detect requested. /OE stays disabled.")
    result = outputs.detect_chain_bits(HC595_CHAIN_TEST_MAX_BITS)
    if result.get("valid"):
        print(
            "595 CHAIN bits=%d registers=%d cue_slots=%d" %
            (result.get("chain_bits", 0), result.get("registers", 0), result.get("cue_slots", 0))
        )
        expected_bits = int(HC595_EXPECTED_CHAIN_BITS or 0)
        if expected_bits and int(result.get("chain_bits") or 0) != expected_bits:
            print("595 CHAIN FAIL expected=%d" % expected_bits)
        elif expected_bits:
            print("595 CHAIN OK expected=%d" % expected_bits)
    else:
        print("595 CHAIN invalid err=%s" % result.get("error", ""))


def read_sense_scaled(battery_mv):
    scaled = channel_sensor.scaler.read_scaled()
    adc_mv = scaled.get("adc_mv")
    sense_mv = None
    fraction = None
    if adc_mv is not None and CHANNEL_SENSE_DIVIDER_RATIO > 0:
        sense_mv = int(float(adc_mv) / CHANNEL_SENSE_DIVIDER_RATIO)
        if battery_mv and battery_mv > 0:
            fraction = float(sense_mv) / float(battery_mv)
    return scaled, sense_mv, fraction


def raw_mux_sweep_once():
    if MEASUREMENT_METHOD == "pretest_selected_cue_short_check":
        return
    if not MUX_RAW_SWEEP_ENABLED:
        return

    battery_mv, battery = read_battery_mv()
    print("")
    print("RAW MUX ADDRESS SWEEP battery=%dmV valid=%d" % (
        battery_mv,
        1 if battery.get("valid") else 0,
    ))
    print("MUX ADDR S2S1S0 raw    adc_mV  sense_mV  frac")

    for mux_index in range(3):
        for mux_channel in range(8):
            scanner.select_raw(mux_index, mux_channel)
            scaled, sense_mv, fraction = read_sense_scaled(battery_mv)
            bits = "%d%d%d" % (
                1 if (mux_channel & 0x04) else 0,
                1 if (mux_channel & 0x02) else 0,
                1 if (mux_channel & 0x01) else 0,
            )
            print(
                "%d   %d    %s    %-5s  %-6s  %-8s  %s" %
                (
                    mux_index + 1,
                    mux_channel,
                    bits,
                    str(scaled.get("raw")),
                    "--" if scaled.get("adc_mv") is None else "%d" % int(scaled.get("adc_mv")),
                    "--" if sense_mv is None else "%d" % int(sense_mv),
                    "--" if fraction is None else "%.3f" % fraction,
                )
            )

    disable_measurement_frontend()


def scan_once():
    battery_mv, battery = read_battery_mv()
    if battery.get("valid"):
        battery_text = "%dmV raw=%s adc=%.1fmV" % (
            battery_mv,
            str(battery.get("raw_adc")),
            battery.get("adc_mv") or 0,
        )
    else:
        battery_text = "invalid fallback=%dmV err=%s" % (
            battery_mv,
            battery.get("error", ""),
        )

    print("")
    print("BAT %s" % battery_text)
    print("CH  raw    adc_mV  sense_mV  frac    state")

    for channel in range(1, CHANNEL_COUNT + 1):
        reading = channel_sensor.read_channel(channel, battery_mv)
        state = classifier.classify(reading)
        fraction = reading.get("battery_fraction")
        if fraction is None:
            fraction_text = "--"
        else:
            fraction_text = "%.3f" % fraction
        print(
            "%02d  %-5s  %-6s  %-8s  %-6s  %s" %
            (
                channel,
                str(reading.get("raw_adc")),
                "--" if reading.get("adc_mv") is None else "%d" % int(reading.get("adc_mv")),
                "--" if reading.get("sense_mv") is None else "%d" % int(reading.get("sense_mv")),
                fraction_text,
                state,
            )
        )

    disable_measurement_frontend()


def main():
    outputs.begin(enable_outputs=False)
    disable_measurement_frontend()
    print_banner()

    if ENABLE_OUTPUT_WALK:
        output_walk_once()
    else:
        print("Output walk skipped. Set ENABLE_OUTPUT_WALK=True to drive dummy loads.")

    if ENABLE_595_CHAIN_TEST:
        chain_detect_once()
    else:
        print("595 chain detect skipped. Set ENABLE_595_CHAIN_TEST=True to read Q7S.")

    raw_mux_sweep_once()

    while True:
        scan_once()
        time.sleep_ms(SCAN_INTERVAL_MS)


main()
