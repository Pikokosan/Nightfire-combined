# Audio box firmware (audio_box/design.md), step 3: a fake clock.
#
# The box is a mesh node like a field module (pairing, INFO, settings, OTA,
# relaying, all from module_link.py) with no outputs, no ARM and no script
# engine. It plays a "track" on its own clock and broadcasts the play
# position as SHOW_TIME, which armed modules follow after a chase START from
# the bridge. Step 4 replaces the fake clock with LTC decoded from a real
# player's output; everything else here stays.
#
# Commands it answers (unique, from the bridge):
#   AUDIO_LOAD   track(2)            -> ACK (NACK_BUSY while playing)
#   AUDIO_PLAY   track(2) from_ms(4) -> ACK; starts (or restarts) at from_ms
#   AUDIO_STOP   track(2)            -> ACK; stops whatever is playing
#   AUDIO_STATUS_REQ                 -> AUDIO_STATUS_RSP
#     track(2) position_ms(4) state(1) level(1) length_ms(4)
#   DISARM -> ACK, OUTPUT_STATUS_REQ -> nothing armed (nothing to disarm, so
#   the app's Disarm all works). ARM, FIRE, scripts and anything else -> NACK.
#
# Safety: the box never builds ARM, FIRE, SCRIPT_START or SCRIPT_STEP
# (a test checks this file). SHOW_TIME does nothing without a chase START
# from the bridge naming this box.

import time

import module_ota
import nrf_module_common
import nrf_radio
from machine import Pin
from module_link import ModuleLink, encode_text_field, ticks_rand
from mesh import MESH_DEFAULT_MAX_HOPS, MESH_RADIO_ADDRESS, mesh_build, mesh_transit_us
from nfcp import *

try:
    import nrf_node_config as node_config
except Exception:
    node_config = None


APP_FIRMWARE_NAME = "audio-box"
APP_FIRMWARE_VERSION = "0.1.1"
CLOCK_SOURCE = "fake-clock"

DEFAULT_NETWORK_ID = 0x04D2
DEFAULT_GROUP_ID = 0x0001
DEVICE_TYPE = 0x30          # field module 0x20, dummy 0x10
CUE_COUNT = 0

RADIO_CHANNEL = getattr(node_config, "RADIO_CHANNEL", 90)
RADIO_ADDRESS = getattr(node_config, "MESH_RADIO_ADDRESS", MESH_RADIO_ADDRESS)
MESH_MAX_HOPS = getattr(node_config, "MESH_MAX_HOPS", MESH_DEFAULT_MAX_HOPS)
MESH_RELAY_ENABLED = getattr(node_config, "MESH_RELAY_ENABLED", True)
MESH_BENCH_DEAF_TO = tuple(getattr(node_config, "MESH_BENCH_DEAF_TO", ()) or ())
RADIO_TX_POWER = 8
RADIO_RX_TIMEOUT_MS = getattr(node_config, "RADIO_RX_TIMEOUT_MS", 50)
RADIO_CCA_ENABLED = getattr(node_config, "RADIO_CCA_ENABLED", True)
RADIO_CCA_MS = getattr(node_config, "RADIO_CCA_MS", 2)
RADIO_CCA_BUSY_DBM = getattr(node_config, "RADIO_CCA_BUSY_DBM", -85)
RADIO_CCA_RETRIES = getattr(node_config, "RADIO_CCA_RETRIES", 4)
RADIO_CCA_BACKOFF_MIN_MS = getattr(node_config, "RADIO_CCA_BACKOFF_MIN_MS", 2)
RADIO_CCA_BACKOFF_MAX_MS = getattr(node_config, "RADIO_CCA_BACKOFF_MAX_MS", 10)

DISCOVER_BACKOFF_MIN_MS = 250
DISCOVER_BACKOFF_MAX_MS = 500
DISCOVER_RESPONSE_REPEATS = 3
DISCOVER_RESPONSE_GAP_MS = 35

# SHOW_TIME: every 250 ms while playing (4 a second; the design's measured
# rate), each sent 3 times 8-12 ms apart like the bridge's time code. After
# the track ends or stops, SHOW_TIME_TAIL more messages carry the new state
# so modules stop at once, then the box goes quiet.
SHOW_TIME_GAP_MS = getattr(node_config, "SHOW_TIME_GAP_MS", 250)
SHOW_TIME_REPEATS = getattr(node_config, "SHOW_TIME_REPEATS", 3)
SHOW_TIME_REPEAT_GAP_MIN_MS = 8
SHOW_TIME_REPEAT_GAP_MAX_MS = 12
SHOW_TIME_TAIL = 4

# Fake tracks: every track id 1-65535 exists, FAKE_TRACK_MS long, unless
# FAKE_TRACKS ({track_id: length_ms}) lists the ones that exist.
FAKE_TRACK_MS = getattr(node_config, "FAKE_TRACK_MS", 600000)
FAKE_TRACKS = getattr(node_config, "FAKE_TRACKS", None)
LEVEL_UNKNOWN = 0xFF        # AUDIO_STATUS_RSP level: not measured

# Holding this pin low (button to ground) at power-up starts pairing.
PAIR_BUTTON_PIN = getattr(node_config, "PAIR_BUTTON_PIN", None)
PAIR_HOLD_MS = 1000


def get_default_unique_id():
    return nrf_module_common.get_default_unique_id(0x52840001)


CONFIG_UNIQUE_ID = getattr(node_config, "UNIQUE_ID", 0)
GROUP_ID = getattr(node_config, "GROUP_ID", DEFAULT_GROUP_ID)
UNIQUE_ID = (CONFIG_UNIQUE_ID & 0xFFFFFFFF) or get_default_unique_id()
NETWORK_ID = getattr(node_config, "NETWORK_ID", DEFAULT_NETWORK_ID)


def save_runtime_config():
    try:
        with open("nrf_node_config.py", "w") as f:
            f.write("# Auto-saved audio box config.\n")
            f.write("UNIQUE_ID = 0x%08X\n" % (CONFIG_UNIQUE_ID & 0xFFFFFFFF))
            f.write("GROUP_ID = 0x%04X\n" % (GROUP_ID & 0xFFFF))
            f.write("NETWORK_ID = 0x%04X\n" % (NETWORK_ID & 0xFFFF))
            f.write("RADIO_CHANNEL = %d\n" % (RADIO_CHANNEL & 0xFF))
            f.write("MESH_MAX_HOPS = %d\n" % MESH_MAX_HOPS)
            f.write("MESH_RELAY_ENABLED = %s\n" % ("True" if MESH_RELAY_ENABLED else "False"))
            f.write("FAKE_TRACK_MS = %d\n" % FAKE_TRACK_MS)
            if FAKE_TRACKS is not None:
                f.write("FAKE_TRACKS = %r\n" % (FAKE_TRACKS,))
            if PAIR_BUTTON_PIN is None:
                f.write("PAIR_BUTTON_PIN = None\n")
            else:
                f.write("PAIR_BUTTON_PIN = %d\n" % PAIR_BUTTON_PIN)
        return True
    except Exception as e:
        print("Config save failed:", e)
        return False


class FakePlayer:
    """The track and its play position on the box's own clock.

    The position is kept as microseconds played, added up from short
    ticks_us() steps, so it is exact to the microsecond and survives
    ticks_us() wrapping (every ~18 min) on long tracks."""

    def __init__(self, tracks=None, default_ms=FAKE_TRACK_MS):
        self.tracks = tracks
        self.default_ms = default_ms
        self.track = 0
        self.state = SHOW_STOPPED
        self.length_ms = 0
        self.base_ms = 0          # position when play started
        self.played_us = 0        # played since then
        self.last_us = 0

    def length_of(self, track):
        if not 1 <= track <= 0xFFFF:
            return None
        if self.tracks is None:
            return self.default_ms
        return self.tracks.get(track)

    def advance(self, now_us):
        """Bring the position up to now_us. True if the track just ended."""
        if self.state != SHOW_PLAYING:
            return False
        step = time.ticks_diff(now_us, self.last_us)
        self.last_us = now_us
        if step > 0:
            self.played_us += step
        if self.base_ms + self.played_us // 1000 >= self.length_ms:
            self.base_ms = self.length_ms
            self.played_us = 0
            self.state = SHOW_ENDED
            return True
        return False

    def position_ms(self):
        return self.base_ms + self.played_us // 1000

    def load(self, track):
        length = self.length_of(track)
        if length is None:
            return NACK_INVALID_VALUE
        if self.state == SHOW_PLAYING:
            return NACK_BUSY
        self.track = track
        self.length_ms = length
        self.state = SHOW_STOPPED
        self.base_ms = 0
        self.played_us = 0
        return ACK_OK

    def play(self, track, from_ms, now_us):
        length = self.length_of(track)
        if length is None or not 0 <= from_ms < length:
            return NACK_INVALID_VALUE
        self.track = track
        self.length_ms = length
        self.base_ms = from_ms
        self.played_us = 0
        self.last_us = now_us
        self.state = SHOW_PLAYING
        return ACK_OK

    def stop(self, now_us):
        """Stop whatever is playing. True if it was playing."""
        playing = self.state == SHOW_PLAYING
        if playing:
            self.advance(now_us)
            self.base_ms = self.position_ms()
            self.played_us = 0
        self.state = SHOW_STOPPED
        return playing


class AudioBox:
    """Firmware state and the hooks module_link.py calls."""

    firmware_version = APP_FIRMWARE_VERSION

    def __init__(self):
        self.player = FakePlayer(FAKE_TRACKS, FAKE_TRACK_MS)
        self.next_show_ms = None   # when the next SHOW_TIME is due (None: quiet)
        self.tail = 0              # SHOW_TIME still to send after an end or stop
        self.sent = 0

    def ids(self):
        return NETWORK_ID, GROUP_ID, UNIQUE_ID, RADIO_CHANNEL

    def armed(self):
        return False               # the box has nothing to arm

    def status_text(self):
        p = self.player
        state = ("stopped", "playing", "paused", "ended")[p.state]
        return "%s %s track=%d pos=%d" % (CLOCK_SOURCE, state, p.track, p.position_ms())

    def info_payload(self):
        return (
            encode_text_field(APP_FIRMWARE_NAME) +
            encode_text_field(APP_FIRMWARE_VERSION) +
            encode_text_field("nrf-radio-native") +
            encode_text_field(self.status_text()) +
            encode_text_field("0x%08X" % (get_default_unique_id() & 0xFFFFFFFF)) +
            encode_text_field("0x%08X" % (CONFIG_UNIQUE_ID & 0xFFFFFFFF)) +
            encode_text_field("0") +
            encode_text_field("USB")
        )

    def apply_setting(self, name, value):
        global GROUP_ID, NETWORK_ID, RADIO_CHANNEL, CONFIG_UNIQUE_ID, UNIQUE_ID

        if name == "group":
            GROUP_ID = value
        elif name == "network":
            NETWORK_ID = value
        elif name == "channel":
            RADIO_CHANNEL = value
        elif name == "unique":
            CONFIG_UNIQUE_ID = value
            UNIQUE_ID = CONFIG_UNIQUE_ID or get_default_unique_id()
        return save_runtime_config()

    # --- SHOW_TIME ---------------------------------------------------------
    def send_now(self):
        self.next_show_ms = time.ticks_ms()

    def show_wait_ms(self, default_ms):
        if self.next_show_ms is None:
            return default_ms
        wait = time.ticks_diff(self.next_show_ms, time.ticks_ms())
        if wait < 1:
            return 1
        return wait if wait < default_ms else default_ms

    def service_show(self):
        """Advance the clock; send SHOW_TIME when it is due."""
        read_us = time.ticks_us()
        player = self.player
        if player.advance(read_us):
            print("AUDIO ended track=%d pos=%d" % (player.track, player.position_ms()))
            self.tail = SHOW_TIME_TAIL
            self.next_show_ms = time.ticks_ms()
        if self.next_show_ms is None or time.ticks_diff(time.ticks_ms(), self.next_show_ms) < 0:
            return
        if player.state != SHOW_PLAYING:
            if self.tail <= 0:
                self.next_show_ms = None
                return
            self.tail -= 1
        send_show_time(nfcp.build_show_time_broadcast(player.track, player.position_ms(), player.state), read_us)
        self.sent += 1
        if player.state == SHOW_PLAYING or self.tail > 0:
            self.next_show_ms = time.ticks_add(time.ticks_ms(), SHOW_TIME_GAP_MS)
        else:
            self.next_show_ms = None

    # --- commands ----------------------------------------------------------
    def handle_audio(self, pkt):
        cmd = pkt["cmd"]
        data = pkt.get("data", b"")
        player = self.player
        now_us = time.ticks_us()

        if cmd == CMD_AUDIO_STOP:
            # Stop first, whatever the track or length: it's the safe way.
            if player.stop(now_us):
                print("AUDIO stop track=%d pos=%d" % (player.track, player.position_ms()))
                self.tail = SHOW_TIME_TAIL
                self.send_now()
            if len(data) != 2:
                link.nack(pkt, cmd, NACK_BAD_LENGTH)
            else:
                link.ack(pkt, cmd)
            return

        if cmd == CMD_AUDIO_STATUS_REQ:
            if data:
                link.nack(pkt, cmd, NACK_BAD_LENGTH)
                return
            player.advance(now_us)
            link.respond(CMD_AUDIO_STATUS_RSP, build_status_payload(player))
            return

        if cmd == CMD_AUDIO_LOAD:
            if len(data) != 2:
                link.nack(pkt, cmd, NACK_BAD_LENGTH)
                return
            track = int.from_bytes(data, "big")
            code = player.load(track)
            if code != ACK_OK:
                link.nack(pkt, cmd, code)
                return
            print("AUDIO load track=%d length=%d" % (track, player.length_ms))
            link.ack(pkt, cmd)
            return

        if cmd == CMD_AUDIO_PLAY:
            if len(data) != 6:
                link.nack(pkt, cmd, NACK_BAD_LENGTH)
                return
            track = int.from_bytes(data[0:2], "big")
            from_ms = int.from_bytes(data[2:6], "big")
            code = player.play(track, from_ms, now_us)
            if code != ACK_OK:
                link.nack(pkt, cmd, code)
                return
            print("AUDIO play track=%d from=%d length=%d" % (track, from_ms, player.length_ms))
            self.tail = 0
            self.send_now()
            link.ack(pkt, cmd)


def build_status_payload(player):
    return (
        (player.track & 0xFFFF).to_bytes(2, "big") +
        (player.position_ms() & 0xFFFFFFFF).to_bytes(4, "big") +
        bytes((player.state & 0xFF, LEVEL_UNKNOWN)) +
        (player.length_ms & 0xFFFFFFFF).to_bytes(4, "big")
    )


def build_output_status_payload():
    """Field module OUTPUT_STATUS format: nothing armed, held or fired."""
    return bytes((1, 0, CUE_COUNT)) + bytes(8) + bytes(8) + bytes(4) + b"\xff\xff\xff\xff"


def send_show_time(frame, read_us):
    """Broadcast a SHOW_TIME as time code: its mesh age counts from read_us,
    the moment the position was read, so each receiver knows how old it is."""
    ids = link.ids()
    payload = mesh_build(ids[0], ids[2], link.next_msg_id(), frame, age=0)
    cca = link.cca_ms if link.cca_enabled else 0
    stamp = (read_us, 0, mesh_transit_us(len(payload), cca))
    link.send_or_queue(payload, 0, stamp)
    delay = 0
    span = SHOW_TIME_REPEAT_GAP_MAX_MS - SHOW_TIME_REPEAT_GAP_MIN_MS + 1
    for unused in range(SHOW_TIME_REPEATS - 1):
        delay += SHOW_TIME_REPEAT_GAP_MIN_MS + ticks_rand(span)
        link.queue.push(payload, delay, 0, stamp)


box = AudioBox()
link = ModuleLink(
    box,
    RADIO_ADDRESS,
    RADIO_TX_POWER,
    cca_enabled=RADIO_CCA_ENABLED,
    cca_ms=RADIO_CCA_MS,
    cca_busy_dbm=RADIO_CCA_BUSY_DBM,
    cca_retries=RADIO_CCA_RETRIES,
    cca_backoff_min_ms=RADIO_CCA_BACKOFF_MIN_MS,
    cca_backoff_max_ms=RADIO_CCA_BACKOFF_MAX_MS,
    max_hops=MESH_MAX_HOPS,
    relay_enabled=MESH_RELAY_ENABLED,
)
link.deaf_to = MESH_BENCH_DEAF_TO
nfcp = link.nfcp

AUDIO_CMDS = (CMD_AUDIO_LOAD, CMD_AUDIO_PLAY, CMD_AUDIO_STOP, CMD_AUDIO_STATUS_REQ)


def handle(pkt):
    cmd = pkt.get("cmd")
    if cmd == CMD_DISCOVER:
        link.discover_reply(
            pkt,
            CUE_COUNT,
            DEVICE_TYPE,
            DISCOVER_BACKOFF_MIN_MS,
            DISCOVER_BACKOFF_MAX_MS,
            DISCOVER_RESPONSE_REPEATS,
            DISCOVER_RESPONSE_GAP_MS,
        )
    elif cmd in AUDIO_CMDS:
        if link.is_for_me(pkt):
            box.handle_audio(pkt)
    elif cmd == CMD_DISARM:
        link.ack(pkt, CMD_DISARM)          # unique only; nothing to disarm
    elif cmd == CMD_OUTPUT_STATUS_REQ:
        if link.is_for_me(pkt) and not pkt.get("data", b""):
            link.respond(CMD_OUTPUT_STATUS_RSP, build_output_status_payload())
    elif link.handle_common(pkt):
        pass
    elif link.is_for_me(pkt):
        # ARM, FIRE, scripts...: the box has none of them.
        link.nack(pkt, cmd, NACK_INVALID_VALUE)


def pair_button_held():
    if PAIR_BUTTON_PIN is None:
        return False
    try:
        pin = Pin(PAIR_BUTTON_PIN, Pin.IN, Pin.PULL_UP)
    except Exception as e:
        print("Pair button init failed P%s:" % str(PAIR_BUTTON_PIN), e)
        return False
    start = time.ticks_ms()
    while time.ticks_diff(time.ticks_ms(), start) < PAIR_HOLD_MS:
        if pin.value():
            return False
        time.sleep_ms(20)
    return True


def setup():
    radio_ready = link.init_radio()
    if pair_button_held():
        radio_ready = link.start_pairing()
    print(
        "Nightfire audio box (%s) uid=0x%08X group=0x%04X net=0x%04X" %
        (CLOCK_SOURCE, UNIQUE_ID, GROUP_ID, NETWORK_ID)
    )
    return radio_ready


def main():
    radio_ready = setup()
    while True:
        try:
            module_ota.poll_timeout()
            if not radio_ready:
                time.sleep_ms(250)
                radio_ready = link.init_radio()
                continue
            box.service_show()
            link.service()
            payload = nrf_radio.recv(link.rx_timeout_ms(box.show_wait_ms(RADIO_RX_TIMEOUT_MS)))
            if payload:
                link.receive(payload, handle)
        except Exception as e:
            # Fail-stop: no more SHOW_TIME, so the modules stop within 1.5 s.
            print("Audio box main loop error:", e)
            box.player.stop(time.ticks_us())
            box.next_show_ms = None
            box.tail = 0
            radio_ready = False
            time.sleep_ms(100)
