"""LTC encoder and decoder (audio_box/main/ltc.py) and the test-track
generator (audio_box/tools/make_ltc_track.py).

The decoder is fed edge times two ways: straight from the encoder, and from
generated audio through an emulated comparator (zero crossings, linearly
interpolated, like the box's comparator and hardware timer would see them).

Run: py -3 scripts/tests/test_ltc.py
"""
import random
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT / "audio_box" / "main"), str(ROOT / "audio_box" / "tools")]
import ltc  # noqa: E402
import make_ltc_track  # noqa: E402

PERIOD = 1 << 30


def crossings_us(samples, rate, offset_us=0.0, hysteresis=0.0):
    """Edge times of a comparator on the signal (with optional hysteresis)."""
    out = []
    high = samples[0] > 0
    for i in range(1, len(samples)):
        a, b = samples[i - 1], samples[i]
        if high and b < -hysteresis or not high and b > hysteresis:
            level = -hysteresis if high else hysteresis
            x = (level - a) / (b - a) if b != a else 0.0
            out.append(offset_us + (i - 1 + x) * 1e6 / rate)
            high = not high
    return out


def decode(edges, fps=30, offset_us=0, period=PERIOD):
    d = ltc.LtcDecoder(fps, period)
    frames = []
    for e in edges:
        f = d.feed(int(round(e + offset_us)) % period)
        if f:
            frames.append((f, d.locked))
    return d, frames


class EncoderTests(unittest.TestCase):
    def test_frame_bits(self):
        bits = ltc.ltc_frame_bits(12, 34, 56, 29, fps=30)
        self.assertEqual(bits[64:], list(ltc.LTC_SYNC))
        val = lambda s, n: sum(bits[s + i] << i for i in range(n))
        self.assertEqual((val(0, 4), val(8, 2)), (9, 2))       # frames 29
        self.assertEqual((val(16, 4), val(24, 3)), (6, 5))     # seconds 56
        self.assertEqual((val(32, 4), val(40, 3)), (4, 3))     # minutes 34
        self.assertEqual((val(48, 4), val(56, 2)), (2, 1))     # hours 12
        for tc in ((0, 0, 0, 0), (1, 2, 3, 4), (23, 59, 59, 29)):
            self.assertEqual(sum(ltc.ltc_frame_bits(*tc, fps=30)) % 2, 0)     # polarity correction
            self.assertEqual(sum(ltc.ltc_frame_bits(*tc[:3], 24, fps=25)) % 2, 0)

    def test_timecode_at(self):
        self.assertEqual(ltc.ltc_timecode_at(0, 30), (0, 0, 0, 0))
        self.assertEqual(ltc.ltc_timecode_at(30 * 3661 + 7, 30), (1, 1, 1, 7))
        self.assertEqual(ltc.ltc_frame_ms(0, 0, 2, 15, 30), 2500)


class DecoderTests(unittest.TestCase):
    def check_frames(self, frames, fps, first_frame, edges_t0_us=0.0, tol_us=2, speed=1.0):
        frame_us = 1e6 / fps / speed
        self.assertGreater(len(frames), 3)
        for (pos, t), locked in frames:
            n = round(pos * fps / 1000) - 1 - first_frame   # frames since the first one
            self.assertEqual(pos, (first_frame + n + 1) * 1000 // fps)
            self.assertAlmostEqual(t, edges_t0_us + (n + 1) * frame_us, delta=tol_us)

    def test_clean_edges_every_rate(self):
        for fps in ltc.LTC_RATES:
            edges = ltc.LtcEncoder(fps).edge_times_us(100, 20)
            d, frames = decode(edges, fps)
            # The last frame ends with the next frame's first edge, which
            # isn't there: 19 of the 20 frames complete.
            self.assertEqual(len(frames), 19)
            self.check_frames(frames, fps, 100)
            self.assertTrue(frames[-1][1])
            self.assertEqual(d.errors, 0)

    def test_lock_needs_two_frames_in_sequence(self):
        d, frames = decode(ltc.LtcEncoder(30).edge_times_us(0, 4))
        self.assertEqual([locked for f, locked in frames][:2], [False, True])

    def test_position_between_frames(self):
        edges = ltc.LtcEncoder(30).edge_times_us(0, 10)
        d, frames = decode(edges)
        pos, t = d.frame
        self.assertEqual(d.position_ms(t + 12000), pos + 12)
        d.gap()
        self.assertIsNone(d.position_ms(t))

    def test_counter_wrap(self):
        edges = ltc.LtcEncoder(30).edge_times_us(0, 30)
        start = PERIOD - 300000                        # wraps ~10 frames in
        d, frames = decode(edges, offset_us=start)
        self.assertEqual(len(frames), 29)
        for (pos, t), locked in frames:
            n = round(pos * 30 / 1000) - 1
            self.assertAlmostEqual((t - start) % PERIOD, (n + 1) * 1e6 / 30, delta=2)

    def test_follows_speed_changes(self):
        for speed in (0.97, 1.03):
            edges = ltc.LtcEncoder(30).edge_times_us(0, 60, speed=speed)
            d, frames = decode(edges)
            self.check_frames(frames, 30, 0, speed=speed)
            self.assertEqual(d.errors, 0)

    def test_starting_mid_frame(self):
        edges = ltc.LtcEncoder(30).edge_times_us(0, 12)
        cut = len(edges) // 3 + 1                      # somewhere inside frame 4
        d, frames = decode(edges[cut:])
        # Frames 5-10 complete: 4 was cut, 11 has no end edge.
        self.assertEqual(len(frames), 6)
        self.check_frames(frames, 30, 0)

    def test_dropout_unlocks_then_relocks(self):
        edges = ltc.LtcEncoder(30).edge_times_us(0, 40)
        third = len(edges) // 3
        cut = [e for i, e in enumerate(edges) if not third <= i < 2 * third]
        d = ltc.LtcDecoder(30)
        seen = []
        for e in cut:
            f = d.feed(int(e))
            if f:
                seen.append((f[0], d.locked))
        jump = [i for i in range(1, len(seen)) if seen[i][0] - seen[i - 1][0] > 40]
        self.assertEqual(len(jump), 1)
        self.assertFalse(seen[jump[0]][1])             # first frame after the gap: not locked
        self.assertTrue(seen[-1][1])

    def test_noise_edges_are_survived(self):
        rng = random.Random(3)
        edges = ltc.LtcEncoder(30).edge_times_us(0, 60)
        # A spurious pair of edges (a glitch) in every 5th frame.
        noisy = list(edges)
        for k in range(5, 60, 5):
            t = k * 1e6 / 30 + 5000 + rng.random() * 1000
            noisy += [t, t + 3]
        noisy.sort()
        d, frames = decode(noisy)
        self.assertGreater(len(frames), 30)
        good = [f for f, locked in frames if locked]
        self.assertGreater(len(good), 25)
        for pos, t in good:
            n = round(pos * 30 / 1000) - 1
            self.assertAlmostEqual(t, (n + 1) * 1e6 / 30, delta=2)

    def test_garbage_gives_nothing(self):
        rng = random.Random(5)
        t, edges = 0.0, []
        for unused in range(20000):
            t += rng.choice((208.3, 416.7)) * rng.uniform(0.9, 1.1)
            edges.append(t)
        d, frames = decode(edges)
        self.assertEqual([f for f, locked in frames if locked], [])


class AudioTests(unittest.TestCase):
    """Generated WAV samples -> comparator -> decoder."""

    def run_track(self, **kw):
        rate = kw.pop("rate", 48000)
        left, right = make_ltc_track.build(2.0, rate=rate, **kw)
        edges = crossings_us(right, rate, hysteresis=kw.pop("hyst", 0.0) if "hyst" in kw else 0.0)
        return decode(edges, kw.get("fps", 30))

    def test_clean_track(self):
        d, frames = self.run_track()
        self.assertGreaterEqual(len(frames), 58)
        DecoderTests.check_frames(self, frames, 30, 0, tol_us=15)

    def test_44k1_and_25_fps(self):
        d, frames = self.run_track(rate=44100, fps=25)
        DecoderTests.check_frames(self, frames, 25, 0, tol_us=15)

    def test_inverted_and_quiet(self):
        d, frames = self.run_track(invert=True, level_db=-30.0)
        DecoderTests.check_frames(self, frames, 30, 0, tol_us=15)

    def test_noisy_track_with_comparator_hysteresis(self):
        left, right = make_ltc_track.build(2.0, noise_db=-40.0, level_db=-10.0)
        edges = crossings_us(right, 48000, hysteresis=0.05)
        d, frames = decode(edges)
        good = [(f, l) for f, l in frames if l]
        self.assertGreater(len(good), 50)
        DecoderTests.check_frames(self, good, 30, 0, tol_us=20)

    def test_clicks_on_the_left(self):
        left, right = make_ltc_track.build(2.0, cues=[500, 1500])
        loud = [i for i, v in enumerate(left) if abs(v) > 0.05]
        self.assertAlmostEqual(loud[0] / 48.0, 500, delta=1)
        self.assertTrue(any(abs(i / 48.0 - 1500) < 1 for i in loud))


if __name__ == "__main__":
    unittest.main()
