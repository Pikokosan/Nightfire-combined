import time
import os
import machine
from machine import Pin, SoftSPI

from e77_gfsk import E77Gfsk
from mc33996_driver import MC33996 as MC33996Driver
from nfcp import *


CONFIG_FILE = "e77_slave.cfg"
DEFAULT_NETWORK_ID = 0x04D2
DEFAULT_GROUP_ID = 0x0001
DEFAULT_UNIQUE_ID = 0xE7700001
DEFAULT_CUE_COUNT = 16
DEVICE_TYPE = 0x20
APP_FIRMWARE_VERSION = "0.1.4"
E77_UPDATE_MAX_FILE_SIZE = 256 * 1024
E77_UPDATE_CHUNK_TIMEOUT_MS = 10000

DISCOVER_BACKOFF_MIN_MS = 10
DISCOVER_BACKOFF_MAX_MS = 250
PING_REPLY_BYTE = 0xAA
FIRE_PULSE_MS = 5
SCRIPT_MAX_ENTRIES = 192
SCRIPT_HEARTBEAT_TIMEOUT_MS = 3000
SCRIPT_TIME_SYNC_VALID_MS = 10000
CONT_MIN_INTERVAL_MS = 100
# Datasheet: MC33996 reports open-load only while the output is OFF.
# This flag is for experiments only; driven outputs clear their fault bits.
CONT_DRIVE_DURING_TEST = False
CONT_DRIVE_SETTLE_MS = 5
OPENLOAD_1_IS_OPEN = True
OPENLOAD_DUMMY_READ = False
# Hardware bring-up override:
# bit0=OUT0/Cue1, 1=open, 0=good. OUT0-3 good, OUT4-15 open.
# Set to None to use live MC33996 open-load reads.
CONTINUITY_TEST_OPEN_MASK = None

# Build-time hardware safety switch. The upload/package scripts can compile a
# temporary copy with this set to False by passing -HardwareSafetyOff.
HARDWARE_SAFETY_ENABLED = True
VPWR_ENABLE_PIN = "A11"
VPWR_ENABLE_ACTIVE = 1
VPWR_SETTLE_MS = 20
BATTERY_ADC_PIN = "A0"
SHORT_ADC_PIN = "A1"
PREARM_TEST_PIN = "B3"
PREARM_TEST_ACTIVE = 1
PREARM_TEST_SETTLE_MS = 20
CONT_VPWR_SETTLE_MS = 100
BATTERY_ADC_MIN_RAW = 8000
BATTERY_ADC_FULL_SCALE_MV = 16800
BATTERY_PACK_TYPE = "2S"
SHORT_ADC_MIN_RATIO_PCT = 25
DISPLAY_I2C_ENABLED = False
DISPLAY_I2C_BUS = 1
DISPLAY_I2C_SCL = "B6"
DISPLAY_I2C_SDA = "B7"
DISPLAY_I2C_FREQ = 100000

MC_SCK = "A8"
MC_MOSI = "A10"
MC_MISO = "A5"
MC_CS = "A9"
MC_RESET = "A12"

class MC33996:
    def __init__(self):
        self.spi = SoftSPI(
            baudrate=500000,
            polarity=0,
            phase=1,
            sck=Pin(MC_SCK),
            mosi=Pin(MC_MOSI),
            miso=Pin(MC_MISO),
        )
        self.driver = MC33996Driver(
            spi=self.spi,
            cs_pin=MC_CS,
            reset_pin=MC_RESET,
        )
        self.out = 0

    def begin(self):
        self.driver.begin()
        self.out = 0

    def digital_write_mask(self, mask):
        self.out = mask & 0xFFFF
        return self.driver.digitalWriteMask(self.out)

    def read_open_load(self, test_mask=None):
        saved_out = self.out
        if test_mask is not None:
            self.digital_write_mask(test_mask)
            time.sleep_ms(CONT_DRIVE_SETTLE_MS)
        try:
            return self.driver.readOpenLoad()
        finally:
            if test_mask is not None:
                self.digital_write_mask(saved_out)


def load_cfg():
    try:
        with open(CONFIG_FILE, "rb") as f:
            data = f.read()
        if len(data) >= 8:
            return (
                int.from_bytes(data[0:2], "big"),
                int.from_bytes(data[2:6], "big"),
                data[6] or DEFAULT_CUE_COUNT,
                int.from_bytes(data[7:9], "big") if len(data) >= 9 else DEFAULT_NETWORK_ID,
            )
    except Exception:
        pass
    return DEFAULT_GROUP_ID, DEFAULT_UNIQUE_ID, DEFAULT_CUE_COUNT, DEFAULT_NETWORK_ID


def save_cfg():
    with open(CONFIG_FILE, "wb") as f:
        f.write((GROUP_ID & 0xFFFF).to_bytes(2, "big"))
        f.write((UNIQUE_ID & 0xFFFFFFFF).to_bytes(4, "big"))
        f.write(bytes([CUE_COUNT & 0xFF]))
        f.write((RADIO_NETWORK_ID & 0xFFFF).to_bytes(2, "big"))


GROUP_ID, UNIQUE_ID, CUE_COUNT, RADIO_NETWORK_ID = load_cfg()
ARMED = False
fire_out_mask = 0
fire_deadlines = [0] * 16
PAIRING_MODE = False
pair_button_down_ms = 0
down_button_down = False
pair_led_last_ms = 0
pair_led_phase = 0
last_rx_network_id = RADIO_NETWORK_ID
_last_cont_ms = 0
script_id_loaded = None
script_entries = []
script_running = False
script_start_ms = 0
script_next_index = 0
script_last_heartbeat_ms = 0
script_time_synced = False
script_master_offset_ms = 0
script_time_sync_local_ms = 0
update_active = False
update_transfer_id = 0
update_target_path = ""
update_temp_path = ""
update_expected_size = 0
update_expected_checksum = 0
update_received_size = 0
update_checksum = 0
update_file = None
update_last_ms = 0
parser = NFCPStreamParser()
nfcp = NFCP(node_id=0x01)
radio = E77Gfsk()
mc = None
vpwr_enable = None
prearm_test = None
battery_adc = None
short_adc = None
hardware_safety_ready = False
display_i2c = None
green_led = Pin("B4", Pin.OUT, value=0)
red_led = Pin("B5", Pin.OUT, value=0)
pair_button = Pin("B8", Pin.IN, Pin.PULL_UP)
down_button = Pin("B0", Pin.IN, Pin.PULL_UP)


def display_i2c_init():
    global display_i2c

    if not DISPLAY_I2C_ENABLED:
        return None

    try:
        display_i2c = machine.I2C(
            DISPLAY_I2C_BUS,
            scl=Pin(DISPLAY_I2C_SCL),
            sda=Pin(DISPLAY_I2C_SDA),
            freq=DISPLAY_I2C_FREQ,
        )
        print(
            "Display I2C ready bus=%d scl=%s sda=%s" %
            (DISPLAY_I2C_BUS, DISPLAY_I2C_SCL, DISPLAY_I2C_SDA)
        )
        return display_i2c
    except Exception as e:
        display_i2c = None
        print("Display I2C init failed:", e)
        return None


def pin_active_value(active=True):
    return VPWR_ENABLE_ACTIVE if active else 1 - VPWR_ENABLE_ACTIVE


def prearm_active_value(active=True):
    return PREARM_TEST_ACTIVE if active else 1 - PREARM_TEST_ACTIVE


def hardware_safety_init():
    global vpwr_enable, prearm_test, battery_adc, short_adc
    global hardware_safety_ready

    if not HARDWARE_SAFETY_ENABLED:
        hardware_safety_ready = True
        print("Hardware safety disabled by build flag")
        return

    try:
        vpwr_enable = Pin(VPWR_ENABLE_PIN, Pin.OUT, value=pin_active_value(False))
        prearm_test = Pin(PREARM_TEST_PIN, Pin.OUT, value=prearm_active_value(False))
        battery_adc = machine.ADC(Pin(BATTERY_ADC_PIN))
        short_adc = machine.ADC(Pin(SHORT_ADC_PIN))
        hardware_safety_ready = True
        print(
            "Hardware safety enabled VPWR=%s BAT=%s SHORT=%s PREARM=%s" %
            (VPWR_ENABLE_PIN, BATTERY_ADC_PIN, SHORT_ADC_PIN, PREARM_TEST_PIN)
        )
    except Exception as e:
        hardware_safety_ready = False
        print("Hardware safety init failed:", e)


def vpwr_set(enabled):
    if not HARDWARE_SAFETY_ENABLED:
        return
    if vpwr_enable is not None:
        vpwr_enable.value(pin_active_value(bool(enabled)))


def prearm_test_set(enabled):
    if not HARDWARE_SAFETY_ENABLED:
        return
    if prearm_test is not None:
        prearm_test.value(prearm_active_value(bool(enabled)))


def read_battery_adc():
    if not HARDWARE_SAFETY_ENABLED or battery_adc is None:
        return 0
    return battery_adc.read_u16()


def read_battery_mv():
    try:
        raw = read_battery_adc()
        if raw <= 0:
            return 0
        return (int(raw) * BATTERY_ADC_FULL_SCALE_MV) // 65535
    except Exception as e:
        print("Battery voltage read failed:", e)
        return 0


def read_short_adc():
    if not HARDWARE_SAFETY_ENABLED or short_adc is None:
        return 0
    return short_adc.read_u16()


def run_prearm_check():
    if not HARDWARE_SAFETY_ENABLED:
        return ACK_OK
    if not hardware_safety_ready:
        print("PREARM failed: hardware safety not ready")
        return NACK_INTERNAL_ERROR

    battery_raw = read_battery_adc()
    if battery_raw < BATTERY_ADC_MIN_RAW:
        print("PREARM failed: battery_raw=%d min=%d" % (battery_raw, BATTERY_ADC_MIN_RAW))
        return NACK_INVALID_VALUE

    prearm_test_set(True)
    try:
        time.sleep_ms(PREARM_TEST_SETTLE_MS)
        short_raw = read_short_adc()
    finally:
        prearm_test_set(False)

    print("PREARM battery_raw=%d short_raw=%d" % (battery_raw, short_raw))
    if short_raw * 100 < battery_raw * SHORT_ADC_MIN_RATIO_PCT:
        print("PREARM failed: short/drop detected")
        return NACK_INVALID_VALUE

    return ACK_OK


def arm_module():
    global ARMED

    result = run_prearm_check()
    if result != ACK_OK:
        ARMED = False
        vpwr_set(False)
        return result

    vpwr_set(True)
    if HARDWARE_SAFETY_ENABLED:
        time.sleep_ms(VPWR_SETTLE_MS)
    ARMED = True
    return ACK_OK


def disarm_module():
    global ARMED, script_running

    ARMED = False
    script_running = False
    try:
        if mc:
            clear_fire_outputs()
    except Exception:
        pass
    prearm_test_set(False)
    vpwr_set(False)


def ticks_rand(limit):
    if limit <= 0:
        return 0
    return (time.ticks_us() ^ time.ticks_ms()) % limit


def change_group(delta):
    global GROUP_ID

    if ARMED or PAIRING_MODE:
        return

    GROUP_ID = (GROUP_ID + delta) & 0xFFFF
    save_cfg()


def update_status_leds(force=False):
    global pair_led_last_ms, pair_led_phase, pair_button_down_ms
    global down_button_down
    global PAIRING_MODE, ARMED, script_running

    now = time.ticks_ms()

    pressed = pair_button.value() == 0

    if pressed:
        if pair_button_down_ms < 0:
            pass
        elif not pair_button_down_ms:
            pair_button_down_ms = now
        elif time.ticks_diff(now, pair_button_down_ms) >= 10000:
            if not PAIRING_MODE:
                PAIRING_MODE = True
                ARMED = False
                script_running = False
                try:
                    if mc:
                        clear_fire_outputs()
                except Exception:
                    pass
            pair_button_down_ms = -1
            force = True
    else:
        if pair_button_down_ms > 0:
            change_group(1)
        pair_button_down_ms = 0

    pressed = down_button.value() == 0
    if pressed:
        down_button_down = True
    else:
        if down_button_down:
            change_group(-1)
        down_button_down = False

    if PAIRING_MODE:
        if force or time.ticks_diff(now, pair_led_last_ms) >= 250:
            pair_led_last_ms = now
            pair_led_phase = 0 if pair_led_phase else 1
            red_led.value(pair_led_phase)
            green_led.value(0 if pair_led_phase else 1)
        return

    pair_led_phase = 0
    red_led.value(1 if ARMED else 0)
    green_led.value(0 if ARMED else 1)


def address_matches(pkt):
    frame_type = pkt.get("frame_type")
    if frame_type == SOF_BROADCAST:
        return True
    if frame_type == SOF_GROUP:
        return pkt.get("target") == GROUP_ID
    if frame_type == SOF_UNIQUE:
        return pkt.get("target") == UNIQUE_ID
    return False


def tx_nfcp(frame, network_id=None):
    if network_id is None:
        network_id = last_rx_network_id if PAIRING_MODE else RADIO_NETWORK_ID
    payload = (network_id & 0xFFFF).to_bytes(2, "big") + frame
    radio.send(payload)


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
        encode_text_field("e77-micropython") +
        encode_text_field(APP_FIRMWARE_VERSION) +
        encode_text_field("") +
        encode_text_field("") +
        encode_text_field("") +
        encode_text_field("") +
        encode_text_field(str(read_battery_mv())) +
        encode_text_field(BATTERY_PACK_TYPE)
    )


def update_close_file():
    global update_file

    try:
        if update_file:
            update_file.close()
    except Exception:
        pass
    update_file = None


def update_remove_temp():
    try:
        if update_temp_path:
            os.remove(update_temp_path)
    except Exception:
        pass


def update_reset_state(remove_temp=False):
    global update_active, update_transfer_id, update_target_path, update_temp_path
    global update_expected_size, update_expected_checksum, update_received_size
    global update_checksum, update_last_ms

    update_close_file()
    if remove_temp:
        update_remove_temp()

    update_active = False
    update_transfer_id = 0
    update_target_path = ""
    update_temp_path = ""
    update_expected_size = 0
    update_expected_checksum = 0
    update_received_size = 0
    update_checksum = 0
    update_last_ms = 0


def safe_update_path(path):
    if not path:
        return None
    if ".." in path:
        return None
    if path.endswith("/"):
        return None
    if path[0] != "/":
        return path
    path = path[1:]
    if not path:
        return None
    if len(path) > 64:
        return None
    return path


def remove_if_exists(path):
    try:
        os.remove(path)
    except Exception:
        pass


def handle_e77_update_begin(pkt):
    global update_active, update_transfer_id, update_target_path, update_temp_path
    global update_expected_size, update_expected_checksum, update_received_size
    global update_checksum, update_file, update_last_ms

    if ARMED or script_running:
        reply_nack_if_unique(pkt, CMD_E77_UPDATE_BEGIN, NACK_REJECTED_ARMED)
        return

    data = pkt.get("data", b"")
    if len(data) < 11:
        reply_nack_if_unique(pkt, CMD_E77_UPDATE_BEGIN, NACK_BAD_LENGTH)
        return

    transfer_id = int.from_bytes(data[0:2], "big") & 0xFFFF
    size = int.from_bytes(data[2:6], "big") & 0xFFFFFFFF
    checksum = int.from_bytes(data[6:10], "big") & 0xFFFFFFFF
    path_len = data[10]
    if len(data) != 11 + path_len:
        reply_nack_if_unique(pkt, CMD_E77_UPDATE_BEGIN, NACK_BAD_LENGTH)
        return

    try:
        target_path = bytes(data[11:]).decode()
    except Exception:
        reply_nack_if_unique(pkt, CMD_E77_UPDATE_BEGIN, NACK_INVALID_VALUE)
        return

    target_path = safe_update_path(target_path)
    if target_path is None or size < 1 or size > E77_UPDATE_MAX_FILE_SIZE:
        reply_nack_if_unique(pkt, CMD_E77_UPDATE_BEGIN, NACK_INVALID_VALUE)
        return

    update_reset_state(remove_temp=True)

    try:
        temp_path = target_path + ".part"
        remove_if_exists(temp_path)
        # The E77 has too little flash to keep both generations of a file.
        remove_if_exists(target_path)
        update_file = open(temp_path, "wb")
    except Exception:
        update_reset_state(remove_temp=True)
        reply_nack_if_unique(pkt, CMD_E77_UPDATE_BEGIN, NACK_INTERNAL_ERROR)
        return

    update_active = True
    update_transfer_id = transfer_id
    update_target_path = target_path
    update_temp_path = temp_path
    update_expected_size = size
    update_expected_checksum = checksum
    update_received_size = 0
    update_checksum = 0
    update_last_ms = time.ticks_ms()
    reply_ack_if_unique(pkt, CMD_E77_UPDATE_BEGIN)


def handle_e77_update_write(pkt):
    global update_received_size, update_checksum, update_last_ms

    data = pkt.get("data", b"")
    if len(data) < 6:
        reply_nack_if_unique(pkt, CMD_E77_UPDATE_WRITE, NACK_BAD_LENGTH)
        return

    transfer_id = int.from_bytes(data[0:2], "big") & 0xFFFF
    offset = int.from_bytes(data[2:6], "big") & 0xFFFFFFFF
    chunk = data[6:]

    if not update_active or transfer_id != update_transfer_id:
        reply_nack_if_unique(pkt, CMD_E77_UPDATE_WRITE, NACK_INVALID_VALUE)
        return

    if offset != update_received_size:
        reply_nack_if_unique(pkt, CMD_E77_UPDATE_WRITE, NACK_INVALID_VALUE)
        return

    if update_received_size + len(chunk) > update_expected_size:
        reply_nack_if_unique(pkt, CMD_E77_UPDATE_WRITE, NACK_INVALID_VALUE)
        return

    try:
        update_file.write(chunk)
    except Exception:
        update_reset_state(remove_temp=True)
        reply_nack_if_unique(pkt, CMD_E77_UPDATE_WRITE, NACK_INTERNAL_ERROR)
        return

    for b in chunk:
        if not isinstance(b, int):
            b = ord(b)
        update_checksum = (update_checksum + b) & 0xFFFFFFFF

    update_received_size += len(chunk)
    update_last_ms = time.ticks_ms()
    reply_ack_if_unique(pkt, CMD_E77_UPDATE_WRITE)


def handle_e77_update_end(pkt):
    data = pkt.get("data", b"")
    if len(data) != 2:
        reply_nack_if_unique(pkt, CMD_E77_UPDATE_END, NACK_BAD_LENGTH)
        return

    transfer_id = int.from_bytes(data, "big") & 0xFFFF
    if not update_active or transfer_id != update_transfer_id:
        reply_nack_if_unique(pkt, CMD_E77_UPDATE_END, NACK_INVALID_VALUE)
        return

    if update_received_size != update_expected_size or update_checksum != update_expected_checksum:
        update_reset_state(remove_temp=True)
        reply_nack_if_unique(pkt, CMD_E77_UPDATE_END, NACK_INVALID_VALUE)
        return

    try:
        update_close_file()
        remove_if_exists(update_target_path)
        os.rename(update_temp_path, update_target_path)
    except Exception:
        update_reset_state(remove_temp=True)
        reply_nack_if_unique(pkt, CMD_E77_UPDATE_END, NACK_INTERNAL_ERROR)
        return

    update_reset_state(remove_temp=False)
    reply_ack_if_unique(pkt, CMD_E77_UPDATE_END)


def handle_e77_update_abort(pkt):
    update_reset_state(remove_temp=True)
    reply_ack_if_unique(pkt, CMD_E77_UPDATE_ABORT)


def handle_e77_update_reboot(pkt):
    if ARMED or script_running:
        reply_nack_if_unique(pkt, CMD_E77_UPDATE_REBOOT, NACK_REJECTED_ARMED)
        return

    reply_ack_if_unique(pkt, CMD_E77_UPDATE_REBOOT)
    time.sleep_ms(250)
    machine.reset()


def poll_update_timeout():
    if not update_active:
        return
    if time.ticks_diff(time.ticks_ms(), update_last_ms) > E77_UPDATE_CHUNK_TIMEOUT_MS:
        update_reset_state(remove_temp=True)


def handle_discover(pkt):
    data = pkt.get("data", b"")
    nonce = int.from_bytes(data[:2], "big") if len(data) >= 2 else None
    span = DISCOVER_BACKOFF_MAX_MS - DISCOVER_BACKOFF_MIN_MS + 1
    time.sleep_ms(DISCOVER_BACKOFF_MIN_MS + ticks_rand(span))
    rsp = nfcp.build_discover_response(
        src_unique=UNIQUE_ID,
        group_id=GROUP_ID,
        unique_id=UNIQUE_ID,
        cue_count=CUE_COUNT,
        device_type=DEVICE_TYPE,
        proto_ver=0x10,
        flags=0x00,
        nonce=nonce,
    )
    tx_nfcp(rsp)


def handle_continuity(pkt):
    global _last_cont_ms

    now = time.ticks_ms()
    if time.ticks_diff(now, _last_cont_ms) < CONT_MIN_INTERVAL_MS:
        return
    _last_cont_ms = now

    nbytes = (CUE_COUNT + 7) // 8
    if nbytes < 1:
        nbytes = 1
    if nbytes > 8:
        nbytes = 8
    try:
        vpwr_was_enabled = bool(ARMED)
        vpwr_auto_enabled = False
        if HARDWARE_SAFETY_ENABLED and hardware_safety_ready and not vpwr_was_enabled:
            vpwr_set(True)
            vpwr_auto_enabled = True
            time.sleep_ms(CONT_VPWR_SETTLE_MS)

        valid_mask = (1 << CUE_COUNT) - 1
        valid_mask &= (1 << (nbytes * 8)) - 1
        test_mask = valid_mask if CONT_DRIVE_DURING_TEST else None

        if CONTINUITY_TEST_OPEN_MASK is not None:
            open_mask = CONTINUITY_TEST_OPEN_MASK & valid_mask
        else:
            if OPENLOAD_DUMMY_READ:
                mc.read_open_load(test_mask)
            _status, raw_openload = mc.read_open_load(test_mask)
            raw_openload &= valid_mask
            if OPENLOAD_1_IS_OPEN:
                open_mask = raw_openload
            else:
                open_mask = (~raw_openload) & valid_mask
        data = open_mask.to_bytes(nbytes, "big")
        tx_nfcp(nfcp.build_response(UNIQUE_ID, CMD_CONTINUITY_RSP, data))
        if vpwr_auto_enabled:
            vpwr_set(False)
    except Exception:
        if HARDWARE_SAFETY_ENABLED and not ARMED:
            vpwr_set(False)
        reply_nack_if_unique(pkt, CMD_CONTINUITY_REQ, NACK_INTERNAL_ERROR)


def clear_fire_outputs():
    global fire_out_mask
    fire_out_mask = 0
    mc.digital_write_mask(0)


def fire_mask(pkt, data):
    data = pkt.get("data", b"")
    if not data or len(data) > 8:
        reply_nack_if_unique(pkt, CMD_FIRE, NACK_BAD_LENGTH)
        return
    result = fire_mask16(int.from_bytes(data, "big") & 0xFFFF)
    if result == ACK_OK:
        reply_ack_if_unique(pkt, CMD_FIRE)
    else:
        reply_nack_if_unique(pkt, CMD_FIRE, result)


def fire_mask16(mask16):
    global fire_out_mask

    if not ARMED:
        return NACK_REJECTED_ARMED

    mask16 &= 0xFFFF
    end_ms = time.ticks_ms() + FIRE_PULSE_MS
    for i in range(16):
        if mask16 & (1 << i):
            fire_deadlines[i] = end_ms
    fire_out_mask |= mask16
    mc.digital_write_mask(fire_out_mask)
    return ACK_OK


def poll_fire_outputs():
    global fire_out_mask

    if not fire_out_mask:
        return

    now = time.ticks_ms()
    for i in range(16):
        bit = 1 << i
        if fire_out_mask & bit and time.ticks_diff(now, fire_deadlines[i]) >= 0:
            fire_out_mask &= ~bit

    mc.digital_write_mask(fire_out_mask)


def clear_script(script_id=None):
    global script_id_loaded, script_entries, script_running, script_start_ms, script_next_index
    global script_last_heartbeat_ms

    script_id_loaded = script_id
    script_entries = []
    script_running = False
    script_start_ms = 0
    script_next_index = 0
    script_last_heartbeat_ms = 0


def handle_time_sync(pkt):
    global script_time_synced, script_master_offset_ms, script_time_sync_local_ms

    data = pkt.get("data", b"")
    if len(data) < 4:
        return

    master_ms = int.from_bytes(data[0:4], "big") & 0x3FFFFFFF
    local_ms = time.ticks_ms() & 0x3FFFFFFF
    script_master_offset_ms = time.ticks_diff(master_ms, local_ms)
    script_time_sync_local_ms = time.ticks_ms()
    script_time_synced = True


def script_time_sync_valid():
    return (
        script_time_synced and
        time.ticks_diff(time.ticks_ms(), script_time_sync_local_ms) <= SCRIPT_TIME_SYNC_VALID_MS
    )


def master_ms_to_local_ms(master_ms):
    return time.ticks_add(master_ms & 0x3FFFFFFF, -script_master_offset_ms)


def handle_script_clear(pkt):
    data = pkt.get("data", b"")
    if len(data) != 2:
        reply_nack_if_unique(pkt, CMD_SCRIPT_CLEAR, NACK_BAD_LENGTH)
        return

    clear_script(int.from_bytes(data, "big") & 0xFFFF)
    reply_ack_if_unique(pkt, CMD_SCRIPT_CLEAR)


def handle_script_load(pkt):
    global script_id_loaded, script_entries

    data = pkt.get("data", b"")
    if len(data) < 4:
        reply_nack_if_unique(pkt, CMD_SCRIPT_LOAD, NACK_BAD_LENGTH)
        return

    script_id = int.from_bytes(data[0:2], "big") & 0xFFFF
    entry_count = data[3]
    payload = data[4:]
    if len(payload) != entry_count * SCRIPT_ENTRY_BYTES:
        reply_nack_if_unique(pkt, CMD_SCRIPT_LOAD, NACK_BAD_LENGTH)
        return

    if script_id_loaded != script_id:
        clear_script(script_id)

    if len(script_entries) + entry_count > SCRIPT_MAX_ENTRIES:
        reply_nack_if_unique(pkt, CMD_SCRIPT_LOAD, NACK_BUSY)
        return

    pos = 0
    for _ in range(entry_count):
        event_ms = int.from_bytes(payload[pos:pos + 4], "big")
        cue_mask = int.from_bytes(payload[pos + 4:pos + 6], "big") & 0xFFFF
        pos += 6
        if cue_mask:
            script_entries.append((event_ms, cue_mask))

    script_entries.sort(key=lambda item: item[0])
    reply_ack_if_unique(pkt, CMD_SCRIPT_LOAD)


def handle_script_start(pkt):
    global script_running, script_start_ms, script_next_index, script_last_heartbeat_ms

    if not ARMED:
        reply_nack_if_unique(pkt, CMD_SCRIPT_START, NACK_REJECTED_ARMED)
        return

    data = pkt.get("data", b"")
    if len(data) < 4:
        reply_nack_if_unique(pkt, CMD_SCRIPT_START, NACK_BAD_LENGTH)
        return

    script_id = int.from_bytes(data[0:2], "big") & 0xFFFF
    start_delay_ms = int.from_bytes(data[2:4], "big")
    offset_ms = 0
    if len(data) >= 8:
        offset_ms = int.from_bytes(data[4:8], "big")
    start_master_ms = None
    if len(data) >= 12:
        start_master_ms = int.from_bytes(data[8:12], "big") & 0x3FFFFFFF

    if script_id_loaded != script_id or not script_entries:
        reply_nack_if_unique(pkt, CMD_SCRIPT_START, NACK_INVALID_VALUE)
        return

    script_next_index = 0
    while script_next_index < len(script_entries):
        if script_entries[script_next_index][0] >= offset_ms:
            break
        script_next_index += 1

    if start_master_ms is not None and script_time_sync_valid():
        script_start_ms = time.ticks_add(master_ms_to_local_ms(start_master_ms), -offset_ms)
    else:
        script_start_ms = time.ticks_add(time.ticks_ms(), start_delay_ms - offset_ms)
    script_running = True
    script_last_heartbeat_ms = time.ticks_ms()
    reply_ack_if_unique(pkt, CMD_SCRIPT_START)


def handle_script_stop(pkt):
    global script_running, script_last_heartbeat_ms

    script_running = False
    script_last_heartbeat_ms = 0
    try:
        clear_fire_outputs()
    except Exception:
        pass
    reply_ack_if_unique(pkt, CMD_SCRIPT_STOP)


def handle_script_heartbeat(pkt):
    global script_last_heartbeat_ms

    data = pkt.get("data", b"")
    if len(data) < 2:
        return

    script_id = int.from_bytes(data[0:2], "big") & 0xFFFF
    if script_running and script_id_loaded == script_id:
        script_last_heartbeat_ms = time.ticks_ms()


def run_script_scheduler():
    global script_running, script_next_index

    if not script_running:
        return

    if not ARMED:
        script_running = False
        try:
            clear_fire_outputs()
        except Exception:
            pass
        return

    if time.ticks_diff(time.ticks_ms(), script_last_heartbeat_ms) > SCRIPT_HEARTBEAT_TIMEOUT_MS:
        script_running = False
        try:
            clear_fire_outputs()
        except Exception:
            pass
        return

    elapsed = time.ticks_diff(time.ticks_ms(), script_start_ms)
    while script_next_index < len(script_entries):
        event_ms, cue_mask = script_entries[script_next_index]
        if event_ms > elapsed:
            break
        fire_mask16(cue_mask)
        script_next_index += 1

    if script_next_index >= len(script_entries):
        script_running = False


def handle(pkt):
    global ARMED, GROUP_ID, UNIQUE_ID, RADIO_NETWORK_ID, script_running, PAIRING_MODE

    cmd = pkt["cmd"]
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
        result = arm_module()
        update_status_leds(force=True)
        if result == ACK_OK:
            reply_ack_if_unique(pkt, CMD_ARM)
        else:
            reply_nack_if_unique(pkt, CMD_ARM, result)
        return

    if cmd == CMD_DISARM:
        disarm_module()
        update_status_leds(force=True)
        reply_ack_if_unique(pkt, CMD_DISARM)
        return

    if cmd == CMD_FIRE:
        fire_mask(pkt, data)
        return

    if cmd == CMD_CONTINUITY_REQ:
        if pkt.get("frame_type") == SOF_UNIQUE and pkt.get("target") == UNIQUE_ID:
            handle_continuity(pkt)
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

    if cmd == CMD_E77_UPDATE_BEGIN:
        handle_e77_update_begin(pkt)
        return

    if cmd == CMD_E77_UPDATE_WRITE:
        handle_e77_update_write(pkt)
        return

    if cmd == CMD_E77_UPDATE_END:
        handle_e77_update_end(pkt)
        return

    if cmd == CMD_E77_UPDATE_ABORT:
        handle_e77_update_abort(pkt)
        return

    if cmd == CMD_E77_UPDATE_REBOOT:
        handle_e77_update_reboot(pkt)
        return

    if cmd == CMD_SET_GROUP:
        if ARMED:
            reply_nack_if_unique(pkt, CMD_SET_GROUP, NACK_REJECTED_ARMED)
        elif len(data) != 2:
            reply_nack_if_unique(pkt, CMD_SET_GROUP, NACK_BAD_LENGTH)
        else:
            GROUP_ID = int.from_bytes(data, "big") & 0xFFFF
            save_cfg()
            reply_ack_if_unique(pkt, CMD_SET_GROUP)
        return

    if cmd == CMD_SET_NETWORK:
        if ARMED:
            reply_nack_if_unique(pkt, CMD_SET_NETWORK, NACK_REJECTED_ARMED)
        elif len(data) != 2:
            reply_nack_if_unique(pkt, CMD_SET_NETWORK, NACK_BAD_LENGTH)
        else:
            new_network = int.from_bytes(data, "big") & 0xFFFF
            reply_ack_if_unique(pkt, CMD_SET_NETWORK)
            RADIO_NETWORK_ID = new_network
            save_cfg()
            PAIRING_MODE = False
            update_status_leds(force=True)
        return

    if cmd == CMD_SET_UNIQUE:
        old_unique = UNIQUE_ID
        if ARMED:
            tx_nfcp(nfcp.build_nack(old_unique, CMD_SET_UNIQUE, NACK_REJECTED_ARMED))
        elif len(data) != 4:
            tx_nfcp(nfcp.build_nack(old_unique, CMD_SET_UNIQUE, NACK_BAD_LENGTH))
        else:
            UNIQUE_ID = int.from_bytes(data, "big") & 0xFFFFFFFF
            tx_nfcp(nfcp.build_ack(old_unique, CMD_SET_UNIQUE, ACK_OK))
            save_cfg()
        return


def handle_radio_payload(payload):
    global last_rx_network_id

    if len(payload) < 3:
        return
    network_id = int.from_bytes(payload[0:2], "big")
    if network_id != RADIO_NETWORK_ID and not (
        PAIRING_MODE and network_id == DEFAULT_NETWORK_ID
    ):
        return
    last_rx_network_id = network_id
    for b in payload[2:]:
        pkt = parser.feed(b)
        if pkt and address_matches(pkt):
            handle(pkt)


def main():
    global mc
    radio.init()
    hardware_safety_init()
    display_i2c_init()
    update_status_leds(force=True)
    mc = MC33996()
    mc.begin()
    try:
        while True:
            update_status_leds()
            payload = radio.poll()
            if payload:
                handle_radio_payload(payload)
            run_script_scheduler()
            poll_fire_outputs()
            poll_update_timeout()
            time.sleep_ms(5)
    finally:
        if mc:
            clear_fire_outputs()
        prearm_test_set(False)
        vpwr_set(False)


main()
