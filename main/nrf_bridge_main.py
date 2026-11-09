import time
import os

from machine import Pin, UART

import bridge_radio
import machine
import mesh
import nrf_radio

try:
    import nrf_bridge_config as bridge_config
except Exception:
    bridge_config = None


APP_FIRMWARE_NAME = "nrf-bridge"
APP_FIRMWARE_VERSION = "0.1.2"

DEFAULT_NETWORK_ID = bridge_radio.DEFAULT_NETWORK_ID
DEFAULT_RADIO_CHANNEL = 90
# Mesh radio (docs/mesh_radio_design.md). Pre-mesh firmware used 0x67676767.
DEFAULT_RADIO_ADDRESS = mesh.MESH_RADIO_ADDRESS
DEFAULT_RADIO_TX_POWER = 8

DEFAULT_UART_ID = 0
DEFAULT_UART_BAUD = 256000
DEFAULT_UART_TX_PIN = 6
DEFAULT_UART_RX_PIN = 8

NETWORK_ID = getattr(bridge_config, "NETWORK_ID", DEFAULT_NETWORK_ID)
RADIO_CHANNEL = getattr(bridge_config, "RADIO_CHANNEL", DEFAULT_RADIO_CHANNEL)
# RADIO_ADDRESS in old configs is the pre-mesh address, so it is ignored.
RADIO_ADDRESS = getattr(bridge_config, "MESH_RADIO_ADDRESS", DEFAULT_RADIO_ADDRESS)
RADIO_TX_POWER = getattr(bridge_config, "RADIO_TX_POWER", DEFAULT_RADIO_TX_POWER)

UART_ID = getattr(bridge_config, "UART_ID", DEFAULT_UART_ID)
UART_BAUD = getattr(bridge_config, "UART_BAUD", DEFAULT_UART_BAUD)
UART_TX_PIN = getattr(bridge_config, "UART_TX_PIN", DEFAULT_UART_TX_PIN)
UART_RX_PIN = getattr(bridge_config, "UART_RX_PIN", DEFAULT_UART_RX_PIN)

# Short, so an app command waits at most ~10 ms for the radio (was 50 ms).
RADIO_POLL_TIMEOUT_MS = 10
UART_READ_MAX = 64
POST_SEND_RX_WINDOW_MS = 1500
SCAN_BUSY_DBM = -85
SCAN_SAMPLE_MS = 45
SCAN_SAMPLE_INTERVAL_MS = 3
SCAN_CANDIDATE_CHANNELS = (0,) + tuple(range(74, 101))


def valid_radio_channel(channel):
    return channel == 0 or (74 <= channel <= 100)

local_parser = bridge_radio.BridgeFrameParser()
uart = None
bridge_mesh = None
uart_rx_us = None   # when the current UART chunk was read; time code ages from here
stat_rx_duplicate = 0
stat_rx_wrong_network = 0
file_update = None


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


def safe_update_name(name):
    allowed = (
        "main.py",
        "bridge_radio.py",
        "nrf_bridge_config.py",
    )
    return name in allowed


def remove_if_exists(path):
    try:
        os.remove(path)
    except OSError:
        pass


def rename_if_exists(src, dst):
    try:
        os.rename(src, dst)
        return True
    except OSError:
        return False


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


def set_network(network_id):
    global NETWORK_ID

    NETWORK_ID = network_id & 0xFFFF
    save_runtime_config()


def set_radio_channel(channel):
    global RADIO_CHANNEL

    if not valid_radio_channel(channel):
        return False

    RADIO_CHANNEL = channel
    bridge_mesh.queue.clear()   # repeats queued for the old channel
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
            f.write("MESH_RADIO_ADDRESS = 0x%08X\n" % (RADIO_ADDRESS & 0xFFFFFFFF))
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


def begin_file_update(seq, payload):
    global file_update

    if len(payload) < 7:
        write_error(seq, bridge_radio.CMD_FILE_BEGIN, 2)
        return

    name_len = payload[0]
    if name_len < 1 or len(payload) != 1 + name_len + 6:
        write_error(seq, bridge_radio.CMD_FILE_BEGIN, 2)
        return

    try:
        name = bytes(payload[1:1 + name_len]).decode()
    except Exception:
        write_error(seq, bridge_radio.CMD_FILE_BEGIN, 2)
        return

    if not safe_update_name(name):
        write_error(seq, bridge_radio.CMD_FILE_BEGIN, 3)
        return

    index = 1 + name_len
    total_size = bridge_radio.int_from_be(payload[index:index + 4])
    expected_crc = bridge_radio.int_from_be(payload[index + 4:index + 6])
    tmp_name = name + ".tmp"

    if total_size < 0:
        write_error(seq, bridge_radio.CMD_FILE_BEGIN, 3)
        return

    try:
        if file_update and file_update.get("file", None):
            try:
                file_update["file"].close()
            except Exception:
                pass

        remove_if_exists(tmp_name)
        f = open(tmp_name, "wb")
        file_update = {
            "name": name,
            "tmp_name": tmp_name,
            "total_size": total_size,
            "expected_crc": expected_crc,
            "written": 0,
            "crc": 0xFFFF,
            "file": f,
        }
        write_local(seq, bridge_radio.CMD_FILE_BEGIN)
    except Exception:
        file_update = None
        write_error(seq, bridge_radio.CMD_FILE_BEGIN, 4)


def write_file_update(seq, payload):
    global file_update

    if file_update is None or file_update.get("file", None) is None:
        write_error(seq, bridge_radio.CMD_FILE_WRITE, 5)
        return

    if len(payload) < 4:
        write_error(seq, bridge_radio.CMD_FILE_WRITE, 2)
        return

    offset = bridge_radio.int_from_be(payload[:4])
    data = payload[4:]

    if offset != file_update["written"]:
        write_error(seq, bridge_radio.CMD_FILE_WRITE, 3)
        return

    if file_update["written"] + len(data) > file_update["total_size"]:
        write_error(seq, bridge_radio.CMD_FILE_WRITE, 3)
        return

    try:
        file_update["file"].write(data)
        file_update["written"] += len(data)
        file_update["crc"] = bridge_radio.crc16_ccitt(data, file_update["crc"])
        write_local(seq, bridge_radio.CMD_FILE_WRITE)
    except Exception:
        write_error(seq, bridge_radio.CMD_FILE_WRITE, 4)


def end_file_update(seq):
    global file_update

    if file_update is None or file_update.get("file", None) is None:
        write_error(seq, bridge_radio.CMD_FILE_END, 5)
        return

    name = file_update["name"]
    tmp_name = file_update["tmp_name"]
    backup_name = name + ".bak"

    try:
        file_update["file"].close()
        file_update["file"] = None

        if file_update["written"] != file_update["total_size"]:
            remove_if_exists(tmp_name)
            file_update = None
            write_error(seq, bridge_radio.CMD_FILE_END, 3)
            return

        if (file_update["crc"] & 0xFFFF) != (file_update["expected_crc"] & 0xFFFF):
            remove_if_exists(tmp_name)
            file_update = None
            write_error(seq, bridge_radio.CMD_FILE_END, 6)
            return

        remove_if_exists(backup_name)
        rename_if_exists(name, backup_name)
        os.rename(tmp_name, name)
        file_update = None
        write_local(seq, bridge_radio.CMD_FILE_END)
    except Exception:
        try:
            remove_if_exists(tmp_name)
        except Exception:
            pass
        file_update = None
        write_error(seq, bridge_radio.CMD_FILE_END, 4)


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

    if frame.cmd == bridge_radio.CMD_FILE_BEGIN:
        begin_file_update(frame.seq, frame.payload)
        return

    if frame.cmd == bridge_radio.CMD_FILE_WRITE:
        write_file_update(frame.seq, frame.payload)
        return

    if frame.cmd == bridge_radio.CMD_FILE_END:
        end_file_update(frame.seq)
        return

    if frame.cmd == bridge_radio.CMD_SEND_NFCP_RELIABLE:
        if len(frame.payload) <= 6:
            write_error(frame.seq, frame.cmd, 3)
            return
        # Answered by poll_deliveries() when the delivery ends.
        bridge_mesh.send_reliable(
            NETWORK_ID, bridge_radio.int_from_be(frame.payload[0:4]), bytes(frame.payload[6:]),
            bridge_radio.int_from_be(frame.payload[4:6]), frame.seq, uart_rx_us)
        receive_and_forward_radio(POST_SEND_RX_WINDOW_MS)
        return

    if frame.cmd == bridge_radio.CMD_SEND_NFCP:
        if len(frame.payload) > bridge_radio.MAX_PAYLOAD - 2:
            write_error(frame.seq, bridge_radio.CMD_SEND_NFCP, 3)
            return

        try:
            # A mesh frame from this bridge: sent now, repeats queued.
            bridge_mesh.send_frame(NETWORK_ID, bytes(frame.payload), uart_rx_us)
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
    global uart_rx_us

    if not uart.any():
        return
    uart_rx_us = time.ticks_us()

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

    # Duplicates, relayed copies of our own frames and pre-mesh payloads
    # are dropped here, before any USB framing.
    rssi = nrf_radio.last_rssi()
    nfcp_frame = bridge_mesh.receive(payload, NETWORK_ID, rssi)
    if nfcp_frame is None:
        stat_rx_duplicate = bridge_mesh.stat_duplicate + bridge_mesh.stat_echo
        return

    # rssi(2) hops(1) frame: the third byte was always 0 before.
    rx_payload = int_to_be_signed(rssi, 2) + bytes((bridge_mesh.last_hops,)) + nfcp_frame
    write_local(0, bridge_radio.CMD_NFCP_RX, rx_payload, 0)


def poll_deliveries():
    for tag, result in bridge_mesh.poll():
        write_local(tag, bridge_radio.CMD_SEND_NFCP_RELIABLE, result)


def receive_and_forward_radio(timeout_ms):
    deadline = time.ticks_add(time.ticks_ms(), timeout_ms)
    while time.ticks_diff(deadline, time.ticks_ms()) > 0:
        bridge_mesh.service()
        poll_deliveries()
        payload = nrf_radio.recv(bridge_mesh.wait_ms(RADIO_POLL_TIMEOUT_MS))
        if payload:
            handle_radio_packet(payload)
            deadline = time.ticks_add(time.ticks_ms(), 100)
        if uart is not None and uart.any():
            return


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
    global uart, bridge_mesh

    bridge_mesh = mesh.MeshBridge(nrf_radio.send, get_node_id())
    # Bench test only: nodes this bridge pretends it can't hear directly.
    bridge_mesh.deaf_to = tuple(getattr(bridge_config, "MESH_BENCH_DEAF_TO", ()) or ())
    if bridge_mesh.deaf_to:
        print("MESH BENCH: deaf to", " ".join("%08X" % u for u in bridge_mesh.deaf_to))

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

            bridge_mesh.service()
            poll_deliveries()
            payload = nrf_radio.recv(bridge_mesh.wait_ms(RADIO_POLL_TIMEOUT_MS))
            if payload:
                handle_radio_packet(payload)
        except Exception as e:
            print("Bridge main loop error:", e)
            radio_ready = False
            time.sleep_ms(100)


main()
