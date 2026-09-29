import time
import urandom

import machine
import module_ota
import nrf_radio
from nfcp import *
from machine import I2C, Pin

try:
    from lcd1602_i2c import I2cLcd1602
except Exception:
    I2cLcd1602 = None

try:
    import nrf_node_config as node_config
except Exception:
    node_config = None


APP_FIRMWARE_NAME = "nrf-dummy"
APP_FIRMWARE_VERSION = "0.1.0"

DEFAULT_NETWORK_ID = 0x04D2
DEFAULT_GROUP_ID = 0x0001
DEFAULT_CUE_COUNT = 16
DEFAULT_DEVICE_TYPE = 0x10

RADIO_CHANNEL = getattr(node_config, "RADIO_CHANNEL", 90)
RADIO_ADDRESS = 0x67676767
RADIO_TX_POWER = 8
RADIO_RX_TIMEOUT_MS = getattr(node_config, "RADIO_RX_TIMEOUT_MS", 50)
RX_DEDUPE_WINDOW_MS = 1500
RX_DEDUPE_CACHE_LIMIT = 16

DISCOVER_BACKOFF_MIN_MS = 250
DISCOVER_BACKOFF_MAX_MS = 500
DISCOVER_RESPONSE_REPEATS = 3
DISCOVER_RESPONSE_GAP_MS = 35
SIM_OPEN_CUE_COUNT = 2
SIM_REFRESH_MS = 2500
SCRIPT_MAX_ENTRIES = 192
SCRIPT_HEARTBEAT_TIMEOUT_MS = 1500
SCRIPT_TIME_SYNC_VALID_MS = 10000
BATTERY_PACK_TYPE = getattr(node_config, "BATTERY_PACK_TYPE", "2S")
BATTERY_MV = getattr(node_config, "BATTERY_MV", 8200)
LCD_ENABLED = getattr(node_config, "LCD_ENABLED", True)
LCD_I2C_ID = getattr(node_config, "LCD_I2C_ID", 0)
LCD_SDA_PIN = getattr(node_config, "LCD_SDA_PIN", 36)
LCD_SCL_PIN = getattr(node_config, "LCD_SCL_PIN", 38)
LCD_I2C_FREQ = getattr(node_config, "LCD_I2C_FREQ", 100000)
LCD_ADDRESS = getattr(node_config, "LCD_ADDRESS", None)
LCD_MAPPING = getattr(node_config, "LCD_MAPPING", "upper")
LCD_COLS = getattr(node_config, "LCD_COLS", 16)
LCD_ROWS = getattr(node_config, "LCD_ROWS", 2)
LCD_REFRESH_MS = getattr(node_config, "LCD_REFRESH_MS", 350)
LCD_BUTTON_DEBOUNCE_MS = getattr(node_config, "LCD_BUTTON_DEBOUNCE_MS", 80)
LCD_CONTINUITY_PAGE_SIZE = getattr(node_config, "LCD_CONTINUITY_PAGE_SIZE", 8)
MASTER_LINK_TIMEOUT_MS = getattr(node_config, "MASTER_LINK_TIMEOUT_MS", 15000)
UP_BUTTON_PIN = getattr(node_config, "UP_BUTTON_PIN", 45)
DOWN_BUTTON_PIN = getattr(node_config, "DOWN_BUTTON_PIN", 47)
OK_FWD_BUTTON_PIN = getattr(node_config, "OK_FWD_BUTTON_PIN", getattr(node_config, "OK_BUTTON_PIN", 43))
BACK_BUTTON_PIN = getattr(node_config, "BACK_BUTTON_PIN", 10)


def valid_radio_channel(channel):
    return channel == 0 or (74 <= channel <= 100)

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
CUE_COUNT = DEFAULT_CUE_COUNT
DEVICE_TYPE = DEFAULT_DEVICE_TYPE
NETWORK_ID = getattr(node_config, "NETWORK_ID", DEFAULT_NETWORK_ID)
ARMED = False
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
_sim_open = set()
_sim_last_refresh = 0
tx_seq = 1
rx_seen = []
debug_radio_payloads = 0
debug_nfcp_packets = 0
debug_discover_packets = 0
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
last_master_rx_ms = 0

LCD_MENU_ITEMS = (
    "System Info",
    "Continuity",
    "Script",
    "Set Group",
    "Set Network",
    "Set Channel",
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
        encode_text_field("") +
        encode_text_field("0x%08X" % (get_default_unique_id() & 0xFFFFFFFF)) +
        encode_text_field("0x%08X" % (CONFIG_UNIQUE_ID & 0xFFFFFFFF)) +
        encode_text_field(str(int(BATTERY_MV))) +
        encode_text_field(BATTERY_PACK_TYPE)
    )


def save_runtime_config():
    try:
        with open("nrf_node_config.py", "w") as f:
            f.write("# Auto-saved nRF dummy config.\n")
            f.write("UNIQUE_ID = 0x%08X\n" % (CONFIG_UNIQUE_ID & 0xFFFFFFFF))
            f.write("GROUP_ID = 0x%04X\n" % (GROUP_ID & 0xFFFF))
            f.write("NETWORK_ID = 0x%04X\n" % (NETWORK_ID & 0xFFFF))
            f.write("RADIO_CHANNEL = %d\n" % (RADIO_CHANNEL & 0xFF))
            f.write("BATTERY_PACK_TYPE = \"%s\"\n" % BATTERY_PACK_TYPE)
            f.write("BATTERY_MV = %d\n" % int(BATTERY_MV or 0))
            f.write("LCD_ENABLED = %s\n" % ("True" if LCD_ENABLED else "False"))
            f.write("LCD_I2C_ID = %d\n" % LCD_I2C_ID)
            f.write("LCD_SDA_PIN = %d\n" % LCD_SDA_PIN)
            f.write("LCD_SCL_PIN = %d\n" % LCD_SCL_PIN)
            f.write("LCD_I2C_FREQ = %d\n" % LCD_I2C_FREQ)
            if LCD_ADDRESS is None:
                f.write("LCD_ADDRESS = None\n")
            else:
                f.write("LCD_ADDRESS = 0x%02X\n" % LCD_ADDRESS)
            f.write("LCD_MAPPING = \"%s\"\n" % LCD_MAPPING)
            f.write("LCD_COLS = %d\n" % LCD_COLS)
            f.write("LCD_ROWS = %d\n" % LCD_ROWS)
            f.write("LCD_REFRESH_MS = %d\n" % LCD_REFRESH_MS)
            f.write("LCD_BUTTON_DEBOUNCE_MS = %d\n" % LCD_BUTTON_DEBOUNCE_MS)
            f.write("LCD_CONTINUITY_PAGE_SIZE = %d\n" % LCD_CONTINUITY_PAGE_SIZE)
            f.write("MASTER_LINK_TIMEOUT_MS = %d\n" % MASTER_LINK_TIMEOUT_MS)
            f.write("UP_BUTTON_PIN = %d\n" % UP_BUTTON_PIN)
            f.write("DOWN_BUTTON_PIN = %d\n" % DOWN_BUTTON_PIN)
            if OK_FWD_BUTTON_PIN is None:
                f.write("OK_FWD_BUTTON_PIN = None\n")
            else:
                f.write("OK_FWD_BUTTON_PIN = %d\n" % OK_FWD_BUTTON_PIN)
            f.write("OK_BUTTON_PIN = OK_FWD_BUTTON_PIN\n")
            if BACK_BUTTON_PIN is None:
                f.write("BACK_BUTTON_PIN = None\n")
            else:
                f.write("BACK_BUTTON_PIN = %d\n" % BACK_BUTTON_PIN)
    except Exception as e:
        print("Config save failed:", e)


class DebouncedButton:
    def __init__(self, pin_id, debounce_ms=80):
        self.pin = None
        self.debounce_ms = int(debounce_ms)
        self.last_raw = False
        self.stable = False
        self.changed_ms = time.ticks_ms()
        if pin_id is not None:
            try:
                self.pin = Pin(pin_id, Pin.IN, Pin.PULL_UP)
                self.last_raw = self.read_raw()
                self.stable = self.last_raw
            except Exception as e:
                print("LCD button init failed P%s:" % str(pin_id), e)

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


def record_master_rx():
    global last_master_rx_ms, lcd_force_refresh

    last_master_rx_ms = time.ticks_ms()
    lcd_force_refresh = True


def init_lcd_ui():
    global lcd_display, lcd_buttons, lcd_force_refresh

    if not LCD_ENABLED:
        return
    if I2cLcd1602 is None:
        print("LCD driver unavailable")
        return

    try:
        try:
            i2c = I2C(
                LCD_I2C_ID,
                sda=Pin(LCD_SDA_PIN),
                scl=Pin(LCD_SCL_PIN),
                freq=LCD_I2C_FREQ,
            )
        except TypeError:
            i2c = I2C(LCD_I2C_ID, sda=Pin(LCD_SDA_PIN), scl=Pin(LCD_SCL_PIN))

        address = I2cLcd1602.find_address(i2c, LCD_ADDRESS)
        if address is None:
            print("LCD not found on I2C")
            return

        lcd_display = I2cLcd1602(i2c, address, LCD_COLS, LCD_ROWS, mapping=LCD_MAPPING)
        lcd_display.show_lines("Nightfire Dummy", "Booting...")
        lcd_buttons = [
            ("up", DebouncedButton(UP_BUTTON_PIN, LCD_BUTTON_DEBOUNCE_MS)),
            ("down", DebouncedButton(DOWN_BUTTON_PIN, LCD_BUTTON_DEBOUNCE_MS)),
        ]
        if OK_FWD_BUTTON_PIN is not None:
            lcd_buttons.append(("ok", DebouncedButton(OK_FWD_BUTTON_PIN, LCD_BUTTON_DEBOUNCE_MS)))
        if BACK_BUTTON_PIN is not None:
            lcd_buttons.append(("back", DebouncedButton(BACK_BUTTON_PIN, LCD_BUTTON_DEBOUNCE_MS)))
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

    save_runtime_config()

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
    global lcd_mode, lcd_menu_index, lcd_force_refresh, lcd_cont_detail_index

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
    return "ARMED" if ARMED else "SAFE"


def lcd_battery_short_text():
    mv = int(BATTERY_MV or 0)
    return "%d.%02dV" % (mv // 1000, (mv % 1000) // 10)


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
    open_mask = build_open_mask()
    if lcd_cont_detail_index < 0:
        good = CUE_COUNT - count_mask_bits(open_mask)
        return "Cont %02d/%02d SIM" % (good, CUE_COUNT), "UP/DN Details"

    page_size = max(1, int(LCD_CONTINUITY_PAGE_SIZE or 1))
    start = lcd_cont_detail_index * page_size + 1
    end = min(CUE_COUNT, start + page_size - 1)

    parts = []
    for cue in range(start, end + 1):
        open_bit = bool(open_mask & (1 << (cue - 1)))
        parts.append("%02d%s" % (cue, " " if open_bit else chr(255)))

    return (
        " ".join(parts[:4]),
        " ".join(parts[4:8]),
    )


def lcd_page_lines():
    if lcd_mode == "home":
        return lcd_home_top_line(), lcd_home_bottom_line()

    if lcd_mode == "menu":
        return lcd_menu_lines()

    if lcd_mode == "edit":
        return lcd_edit_lines()

    if lcd_active_view == "system":
        return "FW %s C%02d" % (APP_FIRMWARE_VERSION, CUE_COUNT), "Net%04X Ch%03d" % (NETWORK_ID & 0xFFFF, RADIO_CHANNEL & 0xFF)

    if lcd_active_view == "continuity":
        return lcd_continuity_lines()

    if lcd_active_view == "script":
        script_id_text = "----" if script_id_loaded is None else "%04X" % script_id_loaded
        return "Script %s" % script_id_text, "%s Run%d N%03d" % (lcd_status_text(), 1 if script_running else 0, script_next_index)

    if lcd_active_view == "edit_blocked":
        return "Disarm first", "BACK=menu"

    return lcd_home_top_line(), lcd_home_bottom_line()


def service_lcd_ui():
    global lcd_display, lcd_last_refresh_ms, lcd_force_refresh

    if lcd_display is None:
        return

    try:
        now = time.ticks_ms()
        for name, button in lcd_buttons:
            if button.update(now):
                lcd_button_action(name)

        if lcd_force_refresh or time.ticks_diff(now, lcd_last_refresh_ms) >= LCD_REFRESH_MS:
            line1, line2 = lcd_page_lines()
            lcd_display.show_lines(lcd_trim(line1), lcd_trim(line2))
            lcd_last_refresh_ms = now
            lcd_force_refresh = False
    except Exception as e:
        lcd_display = None
        print("LCD service failed:", e)


def refresh_open_cues():
    global _sim_open, _sim_last_refresh

    now = time.ticks_ms()
    if _sim_last_refresh and time.ticks_diff(now, _sim_last_refresh) < SIM_REFRESH_MS:
        return

    _sim_last_refresh = now
    _sim_open = set()
    target = min(SIM_OPEN_CUE_COUNT, CUE_COUNT)
    while len(_sim_open) < target:
        _sim_open.add((urandom.getrandbits(16) % CUE_COUNT) + 1)


def build_open_mask():
    refresh_open_cues()
    open_mask = 0
    for cue in _sim_open:
        if 1 <= cue <= CUE_COUNT:
            open_mask |= 1 << (cue - 1)
    return open_mask


def handle_discover(pkt):
    global debug_discover_packets

    debug_discover_packets += 1
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
    nbytes = (CUE_COUNT + 7) // 8
    data = build_open_mask().to_bytes(nbytes, "big")
    tx_nfcp(nfcp.build_response(UNIQUE_ID, CMD_CONTINUITY_RSP, data))


def fire_mask(mask):
    print("DUMMY FIRE mask=0x%04X" % (mask & 0xFFFF))


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
    if not ARMED:
        reply_nack_if_unique(pkt, CMD_FIRE, NACK_REJECTED_ARMED)
        return

    mask = int.from_bytes(data, "big") & 0xFFFF
    fire_mask(mask)
    reply_ack_if_unique(pkt, CMD_FIRE)


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
    if not ARMED:
        script_running = False
        script_last_event = "stopped disarmed"
        return
    if time.ticks_diff(time.ticks_ms(), script_last_heartbeat_ms) > SCRIPT_HEARTBEAT_TIMEOUT_MS:
        script_running = False
        script_last_event = "hb timeout"
        print("SCRIPT stopped - heartbeat timeout")
        return

    elapsed = time.ticks_diff(time.ticks_ms(), script_start_ms)
    while script_next_index < len(script_entries):
        event_ms, cue_mask = script_entries[script_next_index]
        if event_ms > elapsed:
            break
        script_last_event = "fire"
        print("SCRIPT FIRE mask=0x%04X event=%d elapsed=%d" % (cue_mask, event_ms, elapsed))
        fire_mask(cue_mask)
        script_next_index += 1

    if script_next_index >= len(script_entries):
        script_running = False
        script_last_event = "complete"


def handle(pkt):
    global ARMED, GROUP_ID, UNIQUE_ID, CONFIG_UNIQUE_ID, NETWORK_ID, RADIO_CHANNEL
    global script_running, lcd_force_refresh

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
        ARMED = True
        lcd_force_refresh = True
        reply_ack_if_unique(pkt, CMD_ARM)
        print("ARMED")
        return

    if cmd == CMD_DISARM:
        ARMED = False
        script_running = False
        lcd_force_refresh = True
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
        elif not valid_radio_channel(data[0]):
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
    global debug_radio_payloads, debug_nfcp_packets

    debug_radio_payloads += 1
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
            debug_nfcp_packets += 1
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
    radio_ready = init_radio()
    print(
        "Nightfire nRF52840 dummy radio node uid=0x%08X group=0x%04X net=0x%04X" %
        (UNIQUE_ID, GROUP_ID, NETWORK_ID)
    )

    while True:
        try:
            module_ota.poll_timeout()
            run_script_scheduler()

            if not radio_ready:
                time.sleep_ms(250)
                radio_ready = init_radio()
                continue

            payload = nrf_radio.recv(RADIO_RX_TIMEOUT_MS)
            if payload:
                handle_radio_payload(payload)
            service_lcd_ui()
        except Exception as e:
            print("Dummy main loop error:", e)
            radio_ready = False
            ARMED = False
            script_running = False
            time.sleep_ms(100)
