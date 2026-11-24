import time
import urandom

import module_ota
import nrf_module_common
import nrf_radio
from machine import I2C, Pin
from module_link import ModuleLink, encode_text_field, ticks_rand
from mesh import MESH_DEFAULT_MAX_HOPS, MESH_RADIO_ADDRESS
from module_lcd import DebouncedButton, LcdMenu
from module_script import ScriptEngine
from nfcp import *

try:
    import nrf_node_config as node_config
except Exception:
    node_config = None


APP_FIRMWARE_NAME = "nrf-dummy"
APP_FIRMWARE_VERSION = "0.2.5"

DEFAULT_NETWORK_ID = 0x04D2
DEFAULT_GROUP_ID = 0x0001
DEFAULT_CUE_COUNT = 16
DEFAULT_DEVICE_TYPE = 0x10

RADIO_CHANNEL = getattr(node_config, "RADIO_CHANNEL", 90)
# Mesh radio (docs/mesh_radio_design.md). Pre-mesh firmware used 0x67676767.
RADIO_ADDRESS = getattr(node_config, "MESH_RADIO_ADDRESS", MESH_RADIO_ADDRESS)
MESH_MAX_HOPS = getattr(node_config, "MESH_MAX_HOPS", MESH_DEFAULT_MAX_HOPS)
MESH_RELAY_ENABLED = getattr(node_config, "MESH_RELAY_ENABLED", True)
# Bench test only: nodes this module pretends it can't hear directly.
MESH_BENCH_DEAF_TO = tuple(getattr(node_config, "MESH_BENCH_DEAF_TO", ()) or ())
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


def get_default_unique_id():
    return nrf_module_common.get_default_unique_id(0x52840001)


CONFIG_UNIQUE_ID = getattr(node_config, "UNIQUE_ID", 0)
GROUP_ID = getattr(node_config, "GROUP_ID", DEFAULT_GROUP_ID)
UNIQUE_ID = (CONFIG_UNIQUE_ID & 0xFFFFFFFF) or get_default_unique_id()
CUE_COUNT = DEFAULT_CUE_COUNT
DEVICE_TYPE = DEFAULT_DEVICE_TYPE
NETWORK_ID = getattr(node_config, "NETWORK_ID", DEFAULT_NETWORK_ID)
ARMED = False

_sim_open = set()
_sim_last_refresh = 0

# Simulated physical outputs, reported by CMD_OUTPUT_STATUS_RSP.
_last_fire_mask = 0
_last_fire_seq = 0
_last_fire_ms = 0
FIRE_PULSE_MS = getattr(node_config, "FIRE_PULSE_MS", 5)

# Simulated resistance of a connected cue: 1.8-2.4 ohm, in deci-ohms.
SIM_CONNECTED_MIN_DECI_OHMS = 18
SIM_CONNECTED_SPAN_DECI_OHMS = 7
RESISTANCE_VALUE_OPEN = 0xFFFF


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
            f.write("MESH_MAX_HOPS = %d\n" % MESH_MAX_HOPS)
            f.write("MESH_RELAY_ENABLED = %s\n" % ("True" if MESH_RELAY_ENABLED else "False"))
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
        return True
    except Exception as e:
        print("Config save failed:", e)
        return False


def build_open_mask():
    """Simulated continuity: a couple of random open cues, reshuffled every few seconds."""
    global _sim_open, _sim_last_refresh

    now = time.ticks_ms()
    if not _sim_last_refresh or time.ticks_diff(now, _sim_last_refresh) >= SIM_REFRESH_MS:
        _sim_last_refresh = now
        _sim_open = set()
        target = min(SIM_OPEN_CUE_COUNT, CUE_COUNT)
        while len(_sim_open) < target:
            _sim_open.add((urandom.getrandbits(16) % CUE_COUNT) + 1)

    open_mask = 0
    for cue in _sim_open:
        if 1 <= cue <= CUE_COUNT:
            open_mask |= 1 << (cue - 1)
    return open_mask


def build_resistance_payload():
    """Simulated resistance scan in the field module format (deci-ohms)."""
    open_mask = build_open_mask()
    payload = bytearray((
        RESISTANCE_PAYLOAD_VERSION,
        RESISTANCE_UNIT_DECI_OHM,
        CUE_COUNT & 0xFF,
        0,  # status: OK
    ))
    for i in range(CUE_COUNT):
        if open_mask & (1 << i):
            value = RESISTANCE_VALUE_OPEN
        else:
            value = SIM_CONNECTED_MIN_DECI_OHMS + urandom.getrandbits(8) % SIM_CONNECTED_SPAN_DECI_OHMS
        payload += value.to_bytes(2, "big")
    return bytes(payload)


def build_output_status_payload():
    """Simulated physical output state in the field module format."""
    flags = 0
    if ARMED:
        flags |= 0x01 | 0x08  # armed, driver enabled
    if script.running:
        flags |= 0x02
    age_ms = 0xFFFF
    if _last_fire_seq:
        age_ms = max(0, min(0xFFFE, time.ticks_diff(time.ticks_ms(), _last_fire_ms)))
    loaded = 0xFFFF if script.id_loaded is None else script.id_loaded & 0xFFFF
    return bytes((1, flags, CUE_COUNT & 0xFF)) + (
        bytes(8) +  # no output is held on
        (_last_fire_mask & 0xFFFFFFFFFFFFFFFF).to_bytes(8, "big") +
        (_last_fire_seq & 0xFFFFFFFF).to_bytes(4, "big") +
        (age_ms & 0xFFFF).to_bytes(2, "big") +
        loaded.to_bytes(2, "big")
    )


def handle_fire_pulse(pkt):
    """Like the field module's CMD_FIRE_PULSE; the dummy holds no outputs,
    so the hold time is only remembered until restart."""
    global FIRE_PULSE_MS

    data = pkt.get("data", b"")
    if data:
        if len(data) != 2:
            link.nack(pkt, CMD_FIRE_PULSE, NACK_BAD_LENGTH)
            return
        if ARMED:
            link.nack(pkt, CMD_FIRE_PULSE, NACK_REJECTED_ARMED)
            return
        if script.running:
            link.nack(pkt, CMD_FIRE_PULSE, NACK_BUSY)
            return
        value = int.from_bytes(data, "big")
        if not 1 <= value <= 2000:
            link.nack(pkt, CMD_FIRE_PULSE, NACK_INVALID_VALUE)
            return
        FIRE_PULSE_MS = value
        print("DUMMY FIRE PULSE %dms" % value)
    link.respond(CMD_FIRE_PULSE_RSP, FIRE_PULSE_MS.to_bytes(2, "big"))


class DummyNode:
    """Firmware state and hooks used by the shared link, script and LCD code."""

    firmware_version = APP_FIRMWARE_VERSION
    can_scan = False

    def ids(self):
        return NETWORK_ID, GROUP_ID, UNIQUE_ID, RADIO_CHANNEL

    def armed(self):
        return ARMED

    def fire(self, mask):
        global _last_fire_mask, _last_fire_seq, _last_fire_ms

        _last_fire_mask = mask
        _last_fire_seq += 1
        _last_fire_ms = time.ticks_ms()
        print("DUMMY FIRE mask=0x%04X" % (mask & 0xFFFF))
        return ACK_OK

    def fire_blocked(self):
        if not ARMED:
            return NACK_REJECTED_ARMED, "disarmed"
        return None

    def stop_outputs(self):
        pass

    def info_payload(self):
        return (
            encode_text_field(APP_FIRMWARE_NAME) +
            encode_text_field(APP_FIRMWARE_VERSION) +
            encode_text_field("nrf-radio-native") +
            encode_text_field(script.status_text()) +
            encode_text_field("0x%08X" % (get_default_unique_id() & 0xFFFFFFFF)) +
            encode_text_field("0x%08X" % (CONFIG_UNIQUE_ID & 0xFFFFFFFF)) +
            encode_text_field(str(int(BATTERY_MV))) +
            encode_text_field(BATTERY_PACK_TYPE)
        )

    def apply_setting(self, name, value):
        global GROUP_ID, NETWORK_ID, RADIO_CHANNEL, CONFIG_UNIQUE_ID, UNIQUE_ID

        if name == "group":
            GROUP_ID = value
        elif name == "network":
            NETWORK_ID = value
        elif name == "channel":
            RADIO_CHANNEL = value
        elif name == "unique":
            CONFIG_UNIQUE_ID = value
            UNIQUE_ID = CONFIG_UNIQUE_ID or get_default_unique_id()
        return save_runtime_config()

    # LCD
    def status_text(self):
        return "ARMED" if ARMED else "SAFE"

    def battery_mv(self):
        return BATTERY_MV

    def cue_count(self):
        return CUE_COUNT

    def continuity(self):
        return build_open_mask(), "SIM"

    def scan_continuity(self):
        pass

    def alert_lines(self):
        return None


node = DummyNode()
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
link.deaf_to = MESH_BENCH_DEAF_TO
if MESH_BENCH_DEAF_TO:
    print("MESH BENCH: deaf to", " ".join("%08X" % u for u in MESH_BENCH_DEAF_TO))
nfcp = link.nfcp
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


def disarm(reason=None):
    """The one disarm path (command, link loss, main loop error)."""
    global ARMED

    ARMED = False
    script.halt()
    lcd.refresh()
    if reason:
        print(reason)


def check_master_link_failsafe():
    if ARMED and link.master_link_lost(MASTER_LINK_TIMEOUT_MS):
        disarm("DISARMED - master link timeout")


def make_button(name, pin_id, irq=True):
    if pin_id is None:
        return None
    try:
        pin = Pin(pin_id, Pin.IN, Pin.PULL_UP)
    except Exception as e:
        print("LCD button init failed P%s:" % str(pin_id), e)
        pin = None
    return name, DebouncedButton(pin, LCD_BUTTON_DEBOUNCE_MS, irq=irq)


def init_lcd_ui():
    if not LCD_ENABLED:
        return
    try:
        try:
            i2c = I2C(LCD_I2C_ID, sda=Pin(LCD_SDA_PIN), scl=Pin(LCD_SCL_PIN), freq=LCD_I2C_FREQ)
        except TypeError:
            i2c = I2C(LCD_I2C_ID, sda=Pin(LCD_SDA_PIN), scl=Pin(LCD_SCL_PIN))
    except Exception as e:
        print("LCD init failed:", e)
        return
    buttons = [
        make_button("up", UP_BUTTON_PIN),
        make_button("down", DOWN_BUTTON_PIN),
        make_button("ok", OK_FWD_BUTTON_PIN),
        make_button("back", BACK_BUTTON_PIN),
    ]
    lcd.open(i2c, LCD_ADDRESS, LCD_MAPPING, "Nightfire Dummy", [b for b in buttons if b])


def handle_fire(pkt):
    data = pkt.get("data", b"")
    if not data or len(data) > 8:
        link.nack(pkt, CMD_FIRE, NACK_BAD_LENGTH)
        return
    if not ARMED:
        link.nack(pkt, CMD_FIRE, NACK_REJECTED_ARMED)
        return
    node.fire(int.from_bytes(data, "big") & 0xFFFF)
    link.ack(pkt, CMD_FIRE)


def handle(pkt):
    global ARMED

    lcd.refresh()
    cmd = pkt.get("cmd")

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
    elif cmd == CMD_ARM:
        ARMED = True
        link.ack(pkt, CMD_ARM)
        print("ARMED")
    elif cmd == CMD_DISARM:
        disarm()
        link.ack(pkt, CMD_DISARM)
        print("DISARMED")
    elif cmd == CMD_FIRE:
        handle_fire(pkt)
    elif cmd == CMD_CONTINUITY_REQ:
        if link.is_for_me(pkt):
            data = build_open_mask().to_bytes((CUE_COUNT + 7) // 8, "big")
            link.respond(CMD_CONTINUITY_RSP, data)
    elif cmd == CMD_RESISTANCE_REQ:
        if link.is_for_me(pkt):
            if len(pkt.get("data", b"")) > 1:
                link.nack(pkt, CMD_RESISTANCE_REQ, NACK_BAD_LENGTH)
            else:
                # A raw-ADC request gets calibrated values too: the dummy has no ADC.
                link.respond(CMD_RESISTANCE_RSP, build_resistance_payload())
    elif cmd == CMD_OUTPUT_STATUS_REQ:
        if link.is_for_me(pkt) and not pkt.get("data", b""):
            link.respond(CMD_OUTPUT_STATUS_RSP, build_output_status_payload())
    elif cmd == CMD_FIRE_PULSE:
        if link.is_for_me(pkt):
            handle_fire_pulse(pkt)
    elif not script.handle(pkt):
        link.handle_common(pkt)


def handle_radio_payload(payload):
    link.receive(payload, handle)


def setup():
    init_lcd_ui()
    radio_ready = link.init_radio()
    # Holding UP at power-up starts pairing with a bridge.
    up = make_button("up", UP_BUTTON_PIN, irq=False)
    if up and up[1].held_for(1000):
        radio_ready = link.start_pairing()
        lcd.show_pairing()
    print(
        "Nightfire nRF52840 dummy radio node uid=0x%08X group=0x%04X net=0x%04X" %
        (UNIQUE_ID, GROUP_ID, NETWORK_ID)
    )
    return radio_ready


def loop_once():
    module_ota.poll_timeout()
    script.run()
    check_master_link_failsafe()
    link.service()
    lcd.service()


def main():
    radio_ready = setup()
    while True:
        try:
            module_ota.poll_timeout()
            script.run()
            check_master_link_failsafe()

            if not radio_ready:
                time.sleep_ms(250)
                radio_ready = link.init_radio()
                continue

            link.service()
            payload = nrf_radio.recv(script.wait_ms(lcd.wait_ms(link.rx_timeout_ms(RADIO_RX_TIMEOUT_MS))))
            if payload:
                handle_radio_payload(payload)
            lcd.service()
        except Exception as e:
            print("Dummy main loop error:", e)
            radio_ready = False
            disarm()
            time.sleep_ms(100)
