import bridge_radio


def decode_query(frame, allow_settings=False):
    if len(frame) < 7 or len(frame) > 218 or frame[0] != 255:
        return None
    kind = frame[1]
    if kind not in (0xFD, 0xFB):
        return None
    body = bytearray()
    escaped = False
    for value in frame[2:]:
        if escaped:
            if value != 0xFE:
                return None
            body.append(255)
            escaped = False
        elif value == 255:
            escaped = True
        else:
            body.append(value)
    if escaped or len(body) < 5 or len(body) != body[0] + 3:
        return None
    if bridge_radio.crc16_ccitt(body[:-2]) != bridge_radio.int_from_be(body[-2:]):
        return None
    payload = body[1:-2]
    if kind == 0xFD:
        if len(payload) not in (2, 4) or payload[1] != 0x40:
            return None
    elif not ((len(payload) == 6 and payload[5] in (0x22, 0x24, 0x52)) or
              (allow_settings and len(payload) == 8 and payload[5] == 0x31)):
        return None
    return kind, bytes(payload)


def radio_reply(packet, network):
    if len(packet) < 4 or bridge_radio.int_from_be(packet[:2]) != network:
        return None
    offset = 2
    if packet[2] != 255 and len(packet) > 4 and packet[3] == 255:
        offset = 3
    frame = packet[offset:]
    if len(frame) > 217 or len(frame) < 2 or frame[0] != 255 or frame[1] != 0xFA:
        return None
    return frame


def keepalive_frame(ticks_ms):
    # Same broadcast node ID, command and clock payload as the original master.
    body = bytes([6, 1, 0x54]) + bridge_radio.int_to_be(ticks_ms & 0xFFFFFFFF, 4)
    body += bridge_radio.int_to_be(bridge_radio.crc16_ccitt(body), 2)
    encoded = bytearray(b"\xff\xfd")
    for value in body:
        encoded.append(value)
        if value == 255:
            encoded.append(0xFE)
    return bytes(encoded)


class MasterKeepalive:
    def __init__(self, network, send, clock, difference):
        self.network = network
        self.send = send
        self.clock = clock
        self.difference = difference
        self.interval = 2500
        self.active = False
        self.failed = False
        self.last_attempt = None
        self.last_success = None
        self.sent = 0
        self.failures = 0

    def poll(self, host_active, radio_ready):
        self.active = bool(host_active and radio_ready)
        if not self.active:
            self.last_attempt = None
            self.failed = False
            return
        now = self.clock()
        if self.last_attempt is not None and self.difference(now, self.last_attempt) < self.interval:
            return
        self.last_attempt = now
        # No bridge sequence byte: the field supports this original radio envelope.
        packet = bridge_radio.int_to_be(self.network, 2) + keepalive_frame(now)
        try:
            success = bool(self.send(packet))
        except Exception:
            success = False
        self.failed = not success
        if success:
            self.sent = min(0xFFFFFFFF, self.sent + 1)
            self.last_success = now
        else:
            self.failures = min(0xFFFFFFFF, self.failures + 1)

    def payload(self):
        state = (2 if self.failed else 1) if self.active else 0
        age = 65535 if self.last_success is None else min(65535, max(0, self.difference(self.clock(), self.last_success)))
        return (bytes([1, state]) + bridge_radio.int_to_be(self.interval, 2) +
                bridge_radio.int_to_be(age, 2) + bridge_radio.int_to_be(self.sent, 4) +
                bridge_radio.int_to_be(self.failures, 4))
