"""Audio box firmware (audio_box/main/audio_main.py, step 3: fake clock).

Runs the firmware on fake time (a microsecond clock that wraps during the
tests, like ticks_us() on the board) and a fake radio that records every
payload sent. Commands go in through the real mesh and NFCP code.

Run: py -3 scripts/tests/test_audio_box.py
"""
import contextlib
import importlib.util
import io
import re
import sys
import types
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SOURCE = ROOT / "audio_box" / "main" / "audio_main.py"
TICKS_PERIOD = 1 << 30
BOX_UID = 0xA0D10B0C
BRIDGE_UID = 0x0B0B0B0B
TRACK = 7
TRACK_MS = 8000

# --- fakes ---------------------------------------------------------------
clock = {"us": TICKS_PERIOD - 3000000}     # ticks_us() wraps 3 s in
sent = []                                  # (clock us, payload)

fake_time = types.ModuleType("time")
fake_time.ticks_us = lambda: clock["us"] % TICKS_PERIOD
fake_time.ticks_ms = lambda: (clock["us"] // 1000) % TICKS_PERIOD
fake_time.ticks_add = lambda a, b: (a + b) % TICKS_PERIOD


def _ticks_diff(a, b):
    half = TICKS_PERIOD // 2
    return ((a - b + half) % TICKS_PERIOD) - half


fake_time.ticks_diff = _ticks_diff


def _sleep_ms(ms):
    clock["us"] += int(ms) * 1000


fake_time.sleep_ms = _sleep_ms
fake_time.sleep_us = lambda us: None
fake_time.time = lambda: clock["us"] // 1000000

fake_machine = types.ModuleType("machine")


class _Pin(object):
    IN = 0
    OUT = 1
    PULL_UP = 1

    def __init__(self, *args, **kwargs):
        pass

    def value(self, v=None):
        return 1


class _Uart(object):
    def __init__(self, *args, **kwargs):
        pass


fake_machine.Pin = _Pin
fake_machine.UART = _Uart
fake_machine.reset = lambda: None
fake_machine.unique_id = lambda: b"\x00\x00\x00\x01"

fake_radio = types.ModuleType("nrf_radio")
fake_radio.init = lambda **kwargs: None
fake_radio.send = lambda payload, **kwargs: sent.append((clock["us"], bytes(payload))) or True
fake_radio.recv = lambda timeout=0: None
fake_radio.last_rssi = lambda: -60
fake_radio.device_id32 = lambda: BOX_UID

config = types.ModuleType("nrf_node_config")
config.FAKE_TRACKS = {TRACK: TRACK_MS, 8: 600000}


def load_box():
    saved = {name: sys.modules.get(name) for name in ("time", "utime", "machine")}
    for name, module in (("time", fake_time), ("utime", fake_time), ("machine", fake_machine),
                         ("nrf_radio", fake_radio), ("nrf_node_config", config)):
        sys.modules[name] = module
    sys.path[:0] = [str(ROOT / "audio_box" / "main"), str(ROOT / "main")]
    try:
        spec = importlib.util.spec_from_file_location("audio_main_under_test", str(SOURCE))
        module = importlib.util.module_from_spec(spec)
        with contextlib.redirect_stdout(io.StringIO()):
            spec.loader.exec_module(module)
        return module
    finally:
        # The firmware keeps the fakes it imported; unittest gets the real ones.
        for name, module in saved.items():
            if module is None:
                sys.modules.pop(name, None)
            else:
                sys.modules[name] = module


BOX = load_box()
LINK_GLOBALS = type(BOX.link).receive.__globals__
mesh_build = LINK_GLOBALS["mesh_build"]
mesh_parse = LINK_GLOBALS["mesh_parse"]
mesh_transit_us = LINK_GLOBALS["mesh_transit_us"]
link_decode_nfcp = LINK_GLOBALS["link_decode_nfcp"]
MASTER = BOX.NFCP(node_id=0x01)
NETWORK = BOX.NETWORK_ID


def decode(payload):
    parsed = mesh_parse(payload)
    pkt = link_decode_nfcp(payload, parsed[5])
    pkt["msg_id"] = parsed[2]
    pkt["age_us"] = None if parsed[4] is None else parsed[4] * 100
    pkt["read_at"] = None
    return pkt


def read_at(t, payload, pkt):
    """When the box read the position, from the age a copy carries: the age
    runs until the receiver has the frame, so it includes the transit."""
    transit = mesh_transit_us(len(payload), BOX.link.cca_ms)
    return t + transit - pkt["age_us"]


class BoxTest(unittest.TestCase):
    def setUp(self):
        self.out = io.StringIO()
        self._redirect = contextlib.redirect_stdout(self.out)
        self._redirect.__enter__()
        BOX.box.player.stop(fake_time.ticks_us())
        BOX.box.player.track = 0
        BOX.box.next_show_ms = None
        BOX.box.tail = 0
        BOX.link.queue.clear()
        BOX.link.replies = {}
        self.run_ms(50)
        sent[:] = []
        BoxTest.msg = getattr(BoxTest, "msg", 0x100)

    def tearDown(self):
        self._redirect.__exit__(None, None, None)

    # --- driving ----------------------------------------------------------
    def run_ms(self, ms, step_us=500):
        end = clock["us"] + int(ms * 1000)
        while clock["us"] < end:
            BOX.box.service_show()
            BOX.link.service()
            clock["us"] += step_us

    def request(self, frame):
        """A unique command from the bridge; returns the box's reply."""
        BoxTest.msg += 1
        before = len(sent)
        BOX.link.receive(mesh_build(NETWORK, BRIDGE_UID, BoxTest.msg, frame), BOX.handle)
        replies = [decode(p) for t, p in sent[before:]]
        replies = [r for r in replies if r["frame_type"] == BOX.SOF_RESPONSE]
        return replies[0] if replies else None

    def ack_code(self, frame, cmd):
        reply = self.request(frame)
        self.assertIsNotNone(reply, "no reply")
        self.assertIn(reply["cmd"], (BOX.CMD_ACK, BOX.CMD_NACK))
        self.assertEqual(reply["data"][0], cmd)
        return reply["data"][1] if reply["cmd"] == BOX.CMD_ACK else ("NACK", reply["data"][1])

    def status(self):
        reply = self.request(MASTER.build_audio_status_request(BOX_UID))
        self.assertEqual(reply["cmd"], BOX.CMD_AUDIO_STATUS_RSP)
        data = reply["data"]
        self.assertEqual(len(data), 12)
        return (int.from_bytes(data[0:2], "big"), int.from_bytes(data[2:6], "big"), data[6], data[7],
                int.from_bytes(data[8:12], "big"))

    def play(self, track=TRACK, from_ms=0):
        return self.ack_code(MASTER.build_audio_play(BOX_UID, track, from_ms), BOX.CMD_AUDIO_PLAY)

    def stop(self, track=TRACK):
        return self.ack_code(MASTER.build_audio_stop(BOX_UID, track), BOX.CMD_AUDIO_STOP)

    def show_times(self, since_us=None):
        """SHOW_TIME sent: [(send us, pkt)], every copy."""
        out = []
        for t, payload in sent:
            if since_us is not None and t < since_us:
                continue
            pkt = decode(payload)
            if pkt["cmd"] == BOX.CMD_SHOW_TIME:
                data = pkt["data"]
                pkt["track"] = int.from_bytes(data[0:2], "big")
                pkt["pos"] = int.from_bytes(data[2:6], "big")
                pkt["state"] = data[6]
                pkt["read_at"] = read_at(t, payload, pkt)
                out.append((t, pkt))
        return out

    def messages(self, since_us=None):
        """One entry per SHOW_TIME message (first copy of each msg id)."""
        seen = {}
        for t, pkt in self.show_times(since_us):
            seen.setdefault(pkt["msg_id"], (t, pkt))
        return sorted(seen.values(), key=lambda item: item[0])


class PlayTests(BoxTest):
    def test_play_sends_show_time_at_once_then_every_250_ms(self):
        start = clock["us"]
        self.assertEqual(self.play(), BOX.ACK_OK)
        self.run_ms(2010)
        msgs = self.messages(start)
        self.assertEqual(len(msgs), 9)                        # 0, 250, ... 2000 ms
        self.assertLess(msgs[0][0] - start, 2000)
        gaps = [(b[0] - a[0]) / 1000.0 for a, b in zip(msgs, msgs[1:])]
        for gap in gaps:
            self.assertAlmostEqual(gap, 250, delta=1)
        for t, pkt in msgs:
            self.assertEqual((pkt["track"], pkt["state"], pkt["frame_type"]),
                             (TRACK, BOX.SHOW_PLAYING, BOX.SOF_BROADCAST))

    def test_each_message_is_sent_three_times_with_its_age(self):
        start = clock["us"]
        self.play(from_ms=1200)
        self.run_ms(600)
        copies = {}
        for t, pkt in self.show_times(start):
            copies.setdefault(pkt["msg_id"], []).append((t, pkt))
        for group in copies.values():
            self.assertEqual(len(group), 3)
            self.assertEqual(len(set(p["pos"] for t, p in group)), 1)
            # Every copy's position plus its age is the position at that send.
            for t, pkt in group:
                self.assertIsNotNone(pkt["age_us"])
                self.assertLess(abs(pkt["read_at"] - group[0][1]["read_at"]), 100)
            gaps = [(b[0] - a[0]) / 1000.0 for a, b in zip(group, group[1:])]
            for gap in gaps:
                self.assertTrue(7.5 <= gap <= 12.5, gaps)

    def test_position_follows_the_clock_across_the_tick_wrap(self):
        clock["us"] = TICKS_PERIOD * 5 - 1000000            # wraps 1 s into the track
        self.play(from_ms=500)
        played = clock["us"]
        self.run_ms(3000, step_us=700)
        msgs = self.messages(played)
        for t, pkt in msgs:
            expected_ms = 500 + (pkt["read_at"] - played) / 1000.0
            self.assertLess(abs(pkt["pos"] - expected_ms), 1.0, (pkt["pos"], expected_ms))
        track, pos, state, level, length = self.status()
        self.assertEqual((track, state, level, length), (TRACK, BOX.SHOW_PLAYING, 0xFF, TRACK_MS))
        self.assertAlmostEqual(pos, 500 + (clock["us"] - played) / 1000.0, delta=1)

    def test_play_again_jumps_to_from_ms(self):
        self.play()
        self.run_ms(1000)
        jump = clock["us"]
        self.assertEqual(self.play(from_ms=5000), BOX.ACK_OK)
        self.run_ms(30)
        first = self.messages(jump)[0][1]
        self.assertAlmostEqual(first["pos"], 5000, delta=2)

    def test_track_end_sends_ended_then_goes_quiet(self):
        self.play(from_ms=TRACK_MS - 600)
        start = clock["us"]
        self.run_ms(3000)
        msgs = self.messages(start)
        states = [p["state"] for t, p in msgs]
        playing = [s for s in states if s == BOX.SHOW_PLAYING]
        ended = [p for t, p in msgs if p["state"] == BOX.SHOW_ENDED]
        self.assertEqual(states, [BOX.SHOW_PLAYING] * len(playing) + [BOX.SHOW_ENDED] * 4)
        for pkt in ended:
            self.assertEqual(pkt["pos"], TRACK_MS)
        end_at = [t for t, p in msgs if p["state"] == BOX.SHOW_ENDED][0]
        self.assertAlmostEqual((end_at - start) / 1000.0, 600, delta=2)
        self.assertIn("AUDIO ended track=7 pos=8000", self.out.getvalue())
        self.assertEqual(self.status()[1:3], (TRACK_MS, BOX.SHOW_ENDED))
        quiet = clock["us"]
        self.run_ms(2000)
        self.assertEqual(self.show_times(quiet), [])


class StopTests(BoxTest):
    def test_stop_sends_stopped_at_once_then_quiet(self):
        self.play()
        self.run_ms(900)
        stop_at = clock["us"]
        self.assertEqual(self.stop(), BOX.ACK_OK)
        self.run_ms(2000)
        msgs = self.messages(stop_at)
        self.assertEqual([p["state"] for t, p in msgs], [BOX.SHOW_STOPPED] * 4)
        self.assertLess(msgs[0][0] - stop_at, 2000)
        track, pos, state, level, length = self.status()
        self.assertEqual(state, BOX.SHOW_STOPPED)
        self.assertAlmostEqual(pos, 900, delta=2)

    def test_stop_with_any_track_or_bad_length_still_stops(self):
        self.play()
        self.run_ms(300)
        self.assertEqual(self.stop(track=99), BOX.ACK_OK)
        self.assertEqual(self.status()[2], BOX.SHOW_STOPPED)
        self.play()
        reply = self.request(MASTER.build_unique_command(BOX_UID, BOX.CMD_AUDIO_STOP, b"\x00"))
        self.assertEqual((reply["cmd"], reply["data"][1]), (BOX.CMD_NACK, BOX.NACK_BAD_LENGTH))
        self.assertEqual(self.status()[2], BOX.SHOW_STOPPED)

    def test_player_fault_sends_stopped(self):
        # A real player (player_link.UartPlayer) can stop the track itself
        # when the ESP32 stops answering: advance() says so with state
        # stopped, and the box tells the modules at once.
        self.play()
        self.run_ms(500)
        player = BOX.box.player
        real_advance = player.advance

        def faulty(now_us):
            player.state = BOX.SHOW_STOPPED
            player.advance = real_advance
            return True

        player.advance = faulty
        at = clock["us"]
        try:
            self.run_ms(1500)
        finally:
            player.__dict__.pop("advance", None)
        msgs = self.messages(at)
        self.assertEqual([p["state"] for t, p in msgs], [BOX.SHOW_STOPPED] * 4)
        self.assertIn("AUDIO stopped track=7", self.out.getvalue())
        self.assertNotIn("AUDIO ended", self.out.getvalue())

    def test_stop_when_idle_acks_and_sends_nothing(self):
        start = clock["us"]
        self.assertEqual(self.stop(), BOX.ACK_OK)
        self.run_ms(1000)
        self.assertEqual(self.show_times(start), [])

    def test_main_loop_error_stops_the_music(self):
        self.play()
        self.run_ms(300)

        class Stop(Exception):
            pass

        def boom():
            raise RuntimeError("test")

        saved_service, saved_setup, saved_sleep = BOX.box.service_show, BOX.setup, fake_time.sleep_ms
        BOX.box.service_show = boom
        BOX.setup = lambda: True

        def sleep(ms):
            raise Stop()

        fake_time.sleep_ms = sleep
        try:
            with self.assertRaises(Stop):
                BOX.main()
        finally:
            del BOX.box.service_show
            BOX.setup = saved_setup
            fake_time.sleep_ms = saved_sleep
        self.assertIs(BOX.box.service_show.__func__, saved_service.__func__)
        self.assertEqual(BOX.box.player.state, BOX.SHOW_STOPPED)
        quiet = clock["us"]
        self.run_ms(1000)
        self.assertEqual([p for t, p in self.show_times(quiet) if p["state"] == BOX.SHOW_PLAYING], [])


class CommandTests(BoxTest):
    def test_load_checks_track_and_busy(self):
        load = lambda track: self.ack_code(MASTER.build_audio_load(BOX_UID, track), BOX.CMD_AUDIO_LOAD)
        self.assertEqual(load(0), ("NACK", BOX.NACK_INVALID_VALUE))
        self.assertEqual(load(9), ("NACK", BOX.NACK_INVALID_VALUE))       # not in FAKE_TRACKS
        self.assertEqual(load(8), BOX.ACK_OK)
        self.assertEqual(self.status(), (8, 0, BOX.SHOW_STOPPED, 0xFF, 600000))
        self.play()
        self.assertEqual(load(8), ("NACK", BOX.NACK_BUSY))
        self.assertEqual(self.status()[0], TRACK)

    def test_play_refuses_bad_tracks_positions_and_lengths(self):
        self.assertEqual(self.play(track=0), ("NACK", BOX.NACK_INVALID_VALUE))
        self.assertEqual(self.play(track=9), ("NACK", BOX.NACK_INVALID_VALUE))
        self.assertEqual(self.play(from_ms=TRACK_MS), ("NACK", BOX.NACK_INVALID_VALUE))
        reply = self.request(MASTER.build_unique_command(BOX_UID, BOX.CMD_AUDIO_PLAY, b"\x00\x07"))
        self.assertEqual((reply["cmd"], reply["data"][1]), (BOX.CMD_NACK, BOX.NACK_BAD_LENGTH))
        self.assertEqual(self.status()[2], BOX.SHOW_STOPPED)
        self.run_ms(500)
        self.assertEqual(self.show_times(), [])

    def test_status_request_takes_no_data(self):
        reply = self.request(MASTER.build_unique_command(BOX_UID, BOX.CMD_AUDIO_STATUS_REQ, b"\x01"))
        self.assertEqual((reply["cmd"], reply["data"][1]), (BOX.CMD_NACK, BOX.NACK_BAD_LENGTH))

    def test_commands_for_another_node_are_ignored(self):
        before = len(sent)
        BoxTest.msg += 1
        BOX.link.receive(mesh_build(NETWORK, BRIDGE_UID, BoxTest.msg,
                                    MASTER.build_audio_play(0x12345678, TRACK, 0)), BOX.handle)
        self.assertEqual(BOX.box.player.state, BOX.SHOW_STOPPED)
        self.assertEqual([p for t, p in sent[before:] if decode(p)["frame_type"] == BOX.SOF_RESPONSE], [])

    def test_nothing_to_arm_fire_or_script(self):
        self.assertFalse(BOX.box.armed())
        for frame, cmd in (
            (MASTER.build_arm_unique(BOX_UID), BOX.CMD_ARM),
            (MASTER.build_unique_fire(BOX_UID, 1), BOX.CMD_FIRE),
            (MASTER.build_script_start_chase(BOX_UID, 1, BOX_UID, TRACK), BOX.CMD_SCRIPT_START),
            (MASTER.build_script_clear(BOX_UID, 1), BOX.CMD_SCRIPT_CLEAR),
        ):
            self.assertEqual(self.ack_code(frame, cmd), ("NACK", BOX.NACK_INVALID_VALUE))
        self.assertFalse(BOX.box.armed())
        # A broadcast ARM gets no answer and changes nothing.
        before = len(sent)
        BoxTest.msg += 1
        BOX.link.receive(mesh_build(NETWORK, BRIDGE_UID, BoxTest.msg, MASTER.build_arm_broadcast()), BOX.handle)
        self.assertEqual(sent[before:], [])
        self.assertFalse(BOX.box.armed())

    def test_disarm_and_output_status_say_nothing_is_armed(self):
        self.assertEqual(self.ack_code(MASTER.build_disarm_unique(BOX_UID), BOX.CMD_DISARM), BOX.ACK_OK)
        reply = self.request(MASTER.build_output_status_request(BOX_UID))
        self.assertEqual(reply["cmd"], BOX.CMD_OUTPUT_STATUS_RSP)
        data = reply["data"]
        self.assertEqual(len(data), 27)
        self.assertEqual(data[1], 0)                         # flags: not armed, no driver
        self.assertEqual(data[3:11], bytes(8))               # nothing held on

    def test_info_names_the_box(self):
        reply = self.request(MASTER.build_info_request(BOX_UID))
        self.assertEqual(reply["cmd"], BOX.CMD_INFO_RSP)
        fields, data = [], bytes(reply["data"])
        while data:
            fields.append(data[1:1 + data[0]].decode())
            data = data[1 + data[0]:]
        self.assertEqual(fields[:2], ["audio-box", "0.2.0"])
        self.assertTrue(fields[3].startswith("fake-clock stopped"), fields[3])


class SourceTests(unittest.TestCase):
    def test_box_never_builds_arm_fire_or_script_commands(self):
        text = SOURCE.read_text(encoding="utf-8")
        code = "\n".join(line.split("#", 1)[0] for line in text.splitlines())
        for pattern in (r"build_arm", r"build_\w*fire", r"build_script_(start|step)",
                        r"\bCMD_ARM\b", r"\bCMD_FIRE\b", r"\bCMD_SCRIPT_START\b", r"\bCMD_SCRIPT_STEP\b"):
            self.assertIsNone(re.search(pattern, code), pattern)


if __name__ == "__main__":
    unittest.main()
