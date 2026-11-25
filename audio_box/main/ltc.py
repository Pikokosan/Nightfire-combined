# SMPTE linear timecode (LTC) for the audio box (audio_box/design.md, step 4).
#
# The track's right channel carries LTC. The box turns that signal into edge
# times (a comparator on an nRF pin, timed by hardware) and LtcDecoder turns
# the edges into the play position. LtcEncoder makes the same signal for
# test tracks and unit tests.
#
# LTC in brief: 80 bits per frame, sent at fps * 80 bits a second. Biphase
# mark: the level flips at the start of every bit, and flips again in the
# middle of a 1. Bits 64-79 are the sync word 0011 1111 1111 1101, which can't
# occur anywhere else. The time code in a frame names the moment its bit 0
# starts, so the end of the sync word is that time plus one frame.
#
# Runs on MicroPython: integers only, no floats in the decoder.

LTC_BITS = 80
LTC_SYNC = (0, 0, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 0, 1)   # bits 64-79
LTC_RATES = (24, 25, 30)                                   # non-drop frame


def ltc_frame_bits(hours, minutes, seconds, frames, fps=30):
    """The 80 bits (bit 0 first) of one frame. User bits are 0."""
    if fps not in LTC_RATES:
        raise ValueError("fps must be 24, 25 or 30")
    bits = [0] * LTC_BITS

    def put(start, count, value):
        for i in range(count):
            bits[start + i] = (value >> i) & 1

    put(0, 4, frames % 10)
    put(8, 2, frames // 10)
    put(16, 4, seconds % 10)
    put(24, 3, seconds // 10)
    put(32, 4, minutes % 10)
    put(40, 3, minutes // 10)
    put(48, 4, hours % 10)
    put(56, 2, hours // 10)
    for i, b in enumerate(LTC_SYNC):
        bits[64 + i] = b
    # Polarity correction: an even number of 1s, so every frame starts with
    # the same level (bit 27 at 30 and 24 fps, bit 59 at 25 fps).
    polarity = 59 if fps == 25 else 27
    if sum(bits) % 2:
        bits[polarity] = 1
    return bits


def ltc_frame_ms(hours, minutes, seconds, frames, fps):
    """Time code as ms from 00:00:00:00 (whole ms, rounded down)."""
    return ((hours * 60 + minutes) * 60 + seconds) * 1000 + frames * 1000 // fps


def ltc_timecode_at(frame_index, fps):
    """(h, m, s, f) of the frame_index-th frame from 00:00:00:00."""
    seconds, frames = divmod(frame_index, fps)
    minutes, seconds = divmod(seconds, 60)
    hours, minutes = divmod(minutes, 60)
    return hours % 24, minutes, seconds, frames


class LtcEncoder:
    """Level changes of an LTC signal: edge_times_us(first_frame, count)
    gives the time (µs from the start of the first frame) of every flip."""

    def __init__(self, fps=30):
        self.fps = fps

    def bit_us(self):
        return 1000000.0 / (self.fps * LTC_BITS)

    def edge_times_us(self, first_frame, count, speed=1.0):
        bit = self.bit_us() / speed
        out = []
        t = 0.0
        for n in range(first_frame, first_frame + count):
            for b in ltc_frame_bits(*ltc_timecode_at(n, self.fps), fps=self.fps):
                out.append(t)                  # flip at the start of every bit
                if b:
                    out.append(t + bit / 2)    # and in the middle of a 1
                t += bit
        return out


class LtcDecoder:
    """Edge times in, play positions out.

    feed(t_us) takes the time of one level change (any polarity), from a
    microsecond counter that may wrap at `period`. After the sync word of a
    good frame, `frame` holds (position_ms, t_us): the frame's time code
    plus one frame, at the moment the sync word ended. `frames` counts good
    frames; `locked` is True after LTC_LOCK_FRAMES frames in a row that
    follow each other, and goes False on a gap or a frame out of sequence.

    The bit length is learnt from the signal, so speed changes of a few %
    are followed. An interval near half a bit is one half of a 1; near a
    whole bit, a 0; anything else resets the bit stream."""

    LOCK_FRAMES = 2

    def __init__(self, fps=30, period=1 << 30):
        if fps not in LTC_RATES:
            raise ValueError("fps must be 24, 25 or 30")
        self.fps = fps
        self.period = period
        self.nominal = 1000000 // (fps * LTC_BITS)     # µs per bit
        self.reset()
        self.frames = 0
        self.errors = 0

    def reset(self):
        self.bit_us = self.nominal
        self.last_t = None
        self.half = False          # waiting for the second half of a 1
        self.shift = 0             # last 80 bits, newest in bit 79
        self.count = 0             # bits in shift since the last reset
        self.frame = None
        self.prev_index = None
        self.run = 0
        self.locked = False

    def gap(self):
        """No signal: drop the lock (call when no edge came for a while)."""
        self.reset()

    def feed(self, t_us):
        last = self.last_t
        self.last_t = t_us
        if last is None:
            return None
        dt = (t_us - last) % self.period
        bit = self.bit_us
        if dt < bit // 4 or dt > bit + bit // 2:
            # Noise or a dropout: start again (the lock goes on a long gap).
            if dt > 3 * bit:
                self.reset()
                self.last_t = t_us
            else:
                self.errors += 1
                self.half = False
                self.count = 0
            return None
        if dt < (bit * 3) // 4:
            if not self.half:
                self.half = True
                self.half_dt = dt
                return None
            self.half = False
            self.learn(self.half_dt + dt)
            return self.push(1, t_us)
        if self.half:
            # A half bit not followed by another: out of step.
            self.half = False
            self.errors += 1
            self.count = 0
            return None
        self.learn(dt)
        return self.push(0, t_us)

    def learn(self, cell_us):
        # Follow the bit length slowly (1/16 of the difference per bit).
        self.bit_us += (cell_us - self.bit_us) // 16

    def push(self, bit, t_us):
        """One whole bit; t_us is when it ended (the next bit's start)."""
        self.shift = ((self.shift >> 1) | (bit << 79))
        self.count += 1
        if self.count < LTC_BITS:
            return None
        # Sync word in bits 64-79 (the newest 16 bits).
        if (self.shift >> 64) != 0xBFFC:
            return None
        return self.frame_done(t_us)

    def frame_done(self, t_us):
        s = self.shift

        def field(start, count):
            return (s >> start) & ((1 << count) - 1)

        frames = field(0, 4) + 10 * field(8, 2)
        seconds = field(16, 4) + 10 * field(24, 3)
        minutes = field(32, 4) + 10 * field(40, 3)
        hours = field(48, 4) + 10 * field(56, 2)
        if (field(0, 4) > 9 or field(16, 4) > 9 or field(32, 4) > 9 or field(48, 4) > 9 or
                frames >= self.fps or seconds > 59 or minutes > 59 or hours > 23):
            self.errors += 1
            return None
        index = ((hours * 60 + minutes) * 60 + seconds) * self.fps + frames
        if self.prev_index is not None and index == self.prev_index + 1:
            self.run += 1
        else:
            self.run = 1
        self.prev_index = index
        self.locked = self.run >= self.LOCK_FRAMES
        self.frames += 1
        # The sync word ends one frame after the time the frame names.
        self.frame = ((index + 1) * 1000 // self.fps, t_us)
        return self.frame

    def position_ms(self, now_us):
        """The play position at now_us, from the last frame, or None."""
        if self.frame is None or not self.locked:
            return None
        pos, t = self.frame
        return pos + ((now_us - t) % self.period) // 1000
