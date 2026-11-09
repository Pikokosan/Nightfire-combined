# Radio link shared by the field and dummy module firmware: NFCP frames
# carried in mesh frames (mesh.py) over the native radio, duplicate
# suppression, relaying, addressing, replies, and the commands both firmwares
# handle the same way (ping, info, settings, OTA).
#
# Sends never block: repeats and relays wait in a queue that the main loop
# empties with service(), and rx_timeout_ms() keeps recv() short enough to
# send them on time. A node that sleeps can't hear anything.
#
# The firmware passes a "node" object that owns its state. ModuleLink calls:
#   node.ids()                      -> (network_id, group_id, unique_id, channel)
#   node.armed()                    -> bool
#   node.info_payload()             -> bytes for CMD_INFO_RSP
#   node.apply_setting(name, value) -> bool; name is "group", "network",
#                                      "channel" or "unique"; must save it
#
# Keep every top-level name here unique: the field image bundles this file
# into one namespace with field_main.py.

import time

import machine
import module_ota
import nrf_module_common
import nrf_radio
from nfcp import *
from mesh import *


# The radio hardware checks a 16-bit CRC over the whole payload, so the NFCP
# CRC inside it is normally not checked again in Python. That bit-by-bit loop
# was 50-65% of the receive cost on the module.
LINK_CHECK_NFCP_CRC = False

LINK_SOF_TYPES = {
    SOF2_BROADCAST: SOF_BROADCAST,
    SOF2_GROUP: SOF_GROUP,
    SOF2_UNIQUE: SOF_UNIQUE,
    SOF2_RESPONSE: SOF_RESPONSE,
}

_link_nfcp_decoder = NFCPStreamParser()


def link_decode_nfcp(payload, offset):
    """Decode the NFCP frame at payload[offset:] in a few calls, instead of
    feeding the stream parser byte by byte."""
    if len(payload) < offset + 5 or payload[offset] != ESC:
        return None
    frame_type = LINK_SOF_TYPES.get(payload[offset + 1])
    if frame_type is None:
        return None
    body = payload[offset + 2:]
    if b"\xff" in body:
        body = body.replace(b"\xff\xfe", b"\xff")
    length = body[0]
    if len(body) < length + 3:
        return None
    if LINK_CHECK_NFCP_CRC:
        crc = (body[length + 1] << 8) | body[length + 2]
        if crc16_ccitt(body[:length + 1]) != crc:
            return None
    _link_nfcp_decoder.frame_type = frame_type
    return _link_nfcp_decoder._decode(body[1:length + 1])


def ticks_rand(limit):
    if limit <= 0:
        return 0
    return (time.ticks_us() ^ time.ticks_ms()) % limit


def encode_text_field(text):
    try:
        data = text.encode()
    except Exception:
        data = b""
    if len(data) > 63:
        data = data[:63]
    return bytes([len(data)]) + data


def count_mask_bits(mask):
    mask = int(mask or 0)
    count = 0
    while mask:
        count += mask & 1
        mask >>= 1
    return count


class ModuleLink:
    def __init__(
        self,
        node,
        address,
        tx_power,
        cca_enabled=True,
        cca_ms=2,
        cca_busy_dbm=-85,
        cca_retries=4,
        cca_backoff_min_ms=2,
        cca_backoff_max_ms=10,
        max_hops=MESH_DEFAULT_MAX_HOPS,
        relay_enabled=True,
    ):
        self.node = node
        self.address = address
        self.tx_power = tx_power
        self.cca_enabled = cca_enabled
        self.cca_ms = cca_ms
        self.cca_busy_dbm = cca_busy_dbm
        self.cca_retries = cca_retries
        self.cca_backoff_min_ms = cca_backoff_min_ms
        self.cca_backoff_max_ms = cca_backoff_max_ms
        self.max_hops = max(1, min(MESH_HOPS_LIMIT, int(max_hops)))
        self.relay_enabled = relay_enabled
        self.deaf_to = ()        # bench test: see mesh_deaf()
        self.stat_deaf = 0
        self.nfcp = NFCP(node_id=0x01)
        self.queue = MeshQueue()
        self.seen = MeshSeen()
        # Relays waiting to go out: key -> [due_ms, payload, relayed_copies_heard, stamp, suppressible]
        self.relays = {}
        self.stat_suppressed = 0
        # The unicast request being handled: (key, msg_id, mode, relay, uid).
        # Its first reply reuses the request's msg_id and route, and is kept
        # in self.replies (key -> [frame, sent_ms, msg_id]) for retries.
        self.request = None
        self.replies = {}
        self.stat_resent = 0
        self.attempts = {}       # key -> when its latest attempt was heard
        self.msg_id = (time.ticks_us() ^ (node.ids()[2] & 0xFFFF)) & 0xFFFF or 1
        self.last_rx_ms = 0
        self.stat_relayed = 0
        self.stat_duplicate = 0

    def note_attempt(self, key, now):
        attempts = self.attempts
        attempts[key] = now
        if len(attempts) > MESH_DEDUPE_LIMIT:
            for old in list(attempts):
                if time.ticks_diff(now, attempts[old]) >= MESH_DEDUPE_WINDOW_MS:
                    del attempts[old]
            while len(attempts) > MESH_DEDUPE_LIMIT:
                oldest = None
                for old in attempts:
                    if oldest is None or time.ticks_diff(attempts[oldest], attempts[old]) > 0:
                        oldest = old
                del attempts[oldest]

    # --- radio -------------------------------------------------------------
    def init_radio(self):
        try:
            nrf_radio.init(
                channel=self.node.ids()[3],
                tx_power=self.tx_power,
                address=self.address,
            )
            return True
        except Exception as e:
            print("Radio init failed:", e)
            return False

    def next_msg_id(self):
        self.msg_id = (self.msg_id + 1) & 0xFFFF
        if not self.msg_id:
            self.msg_id = 1
        return self.msg_id

    def try_send(self, payload):
        """One send attempt that never sleeps: False if clear-channel
        assessment found the channel busy."""
        if not self.cca_enabled:
            return bool(nrf_radio.send(payload))
        return bool(nrf_radio.send(payload, cca_ms=self.cca_ms, busy_dbm=self.cca_busy_dbm, backoff_ms=0))

    def send_or_queue(self, payload, tries=0, stamp=None):
        """Send now if the channel is clear; otherwise queue a retry after a
        random backoff (up to cca_retries), so the main loop never sleeps.
        A timed frame's age is set from stamp at the moment it is sent."""
        if self.try_send(mesh_stamp(payload, stamp)):
            return True
        if tries < self.cca_retries:
            span = self.cca_backoff_max_ms - self.cca_backoff_min_ms + 1
            self.queue.push(payload, self.cca_backoff_min_ms + ticks_rand(span), tries + 1, stamp)
        return False

    def send_payload(self, payload, use_cca=True):
        """Blocking send with clear-channel retries; only for flush()."""
        return nrf_module_common.send_with_cca(
            payload,
            enabled=use_cca and self.cca_enabled,
            cca_ms=self.cca_ms,
            busy_dbm=self.cca_busy_dbm,
            retries=self.cca_retries,
            backoff_min_ms=self.cca_backoff_min_ms,
            backoff_max_ms=self.cca_backoff_max_ms,
            rand_fn=ticks_rand,
        )

    def tx(self, frame, repeats=3, gap_ms=20, delay_ms=0):
        """Send an NFCP frame. The first copy goes out now (unless delayed);
        the repeats are queued.

        The first reply to a unicast request goes back the way the request
        came (direct, via its relay, or flooded) with the request's msg_id,
        so the bridge can match it, and is cached for retries. Anything else
        is flooded with a new msg_id."""
        ids = self.node.ids()
        request = self.request
        if request is not None:
            self.request = None          # later frames are not "the reply"
            key, msg_id, mode, relay, uid = request
            self.remember_reply(key, frame, msg_id)
            payload = mesh_build(ids[0], uid, msg_id, frame, mode=mode, relay=relay)
        else:
            payload = mesh_build(ids[0], ids[2], self.next_msg_id(), frame)
        self.send_repeated(payload, repeats, gap_ms, delay_ms)

    def remember_reply(self, key, frame, msg_id):
        now = time.ticks_ms()
        replies = self.replies
        for old in list(replies):
            if time.ticks_diff(now, replies[old][1]) >= MESH_REPLY_KEEP_MS:
                del replies[old]
        while len(replies) >= MESH_REPLY_CACHE:
            oldest = None
            for old in replies:
                if oldest is None or time.ticks_diff(replies[oldest][1], replies[old][1]) > 0:
                    oldest = old
            del replies[oldest]
        replies[key] = [frame, now, msg_id]

    def send_repeated(self, payload, repeats=3, gap_ms=20, delay_ms=0):
        start = 0
        if delay_ms <= 0:
            self.send_or_queue(payload)
            start = 1
        # Each gap is gap_ms +-25%. With fixed gaps, two nodes whose first
        # copies collide would collide on every repeat too.
        jitter = gap_ms // 2
        due = delay_ms
        for i in range(start, repeats):
            if i:
                due += gap_ms - jitter // 2 + ticks_rand(jitter + 1)
            self.queue.push(payload, due)

    def service(self):
        """Send queued repeats and relays that are due. Call every loop."""
        if self.relays:
            self.service_relays(time.ticks_ms())
        if not self.queue.items:
            return
        now = time.ticks_ms()
        while True:
            item = self.queue.pop_due(now)
            if item is None:
                return
            self.send_or_queue(item[1], item[2], item[3])

    def service_relays(self, now):
        """Forward relays whose wait is over, unless enough other relays
        were heard forwarding the same frame meanwhile (suppression)."""
        for key in list(self.relays):
            item = self.relays[key]
            if time.ticks_diff(now, item[0]) < 0:
                continue
            del self.relays[key]
            if item[4] and item[2] >= MESH_SUPPRESS_COUNT:
                self.stat_suppressed += 1
                continue
            self.stat_relayed += 1
            self.send_or_queue(item[1], 0, item[3])
            for copy in range(1, MESH_RELAY_COPIES):
                self.queue.push(item[1], copy * MESH_RELAY_GAP_MS, 0, item[3])

    def flush(self):
        """Send everything queued now, e.g. before a channel change or reset."""
        items = self.queue.items
        self.queue.clear()
        self.relays = {}
        for index, item in enumerate(items):
            if index:
                time.sleep_ms(5)
            self.send_payload(mesh_stamp(item[1], item[3]))

    def rx_timeout_ms(self, default_ms):
        if not self.queue.items and not self.relays:
            return default_ms
        now = time.ticks_ms()
        wait = self.queue.wait_ms(now, default_ms)
        for item in self.relays.values():
            left = time.ticks_diff(item[0], now)
            if left < wait:
                wait = left
        return wait if wait > 1 else 1

    # --- receive -----------------------------------------------------------
    def matches(self, pkt):
        frame_type = pkt.get("frame_type")
        if frame_type == SOF_BROADCAST:
            return True
        ids = self.node.ids()
        if frame_type == SOF_GROUP:
            return pkt.get("target") == ids[1]
        if frame_type == SOF_UNIQUE:
            return pkt.get("target") == ids[2]
        return False

    def is_for_me(self, pkt):
        return pkt.get("frame_type") == SOF_UNIQUE and pkt.get("target") == self.node.ids()[2]

    def receive(self, payload, handler):
        """Handles one radio payload: drops what isn't new, queues a relay
        copy, then calls handler(pkt) if the packet is addressed to us.

        The cheap checks come first, so a frame we've already heard costs a
        handful of calls instead of a full NFCP parse."""
        rx_us = time.ticks_us()     # arrival anchor for time code
        if len(payload) < MESH_HEADER_LEN + 2:
            return
        ids = self.node.ids()
        network_id = ids[0]
        if payload[0] != ((network_id >> 8) & 0xFF) or payload[1] != (network_id & 0xFF):
            return
        parsed = mesh_parse(payload)
        if parsed is None:
            return
        if mesh_deaf(self.deaf_to, parsed):
            self.stat_deaf += 1
            return
        ctl = parsed[0]
        origin = parsed[1]
        uid = ids[2]
        if origin == uid:
            return      # our own frame, relayed back
        now = time.ticks_ms()
        key = (origin << 16) | parsed[2]
        mode = (ctl >> 4) & 3
        duplicate = self.seen.check(key, now)
        cached = self.replies.get(key)
        new_attempt = False
        # A request still in the reply cache is a retry even after the
        # duplicate window (2 s) has forgotten it: never run it twice.
        if duplicate or cached is not None:
            self.stat_duplicate += 1
            if duplicate and ctl & 3:
                # Another node relayed a frame we are about to relay too.
                pending = self.relays.get(key)
                if pending is not None:
                    pending[2] += 1
            if cached is not None:
                if time.ticks_diff(now, cached[1]) >= MESH_REPLY_RESEND_MS:
                    # A retry of a request we already answered (copies of one
                    # attempt arrive faster than this): send the same reply
                    # again, the way this copy came, and don't run it again.
                    cached[1] = now
                    self.stat_resent += 1
                    relay = parsed[3] if mode == MESH_MODE_VIA else None
                    self.send_repeated(mesh_build(network_id, uid, cached[2], cached[0], mode=mode, relay=relay))
                return
            # A new attempt by the sender (a copy straight from it, well after
            # the last one) may take a different route, so relays decide
            # again whether to forward it. Nobody runs it twice: the target
            # answers retries from its reply cache above.
            last = self.attempts.get(key)
            if (ctl & 3) != 0 or last is None or time.ticks_diff(now, last) < MESH_REPLY_RESEND_MS:
                return
            new_attempt = True
        self.note_attempt(key, now)

        pkt = link_decode_nfcp(payload, parsed[5])
        if pkt is None:
            return
        age = parsed[4]
        pkt["mesh_origin"] = origin
        pkt["mesh_hops"] = ctl & 3
        pkt["rx_us"] = rx_us
        if age is not None:
            pkt["mesh_age_us"] = age * 100

        # Relaying, up to max_hops transmissions in total; a unicast frame for
        # us goes no further.
        # - Flooded frames: every module may relay once. The wait depends on
        #   how strongly we heard it (weak = go first), and the relay is
        #   dropped if others relay it first (service_relays).
        # - VIA frames: only the relay the sender named forwards it.
        # - DIRECT frames: nobody relays.
        hops = ctl & 3
        for_me = pkt.get("frame_type") == SOF_UNIQUE and pkt.get("target") == uid
        if self.relay_enabled and hops + 1 < self.max_hops and not for_me:
            wait = None
            suppressible = False
            if mode == MESH_MODE_FLOOD:
                wait = mesh_relay_wait_ms(nrf_radio.last_rssi(), ticks_rand)
                # Replies heading back to the bridge are never suppressed:
                # the nodes that relay first (heard the reply weakly) are not
                # necessarily ones the bridge can hear.
                suppressible = MESH_SUPPRESS_RESPONSES or pkt.get("frame_type") != SOF_RESPONSE
            elif mode == MESH_MODE_VIA and parsed[3] == uid and hops == 0:
                wait = MESH_RELAY_WAIT_MIN_MS
            if wait is not None:
                copy = mesh_relay_copy(payload, parsed, uid)
                stamp = None
                if age is not None:
                    cca = self.cca_ms if self.cca_enabled else 0
                    stamp = (rx_us, age, mesh_transit_us(len(copy), cca))
                self.relays[key] = [time.ticks_add(now, wait), copy, 0, stamp, suppressible]

        # Act on it before any relay goes out (relays wait in self.relays).
        if self.matches(pkt) and not new_attempt:
            self.last_rx_ms = now
            if for_me:
                self.request = (key, parsed[2], mode,
                                parsed[3] if mode == MESH_MODE_VIA else None, uid)
            try:
                handler(pkt)
            finally:
                self.request = None

    def master_link_ok(self, timeout_ms):
        if not self.last_rx_ms:
            return False
        return time.ticks_diff(time.ticks_ms(), self.last_rx_ms) <= timeout_ms

    def master_link_lost(self, timeout_ms):
        """True once a master was heard and has then been silent too long."""
        if not self.last_rx_ms:
            return False
        return time.ticks_diff(time.ticks_ms(), self.last_rx_ms) > timeout_ms

    # --- replies -----------------------------------------------------------
    def ack(self, pkt, orig_cmd, code=ACK_OK):
        if pkt.get("frame_type") == SOF_UNIQUE:
            self.tx(self.nfcp.build_ack(self.node.ids()[2], orig_cmd, code))

    def nack(self, pkt, orig_cmd, code):
        if pkt.get("frame_type") == SOF_UNIQUE:
            self.tx(self.nfcp.build_nack(self.node.ids()[2], orig_cmd, code))

    def respond(self, cmd, data=b""):
        self.tx(self.nfcp.build_response(self.node.ids()[2], cmd, data))

    def discover_reply(self, pkt, cue_count, device_type, backoff_min_ms, backoff_max_ms, repeats, gap_ms):
        data = pkt.get("data", b"")
        nonce = int.from_bytes(data[:2], "big") if len(data) >= 2 else None
        span = backoff_max_ms - backoff_min_ms + 1
        ids = self.node.ids()
        # The backoff is a queued delay, not a sleep, so we keep listening.
        self.tx(
            self.nfcp.build_discover_response(
                src_unique=ids[2],
                group_id=ids[1],
                unique_id=ids[2],
                cue_count=cue_count,
                device_type=device_type,
                proto_ver=0x20,
                flags=0x00,
                nonce=nonce,
            ),
            repeats,
            gap_ms,
            delay_ms=backoff_min_ms + ticks_rand(span),
        )

    # --- commands both firmwares handle the same way -----------------------
    def handle_common(self, pkt):
        """Handles ping, info, keepalive, settings and OTA. Returns True if handled."""
        cmd = pkt.get("cmd")
        data = pkt.get("data", b"")
        node = self.node

        if cmd == CMD_PING:
            if self.is_for_me(pkt):
                self.respond(CMD_PING, data or bytes([PING_REPLY_BYTE]))
            return True

        if cmd == CMD_INFO_REQ:
            if self.is_for_me(pkt):
                self.respond(CMD_INFO_RSP, node.info_payload())
            return True

        if cmd == CMD_KEEPALIVE:
            return True

        if cmd == CMD_SET_GROUP or cmd == CMD_SET_NETWORK:
            if node.armed():
                self.nack(pkt, cmd, NACK_REJECTED_ARMED)
            elif len(data) != 2:
                self.nack(pkt, cmd, NACK_BAD_LENGTH)
            elif node.apply_setting(
                "group" if cmd == CMD_SET_GROUP else "network",
                int.from_bytes(data, "big") & 0xFFFF,
            ):
                self.ack(pkt, cmd)
            else:
                self.nack(pkt, cmd, NACK_INTERNAL_ERROR)
            return True

        if cmd == CMD_SET_RADIO_CHANNEL:
            if node.armed():
                self.nack(pkt, cmd, NACK_REJECTED_ARMED)
            elif len(data) != 1:
                self.nack(pkt, cmd, NACK_BAD_LENGTH)
            elif not nrf_module_common.valid_radio_channel(data[0]):
                self.nack(pkt, cmd, NACK_INVALID_VALUE)
            elif node.apply_setting("channel", data[0]):
                self.ack(pkt, cmd)
                self.flush()
                time.sleep_ms(80)
                nrf_radio.init(
                    channel=node.ids()[3],
                    tx_power=self.tx_power,
                    address=self.address,
                )
            else:
                self.nack(pkt, cmd, NACK_INTERNAL_ERROR)
            return True

        if cmd == CMD_SET_UNIQUE:
            # Replies come from the old address, which the master is waiting on.
            old_unique = node.ids()[2]
            unique = pkt.get("frame_type") == SOF_UNIQUE
            if node.armed():
                if unique:
                    self.tx(self.nfcp.build_nack(old_unique, cmd, NACK_REJECTED_ARMED))
            elif len(data) != 4:
                if unique:
                    self.tx(self.nfcp.build_nack(old_unique, cmd, NACK_BAD_LENGTH))
            elif node.apply_setting("unique", int.from_bytes(data, "big") & 0xFFFFFFFF):
                if unique:
                    self.tx(self.nfcp.build_ack(old_unique, cmd, ACK_OK))
                print("NEW UNIQUE_ID = 0x%08X" % node.ids()[2])
            elif unique:
                self.tx(self.nfcp.build_nack(old_unique, cmd, NACK_INTERNAL_ERROR))
            return True

        if cmd == CMD_OTA_UPDATE_BEGIN:
            module_ota.begin(pkt, node.ids()[2], node.armed(), self.ack, self.nack)
            return True

        if cmd == CMD_OTA_UPDATE_WRITE:
            module_ota.write(pkt, node.ids()[2], self.ack, self.nack)
            return True

        if cmd == CMD_OTA_UPDATE_END:
            module_ota.end(pkt, node.ids()[2], self.ack, self.nack)
            return True

        if cmd == CMD_OTA_UPDATE_ABORT:
            module_ota.abort(pkt, node.ids()[2], self.ack, self.nack)
            return True

        if cmd == CMD_OTA_UPDATE_REBOOT:
            if module_ota.reboot(pkt, node.ids()[2], node.armed(), self.ack, self.nack):
                self.flush()
                time.sleep_ms(250)
                machine.reset()
            return True

        return False
