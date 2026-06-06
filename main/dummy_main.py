import time
import urandom

from nfcp import *
import e77_radio
from module_transport import ModuleTransport
from module_ota import ModuleOtaSession


UART_ID = 0
TRANSPORT = "e77"
RADIO_BOOT_PING = True
RADIO_UPDATE_ON_BOOT = True
RADIO_APP_BAUD = 256000
RADIO_BOOTLOADER_BAUD = 115200
APP_FIRMWARE_NAME = "dummy"
APP_FIRMWARE_VERSION = "0.3.0"

UART_BAUD = RADIO_APP_BAUD if TRANSPORT == "e77" else 256000
UART_TX_PIN = 0
UART_RX_PIN = 1
UART_INVERT = False if TRANSPORT == "e77" else True

RS485_DE_PIN = None if TRANSPORT == "e77" else 2  # e.g. 2

CFG_PATH = "/flash/nfcp_cfg.bin"
DEFAULT_GROUP_ID = 0x0001
DEFAULT_UNIQUE_ID = 0x12345678
DEFAULT_CUE_COUNT = 16  # may be set from config; valid range is 1..64
DEFAULT_DEVICE_TYPE = 0x10  # test device type
DEFAULT_RADIO_NETWORK_ID = e77_radio.DEFAULT_NETWORK_ID

BUS_IDLE_MS = 8
DISCOVER_MAX_TRIES = 6
DISCOVER_BACKOFF_MIN_MS = 10
DISCOVER_BACKOFF_MAX_MS = 250
TX_TURNAROUND_MS = 2

# Continuity rate-limit (allowed when ARMED)
CONT_MIN_INTERVAL_MS = 100  # 10/sec
_last_cont_ms = 0

# Simulated continuity fault model
SIM_OPEN_CUE_COUNT = 2
SIM_REFRESH_MS = 2500  # refresh which cues are "open" every 2.5s
_sim_open = set()
_sim_last_refresh = 0

CUE_MIN = 1
CUE_MAX = DEFAULT_CUE_COUNT
SCRIPT_MAX_ENTRIES = 192
SCRIPT_HEARTBEAT_TIMEOUT_MS = 3000


def clamp_cue_count(cue_count):
    try:
        cue_count = int(cue_count)
    except:
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
    except:
        pass
    return (
        DEFAULT_GROUP_ID,
        DEFAULT_UNIQUE_ID,
        DEFAULT_CUE_COUNT,
        DEFAULT_DEVICE_TYPE,
        DEFAULT_RADIO_NETWORK_ID
    )


def save_cfg(group_id: int, unique_id: int, cue_count: int, device_type: int, radio_network_id: int):
    cue_count = clamp_cue_count(cue_count)

    with open(CFG_PATH, "wb") as f:
        f.write((group_id & 0xFFFF).to_bytes(2, "big"))
        f.write((unique_id & 0xFFFFFFFF).to_bytes(4, "big"))
        f.write(bytes([cue_count & 0xFF, device_type & 0xFF]))
        f.write((radio_network_id & 0xFFFF).to_bytes(2, "big"))


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
ota_session = ModuleOtaSession()

ARMED = False
GROUP_ID, UNIQUE_ID, CUE_COUNT, DEVICE_TYPE, RADIO_NETWORK_ID = load_cfg()
script_id_loaded = None
script_entries = []
script_running = False
script_start_ms = 0
script_next_index = 0
script_last_heartbeat_ms = 0

# Clamp cue count to supported protocol range.
# Continuity masks support 1..64 cues.
if CUE_COUNT < 1:
    CUE_COUNT = 1
if CUE_COUNT > 64:
    CUE_COUNT = 64
CUE_MAX = CUE_COUNT

print("NFCP TEST16 RX node started - DEFAULT: DISARMED")
print(
    "CFG: GROUP=0x%04X UNIQUE=0x%08X CUES=%d TYPE=0x%02X NET=0x%04X" %
    (GROUP_ID, UNIQUE_ID, CUE_COUNT, DEVICE_TYPE, RADIO_NETWORK_ID)
)

last_rx_ms = time.ticks_ms()


def bus_idle() -> bool:
    return time.ticks_diff(time.ticks_ms(), last_rx_ms) >= BUS_IDLE_MS


def set_local_radio_network(network_id, timeout_ms=1000):
    return transport.set_network(network_id, timeout_ms)


def tx_bytes(data: bytes):
    transport.tx_bytes(data)


transport.run_radio_update_if_requested(RADIO_UPDATE_ON_BOOT)
transport.radio_boot_ping(RADIO_BOOT_PING)
set_local_radio_network(RADIO_NETWORK_ID)


def encode_text_field(text):
    try:
        data = text.encode()
    except:
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

    return "id=%s entries=%d run=%d next=%d hb=%d" % (
        script_id_text,
        len(script_entries),
        1 if script_running else 0,
        script_next_index,
        hb_age
    )


def handle_ota_begin(data):
    if len(data) < 9:
        return False

    size = int.from_bytes(data[0:4], "big")
    checksum = int.from_bytes(data[4:8], "big")
    path_len = data[8]
    if len(data) != 9 + path_len:
        return False

    try:
        path = bytes(data[9:]).decode()
    except:
        return False

    return ota_session.begin(path, size, checksum)


def handle_ota_write(data):
    if len(data) < 4:
        return False

    offset = int.from_bytes(data[0:4], "big")
    return ota_session.write(offset, data[4:])


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


def sim_refresh_open_cues():
    global _sim_open, _sim_last_refresh
    now = time.ticks_ms()
    if _sim_last_refresh == 0 or time.ticks_diff(now, _sim_last_refresh) >= SIM_REFRESH_MS:
        _sim_last_refresh = now
        _sim_open = set()
        # pick SIM_OPEN_CUE_COUNT distinct cues 1..CUE_COUNT
        # simple loop to avoid heavy operations
        target_open_count = SIM_OPEN_CUE_COUNT
        if target_open_count > CUE_MAX:
            target_open_count = CUE_MAX
        while len(_sim_open) < target_open_count:
            cue = (urandom.getrandbits(16) % CUE_MAX) + 1
            _sim_open.add(cue)


def build_open_mask() -> int:
    """
    Variable-length continuity mask source.
    bit0=cue1, bit63=cue64
    1 = OPEN, 0 = OK/closed
    """
    sim_refresh_open_cues()
    open_mask = 0
    for cue in _sim_open:
        if 1 <= cue <= CUE_COUNT:
            open_mask |= (1 << (cue - 1))
    return open_mask & 0xFFFFFFFFFFFFFFFF


def build_continuity_frame(src_unique, open_mask):
    cue_count = clamp_cue_count(CUE_COUNT)
    nbytes = (cue_count + 7) // 8

    open_mask &= (1 << (nbytes * 8)) - 1
    data = open_mask.to_bytes(nbytes, "big")

    return nfcp.build_response(src_unique, CMD_CONTINUITY_RSP, data)


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
            nonce=nonce
        )
        tx_bytes(rsp)
        break


def clear_script(script_id=None):
    global script_id_loaded, script_entries, script_running, script_start_ms, script_next_index
    global script_last_heartbeat_ms

    script_id_loaded = script_id
    script_entries = []
    script_running = False
    script_start_ms = 0
    script_next_index = 0
    script_last_heartbeat_ms = 0


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

    if script_id_loaded != script_id or not script_entries:
        reply_nack_if_unique(pkt, CMD_SCRIPT_START, NACK_INVALID_VALUE)
        return

    script_next_index = 0
    while script_next_index < len(script_entries):
        if script_entries[script_next_index][0] >= offset_ms:
            break
        script_next_index += 1

    script_start_ms = time.ticks_add(time.ticks_ms(), start_delay_ms - offset_ms)
    script_running = True
    script_last_heartbeat_ms = time.ticks_ms()
    reply_ack_if_unique(pkt, CMD_SCRIPT_START)


def handle_script_stop(pkt):
    global script_running, script_last_heartbeat_ms

    script_running = False
    script_last_heartbeat_ms = 0
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
        return
    if time.ticks_diff(time.ticks_ms(), script_last_heartbeat_ms) > SCRIPT_HEARTBEAT_TIMEOUT_MS:
        script_running = False
        print("SCRIPT stopped - heartbeat timeout")
        return

    elapsed = time.ticks_diff(time.ticks_ms(), script_start_ms)
    while script_next_index < len(script_entries):
        event_ms, cue_mask = script_entries[script_next_index]
        if event_ms > elapsed:
            break
        print("SCRIPT FIRE mask=0x%04X @ %dms" % (cue_mask, event_ms))
        script_next_index += 1

    if script_next_index >= len(script_entries):
        script_running = False


def handle(pkt):
    global ARMED, GROUP_ID, UNIQUE_ID, CUE_COUNT, DEVICE_TYPE, RADIO_NETWORK_ID, _last_cont_ms, script_running

    cmd = pkt["cmd"]

    # ---- SPEED TEST: immediate echo/ACK ----
    if cmd == CMD_PING:
        # Respond ONLY when addressed via UNIQUE ID (not group/broadcast)
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
        print("DISARMED")
        reply_ack_if_unique(pkt, CMD_DISARM)
        return

    if cmd == CMD_DISCOVER:
        print("Discovery requested")
        handle_discover(pkt)
        return

    # FIRE intentionally ignored in this test device
    if cmd == CMD_FIRE:
        print("FIRE received (ignored - dummy device)")
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

    if cmd == CMD_OTA_BEGIN:
        if ARMED:
            tx_bytes(nfcp.build_nack(UNIQUE_ID, CMD_OTA_BEGIN, NACK_REJECTED_ARMED))
            return
        if handle_ota_begin(pkt.get("data", b"")):
            tx_bytes(nfcp.build_ack(UNIQUE_ID, CMD_OTA_BEGIN, ACK_OK))
        else:
            tx_bytes(nfcp.build_nack(UNIQUE_ID, CMD_OTA_BEGIN, NACK_INVALID_VALUE))
        return

    if cmd == CMD_OTA_WRITE:
        if handle_ota_write(pkt.get("data", b"")):
            tx_bytes(nfcp.build_ack(UNIQUE_ID, CMD_OTA_WRITE, ACK_OK))
        else:
            tx_bytes(nfcp.build_nack(UNIQUE_ID, CMD_OTA_WRITE, NACK_INVALID_VALUE))
        return

    if cmd == CMD_OTA_END:
        if ota_session.end():
            tx_bytes(nfcp.build_ack(UNIQUE_ID, CMD_OTA_END, ACK_OK))
        else:
            tx_bytes(nfcp.build_nack(UNIQUE_ID, CMD_OTA_END, NACK_INVALID_VALUE))
        return

    if cmd == CMD_OTA_ABORT:
        ota_session.abort()
        tx_bytes(nfcp.build_ack(UNIQUE_ID, CMD_OTA_ABORT, ACK_OK))
        return

    # Continuity allowed even when ARMED, but rate-limited
    if cmd == CMD_CONTINUITY_REQ:
        now = time.ticks_ms()
        if time.ticks_diff(now, _last_cont_ms) < CONT_MIN_INTERVAL_MS:
            return
        _last_cont_ms = now

        try:
            open_mask = build_open_mask()

            # New on-wire continuity format:
            # open_mask(1..8 bytes), big-endian, bit0=cue1, 1=open, 0=OK/closed
            # Byte count is based on CUE_COUNT: ceil(CUE_COUNT / 8), max 8 bytes.
            rsp = build_continuity_frame(UNIQUE_ID, open_mask)
            tx_bytes(rsp)

            # Optional serial print for debugging
            # print("CONT: open cues:", sorted(list(_sim_open)), "mask=0x%016X" % open_mask)
        except:
            tx_bytes(nfcp.build_nack(UNIQUE_ID, CMD_CONTINUITY_REQ, NACK_INTERNAL_ERROR))
        return

    # ---- SET_GROUP with ACK/NACK ----
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
        except:
            tx_bytes(nfcp.build_nack(UNIQUE_ID, CMD_SET_GROUP, NACK_INTERNAL_ERROR))
        return

    # ---- SET_NETWORK with ACK/NACK ----
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
        except:
            tx_bytes(nfcp.build_nack(UNIQUE_ID, CMD_SET_NETWORK, NACK_INTERNAL_ERROR))
        return

    # ---- SET_UNIQUE with ACK/NACK (responds from OLD unique) ----
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
        except:
            tx_bytes(nfcp.build_nack(old_unique, CMD_SET_UNIQUE, NACK_INTERNAL_ERROR))
        return


def handle_nfcp_bytes(data):
    for nfcp_b in data:
        pkt = parser.feed(nfcp_b)
        if pkt and address_matches(pkt):
            handle(pkt)


while True:
    had_data = False

    if transport.any():
        data = transport.read_nfcp_bytes()
        if data:
            had_data = True
            last_rx_ms = time.ticks_ms()
            handle_nfcp_bytes(data)

    run_script_scheduler()

    if not had_data:
        time.sleep_ms(1)


