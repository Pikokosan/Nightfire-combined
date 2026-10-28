import time

import machine
import module_ota
import nrf_module_common
import nrf_radio
from analog_mux74hc4051 import HC4051Scanner
from field_measurement import (
    BatteryVoltageSensor,
    ChannelCalibration,
    ChannelClassifier,
    ChannelSenseSensor,
    PretestCueSenseSensor,
    ResistanceCalibration,
    STATE_FAULT,
    STATE_NORMAL,
    STATE_OPEN,
)
from nfcp import *
from machine import I2C, Pin
from shift_register74hc595 import ShiftRegister74HC59524

try:
    from lcd1602_i2c import I2cLcd1602
except Exception:
    I2cLcd1602 = None

try:
    import nrf_node_config as node_config
except Exception:
    node_config = None


RUNTIME_CONFIG_PATH = "field_runtime.bin"
RUNTIME_CONFIG_MAGIC = b"NFRT\x01"
RESISTANCE_CALIBRATION_PATH = "field_resistance_cal.bin"
RESISTANCE_CALIBRATION_MAGIC = b"NFRC\x01"
RESISTANCE_CALIBRATION_BANK_SIZE = 8


def load_crc_payload(path, magic):
    try:
        with open(path, "rb") as data_file:
            data = data_file.read()
        if len(data) < len(magic) + 2 or data[:len(magic)] != magic:
            return None
        if crc16_ccitt(data[:-2]) != int.from_bytes(data[-2:], "big"):
            return None
        return data[:-2]
    except Exception:
        return None


def save_crc_payload(path, payload):
    data = bytearray(payload)
    data += crc16_ccitt(data).to_bytes(2, "big")
    with open(path, "wb") as data_file:
        data_file.write(data)


def load_runtime_config():
    payload = load_crc_payload(RUNTIME_CONFIG_PATH, RUNTIME_CONFIG_MAGIC)
    if payload is None or len(payload) != 14:
        return None
    return (
        int.from_bytes(payload[5:9], "big"),
        int.from_bytes(payload[9:11], "big"),
        int.from_bytes(payload[11:13], "big"),
        payload[13],
    )


RUNTIME_CONFIG = load_runtime_config()


def load_resistance_calibration_banks(default=()):
    data = load_crc_payload(RESISTANCE_CALIBRATION_PATH, RESISTANCE_CALIBRATION_MAGIC)
    if data is None or len(data) < 6:
        return tuple(default or ())
    try:
        bank_count = int(data[5])
        index = 6
        banks = []
        for _ in range(bank_count):
            if index >= len(data):
                return tuple(default or ())
            point_count = int(data[index])
            index += 1
            points = []
            for _point in range(point_count):
                if index + 4 > len(data):
                    return tuple(default or ())
                adc_mv = int.from_bytes(data[index:index + 2], "big")
                deci_ohms = int.from_bytes(data[index + 2:index + 4], "big")
                points.append((adc_mv, deci_ohms))
                index += 4
            if points and not ResistanceCalibration(points).is_valid():
                return tuple(default or ())
            banks.append(tuple(points))
        if index != len(data):
            return tuple(default or ())
        return tuple(banks)
    except Exception:
        return tuple(default or ())


def save_resistance_calibration_banks(banks):
    payload = bytearray(RESISTANCE_CALIBRATION_MAGIC)
    payload.append(len(banks) & 0xFF)
    for bank in banks:
        points = tuple(bank or ())
        payload.append(len(points) & 0xFF)
        for adc_mv, deci_ohms in points:
            payload += (int(adc_mv) & 0xFFFF).to_bytes(2, "big")
            payload += (int(deci_ohms) & 0xFFFF).to_bytes(2, "big")
    save_crc_payload(RESISTANCE_CALIBRATION_PATH, payload)


APP_FIRMWARE_NAME = "field-module"
APP_FIRMWARE_VERSION = "0.2.0"

DEFAULT_NETWORK_ID = 0x04D2
DEFAULT_GROUP_ID = 0x0001
DEFAULT_CUE_COUNT = 24
DEFAULT_DEVICE_TYPE = 0x20

RADIO_CHANNEL = RUNTIME_CONFIG[3] if RUNTIME_CONFIG else getattr(node_config, "RADIO_CHANNEL", 90)
RADIO_ADDRESS = 0x67676767
RADIO_TX_POWER = 8
RADIO_RX_TIMEOUT_MS = getattr(node_config, "RADIO_RX_TIMEOUT_MS", 50)
RADIO_CCA_ENABLED = getattr(node_config, "RADIO_CCA_ENABLED", True)
RADIO_CCA_MS = getattr(node_config, "RADIO_CCA_MS", 2)
RADIO_CCA_BUSY_DBM = getattr(node_config, "RADIO_CCA_BUSY_DBM", -85)
RADIO_CCA_RETRIES = getattr(node_config, "RADIO_CCA_RETRIES", 4)
RADIO_CCA_BACKOFF_MIN_MS = getattr(node_config, "RADIO_CCA_BACKOFF_MIN_MS", 2)
RADIO_CCA_BACKOFF_MAX_MS = getattr(node_config, "RADIO_CCA_BACKOFF_MAX_MS", 10)
RX_DEDUPE_WINDOW_MS = 1500
RX_DEDUPE_CACHE_LIMIT = 16

DISCOVER_BACKOFF_MIN_MS = 250
DISCOVER_BACKOFF_MAX_MS = 500
DISCOVER_RESPONSE_REPEATS = 3
DISCOVER_RESPONSE_GAP_MS = 35
SCRIPT_MAX_ENTRIES = 192
SCRIPT_HEARTBEAT_TIMEOUT_MS = 1500
SCRIPT_TIME_SYNC_VALID_MS = 10000
BATTERY_PACK_TYPE = getattr(node_config, "BATTERY_PACK_TYPE", "2S")
BATTERY_MV = getattr(node_config, "BATTERY_MV", 0)

# Board-specific assignments come from nrf_node_config.py. The local
# MicroPython build addresses P0.nn as nn and P1.nn as 32 + nn.
BOARD_PINOUT = getattr(node_config, "BOARD_PINOUT", "legacy")
EXTERNAL_VCC_CONTROL_ENABLED = getattr(node_config, "EXTERNAL_VCC_CONTROL_ENABLED", True)
EXTERNAL_VCC_CONTROL_PIN = getattr(node_config, "EXTERNAL_VCC_CONTROL_PIN", 13)  # P0.13 board VCC control
EXTERNAL_VCC_ENABLE_VALUE = getattr(node_config, "EXTERNAL_VCC_ENABLE_VALUE", 1)
EXTERNAL_VCC_SETTLE_MS = getattr(node_config, "EXTERNAL_VCC_SETTLE_MS", 50)

PRE_TEST_PIN = getattr(node_config, "PRE_TEST_PIN", 9)
ARM_CONTROL_PIN = getattr(node_config, "ARM_CONTROL_PIN", None)
ARM_CONTROL_ACTIVE_VALUE = 1 if getattr(node_config, "ARM_CONTROL_ACTIVE_VALUE", 1) else 0
SAFE_LED_PIN = getattr(node_config, "SAFE_LED_PIN", 10)
ARM_LED_PIN = getattr(node_config, "ARM_LED_PIN", 11)
UP_BUTTON_PIN = getattr(node_config, "UP_BUTTON_PIN", 33)      # P1.01 bottom pad
DOWN_BUTTON_PIN = getattr(node_config, "DOWN_BUTTON_PIN", 34)  # P1.02 bottom pad
OK_FWD_BUTTON_PIN = getattr(node_config, "OK_FWD_BUTTON_PIN", getattr(node_config, "OK_BUTTON_PIN", None))
BACK_BUTTON_PIN = getattr(node_config, "BACK_BUTTON_PIN", None)
BATT_CHECK_PIN = getattr(node_config, "BATT_CHECK_PIN", 29)    # P0.29 / D20 / AIN5
LCD_SDA_PIN = getattr(node_config, "LCD_SDA_PIN", 39)          # P1.07 bottom pad
LCD_SCL_PIN = getattr(node_config, "LCD_SCL_PIN", 35)          # P1.03 if LCD is fitted later
LCD_ENABLED = getattr(node_config, "LCD_ENABLED", True)
LCD_I2C_ID = getattr(node_config, "LCD_I2C_ID", 0)
LCD_I2C_FREQ = getattr(node_config, "LCD_I2C_FREQ", 100000)
LCD_ADDRESS = getattr(node_config, "LCD_ADDRESS", None)
LCD_MAPPING = getattr(node_config, "LCD_MAPPING", "upper")
LCD_COLS = getattr(node_config, "LCD_COLS", 16)
LCD_ROWS = getattr(node_config, "LCD_ROWS", 2)
LCD_REFRESH_MS = getattr(node_config, "LCD_REFRESH_MS", 350)
LCD_BUTTON_DEBOUNCE_MS = getattr(node_config, "LCD_BUTTON_DEBOUNCE_MS", 80)
LCD_CONTINUITY_PAGE_SIZE = getattr(node_config, "LCD_CONTINUITY_PAGE_SIZE", 8)
MASTER_LINK_TIMEOUT_MS = getattr(node_config, "MASTER_LINK_TIMEOUT_MS", 15000)


valid_radio_channel = nrf_module_common.valid_radio_channel

HC595_DATA_PIN = getattr(node_config, "HC595_DATA_PIN", 6)     # P0.06 / D1
HC595_CLOCK_PIN = getattr(node_config, "HC595_CLOCK_PIN", 8)   # P0.08 / D0
HC595_LATCH_PIN = getattr(node_config, "HC595_LATCH_PIN", 17)  # P0.17 / D2
HC595_OE_PIN = getattr(node_config, "HC595_OE_PIN", 20)        # P0.20 / D3
HC595_SRCLR_PIN = getattr(node_config, "HC595_SRCLR_PIN", 22)  # P0.22 / D4
HC595_Q7S_PIN = getattr(node_config, "HC595_Q7S_PIN", None)
HC595_CLOCK_DELAY_US = getattr(node_config, "HC595_CLOCK_DELAY_US", 1)
STARTUP_595_CHAIN_TEST_ENABLED = getattr(node_config, "STARTUP_595_CHAIN_TEST_ENABLED", False)
HC595_EXPECTED_CHAIN_BITS = getattr(node_config, "HC595_EXPECTED_CHAIN_BITS", 0)
HC595_CHAIN_TEST_MAX_BITS = getattr(node_config, "HC595_CHAIN_TEST_MAX_BITS", 64)

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

FIRE_PULSE_MS = getattr(node_config, "FIRE_PULSE_MS", 5)
LOG_FIRE_EVENTS = getattr(node_config, "LOG_FIRE_EVENTS", False)
LOG_SCRIPT_EVENTS = getattr(node_config, "LOG_SCRIPT_EVENTS", False)
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
CHANNEL_INVALID_IS_OPEN = getattr(node_config, "CHANNEL_INVALID_IS_OPEN", True)
CHANNEL_OPEN_MIN_FRACTION = getattr(node_config, "CHANNEL_OPEN_MIN_FRACTION", None)
RESISTANCE_CALIBRATION_POINTS = getattr(node_config, "RESISTANCE_CALIBRATION_POINTS", ())
RESISTANCE_CALIBRATION_BANKS = load_resistance_calibration_banks()

CONT_STATUS_OK = 0x00
CONT_STATUS_BATTERY_INVALID = 0x01
CONT_STATUS_ADC_INVALID = 0x02
CONT_STATUS_FAULT_DETECTED = 0x04

def get_default_unique_id():
    return nrf_module_common.get_default_unique_id(0x52840001)


CONFIG_UNIQUE_ID = RUNTIME_CONFIG[0] if RUNTIME_CONFIG else getattr(node_config, "UNIQUE_ID", 0)
GROUP_ID = RUNTIME_CONFIG[1] if RUNTIME_CONFIG else getattr(node_config, "GROUP_ID", DEFAULT_GROUP_ID)
UNIQUE_ID = (CONFIG_UNIQUE_ID & 0xFFFFFFFF) or get_default_unique_id()
CUE_COUNT = int(getattr(node_config, "CUE_COUNT", DEFAULT_CUE_COUNT))
if CUE_COUNT < 1:
    CUE_COUNT = 1
if CUE_COUNT > DEFAULT_CUE_COUNT:
    CUE_COUNT = DEFAULT_CUE_COUNT
DEVICE_TYPE = DEFAULT_DEVICE_TYPE
NETWORK_ID = RUNTIME_CONFIG[2] if RUNTIME_CONFIG else getattr(node_config, "NETWORK_ID", DEFAULT_NETWORK_ID)
ARMED = False
STARTUP_FAULT = False
STARTUP_FAULT_REASON = ""
STARTUP_595_CHAIN_RESULT = None
script_id_loaded = None
script_entries = []
script_running = False
script_start_ms = 0
script_next_index = 0
script_last_heartbeat_ms = 0
script_last_event = "idle"
script_time_synced = False
script_master_offset_ms = 0
script_time_sync_local_ms = 0

parser = NFCPStreamParser()
nfcp = NFCP(node_id=0x01)
tx_seq = 1
rx_seen = []
fire_out_mask = 0
fire_deadlines = [0] * CUE_COUNT
last_cont_status = CONT_STATUS_OK
last_cont_open_mask = None
last_cont_ms = 0
lcd_display = None
lcd_buttons = []
lcd_mode = "home"
lcd_menu_index = 0
lcd_active_view = ""
lcd_cont_detail_index = -1
lcd_edit_target = ""
lcd_edit_value = 0
lcd_force_refresh = True
lcd_last_refresh_ms = 0
lcd_local_scan_pending = False
last_master_rx_ms = 0

LCD_MENU_ITEMS = (
    "System Info",
    "Continuity",
    "Script",
    "Set Group",
    "Set Network",
    "Set Channel",
)


def record_startup_fault(reason):
    global STARTUP_FAULT, STARTUP_FAULT_REASON

    STARTUP_FAULT = True
    STARTUP_FAULT_REASON = str(reason or "startup fault")
    print("STARTUP FAULT:", STARTUP_FAULT_REASON)


class NullOutputs:
    def __init__(self, reason):
        self.reason = str(reason or "outputs unavailable")

    def begin(self, enable_outputs=False):
        pass

    def set_enabled(self, enabled):
        pass

    def clear_storage(self):
        pass

    def clear(self):
        pass

    def digitalWriteMask(self, mask):
        raise RuntimeError(self.reason)

    def detect_chain_bits(self, max_bits=None):
        return {
            "valid": False,
            "chain_bits": 0,
            "registers": 0,
            "cue_slots": 0,
            "error": self.reason,
        }


class NullPin:
    def __init__(self, fallback=0):
        self._value = fallback

    def value(self, new_value=None):
        if new_value is None:
            return self._value
        self._value = 1 if new_value else 0
        return self._value


def make_pin(pin_id, mode, pull=None, value=None, name="pin"):
    try:
        if pull is not None:
            return Pin(pin_id, mode, pull)
        if value is not None:
            return Pin(pin_id, mode, value=value)
        return Pin(pin_id, mode)
    except Exception as e:
        record_startup_fault("%s P%s init failed: %s" % (name, str(pin_id), str(e)))
        return NullPin(1 if mode == Pin.IN else 0)


safe_led = make_pin(SAFE_LED_PIN, Pin.OUT, value=1, name="safe led")
arm_led = make_pin(ARM_LED_PIN, Pin.OUT, value=0, name="arm led")
pre_test = make_pin(PRE_TEST_PIN, Pin.OUT, value=0, name="pre-test")
arm_control = (
    make_pin(
        ARM_CONTROL_PIN,
        Pin.OUT,
        value=1 - ARM_CONTROL_ACTIVE_VALUE,
        name="arm control",
    )
    if ARM_CONTROL_PIN is not None else None
)
up_button = make_pin(UP_BUTTON_PIN, Pin.IN, Pin.PULL_UP, name="up button")
down_button = make_pin(DOWN_BUTTON_PIN, Pin.IN, Pin.PULL_UP, name="down button")
ok_fwd_button = make_pin(OK_FWD_BUTTON_PIN, Pin.IN, Pin.PULL_UP, name="ok/fwd button") if OK_FWD_BUTTON_PIN is not None else None
back_button = make_pin(BACK_BUTTON_PIN, Pin.IN, Pin.PULL_UP, name="back button") if BACK_BUTTON_PIN is not None else None

try:
    lcd_i2c = None
    if LCD_ENABLED:
        try:
            lcd_i2c = I2C(
                LCD_I2C_ID,
                sda=Pin(LCD_SDA_PIN),
                scl=Pin(LCD_SCL_PIN),
                freq=LCD_I2C_FREQ,
            )
        except TypeError:
            lcd_i2c = I2C(LCD_I2C_ID, sda=Pin(LCD_SDA_PIN), scl=Pin(LCD_SCL_PIN))
except Exception as e:
    lcd_i2c = None
    print("LCD I2C init skipped:", e)


class ReconfiguringADC:
    """Reselect an nRF SAADC input before every sample.

    The nRF MicroPython port shares SAADC configuration between ADC objects.
    Without reselection, alternating battery and continuity reads can return
    the value from whichever input was configured most recently.
    """

    def __init__(self, pin):
        self.pin = pin

    def read_u16(self):
        try:
            adc = machine.ADC(Pin(self.pin))
        except Exception:
            adc = machine.ADC(self.pin)
        return adc.read_u16()


def make_adc(pin):
    return ReconfiguringADC(pin)


try:
    battery_adc = make_adc(BATT_CHECK_PIN) if BATTERY_ADC_ENABLED else None
except Exception as e:
    battery_adc = None
    print("Battery ADC init failed:", e)

try:
    sense_adc_pin = SHORT_CHECK_PIN if MEASUREMENT_METHOD == "pretest_selected_cue_short_check" else SENSE_SELECTED_ADC_PIN
    sense_adc = make_adc(sense_adc_pin)
except Exception as e:
    sense_adc = None
    print("Channel sense ADC init failed:", e)


def set_external_vcc(enabled):
    if not EXTERNAL_VCC_CONTROL_ENABLED:
        return None

    value = EXTERNAL_VCC_ENABLE_VALUE if enabled else 1 - EXTERNAL_VCC_ENABLE_VALUE
    pin = Pin(EXTERNAL_VCC_CONTROL_PIN, Pin.OUT, value=value)
    if enabled and EXTERNAL_VCC_SETTLE_MS:
        time.sleep_ms(EXTERNAL_VCC_SETTLE_MS)
    return pin


try:
    external_vcc = set_external_vcc(True)
except Exception as e:
    external_vcc = None
    record_startup_fault("external VCC control init failed: " + str(e))

try:
    outputs = ShiftRegister74HC59524(
        data_pin=HC595_DATA_PIN,
        clock_pin=HC595_CLOCK_PIN,
        latch_pin=HC595_LATCH_PIN,
        oe_pin=HC595_OE_PIN,
        srclr_pin=HC595_SRCLR_PIN,
        q7s_pin=HC595_Q7S_PIN,
        bit_count=24,
        used_outputs=CUE_COUNT,
        clock_delay_us=HC595_CLOCK_DELAY_US,
    )
    outputs.begin(enable_outputs=False)
except Exception as e:
    outputs = NullOutputs("74HC595 init failed: " + str(e))
    record_startup_fault(outputs.reason)

scanner = None
if MEASUREMENT_METHOD != "pretest_selected_cue_short_check":
    try:
        scanner = HC4051Scanner(
            s0_pin=MUX_S0_PIN,
            s1_pin=MUX_S1_PIN,
            s2_pin=MUX_S2_PIN,
            en_pins=(MUX1_EN_PIN, MUX2_EN_PIN, MUX3_EN_PIN),
            channel_count=CUE_COUNT,
            settle_us=MUX_SETTLE_US,
        )
    except Exception as e:
        scanner = None
        record_startup_fault("74HC4051 init failed: " + str(e))

battery_sensor = BatteryVoltageSensor(
    battery_adc,
    adc_full_scale_mv=ADC_FULL_SCALE_MV,
    divider_ratio=BATTERY_ADC_DIVIDER_RATIO,
    samples=ADC_SAMPLES,
    sample_delay_us=ADC_SAMPLE_DELAY_US,
)
channel_calibration = ChannelCalibration(CHANNEL_SENSE_DIVIDER_RATIO)
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
        channel_calibration,
        adc_full_scale_mv=ADC_FULL_SCALE_MV,
        samples=ADC_SAMPLES,
        sample_delay_us=ADC_SAMPLE_DELAY_US,
    )
channel_classifier = ChannelClassifier(
    open_max_fraction=CHANNEL_OPEN_MAX_FRACTION,
    short_min_fraction=CHANNEL_SHORT_MIN_FRACTION,
    invalid_is_open=CHANNEL_INVALID_IS_OPEN,
    open_min_fraction=CHANNEL_OPEN_MIN_FRACTION,
)


class DebouncedButton:
    def __init__(self, pin, debounce_ms=80):
        self.pin = pin
        self.debounce_ms = int(debounce_ms)
        self.last_raw = self.read_raw()
        self.stable = self.last_raw
        self.changed_ms = time.ticks_ms()

    def read_raw(self):
        if self.pin is None:
            return False
        try:
            return self.pin.value() == 0
        except Exception:
            return False

    def update(self, now):
        raw = self.read_raw()
        if raw != self.last_raw:
            self.last_raw = raw
            self.changed_ms = now
            return False

        if raw != self.stable and time.ticks_diff(now, self.changed_ms) >= self.debounce_ms:
            self.stable = raw
            return self.stable

        return False


def count_mask_bits(mask):
    mask = int(mask or 0)
    count = 0
    while mask:
        count += mask & 1
        mask >>= 1
    return count


def lcd_trim(text):
    return str(text)[:LCD_COLS]


def set_last_continuity(status, open_mask):
    global last_cont_status, last_cont_open_mask, last_cont_ms, lcd_force_refresh

    last_cont_status = int(status or CONT_STATUS_OK)
    last_cont_open_mask = int(open_mask or 0) & ((1 << CUE_COUNT) - 1)
    last_cont_ms = time.ticks_ms()
    lcd_force_refresh = True


def record_master_rx():
    global last_master_rx_ms, lcd_force_refresh

    last_master_rx_ms = time.ticks_ms()
    lcd_force_refresh = True


def check_master_link_failsafe():
    global ARMED, script_running, lcd_force_refresh

    if not ARMED:
        return
    if not last_master_rx_ms:
        return
    if time.ticks_diff(time.ticks_ms(), last_master_rx_ms) <= MASTER_LINK_TIMEOUT_MS:
        return

    ARMED = False
    script_running = False
    clear_fire_outputs()
    set_armed_outputs(False)
    lcd_force_refresh = True
    print("DISARMED - master link timeout")


def init_lcd_ui():
    global lcd_display, lcd_buttons, lcd_force_refresh

    if not LCD_ENABLED:
        return
    if I2cLcd1602 is None:
        print("LCD driver unavailable")
        return
    if lcd_i2c is None:
        return

    try:
        address = I2cLcd1602.find_address(lcd_i2c, LCD_ADDRESS)
        if address is None:
            print("LCD not found on I2C")
            return

        lcd_display = I2cLcd1602(lcd_i2c, address, LCD_COLS, LCD_ROWS, mapping=LCD_MAPPING)
        lcd_display.show_lines("Nightfire Field", "Booting...")
        lcd_buttons = [
            ("up", DebouncedButton(up_button, LCD_BUTTON_DEBOUNCE_MS)),
            ("down", DebouncedButton(down_button, LCD_BUTTON_DEBOUNCE_MS)),
        ]
        if ok_fwd_button is not None:
            lcd_buttons.append(("ok", DebouncedButton(ok_fwd_button, LCD_BUTTON_DEBOUNCE_MS)))
        if back_button is not None:
            lcd_buttons.append(("back", DebouncedButton(back_button, LCD_BUTTON_DEBOUNCE_MS)))
        lcd_force_refresh = True
        print("LCD ready at 0x%02X mapping=%s" % (address, LCD_MAPPING))
    except Exception as e:
        lcd_display = None
        print("LCD init failed:", e)


def lcd_continuity_detail_pages():
    page_size = max(1, int(LCD_CONTINUITY_PAGE_SIZE or 1))
    return max(1, (CUE_COUNT + page_size - 1) // page_size)


def lcd_start_edit(target, value):
    global lcd_mode, lcd_edit_target, lcd_edit_value

    lcd_mode = "edit"
    lcd_edit_target = target
    lcd_edit_value = int(value or 0)


def lcd_open_menu_item():
    global lcd_mode, lcd_active_view, lcd_cont_detail_index

    item = LCD_MENU_ITEMS[lcd_menu_index]
    if item == "System Info":
        lcd_mode = "view"
        lcd_active_view = "system"
    elif item == "Continuity":
        lcd_mode = "view"
        lcd_active_view = "continuity"
        lcd_cont_detail_index = -1
    elif item == "Script":
        lcd_mode = "view"
        lcd_active_view = "script"
    elif item == "Set Group":
        lcd_start_edit("group", GROUP_ID)
    elif item == "Set Network":
        lcd_start_edit("network", NETWORK_ID)
    elif item == "Set Channel":
        lcd_start_edit("channel", RADIO_CHANNEL)


def lcd_adjust_edit(delta):
    global lcd_edit_value

    if lcd_edit_target == "channel":
        lcd_edit_value = (lcd_edit_value + delta) % 101
        while not valid_radio_channel(lcd_edit_value):
            lcd_edit_value = (lcd_edit_value + delta) % 101
    else:
        lcd_edit_value = (lcd_edit_value + delta) & 0xFFFF


def lcd_save_edit():
    global GROUP_ID, NETWORK_ID, RADIO_CHANNEL, lcd_mode, lcd_active_view

    if ARMED:
        lcd_mode = "view"
        lcd_active_view = "edit_blocked"
        return

    previous = runtime_config_values()
    if lcd_edit_target == "group":
        GROUP_ID = lcd_edit_value & 0xFFFF
    elif lcd_edit_target == "network":
        NETWORK_ID = lcd_edit_value & 0xFFFF
    elif lcd_edit_target == "channel":
        if not valid_radio_channel(lcd_edit_value):
            lcd_mode = "view"
            lcd_active_view = "edit_blocked"
            return
        RADIO_CHANNEL = lcd_edit_value % 101

    if not persist_runtime_change(previous):
        lcd_mode = "view"
        lcd_active_view = "edit_blocked"
        return

    if lcd_edit_target == "channel":
        try:
            nrf_radio.init(
                channel=RADIO_CHANNEL,
                tx_power=RADIO_TX_POWER,
                address=RADIO_ADDRESS,
            )
        except Exception as e:
            print("LCD channel radio init failed:", e)

    lcd_mode = "menu"


def lcd_button_action(name):
    global lcd_mode, lcd_menu_index, lcd_force_refresh, lcd_local_scan_pending, lcd_cont_detail_index

    if lcd_mode == "home":
        if name == "ok":
            lcd_mode = "menu"
        lcd_force_refresh = True
        return

    if lcd_mode == "menu":
        if name == "up":
            lcd_menu_index = (lcd_menu_index - 1) % len(LCD_MENU_ITEMS)
        elif name == "down":
            lcd_menu_index = (lcd_menu_index + 1) % len(LCD_MENU_ITEMS)
        elif name == "back":
            lcd_mode = "home"
        elif name == "ok":
            lcd_open_menu_item()
        lcd_force_refresh = True
        return

    if lcd_mode == "view":
        if name == "back":
            lcd_mode = "menu"
        elif lcd_active_view == "continuity" and name == "ok":
            lcd_local_scan_pending = True
        elif lcd_active_view == "continuity" and name == "up":
            lcd_cont_detail_index -= 1
            if lcd_cont_detail_index < -1:
                lcd_cont_detail_index = lcd_continuity_detail_pages() - 1
        elif lcd_active_view == "continuity" and name == "down":
            lcd_cont_detail_index += 1
            if lcd_cont_detail_index >= lcd_continuity_detail_pages():
                lcd_cont_detail_index = -1
        elif name == "ok":
            lcd_mode = "menu"
        lcd_force_refresh = True
        return

    if lcd_mode == "edit":
        if name == "up":
            lcd_adjust_edit(1)
        elif name == "down":
            lcd_adjust_edit(-1)
        elif name == "back":
            lcd_mode = "menu"
        elif name == "ok":
            lcd_save_edit()

    lcd_force_refresh = True


def lcd_status_text():
    if STARTUP_FAULT:
        return "FAULT"
    if ARMED:
        return "ARMED"
    return "SAFE"


def lcd_battery_short_text():
    try:
        battery_mv = read_battery_mv()
    except Exception:
        battery_mv = 0
    return "%d.%02dV" % (battery_mv // 1000, (battery_mv % 1000) // 10)


def lcd_home_top_line():
    left = lcd_battery_short_text()
    right = "G%04X" % (GROUP_ID & 0xFFFF)
    gap = LCD_COLS - len(left) - len(right)
    if gap < 1:
        gap = 1
    return left + (" " * gap) + right


def lcd_master_link_text():
    if not last_master_rx_ms:
        return "M:--"

    age = time.ticks_diff(time.ticks_ms(), last_master_rx_ms)
    return "M:OK" if age <= MASTER_LINK_TIMEOUT_MS else "M:--"


def lcd_home_bottom_line():
    return "%s %08X" % (lcd_master_link_text(), UNIQUE_ID & 0xFFFFFFFF)


def lcd_menu_lines():
    item = LCD_MENU_ITEMS[lcd_menu_index]
    next_item = LCD_MENU_ITEMS[(lcd_menu_index + 1) % len(LCD_MENU_ITEMS)]
    return ">%s" % item, " %s" % next_item


def lcd_edit_lines():
    if lcd_edit_target == "channel":
        return "Channel", "%03d OK=save" % lcd_edit_value
    if lcd_edit_target == "network":
        return "Network ID", "%04X OK=save" % (lcd_edit_value & 0xFFFF)
    return "Group ID", "%04X OK=save" % (lcd_edit_value & 0xFFFF)


def lcd_continuity_lines():
    if lcd_cont_detail_index < 0:
        if last_cont_open_mask is None:
            return ("Cont not checked", "OK=Scan")

        open_count = count_mask_bits(last_cont_open_mask)
        good = CUE_COUNT - open_count
        status = "OK" if last_cont_status == CONT_STATUS_OK else "WARN%02X" % last_cont_status
        return (
            "Cont %02d/%02d %s" % (good, CUE_COUNT, status),
            "OK=Scan UP/DN",
        )

    page_size = max(1, int(LCD_CONTINUITY_PAGE_SIZE or 1))
    start = lcd_cont_detail_index * page_size + 1
    end = min(CUE_COUNT, start + page_size - 1)

    if last_cont_open_mask is None:
        return ("Cues %02d-%02d" % (start, end), "-- no data --")

    parts = []
    for cue in range(start, end + 1):
        open_bit = bool(last_cont_open_mask & (1 << (cue - 1)))
        parts.append("%02d%s" % (cue, " " if open_bit else chr(255)))

    return (
        " ".join(parts[:4]),
        " ".join(parts[4:8]),
    )


def lcd_page_lines():
    if lcd_mode == "home":
        return (
            lcd_home_top_line(),
            lcd_home_bottom_line(),
        )

    if lcd_mode == "menu":
        return lcd_menu_lines()

    if lcd_mode == "edit":
        return lcd_edit_lines()

    if lcd_active_view == "system":
        return (
            "FW %s C%02d" % (APP_FIRMWARE_VERSION, CUE_COUNT),
            "Net%04X Ch%03d" % (NETWORK_ID & 0xFFFF, RADIO_CHANNEL & 0xFF),
        )

    if lcd_active_view == "script":
        script_id_text = "----" if script_id_loaded is None else "%04X" % script_id_loaded
        return (
            "Script %s" % script_id_text,
            "%s Run%d N%03d" % (lcd_status_text(), 1 if script_running else 0, script_next_index),
        )

    if lcd_active_view == "continuity":
        return lcd_continuity_lines()

    if lcd_active_view == "edit_blocked":
        return "Disarm first", "BACK=menu"

    return lcd_home_top_line(), lcd_home_bottom_line()


def run_lcd_continuity_scan():
    global lcd_local_scan_pending

    if not lcd_local_scan_pending:
        return

    lcd_local_scan_pending = False
    if lcd_display:
        try:
            lcd_display.show_lines("Checking cues", "Please wait...")
        except Exception:
            pass

    try:
        unit, status, values = read_resistance_values()
        open_mask = 0
        for index, value in enumerate(values):
            if value in (RESISTANCE_VALUE_OPEN, RESISTANCE_VALUE_INVALID):
                open_mask |= 1 << index
            elif unit == RESISTANCE_UNIT_DECI_OHM and int(value) <= 5:
                open_mask |= 1 << index
        set_last_continuity(status, open_mask)
        print("LCD RES status=0x%02X open_mask=0x%06X" % (status, open_mask))
    except Exception as e:
        print("LCD continuity scan failed:", e)


def service_lcd_ui():
    global lcd_display, lcd_last_refresh_ms, lcd_force_refresh

    if lcd_display is None:
        return

    try:
        now = time.ticks_ms()
        for name, button in lcd_buttons:
            if button.update(now):
                lcd_button_action(name)

        run_lcd_continuity_scan()

        if lcd_force_refresh or time.ticks_diff(now, lcd_last_refresh_ms) >= LCD_REFRESH_MS:
            line1, line2 = lcd_page_lines()
            lcd_display.show_lines(lcd_trim(line1), lcd_trim(line2))
            lcd_last_refresh_ms = now
            lcd_force_refresh = False
    except Exception as e:
        lcd_display = None
        print("LCD service failed:", e)


def ticks_rand(limit):
    if limit <= 0:
        return 0
    return (time.ticks_us() ^ time.ticks_ms()) % limit


def next_tx_seq():
    global tx_seq

    seq = tx_seq & 0xFF
    tx_seq = (tx_seq + 1) & 0xFF
    if tx_seq == 0:
        tx_seq = 1
    return seq


def tx_radio_payload(payload, use_cca=True):
    return nrf_module_common.send_with_cca(
        payload,
        enabled=use_cca and RADIO_CCA_ENABLED,
        cca_ms=RADIO_CCA_MS,
        busy_dbm=RADIO_CCA_BUSY_DBM,
        retries=RADIO_CCA_RETRIES,
        backoff_min_ms=RADIO_CCA_BACKOFF_MIN_MS,
        backoff_max_ms=RADIO_CCA_BACKOFF_MAX_MS,
        rand_fn=ticks_rand,
    )


def tx_nfcp(frame, repeats=3, gap_ms=20):
    payload = (NETWORK_ID & 0xFFFF).to_bytes(2, "big") + bytes([next_tx_seq()]) + frame
    for i in range(repeats):
        tx_radio_payload(payload)
        if i + 1 < repeats:
            time.sleep_ms(gap_ms)


def remember_rx_packet(seq, pkt):
    global rx_seen

    if seq is None:
        return False

    now = time.ticks_ms()
    signature = (
        pkt.get("frame_type"),
        pkt.get("src"),
        pkt.get("target"),
        pkt.get("cmd"),
        bytes(pkt.get("data", b"")[:16]),
    )
    kept = []
    duplicate = False

    for item in rx_seen:
        age = time.ticks_diff(now, item[0])
        if age < RX_DEDUPE_WINDOW_MS:
            kept.append(item)
            if item[1] == seq and item[2] == signature:
                duplicate = True

    if not duplicate:
        kept.append((now, seq, signature))
        if len(kept) > RX_DEDUPE_CACHE_LIMIT:
            kept = kept[-RX_DEDUPE_CACHE_LIMIT:]

    rx_seen = kept
    return duplicate


def address_matches(pkt):
    frame_type = pkt.get("frame_type")
    if frame_type == SOF_BROADCAST:
        return True
    if frame_type == SOF_GROUP:
        return pkt.get("target") == GROUP_ID
    if frame_type == SOF_UNIQUE:
        return pkt.get("target") == UNIQUE_ID
    return False


def reply_ack_if_unique(pkt, orig_cmd, code=ACK_OK):
    if pkt.get("frame_type") == SOF_UNIQUE:
        tx_nfcp(nfcp.build_ack(UNIQUE_ID, orig_cmd, code))


def reply_nack_if_unique(pkt, orig_cmd, code):
    if pkt.get("frame_type") == SOF_UNIQUE:
        tx_nfcp(nfcp.build_nack(UNIQUE_ID, orig_cmd, code))


def encode_text_field(text):
    try:
        data = text.encode()
    except Exception:
        data = b""
    if len(data) > 63:
        data = data[:63]
    return bytes([len(data)]) + data


def build_info_payload():
    return (
        encode_text_field(APP_FIRMWARE_NAME) +
        encode_text_field(APP_FIRMWARE_VERSION) +
        encode_text_field("nrf-radio-native") +
        encode_text_field(build_script_status_text()) +
        encode_text_field("0x%08X" % (get_default_unique_id() & 0xFFFFFFFF)) +
        encode_text_field("0x%08X" % (CONFIG_UNIQUE_ID & 0xFFFFFFFF)) +
        encode_text_field(str(read_battery_mv())) +
        encode_text_field(BATTERY_PACK_TYPE)
    )


def read_battery_mv():
    reading = battery_sensor.read()
    if reading.get("valid"):
        return int(reading.get("battery_mv") or 0)
    if BATTERY_MV:
        return int(BATTERY_MV or 0)
    return int(reading.get("battery_mv") or 0)


def build_script_status_text():
    if script_id_loaded is None:
        script_id_text = "--"
    else:
        script_id_text = "%04X" % script_id_loaded

    hb_age = -1
    if script_last_heartbeat_ms:
        hb_age = time.ticks_diff(time.ticks_ms(), script_last_heartbeat_ms)

    fault_text = " fault=%s" % STARTUP_FAULT_REASON if STARTUP_FAULT else ""

    return "id=%s entries=%d run=%d next=%d hb=%d %s%s" % (
        script_id_text,
        len(script_entries),
        1 if script_running else 0,
        script_next_index,
        hb_age,
        script_last_event,
        fault_text,
    )


def save_runtime_config():
    try:
        payload = bytearray(RUNTIME_CONFIG_MAGIC)
        payload += (CONFIG_UNIQUE_ID & 0xFFFFFFFF).to_bytes(4, "big")
        payload += (GROUP_ID & 0xFFFF).to_bytes(2, "big")
        payload += (NETWORK_ID & 0xFFFF).to_bytes(2, "big")
        payload.append(RADIO_CHANNEL & 0xFF)
        save_crc_payload(RUNTIME_CONFIG_PATH, payload)
        return True
    except Exception as e:
        print("Runtime config save failed:", e)
        return False


def runtime_config_values():
    return CONFIG_UNIQUE_ID, GROUP_ID, NETWORK_ID, RADIO_CHANNEL


def persist_runtime_change(previous):
    global CONFIG_UNIQUE_ID, UNIQUE_ID, GROUP_ID, NETWORK_ID, RADIO_CHANNEL

    if save_runtime_config():
        return True
    CONFIG_UNIQUE_ID, GROUP_ID, NETWORK_ID, RADIO_CHANNEL = previous
    UNIQUE_ID = CONFIG_UNIQUE_ID or get_default_unique_id()
    return False


def set_armed_outputs(armed):
    global lcd_force_refresh

    if armed and STARTUP_FAULT:
        armed = False
    lcd_force_refresh = True
    safe_led.value(0 if armed else 1)
    arm_led.value(1 if armed else 0)
    if arm_control is not None:
        arm_control.value(ARM_CONTROL_ACTIVE_VALUE if armed else 1 - ARM_CONTROL_ACTIVE_VALUE)
    if armed:
        outputs.clear()
        outputs.set_enabled(True)
    else:
        outputs.set_enabled(False)
        outputs.clear()


def clear_fire_outputs():
    global fire_out_mask

    fire_out_mask = 0
    outputs.clear()


def disable_measurement_frontend():
    if scanner is not None:
        scanner.disable_all()


def detect_output_chain(max_bits=64):
    result = outputs.detect_chain_bits(max_bits)
    if result.get("valid"):
        print(
            "595 chain q7s=P%s bits=%d registers=%d cue_slots=%d" %
            (
                "--" if HC595_Q7S_PIN is None else str(HC595_Q7S_PIN),
                result.get("chain_bits", 0),
                result.get("registers", 0),
                result.get("cue_slots", 0),
            )
        )
    else:
        print("595 chain detect failed:", result.get("error", ""))
    return result


def set_startup_fault(reason):
    global ARMED, script_running

    record_startup_fault(reason)
    ARMED = False
    script_running = False
    pre_test.value(0)
    clear_fire_outputs()
    set_armed_outputs(False)


def run_startup_self_tests():
    global STARTUP_FAULT, STARTUP_FAULT_REASON, STARTUP_595_CHAIN_RESULT

    if STARTUP_FAULT:
        print("Startup self-tests skipped due to existing fault:", STARTUP_FAULT_REASON)
        return False

    STARTUP_FAULT = False
    STARTUP_FAULT_REASON = ""
    STARTUP_595_CHAIN_RESULT = None

    if not STARTUP_595_CHAIN_TEST_ENABLED:
        print("Startup 595 chain test skipped.")
        return True

    expected_bits = int(HC595_EXPECTED_CHAIN_BITS or 0)
    result = detect_output_chain(HC595_CHAIN_TEST_MAX_BITS)
    STARTUP_595_CHAIN_RESULT = result

    if not result.get("valid"):
        set_startup_fault("595 chain no q7s feedback")
        return False

    chain_bits = int(result.get("chain_bits") or 0)
    if expected_bits and chain_bits != expected_bits:
        set_startup_fault("595 chain bits %d expected %d" % (chain_bits, expected_bits))
        return False

    print("Startup 595 chain test OK: bits=%d expected=%d" % (chain_bits, expected_bits))
    return True


def poll_fire_outputs():
    global fire_out_mask

    if not fire_out_mask:
        return

    now = time.ticks_ms()
    old_mask = fire_out_mask
    for i in range(CUE_COUNT):
        bit = 1 << i
        if fire_out_mask & bit and time.ticks_diff(now, fire_deadlines[i]) >= 0:
            fire_out_mask &= ~bit

    if fire_out_mask != old_mask:
        outputs.digitalWriteMask(fire_out_mask)


def build_resistance_payload(unit, status, values):
    payload = bytearray((
        RESISTANCE_PAYLOAD_VERSION,
        int(unit) & 0xFF,
        CUE_COUNT & 0xFF,
        int(status) & 0xFF,
    ))
    for value in values:
        payload += (int(value) & 0xFFFF).to_bytes(2, "big")
    return bytes(payload)


def read_resistance_values(force_raw_adc=False):
    if STARTUP_FAULT:
        raise RuntimeError("startup fault: " + STARTUP_FAULT_REASON)

    battery = battery_sensor.read()
    battery_mv = battery.get("battery_mv")
    status = CONT_STATUS_OK
    if not battery.get("valid"):
        status |= CONT_STATUS_BATTERY_INVALID
        battery_mv = int(BATTERY_MV or 0)

    default_calibration = ResistanceCalibration(RESISTANCE_CALIBRATION_POINTS or ())
    calibrated = default_calibration.is_valid() and not force_raw_adc
    unit = RESISTANCE_UNIT_DECI_OHM if calibrated else RESISTANCE_UNIT_ADC_MV
    bank_calibrations = []
    if calibrated:
        for points in RESISTANCE_CALIBRATION_BANKS:
            calibration = ResistanceCalibration(points)
            bank_calibrations.append(
                calibration if calibration.is_valid() else default_calibration
            )

    values = []
    try:
        for cue_index in range(CUE_COUNT):
            reading = channel_sensor.read_channel(cue_index + 1, battery_mv)
            state = channel_classifier.classify(reading)
            if not reading.get("valid"):
                status |= CONT_STATUS_ADC_INVALID
                values.append(RESISTANCE_VALUE_INVALID)
                continue
            if state == STATE_OPEN:
                values.append(RESISTANCE_VALUE_OPEN)
                continue

            value = reading.get("adc_mv")
            if calibrated:
                bank_index = cue_index // RESISTANCE_CALIBRATION_BANK_SIZE
                calibration = (
                    bank_calibrations[bank_index]
                    if bank_index < len(bank_calibrations)
                    else default_calibration
                )
                value = calibration.deci_ohms_from_adc_mv(value)

            if value is None:
                values.append(RESISTANCE_VALUE_INVALID)
            else:
                values.append(max(0, min(0xFFFD, int(value))))
    finally:
        disable_measurement_frontend()

    return unit, status, values


def handle_discover(pkt):
    data = pkt.get("data", b"")
    nonce = int.from_bytes(data[:2], "big") if len(data) >= 2 else None
    span = DISCOVER_BACKOFF_MAX_MS - DISCOVER_BACKOFF_MIN_MS + 1
    time.sleep_ms(DISCOVER_BACKOFF_MIN_MS + ticks_rand(span))
    tx_nfcp(
        nfcp.build_discover_response(
            src_unique=UNIQUE_ID,
            group_id=GROUP_ID,
            unique_id=UNIQUE_ID,
            cue_count=CUE_COUNT,
            device_type=DEVICE_TYPE,
            proto_ver=0x20,
            flags=0x00,
            nonce=nonce,
        ),
        DISCOVER_RESPONSE_REPEATS,
        DISCOVER_RESPONSE_GAP_MS,
    )


def handle_resistance(pkt):
    if STARTUP_FAULT:
        reply_nack_if_unique(pkt, CMD_RESISTANCE_REQ, NACK_INTERNAL_ERROR)
        return

    try:
        data = pkt.get("data", b"")
        if len(data) > 1:
            reply_nack_if_unique(pkt, CMD_RESISTANCE_REQ, NACK_BAD_LENGTH)
            return
        force_raw_adc = bool(data and data[0] == RESISTANCE_REQUEST_RAW_ADC)
        unit, status, values = read_resistance_values(force_raw_adc)
        payload = build_resistance_payload(unit, status, values)
        tx_nfcp(nfcp.build_response(UNIQUE_ID, CMD_RESISTANCE_RSP, payload))
        print("RES unit=%d status=0x%02X values=%s" % (unit, status, str(values)))
    except Exception as e:
        print("RES error:", e)
        reply_nack_if_unique(pkt, CMD_RESISTANCE_REQ, NACK_INTERNAL_ERROR)


def handle_set_resistance_calibration(pkt):
    global RESISTANCE_CALIBRATION_BANKS

    data = pkt.get("data", b"")
    if ARMED:
        reply_nack_if_unique(pkt, CMD_SET_RESISTANCE_CAL, NACK_REJECTED_ARMED)
        return
    if len(data) < 3 or data[0] != RESISTANCE_CALIBRATION_VERSION:
        reply_nack_if_unique(pkt, CMD_SET_RESISTANCE_CAL, NACK_BAD_LENGTH)
        return

    bank_index = int(data[1])
    point_count = int(data[2])
    bank_count = (
        CUE_COUNT + RESISTANCE_CALIBRATION_BANK_SIZE - 1
    ) // RESISTANCE_CALIBRATION_BANK_SIZE
    if (
        bank_index >= bank_count or
        point_count < 2 or
        point_count > RESISTANCE_CALIBRATION_BANK_SIZE
    ):
        reply_nack_if_unique(pkt, CMD_SET_RESISTANCE_CAL, NACK_INVALID_VALUE)
        return
    if len(data) != 3 + point_count * 4:
        reply_nack_if_unique(pkt, CMD_SET_RESISTANCE_CAL, NACK_BAD_LENGTH)
        return

    points = []
    offset = 3
    for _ in range(point_count):
        adc_mv = int.from_bytes(data[offset:offset + 2], "big")
        deci_ohms = int.from_bytes(data[offset + 2:offset + 4], "big")
        points.append((adc_mv, deci_ohms))
        offset += 4

    points.sort(key=lambda point: point[0])
    if not ResistanceCalibration(points).is_valid():
        reply_nack_if_unique(pkt, CMD_SET_RESISTANCE_CAL, NACK_INVALID_VALUE)
        return

    banks = list(RESISTANCE_CALIBRATION_BANKS or ())
    while len(banks) < bank_count:
        banks.append(())
    banks[bank_index] = tuple(points)
    updated_banks = tuple(banks)
    try:
        save_resistance_calibration_banks(updated_banks)
    except Exception as e:
        print("Resistance calibration save failed:", e)
        reply_nack_if_unique(pkt, CMD_SET_RESISTANCE_CAL, NACK_INTERNAL_ERROR)
        return
    RESISTANCE_CALIBRATION_BANKS = updated_banks
    reply_ack_if_unique(pkt, CMD_SET_RESISTANCE_CAL)
    print("RES CAL bank=%d points=%s" % (bank_index + 1, str(points)))


def fire_mask(mask):
    global fire_out_mask

    if STARTUP_FAULT:
        clear_fire_outputs()
        return NACK_INTERNAL_ERROR

    mask &= (1 << CUE_COUNT) - 1
    if not mask:
        return ACK_OK

    if LOG_FIRE_EVENTS:
        print("FIRE mask=0x%06X pulse=%dms" % (mask, FIRE_PULSE_MS))

    try:
        end_ms = time.ticks_add(time.ticks_ms(), FIRE_PULSE_MS)
        for i in range(CUE_COUNT):
            if mask & (1 << i):
                fire_deadlines[i] = end_ms
        fire_out_mask |= mask
        outputs.digitalWriteMask(fire_out_mask)
        return ACK_OK
    except Exception as e:
        clear_fire_outputs()
        print("FIRE error:", e)
        return NACK_INTERNAL_ERROR


def handle_time_sync(pkt):
    global script_time_synced, script_master_offset_ms, script_time_sync_local_ms, script_last_event

    data = pkt.get("data", b"")
    if len(data) < 4:
        return

    master_ms = int.from_bytes(data[0:4], "big") & 0x3FFFFFFF
    local_ms = time.ticks_ms() & 0x3FFFFFFF
    script_master_offset_ms = time.ticks_diff(master_ms, local_ms)
    script_time_sync_local_ms = time.ticks_ms()
    script_time_synced = True
    script_last_event = "time sync"


def script_time_sync_valid():
    return (
        script_time_synced and
        time.ticks_diff(time.ticks_ms(), script_time_sync_local_ms) <= SCRIPT_TIME_SYNC_VALID_MS
    )


def master_ms_to_local_ms(master_ms):
    return time.ticks_add(master_ms & 0x3FFFFFFF, -script_master_offset_ms)


def handle_fire(pkt):
    data = pkt.get("data", b"")
    if not data or len(data) > 8:
        reply_nack_if_unique(pkt, CMD_FIRE, NACK_BAD_LENGTH)
        return
    if STARTUP_FAULT:
        reply_nack_if_unique(pkt, CMD_FIRE, NACK_INTERNAL_ERROR)
        return
    if not ARMED:
        reply_nack_if_unique(pkt, CMD_FIRE, NACK_REJECTED_ARMED)
        return

    mask = int.from_bytes(data, "big") & ((1 << CUE_COUNT) - 1)
    code = fire_mask(mask)
    if code == ACK_OK:
        reply_ack_if_unique(pkt, CMD_FIRE)
    else:
        reply_nack_if_unique(pkt, CMD_FIRE, code)


def clear_script(script_id=None):
    global script_id_loaded, script_entries, script_running, script_start_ms, script_next_index
    global script_last_heartbeat_ms, script_last_event

    script_id_loaded = script_id
    script_entries = []
    script_running = False
    script_start_ms = 0
    script_next_index = 0
    script_last_heartbeat_ms = 0
    script_last_event = "clear"
    print("SCRIPT CLEAR id=%s" % ("--" if script_id is None else "%04X" % script_id))


def handle_script_clear(pkt):
    data = pkt.get("data", b"")
    if len(data) != 2:
        reply_nack_if_unique(pkt, CMD_SCRIPT_CLEAR, NACK_BAD_LENGTH)
        return

    clear_script(int.from_bytes(data, "big") & 0xFFFF)
    reply_ack_if_unique(pkt, CMD_SCRIPT_CLEAR)


def parse_script_load_data(data):
    if len(data) < 4:
        return None, None, None, NACK_BAD_LENGTH

    script_id = int.from_bytes(data[0:2], "big") & 0xFFFF
    chunk_index = data[2]
    entry_count = data[3]
    payload = data[4:]
    if len(payload) != entry_count * SCRIPT_ENTRY_BYTES:
        return None, None, None, NACK_BAD_LENGTH

    entries = []
    pos = 0
    for _ in range(entry_count):
        event_ms = int.from_bytes(payload[pos:pos + 4], "big")
        cue_mask = int.from_bytes(payload[pos + 4:pos + 12], "big") & 0xFFFFFFFFFFFFFFFF
        pos += SCRIPT_ENTRY_BYTES
        if cue_mask:
            entries.append((event_ms, cue_mask))

    return script_id, chunk_index, entries, ACK_OK


def find_script_index_at_or_after(offset_ms):
    index = 0
    while index < len(script_entries):
        if script_entries[index][0] >= offset_ms:
            break
        index += 1
    return index


def handle_script_load(pkt):
    global script_id_loaded, script_entries, script_last_event

    script_id, chunk_index, entries, code = parse_script_load_data(pkt.get("data", b""))
    if code != ACK_OK:
        reply_nack_if_unique(pkt, CMD_SCRIPT_LOAD, code)
        return

    if script_id_loaded != script_id:
        clear_script(script_id)

    if len(script_entries) + len(entries) > SCRIPT_MAX_ENTRIES:
        reply_nack_if_unique(pkt, CMD_SCRIPT_LOAD, NACK_BUSY)
        return

    script_entries.extend(entries)
    script_entries.sort(key=lambda item: item[0])
    script_last_event = "load"
    print("SCRIPT LOAD id=%04X chunk=%d add=%d total=%d" % (script_id, chunk_index, len(entries), len(script_entries)))
    reply_ack_if_unique(pkt, CMD_SCRIPT_LOAD)


def handle_script_start(pkt):
    global script_running, script_start_ms, script_next_index, script_last_heartbeat_ms, script_last_event

    if STARTUP_FAULT:
        script_last_event = "start fault"
        reply_nack_if_unique(pkt, CMD_SCRIPT_START, NACK_INTERNAL_ERROR)
        return

    if not ARMED:
        script_last_event = "start disarmed"
        reply_nack_if_unique(pkt, CMD_SCRIPT_START, NACK_REJECTED_ARMED)
        return

    data = pkt.get("data", b"")
    if len(data) < 4:
        reply_nack_if_unique(pkt, CMD_SCRIPT_START, NACK_BAD_LENGTH)
        return

    script_id = int.from_bytes(data[0:2], "big") & 0xFFFF
    start_delay_ms = int.from_bytes(data[2:4], "big")
    offset_ms = int.from_bytes(data[4:8], "big") if len(data) >= 8 else 0
    start_master_ms = int.from_bytes(data[8:12], "big") & 0x3FFFFFFF if len(data) >= 12 else None

    if script_id_loaded != script_id or not script_entries:
        script_last_event = "start invalid"
        reply_nack_if_unique(pkt, CMD_SCRIPT_START, NACK_INVALID_VALUE)
        return

    script_next_index = find_script_index_at_or_after(offset_ms)

    if start_master_ms is not None and script_time_sync_valid():
        script_start_ms = time.ticks_add(master_ms_to_local_ms(start_master_ms), -offset_ms)
    else:
        script_start_ms = time.ticks_add(time.ticks_ms(), start_delay_ms - offset_ms)
    script_running = True
    script_last_heartbeat_ms = time.ticks_ms()
    script_last_event = "start"
    print("SCRIPT START id=%04X entries=%d offset=%d next=%d" % (script_id, len(script_entries), offset_ms, script_next_index))
    reply_ack_if_unique(pkt, CMD_SCRIPT_START)


def handle_script_stop(pkt):
    global script_running, script_last_heartbeat_ms, script_last_event

    script_running = False
    script_last_heartbeat_ms = 0
    script_last_event = "stop"
    clear_fire_outputs()
    print("SCRIPT STOP")
    reply_ack_if_unique(pkt, CMD_SCRIPT_STOP)


def handle_script_heartbeat(pkt):
    global script_last_heartbeat_ms, script_last_event

    data = pkt.get("data", b"")
    if len(data) < 2:
        return

    script_id = int.from_bytes(data[0:2], "big") & 0xFFFF
    if script_running and script_id_loaded == script_id:
        script_last_heartbeat_ms = time.ticks_ms()
        script_last_event = "heartbeat"


def fire_script_event(event_ms):
    fired = 0
    for entry_ms, cue_mask in script_entries:
        if entry_ms == event_ms:
            fire_mask(cue_mask)
            fired += 1
    return fired


def handle_script_step(pkt):
    global script_last_event

    if STARTUP_FAULT:
        script_last_event = "step fault"
        return

    if not ARMED:
        script_last_event = "step disarmed"
        return

    data = pkt.get("data", b"")
    if len(data) != 6:
        return

    script_id = int.from_bytes(data[0:2], "big") & 0xFFFF
    event_ms = int.from_bytes(data[2:6], "big")
    if script_id_loaded != script_id:
        script_last_event = "step invalid"
        return

    if not fire_script_event(event_ms):
        script_last_event = "step missing"
        return

    script_last_event = "step"


def run_script_scheduler():
    global script_running, script_next_index, script_last_event

    if not script_running:
        return
    if STARTUP_FAULT:
        script_running = False
        clear_fire_outputs()
        script_last_event = "stopped fault"
        return
    if not ARMED:
        script_running = False
        clear_fire_outputs()
        script_last_event = "stopped disarmed"
        return
    if time.ticks_diff(time.ticks_ms(), script_last_heartbeat_ms) > SCRIPT_HEARTBEAT_TIMEOUT_MS:
        script_running = False
        clear_fire_outputs()
        script_last_event = "hb timeout"
        print("SCRIPT stopped - heartbeat timeout")
        return

    elapsed = time.ticks_diff(time.ticks_ms(), script_start_ms)
    while script_next_index < len(script_entries):
        event_ms, cue_mask = script_entries[script_next_index]
        if event_ms > elapsed:
            break
        script_last_event = "fire"
        print("SCRIPT FIRE mask=0x%06X event=%d elapsed=%d" % (cue_mask, event_ms, elapsed))
        fire_mask(cue_mask)
        script_next_index += 1

    if script_next_index >= len(script_entries):
        script_running = False
        script_last_event = "complete"


def handle(pkt):
    global ARMED, GROUP_ID, UNIQUE_ID, CONFIG_UNIQUE_ID, NETWORK_ID, RADIO_CHANNEL, script_running

    cmd = pkt.get("cmd")
    data = pkt.get("data", b"")

    if cmd == CMD_DISCOVER:
        handle_discover(pkt)
        return

    if cmd == CMD_PING:
        if pkt.get("frame_type") == SOF_UNIQUE and pkt.get("target") == UNIQUE_ID:
            tx_nfcp(nfcp.build_response(UNIQUE_ID, CMD_PING, data or bytes([PING_REPLY_BYTE])))
        return

    if cmd == CMD_INFO_REQ:
        if pkt.get("frame_type") == SOF_UNIQUE and pkt.get("target") == UNIQUE_ID:
            tx_nfcp(nfcp.build_response(UNIQUE_ID, CMD_INFO_RSP, build_info_payload()))
        return

    if cmd == CMD_KEEPALIVE:
        return

    if cmd == CMD_ARM:
        if STARTUP_FAULT:
            ARMED = False
            set_armed_outputs(False)
            reply_nack_if_unique(pkt, CMD_ARM, NACK_INTERNAL_ERROR)
            print("ARM rejected - startup fault:", STARTUP_FAULT_REASON)
            return
        ARMED = True
        set_armed_outputs(True)
        reply_ack_if_unique(pkt, CMD_ARM)
        print("ARMED")
        return

    if cmd == CMD_DISARM:
        ARMED = False
        script_running = False
        clear_fire_outputs()
        set_armed_outputs(False)
        reply_ack_if_unique(pkt, CMD_DISARM)
        print("DISARMED")
        return

    if cmd == CMD_FIRE:
        handle_fire(pkt)
        return

    if cmd == CMD_SCRIPT_CLEAR:
        handle_script_clear(pkt)
        return

    if cmd == CMD_SCRIPT_LOAD:
        handle_script_load(pkt)
        return

    if cmd == CMD_SCRIPT_START:
        handle_script_start(pkt)
        return

    if cmd == CMD_SCRIPT_STOP:
        handle_script_stop(pkt)
        return

    if cmd == CMD_SCRIPT_HEARTBEAT:
        handle_script_heartbeat(pkt)
        return

    if cmd == CMD_TIME_SYNC:
        handle_time_sync(pkt)
        return

    if cmd == CMD_SCRIPT_STEP:
        handle_script_step(pkt)
        return

    if cmd == CMD_OTA_UPDATE_BEGIN:
        module_ota.begin(pkt, UNIQUE_ID, ARMED, reply_ack_if_unique, reply_nack_if_unique)
        return

    if cmd == CMD_OTA_UPDATE_WRITE:
        module_ota.write(pkt, UNIQUE_ID, reply_ack_if_unique, reply_nack_if_unique)
        return

    if cmd == CMD_OTA_UPDATE_END:
        module_ota.end(pkt, UNIQUE_ID, reply_ack_if_unique, reply_nack_if_unique)
        return

    if cmd == CMD_OTA_UPDATE_ABORT:
        module_ota.abort(pkt, UNIQUE_ID, reply_ack_if_unique, reply_nack_if_unique)
        return

    if cmd == CMD_OTA_UPDATE_REBOOT:
        if module_ota.reboot(pkt, UNIQUE_ID, ARMED, reply_ack_if_unique, reply_nack_if_unique):
            time.sleep_ms(250)
            machine.reset()
        return

    if cmd == CMD_RESISTANCE_REQ:
        if pkt.get("frame_type") == SOF_UNIQUE and pkt.get("target") == UNIQUE_ID:
            handle_resistance(pkt)
        return

    if cmd == CMD_SET_RESISTANCE_CAL:
        if pkt.get("frame_type") == SOF_UNIQUE and pkt.get("target") == UNIQUE_ID:
            handle_set_resistance_calibration(pkt)
        return

    if cmd == CMD_SET_GROUP:
        if ARMED:
            reply_nack_if_unique(pkt, CMD_SET_GROUP, NACK_REJECTED_ARMED)
        elif len(data) != 2:
            reply_nack_if_unique(pkt, CMD_SET_GROUP, NACK_BAD_LENGTH)
        else:
            previous = runtime_config_values()
            GROUP_ID = int.from_bytes(data, "big") & 0xFFFF
            if persist_runtime_change(previous):
                reply_ack_if_unique(pkt, CMD_SET_GROUP)
            else:
                reply_nack_if_unique(pkt, CMD_SET_GROUP, NACK_INTERNAL_ERROR)
        return

    if cmd == CMD_SET_NETWORK:
        if ARMED:
            reply_nack_if_unique(pkt, CMD_SET_NETWORK, NACK_REJECTED_ARMED)
        elif len(data) != 2:
            reply_nack_if_unique(pkt, CMD_SET_NETWORK, NACK_BAD_LENGTH)
        else:
            previous = runtime_config_values()
            NETWORK_ID = int.from_bytes(data, "big") & 0xFFFF
            if persist_runtime_change(previous):
                reply_ack_if_unique(pkt, CMD_SET_NETWORK)
            else:
                reply_nack_if_unique(pkt, CMD_SET_NETWORK, NACK_INTERNAL_ERROR)
        return

    if cmd == CMD_SET_RADIO_CHANNEL:
        if ARMED:
            reply_nack_if_unique(pkt, CMD_SET_RADIO_CHANNEL, NACK_REJECTED_ARMED)
        elif len(data) != 1:
            reply_nack_if_unique(pkt, CMD_SET_RADIO_CHANNEL, NACK_BAD_LENGTH)
        elif not valid_radio_channel(data[0]):
            reply_nack_if_unique(pkt, CMD_SET_RADIO_CHANNEL, NACK_INVALID_VALUE)
        else:
            previous = runtime_config_values()
            RADIO_CHANNEL = data[0]
            if persist_runtime_change(previous):
                reply_ack_if_unique(pkt, CMD_SET_RADIO_CHANNEL)
                time.sleep_ms(80)
                nrf_radio.init(
                    channel=RADIO_CHANNEL,
                    tx_power=RADIO_TX_POWER,
                    address=RADIO_ADDRESS,
                )
            else:
                reply_nack_if_unique(pkt, CMD_SET_RADIO_CHANNEL, NACK_INTERNAL_ERROR)
        return

    if cmd == CMD_SET_UNIQUE:
        old_unique = UNIQUE_ID
        if ARMED:
            if pkt.get("frame_type") == SOF_UNIQUE:
                tx_nfcp(nfcp.build_nack(old_unique, CMD_SET_UNIQUE, NACK_REJECTED_ARMED))
        elif len(data) != 4:
            if pkt.get("frame_type") == SOF_UNIQUE:
                tx_nfcp(nfcp.build_nack(old_unique, CMD_SET_UNIQUE, NACK_BAD_LENGTH))
        else:
            previous = runtime_config_values()
            CONFIG_UNIQUE_ID = int.from_bytes(data, "big") & 0xFFFFFFFF
            UNIQUE_ID = CONFIG_UNIQUE_ID or get_default_unique_id()
            saved = persist_runtime_change(previous)
            if saved and pkt.get("frame_type") == SOF_UNIQUE:
                tx_nfcp(nfcp.build_ack(old_unique, CMD_SET_UNIQUE, ACK_OK))
            elif not saved and pkt.get("frame_type") == SOF_UNIQUE:
                tx_nfcp(nfcp.build_nack(old_unique, CMD_SET_UNIQUE, NACK_INTERNAL_ERROR))
            if saved:
                print("NEW UNIQUE_ID = 0x%08X" % UNIQUE_ID)
        return


def handle_radio_payload(payload):
    if len(payload) < 3:
        return

    network_id = int.from_bytes(payload[:2], "big")
    if network_id != NETWORK_ID:
        return

    radio_seq = None
    nfcp_offset = 2

    if len(payload) >= 4 and payload[2] != 0xFF and payload[3] == 0xFF:
        radio_seq = payload[2]
        nfcp_offset = 3

    for b in payload[nfcp_offset:]:
        pkt = parser.feed(b)
        if pkt and address_matches(pkt):
            if remember_rx_packet(radio_seq, pkt):
                return
            record_master_rx()
            handle(pkt)


def init_radio():
    try:
        nrf_radio.init(
            channel=RADIO_CHANNEL,
            tx_power=RADIO_TX_POWER,
            address=RADIO_ADDRESS,
        )
        return True
    except Exception as e:
        print("Radio init failed:", e)
        return False


def main():
    global ARMED, script_running

    init_lcd_ui()
    set_armed_outputs(False)
    run_startup_self_tests()
    radio_ready = init_radio()
    print(
        "Nightfire nRF52840 field module board=%s uid=0x%08X group=0x%04X net=0x%04X channel=%d" %
        (BOARD_PINOUT, UNIQUE_ID, GROUP_ID, NETWORK_ID, RADIO_CHANNEL)
    )
    if STARTUP_FAULT:
        print("Startup status: FAULT %s" % STARTUP_FAULT_REASON)
    else:
        print("Startup status: OK")
    print(
        "74HC595 DATA=P%d CLOCK=P%d LATCH=P%d OE=P%d SRCLR=P%d Q7S=%s cues=%d" %
        (
            HC595_DATA_PIN,
            HC595_CLOCK_PIN,
            HC595_LATCH_PIN,
            HC595_OE_PIN,
            HC595_SRCLR_PIN,
            "--" if HC595_Q7S_PIN is None else "P%d" % HC595_Q7S_PIN,
            CUE_COUNT,
        )
    )
    if EXTERNAL_VCC_CONTROL_ENABLED:
        print(
            "External VCC control P%d value=%d settle=%dms" %
            (EXTERNAL_VCC_CONTROL_PIN, EXTERNAL_VCC_ENABLE_VALUE, EXTERNAL_VCC_SETTLE_MS)
        )
    if ARM_CONTROL_PIN is not None:
        print("Arm control P%d active=%d" % (ARM_CONTROL_PIN, ARM_CONTROL_ACTIVE_VALUE))
    if MEASUREMENT_METHOD == "pretest_selected_cue_short_check":
        print(
            "Pre-test measurement PRE_TEST=P%d SHORT_CHECK=P%d settle=%dms output_settle=%dus" %
            (PRE_TEST_PIN, SHORT_CHECK_PIN, PRETEST_SETTLE_MS, PRETEST_OUTPUT_SETTLE_US)
        )
    else:
        print(
            "74HC4051 S0=P%d S1=P%d S2=P%d EN=P%d/%d/%d SENSE=P%d" %
            (MUX_S0_PIN, MUX_S1_PIN, MUX_S2_PIN, MUX1_EN_PIN, MUX2_EN_PIN, MUX3_EN_PIN, SENSE_SELECTED_ADC_PIN)
        )
    print(
        "I2C LCD reserved SDA=P%d SCL=P%d available=%d" %
        (LCD_SDA_PIN, LCD_SCL_PIN, 1 if lcd_i2c else 0)
    )
    print(
        "Controls SAFE=P%d ARM_LED=P%d UP=P%d DOWN=P%d OK_FWD=%s BACK=%s BATT=P%d" %
        (
            SAFE_LED_PIN,
            ARM_LED_PIN,
            UP_BUTTON_PIN,
            DOWN_BUTTON_PIN,
            "--" if OK_FWD_BUTTON_PIN is None else "P%d" % OK_FWD_BUTTON_PIN,
            "--" if BACK_BUTTON_PIN is None else "P%d" % BACK_BUTTON_PIN,
            BATT_CHECK_PIN,
        )
    )
    print(
        "Radio node uid=0x%08X group=0x%04X net=0x%04X" %
        (UNIQUE_ID, GROUP_ID, NETWORK_ID)
    )

    while True:
        try:
            module_ota.poll_timeout()
            poll_fire_outputs()
            run_script_scheduler()
            check_master_link_failsafe()

            if not radio_ready:
                time.sleep_ms(250)
                radio_ready = init_radio()
                continue

            payload = nrf_radio.recv(RADIO_RX_TIMEOUT_MS)
            if payload:
                handle_radio_payload(payload)
            service_lcd_ui()
        except Exception as e:
            print("Field main loop error:", e)
            radio_ready = False
            ARMED = False
            script_running = False
            clear_fire_outputs()
            set_armed_outputs(False)
            time.sleep_ms(100)


main()
