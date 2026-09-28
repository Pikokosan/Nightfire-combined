import time

import machine
import module_ota
import nrf_radio
from analog_mux74hc4051 import HC4051Scanner
from field_measurement import (
    BatteryVoltageSensor,
    ChannelCalibration,
    ChannelClassifier,
    ChannelSenseSensor,
    PretestCueSenseSensor,
    STATE_FAULT,
    STATE_NORMAL,
    STATE_OPEN,
)
from nfcp import *
from machine import I2C, Pin
from shift_register74hc595 import ShiftRegister74HC59524

try:
    import nrf_node_config as node_config
except Exception:
    node_config = None


APP_FIRMWARE_NAME = "field-module"
APP_FIRMWARE_VERSION = "0.2.0"

DEFAULT_NETWORK_ID = 0x04D2
DEFAULT_GROUP_ID = 0x0001
DEFAULT_CUE_COUNT = 18
DEFAULT_DEVICE_TYPE = 0x20

RADIO_CHANNEL = getattr(node_config, "RADIO_CHANNEL", 40)
RADIO_ADDRESS = 0x67676767
RADIO_TX_POWER = 8
RADIO_RX_TIMEOUT_MS = 10
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

# SuperMini NRF52840 field-module pinout. The local MicroPython build addresses
# nRF pins as integers: P0.nn is nn, and P1.nn is 32 + nn.
EXTERNAL_VCC_CONTROL_ENABLED = getattr(node_config, "EXTERNAL_VCC_CONTROL_ENABLED", True)
EXTERNAL_VCC_CONTROL_PIN = getattr(node_config, "EXTERNAL_VCC_CONTROL_PIN", 13)  # P0.13 board VCC control
EXTERNAL_VCC_ENABLE_VALUE = getattr(node_config, "EXTERNAL_VCC_ENABLE_VALUE", 1)
EXTERNAL_VCC_SETTLE_MS = getattr(node_config, "EXTERNAL_VCC_SETTLE_MS", 50)

PRE_TEST_PIN = getattr(node_config, "PRE_TEST_PIN", 9)
SAFE_LED_PIN = getattr(node_config, "SAFE_LED_PIN", 10)
ARM_LED_PIN = getattr(node_config, "ARM_LED_PIN", 11)
UP_BUTTON_PIN = getattr(node_config, "UP_BUTTON_PIN", 33)      # P1.01 bottom pad
DOWN_BUTTON_PIN = getattr(node_config, "DOWN_BUTTON_PIN", 34)  # P1.02 bottom pad
OK_FWD_BUTTON_PIN = getattr(node_config, "OK_FWD_BUTTON_PIN", getattr(node_config, "OK_BUTTON_PIN", None))
BACK_BUTTON_PIN = getattr(node_config, "BACK_BUTTON_PIN", None)
BATT_CHECK_PIN = getattr(node_config, "BATT_CHECK_PIN", 29)    # P0.29 / D20 / AIN5
LCD_SDA_PIN = getattr(node_config, "LCD_SDA_PIN", 39)          # P1.07 bottom pad
LCD_SCL_PIN = getattr(node_config, "LCD_SCL_PIN", 35)          # P1.03 if LCD is fitted later

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
CONT_MIN_INTERVAL_MS = 100
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

CONT_STATUS_OK = 0x00
CONT_STATUS_BATTERY_INVALID = 0x01
CONT_STATUS_ADC_INVALID = 0x02
CONT_STATUS_FAULT_DETECTED = 0x04

def get_default_unique_id():
    try:
        uid = nrf_radio.device_id32()
        if uid:
            return int(uid) & 0xFFFFFFFF
    except Exception:
        pass
    try:
        uid = machine.unique_id()
        if uid:
            return int.from_bytes(uid[-4:], "big") & 0xFFFFFFFF
    except Exception:
        pass
    return 0x52840001


CONFIG_UNIQUE_ID = getattr(node_config, "UNIQUE_ID", 0)
GROUP_ID = getattr(node_config, "GROUP_ID", DEFAULT_GROUP_ID)
UNIQUE_ID = (CONFIG_UNIQUE_ID & 0xFFFFFFFF) or get_default_unique_id()
CUE_COUNT = int(getattr(node_config, "CUE_COUNT", DEFAULT_CUE_COUNT))
if CUE_COUNT < 1:
    CUE_COUNT = 1
if CUE_COUNT > DEFAULT_CUE_COUNT:
    CUE_COUNT = DEFAULT_CUE_COUNT
DEVICE_TYPE = DEFAULT_DEVICE_TYPE
NETWORK_ID = getattr(node_config, "NETWORK_ID", DEFAULT_NETWORK_ID)
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
_last_cont_ms = 0
fire_out_mask = 0
fire_deadlines = [0] * CUE_COUNT


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
up_button = make_pin(UP_BUTTON_PIN, Pin.IN, Pin.PULL_UP, name="up button")
down_button = make_pin(DOWN_BUTTON_PIN, Pin.IN, Pin.PULL_UP, name="down button")
ok_fwd_button = make_pin(OK_FWD_BUTTON_PIN, Pin.IN, Pin.PULL_UP, name="ok/fwd button") if OK_FWD_BUTTON_PIN is not None else None
back_button = make_pin(BACK_BUTTON_PIN, Pin.IN, Pin.PULL_UP, name="back button") if BACK_BUTTON_PIN is not None else None

try:
    lcd_i2c = I2C(0, sda=Pin(LCD_SDA_PIN), scl=Pin(LCD_SCL_PIN))
except Exception as e:
    lcd_i2c = None
    print("LCD I2C init skipped:", e)


def make_adc(pin):
    try:
        return machine.ADC(Pin(pin))
    except Exception:
        return machine.ADC(pin)


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
)


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


def tx_nfcp(frame, repeats=3, gap_ms=20):
    payload = (NETWORK_ID & 0xFFFF).to_bytes(2, "big") + bytes([next_tx_seq()]) + frame
    for i in range(repeats):
        nrf_radio.send(payload)
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
        with open("nrf_node_config.py", "w") as f:
            f.write("UNIQUE_ID = 0x%08X\n" % (CONFIG_UNIQUE_ID & 0xFFFFFFFF))
            f.write("GROUP_ID = 0x%04X\n" % (GROUP_ID & 0xFFFF))
            f.write("NETWORK_ID = 0x%04X\n" % (NETWORK_ID & 0xFFFF))
            f.write("RADIO_CHANNEL = %d\n" % (RADIO_CHANNEL & 0xFF))
    except Exception as e:
        print("Config save failed:", e)


def set_armed_outputs(armed):
    if armed and STARTUP_FAULT:
        armed = False
    safe_led.value(0 if armed else 1)
    arm_led.value(1 if armed else 0)
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


def open_cue_list(open_mask):
    out = []
    for cue in range(1, CUE_COUNT + 1):
        if open_mask & (1 << (cue - 1)):
            out.append(cue)
    return out


def build_continuity_payload(open_mask):
    nbytes = (CUE_COUNT + 7) // 8
    open_mask &= (1 << (nbytes * 8)) - 1
    return open_mask.to_bytes(nbytes, "big")


def read_open_mask():
    if STARTUP_FAULT:
        raise RuntimeError("startup fault: " + STARTUP_FAULT_REASON)

    battery = battery_sensor.read()
    battery_mv = battery.get("battery_mv")
    status = CONT_STATUS_OK
    if not battery.get("valid"):
        status |= CONT_STATUS_BATTERY_INVALID
        battery_mv = int(BATTERY_MV or 0)

    open_mask = 0
    normal_count = 0
    fault_count = 0

    for cue in range(1, CUE_COUNT + 1):
        reading = channel_sensor.read_channel(cue, battery_mv)
        state = channel_classifier.classify(reading)

        if not reading.get("valid") or state not in (STATE_NORMAL, STATE_OPEN, STATE_FAULT):
            status |= CONT_STATUS_ADC_INVALID

        if state == STATE_NORMAL:
            normal_count += 1
        else:
            open_mask |= 1 << (cue - 1)
            if state == STATE_FAULT:
                status |= CONT_STATUS_FAULT_DETECTED
                fault_count += 1

    disable_measurement_frontend()
    if fault_count:
        print("CONT faults=%d normal=%d" % (fault_count, normal_count))
    return status, open_mask


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


def handle_continuity(pkt):
    global _last_cont_ms

    if STARTUP_FAULT:
        print("CONT rejected - startup fault:", STARTUP_FAULT_REASON)
        reply_nack_if_unique(pkt, CMD_CONTINUITY_REQ, NACK_INTERNAL_ERROR)
        return

    now = time.ticks_ms()
    if time.ticks_diff(now, _last_cont_ms) < CONT_MIN_INTERVAL_MS:
        return
    _last_cont_ms = now

    try:
        status, open_mask = read_open_mask()
        tx_nfcp(nfcp.build_response(UNIQUE_ID, CMD_CONTINUITY_RSP, build_continuity_payload(open_mask)))
        print("CONT status=0x%02X open_mask=0x%06X open_cues=%s" % (
            status,
            open_mask,
            str(open_cue_list(open_mask)),
        ))
    except Exception as e:
        print("CONT error:", e)
        reply_nack_if_unique(pkt, CMD_CONTINUITY_REQ, NACK_INTERNAL_ERROR)


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

    if cmd == CMD_CONTINUITY_REQ:
        if pkt.get("frame_type") == SOF_UNIQUE and pkt.get("target") == UNIQUE_ID:
            handle_continuity(pkt)
        return

    if cmd == CMD_SET_GROUP:
        if ARMED:
            reply_nack_if_unique(pkt, CMD_SET_GROUP, NACK_REJECTED_ARMED)
        elif len(data) != 2:
            reply_nack_if_unique(pkt, CMD_SET_GROUP, NACK_BAD_LENGTH)
        else:
            GROUP_ID = int.from_bytes(data, "big") & 0xFFFF
            save_runtime_config()
            reply_ack_if_unique(pkt, CMD_SET_GROUP)
        return

    if cmd == CMD_SET_NETWORK:
        if ARMED:
            reply_nack_if_unique(pkt, CMD_SET_NETWORK, NACK_REJECTED_ARMED)
        elif len(data) != 2:
            reply_nack_if_unique(pkt, CMD_SET_NETWORK, NACK_BAD_LENGTH)
        else:
            NETWORK_ID = int.from_bytes(data, "big") & 0xFFFF
            save_runtime_config()
            reply_ack_if_unique(pkt, CMD_SET_NETWORK)
        return

    if cmd == CMD_SET_RADIO_CHANNEL:
        if ARMED:
            reply_nack_if_unique(pkt, CMD_SET_RADIO_CHANNEL, NACK_REJECTED_ARMED)
        elif len(data) != 1:
            reply_nack_if_unique(pkt, CMD_SET_RADIO_CHANNEL, NACK_BAD_LENGTH)
        elif data[0] > 100:
            reply_nack_if_unique(pkt, CMD_SET_RADIO_CHANNEL, NACK_INVALID_VALUE)
        else:
            RADIO_CHANNEL = data[0]
            save_runtime_config()
            reply_ack_if_unique(pkt, CMD_SET_RADIO_CHANNEL)
            time.sleep_ms(80)
            nrf_radio.init(
                channel=RADIO_CHANNEL,
                tx_power=RADIO_TX_POWER,
                address=RADIO_ADDRESS,
            )
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
            CONFIG_UNIQUE_ID = int.from_bytes(data, "big") & 0xFFFFFFFF
            UNIQUE_ID = CONFIG_UNIQUE_ID or get_default_unique_id()
            save_runtime_config()
            if pkt.get("frame_type") == SOF_UNIQUE:
                tx_nfcp(nfcp.build_ack(old_unique, CMD_SET_UNIQUE, ACK_OK))
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

    set_armed_outputs(False)
    run_startup_self_tests()
    radio_ready = init_radio()
    print(
        "Nightfire nRF52840 field module uid=0x%08X group=0x%04X net=0x%04X channel=%d" %
        (UNIQUE_ID, GROUP_ID, NETWORK_ID, RADIO_CHANNEL)
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

            if not radio_ready:
                time.sleep_ms(250)
                radio_ready = init_radio()
                continue

            payload = nrf_radio.recv(RADIO_RX_TIMEOUT_MS)
            if payload:
                handle_radio_payload(payload)
        except Exception as e:
            print("Field main loop error:", e)
            radio_ready = False
            ARMED = False
            script_running = False
            clear_fire_outputs()
            set_armed_outputs(False)
            time.sleep_ms(100)


main()
