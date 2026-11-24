"""Opt-in USB master command policy and RF fail-stop watchdog.

No hardware imports: the same framing and watchdog run in host-side tests.
"""
import bridge_radio
from nfcp_frame import *


def _master_broadcast_valid(command, data):
    """Broadcasts the app may send through the master bridge."""
    return ((command == NFCP_CMD_DISARM and not data) or
            (command == NFCP_CMD_DISCOVER and len(data) in (0, 2)) or
            (command in (NFCP_CMD_KEEPALIVE, NFCP_CMD_TIME_SYNC) and len(data) == 4) or
            (command == NFCP_CMD_SCRIPT_STOP and len(data) == 2) or
            (command == NFCP_CMD_SCRIPT_HEARTBEAT and len(data) == 6))


def _master_unique_valid(command, data):
    """Commands to one module the app may send, with their data lengths."""
    if command in (NFCP_CMD_ARM, NFCP_CMD_DISARM, NFCP_CMD_CONTINUITY_REQ,
                   NFCP_CMD_OUTPUT_STATUS_REQ, NFCP_CMD_INFO_REQ, NFCP_CMD_OTA_UPDATE_REBOOT):
        return not data
    if command == NFCP_CMD_RESISTANCE_REQ:
        return not data or data == b'\x01'          # calibrated, or raw ADC
    if command == NFCP_CMD_FIRE_PULSE:
        return len(data) in (0, 2)                  # read, or set (ms)
    if command == NFCP_CMD_FIRE:
        return 1 <= len(data) <= 8 and any(data)    # cue mask
    if command == NFCP_CMD_SET_UNIQUE:
        return len(data) == 4
    if command in (NFCP_CMD_SET_GROUP, NFCP_CMD_SET_NETWORK, NFCP_CMD_OTA_UPDATE_END,
                   NFCP_CMD_OTA_UPDATE_ABORT, NFCP_CMD_SCRIPT_CLEAR, NFCP_CMD_SCRIPT_STOP):
        return len(data) == 2
    if command == NFCP_CMD_SET_RADIO_CHANNEL:
        return len(data) == 1 and nfcp_valid_channel(data[0])
    if command == NFCP_CMD_SET_RESISTANCE_CAL:
        # version 1, bank < 8, 1-8 points of adc_mv(2) deci_ohms(2)
        return (len(data) >= 3 and data[0] == 1 and data[1] < 8 and 1 <= data[2] <= 8 and
                len(data) == 3 + data[2] * 4)
    if command == NFCP_CMD_PING:
        return len(data) <= 16
    if command == NFCP_CMD_OTA_UPDATE_BEGIN:
        # transfer_id(2) size(4) checksum(4) path_len(1, 1-64) path
        return len(data) >= 12 and 1 <= data[10] <= 64 and len(data) == 11 + data[10]
    if command == NFCP_CMD_OTA_UPDATE_WRITE:
        return 7 <= len(data) <= 86
    if command == NFCP_CMD_SCRIPT_LOAD:
        # script_id(2) chunk(1) count(1, 1-3) entries
        return (len(data) >= 4 and 1 <= data[3] <= 3 and
                len(data) == 4 + data[3] * NFCP_SCRIPT_ENTRY_BYTES)
    if command == NFCP_CMD_SCRIPT_START:
        # timed, or chase: follow an audio box (audio_box/design.md)
        return len(data) in (NFCP_SCRIPT_START_BYTES, NFCP_SCRIPT_START_CHASE_BYTES)
    if command in (NFCP_CMD_AUDIO_LOAD, NFCP_CMD_AUDIO_STOP):
        return len(data) == 2                       # track_id
    if command == NFCP_CMD_AUDIO_PLAY:
        return len(data) == 6                       # track_id from_ms
    if command == NFCP_CMD_AUDIO_STATUS_REQ:
        return not data
    if command in (NFCP_CMD_SCRIPT_HEARTBEAT, NFCP_CMD_SCRIPT_STEP):
        return len(data) == 6
    return False


def decode_master(frame):
    """(kind, command, data, uid or None) of an NFCP frame the app may send
    through the master bridge, or None."""
    decoded = nfcp_decode(frame, (NFCP_KIND_BROADCAST, NFCP_KIND_UNIQUE))
    if decoded is None:
        return None
    kind, payload = decoded
    broadcast = kind == NFCP_KIND_BROADCAST
    header = 2 if broadcast else 6    # node_id [uid(4)] cmd
    if len(payload) < header or payload[0] != NFCP_MASTER_NODE_ID:
        return None
    command = payload[header - 1]
    data = payload[header:]
    valid = _master_broadcast_valid if broadcast else _master_unique_valid
    if not valid(command, data):
        return None
    return kind, command, bytes(data), (None if broadcast else bridge_radio.int_from_be(payload[1:5]))


# Commands that may arm or fire. They need the ARM key on ARM, and all but
# ARM need a module this bridge armed.
MASTER_ARMING = (NFCP_CMD_ARM, NFCP_CMD_FIRE, NFCP_CMD_SCRIPT_START, NFCP_CMD_SCRIPT_STEP)
# Commands that stop things: allowed even after the app session expired.
MASTER_STOPPING = (NFCP_CMD_DISARM, NFCP_CMD_SCRIPT_STOP, NFCP_CMD_AUDIO_STOP)
# Changes refused while any module may be armed.
MASTER_SAFE_ONLY = (NFCP_CMD_SET_RESISTANCE_CAL, NFCP_CMD_SET_UNIQUE, NFCP_CMD_SET_GROUP,
                    NFCP_CMD_SET_NETWORK, NFCP_CMD_SET_RADIO_CHANNEL,
                    NFCP_CMD_OTA_UPDATE_BEGIN, NFCP_CMD_OTA_UPDATE_WRITE,
                    NFCP_CMD_OTA_UPDATE_END, NFCP_CMD_OTA_UPDATE_REBOOT,
                    NFCP_CMD_SCRIPT_CLEAR, NFCP_CMD_SCRIPT_LOAD)
# A module's "uid ACK DISARM OK" reply ends with these.
MASTER_DISARM_ACK = bytes((NFCP_CMD_ACK, NFCP_CMD_DISARM, NFCP_ACK_OK))

PAIR_OFFER_GAP_MS = 300
PAIR_MAX_SECONDS = 30


class BridgePairing:
    """The bridge side of pairing: for a few seconds the radio sits on the
    pairing network and channel and offers this bridge's own network and
    channel (NFCP broadcast NFCP_CMD_PAIR) every PAIR_OFFER_GAP_MS."""

    def __init__(self, clock, difference):
        self.clock = clock
        self.difference = difference
        self.until = None
        self.last_offer = None
        self.nonce = 0

    def active(self):
        return self.until is not None

    def start(self, seconds):
        if not 1 <= seconds <= PAIR_MAX_SECONDS:
            return False
        self.until = self.clock() + seconds * 1000
        self.last_offer = None
        self.nonce = (self.nonce + 1) & 0xFFFF or 1
        return True

    def stop(self):
        self.until = None

    def seconds_left(self):
        if self.until is None:
            return 0
        return max(0, (self.difference(self.until, self.clock()) + 999) // 1000)

    def poll(self, network, channel):
        """The offer frame to send now, or None. Ends the window on time."""
        if self.until is None:
            return None
        now = self.clock()
        if self.difference(now, self.until) >= 0:
            self.until = None
            return None
        if self.last_offer is not None and self.difference(now, self.last_offer) < PAIR_OFFER_GAP_MS:
            return None
        self.last_offer = now
        return nfcp_broadcast(NFCP_CMD_PAIR, bridge_radio.int_to_be(network, 2) +
                              bytes([channel & 0xFF]) + bridge_radio.int_to_be(self.nonce, 2))


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
        # A broadcast DISARM gets no per-module ACK, so it is unconfirmed
        # until every armed target acknowledges a unique DISARM.
        self.broadcast_disarm = False

    def permit(self, frame, host_active, switch=None):
        decoded = decode_master(frame)
        if decoded is None:
            return False
        kind, command, data, uid = decoded
        if command in MASTER_STOPPING:
            if command == NFCP_CMD_DISARM and kind == NFCP_KIND_BROADCAST and self.may_be_armed:
                self.broadcast_disarm = True
            return True  # STOP/DISARM remain possible after host expiry.
        if not host_active or self.expired or self.stop_pending:
            return False
        if command in MASTER_ARMING:
            if switch is not None and switch != 2:
                return False
            if command != NFCP_CMD_ARM and uid not in self.targets:
                return False
            # Set before RF send: transmission failure does not prove disarm.
            self.may_be_armed = True
            if command == NFCP_CMD_ARM and kind == NFCP_KIND_UNIQUE:
                self.targets.add(uid)
                self.needs_rearm = False
                self.broadcast_disarm = False
        elif command in MASTER_SAFE_ONLY and self.may_be_armed:
            return False
        elif command == NFCP_CMD_FIRE_PULSE and data and self.may_be_armed:
            return False  # The cue hold time only changes while all is safe.
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
                stopped = self.send(network + nfcp_broadcast(NFCP_CMD_SCRIPT_STOP, b'\x00\x00'))
                disarmed = self.send(network + nfcp_broadcast(NFCP_CMD_DISARM))
                if stopped and disarmed:
                    self.stop_pending -= 1
            except Exception:
                pass
        if self.expired and not self.stop_pending and host_active:
            # Fresh explicit ARM is required; this never represents field ACK.
            self.may_be_armed = False
            self.broadcast_disarm = False
            self.targets.clear()
            self.expired = False
        if (self.needs_rearm and not self.stop_pending and host_active and
                (not self.rearm_hold or
                 self.difference(self.clock(), self.stopped_at) >= KEEPALIVE_HOLD_MS)):
            # Resume the keepalive while the host stays connected, as the
            # original master does. ARM still requires a fresh explicit request.
            self.needs_rearm = False

    ARM_SAFE = 0
    ARM_ARMED = 1
    ARM_UNCONFIRMED = 2

    def arm_state(self):
        """Field arm state for the status LED, as far as this bridge knows.

        ARMED: an ARM was sent and not every target has ACKed a DISARM.
        UNCONFIRMED: a DISARM went out without per-module confirmation, or the
        keepalive is held so possibly-armed modules can self-disarm.
        SAFE: nothing was armed through this bridge, or every target ACKed
        DISARM, or the self-disarm window has passed.
        """
        if self.stop_pending or (self.needs_rearm and self.rearm_hold):
            return self.ARM_UNCONFIRMED
        if self.may_be_armed:
            return self.ARM_UNCONFIRMED if self.broadcast_disarm else self.ARM_ARMED
        return self.ARM_SAFE

    def sent(self, frame):
        # RF transmit success does not establish remote disarm.
        pass

    def observe_reply(self, frame):
        # Only an ACK of a DISARM matters here. Its three bytes are never
        # escaped, so a frame without them can skip the full decode (which
        # keeps the bridge off the radio for a few ms per reply).
        if MASTER_DISARM_ACK not in frame:
            return
        decoded = nfcp_decode(frame, (NFCP_KIND_RESPONSE,))
        if decoded is None:
            return
        payload = decoded[1]
        # uid(4) ACK DISARM OK
        if (len(payload) == 7 and payload[4] == NFCP_CMD_ACK and
                payload[5] == NFCP_CMD_DISARM and payload[6] == NFCP_ACK_OK):
            self.targets.discard(bridge_radio.int_from_be(payload[:4]))
            self.may_be_armed = bool(self.targets)
            if not self.may_be_armed:
                self.broadcast_disarm = False
