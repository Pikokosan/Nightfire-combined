import time

from machine import Pin, UART

import e77_radio
import machine
import nrf_radio

try:
    import nrf_bridge_config as bridge_config
except Exception:
    bridge_config = None


APP_FIRMWARE_NAME = "nrf-bridge"
APP_FIRMWARE_VERSION = "0.1.1"

DEFAULT_NETWORK_ID = e77_radio.DEFAULT_NETWORK_ID
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

local_parser = e77_radio.E77FrameParser()
uart = None


def int_to_be_signed(value, size):
    if value < 0:
        value += 1 << (size * 8)
    return e77_radio.int_to_be(value, size)


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
        uid = machine.unique_id()
        if uid:
            return int.from_bytes(uid[-4:], "big") & 0xFFFFFFFF
    except Exception:
        pass
    return 0x5284B001


def build_info_payload():
    return (
        e77_radio.int_to_be(get_node_id(), 4) +
        e77_radio.int_to_be(0, 2) +
        e77_radio.int_to_be(NETWORK_ID & 0xFFFF, 2) +
        encode_text_field(APP_FIRMWARE_VERSION)
    )


def write_local(seq, cmd, payload=b"", flags=e77_radio.FLAG_RESPONSE):
    uart.write(e77_radio.build_frame(seq, cmd, payload, flags))


def write_error(seq, cmd, code=1):
    write_local(seq, cmd, bytes([code & 0xFF]), e77_radio.FLAG_RESPONSE | e77_radio.FLAG_ERROR)


def set_network(network_id):
    global NETWORK_ID

    NETWORK_ID = network_id & 0xFFFF


def set_radio_channel(channel):
    global RADIO_CHANNEL

    if channel < 0 or channel > 100:
        return False

    RADIO_CHANNEL = channel
    nrf_radio.channel(RADIO_CHANNEL)
    return True


def build_scan_payload():
    results = []

    for channel in range(74, 101):
        sample = nrf_radio.noise_sample(channel, duration_ms=45, interval_ms=3)
        results.append((
            int(sample.get("channel", channel)),
            int(sample.get("avg", 0)),
            int(sample.get("min", 0)),
            int(sample.get("max", 0)),
        ))

    results.sort(key=lambda item: (item[1], item[3], item[0]))
    payload = bytearray()
    payload.append(min(10, len(results)))

    for channel, avg, min_rssi, max_rssi in results[:10]:
        payload.append(channel & 0xFF)
        payload += int_to_be_signed(avg, 2)
        payload += int_to_be_signed(min_rssi, 2)
        payload += int_to_be_signed(max_rssi, 2)

    nrf_radio.channel(RADIO_CHANNEL)
    return bytes(payload)


def handle_local_frame(frame):
    if frame.cmd == e77_radio.CMD_PING:
        write_local(frame.seq, e77_radio.CMD_PONG)
        return

    if frame.cmd == e77_radio.CMD_GET_INFO:
        write_local(frame.seq, e77_radio.CMD_GET_INFO, build_info_payload())
        return

    if frame.cmd == e77_radio.CMD_SET_NETWORK:
        if len(frame.payload) != 2:
            write_error(frame.seq, e77_radio.CMD_SET_NETWORK, 2)
            return
        set_network(e77_radio.int_from_be(frame.payload))
        write_local(frame.seq, e77_radio.CMD_SET_NETWORK)
        return

    if frame.cmd == e77_radio.CMD_SET_CHANNEL:
        if len(frame.payload) != 1:
            write_error(frame.seq, e77_radio.CMD_SET_CHANNEL, 2)
            return
        channel = frame.payload[0]
        if set_radio_channel(channel):
            write_local(frame.seq, e77_radio.CMD_SET_CHANNEL)
        else:
            write_error(frame.seq, e77_radio.CMD_SET_CHANNEL, 3)
        return

    if frame.cmd == e77_radio.CMD_SCAN_CHANNELS:
        try:
            write_local(frame.seq, e77_radio.CMD_SCAN_CHANNELS, build_scan_payload())
        except Exception:
            write_error(frame.seq, e77_radio.CMD_SCAN_CHANNELS, 4)
        return

    if frame.cmd == e77_radio.CMD_SEND_NFCP:
        if len(frame.payload) > e77_radio.MAX_PAYLOAD - 2:
            write_error(frame.seq, e77_radio.CMD_SEND_NFCP, 3)
            return

        radio_payload = e77_radio.int_to_be(NETWORK_ID & 0xFFFF, 2) + frame.payload
        try:
            nrf_radio.send(radio_payload)
            write_local(frame.seq, e77_radio.CMD_SEND_NFCP, b"\x00")
            receive_and_forward_radio(POST_SEND_RX_WINDOW_MS)
        except Exception:
            write_error(frame.seq, e77_radio.CMD_SEND_NFCP, 4)
        return

    if frame.cmd == e77_radio.CMD_BOOTLOADER:
        write_local(frame.seq, e77_radio.CMD_BOOTLOADER)
        time.sleep_ms(150)
        try:
            machine.bootloader()
        except Exception:
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
    if len(payload) < 3:
        return

    network_id = e77_radio.int_from_be(payload[:2])
    if network_id != NETWORK_ID:
        return

    rssi = nrf_radio.last_rssi()
    nfcp_frame = payload[2:]
    rx_payload = int_to_be_signed(rssi, 2) + int_to_be_signed(0, 1) + nfcp_frame
    write_local(0, e77_radio.CMD_NFCP_RX, rx_payload, 0)


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


def main():
    global uart

    uart = init_uart()
    nrf_radio.init(
        channel=RADIO_CHANNEL,
        tx_power=RADIO_TX_POWER,
        address=RADIO_ADDRESS,
    )

    print(
        "Nightfire nRF52840 bridge net=0x%04X ch=%d uart=%d baud=%d tx=P%d rx=P%d" %
        (NETWORK_ID, RADIO_CHANNEL, UART_ID, UART_BAUD, UART_TX_PIN, UART_RX_PIN)
    )

    while True:
        handle_local_uart()

        payload = nrf_radio.recv(RADIO_POLL_TIMEOUT_MS)
        if payload:
            handle_radio_packet(payload)


main()
