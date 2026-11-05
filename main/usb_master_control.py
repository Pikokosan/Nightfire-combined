"""Opt-in USB master command policy and RF fail-stop watchdog.

No hardware imports: the same framing and watchdog run in host-side tests.
"""
import bridge_radio


def _decode(frame, kinds):
    if len(frame) < 7 or len(frame) > 218 or frame[0] != 255:
        return None
    kind = frame[1]
    if kind not in kinds:
        return None
    body = bytearray()
    escaped = False
    for value in frame[2:]:
        if escaped:
            if value != 254:
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
    return kind, payload


def decode_master(frame):
    decoded = _decode(frame, (0xFD, 0xFB))
    if decoded is None:
        return None
    kind, payload = decoded
    header = 2 if kind == 0xFD else 6
    if len(payload) < header or payload[0] != 1:
        return None
    command = payload[header - 1]
    data = payload[header:]
    if kind == 0xFD:
        valid = ((command == 0x11 and not data) or
                 (command == 0x40 and len(data) in (0, 2)) or
                 (command in (0x54, 0x75) and len(data) == 4) or
                 (command == 0x73 and len(data) == 2) or
                 (command == 0x74 and len(data) == 6))
    else:
        valid = ((command in (0x10, 0x11, 0x22, 0x27, 0x52, 0x64) and not data) or
                 (command == 0x24 and (not data or data == b'\x01')) or
                 (command == 0x20 and 1 <= len(data) <= 8 and any(data)) or
                 (command == 0x30 and len(data) == 4) or
                 (command in (0x31, 0x32, 0x62, 0x63, 0x70, 0x73) and len(data) == 2) or
                 (command == 0x33 and len(data) == 1 and
                  (data[0] == 0 or 74 <= data[0] <= 100)) or
                 (command == 0x26 and len(data) >= 3 and data[0] == 1 and
                  data[1] < 8 and 1 <= data[2] <= 8 and len(data) == 3 + data[2] * 4) or
                 (command == 0x7F and len(data) <= 16) or
                 (command == 0x60 and len(data) >= 12 and 1 <= data[10] <= 64 and
                  len(data) == 11 + data[10]) or
                 (command == 0x61 and 7 <= len(data) <= 86) or
                 (command == 0x71 and len(data) >= 4 and 1 <= data[3] <= 3 and
                  len(data) == 4 + data[3] * 12) or
                 (command == 0x72 and len(data) == 12) or
                 (command in (0x74, 0x76) and len(data) == 6))
    if not valid:
        return None
    return kind, command, bytes(data), (bridge_radio.int_from_be(payload[1:5]) if kind == 0xFB else None)


def broadcast(command, data=b''):
    body = bytes([2 + len(data), 1, command]) + data
    body += bridge_radio.int_to_be(bridge_radio.crc16_ccitt(body), 2)
    result = bytearray(b'\xff\xfd')
    for value in body:
        result.append(value)
        if value == 255:
            result.append(254)
    return bytes(result)


# After a STOP the RF keepalive is withheld for longer than the field's
# MASTER_LINK_TIMEOUT_MS (15 s default), so a module that missed the DISARM
# broadcast still self-disarms before the keepalive resumes.
KEEPALIVE_HOLD_MS = 20000


class MasterControl:
    def __init__(self, send, clock, difference):
        self.send = send
        self.clock = clock
        self.difference = difference
        self.network = bridge_radio.DEFAULT_NETWORK_ID
        self.may_be_armed = False
        self.expired = False
        self.stop_pending = 0
        self.last_stop = None
        self.targets = set()
        # True while the RF keepalive is withheld after a STOP.
        self.needs_rearm = False
        self.rearm_hold = False
        self.stopped_at = None

    def permit(self, frame, host_active, switch=None):
        decoded = decode_master(frame)
        if decoded is None:
            return False
        kind, command, data, uid = decoded
        if command in (0x11, 0x73):
            return True  # STOP/DISARM remain possible after host expiry.
        if not host_active or self.expired or self.stop_pending:
            return False
        if command in (0x10, 0x20, 0x72, 0x76):
            if switch is not None and switch != 2:
                return False
            if command != 0x10 and uid not in self.targets:
                return False
            # Set before RF send: transmission failure does not prove disarm.
            self.may_be_armed = True
            if command == 0x10 and kind == 0xFB:
                self.targets.add(uid)
                self.needs_rearm = False
        elif command in (0x26, 0x30, 0x31, 0x32, 0x33, 0x60, 0x61, 0x62,
                         0x64, 0x70, 0x71) and self.may_be_armed:
            return False
        return True

    def stop(self):
        self.needs_rearm = True
        # Only a possibly-armed field needs the self-disarm window.
        self.rearm_hold = self.may_be_armed
        self.stopped_at = self.clock()
        self.expired = True
        self.stop_pending = 3
        self.last_stop = None

    def poll(self, host_active, radio_ready, switch=None):
        if not host_active or not radio_ready or (switch is not None and switch != 2):
            if self.may_be_armed and not self.expired:
                self.stop()
        if self.stop_pending and radio_ready:
            now = self.clock()
            if self.last_stop is not None and self.difference(now, self.last_stop) < 40:
                return
            self.last_stop = now
            network = bridge_radio.int_to_be(self.network, 2)
            try:
                stopped = self.send(network + broadcast(0x73, b'\x00\x00'))
                disarmed = self.send(network + broadcast(0x11))
                if stopped and disarmed:
                    self.stop_pending -= 1
            except Exception:
                pass
        if self.expired and not self.stop_pending and host_active:
            # Fresh explicit ARM is required; this never represents field ACK.
            self.may_be_armed = False
            self.targets.clear()
            self.expired = False
        if (self.needs_rearm and not self.stop_pending and host_active and
                (not self.rearm_hold or
                 self.difference(self.clock(), self.stopped_at) >= KEEPALIVE_HOLD_MS)):
            # Resume the keepalive while the host stays connected, as the
            # original master does. ARM still requires a fresh explicit request.
            self.needs_rearm = False

    def sent(self, frame):
        # RF transmit success does not establish remote disarm.
        pass

    def observe_reply(self, frame):
        decoded = _decode(frame, (0xFA,))
        if decoded is None:
            return
        payload = decoded[1]
        if len(payload) == 7 and payload[4] == 0x50 and payload[5] == 0x11 and payload[6] == 0:
            self.targets.discard(bridge_radio.int_from_be(payload[:4]))
            self.may_be_armed = bool(self.targets)
