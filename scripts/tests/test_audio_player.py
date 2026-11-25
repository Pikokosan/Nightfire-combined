"""ESP32 audio player (audio_box/player/player_main.py) on the PC.

A fake I2S drains its DMA buffer in real time on a fake clock and blocks a
write while the buffer is full, like machine.I2S. Tracks are real WAV files
from audio_box/tools/make_ltc_track.py, so the LTC in what the player
outputs can be decoded and checked against where it was told to play.

Run: py -3 scripts/tests/test_audio_player.py
"""
import os
import struct
import sys
import tempfile
import unittest
import wave
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT / "audio_box" / "player"), str(ROOT / "audio_box" / "main"),
                str(ROOT / "audio_box" / "tools"), str(ROOT / "scripts" / "tests")]
import player_main as pm  # noqa: E402
import make_ltc_track  # noqa: E402
import ltc  # noqa: E402
from test_ltc import crossings_us  # noqa: E402

CLOCK = {"ms": 0}
TRACK_S = 3.0


class FakeI2S:
    """DMA buffer of `capacity` bytes, drained at rate*4 bytes a second."""

    instances = []

    def __init__(self, rate, capacity=pm.I2S_BUFFER_BYTES):
        self.rate = rate
        self.capacity = capacity
        self.buffered = 0
        self.out = bytearray()       # bytes that reached the DAC, in order
        self.pending = bytearray()
        self.closed = False
        self.carry = 0.0
        FakeI2S.instances.append(self)

    def drain(self, ms):
        self.carry += ms * self.rate * 4 / 1000
        n = int(self.carry) // 4 * 4
        self.carry -= n
        n = min(n, len(self.pending))
        self.out += self.pending[:n]
        del self.pending[:n]
        self.buffered = len(self.pending)

    def write(self, buf):
        assert not self.closed
        data = bytes(buf)
        while self.buffered + len(data) > self.capacity:
            advance(1)
        self.pending += data
        self.buffered = len(self.pending)
        return len(data)

    def deinit(self):
        self.closed = True


def advance(ms):
    CLOCK["ms"] += ms
    for i2s in FakeI2S.instances:
        if not i2s.closed:
            i2s.drain(ms)


def write_track(path, seconds=TRACK_S, rate=48000, fps=30, start_frame=0):
    left, right = make_ltc_track.build(seconds, rate=rate, fps=fps, start_frame=start_frame)
    make_ltc_track.write_wav(path, left, right, rate)


def write_bad(path, channels=1, bits=16, rate=48000):
    with wave.open(str(path), "wb") as w:
        w.setnchannels(channels)
        w.setsampwidth(bits // 8)
        w.setframerate(rate)
        w.writeframes(bytes(1000))


class PlayerTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.dir = tempfile.mkdtemp(prefix="player-")
        write_track(os.path.join(cls.dir, "007.wav"))
        write_track(os.path.join(cls.dir, "008.wav"), rate=44100, seconds=1.0)
        write_bad(os.path.join(cls.dir, "009.wav"), channels=1)
        write_bad(os.path.join(cls.dir, "010.wav"), bits=8)
        with open(os.path.join(cls.dir, "011.wav"), "wb") as f:
            f.write(b"not a wav at all")

    def setUp(self):
        FakeI2S.instances = []
        self.mutes = []
        self.player = pm.AudioPlayer(
            lambda name: open(os.path.join(self.dir, name), "rb"), FakeI2S,
            clock=lambda: CLOCK["ms"], diff=lambda a, b: a - b, mute=self.mutes.append)
        self.proto = pm.LineProtocol(self.player, lambda: pm.list_tracks(os.listdir, self.dir))

    def tearDown(self):
        self.player.close()

    def run_ms(self, ms):
        end = CLOCK["ms"] + ms
        while CLOCK["ms"] < end:
            if not self.player.service():
                advance(1)

    def ask(self, line):
        out = self.proto.feed((line + "\n").encode())
        self.assertEqual(len(out), 1, out)
        return out[0]

    def data_of(self, name):
        with open(os.path.join(self.dir, name), "rb") as f:
            rate, start, size = pm.wav_info(f)
            f.seek(start)
            return rate, f.read(size)

    def true_position_ms(self):
        i2s = FakeI2S.instances[-1]
        return self.player.start_ms + len(i2s.out) / (i2s.rate * 4 / 1000)


class WavTests(PlayerTest):
    def test_header(self):
        with open(os.path.join(self.dir, "007.wav"), "rb") as f:
            rate, start, size = pm.wav_info(f)
        self.assertEqual((rate, start, size), (48000, 44, int(TRACK_S * 48000) * 4))
        for name in ("009.wav", "010.wav", "011.wav"):
            with open(os.path.join(self.dir, name), "rb") as f:
                with self.assertRaises(pm.WavError):
                    pm.wav_info(f)

    def test_skips_other_chunks(self):
        path = os.path.join(self.dir, "012.wav")
        rate, data = self.data_of("007.wav")
        fmt = struct.pack("<HHIIHH", 1, 2, 48000, 192000, 4, 16)
        body = (b"WAVE" + b"fmt " + struct.pack("<I", 16) + fmt + b"LIST" + struct.pack("<I", 5) + b"abcde\x00" +
                b"data" + struct.pack("<I", 4000) + data[:4000])
        with open(path, "wb") as f:
            f.write(b"RIFF" + struct.pack("<I", len(body)) + body)
        with open(path, "rb") as f:
            self.assertEqual(pm.wav_info(f), (48000, 12 + 8 + 16 + 8 + 6 + 8, 4000))
        os.remove(path)


class CommandTests(PlayerTest):
    def test_ping_list_load(self):
        self.assertEqual(self.ask("PING"), "PONG audio-player 0.1.0")
        self.assertEqual(self.ask("LIST"), "TRACKS 7,8,9,10,11")
        self.assertEqual(self.ask("LOAD 7"), "OK LOAD 7 3000")
        self.assertEqual(self.ask("LOAD 8"), "OK LOAD 8 1000")
        self.assertEqual(self.ask("STATUS"), "STATUS 8 0 0 1000")
        self.assertEqual(self.ask("LOAD 3"), "ERR LOAD 3 nofile")
        self.assertEqual(self.ask("LOAD 9"), "ERR LOAD 9 format")
        self.assertEqual(self.ask("LOAD 11"), "ERR LOAD 11 format")
        self.assertEqual(self.ask("LOAD 0"), "ERR LOAD 0 range")

    def test_bad_lines(self):
        for line in ("HELLO", "PLAY 7", "PLAY x 0", "LOAD", "STOP now", ""):
            out = self.proto.feed((line + "\n").encode())
            self.assertEqual(out, [] if not line else ["ERR ?"], line)
        # A line split across reads, and two in one read.
        self.assertEqual(self.proto.feed(b"PI"), [])
        self.assertEqual(self.proto.feed(b"NG\nSTATUS\n"), ["PONG audio-player 0.1.0", "STATUS 0 0 0 0"])
        # 300 bytes without a newline can't be a command: dropped.
        self.assertEqual(self.proto.feed(b"x" * 300), [])
        self.assertEqual(self.proto.feed(b"\nPING\n"), ["PONG audio-player 0.1.0"])

    def test_play_refusals(self):
        self.assertEqual(self.ask("PLAY 7 3000"), "ERR PLAY 7 range")
        self.assertEqual(self.ask("PLAY 3 0"), "ERR PLAY 3 nofile")
        self.assertEqual(self.ask("PLAY 10 0"), "ERR PLAY 10 format")
        self.assertEqual(self.ask("PLAY 7 0"), "OK PLAY 7")
        self.assertEqual(self.ask("LOAD 8"), "ERR LOAD 8 busy")


class PlaybackTests(PlayerTest):
    def test_output_is_the_track_from_the_start(self):
        rate, data = self.data_of("007.wav")
        self.assertEqual(self.ask("PLAY 7 0"), "OK PLAY 7")
        self.run_ms(1000)
        out = FakeI2S.instances[-1].out
        self.assertGreater(len(out), 150000)
        self.assertEqual(bytes(out), data[:len(out)])
        self.assertEqual(self.mutes[-1], False)

    def test_play_from_seeks_and_the_ltc_agrees(self):
        rate, data = self.data_of("007.wav")
        self.assertEqual(self.ask("PLAY 7 1500"), "OK PLAY 7")
        self.run_ms(800)
        out = bytes(FakeI2S.instances[-1].out)
        skip = 1500 * 48 * 4
        self.assertEqual(out, data[skip:skip + len(out)])
        # The LTC in what came out: the first frame it decodes is ~1.5 s in.
        right = [struct.unpack_from("<h", out, i + 2)[0] / 32767 for i in range(0, len(out), 4)]
        d = ltc.LtcDecoder(30)
        first = None
        for e in crossings_us(right, 48000):
            f = d.feed(int(e))
            if f and first is None:
                first = f
        self.assertIsNotNone(first)
        pos, t_us = first
        # Position at the start of the output = frame position - its time in.
        self.assertAlmostEqual(pos - t_us / 1000, 1500, delta=1)

    def test_reported_position_follows_the_dac(self):
        self.ask("PLAY 7 200")
        errors = []
        for unused in range(25):
            self.run_ms(100)
            errors.append(self.player.position_ms() - self.true_position_ms())
        self.assertLess(max(abs(e) for e in errors[1:]), 25, errors)     # within one chunk
        track, pos, state, length = self.player.status()
        self.assertEqual((track, state, length), (7, pm.STATE_PLAYING, 3000))

    def test_track_end(self):
        self.ask("PLAY 7 2500")
        self.run_ms(400)
        self.assertEqual(self.player.state, pm.STATE_PLAYING)
        self.run_ms(300)                  # 500 ms of audio + the buffer's 85 ms
        self.assertEqual(self.ask("STATUS"), "STATUS 7 3000 3 3000")
        self.assertEqual(self.player.events, ["ENDED 7"])
        i2s = FakeI2S.instances[-1]
        self.assertTrue(i2s.closed)
        rate, data = self.data_of("007.wav")
        self.assertEqual(bytes(i2s.out[:500 * 192]), data[2500 * 192:])     # every sample played
        self.assertEqual(self.mutes[-1], True)

    def test_stop(self):
        self.ask("PLAY 7 0")
        self.run_ms(1000)
        self.assertEqual(self.ask("STOP"), "OK STOP")
        i2s = FakeI2S.instances[-1]
        self.assertTrue(i2s.closed)
        track, pos, state, length = self.player.status()
        self.assertEqual(state, pm.STATE_STOPPED)
        self.assertAlmostEqual(pos, 1000, delta=110)
        self.assertEqual(self.ask("STOP"), "OK STOP")       # safe to repeat
        self.assertEqual(self.player.status()[1], pos)
        self.assertEqual(self.mutes[-1], True)

    def test_play_again_jumps(self):
        self.ask("PLAY 7 0")
        self.run_ms(500)
        self.assertEqual(self.ask("PLAY 7 2000"), "OK PLAY 7")
        jumped = CLOCK["ms"]
        self.assertTrue(FakeI2S.instances[0].closed)
        self.run_ms(300)
        # The DAC starts on the new data at once.
        self.assertAlmostEqual(self.true_position_ms(), 2000 + CLOCK["ms"] - jumped, delta=2)
        self.assertAlmostEqual(self.player.position_ms(), self.true_position_ms(), delta=25)

    def test_44k1(self):
        self.ask("PLAY 8 0")
        self.run_ms(1300)
        self.assertEqual(self.player.status()[1:3], (1000, pm.STATE_ENDED))


if __name__ == "__main__":
    unittest.main()
