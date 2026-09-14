# main_mc33996_16.py
# NFCP 16-cue module using one MC33996 output driver.
# Copy this to the RP2040 as main.py along with nfcp.py and mc33996_driver.py.

from machine import Pin, SPI
import machine
import time
import urandom

import module_ota
from nfcp import *
import e77_radio
from module_transport import ModuleTransport
from mc33996_driver import MC33996


# -------------------------
# NFCP / RS-485 UART config
# -------------------------
UART_ID = 0
TRANSPORT = "e77"
RADIO_BOOT_PING = True
RADIO_UPDATE_ON_BOOT = True
RADIO_APP_BAUD = 256000
RADIO_BOOTLOADER_BAUD = 115200
APP_FIRMWARE_NAME = "slave"
APP_FIRMWARE_VERSION = "0.3.0"

UART_BAUD = RADIO_APP_BAUD if TRANSPORT == "e77" else 256000
UART_TX_PIN = 0
UART_RX_PIN = 1
UART_INVERT = False if TRANSPORT == "e77" else True
RS485_DE_PIN = None if TRANSPORT == "e77" else 2

# -------------------------
# MC33996 hardware config
# -------------------------
SPI_ID = 0
SCK_PIN = 6
MOSI_PIN = 7
MISO_PIN = 4
CS_PIN = 5
RESET_PIN = 3
SPI_BAUD = 1_000_000
SPI_POLARITY = 0
SPI_PHASE = 1  # MC33996: SI samples on SCLK falling edge, SO changes on rising edge.

# If continuity looks inverted, flip this to False.
# NFCP continuity response uses: 1 = OPEN, 0 = OK/closed.
OPENLOAD_1_IS_OPEN = True

# Some MC33996/open-load circuits need one throwaway read before
# the returned status is valid. Set True if first reads lag.
OPENLOAD_DUMMY_READ = False

# -------------------------
# Device config
# -------------------------
CFG_PATH = "/flash/nfcp_cfg.bin"
DEFAULT_GROUP_ID = 0x0001
DEFAULT_UNIQUE_ID = 0x12345678
DEFAULT_CUE_COUNT = 16
DEFAULT_DEVICE_TYPE = 0x20  # 0x20 = real 16-cue MC33996 module
DEFAULT_RADIO_NETWORK_ID = e77_radio.DEFAULT_NETWORK_ID

BUS_IDLE_MS = 8
DISCOVER_MAX_TRIES = 6
DISCOVER_BACKOFF_MIN_MS = 10
DISCOVER_BACKOFF_MAX_MS = 250
TX_TURNAROUND_MS = 2

CONT_MIN_INTERVAL_MS = 100  # 10/sec max
_last_cont_ms = 0

FIRE_PULSE_MS = 5
LOG_FIRE_EVENTS = False
ALLOW_FIRE_WHEN_DISARMED = False
SCRIPT_MAX_ENTRIES = 192
SCRIPT_HEARTBEAT_TIMEOUT_MS = 3000
SCRIPT_TIME_SYNC_VALID_MS = 10000
LOG_SCRIPT_EVENTS = True

CUE_COUNT = 16


def clamp_cue_count(cue_count):
    try:
        cue_count = int(cue_count)
    except Exception:
        cue_count = DEFAULT_CUE_COUNT

    if cue_count < 1:
        return 1
    if cue_count > 64:
        return 64
    return cue_count


def load_cfg():
    try:
        with open(CFG_PATH, "rb") as f:
            b = f.read()
            if len(b) >= 8:
                g = int.from_bytes(b[0:2], "big")
                u = int.from_bytes(b[2:6], "big")
                cues = clamp_cue_count(b[6])
                dtype = b[7]
                if len(b) >= 10:
                    network = int.from_bytes(b[8:10], "big")
                else:
                    network = DEFAULT_RADIO_NETWORK_ID
                return g, u, cues, dtype, network
            if len(b) == 6:
                g = int.from_bytes(b[0:2], "big")
                u = int.from_bytes(b[2:6], "big")
                return g, u, DEFAULT_CUE_COUNT, DEFAULT_DEVICE_TYPE, DEFAULT_RADIO_NETWORK_ID
    except Exception:
        pass
    return (
        DEFAULT_GROUP_ID,
        DEFAULT_UNIQUE_ID,
        DEFAULT_CUE_COUNT,
        DEFAULT_DEVICE_TYPE,
        DEFAULT_RADIO_NETWORK_ID
    )


def save_cfg(group_id, unique_id, cue_count, device_type, radio_network_id):
    cue_count = clamp_cue_count(cue_count)

    with open(CFG_PATH, "wb") as f:
        f.write((group_id & 0xFFFF).to_bytes(2, "big"))
        f.write((unique_id & 0xFFFFFFFF).to_bytes(4, "big"))
        f.write(bytes([cue_count & 0xFF, device_type & 0xFF]))
        f.write((radio_network_id & 0xFFFF).to_bytes(2, "big"))


def mask_from_payload(data):
    """
    FIRE data is a 1..8 byte big-endian mask.
    bit0 = cue1, bit15 = cue16. Extra bits above cue16 are ignored.
    """
    if not data:
        return None
    if len(data) > 8:
        return None
    return int.from_bytes(data, "big") & 0xFFFF


def open_cue_list(open_mask):
    out = []
    for cue in range(1, CUE_COUNT + 1):
        if open_mask & (1 << (cue - 1)):
            out.append(cue)
    return out


def build_continuity_frame(src_unique, open_mask):
    """
    Build the NFCP continuity response:
        data = open_mask encoded as ceil(CUE_COUNT / 8) bytes.

    Current NFCP continuity format:
        bit0 = cue1
        1 = OPEN / no continuity
        0 = OK / closed
    """
    cue_count = clamp_cue_count(CUE_COUNT)
    nbytes = (cue_count + 7) // 8

    open_mask &= (1 << (nbytes * 8)) - 1
    data = open_mask.to_bytes(nbytes, "big")

    return nfcp.build_response(src_unique, CMD_CONTINUITY_RSP, data)


# -------------------------
# Init UART / RS-485 / NFCP
# -------------------------
transport = ModuleTransport(
    UART_ID,
    UART_BAUD,
    UART_TX_PIN,
    UART_RX_PIN,
    invert=UART_INVERT,
    rs485_de_pin=RS485_DE_PIN,
    tx_turnaround_ms=TX_TURNAROUND_MS,
    transport=TRANSPORT,
    radio_bootloader_baud=RADIO_BOOTLOADER_BAUD,
)

parser = NFCPStreamParser()
nfcp = NFCP(node_id=0x01)

# -------------------------
# Init MC33996
# -------------------------
spi = SPI(
    SPI_ID,
    baudrate=SPI_BAUD,
    polarity=SPI_POLARITY,
    phase=SPI_PHASE,
    bits=8,
    firstbit=SPI.MSB,
    sck=Pin(SCK_PIN),
    mosi=Pin(MOSI_PIN),
    miso=Pin(MISO_PIN),
)

mc = MC33996(
    spi=spi,
    cs_pin=CS_PIN,
    reset_pin=RESET_PIN,
)
mc.begin()

ARMED = False
GROUP_ID, UNIQUE_ID, _stored_cues, DEVICE_TYPE, RADIO_NETWORK_ID = load_cfg()
CUE_COUNT = 16  # this hardware is fixed at 16 cues
fire_out_mask = 0
fire_deadlines = [0] * 16
script_id_loaded = None
script_entries = []
script_running = False
script_start_ms = 0
script_next_index = 0
script_last_heartbeat_ms = 0
script_last_event = "boot"
script_time_synced = False
script_master_offset_ms = 0
script_time_sync_local_ms = 0

print("NFCP MC33996 16-cue module started - DEFAULT: DISARMED")
print(
    "CFG: GROUP=0x%04X UNIQUE=0x%08X CUES=%d TYPE=0x%02X NET=0x%04X" %
    (GROUP_ID, UNIQUE_ID, CUE_COUNT, DEVICE_TYPE, RADIO_NETWORK_ID)
)
print("MC33996 SPI%d SCK=%d MOSI=%d MISO=%d CS=%d RESET=%s" % (SPI_ID, SCK_PIN, MOSI_PIN, MISO_PIN, CS_PIN, str(RESET_PIN)))


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

last_rx_ms = time.ticks_ms()


def bus_idle():
    return time.ticks_diff(time.ticks_ms(), last_rx_ms) >= BUS_IDLE_MS


def set_local_radio_network(network_id, timeout_ms=1000):
    return transport.set_network(network_id, timeout_ms)


def tx_bytes(data):
    transport.tx_bytes(data)


transport.run_radio_update_if_requested(RADIO_UPDATE_ON_BOOT)
transport.radio_boot_ping(RADIO_BOOT_PING)
set_local_radio_network(RADIO_NETWORK_ID)


def encode_text_field(text):
    try:
        data = text.encode()
    except Exception:
        data = b""
    if len(data) > 63:
        data = data[:63]
    return bytes([len(data)]) + data


def build_info_payload():
    radio_info = transport.get_radio_info(300)
    radio_version = ""
    if radio_info:
        radio_version = radio_info.get("firmware_version", "")

    return (
        encode_text_field(APP_FIRMWARE_NAME) +
        encode_text_field(APP_FIRMWARE_VERSION) +
        encode_text_field(radio_version) +
        encode_text_field(build_script_status_text())
    )


def build_script_status_text():
    if script_id_loaded is None:
        script_id_text = "--"
    else:
        script_id_text = "%04X" % script_id_loaded

    hb_age = -1
    if script_last_heartbeat_ms:
        hb_age = time.ticks_diff(time.ticks_ms(), script_last_heartbeat_ms)

    return "id=%s entries=%d run=%d next=%d hb=%d %s" % (
        script_id_text,
        len(script_entries),
        1 if script_running else 0,
        script_next_index,
        hb_age,
        script_last_event
    )


def address_matches(pkt):
    if pkt["frame_type"] == SOF_BROADCAST:
        return True
    if pkt["frame_type"] == SOF_GROUP:
        return pkt.get("target") == GROUP_ID
    if pkt["frame_type"] == SOF_UNIQUE:
        return pkt.get("target") == UNIQUE_ID
    return False


def reply_ack_if_unique(pkt, orig_cmd, code=ACK_OK):
    if pkt.get("frame_type") == SOF_UNIQUE:
        tx_bytes(nfcp.build_ack(UNIQUE_ID, orig_cmd, code))


def reply_nack_if_unique(pkt, orig_cmd, code):
    if pkt.get("frame_type") == SOF_UNIQUE:
        tx_bytes(nfcp.build_nack(UNIQUE_ID, orig_cmd, code))


def handle_discover(pkt):
    nonce = None
    if pkt.get("data") and len(pkt["data"]) >= 2:
        nonce = int.from_bytes(pkt["data"][:2], "big")

    for _ in range(DISCOVER_MAX_TRIES):
        delay = DISCOVER_BACKOFF_MIN_MS + (
            urandom.getrandbits(16) %
            (DISCOVER_BACKOFF_MAX_MS - DISCOVER_BACKOFF_MIN_MS + 1)
        )
        time.sleep_ms(delay)
        if not bus_idle():
            continue

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
        tx_bytes(rsp)
        break


def clear_fire_outputs():
    global fire_out_mask
    fire_out_mask = 0
    mc.digitalWriteMask(0x0000)


def fire_mask(mask16, report_errors=True):
    global fire_out_mask

    if (not ARMED) and (not ALLOW_FIRE_WHEN_DISARMED):
        print("FIRE rejected - DISARMED mask=0x%04X" % mask16)
        return NACK_REJECTED_ARMED

    if mask16 == 0:
        return ACK_OK

    # Safety: only allow real 16 cue bits.
    mask16 &= 0xFFFF
    if LOG_FIRE_EVENTS:
        print("FIRE mask=0x%04X pulse=%dms" % (mask16, FIRE_PULSE_MS))
    try:
        end_ms = time.ticks_add(time.ticks_ms(), FIRE_PULSE_MS)
        for i in range(16):
            if mask16 & (1 << i):
                fire_deadlines[i] = end_ms
        fire_out_mask |= mask16
        mc.digitalWriteMask(fire_out_mask)
        return ACK_OK
    except Exception as e:
        clear_fire_outputs()
        print("FIRE error:", e)
        return NACK_INTERNAL_ERROR


def poll_fire_outputs():
    global fire_out_mask

    if not fire_out_mask:
        return

    now = time.ticks_ms()
    old_mask = fire_out_mask
    for i in range(16):
        bit = 1 << i
        if fire_out_mask & bit and time.ticks_diff(now, fire_deadlines[i]) >= 0:
            fire_out_mask &= ~bit

    if fire_out_mask != old_mask:
        mc.digitalWriteMask(fire_out_mask)


def handle_fire(pkt):
    data = pkt.get("data", b"")
    mask16 = mask_from_payload(data)
    if mask16 is None:
        tx_bytes(nfcp.build_nack(UNIQUE_ID, CMD_FIRE, NACK_BAD_LENGTH))
        return

    result = fire_mask(mask16)
    if result not in (ACK_OK, None):
        tx_bytes(nfcp.build_nack(UNIQUE_ID, CMD_FIRE, result))


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
    if LOG_SCRIPT_EVENTS:
        print("SCRIPT CLEAR id=%s" % ("--" if script_id is None else "%04X" % script_id))


def handle_script_clear(pkt):
    data = pkt.get("data", b"")
    if len(data) != 2:
        reply_nack_if_unique(pkt, CMD_SCRIPT_CLEAR, NACK_BAD_LENGTH)
        return

    clear_script(int.from_bytes(data, "big") & 0xFFFF)
    reply_ack_if_unique(pkt, CMD_SCRIPT_CLEAR)


def handle_script_load(pkt):
    global script_id_loaded, script_entries, script_last_event

    data = pkt.get("data", b"")
    if len(data) < 4:
        reply_nack_if_unique(pkt, CMD_SCRIPT_LOAD, NACK_BAD_LENGTH)
        return

    script_id = int.from_bytes(data[0:2], "big") & 0xFFFF
    entry_count = data[3]
    payload = data[4:]
    if len(payload) != entry_count * 6:
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
    script_last_event = "load"
    if LOG_SCRIPT_EVENTS:
        print(
            "SCRIPT LOAD id=%04X chunk=%d add=%d total=%d" %
            (script_id, data[2], entry_count, len(script_entries))
        )
    reply_ack_if_unique(pkt, CMD_SCRIPT_LOAD)


def handle_script_start(pkt):
    global script_running, script_start_ms, script_next_index, script_last_heartbeat_ms, script_last_event

    if not ARMED:
        script_last_event = "start disarmed"
        if LOG_SCRIPT_EVENTS:
            print("SCRIPT START rejected - disarmed")
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
        script_last_event = "start invalid"
        if LOG_SCRIPT_EVENTS:
            print(
                "SCRIPT START rejected id=%04X loaded=%s entries=%d" %
                (
                    script_id,
                    "--" if script_id_loaded is None else "%04X" % script_id_loaded,
                    len(script_entries)
                )
            )
        reply_nack_if_unique(pkt, CMD_SCRIPT_START, NACK_INVALID_VALUE)
        return

    script_next_index = 0
    while script_next_index < len(script_entries):
        if script_entries[script_next_index][0] >= offset_ms:
            break
        script_next_index += 1

    use_synced_start = start_master_ms is not None and script_time_sync_valid()
    if use_synced_start:
        script_start_ms = time.ticks_add(master_ms_to_local_ms(start_master_ms), -offset_ms)
    else:
        script_start_ms = time.ticks_add(time.ticks_ms(), start_delay_ms - offset_ms)
    script_running = True
    script_last_heartbeat_ms = time.ticks_ms()
    script_last_event = "start"
    if LOG_SCRIPT_EVENTS:
        print(
            "SCRIPT START id=%04X entries=%d delay=%d offset=%d next=%d sync=%d" %
            (script_id, len(script_entries), start_delay_ms, offset_ms, script_next_index, 1 if use_synced_start else 0)
        )
    reply_ack_if_unique(pkt, CMD_SCRIPT_START)


def handle_script_stop(pkt):
    global script_running, script_last_heartbeat_ms, script_last_event

    script_running = False
    script_last_heartbeat_ms = 0
    script_last_event = "stop"
    clear_fire_outputs()
    if LOG_SCRIPT_EVENTS:
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


def run_script_scheduler():
    global script_running, script_next_index, script_last_event

    if not script_running:
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
        if LOG_SCRIPT_EVENTS:
            print("SCRIPT FIRE mask=0x%04X event=%d elapsed=%d" % (cue_mask, event_ms, elapsed))
        fire_mask(cue_mask, report_errors=False)
        script_next_index += 1

    if script_next_index >= len(script_entries):
        script_running = False
        script_last_event = "complete"
        if LOG_SCRIPT_EVENTS:
            print("SCRIPT COMPLETE")


def handle(pkt):
    global ARMED, GROUP_ID, UNIQUE_ID, DEVICE_TYPE, RADIO_NETWORK_ID, _last_cont_ms, script_running

    cmd = pkt["cmd"]

    if cmd == CMD_PING:
        if not (pkt.get("frame_type") == SOF_UNIQUE and pkt.get("target") == UNIQUE_ID):
            return
        data = pkt.get("data", b"")
        if not data:
            data = bytes([PING_REPLY_BYTE])
        tx_bytes(nfcp.build_response(UNIQUE_ID, CMD_PING, data))
        return

    if cmd == CMD_INFO_REQ:
        tx_bytes(nfcp.build_response(UNIQUE_ID, CMD_INFO_RSP, build_info_payload()))
        return

    if cmd == CMD_ARM:
        ARMED = True
        print("ARMED")
        reply_ack_if_unique(pkt, CMD_ARM)
        return

    if cmd == CMD_DISARM:
        ARMED = False
        script_running = False
        clear_fire_outputs()
        print("DISARMED - outputs off")
        reply_ack_if_unique(pkt, CMD_DISARM)
        return

    if cmd == CMD_DISCOVER:
        print("Discovery requested")
        handle_discover(pkt)
        return

    if cmd == CMD_FIRE:
        handle_fire(pkt)
        return

    if cmd == CMD_E77_UPDATE_BEGIN:
        module_ota.begin(pkt, UNIQUE_ID, ARMED or script_running, reply_ack_if_unique, reply_nack_if_unique)
        return

    if cmd == CMD_E77_UPDATE_WRITE:
        module_ota.write(pkt, UNIQUE_ID, reply_ack_if_unique, reply_nack_if_unique)
        return

    if cmd == CMD_E77_UPDATE_END:
        module_ota.end(pkt, UNIQUE_ID, reply_ack_if_unique, reply_nack_if_unique)
        return

    if cmd == CMD_E77_UPDATE_ABORT:
        module_ota.abort(pkt, UNIQUE_ID, reply_ack_if_unique, reply_nack_if_unique)
        return

    if cmd == CMD_E77_UPDATE_REBOOT:
        if module_ota.reboot(pkt, UNIQUE_ID, ARMED or script_running, reply_ack_if_unique, reply_nack_if_unique):
            time.sleep_ms(250)
            machine.reset()
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

    if cmd == CMD_CONTINUITY_REQ:
        now = time.ticks_ms()
        if time.ticks_diff(now, _last_cont_ms) < CONT_MIN_INTERVAL_MS:
            return
        _last_cont_ms = now

        try:
            if OPENLOAD_DUMMY_READ:
                mc.readOpenLoad()
            status, raw_openload = mc.readOpenLoad()
            raw_openload &= 0xFFFF
            if OPENLOAD_1_IS_OPEN:
                open_mask = raw_openload
            else:
                open_mask = (~raw_openload) & 0xFFFF
            rsp = build_continuity_frame(UNIQUE_ID, open_mask)
            tx_bytes(rsp)
            print("CONT status=0x%02X open_mask=0x%04X open_cues=%s" % (
                status,
                open_mask,
                str(open_cue_list(open_mask)),
            ))
        except Exception as e:
            print("CONT error:", e)
            tx_bytes(nfcp.build_nack(UNIQUE_ID, CMD_CONTINUITY_REQ, NACK_INTERNAL_ERROR))
        return

    if cmd == CMD_SET_GROUP:
        if ARMED:
            tx_bytes(nfcp.build_nack(UNIQUE_ID, CMD_SET_GROUP, NACK_REJECTED_ARMED))
            return
        data = pkt.get("data", b"")
        if len(data) != 2:
            tx_bytes(nfcp.build_nack(UNIQUE_ID, CMD_SET_GROUP, NACK_BAD_LENGTH))
            return
        try:
            GROUP_ID = int.from_bytes(data, "big") & 0xFFFF
            save_cfg(GROUP_ID, UNIQUE_ID, CUE_COUNT, DEVICE_TYPE, RADIO_NETWORK_ID)
            tx_bytes(nfcp.build_ack(UNIQUE_ID, CMD_SET_GROUP, ACK_OK))
            print("NEW GROUP_ID = 0x%04X" % GROUP_ID)
        except Exception:
            tx_bytes(nfcp.build_nack(UNIQUE_ID, CMD_SET_GROUP, NACK_INTERNAL_ERROR))
        return

    if cmd == CMD_SET_NETWORK:
        if ARMED:
            tx_bytes(nfcp.build_nack(UNIQUE_ID, CMD_SET_NETWORK, NACK_REJECTED_ARMED))
            return
        data = pkt.get("data", b"")
        if len(data) != 2:
            tx_bytes(nfcp.build_nack(UNIQUE_ID, CMD_SET_NETWORK, NACK_BAD_LENGTH))
            return
        try:
            new_network = int.from_bytes(data, "big") & 0xFFFF
            tx_bytes(nfcp.build_ack(UNIQUE_ID, CMD_SET_NETWORK, ACK_OK))
            RADIO_NETWORK_ID = new_network
            save_cfg(GROUP_ID, UNIQUE_ID, CUE_COUNT, DEVICE_TYPE, RADIO_NETWORK_ID)
            time.sleep_ms(50)
            set_local_radio_network(RADIO_NETWORK_ID)
            print("NEW RADIO_NETWORK_ID = 0x%04X" % RADIO_NETWORK_ID)
        except Exception:
            tx_bytes(nfcp.build_nack(UNIQUE_ID, CMD_SET_NETWORK, NACK_INTERNAL_ERROR))
        return

    if cmd == CMD_SET_UNIQUE:
        old_unique = UNIQUE_ID
        if ARMED:
            tx_bytes(nfcp.build_nack(old_unique, CMD_SET_UNIQUE, NACK_REJECTED_ARMED))
            return
        data = pkt.get("data", b"")
        if len(data) != 4:
            tx_bytes(nfcp.build_nack(old_unique, CMD_SET_UNIQUE, NACK_BAD_LENGTH))
            return
        try:
            new_unique = int.from_bytes(data, "big") & 0xFFFFFFFF
            tx_bytes(nfcp.build_ack(old_unique, CMD_SET_UNIQUE, ACK_OK))
            UNIQUE_ID = new_unique
            save_cfg(GROUP_ID, UNIQUE_ID, CUE_COUNT, DEVICE_TYPE, RADIO_NETWORK_ID)
            print("NEW UNIQUE_ID = 0x%08X" % UNIQUE_ID)
        except Exception:
            tx_bytes(nfcp.build_nack(old_unique, CMD_SET_UNIQUE, NACK_INTERNAL_ERROR))
        return


def handle_nfcp_bytes(data):
    for nfcp_b in data:
        pkt = parser.feed(nfcp_b)
        if pkt and address_matches(pkt):
            handle(pkt)


try:
    while True:
        module_ota.poll_timeout()
        had_data = False

        while transport.any():
            data = transport.read_nfcp_bytes()
            if data:
                had_data = True
                last_rx_ms = time.ticks_ms()
                handle_nfcp_bytes(data)

        if not had_data:
            run_script_scheduler()
            poll_fire_outputs()
            time.sleep_ms(1)
        else:
            run_script_scheduler()
            poll_fire_outputs()
finally:
    # In case of soft reset/exception, try to leave outputs safe.
    try:
        clear_fire_outputs()
    except Exception:
        pass
