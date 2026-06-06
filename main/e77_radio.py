# E77 local UART protocol for NFCP-over-radio.

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
CMD_BOOTLOADER = 0x30
CMD_ERROR = 0x40

DEFAULT_NETWORK_ID = 0x04D2


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


def crc16_ccitt(data):
    crc = 0xFFFF
    for b in data:
        if not isinstance(b, int):
            b = ord(b)
        crc ^= b << 8
        for _ in range(8):
            if crc & 0x8000:
                crc = ((crc << 1) ^ 0x1021) & 0xFFFF
            else:
                crc = (crc << 1) & 0xFFFF
    return crc


def build_frame(seq, cmd, payload=b"", flags=0):
    if payload is None:
        payload = b""
    if len(payload) > MAX_PAYLOAD:
        raise ValueError("E77 payload too large")

    body = bytearray([4 + len(payload), VERSION, flags & 0xFF, seq & 0xFF, cmd & 0xFF])
    body += bytearray(payload)
    crc = crc16_ccitt(body)
    return bytearray([SOF0, SOF1]) + body + int_to_be(crc, 2)


def build_send_nfcp(seq, nfcp_frame):
    return build_frame(seq, CMD_SEND_NFCP, nfcp_frame)


def build_set_network(seq, network_id):
    return build_frame(seq, CMD_SET_NETWORK, int_to_be(network_id & 0xFFFF, 2))


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


class Frame(object):
    def __init__(self, flags, seq, cmd, payload):
        self.flags = flags
        self.seq = seq
        self.cmd = cmd
        self.payload = payload


class E77FrameParser(object):
    def __init__(self):
        self.reset()

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
