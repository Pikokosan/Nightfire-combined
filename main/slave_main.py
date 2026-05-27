# main_mc33996_16.py
# NFCP 16-cue module using one MC33996 output driver.
# Copy this to the RP2040 as main.py along with nfcp.py and mc33996_driver.py.

from machine import UART, Pin, SPI
import time
import urandom

from nfcp import *
import e77_radio
from mc33996_driver import MC33996


# -------------------------
# NFCP / RS-485 UART config
# -------------------------
UART_ID = 0
TRANSPORT = "e77"
RADIO_BOOT_PING = True
RADIO_UPDATE_ON_BOOT = True

UART_BAUD = 115200 if TRANSPORT == "e77" else 256000
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
            if len(b) == 8:
                g = int.from_bytes(b[0:2], "big")
                u = int.from_bytes(b[2:6], "big")
                cues = clamp_cue_count(b[6])
                dtype = b[7]
                return g, u, cues, dtype
            if len(b) == 6:
                g = int.from_bytes(b[0:2], "big")
                u = int.from_bytes(b[2:6], "big")
                return g, u, DEFAULT_CUE_COUNT, DEFAULT_DEVICE_TYPE
    except Exception:
        pass
    return DEFAULT_GROUP_ID, DEFAULT_UNIQUE_ID, DEFAULT_CUE_COUNT, DEFAULT_DEVICE_TYPE


def save_cfg(group_id, unique_id, cue_count, device_type):
    cue_count = clamp_cue_count(cue_count)

    with open(CFG_PATH, "wb") as f:
        f.write((group_id & 0xFFFF).to_bytes(2, "big"))
        f.write((unique_id & 0xFFFFFFFF).to_bytes(4, "big"))
        f.write(bytes([cue_count & 0xFF, device_type & 0xFF]))


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
uart = UART(
    UART_ID,
    baudrate=UART_BAUD,
    tx=Pin(UART_TX_PIN),
    rx=Pin(UART_RX_PIN),
    invert=(UART.INV_TX | UART.INV_RX) if UART_INVERT else 0,
)
uart.read(1)

de = None
if RS485_DE_PIN is not None:
    de = Pin(RS485_DE_PIN, Pin.OUT, value=0)

parser = NFCPStreamParser()
radio_parser = e77_radio.E77FrameParser()
radio_seq = 1
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
GROUP_ID, UNIQUE_ID, _stored_cues, DEVICE_TYPE = load_cfg()
CUE_COUNT = 16  # this hardware is fixed at 16 cues

print("NFCP MC33996 16-cue module started - DEFAULT: DISARMED")
print("CFG: GROUP=0x%04X UNIQUE=0x%08X CUES=%d TYPE=0x%02X" % (GROUP_ID, UNIQUE_ID, CUE_COUNT, DEVICE_TYPE))
print("MC33996 SPI%d SCK=%d MOSI=%d MISO=%d CS=%d RESET=%s" % (SPI_ID, SCK_PIN, MOSI_PIN, MISO_PIN, CS_PIN, str(RESET_PIN)))

last_rx_ms = time.ticks_ms()


def restore_radio_uart():
    global uart, radio_parser

    uart = UART(
        UART_ID,
        baudrate=UART_BAUD,
        tx=Pin(UART_TX_PIN),
        rx=Pin(UART_RX_PIN),
        invert=(UART.INV_TX | UART.INV_RX) if UART_INVERT else 0,
    )
    uart.read(1)
    radio_parser = e77_radio.E77FrameParser()


def run_radio_update_if_requested():
    if TRANSPORT != "e77" or not RADIO_UPDATE_ON_BOOT:
        return

    try:
        import e77_bootloader
    except Exception as e:
        print("E77 updater import failed:", e)
        return

    if not e77_bootloader.update_requested():
        return

    info = e77_bootloader.find_firmware()
    if not info:
        print("E77 radio update requested, but no firmware was found")
        return

    def progress(message):
        print("E77 radio update:", message)

    try:
        result = e77_bootloader.flash_first_available(
            UART_ID,
            UART_TX_PIN,
            UART_RX_PIN,
            app_baud=UART_BAUD,
            boot_baud=115200,
            progress_cb=progress,
        )
        e77_bootloader.clear_update_request()
        print("E77 radio update complete:", result.get("path", ""))
    except Exception as e:
        print("E77 radio update failed:", e)
    finally:
        restore_radio_uart()


def bus_idle():
    return time.ticks_diff(time.ticks_ms(), last_rx_ms) >= BUS_IDLE_MS


def radio_boot_ping():
    if TRANSPORT != "e77" or not RADIO_BOOT_PING:
        return

    seq = 0xA5
    uart.write(e77_radio.build_frame(seq, e77_radio.CMD_PING))
    deadline = time.ticks_add(time.ticks_ms(), 1000)

    while time.ticks_diff(deadline, time.ticks_ms()) > 0:
        if not uart.any():
            time.sleep_ms(5)
            continue

        data = uart.read(32) or b""
        for b in data:
            frame = radio_parser.feed(b)
            if frame and frame.seq == seq and frame.cmd == e77_radio.CMD_PONG:
                print("E77 radio UART ping OK")
                return

    print("E77 radio UART ping failed")


def tx_bytes(data):
    global radio_seq

    if TRANSPORT == "e77":
        for attempt in range(3):
            seq = radio_seq
            uart.write(e77_radio.build_send_nfcp(seq, data))
            radio_seq = (radio_seq + 1) & 0xFF
            if radio_seq == 0:
                radio_seq = 1

            deadline = time.ticks_add(time.ticks_ms(), 250)
            while time.ticks_diff(deadline, time.ticks_ms()) > 0:
                if not uart.any():
                    time.sleep_ms(2)
                    continue

                rx_data = uart.read(32) or b""
                for b in rx_data:
                    frame = radio_parser.feed(b)
                    if not frame:
                        continue
                    if frame.cmd == e77_radio.CMD_SEND_NFCP and frame.seq == seq:
                        if frame.payload and frame.payload[0] == 0:
                            return
                        break

            time.sleep_ms(20 + attempt * 20)

        print("E77 SEND_NFCP failed")
        return

    if de is not None:
        de.value(1)
        time.sleep_ms(1)
    uart.write(data)
    uart.flush()
    if de is not None:
        time.sleep_ms(TX_TURNAROUND_MS)
        de.value(0)


run_radio_update_if_requested()
radio_boot_ping()


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
    global ARMED, GROUP_ID, UNIQUE_ID, DEVICE_TYPE, _last_cont_ms

    cmd = pkt["cmd"]

    if cmd == CMD_PING:
        if not (pkt.get("frame_type") == SOF_UNIQUE and pkt.get("target") == UNIQUE_ID):
            return
        data = pkt.get("data", b"")
        if not data:
            data = bytes([PING_REPLY_BYTE])
        tx_bytes(nfcp.build_response(UNIQUE_ID, CMD_PING, data))
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
            save_cfg(GROUP_ID, UNIQUE_ID, CUE_COUNT, DEVICE_TYPE)
            tx_bytes(nfcp.build_ack(UNIQUE_ID, CMD_SET_GROUP, ACK_OK))
            print("NEW GROUP_ID = 0x%04X" % GROUP_ID)
        except Exception:
            tx_bytes(nfcp.build_nack(UNIQUE_ID, CMD_SET_GROUP, NACK_INTERNAL_ERROR))
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
            save_cfg(GROUP_ID, UNIQUE_ID, CUE_COUNT, DEVICE_TYPE)
            print("NEW UNIQUE_ID = 0x%08X" % UNIQUE_ID)
        except Exception:
            tx_bytes(nfcp.build_nack(old_unique, CMD_SET_UNIQUE, NACK_INTERNAL_ERROR))
        return


def handle_uart_byte(b):
    if TRANSPORT == "e77":
        frame = radio_parser.feed(b)
        if not frame:
            return
        if frame.cmd != e77_radio.CMD_NFCP_RX:
            return

        rx_info = e77_radio.parse_nfcp_rx(frame.payload)
        if not rx_info:
            return

        for nfcp_b in rx_info.get("nfcp_frame", b""):
            pkt = parser.feed(nfcp_b)
            if pkt and address_matches(pkt):
                handle(pkt)
        return

    pkt = parser.feed(b)
    if pkt and address_matches(pkt):
        handle(pkt)


try:
    while True:
        if uart.any():
            b = uart.read(1)
            if b:
                last_rx_ms = time.ticks_ms()
                handle_uart_byte(b[0])
        time.sleep_ms(1)
finally:
    # In case of soft reset/exception, try to leave outputs safe.
    try:
        mc.digitalWriteMask(0x0000)
    except Exception:
        pass
