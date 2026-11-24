"""Chase mode of the module script engine (audio_box/design.md): cues
follow an audio box's SHOW_TIME broadcasts.

ScriptEngine runs on a fake clock that starts just before the MicroPython
ticks wrap, with a fake node and link. The audio box is modelled here: it
reads its play position, and the SHOW_TIME reaches the module a few ms later
with that delay in the mesh age field, as the mesh delivers it.

Run: py -3 scripts/tests/test_module_chase.py
"""
import contextlib
import io
import sys
import types
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PERIOD = 1 << 30            # MicroPython ticks_ms / ticks_us period


class Clock:
    def __init__(self):
        self.us = (PERIOD - 3000) * 1000     # ticks_ms wraps 3 s in

    def module(self):
        t = types.ModuleType("time")
        t.ticks_ms = lambda: (self.us // 1000) % PERIOD
        t.ticks_us = lambda: self.us % PERIOD
        t.ticks_add = lambda a, b: (a + b) % PERIOD

        def diff(a, b):
            d = (a - b) % PERIOD
            return d - PERIOD if d >= PERIOD // 2 else d
        t.ticks_diff = diff
        return t


CLOCK = Clock()


def load_engine():
    """Import nfcp and module_script from main/ with the fake time module,
    then put the real one back for unittest."""
    saved = sys.modules.get("time")
    sys.path.insert(0, str(ROOT / "main"))
    sys.modules["time"] = CLOCK.module()
    try:
        for name in ("module_script",):
            sys.modules.pop(name, None)
        import nfcp
        import module_script
        return nfcp, module_script
    finally:
        sys.modules["time"] = saved
        sys.path.pop(0)


nfcp, module_script = load_engine()

BOX = 0xA0D10B0C
BRIDGE = 0x00B21D6E
SCRIPT_ID = 0x4D53
TRACK = 0x0007
CUES = [(0, 0x1), (500, 0x2), (1000, 0x4), (2000, 0x8), (3000, 0x10),
        (6000, 0x20), (9000, 0x40), (60000, 0x80)]


class Node:
    def __init__(self):
        self.fired = []          # (local ms since test start, mask)
        self.blocked = None
        self.stops = 0
        self.t0 = 0

    def fire(self, mask):
        self.fired.append((CLOCK.us / 1000.0 - self.t0, mask))
        return nfcp.ACK_OK

    def fire_blocked(self):
        return self.blocked

    def stop_outputs(self):
        self.stops += 1


class Link:
    def __init__(self):
        self.sent = []

    def ack(self, pkt, cmd, code=nfcp.ACK_OK):
        if pkt.get("frame_type") == nfcp.SOF_UNIQUE:
            self.sent.append(("ack", cmd, code))

    def nack(self, pkt, cmd, code):
        if pkt.get("frame_type") == nfcp.SOF_UNIQUE:
            self.sent.append(("nack", cmd, code))


def decoded(frame):
    """The pkt dict module_link hands the engine for an NFCP frame."""
    parser = nfcp.NFCPStreamParser()
    for byte in frame:
        pkt = parser.feed(byte)
        if pkt is not None:
            return pkt
    raise AssertionError("frame did not decode")


class Show:
    """One module following one audio box."""

    def __init__(self, test, delay_ms=4, box_ppm=0.0):
        self.test = test
        self.node = Node()
        self.link = Link()
        self.engine = module_script.ScriptEngine(self.node, self.link)
        self.master = nfcp.NFCP(node_id=0x01)
        self.delay_ms = delay_ms           # box reads position -> module hears it
        self.box_ppm = box_ppm             # box clock against the module's
        self.playing = False
        self.position_ms = 0.0
        self.state = nfcp.SHOW_PLAYING
        self.source = BOX
        self.track = TRACK
        self.send_show_time = True
        self.send_heartbeat = True
        self.pending = []                  # (deliver_at_us, pkt)
        self.out = io.StringIO()
        self.node.t0 = CLOCK.us / 1000.0
        self.next_show_us = CLOCK.us
        self.next_beat_us = CLOCK.us
        self.load()

    def call(self, fn, *args):
        with contextlib.redirect_stdout(self.out):
            return fn(*args)

    def pkt(self, frame, origin):
        pkt = decoded(frame)
        pkt["mesh_origin"] = origin
        pkt["rx_us"] = CLOCK.us % PERIOD
        return pkt

    def load(self):
        uid = 0x11223344
        self.call(self.engine.handle, self.pkt(self.master.build_script_clear(uid, SCRIPT_ID), BRIDGE))
        self.call(self.engine.handle, self.pkt(self.master.build_script_load(uid, SCRIPT_ID, 0, CUES[:3]), BRIDGE))
        self.call(self.engine.handle, self.pkt(self.master.build_script_load(uid, SCRIPT_ID, 1, CUES[3:6]), BRIDGE))
        self.call(self.engine.handle, self.pkt(self.master.build_script_load(uid, SCRIPT_ID, 2, CUES[6:]), BRIDGE))
        self.test.assertEqual(len(self.engine.entries), len(CUES))
        self.uid = uid

    def start(self, audio_offset_ms=0, source=BOX, track=TRACK):
        frame = self.master.build_script_start_chase(self.uid, SCRIPT_ID, source, track, audio_offset_ms)
        self.call(self.engine.handle, self.pkt(frame, BRIDGE))
        return self.link.sent[-1]

    def play(self):
        self.playing = True
        self.position_ms = 0.0

    def elapsed(self):
        return CLOCK.us / 1000.0 - self.node.t0

    def step(self):
        """1 ms of time: box, bridge heartbeat, deliveries, engine."""
        CLOCK.us += 1000
        if self.playing and self.state == nfcp.SHOW_PLAYING:
            self.position_ms += 1.0 * (1.0 + self.box_ppm * 1e-6)
        if self.send_show_time and self.playing and CLOCK.us >= self.next_show_us:
            self.next_show_us = CLOCK.us + 100 * 1000
            frame = self.master.build_show_time_broadcast(self.track, int(self.position_ms), self.state)
            pkt = decoded(frame)
            pkt["mesh_origin"] = self.source
            pkt["mesh_age_us"] = self.delay_ms * 1000
            self.pending.append((CLOCK.us + self.delay_ms * 1000, pkt))
        if self.send_heartbeat and CLOCK.us >= self.next_beat_us:
            self.next_beat_us = CLOCK.us + 400 * 1000
            self.call(self.engine.handle, self.pkt(
                self.master.build_script_heartbeat_broadcast(SCRIPT_ID, 0), BRIDGE))
        for item in [p for p in self.pending if p[0] <= CLOCK.us]:
            self.pending.remove(item)
            item[1]["rx_us"] = CLOCK.us % PERIOD
            self.call(self.engine.handle, item[1])
        self.call(self.engine.run)

    def run_ms(self, ms):
        for unused in range(int(ms)):
            self.step()

    def fired_at(self, mask):
        return [t for t, m in self.node.fired if m == mask]


class ChaseTests(unittest.TestCase):
    def test_chase_start_waits_for_timecode(self):
        show = Show(self)
        self.assertEqual(show.start(), ("ack", nfcp.CMD_SCRIPT_START, nfcp.ACK_CHASE))
        show.run_ms(2500)
        self.assertEqual(show.node.fired, [])
        self.assertTrue(show.engine.running)
        self.assertEqual(show.engine.chase, "wait")

    def test_cues_follow_the_audio(self):
        show = Show(self)
        show.start()
        show.run_ms(700)
        play_at = show.elapsed()
        show.play()
        show.run_ms(3200)
        # Cue 0 is due at once; the first SHOW_TIME arrives delay_ms later.
        self.assertEqual(len(show.fired_at(0x1)), 1)
        self.assertLessEqual(show.fired_at(0x1)[0] - play_at, show.delay_ms + 2)
        for event_ms, mask in CUES[1:5]:
            times = show.fired_at(mask)
            self.assertEqual(len(times), 1, hex(mask))
            self.assertAlmostEqual(times[0] - play_at, event_ms, delta=2)

    def test_audio_offset_moves_cues(self):
        show = Show(self)
        show.start(audio_offset_ms=150)
        show.play()
        play_at = show.elapsed()
        show.run_ms(1300)
        self.assertAlmostEqual(show.fired_at(0x4)[0] - play_at, 1150, delta=2)
        show = Show(self)
        show.start(audio_offset_ms=-150)
        show.play()
        play_at = show.elapsed()
        show.run_ms(1300)
        self.assertAlmostEqual(show.fired_at(0x4)[0] - play_at, 850, delta=2)

    def test_follows_a_drifting_box(self):
        # 500 ppm: 30 ms off after a minute if nothing corrected it.
        for ppm in (500.0, -500.0):
            show = Show(self, box_ppm=ppm)
            show.start()
            show.play()
            play_at = show.elapsed()
            show.run_ms(60500)
            box_time = 60000 / (1.0 + ppm * 1e-6)
            self.assertAlmostEqual(show.fired_at(0x80)[0] - play_at, box_time, delta=3)
            self.assertEqual(show.engine.chase_jumps, 0)

    def test_forward_jump_skips_cues(self):
        show = Show(self)
        show.start()
        show.play()
        show.run_ms(1500)
        show.position_ms += 4000          # track skips from 1.5 s to 5.5 s
        jump_at = show.elapsed()
        show.run_ms(1000)
        self.assertEqual(show.fired_at(0x8), [])     # 2 s: skipped
        self.assertEqual(show.fired_at(0x10), [])    # 3 s: skipped
        self.assertAlmostEqual(show.fired_at(0x20)[0] - jump_at, 500, delta=101)
        self.assertEqual(show.engine.chase_jumps, 1)
        self.assertIn("skip 2 cues", show.out.getvalue())

    def test_backward_jump_never_fires_twice(self):
        show = Show(self)
        show.start()
        show.play()
        show.run_ms(2500)
        self.assertEqual(len(show.fired_at(0x8)), 1)
        show.position_ms -= 2000          # rewind from 2.5 s to 0.5 s
        back_at = show.elapsed()
        show.run_ms(3200)
        for mask in (0x1, 0x2, 0x4, 0x8):
            self.assertEqual(len(show.fired_at(mask)), 1, hex(mask))
        # 3 s cue now plays 2.5 s after the rewind (0.5 s -> 3 s).
        self.assertAlmostEqual(show.fired_at(0x10)[0] - back_at, 2500, delta=101)

    def test_small_errors_are_corrected_gently(self):
        show = Show(self)
        show.start()
        show.play()
        show.run_ms(500)
        start = show.engine.start_ms
        show.position_ms += 40            # 40 ms: corrected, not a jump
        show.run_ms(1000)
        self.assertEqual(show.engine.chase_jumps, 0)
        moved = module_script.time.ticks_diff(start, show.engine.start_ms)
        self.assertAlmostEqual(moved, 40, delta=3)

    def test_one_message_moves_the_clock_at_most_one_step(self):
        for shift in (200, -200):         # under the jump limit
            show = Show(self)
            show.start()
            show.play()
            show.run_ms(500)
            start = show.engine.start_ms
            show.position_ms += shift
            show.run_ms(100)              # one SHOW_TIME
            moved = module_script.time.ticks_diff(start, show.engine.start_ms)
            self.assertEqual(abs(moved), show.engine.CHASE_STEP_MS, shift)
            show.run_ms(3000)
            moved = module_script.time.ticks_diff(start, show.engine.start_ms)
            self.assertAlmostEqual(moved, shift, delta=3)
            self.assertEqual(show.engine.chase_jumps, 0)

    def test_timecode_lost_stops_the_show(self):
        show = Show(self)
        show.start()
        show.play()
        show.run_ms(1200)
        show.send_show_time = False
        show.run_ms(show.engine.chase_timeout_ms + 200)
        # The 2 s cue came while the module ran on its own clock.
        self.assertEqual(len(show.fired_at(0x8)), 1)
        self.assertFalse(show.engine.running)
        self.assertGreaterEqual(show.node.stops, 1)
        self.assertIn("timecode lost", show.out.getvalue())
        show.send_show_time = True
        show.run_ms(2000)
        self.assertEqual(show.fired_at(0x10), [])

    def test_timecode_never_came(self):
        show = Show(self)
        show.start()
        show.run_ms(show.engine.chase_wait_ms + 200)
        self.assertFalse(show.engine.running)
        self.assertIn("timecode never came", show.out.getvalue())

    def test_other_sources_and_tracks_are_ignored(self):
        for source, track in ((0x0BAD0BAD, TRACK), (BOX, TRACK + 1)):
            show = Show(self)
            show.start()
            show.source, show.track = source, track
            show.play()
            show.run_ms(1500)
            self.assertEqual(show.node.fired, [])
            self.assertEqual(show.engine.chase, "wait")

    def test_pause_holds_and_resumes_without_a_burst(self):
        show = Show(self)
        show.start()
        show.play()
        show.run_ms(1500)
        show.state = nfcp.SHOW_PAUSED
        show.run_ms(4000)                  # long pause; the box keeps sending
        self.assertEqual(show.fired_at(0x8), [])
        self.assertTrue(show.engine.running)
        show.state = nfcp.SHOW_PLAYING
        resume_at = show.elapsed()
        show.run_ms(1000)
        self.assertEqual(len(show.fired_at(0x8)), 1)
        # Paused at ~1.5 s, so the 2 s cue comes ~0.5 s after the resume.
        self.assertAlmostEqual(show.fired_at(0x8)[0] - resume_at, 500, delta=105)

    def test_audio_ended_stops(self):
        show = Show(self)
        show.start()
        show.play()
        show.run_ms(1200)
        show.state = nfcp.SHOW_ENDED
        show.run_ms(300)
        self.assertFalse(show.engine.running)
        self.assertIn("audio ended", show.out.getvalue())

    def test_bridge_heartbeat_still_required(self):
        show = Show(self)
        show.start()
        show.play()
        show.run_ms(1200)
        show.send_heartbeat = False
        show.run_ms(show.engine.heartbeat_timeout_ms + 500)
        self.assertFalse(show.engine.running)
        self.assertIn("heartbeat timeout", show.out.getvalue())

    def test_disarm_stops(self):
        show = Show(self)
        show.start()
        show.play()
        show.run_ms(1200)
        show.node.blocked = (nfcp.NACK_REJECTED_ARMED, "disarmed")
        show.run_ms(50)
        self.assertFalse(show.engine.running)
        self.assertIsNone(show.engine.chase)

    def test_stop_command(self):
        show = Show(self)
        show.start()
        show.play()
        show.run_ms(1200)
        show.call(show.engine.handle, show.pkt(show.master.build_script_stop(show.uid, SCRIPT_ID), BRIDGE))
        show.run_ms(1500)
        self.assertFalse(show.engine.running)
        self.assertEqual(show.fired_at(0x8), [])

    def log_lines(self, show):
        """{"err": ..., "age": ..., "handled": ...} from the printed log."""
        out = {}
        for line in show.out.getvalue().splitlines():
            if line.startswith("SCRIPT CHASE log "):
                key, value = line.rsplit(" ", 1)[1].split("=", 1)
                out[key] = value
        return out

    def test_error_log_is_printed_when_the_show_stops(self):
        show = Show(self, delay_ms=4)
        show.start()
        show.play()
        show.run_ms(1050)                 # SHOW_TIME every 100 ms: 11 messages
        show.position_ms += 20            # one message 20 ms off, then corrected
        show.run_ms(200)
        show.position_ms += 1000          # a jump
        show.run_ms(300)
        show.state = nfcp.SHOW_STOPPED
        show.run_ms(150)
        self.assertFalse(show.engine.running)
        log = self.log_lines(show)
        errs = log["err"].split(",")
        self.assertEqual(errs[0], "L")
        self.assertIn("J", errs)
        numbers = [int(e) for e in errs[1:] if e not in ("L", "J")]
        self.assertTrue(all(abs(e) <= 1 for e in numbers[:9]), errs)
        self.assertIn(-20, numbers)                 # the shifted message, before correction
        self.assertEqual(log["age"].split(",")[:3], ["4", "4", "4"])
        self.assertEqual(len(log["handled"].split(",")), len(errs))
        self.assertIn("SCRIPT CHASE log n=%d " % len(errs), show.out.getvalue())
        self.assertEqual(show.engine.chase_log, [])

    def test_error_log_is_bounded_and_printed_on_stop_command(self):
        show = Show(self)
        show.start()
        show.play()
        show.run_ms(6000)                 # 60 messages, the log keeps 48
        self.assertEqual(len(show.engine.chase_log), module_script.ScriptEngine.CHASE_LOG_MAX)
        show.call(show.engine.handle, show.pkt(show.master.build_script_stop(show.uid, SCRIPT_ID), BRIDGE))
        self.assertIn("SCRIPT CHASE log n=48 ", show.out.getvalue())
        # A new show starts a new log.
        show.start()
        self.assertEqual(show.engine.chase_log, [])

    def test_start_refused_when_disarmed(self):
        show = Show(self)
        show.node.blocked = (nfcp.NACK_REJECTED_ARMED, "disarmed")
        self.assertEqual(show.start(), ("nack", nfcp.CMD_SCRIPT_START, nfcp.NACK_REJECTED_ARMED))
        self.assertFalse(show.engine.running)

    def test_start_without_a_source_is_refused(self):
        show = Show(self)
        self.assertEqual(show.start(source=0), ("nack", nfcp.CMD_SCRIPT_START, nfcp.NACK_INVALID_VALUE))
        self.assertFalse(show.engine.running)

    def test_plain_start_is_unchanged(self):
        show = Show(self)
        show.start()
        frame = show.master.build_script_start(show.uid, SCRIPT_ID, 100, 0)
        show.call(show.engine.handle, show.pkt(frame, BRIDGE))
        self.assertEqual(show.link.sent[-1], ("ack", nfcp.CMD_SCRIPT_START, nfcp.ACK_OK))
        self.assertIsNone(show.engine.chase)
        start = show.elapsed()
        show.run_ms(700)
        self.assertAlmostEqual(show.fired_at(0x2)[0] - start, 600, delta=2)

    def test_show_time_frame(self):
        frame = nfcp.NFCP(node_id=0x01).build_show_time_broadcast(TRACK, 123456, nfcp.SHOW_PAUSED)
        pkt = decoded(frame)
        self.assertEqual(pkt["cmd"], nfcp.CMD_SHOW_TIME)
        self.assertEqual(pkt["frame_type"], nfcp.SOF_BROADCAST)
        self.assertEqual(pkt["data"], bytes.fromhex("0007") + (123456).to_bytes(4, "big") + b"\x02")


if __name__ == "__main__":
    unittest.main()
