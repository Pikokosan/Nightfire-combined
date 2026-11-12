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
from module_lcd import DebouncedButton, LcdMenu
from module_link import ModuleLink, encode_text_field
from mesh import MESH_DEFAULT_MAX_HOPS, MESH_RADIO_ADDRESS
from module_script import ScriptEngine
from nfcp import *
from machine import I2C, Pin
from shift_register74hc595 import ShiftRegister74HC59524

try:
    import nrf_node_config as node_config
except Exception:
    node_config = None


RUNTIME_CONFIG_PATH = "field_runtime.bin"
RUNTIME_CONFIG_MAGIC = b"NFRT\x01"
RESISTANCE_CALIBRATION_PATH = "field_resistance_cal.bin"
RESISTANCE_CALIBRATION_MAGIC = b"NFRC\x01"
RESISTANCE_CALIBRATION_BANK_SIZE = 8
FIRE_PULSE_PATH = "field_fire_pulse.bin"
FIRE_PULSE_MAGIC = b"NFFP\x01"


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
APP_FIRMWARE_VERSION = "0.3.1"

DEFAULT_NETWORK_ID = 0x04D2
DEFAULT_GROUP_ID = 0x0001
DEFAULT_CUE_COUNT = 24
DEFAULT_DEVICE_TYPE = 0x20

RADIO_CHANNEL = RUNTIME_CONFIG[3] if RUNTIME_CONFIG else getattr(node_config, "RADIO_CHANNEL", 90)
# Mesh radio (docs/mesh_radio_design.md). Pre-mesh firmware used 0x67676767.
RADIO_ADDRESS = getattr(node_config, "MESH_RADIO_ADDRESS", MESH_RADIO_ADDRESS)
MESH_MAX_HOPS = getattr(node_config, "MESH_MAX_HOPS", MESH_DEFAULT_MAX_HOPS)
MESH_RELAY_ENABLED = getattr(node_config, "MESH_RELAY_ENABLED", True)
RADIO_TX_POWER = 8
RADIO_RX_TIMEOUT_MS = getattr(node_config, "RADIO_RX_TIMEOUT_MS", 50)
RADIO_CCA_ENABLED = getattr(node_config, "RADIO_CCA_ENABLED", True)
RADIO_CCA_MS = getattr(node_config, "RADIO_CCA_MS", 2)
RADIO_CCA_BUSY_DBM = getattr(node_config, "RADIO_CCA_BUSY_DBM", -85)
RADIO_CCA_RETRIES = getattr(node_config, "RADIO_CCA_RETRIES", 4)
RADIO_CCA_BACKOFF_MIN_MS = getattr(node_config, "RADIO_CCA_BACKOFF_MIN_MS", 2)
RADIO_CCA_BACKOFF_MAX_MS = getattr(node_config, "RADIO_CCA_BACKOFF_MAX_MS", 10)

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
ARM_VOLTAGE_CHECK_ENABLED = bool(getattr(node_config, "ARM_VOLTAGE_CHECK_ENABLED", True))
ARM_VOLTAGE_CHECK_SETTLE_MS = int(getattr(node_config, "ARM_VOLTAGE_CHECK_SETTLE_MS", 50))
ARM_VOLTAGE_CHECK_MIN_MV = int(getattr(node_config, "ARM_VOLTAGE_CHECK_MIN_MV", 500))
ARM_VOLTAGE_CHECK_SAMPLES = int(getattr(node_config, "ARM_VOLTAGE_CHECK_SAMPLES", 16))
SAFE_LED_PIN = getattr(node_config, "SAFE_LED_PIN", 10)
ARM_LED_PIN = getattr(node_config, "ARM_LED_PIN", 11)
UP_BUTTON_PIN = getattr(node_config, "UP_BUTTON_PIN", 33)      # P1.01 bottom pad
DOWN_BUTTON_PIN = getattr(node_config, "DOWN_BUTTON_PIN", 34)  # P1.02 bottom pad
OK_FWD_BUTTON_PIN = getattr(node_config, "OK_FWD_BUTTON_PIN", getattr(node_config, "OK_BUTTON_PIN", None))
BACK_BUTTON_PIN = getattr(node_config, "BACK_BUTTON_PIN", None)
BUTTON_ACTIVE_VALUE = 1 if getattr(node_config, "BUTTON_ACTIVE_VALUE", 0) else 0
BUTTON_INTERNAL_PULL_UP = bool(getattr(node_config, "BUTTON_INTERNAL_PULL_UP", True))
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


def valid_fire_pulse_ms(value):
    return type(value) is int and FIRE_PULSE_MIN_MS <= value <= FIRE_PULSE_MAX_MS


def load_fire_pulse_ms(default):
    """The cue hold time saved by CMD_FIRE_PULSE, else the config value."""
    data = load_crc_payload(FIRE_PULSE_PATH, FIRE_PULSE_MAGIC)
    if data is not None and len(data) == len(FIRE_PULSE_MAGIC) + 2:
        value = int.from_bytes(data[-2:], "big")
        if valid_fire_pulse_ms(value):
            return value
    return default if valid_fire_pulse_ms(default) else 5


def save_fire_pulse_ms(value):
    save_crc_payload(FIRE_PULSE_PATH, FIRE_PULSE_MAGIC + value.to_bytes(2, "big"))


# How long a fired cue output is held on. The app can change it over the
# radio (CMD_FIRE_PULSE); the saved value replaces FIRE_PULSE_MS from config.
FIRE_PULSE_MIN_MS = 1
FIRE_PULSE_MAX_MS = 2000
FIRE_PULSE_MS = load_fire_pulse_ms(int(getattr(node_config, "FIRE_PULSE_MS", 5)))
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
RESISTANCE_SHORT_MAX_DECI_OHM = int(
    getattr(node_config, "RESISTANCE_SHORT_MAX_DECI_OHM", 0)
)
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
arm_check_error = ""
arm_check_mv = None
STARTUP_FAULT = False
STARTUP_FAULT_REASON = ""
STARTUP_595_CHAIN_RESULT = None

fire_out_mask = 0
fire_deadlines = [0] * CUE_COUNT
last_fire_mask = 0
last_fire_seq = 0
last_fire_ms = 0
last_cont_status = CONT_STATUS_OK
last_cont_open_mask = None
last_cont_ms = 0



class FieldNode:
    """Firmware state and hooks used by the shared link, script and LCD code."""

    firmware_version = APP_FIRMWARE_VERSION
    can_scan = True

    def ids(self):
        return NETWORK_ID, GROUP_ID, UNIQUE_ID, RADIO_CHANNEL

    def armed(self):
        return ARMED

    def fire(self, mask):
        return fire_mask(mask)

    def fire_blocked(self):
        if STARTUP_FAULT:
            return NACK_INTERNAL_ERROR, "fault"
        if not ARMED:
            return NACK_REJECTED_ARMED, "disarmed"
        return None

    def stop_outputs(self):
        clear_fire_outputs()

    def info_payload(self):
        fault_text = " fault=%s" % STARTUP_FAULT_REASON if STARTUP_FAULT else ""
        return (
            encode_text_field(APP_FIRMWARE_NAME) +
            encode_text_field(APP_FIRMWARE_VERSION) +
            encode_text_field("nrf-radio-native") +
            encode_text_field(script.status_text(fault_text)) +
            encode_text_field("0x%08X" % (get_default_unique_id() & 0xFFFFFFFF)) +
            encode_text_field("0x%08X" % (CONFIG_UNIQUE_ID & 0xFFFFFFFF)) +
            encode_text_field(str(read_battery_mv())) +
            encode_text_field(BATTERY_PACK_TYPE)
        )

    def apply_setting(self, name, value):
        global GROUP_ID, NETWORK_ID, RADIO_CHANNEL, CONFIG_UNIQUE_ID, UNIQUE_ID

        previous = runtime_config_values()
        if name == "group":
            GROUP_ID = value
        elif name == "network":
            NETWORK_ID = value
        elif name == "channel":
            RADIO_CHANNEL = value
        elif name == "unique":
            CONFIG_UNIQUE_ID = value
            UNIQUE_ID = CONFIG_UNIQUE_ID or get_default_unique_id()
        return persist_runtime_change(previous)

    # LCD
    def status_text(self):
        if STARTUP_FAULT:
            return "FAULT"
        if ARMED:
            return "ARMED"
        return "SAFE"

    def battery_mv(self):
        try:
            return read_battery_mv()
        except Exception:
            return 0

    def cue_count(self):
        return CUE_COUNT

    def continuity(self):
        status = "OK" if last_cont_status == CONT_STATUS_OK else "WARN%02X" % last_cont_status
        return last_cont_open_mask, status

    def scan_continuity(self):
        try:
            unit, status, values = read_resistance_values()
            open_mask = 0
            for index, value in enumerate(values):
                if value in (RESISTANCE_VALUE_OPEN, RESISTANCE_VALUE_INVALID):
                    open_mask |= 1 << index
                elif (
                    unit == RESISTANCE_UNIT_DECI_OHM and
                    int(value) <= RESISTANCE_SHORT_MAX_DECI_OHM
                ):
                    open_mask |= 1 << index
            set_last_continuity(status, open_mask)
            print("LCD RES status=0x%02X open_mask=0x%06X" % (status, open_mask))
        except Exception as e:
            print("LCD continuity scan failed:", e)

    def alert_lines(self):
        if arm_check_error:
            measured = "--" if arm_check_mv is None else str(int(arm_check_mv))
            return "ARM CHECK FAILED", "%smV need %d" % (measured, ARM_VOLTAGE_CHECK_MIN_MV)
        return None


node = FieldNode()
link = ModuleLink(
    node,
    RADIO_ADDRESS,
    RADIO_TX_POWER,
    cca_enabled=RADIO_CCA_ENABLED,
    cca_ms=RADIO_CCA_MS,
    cca_busy_dbm=RADIO_CCA_BUSY_DBM,
    cca_retries=RADIO_CCA_RETRIES,
    cca_backoff_min_ms=RADIO_CCA_BACKOFF_MIN_MS,
    cca_backoff_max_ms=RADIO_CCA_BACKOFF_MAX_MS,
    max_hops=MESH_MAX_HOPS,
    relay_enabled=MESH_RELAY_ENABLED,
)
script = ScriptEngine(
    node,
    link,
    max_entries=SCRIPT_MAX_ENTRIES,
    heartbeat_timeout_ms=SCRIPT_HEARTBEAT_TIMEOUT_MS,
    sync_valid_ms=SCRIPT_TIME_SYNC_VALID_MS,
)
lcd = LcdMenu(
    node,
    link,
    script,
    cols=LCD_COLS,
    rows=LCD_ROWS,
    refresh_ms=LCD_REFRESH_MS,
    page_size=LCD_CONTINUITY_PAGE_SIZE,
    link_timeout_ms=MASTER_LINK_TIMEOUT_MS,
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


def release_nfc_pins(pins):
    """P0.09 and P0.10 are the NFC antenna pins: they work as GPIO only once
    UICR.NFCPINS bit 0 is cleared (the Pro Micro uses them for ARM_CONTROL
    and BACK). Clears it once, then restarts so the setting takes effect.
    Writing 0 to a UICR bit needs no erase; only a full chip erase sets it
    back."""
    if 9 not in pins and 10 not in pins:
        return
    try:
        from machine import mem32
    except ImportError:
        return
    uicr_nfcpins = 0x1000120C
    nvmc_ready = 0x4001E400
    nvmc_config = 0x4001E504
    if not mem32[uicr_nfcpins] & 1:
        return
    print("NFC pins: switching P0.09/P0.10 to GPIO, then restarting")
    try:
        mem32[nvmc_config] = 1          # write enable
        while not mem32[nvmc_ready]:
            pass
        mem32[uicr_nfcpins] = 0xFFFFFFFE
        while not mem32[nvmc_ready]:
            pass
    finally:
        mem32[nvmc_config] = 0          # read only
    if mem32[uicr_nfcpins] & 1:
        # Never restart in a loop: report it and carry on.
        record_startup_fault("NFC pins still enabled; P0.09/P0.10 unusable")
        return
    time.sleep_ms(50)
    machine.reset()


release_nfc_pins((ARM_CONTROL_PIN, BACK_BUTTON_PIN, OK_FWD_BUTTON_PIN, UP_BUTTON_PIN,
                  DOWN_BUTTON_PIN, SAFE_LED_PIN, ARM_LED_PIN, PRE_TEST_PIN))

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
button_pull = Pin.PULL_UP if BUTTON_INTERNAL_PULL_UP else None
up_button = make_pin(UP_BUTTON_PIN, Pin.IN, button_pull, name="up button")
down_button = make_pin(DOWN_BUTTON_PIN, Pin.IN, button_pull, name="down button")
ok_fwd_button = make_pin(OK_FWD_BUTTON_PIN, Pin.IN, button_pull, name="ok/fwd button") if OK_FWD_BUTTON_PIN is not None else None
back_button = make_pin(BACK_BUTTON_PIN, Pin.IN, button_pull, name="back button") if BACK_BUTTON_PIN is not None else None

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


def set_last_continuity(status, open_mask):
    global last_cont_status, last_cont_open_mask, last_cont_ms

    last_cont_status = int(status or CONT_STATUS_OK)
    last_cont_open_mask = int(open_mask or 0) & ((1 << CUE_COUNT) - 1)
    last_cont_ms = time.ticks_ms()
    lcd.refresh()


def read_battery_mv():
    reading = battery_sensor.read()
    if reading.get("valid"):
        return int(reading.get("battery_mv") or 0)
    if BATTERY_MV:
        return int(BATTERY_MV or 0)
    return int(reading.get("battery_mv") or 0)


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
    if armed and STARTUP_FAULT:
        armed = False
    lcd.refresh()
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


def read_short_check_mv():
    if sense_adc is None:
        return None

    sample_count = max(1, ARM_VOLTAGE_CHECK_SAMPLES)
    total = 0
    try:
        for index in range(sample_count):
            total += int(sense_adc.read_u16())
            if index + 1 < sample_count and ADC_SAMPLE_DELAY_US:
                time.sleep_us(ADC_SAMPLE_DELAY_US)
    except Exception as e:
        print("Arm voltage ADC failed:", e)
        return None

    average = total // sample_count
    return (average * ADC_FULL_SCALE_MV + 32767) // 65535


def check_arm_voltage():
    global arm_check_mv

    arm_check_mv = None
    if not ARM_VOLTAGE_CHECK_ENABLED:
        return True
    if arm_control is None or sense_adc is None:
        return False

    clear_fire_outputs()
    outputs.set_enabled(False)
    outputs.clear()
    pre_test.value(0)
    safe_led.value(1)
    arm_led.value(0)
    arm_control.value(ARM_CONTROL_ACTIVE_VALUE)
    if ARM_VOLTAGE_CHECK_SETTLE_MS:
        time.sleep_ms(ARM_VOLTAGE_CHECK_SETTLE_MS)

    arm_check_mv = read_short_check_mv()
    return arm_check_mv is not None and arm_check_mv >= ARM_VOLTAGE_CHECK_MIN_MV


def clear_fire_outputs():
    global fire_out_mask

    fire_out_mask = 0
    outputs.clear()


def disable_measurement_frontend():
    if scanner is not None:
        scanner.disable_all()


def begin_resistance_measurement():
    if fire_out_mask or script.running:
        raise RuntimeError("resistance scan blocked while firing")

    restore_armed_outputs = bool(ARMED)
    if restore_armed_outputs:
        set_armed_outputs(False)
    return restore_armed_outputs


def end_resistance_measurement(restore_armed_outputs):
    disable_measurement_frontend()
    if restore_armed_outputs and ARMED:
        set_armed_outputs(True)


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
    global ARMED

    record_startup_fault(reason)
    ARMED = False
    script.halt()
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


def fire_wait_ms(default_ms):
    """Cap a blocking radio wait so an active cue is switched off on time
    instead of being held for the whole receive timeout."""
    if not fire_out_mask:
        return default_ms
    now = time.ticks_ms()
    wait = default_ms
    for i in range(CUE_COUNT):
        if fire_out_mask & (1 << i):
            wait = min(wait, time.ticks_diff(fire_deadlines[i], now))
    return wait if wait > 1 else 1


def handle_fire_pulse(pkt):
    """CMD_FIRE_PULSE: empty reads the cue hold time; two bytes (ms, big
    endian) set and save it. Both reply CMD_FIRE_PULSE_RSP with the value."""
    global FIRE_PULSE_MS

    data = pkt.get("data", b"")
    if data:
        if len(data) != 2:
            link.nack(pkt, CMD_FIRE_PULSE, NACK_BAD_LENGTH)
            return
        if ARMED:
            link.nack(pkt, CMD_FIRE_PULSE, NACK_REJECTED_ARMED)
            return
        if script.running or fire_out_mask:
            link.nack(pkt, CMD_FIRE_PULSE, NACK_BUSY)
            return
        value = int.from_bytes(data, "big")
        if not valid_fire_pulse_ms(value):
            link.nack(pkt, CMD_FIRE_PULSE, NACK_INVALID_VALUE)
            return
        if value != FIRE_PULSE_MS:
            try:
                save_fire_pulse_ms(value)
            except Exception as e:
                print("Fire pulse save failed:", e)
                link.nack(pkt, CMD_FIRE_PULSE, NACK_INTERNAL_ERROR)
                return
            FIRE_PULSE_MS = value
            print("FIRE PULSE %dms" % value)
    link.respond(CMD_FIRE_PULSE_RSP, FIRE_PULSE_MS.to_bytes(2, "big"))


def build_output_status_payload():
    flags = 0
    if ARMED:
        flags |= 0x01
    if script.running:
        flags |= 0x02
    if STARTUP_FAULT:
        flags |= 0x04
    try:
        if outputs.is_enabled():
            flags |= 0x08
    except Exception:
        pass
    age_ms = 0xFFFF
    if last_fire_seq:
        age_ms = max(0, min(0xFFFE, time.ticks_diff(time.ticks_ms(), last_fire_ms)))
    loaded = 0xFFFF if script.id_loaded is None else script.id_loaded & 0xFFFF
    return bytes((1, flags, CUE_COUNT & 0xFF)) + (
        (fire_out_mask & 0xFFFFFFFFFFFFFFFF).to_bytes(8, "big") +
        (last_fire_mask & 0xFFFFFFFFFFFFFFFF).to_bytes(8, "big") +
        (last_fire_seq & 0xFFFFFFFF).to_bytes(4, "big") +
        (age_ms & 0xFFFF).to_bytes(2, "big") +
        loaded.to_bytes(2, "big")
    )


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
    restore_armed_outputs = begin_resistance_measurement()
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
        end_resistance_measurement(restore_armed_outputs)

    return unit, status, values


def handle_resistance(pkt):
    if STARTUP_FAULT:
        link.nack(pkt, CMD_RESISTANCE_REQ, NACK_INTERNAL_ERROR)
        return

    try:
        data = pkt.get("data", b"")
        if len(data) > 1:
            link.nack(pkt, CMD_RESISTANCE_REQ, NACK_BAD_LENGTH)
            return
        force_raw_adc = bool(data and data[0] == RESISTANCE_REQUEST_RAW_ADC)
        unit, status, values = read_resistance_values(force_raw_adc)
        payload = build_resistance_payload(unit, status, values)
        link.respond(CMD_RESISTANCE_RSP, payload)
        print("RES unit=%d status=0x%02X values=%s" % (unit, status, str(values)))
    except Exception as e:
        print("RES error:", e)
        link.nack(pkt, CMD_RESISTANCE_REQ, NACK_INTERNAL_ERROR)


def handle_set_resistance_calibration(pkt):
    global RESISTANCE_CALIBRATION_BANKS

    data = pkt.get("data", b"")
    if ARMED:
        link.nack(pkt, CMD_SET_RESISTANCE_CAL, NACK_REJECTED_ARMED)
        return
    if len(data) < 3 or data[0] != RESISTANCE_CALIBRATION_VERSION:
        link.nack(pkt, CMD_SET_RESISTANCE_CAL, NACK_BAD_LENGTH)
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
        link.nack(pkt, CMD_SET_RESISTANCE_CAL, NACK_INVALID_VALUE)
        return
    if len(data) != 3 + point_count * 4:
        link.nack(pkt, CMD_SET_RESISTANCE_CAL, NACK_BAD_LENGTH)
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
        link.nack(pkt, CMD_SET_RESISTANCE_CAL, NACK_INVALID_VALUE)
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
        link.nack(pkt, CMD_SET_RESISTANCE_CAL, NACK_INTERNAL_ERROR)
        return
    RESISTANCE_CALIBRATION_BANKS = updated_banks
    link.ack(pkt, CMD_SET_RESISTANCE_CAL)
    print("RES CAL bank=%d points=%s" % (bank_index + 1, str(points)))


def fire_mask(mask):
    global fire_out_mask, last_fire_mask, last_fire_seq, last_fire_ms

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
        last_fire_mask = mask
        last_fire_seq = (last_fire_seq + 1) & 0xFFFFFFFF
        last_fire_ms = time.ticks_ms()
        return ACK_OK
    except Exception as e:
        clear_fire_outputs()
        print("FIRE error:", e)
        return NACK_INTERNAL_ERROR


def handle_fire(pkt):
    data = pkt.get("data", b"")
    if not data or len(data) > 8:
        link.nack(pkt, CMD_FIRE, NACK_BAD_LENGTH)
        return
    if STARTUP_FAULT:
        link.nack(pkt, CMD_FIRE, NACK_INTERNAL_ERROR)
        return
    if not ARMED:
        link.nack(pkt, CMD_FIRE, NACK_REJECTED_ARMED)
        return

    mask = int.from_bytes(data, "big") & ((1 << CUE_COUNT) - 1)
    code = fire_mask(mask)
    if code == ACK_OK:
        link.ack(pkt, CMD_FIRE)
    else:
        link.nack(pkt, CMD_FIRE, code)


def check_master_link_failsafe():
    global ARMED

    if not ARMED or not link.master_link_lost(MASTER_LINK_TIMEOUT_MS):
        return

    ARMED = False
    script.halt()
    clear_fire_outputs()
    set_armed_outputs(False)
    lcd.refresh()
    print("DISARMED - master link timeout")


def init_lcd_ui():
    if not LCD_ENABLED or lcd_i2c is None:
        return

    buttons = [
        ("up", DebouncedButton(up_button, LCD_BUTTON_DEBOUNCE_MS, BUTTON_ACTIVE_VALUE)),
        ("down", DebouncedButton(down_button, LCD_BUTTON_DEBOUNCE_MS, BUTTON_ACTIVE_VALUE)),
    ]
    if ok_fwd_button is not None:
        buttons.append(("ok", DebouncedButton(ok_fwd_button, LCD_BUTTON_DEBOUNCE_MS, BUTTON_ACTIVE_VALUE)))
    if back_button is not None:
        buttons.append(("back", DebouncedButton(back_button, LCD_BUTTON_DEBOUNCE_MS, BUTTON_ACTIVE_VALUE)))
    lcd.open(lcd_i2c, LCD_ADDRESS, LCD_MAPPING, "Nightfire Field", buttons)


def handle_arm(pkt):
    global ARMED, arm_check_error

    if STARTUP_FAULT:
        ARMED = False
        set_armed_outputs(False)
        link.nack(pkt, CMD_ARM, NACK_INTERNAL_ERROR)
        print("ARM rejected - startup fault:", STARTUP_FAULT_REASON)
        return

    ARMED = False
    script.halt()
    if not check_arm_voltage():
        clear_fire_outputs()
        set_armed_outputs(False)
        measured = "--" if arm_check_mv is None else str(int(arm_check_mv))
        arm_check_error = "arm voltage %smV" % measured
        lcd.refresh()
        link.nack(pkt, CMD_ARM, NACK_INTERNAL_ERROR)
        print(
            "ARM rejected - SHORT_CHECK %smV below %dmV" %
            (measured, ARM_VOLTAGE_CHECK_MIN_MV)
        )
        return

    arm_check_error = ""
    ARMED = True
    set_armed_outputs(True)
    lcd.refresh()
    link.ack(pkt, CMD_ARM)
    print("ARMED - SHORT_CHECK %dmV" % int(arm_check_mv or 0))


def handle(pkt):
    global ARMED

    lcd.refresh()
    cmd = pkt.get("cmd")
    data = pkt.get("data", b"")

    if cmd == CMD_DISCOVER:
        link.discover_reply(
            pkt,
            CUE_COUNT,
            DEVICE_TYPE,
            DISCOVER_BACKOFF_MIN_MS,
            DISCOVER_BACKOFF_MAX_MS,
            DISCOVER_RESPONSE_REPEATS,
            DISCOVER_RESPONSE_GAP_MS,
        )
    elif cmd == CMD_OUTPUT_STATUS_REQ:
        if link.is_for_me(pkt) and not data:
            link.respond(CMD_OUTPUT_STATUS_RSP, build_output_status_payload())
    elif cmd == CMD_ARM:
        handle_arm(pkt)
    elif cmd == CMD_DISARM:
        ARMED = False
        script.halt()
        clear_fire_outputs()
        set_armed_outputs(False)
        link.ack(pkt, CMD_DISARM)
        print("DISARMED")
    elif cmd == CMD_FIRE:
        handle_fire(pkt)
    elif cmd == CMD_RESISTANCE_REQ:
        if link.is_for_me(pkt):
            handle_resistance(pkt)
    elif cmd == CMD_SET_RESISTANCE_CAL:
        if link.is_for_me(pkt):
            handle_set_resistance_calibration(pkt)
    elif cmd == CMD_FIRE_PULSE:
        if link.is_for_me(pkt):
            handle_fire_pulse(pkt)
    elif not script.handle(pkt):
        link.handle_common(pkt)


def handle_radio_payload(payload):
    link.receive(payload, handle)


def setup():
    init_lcd_ui()
    set_armed_outputs(False)
    run_startup_self_tests()
    radio_ready = link.init_radio()
    # Holding UP at power-up starts pairing with a bridge.
    if DebouncedButton(up_button, LCD_BUTTON_DEBOUNCE_MS, BUTTON_ACTIVE_VALUE, irq=False).held_for(1000):
        radio_ready = link.start_pairing()
        lcd.show_pairing()
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
        "Controls SAFE=P%d ARM_LED=P%d UP=P%d DOWN=P%d OK_FWD=%s BACK=%s BATT=P%d active=%d pullup=%d" %
        (
            SAFE_LED_PIN,
            ARM_LED_PIN,
            UP_BUTTON_PIN,
            DOWN_BUTTON_PIN,
            "--" if OK_FWD_BUTTON_PIN is None else "P%d" % OK_FWD_BUTTON_PIN,
            "--" if BACK_BUTTON_PIN is None else "P%d" % BACK_BUTTON_PIN,
            BATT_CHECK_PIN,
            BUTTON_ACTIVE_VALUE,
            1 if BUTTON_INTERNAL_PULL_UP else 0,
        )
    )
    print(
        "Radio node uid=0x%08X group=0x%04X net=0x%04X" %
        (UNIQUE_ID, GROUP_ID, NETWORK_ID)
    )
    return radio_ready


def loop_once():
    """One main-loop pass without radio receive (used by the PC trace harness)."""
    module_ota.poll_timeout()
    poll_fire_outputs()
    script.run()
    check_master_link_failsafe()
    link.service()
    lcd.service()


def main():
    global ARMED

    radio_ready = setup()
    while True:
        try:
            module_ota.poll_timeout()
            poll_fire_outputs()
            script.run()
            check_master_link_failsafe()

            if not radio_ready:
                time.sleep_ms(250)
                radio_ready = link.init_radio()
                continue

            link.service()
            payload = nrf_radio.recv(fire_wait_ms(script.wait_ms(lcd.wait_ms(link.rx_timeout_ms(RADIO_RX_TIMEOUT_MS)))))
            if payload:
                handle_radio_payload(payload)
                poll_fire_outputs()
            lcd.service()
        except Exception as e:
            print("Field main loop error:", e)
            radio_ready = False
            ARMED = False
            script.halt()
            clear_fire_outputs()
            set_armed_outputs(False)
            time.sleep_ms(100)


try:
    service_file = open("field_service.flag", "r")
    service_file.close()
    field_service_mode = True
except OSError:
    field_service_mode = False

if not field_service_mode:
    main()
