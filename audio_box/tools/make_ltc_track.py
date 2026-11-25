"""Make a test track for the audio box (audio_box/design.md, step 4).

A stereo 16-bit WAV: SMPTE LTC on the right channel starting at 00:00:00:00
at the start of the file, and on the left a short click at every cue time
(or every second), so a module's output and the music can be compared on a
scope.

    py -3 audio_box/tools/make_ltc_track.py track007.wav --seconds 60 --cues 500,1500,3000

The LTC is a square wave with ~25 µs edges (SMPTE allows 20-30 µs), at
-10 dBFS by default (keep it well below the music so it doesn't bleed into
the left channel). Options for decoder tests: --start-frame, --speed,
--noise, --invert.
"""
import argparse
import math
import random
import struct
import sys
import wave
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "main"))
from ltc import LtcEncoder  # noqa: E402


def ltc_samples(edges_us, total, rate, amplitude, rise_us=25.0, invert=False):
    """Sample the LTC square wave: the level flips at each edge time, each
    flip a linear ramp rise_us long centred on it."""
    out = [0.0] * total
    half = rise_us * 1e-6 * rate / 2
    level = 1.0 if invert else -1.0      # before the first edge
    i = 0
    for e_us in edges_us:
        e = e_us * 1e-6 * rate
        start, end = e - half, e + half
        while i < total and i < start:
            out[i] = level * amplitude
            i += 1
        new = -level
        while i < total and i <= end:
            out[i] = (level + (new - level) * (i - start) / (end - start)) * amplitude
            i += 1
        level = new
    while i < total:
        out[i] = level * amplitude
        i += 1
    return out


def click_samples(times_ms, total, rate, amplitude):
    """A 10 ms, 2 kHz click with a sharp start at each time."""
    out = [0.0] * total
    length = int(0.010 * rate)
    for t in times_ms:
        start = int(t * rate / 1000)
        for j in range(length):
            if start + j < total:
                out[start + j] += amplitude * math.sin(2 * math.pi * 2000 * j / rate) * (1 - j / length)
    return out


def build(seconds, rate=48000, fps=30, level_db=-10.0, click_db=-6.0, cues=None, start_frame=0,
          speed=1.0, noise_db=None, invert=False, seed=1):
    """(left, right) float samples in -1..1."""
    total = int(seconds * rate)
    frames = int(seconds * fps * speed) + 2
    edges = LtcEncoder(fps).edge_times_us(start_frame, frames, speed=speed)
    right = ltc_samples(edges, total, rate, 10 ** (level_db / 20), invert=invert)
    times = cues if cues is not None else [1000 * s for s in range(int(seconds))]
    left = click_samples(times, total, rate, 10 ** (click_db / 20))
    if noise_db is not None:
        rng = random.Random(seed)
        n = 10 ** (noise_db / 20)
        right = [v + rng.gauss(0, n) for v in right]
    return left, right


def write_wav(path, left, right, rate=48000):
    with wave.open(str(path), "wb") as w:
        w.setnchannels(2)
        w.setsampwidth(2)
        w.setframerate(rate)
        frames = bytearray()
        for a, b in zip(left, right):
            frames += struct.pack("<hh", max(-32767, min(32767, int(a * 32767))),
                                  max(-32767, min(32767, int(b * 32767))))
        w.writeframes(bytes(frames))


def main():
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("output")
    p.add_argument("--seconds", type=float, default=60)
    p.add_argument("--rate", type=int, default=48000)
    p.add_argument("--fps", type=int, default=30, choices=(24, 25, 30))
    p.add_argument("--level-db", type=float, default=-10.0, help="LTC level (dBFS)")
    p.add_argument("--cues", help="click times in ms, comma separated (default: every second)")
    p.add_argument("--start-frame", type=int, default=0)
    p.add_argument("--speed", type=float, default=1.0)
    p.add_argument("--noise-db", type=float)
    p.add_argument("--invert", action="store_true")
    a = p.parse_args()
    cues = [float(c) for c in a.cues.split(",")] if a.cues else None
    left, right = build(a.seconds, a.rate, a.fps, a.level_db, cues=cues, start_frame=a.start_frame,
                        speed=a.speed, noise_db=a.noise_db, invert=a.invert)
    write_wav(a.output, left, right, a.rate)
    print("Wrote %s: %.1f s, %d Hz, LTC %d fps at %.0f dBFS on the right, clicks on the left" % (
        a.output, a.seconds, a.rate, a.fps, a.level_db))


if __name__ == "__main__":
    main()
