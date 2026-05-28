from machine import UART, Pin
import time
import urandom

from nfcp import *
import e77_radio


UART_ID = 0
TRANSPORT = "e77"
RADIO_BOOT_PING = True
RADIO_UPDATE_ON_BOOT = True
RADIO_APP_BAUD = 256000
RADIO_BOOTLOADER_BAUD = 115200

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

ARMED = False
GROUP_ID, UNIQUE_ID, CUE_COUNT, DEVICE_TYPE, RADIO_NETWORK_ID = load_cfg()

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
            boot_baud=RADIO_BOOTLOADER_BAUD,
            progress_cb=progress,
        )
        e77_bootloader.clear_update_request()
        print("E77 radio update complete:", result.get("path", ""))
    except Exception as e:
        print("E77 radio update failed:", e)
    finally:
        restore_radio_uart()


def bus_idle() -> bool:
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


def set_local_radio_network(network_id, timeout_ms=1000):
    if TRANSPORT != "e77":
        return True

    seq = 0xA6
    uart.write(e77_radio.build_set_network(seq, network_id))
    deadline = time.ticks_add(time.ticks_ms(), timeout_ms)

    while time.ticks_diff(deadline, time.ticks_ms()) > 0:
        if not uart.any():
            time.sleep_ms(5)
            continue

        data = uart.read(32) or b""
        for b in data:
            frame = radio_parser.feed(b)
            if not frame:
                continue
            if frame.seq != seq:
                continue
            if frame.cmd == e77_radio.CMD_SET_NETWORK and not (frame.flags & e77_radio.FLAG_ERROR):
                print("E77 radio network = 0x%04X" % (network_id & 0xFFFF))
                return True

    print("E77 radio network set failed")
    return False


def tx_bytes(data: bytes):
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
set_local_radio_network(RADIO_NETWORK_ID)


def address_matches(pkt):
    if pkt["frame_type"] == SOF_BROADCAST:
        return True
    if pkt["frame_type"] == SOF_GROUP:
        return pkt.get("target") == GROUP_ID
    if pkt["frame_type"] == SOF_UNIQUE:
        return pkt.get("target") == UNIQUE_ID
    return False


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


def handle(pkt):
    global ARMED, GROUP_ID, UNIQUE_ID, CUE_COUNT, DEVICE_TYPE, RADIO_NETWORK_ID, _last_cont_ms

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


    if cmd == CMD_ARM:
        ARMED = True
        print("ARMED")
        return

    if cmd == CMD_DISARM:
        ARMED = False
        print("DISARMED")
        return

    if cmd == CMD_DISCOVER:
        print("Discovery requested")
        handle_discover(pkt)
        return

    # FIRE intentionally ignored in this test device
    if cmd == CMD_FIRE:
        print("FIRE received (ignored - dummy device)")
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


while True:
    if uart.any():
        b = uart.read(1)
        if b:
            last_rx_ms = time.ticks_ms()
            handle_uart_byte(b[0])

    time.sleep_ms(1)


