# Local UART protocol for the nRF radio bridge.

SOF0 = 0xAA
SOF1 = 0x55
VERSION = 1
MAX_PAYLOAD = 220

FLAG_RESPONSE = 0x02
FLAG_ERROR = 0x04

CMD_PING = 0x01
CMD_PONG = 0x02
CMD_GET_INFO = 0x03
CMD_SEND_NFCP = 0x10
CMD_NFCP_RX = 0x11
CMD_SET_NETWORK = 0x12
CMD_SET_CHANNEL = 0x13
CMD_SCAN_CHANNELS = 0x14
CMD_GET_STATS = 0x15
CMD_CLEAR_STATS = 0x16
CMD_GET_INTERFACE_STATUS = 0x17
CMD_HOST_HEARTBEAT = 0x18
CMD_GET_KEEPALIVE_STATUS = 0x19
# Reliable delivery to one module. Payload: uid(4) timeout_ms(2) NFCP frame.
# The bridge answers when the module's reply has arrived (it is forwarded as
# a normal CMD_NFCP_RX too) or all attempts failed: status(1, 0 delivered,
# 1 no reply) route(1, 0 direct, 1 via relay, 2 flood) relay_uid(4)
# attempts(1). A bridge without mesh answers with FLAG_ERROR.
CMD_SEND_NFCP_RELIABLE = 0x1A
# Status LED brightness. Empty payload: reply brightness(1). Payload
# brightness(1, 1-255): set it, save it in nrf_bridge_config.py (master
# firmware), reply with the new value. Setting needs an app session and no
# modules that may be armed.
CMD_LED_BRIGHTNESS = 0x1B
# Pairing window (usb-master-0.4.5+, capability 4): 1 byte seconds (1-30)
# moves the radio to the pairing network/channel and broadcasts this bridge's
# network and channel to modules in pairing mode; their replies arrive as
# normal NFCP RX frames. 0 ends it early; empty reads the seconds left.
CMD_PAIR = 0x1C
CMD_BOOTLOADER = 0x30
CMD_RESET = 0x31
CMD_FILE_BEGIN = 0x32
CMD_FILE_WRITE = 0x33
CMD_FILE_END = 0x34
CMD_ERROR = 0x40

DEFAULT_NETWORK_ID = 0x04D2
SOF_PAIR = bytes((SOF0, SOF1))


def byte_value(data, index=0):
    value = data[index]
    if isinstance(value, int):
        return value
    return ord(value)


def int_to_be(value, size):
    return bytearray((value >> (8 * shift)) & 0xFF for shift in range(size - 1, -1, -1))


def int_from_be(data, signed=False):
    value = 0
    for i in range(len(data)):
        value = (value << 8) | byte_value(data, i)

    if signed and len(data) > 0:
        sign_bit = 1 << ((len(data) * 8) - 1)
        if value & sign_bit:
            value -= 1 << (len(data) * 8)
    return value


# CRC16-CCITT (poly 0x1021) by table lookup: one step per byte instead of
# eight bit steps. Same results as the bit loop; on the nRF52840 the bit
# loop was most of the cost of handling a frame in MicroPython.
BRIDGE_CRC_TABLE = (
    0x0000, 0x1021, 0x2042, 0x3063, 0x4084, 0x50A5, 0x60C6, 0x70E7,
    0x8108, 0x9129, 0xA14A, 0xB16B, 0xC18C, 0xD1AD, 0xE1CE, 0xF1EF,
    0x1231, 0x0210, 0x3273, 0x2252, 0x52B5, 0x4294, 0x72F7, 0x62D6,
    0x9339, 0x8318, 0xB37B, 0xA35A, 0xD3BD, 0xC39C, 0xF3FF, 0xE3DE,
    0x2462, 0x3443, 0x0420, 0x1401, 0x64E6, 0x74C7, 0x44A4, 0x5485,
    0xA56A, 0xB54B, 0x8528, 0x9509, 0xE5EE, 0xF5CF, 0xC5AC, 0xD58D,
    0x3653, 0x2672, 0x1611, 0x0630, 0x76D7, 0x66F6, 0x5695, 0x46B4,
    0xB75B, 0xA77A, 0x9719, 0x8738, 0xF7DF, 0xE7FE, 0xD79D, 0xC7BC,
    0x48C4, 0x58E5, 0x6886, 0x78A7, 0x0840, 0x1861, 0x2802, 0x3823,
    0xC9CC, 0xD9ED, 0xE98E, 0xF9AF, 0x8948, 0x9969, 0xA90A, 0xB92B,
    0x5AF5, 0x4AD4, 0x7AB7, 0x6A96, 0x1A71, 0x0A50, 0x3A33, 0x2A12,
    0xDBFD, 0xCBDC, 0xFBBF, 0xEB9E, 0x9B79, 0x8B58, 0xBB3B, 0xAB1A,
    0x6CA6, 0x7C87, 0x4CE4, 0x5CC5, 0x2C22, 0x3C03, 0x0C60, 0x1C41,
    0xEDAE, 0xFD8F, 0xCDEC, 0xDDCD, 0xAD2A, 0xBD0B, 0x8D68, 0x9D49,
    0x7E97, 0x6EB6, 0x5ED5, 0x4EF4, 0x3E13, 0x2E32, 0x1E51, 0x0E70,
    0xFF9F, 0xEFBE, 0xDFDD, 0xCFFC, 0xBF1B, 0xAF3A, 0x9F59, 0x8F78,
    0x9188, 0x81A9, 0xB1CA, 0xA1EB, 0xD10C, 0xC12D, 0xF14E, 0xE16F,
    0x1080, 0x00A1, 0x30C2, 0x20E3, 0x5004, 0x4025, 0x7046, 0x6067,
    0x83B9, 0x9398, 0xA3FB, 0xB3DA, 0xC33D, 0xD31C, 0xE37F, 0xF35E,
    0x02B1, 0x1290, 0x22F3, 0x32D2, 0x4235, 0x5214, 0x6277, 0x7256,
    0xB5EA, 0xA5CB, 0x95A8, 0x8589, 0xF56E, 0xE54F, 0xD52C, 0xC50D,
    0x34E2, 0x24C3, 0x14A0, 0x0481, 0x7466, 0x6447, 0x5424, 0x4405,
    0xA7DB, 0xB7FA, 0x8799, 0x97B8, 0xE75F, 0xF77E, 0xC71D, 0xD73C,
    0x26D3, 0x36F2, 0x0691, 0x16B0, 0x6657, 0x7676, 0x4615, 0x5634,
    0xD94C, 0xC96D, 0xF90E, 0xE92F, 0x99C8, 0x89E9, 0xB98A, 0xA9AB,
    0x5844, 0x4865, 0x7806, 0x6827, 0x18C0, 0x08E1, 0x3882, 0x28A3,
    0xCB7D, 0xDB5C, 0xEB3F, 0xFB1E, 0x8BF9, 0x9BD8, 0xABBB, 0xBB9A,
    0x4A75, 0x5A54, 0x6A37, 0x7A16, 0x0AF1, 0x1AD0, 0x2AB3, 0x3A92,
    0xFD2E, 0xED0F, 0xDD6C, 0xCD4D, 0xBDAA, 0xAD8B, 0x9DE8, 0x8DC9,
    0x7C26, 0x6C07, 0x5C64, 0x4C45, 0x3CA2, 0x2C83, 0x1CE0, 0x0CC1,
    0xEF1F, 0xFF3E, 0xCF5D, 0xDF7C, 0xAF9B, 0xBFBA, 0x8FD9, 0x9FF8,
    0x6E17, 0x7E36, 0x4E55, 0x5E74, 0x2E93, 0x3EB2, 0x0ED1, 0x1EF0,
)


def crc16_ccitt(data, crc=0xFFFF):
    if isinstance(data, str):
        data = data.encode()
    table = BRIDGE_CRC_TABLE
    for b in data:
        crc = ((crc << 8) & 0xFF00) ^ table[((crc >> 8) ^ b) & 0xFF]
    return crc


def build_frame(seq, cmd, payload=b"", flags=0):
    if payload is None:
        payload = b""
    if len(payload) > MAX_PAYLOAD:
        raise ValueError("radio bridge payload too large")

    body = bytearray([4 + len(payload), VERSION, flags & 0xFF, seq & 0xFF, cmd & 0xFF])
    body += bytearray(payload)
    crc = crc16_ccitt(body)
    return bytearray([SOF0, SOF1]) + body + int_to_be(crc, 2)


def build_send_nfcp(seq, nfcp_frame):
    return build_frame(seq, CMD_SEND_NFCP, nfcp_frame)


def build_set_network(seq, network_id):
    return build_frame(seq, CMD_SET_NETWORK, int_to_be(network_id & 0xFFFF, 2))


def build_set_channel(seq, channel):
    return build_frame(seq, CMD_SET_CHANNEL, bytes([channel & 0xFF]))


def build_get_info(seq):
    return build_frame(seq, CMD_GET_INFO)


def parse_get_info(payload):
    if len(payload) < 8:
        return None

    info = {
        "node_id": int_from_be(payload[0:4]),
        "group_id": int_from_be(payload[4:6]),
        "network_id": int_from_be(payload[6:8]),
        "firmware_version": "",
    }

    if len(payload) > 8:
        version_len = byte_value(payload, 8)
        if version_len > 0 and len(payload) >= 9 + version_len:
            try:
                info["firmware_version"] = bytes(payload[9:9 + version_len]).decode()
            except:
                info["firmware_version"] = ""

    return info


def parse_nfcp_rx(payload):
    if len(payload) < 3:
        return None

    return {
        "rssi": int_from_be(payload[0:2], signed=True),
        "snr": int_from_be(payload[2:3], signed=True),
        "nfcp_frame": payload[3:],
    }


def parse_stats_payload(payload):
    if len(payload) < 34:
        return None

    stats = {
        "tx_ok": int_from_be(payload[0:4]),
        "tx_timeout": int_from_be(payload[4:8]),
        "rx_ok": int_from_be(payload[8:12]),
        "rx_timeout": int_from_be(payload[12:16]),
        "rx_crc_error": int_from_be(payload[16:20]),
        "rx_bad_len": int_from_be(payload[20:24]),
        "channel": byte_value(payload, 24),
        "tx_power": int_from_be(payload[25:26], signed=True),
        "last_rssi": int_from_be(payload[26:28], signed=True),
        "max_payload": int_from_be(payload[28:30]),
        "address": int_from_be(payload[30:34]),
    }

    if len(payload) >= 46:
        stats["tx_busy"] = int_from_be(payload[34:38])
        stats["cca_ok"] = int_from_be(payload[38:42])
        stats["cca_busy"] = int_from_be(payload[42:46])

    if len(payload) >= 54:
        stats["rx_duplicate"] = int_from_be(payload[46:50])
        stats["rx_wrong_network"] = int_from_be(payload[50:54])

    return stats


class Frame(object):
    def __init__(self, flags, seq, cmd, payload):
        self.flags = flags
        self.seq = seq
        self.cmd = cmd
        self.payload = payload


class BridgeFrameParser(object):
    def __init__(self):
        self.reset()
        self.bulk = bytearray()   # feed_bytes() buffer

    def feed_bytes(self, data):
        """Frames found in data (and any partial frame left from before).

        Same frames as feeding every byte to feed(), with a few calls per
        frame instead of several per byte: on MicroPython that per-byte cost
        added milliseconds to every command the bridge forwards. Use either
        feed() or feed_bytes() on one parser, not both."""
        buf = self.bulk
        buf += data
        frames = []
        start = 0
        end = len(buf)
        while True:
            index = buf.find(SOF_PAIR, start)
            if index < 0:
                # Keep a trailing SOF0: it may begin the next frame.
                start = end - 1 if end and buf[end - 1] == SOF0 else end
                break
            if index + 3 > end:
                start = index
                break
            length = buf[index + 2]
            if length < 4 or length > MAX_PAYLOAD + 4:
                start = index + 1
                continue
            frame_end = index + 3 + length + 2
            if frame_end > end:
                start = index
                break
            body = buf[index + 2:index + 3 + length]
            rx_crc = (buf[frame_end - 2] << 8) | buf[frame_end - 1]
            if rx_crc == crc16_ccitt(body) and body[1] == VERSION:
                frames.append(Frame(flags=body[2], seq=body[3], cmd=body[4], payload=body[5:]))
                start = frame_end
            else:
                start = index + 1
        if start:
            self.bulk = buf[start:]
        return frames

    def reset(self):
        self.state = 0
        self.length = 0
        self.buf = bytearray()
        self.crc_hi = 0

    def feed(self, b):
        if not isinstance(b, int):
            b = ord(b)

        if self.state == 0:
            if b == SOF0:
                self.state = 1
            return None

        if self.state == 1:
            if b == SOF1:
                self.state = 2
            elif b != SOF0:
                self.state = 0
            return None

        if self.state == 2:
            self.length = b
            if self.length < 4 or self.length > MAX_PAYLOAD + 4:
                self.reset()
                return None
            self.buf = bytearray([b])
            self.state = 3
            return None

        if self.state == 3:
            self.buf.append(b)
            if len(self.buf) >= 1 + self.length:
                self.state = 4
            return None

        if self.state == 4:
            self.crc_hi = b
            self.state = 5
            return None

        if self.state == 5:
            rx_crc = (self.crc_hi << 8) | b
            calc_crc = crc16_ccitt(self.buf)
            if rx_crc == calc_crc and byte_value(self.buf, 1) == VERSION:
                frame = Frame(
                    flags=byte_value(self.buf, 2),
                    seq=byte_value(self.buf, 3),
                    cmd=byte_value(self.buf, 4),
                    payload=self.buf[5:],
                )
                self.reset()
                return frame
            self.reset()
            return None

        self.reset()
        return None
