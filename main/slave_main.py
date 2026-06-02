# main_mc33996_16.py
# NFCP 16-cue module using one MC33996 output driver.
# Copy this to the RP2040 as main.py along with nfcp.py and mc33996_driver.py.

from machine import Pin, SPI
import time
import urandom

from nfcp import *
import e77_radio
from module_transport import ModuleTransport
from module_ota import ModuleOtaSession
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
APP_FIRMWARE_VERSION = "0.2.1"

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

FIRE_PULSE_MS = 100
ALLOW_FIRE_WHEN_DISARMED = False

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
ota_session = ModuleOtaSession()

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

print("NFCP MC33996 16-cue module started - DEFAULT: DISARMED")
print(
    "CFG: GROUP=0x%04X UNIQUE=0x%08X CUES=%d TYPE=0x%02X NET=0x%04X" %
    (GROUP_ID, UNIQUE_ID, CUE_COUNT, DEVICE_TYPE, RADIO_NETWORK_ID)
)
print("MC33996 SPI%d SCK=%d MOSI=%d MISO=%d CS=%d RESET=%s" % (SPI_ID, SCK_PIN, MOSI_PIN, MISO_PIN, CS_PIN, str(RESET_PIN)))

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
        encode_text_field(radio_version)
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
    except Exception:
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


def handle_fire(pkt):
    data = pkt.get("data", b"")
    mask16 = mask_from_payload(data)
    if mask16 is None:
        tx_bytes(nfcp.build_nack(UNIQUE_ID, CMD_FIRE, NACK_BAD_LENGTH))
        return

    if (not ARMED) and (not ALLOW_FIRE_WHEN_DISARMED):
        print("FIRE rejected - DISARMED mask=0x%04X" % mask16)
        tx_bytes(nfcp.build_nack(UNIQUE_ID, CMD_FIRE, NACK_REJECTED_ARMED))
        return

    if mask16 == 0:
        return

    # Safety: only allow real 16 cue bits.
    mask16 &= 0xFFFF
    print("FIRE mask=0x%04X pulse=%dms" % (mask16, FIRE_PULSE_MS))
    try:
        mc.digitalWriteMask(mask16)
        time.sleep_ms(FIRE_PULSE_MS)
        mc.digitalWriteMask(0x0000)
        tx_bytes(nfcp.build_ack(UNIQUE_ID, CMD_FIRE, ACK_OK))
    except Exception as e:
        mc.digitalWriteMask(0x0000)
        print("FIRE error:", e)
        tx_bytes(nfcp.build_nack(UNIQUE_ID, CMD_FIRE, NACK_INTERNAL_ERROR))


def handle(pkt):
    global ARMED, GROUP_ID, UNIQUE_ID, DEVICE_TYPE, RADIO_NETWORK_ID, _last_cont_ms

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
        return

    if cmd == CMD_DISARM:
        ARMED = False
        mc.digitalWriteMask(0x0000)
        print("DISARMED - outputs off")
        return

    if cmd == CMD_DISCOVER:
        print("Discovery requested")
        handle_discover(pkt)
        return

    if cmd == CMD_FIRE:
        handle_fire(pkt)
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


def handle_uart_byte(b):
    for nfcp_b in transport.nfcp_bytes_from_uart_byte(b):
        pkt = parser.feed(nfcp_b)
        if pkt and address_matches(pkt):
            handle(pkt)


try:
    while True:
        if transport.any():
            b = transport.read_byte()
            if b is not None:
                last_rx_ms = time.ticks_ms()
                handle_uart_byte(b)
        time.sleep_ms(1)
finally:
    # In case of soft reset/exception, try to leave outputs safe.
    try:
        mc.digitalWriteMask(0x0000)
    except Exception:
        pass
