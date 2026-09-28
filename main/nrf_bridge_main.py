import time

from machine import Pin, UART

import bridge_radio
import machine
import nrf_radio

try:
    import nrf_bridge_config as bridge_config
except Exception:
    bridge_config = None


APP_FIRMWARE_NAME = "nrf-bridge"
APP_FIRMWARE_VERSION = "0.1.1"

DEFAULT_NETWORK_ID = bridge_radio.DEFAULT_NETWORK_ID
DEFAULT_RADIO_CHANNEL = 40
DEFAULT_RADIO_ADDRESS = 0x67676767
DEFAULT_RADIO_TX_POWER = 8

DEFAULT_UART_ID = 0
DEFAULT_UART_BAUD = 256000
DEFAULT_UART_TX_PIN = 6
DEFAULT_UART_RX_PIN = 8

NETWORK_ID = getattr(bridge_config, "NETWORK_ID", DEFAULT_NETWORK_ID)
RADIO_CHANNEL = getattr(bridge_config, "RADIO_CHANNEL", DEFAULT_RADIO_CHANNEL)
RADIO_ADDRESS = getattr(bridge_config, "RADIO_ADDRESS", DEFAULT_RADIO_ADDRESS)
RADIO_TX_POWER = getattr(bridge_config, "RADIO_TX_POWER", DEFAULT_RADIO_TX_POWER)

UART_ID = getattr(bridge_config, "UART_ID", DEFAULT_UART_ID)
UART_BAUD = getattr(bridge_config, "UART_BAUD", DEFAULT_UART_BAUD)
UART_TX_PIN = getattr(bridge_config, "UART_TX_PIN", DEFAULT_UART_TX_PIN)
UART_RX_PIN = getattr(bridge_config, "UART_RX_PIN", DEFAULT_UART_RX_PIN)

RADIO_POLL_TIMEOUT_MS = 50
UART_READ_MAX = 64
POST_SEND_RX_WINDOW_MS = 1500
RX_DEDUPE_WINDOW_MS = 1500
RX_DEDUPE_CACHE_LIMIT = 16
SCAN_BUSY_DBM = -85
SCAN_SAMPLE_MS = 45
SCAN_SAMPLE_INTERVAL_MS = 3
SCAN_CANDIDATE_CHANNELS = (0,) + tuple(range(74, 101))

local_parser = bridge_radio.BridgeFrameParser()
uart = None
rx_seen = []
stat_rx_duplicate = 0
stat_rx_wrong_network = 0


def int_to_be_signed(value, size):
    if value < 0:
        value += 1 << (size * 8)
    return bridge_radio.int_to_be(value, size)


def encode_text_field(text):
    try:
        data = text.encode()
    except Exception:
        data = b""
    if len(data) > 63:
        data = data[:63]
    return bytes([len(data)]) + data


def get_node_id():
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
    return 0x5284B001


def build_info_payload():
    return (
        bridge_radio.int_to_be(get_node_id(), 4) +
        bridge_radio.int_to_be(0, 2) +
        bridge_radio.int_to_be(NETWORK_ID & 0xFFFF, 2) +
        encode_text_field(APP_FIRMWARE_VERSION)
    )


def stat_value(stats, key, fallback=0):
    try:
        return int(stats.get(key, fallback))
    except Exception:
        return fallback


def build_stats_payload():
    try:
        stats = nrf_radio.stats()
    except Exception:
        stats = {}

    tx_power = stat_value(stats, "tx_power", RADIO_TX_POWER)
    last_rssi = stat_value(stats, "last_rssi", 0)
    payload = bytearray()
    payload += bridge_radio.int_to_be(stat_value(stats, "tx_ok"), 4)
    payload += bridge_radio.int_to_be(stat_value(stats, "tx_timeout"), 4)
    payload += bridge_radio.int_to_be(stat_value(stats, "rx_ok"), 4)
    payload += bridge_radio.int_to_be(stat_value(stats, "rx_timeout"), 4)
    payload += bridge_radio.int_to_be(stat_value(stats, "rx_crc_error"), 4)
    payload += bridge_radio.int_to_be(stat_value(stats, "rx_bad_len"), 4)
    payload.append(stat_value(stats, "channel", RADIO_CHANNEL) & 0xFF)
    payload += int_to_be_signed(tx_power, 1)
    payload += int_to_be_signed(last_rssi, 2)
    payload += bridge_radio.int_to_be(stat_value(stats, "max_payload", 252), 2)
    payload += bridge_radio.int_to_be(stat_value(stats, "address", RADIO_ADDRESS), 4)
    payload += bridge_radio.int_to_be(stat_value(stats, "tx_busy"), 4)
    payload += bridge_radio.int_to_be(stat_value(stats, "cca_ok"), 4)
    payload += bridge_radio.int_to_be(stat_value(stats, "cca_busy"), 4)
    payload += bridge_radio.int_to_be(stat_rx_duplicate, 4)
    payload += bridge_radio.int_to_be(stat_rx_wrong_network, 4)
    return bytes(payload)


def write_local(seq, cmd, payload=b"", flags=bridge_radio.FLAG_RESPONSE):
    uart.write(bridge_radio.build_frame(seq, cmd, payload, flags))


def write_error(seq, cmd, code=1):
    write_local(seq, cmd, bytes([code & 0xFF]), bridge_radio.FLAG_RESPONSE | bridge_radio.FLAG_ERROR)


def remember_rx_packet(seq, frame):
    global rx_seen

    if seq is None:
        return False

    now = time.ticks_ms()
    signature = bytes(frame[:24])
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


def set_network(network_id):
    global NETWORK_ID

    NETWORK_ID = network_id & 0xFFFF
    save_runtime_config()


def set_radio_channel(channel):
    global RADIO_CHANNEL

    if channel < 0 or channel > 100:
        return False

    RADIO_CHANNEL = channel
    nrf_radio.init(
        channel=RADIO_CHANNEL,
        tx_power=RADIO_TX_POWER,
        address=RADIO_ADDRESS,
    )
    save_runtime_config()
    return True


def save_runtime_config():
    try:
        with open("nrf_bridge_config.py", "w") as f:
            f.write("NETWORK_ID = 0x%04X\n" % (NETWORK_ID & 0xFFFF))
            f.write("RADIO_CHANNEL = %d\n" % (RADIO_CHANNEL & 0xFF))
            f.write("RADIO_ADDRESS = 0x%08X\n" % (RADIO_ADDRESS & 0xFFFFFFFF))
            f.write("RADIO_TX_POWER = %d\n" % RADIO_TX_POWER)
            f.write("UART_ID = %d\n" % UART_ID)
            f.write("UART_BAUD = %d\n" % UART_BAUD)
            f.write("UART_TX_PIN = %d\n" % UART_TX_PIN)
            f.write("UART_RX_PIN = %d\n" % UART_RX_PIN)
    except Exception as e:
        print("Bridge config save failed:", e)


def build_scan_payload():
    results = []

    for channel in SCAN_CANDIDATE_CHANNELS:
        try:
            sample = nrf_radio.noise_sample(
                channel,
                duration_ms=SCAN_SAMPLE_MS,
                interval_ms=SCAN_SAMPLE_INTERVAL_MS,
                busy_dbm=SCAN_BUSY_DBM
            )
        except TypeError:
            sample = nrf_radio.noise_sample(
                channel,
                duration_ms=SCAN_SAMPLE_MS,
                interval_ms=SCAN_SAMPLE_INTERVAL_MS
            )
        count = int(sample.get("count", 0))
        avg = int(sample.get("avg", 0))
        min_rssi = int(sample.get("min", 0))
        max_rssi = int(sample.get("max", 0))
        busy_count = int(sample.get("busy_count", 0))
        if "busy_count" not in sample and max_rssi >= SCAN_BUSY_DBM:
            busy_count = count

        quiet_margin = SCAN_BUSY_DBM - max_rssi
        avg_margin = SCAN_BUSY_DBM - avg
        score = quiet_margin * 4 + avg_margin * 2 - busy_count * 5
        if score < 0:
            score = 0
        if score > 100:
            score = 100

        results.append((
            int(sample.get("channel", channel)),
            avg,
            min_rssi,
            max_rssi,
            count,
            busy_count,
            score,
        ))

    results.sort(key=lambda item: (-item[6], item[3], item[1], item[0]))
    payload = bytearray()
    payload.append(min(10, len(results)))

    for channel, avg, min_rssi, max_rssi, count, busy_count, score in results[:10]:
        payload.append(channel & 0xFF)
        payload += int_to_be_signed(avg, 2)
        payload += int_to_be_signed(min_rssi, 2)
        payload += int_to_be_signed(max_rssi, 2)
        payload.append(count & 0xFF)
        payload.append(busy_count & 0xFF)
        payload.append(score & 0xFF)

    nrf_radio.channel(RADIO_CHANNEL)
    return bytes(payload)


def handle_local_frame(frame):
    global stat_rx_duplicate, stat_rx_wrong_network

    if frame.cmd == bridge_radio.CMD_PING:
        write_local(frame.seq, bridge_radio.CMD_PONG)
        return

    if frame.cmd == bridge_radio.CMD_GET_INFO:
        write_local(frame.seq, bridge_radio.CMD_GET_INFO, build_info_payload())
        return

    if frame.cmd == bridge_radio.CMD_GET_STATS:
        write_local(frame.seq, bridge_radio.CMD_GET_STATS, build_stats_payload())
        return

    if frame.cmd == bridge_radio.CMD_CLEAR_STATS:
        try:
            nrf_radio.clear_stats()
            stat_rx_duplicate = 0
            stat_rx_wrong_network = 0
            write_local(frame.seq, bridge_radio.CMD_CLEAR_STATS)
        except Exception:
            write_error(frame.seq, bridge_radio.CMD_CLEAR_STATS, 0)
        return

    if frame.cmd == bridge_radio.CMD_SET_NETWORK:
        if len(frame.payload) != 2:
            write_error(frame.seq, bridge_radio.CMD_SET_NETWORK, 2)
            return
        set_network(bridge_radio.int_from_be(frame.payload))
        write_local(frame.seq, bridge_radio.CMD_SET_NETWORK)
        return

    if frame.cmd == bridge_radio.CMD_SET_CHANNEL:
        if len(frame.payload) != 1:
            write_error(frame.seq, bridge_radio.CMD_SET_CHANNEL, 2)
            return
        channel = frame.payload[0]
        if set_radio_channel(channel):
            write_local(frame.seq, bridge_radio.CMD_SET_CHANNEL)
        else:
            write_error(frame.seq, bridge_radio.CMD_SET_CHANNEL, 3)
        return

    if frame.cmd == bridge_radio.CMD_SCAN_CHANNELS:
        try:
            write_local(frame.seq, bridge_radio.CMD_SCAN_CHANNELS, build_scan_payload())
        except Exception:
            write_error(frame.seq, bridge_radio.CMD_SCAN_CHANNELS, 4)
        return

    if frame.cmd == bridge_radio.CMD_SEND_NFCP:
        if len(frame.payload) > bridge_radio.MAX_PAYLOAD - 2:
            write_error(frame.seq, bridge_radio.CMD_SEND_NFCP, 3)
            return

        radio_payload = bridge_radio.int_to_be(NETWORK_ID & 0xFFFF, 2) + bytes([frame.seq & 0xFF]) + frame.payload
        try:
            nrf_radio.send(radio_payload)
            write_local(frame.seq, bridge_radio.CMD_SEND_NFCP, b"\x00")
            receive_and_forward_radio(POST_SEND_RX_WINDOW_MS)
        except Exception:
            write_error(frame.seq, bridge_radio.CMD_SEND_NFCP, 4)
        return

    if frame.cmd == bridge_radio.CMD_BOOTLOADER:
        write_local(frame.seq, bridge_radio.CMD_BOOTLOADER)
        time.sleep_ms(150)
        try:
            machine.bootloader()
        except Exception:
            machine.reset()
        return

    if frame.cmd == bridge_radio.CMD_RESET:
        write_local(frame.seq, bridge_radio.CMD_RESET)
        time.sleep_ms(150)
        machine.reset()
        return

    write_error(frame.seq, frame.cmd, 0xFF)


def handle_local_uart():
    if not uart.any():
        return

    data = uart.read(UART_READ_MAX) or b""
    for b in data:
        frame = local_parser.feed(b)
        if frame:
            handle_local_frame(frame)


def handle_radio_packet(payload):
    global stat_rx_duplicate, stat_rx_wrong_network

    if len(payload) < 3:
        return

    network_id = bridge_radio.int_from_be(payload[:2])
    if network_id != NETWORK_ID:
        stat_rx_wrong_network += 1
        return

    radio_seq = None
    nfcp_offset = 2

    if len(payload) >= 4 and payload[2] != 0xFF and payload[3] == 0xFF:
        radio_seq = payload[2]
        nfcp_offset = 3

    rssi = nrf_radio.last_rssi()
    nfcp_frame = payload[nfcp_offset:]
    if remember_rx_packet(radio_seq, nfcp_frame):
        stat_rx_duplicate += 1
        return

    rx_payload = int_to_be_signed(rssi, 2) + int_to_be_signed(0, 1) + nfcp_frame
    write_local(0, bridge_radio.CMD_NFCP_RX, rx_payload, 0)


def receive_and_forward_radio(timeout_ms):
    deadline = time.ticks_add(time.ticks_ms(), timeout_ms)
    while time.ticks_diff(deadline, time.ticks_ms()) > 0:
        payload = nrf_radio.recv(RADIO_POLL_TIMEOUT_MS)
        if payload:
            handle_radio_packet(payload)
            deadline = time.ticks_add(time.ticks_ms(), 100)


def init_uart():
    try:
        return UART(
            UART_ID,
            baudrate=UART_BAUD,
            tx=Pin(UART_TX_PIN),
            rx=Pin(UART_RX_PIN),
        )
    except TypeError:
        return UART(UART_ID, UART_BAUD)


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
    global uart

    try:
        uart = init_uart()
    except Exception as e:
        uart = None
        print("UART init failed:", e)

    radio_ready = init_radio()

    print(
        "Nightfire nRF52840 bridge net=0x%04X ch=%d uart=%d baud=%d tx=P%d rx=P%d" %
        (NETWORK_ID, RADIO_CHANNEL, UART_ID, UART_BAUD, UART_TX_PIN, UART_RX_PIN)
    )

    while True:
        try:
            if uart is None:
                time.sleep_ms(250)
                try:
                    uart = init_uart()
                except Exception as e:
                    print("UART retry failed:", e)
                    continue

            handle_local_uart()

            if not radio_ready:
                time.sleep_ms(250)
                radio_ready = init_radio()
                continue

            payload = nrf_radio.recv(RADIO_POLL_TIMEOUT_MS)
            if payload:
                handle_radio_packet(payload)
        except Exception as e:
            print("Bridge main loop error:", e)
            radio_ready = False
            time.sleep_ms(100)


main()
