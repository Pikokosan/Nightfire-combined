"""The audio box's player driver (audio_box/main/player_link.py) against the
real ESP32 player code (audio_box/player/player_main.py), joined by a fake
UART on one fake clock. The ESP32 side's DAC drains in real time, so the
box's position can be compared with what the DAC is really playing.

Run: py -3 scripts/tests/test_player_link.py
"""
import os
import sys
import tempfile
import types
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT / "audio_box" / "main"), str(ROOT / "audio_box" / "player"),
                str(ROOT / "audio_box" / "tools"), str(ROOT / "main")]
PERIOD = 1 << 30
CLOCK = {"us": PERIOD - 2000000}       # ticks wrap 2 s in

fake_time = types.ModuleType("time")
fake_time.ticks_us = lambda: CLOCK["us"] % PERIOD
fake_time.ticks_ms = lambda: (CLOCK["us"] // 1000) % PERIOD
fake_time.ticks_add = lambda a, b: (a + b) % PERIOD
fake_time.ticks_diff = lambda a, b: ((a - b + PERIOD // 2) % PERIOD) - PERIOD // 2
fake_time.sleep_ms = lambda ms: advance(ms)

saved = sys.modules.get("time")
sys.modules["time"] = fake_time
try:
    import player_link  # noqa: E402
    import nfcp  # noqa: E402
finally:
    sys.modules["time"] = saved
import player_main as pm  # noqa: E402
import make_ltc_track  # noqa: E402

ESP = None


def now_ms():
    return CLOCK["us"] / 1000.0


def advance(ms):
    for unused in range(int(ms)):
        CLOCK["us"] += 1000
        if ESP is not None:
            ESP.step()


class DrainingI2S:
    """Never blocks: the fake ESP32 only writes while there is room."""

    def __init__(self, rate):
        self.rate = rate
        self.pending = 0
        self.played = 0            # bytes out of the DAC
        self.closed = False
        self.carry = 0.0

    def drain_ms(self, ms):
        self.carry += ms * self.rate * 4 / 1000
        n = min(int(self.carry) // 4 * 4, self.pending)
        self.carry -= int(self.carry) // 4 * 4
        self.pending -= n
        self.played += n

    def room(self):
        return self.pending + pm.CHUNK_BYTES <= pm.I2S_BUFFER_BYTES

    def write(self, buf):
        self.pending += len(buf)

    def deinit(self):
        self.closed = True


class FakeEsp32:
    """The player firmware's loop, one fake ms at a time. Bytes take 1 ms
    over the UART each way."""

    def __init__(self, track_dir):
        self.i2s = None
        self.player = pm.AudioPlayer(lambda name: open(os.path.join(track_dir, name), "rb"), self.make_i2s,
                                     clock=lambda: int(now_ms()), diff=lambda a, b: a - b)
        self.proto = pm.LineProtocol(self.player)
        self.inbox = []            # (deliver_ms, bytes) to the ESP32
        self.outbox = []           # (deliver_ms, bytes) to the box
        self.alive = True          # False: stops answering and playing (crash)
        self.feeding = True        # False: stops feeding the DAC (stall)
        self.commands = []

    def make_i2s(self, rate):
        self.i2s = DrainingI2S(rate)
        return self.i2s

    def step(self):
        t = now_ms()
        if self.i2s is not None and not self.i2s.closed:
            self.i2s.drain_ms(1)
        if not self.alive:
            return
        for item in [i for i in self.inbox if i[0] <= t]:
            self.inbox.remove(item)
            for line in item[1].decode().splitlines():
                self.commands.append(line)
            for reply in self.proto.feed(item[1]):
                self.outbox.append((t + 1, (reply + "\n").encode()))
        while self.player.events:
            self.outbox.append((t + 1, (self.player.events.pop(0) + "\n").encode()))
        while (self.feeding and self.player.state == pm.STATE_PLAYING and
               (self.i2s.room() or self.player.eof_ms is not None)):
            self.player.service()
            if self.player.eof_ms is not None:
                break

    def dac_position_ms(self):
        return self.player.start_ms + self.i2s.played / (self.i2s.rate * 4 / 1000)


class BoxUart:
    def write(self, data):
        ESP.inbox.append((now_ms() + 1, bytes(data)))

    def arrived(self):
        return [i for i in ESP.outbox if i[0] <= now_ms()]

    def any(self):
        return len(self.arrived())

    def read(self):
        got = self.arrived()
        for item in got:
            ESP.outbox.remove(item)
        return b"".join(i[1] for i in got)


class LinkTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.dir = tempfile.mkdtemp(prefix="link-")
        for track, seconds in ((7, 6.0), (8, 2.0)):
            left, right = make_ltc_track.build(seconds)
            make_ltc_track.write_wav(os.path.join(cls.dir, "%03d.wav" % track), left, right)

    def setUp(self):
        global ESP
        ESP = FakeEsp32(self.dir)
        self.link = player_link.UartPlayer(BoxUart(), sleep_ms=advance)
        self.saved = sys.modules.get("time")
        player_link.time = fake_time
        self.out = []
        player_link.print = lambda *a: self.out.append(" ".join(str(x) for x in a))

    def tearDown(self):
        del player_link.print

    def run_ms(self, ms):
        """The box loop: advance() every ms. Returns the ms advance() said True."""
        events = []
        for unused in range(int(ms)):
            advance(1)
            if self.link.advance(fake_time.ticks_us()):
                events.append(now_ms())
        return events


class LoadPlayTests(LinkTest):
    def test_load(self):
        self.assertEqual(self.link.load(7), nfcp.ACK_OK)
        self.assertEqual((self.link.track, self.link.length_ms), (7, 6000))
        self.assertEqual(self.link.load(3), nfcp.NACK_INVALID_VALUE)
        self.assertEqual(self.link.play(7, 0, fake_time.ticks_us()), nfcp.ACK_OK)
        self.assertEqual(self.link.load(8), nfcp.NACK_BUSY)

    def test_play_follows_the_dac(self):
        self.assertEqual(self.link.play(7, 1000, fake_time.ticks_us()), nfcp.ACK_OK)
        self.assertEqual(ESP.commands[-1], "PLAY 7 1000")
        for unused in range(6):
            self.run_ms(500)
            self.assertLess(abs(self.link.position_ms() - ESP.dac_position_ms()), 5)
        self.assertEqual(self.link.state, nfcp.SHOW_PLAYING)
        self.assertIsNone(self.link.fault)
        polls = ESP.commands.count("STATUS")
        self.assertGreaterEqual(polls, 5)            # one every 500 ms
        self.assertIsNotNone(self.link.player_pos)

    def test_play_checks_range_without_asking_the_player(self):
        self.link.load(8)
        sent = len(ESP.commands)
        self.assertEqual(self.link.play(8, 2000, fake_time.ticks_us()), nfcp.NACK_INVALID_VALUE)
        self.assertEqual(len(ESP.commands), sent)

    def test_play_another_track_while_playing(self):
        self.link.play(7, 0, fake_time.ticks_us())
        self.run_ms(300)
        self.assertEqual(self.link.play(8, 500, fake_time.ticks_us()), nfcp.ACK_OK)
        self.assertEqual(ESP.commands[-3:], ["STOP", "LOAD 8", "PLAY 8 500"])
        self.assertEqual((self.link.track, self.link.length_ms), (8, 2000))
        self.run_ms(400)
        self.assertLess(abs(self.link.position_ms() - ESP.dac_position_ms()), 5)

    def test_player_not_answering_play(self):
        ESP.alive = False
        self.link.lengths[7] = 6000
        self.assertEqual(self.link.play(7, 0, fake_time.ticks_us()), nfcp.NACK_INTERNAL_ERROR)
        self.assertEqual(self.link.state, nfcp.SHOW_STOPPED)


class EndAndStopTests(LinkTest):
    def test_track_end(self):
        self.link.play(8, 1000, fake_time.ticks_us())
        events = self.run_ms(1500)
        self.assertEqual(len(events), 1)
        self.assertEqual(self.link.state, nfcp.SHOW_ENDED)
        self.assertEqual(self.link.position_ms(), 2000)
        self.assertFalse(self.link.stopped_now)
        start = events[0] - 1000                     # ~1 s of audio played
        self.assertLess(abs(events[0] - (now_ms() - 1500 + 1000)), 20)

    def test_player_ending_early_ends_the_track(self):
        # The player knows best when the audio ran out (its ENDED, or a
        # STATUS saying ended), even before the box's clock gets there.
        for line in ("ENDED 7\n", "STATUS 7 3100 3 6000\n"):
            setUp = self.setUp
            setUp()
            self.link.play(7, 0, fake_time.ticks_us())
            self.run_ms(3000)
            ESP.outbox.append((now_ms(), line.encode()))
            events = self.run_ms(5)
            self.assertEqual(len(events), 1, line)
            self.assertEqual(self.link.state, nfcp.SHOW_ENDED, line)
            self.assertIsNone(self.link.fault)

    def test_player_busy_on_load(self):
        # The box thinks nothing plays, but the player is (e.g. after a
        # restart of the box): its "busy" comes back as NACK_BUSY.
        ESP.player.play(7, 0)
        self.assertEqual(self.link.load(8), nfcp.NACK_BUSY)

    def test_stop(self):
        self.link.play(7, 0, fake_time.ticks_us())
        self.run_ms(800)
        self.assertTrue(self.link.stop(fake_time.ticks_us()))
        self.assertEqual(ESP.player.state, pm.STATE_STOPPED)
        self.assertTrue(ESP.i2s.closed)
        self.assertEqual(self.link.state, nfcp.SHOW_STOPPED)
        self.assertAlmostEqual(self.link.position_ms(), 800, delta=10)
        self.assertEqual(self.run_ms(500), [])

    def test_stop_with_a_dead_player_still_stops_the_box(self):
        self.link.play(7, 0, fake_time.ticks_us())
        self.run_ms(300)
        ESP.alive = False
        self.assertTrue(self.link.stop(fake_time.ticks_us()))
        self.assertEqual(self.link.state, nfcp.SHOW_STOPPED)


class FaultTests(LinkTest):
    def test_player_stops_answering(self):
        self.link.play(7, 0, fake_time.ticks_us())
        self.run_ms(1000)
        died = now_ms()
        ESP.alive = False
        events = self.run_ms(2000)
        self.assertEqual(len(events), 1)
        self.assertEqual(self.link.state, nfcp.SHOW_STOPPED)
        self.assertTrue(self.link.stopped_now or self.link.fault)
        self.assertEqual(self.link.fault, "not answering")
        self.assertLessEqual(events[0] - died, 1500)
        self.assertIn("AUDIO player fault: not answering", self.out)

    def test_player_stalls(self):
        self.link.play(7, 0, fake_time.ticks_us())
        self.run_ms(1000)
        stalled = now_ms()
        ESP.feeding = False                           # the DAC runs dry; STATUS still answers
        events = self.run_ms(1500)
        self.assertEqual(len(events), 1)
        self.assertTrue(self.link.fault.startswith("player -"), self.link.fault)
        # Off by more than 150 ms, seen at the next poll (every 500 ms).
        self.assertLessEqual(events[0] - stalled, 150 + 85 + 500 + 50)

    def test_player_says_stopped(self):
        self.link.play(7, 0, fake_time.ticks_us())
        self.run_ms(600)
        ESP.player.stop()                             # e.g. its own error path
        events = self.run_ms(700)
        self.assertEqual(len(events), 1)
        self.assertEqual(self.link.fault, "player stopped")

    def test_no_false_alarms_over_a_whole_track(self):
        self.link.play(7, 0, fake_time.ticks_us())
        events = self.run_ms(6500)
        self.assertEqual(len(events), 1)
        self.assertEqual(self.link.state, nfcp.SHOW_ENDED)
        self.assertIsNone(self.link.fault)


if __name__ == "__main__":
    unittest.main()
