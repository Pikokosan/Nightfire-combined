"""BridgeFrameParser.feed_bytes() must find the same frames as feed().

    py -3 scripts/tests/test_bridge_frame_parser.py
"""

import os
import random
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "main"))
import bridge_radio  # noqa: E402


def as_tuples(frames):
    return [(f.flags, f.seq, f.cmd, bytes(f.payload)) for f in frames]


def per_byte(data):
    parser = bridge_radio.BridgeFrameParser()
    out = []
    for b in data:
        frame = parser.feed(b)
        if frame:
            out.append(frame)
    return as_tuples(out)


def bulk(data, rng):
    parser = bridge_radio.BridgeFrameParser()
    out = []
    i = 0
    while i < len(data):
        n = rng.randrange(1, 80)
        out += parser.feed_bytes(data[i:i + n])
        i += n
    return as_tuples(out)


def random_frame(rng):
    payload = bytes(rng.randrange(256) for _ in range(rng.randrange(0, 60)))
    return bytes(bridge_radio.build_frame(rng.randrange(256), rng.randrange(256), payload, rng.randrange(256)))


def noise(rng):
    # No SOF0 in noise, so per-byte and bulk parsing must agree exactly.
    return bytes(rng.choice([b for b in range(256) if b != bridge_radio.SOF0]) for _ in range(rng.randrange(0, 12)))


class FrameParserTests(unittest.TestCase):
    def test_valid_frames_with_noise_and_random_chunks(self):
        rng = random.Random(1)
        for trial in range(300):
            count = rng.randrange(1, 6)
            data = b"".join(noise(rng) + random_frame(rng) for _ in range(count)) + noise(rng)
            expected = per_byte(data)
            self.assertEqual(len(expected), count, trial)    # every frame found
            self.assertEqual(bulk(data, rng), expected, trial)

    def test_corrupted_frame_is_dropped_and_next_found(self):
        rng = random.Random(2)
        for trial in range(200):
            good = random_frame(rng)
            bad = bytearray(random_frame(rng))
            bad[-1] ^= 0x01                  # wrong CRC
            data = bytes(bad) + noise(rng) + good
            frames = bulk(data, rng)
            self.assertEqual(frames[-1:], per_byte(good), trial)

    def test_partial_frame_waits(self):
        parser = bridge_radio.BridgeFrameParser()
        frame = bytes(bridge_radio.build_send_nfcp(5, b"\xff\xfd\x03\x01\x40\x12\x34"))
        self.assertEqual(parser.feed_bytes(frame[:4]), [])
        self.assertEqual(parser.feed_bytes(frame[4:-1]), [])
        self.assertEqual(as_tuples(parser.feed_bytes(frame[-1:])), per_byte(frame))

    def test_back_to_back_frames_in_one_call(self):
        rng = random.Random(3)
        frames = [random_frame(rng) for _ in range(5)]
        parser = bridge_radio.BridgeFrameParser()
        self.assertEqual(as_tuples(parser.feed_bytes(b"".join(frames))), per_byte(b"".join(frames)))


if __name__ == "__main__":
    unittest.main()
