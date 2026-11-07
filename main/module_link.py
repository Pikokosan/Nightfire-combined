# Radio link shared by the field and dummy module firmware: NFCP framing over
# the native radio, duplicate suppression, addressing, replies, and the
# commands both firmwares handle the same way (ping, info, settings, OTA).
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

LINK_DEDUPE_WINDOW_MS = 1500
LINK_DEDUPE_LIMIT = 16


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
        self.nfcp = NFCP(node_id=0x01)
        self.parser = NFCPStreamParser()
        self.tx_seq = 1
        self.rx_seen = []
        self.last_rx_ms = 0

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

    def next_seq(self):
        seq = self.tx_seq & 0xFF
        self.tx_seq = (self.tx_seq + 1) & 0xFF
        if self.tx_seq == 0:
            self.tx_seq = 1
        return seq

    def send_payload(self, payload, use_cca=True):
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

    def tx(self, frame, repeats=3, gap_ms=20):
        network_id = self.node.ids()[0]
        payload = (network_id & 0xFFFF).to_bytes(2, "big") + bytes([self.next_seq()]) + frame
        for i in range(repeats):
            self.send_payload(payload)
            if i + 1 < repeats:
                time.sleep_ms(gap_ms)

    # --- receive -----------------------------------------------------------
    def is_duplicate(self, seq, pkt):
        if seq is None:
            return False

        now = time.ticks_ms()
        signature = (
            pkt.get("frame_type"),
            pkt.get("src"),
            pkt.get("target"),
            pkt.get("cmd"),
            bytes(pkt.get("data", b"")[:16]),
        )
        kept = []
        duplicate = False

        for item in self.rx_seen:
            if time.ticks_diff(now, item[0]) < LINK_DEDUPE_WINDOW_MS:
                kept.append(item)
                if item[1] == seq and item[2] == signature:
                    duplicate = True

        if not duplicate:
            kept.append((now, seq, signature))
            if len(kept) > LINK_DEDUPE_LIMIT:
                kept = kept[-LINK_DEDUPE_LIMIT:]

        self.rx_seen = kept
        return duplicate

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
        """Feeds one radio payload; calls handler(pkt) for each new packet."""
        if len(payload) < 3:
            return
        if int.from_bytes(payload[:2], "big") != self.node.ids()[0]:
            return

        radio_seq = None
        offset = 2
        if len(payload) >= 4 and payload[2] != 0xFF and payload[3] == 0xFF:
            radio_seq = payload[2]
            offset = 3

        for b in payload[offset:]:
            pkt = self.parser.feed(b)
            if pkt and self.matches(pkt):
                if self.is_duplicate(radio_seq, pkt):
                    return
                self.last_rx_ms = time.ticks_ms()
                handler(pkt)

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
        time.sleep_ms(backoff_min_ms + ticks_rand(span))
        ids = self.node.ids()
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
                time.sleep_ms(250)
                machine.reset()
            return True

        return False
