import bridge_radio
from nfcp_frame import (NFCP_KIND_BROADCAST, NFCP_KIND_UNIQUE, NFCP_KIND_RESPONSE,
                        NFCP_ESC, NFCP_MAX_RX_FRAME, NFCP_CMD_DISCOVER,
                        NFCP_CMD_CONTINUITY_REQ, NFCP_CMD_RESISTANCE_REQ,
                        NFCP_CMD_INFO_REQ, NFCP_CMD_SET_GROUP, NFCP_CMD_KEEPALIVE,
                        nfcp_decode, nfcp_broadcast)

# What the monitor bridge (no master mode) passes on from the app: discovery,
# and the read-only requests to one module. A group change too, while the
# app holds a session.
MONITOR_UNIQUE_QUERIES = (NFCP_CMD_CONTINUITY_REQ, NFCP_CMD_RESISTANCE_REQ, NFCP_CMD_INFO_REQ)


def decode_query(frame, allow_settings=False):
    """(kind, payload) of an NFCP frame the monitor bridge may send, or None."""
    decoded = nfcp_decode(frame, (NFCP_KIND_BROADCAST, NFCP_KIND_UNIQUE))
    if decoded is None:
        return None
    kind, payload = decoded
    if kind == NFCP_KIND_BROADCAST:
        # node_id cmd [nonce(2)]
        if len(payload) not in (2, 4) or payload[1] != NFCP_CMD_DISCOVER:
            return None
    # node_id uid(4) cmd [group(2)]
    elif not ((len(payload) == 6 and payload[5] in MONITOR_UNIQUE_QUERIES) or
              (allow_settings and len(payload) == 8 and payload[5] == NFCP_CMD_SET_GROUP)):
        return None
    return kind, bytes(payload)


def radio_reply(packet, network):
    """The NFCP response frame in a radio packet (network(2) [seq(1)] frame)
    for this network, or None. Only the header is checked: the app checks
    the rest."""
    if len(packet) < 4 or bridge_radio.int_from_be(packet[:2]) != network:
        return None
    offset = 2
    if packet[2] != NFCP_ESC and len(packet) > 4 and packet[3] == NFCP_ESC:
        offset = 3
    frame = packet[offset:]
    if (len(frame) > NFCP_MAX_RX_FRAME or len(frame) < 2 or frame[0] != NFCP_ESC or
            frame[1] != NFCP_KIND_RESPONSE):
        return None
    return frame


def keepalive_frame(ticks_ms):
    """The RF keepalive broadcast, carrying the bridge clock as the original
    master's does."""
    return nfcp_broadcast(NFCP_CMD_KEEPALIVE, bridge_radio.int_to_be(ticks_ms & 0xFFFFFFFF, 4))


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
