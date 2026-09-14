import time
import urandom

import machine
import module_ota
import nrf_radio
from nfcp import *

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

RADIO_CHANNEL = getattr(node_config, "RADIO_CHANNEL", 40)
RADIO_ADDRESS = 0x67676767
RADIO_TX_POWER = 8
RADIO_RX_TIMEOUT_MS = 10

DISCOVER_BACKOFF_MIN_MS = 250
DISCOVER_BACKOFF_MAX_MS = 500
DISCOVER_RESPONSE_REPEATS = 3
DISCOVER_RESPONSE_GAP_MS = 35
SIM_OPEN_CUE_COUNT = 2
SIM_REFRESH_MS = 2500

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

parser = NFCPStreamParser()
nfcp = NFCP(node_id=0x01)
_sim_open = set()
_sim_last_refresh = 0


def ticks_rand(limit):
    if limit <= 0:
        return 0
    return (time.ticks_us() ^ time.ticks_ms()) % limit


def tx_nfcp(frame, repeats=3, gap_ms=20):
    payload = (NETWORK_ID & 0xFFFF).to_bytes(2, "big") + frame
    for i in range(repeats):
        nrf_radio.send(payload)
        if i + 1 < repeats:
            time.sleep_ms(gap_ms)


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
        encode_text_field("0x%08X" % (CONFIG_UNIQUE_ID & 0xFFFFFFFF))
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


def handle_fire(pkt):
    data = pkt.get("data", b"")
    if not data or len(data) > 8:
        reply_nack_if_unique(pkt, CMD_FIRE, NACK_BAD_LENGTH)
        return
    if not ARMED:
        reply_nack_if_unique(pkt, CMD_FIRE, NACK_REJECTED_ARMED)
        return

    mask = int.from_bytes(data, "big") & 0xFFFF
    print("DUMMY FIRE mask=0x%04X" % mask)
    reply_ack_if_unique(pkt, CMD_FIRE)


def handle(pkt):
    global ARMED, GROUP_ID, UNIQUE_ID, CONFIG_UNIQUE_ID, NETWORK_ID, RADIO_CHANNEL

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
        ARMED = True
        reply_ack_if_unique(pkt, CMD_ARM)
        print("ARMED")
        return

    if cmd == CMD_DISARM:
        ARMED = False
        reply_ack_if_unique(pkt, CMD_DISARM)
        print("DISARMED")
        return

    if cmd == CMD_FIRE:
        handle_fire(pkt)
        return

    if cmd == CMD_E77_UPDATE_BEGIN:
        module_ota.begin(pkt, UNIQUE_ID, ARMED, reply_ack_if_unique, reply_nack_if_unique)
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
            nrf_radio.channel(RADIO_CHANNEL)
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

    for b in payload[2:]:
        pkt = parser.feed(b)
        if pkt and address_matches(pkt):
            handle(pkt)


def main():
    nrf_radio.init(
        channel=RADIO_CHANNEL,
        tx_power=RADIO_TX_POWER,
        address=RADIO_ADDRESS,
    )
    print(
        "Nightfire nRF52840 dummy radio node uid=0x%08X group=0x%04X net=0x%04X" %
        (UNIQUE_ID, GROUP_ID, NETWORK_ID)
    )

    while True:
        module_ota.poll_timeout()
        payload = nrf_radio.recv(RADIO_RX_TIMEOUT_MS)
        if payload:
            handle_radio_payload(payload)


main()
