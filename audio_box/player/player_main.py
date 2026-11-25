# Audio box player (audio_box/design.md, step 4): an ESP32 with a PCM5102A
# I2S DAC and an SD card, driven by the audio box's nRF over UART.
#
# Tracks are NNN.wav on the SD card (track 7 is 007.wav): 16-bit stereo PCM
# at 44.1 or 48 kHz. The right channel carries the LTC the nRF decodes; the
# left goes to the amplifier.
#
# UART protocol, one line each way, ASCII, "\n" ends a line:
#   PING              -> PONG audio-player <version>
#   LIST              -> TRACKS <n>,<n>,...
#   LOAD n            -> OK LOAD n <length_ms>     | ERR LOAD n <why>
#   PLAY n from_ms    -> OK PLAY n                 | ERR PLAY n <why>
#   STOP              -> OK STOP
#   STATUS            -> STATUS n <pos_ms> <state> <length_ms>
#   (state as SHOW_TIME: 0 stopped, 1 playing, 3 ended)
#   anything else     -> ERR ?
# <why> is nofile, format, range or busy. When a track ends the player also
# sends "ENDED n" unasked.
#
# The position is what has gone into I2S minus what is still buffered, so
# it is only as good as that estimate (a few ms); the nRF uses the LTC it
# decodes from the DAC's output as the real position and this as a check.

import time

APP_NAME = "audio-player"
APP_VERSION = "0.1.0"

STATE_STOPPED = 0
STATE_PLAYING = 1
STATE_ENDED = 3

CHUNK_BYTES = 4096            # ~21 ms of 48 kHz stereo per I2S write
I2S_BUFFER_BYTES = 16384      # DMA buffer: ~85 ms at 48 kHz


class WavError(Exception):
    pass


def wav_info(f):
    """(rate, data_start, data_bytes) of a 16-bit stereo PCM WAV file, read
    from its header; WavError if it isn't one."""
    head = f.read(12)
    if len(head) < 12 or head[0:4] != b"RIFF" or head[8:12] != b"WAVE":
        raise WavError("not a WAV file")
    rate = None
    pos = 12
    while True:
        chunk = f.read(8)
        if len(chunk) < 8:
            raise WavError("no data chunk")
        kind = chunk[0:4]
        size = int.from_bytes(chunk[4:8], "little")
        body = pos + 8
        if kind == b"fmt ":
            fmt = f.read(size)
            if len(fmt) < 16:
                raise WavError("short fmt")
            tag = int.from_bytes(fmt[0:2], "little")
            channels = int.from_bytes(fmt[2:4], "little")
            rate = int.from_bytes(fmt[4:8], "little")
            bits = int.from_bytes(fmt[14:16], "little")
            if tag != 1 or channels != 2 or bits != 16 or rate not in (44100, 48000):
                raise WavError("need 16-bit stereo PCM at 44.1 or 48 kHz")
        elif kind == b"data":
            if rate is None:
                raise WavError("data before fmt")
            return rate, body, size - size % 4
        pos = body + size + (size & 1)      # chunks are padded to even sizes
        f.seek(pos)


class AudioPlayer:
    """Plays one track at a time into I2S.

    open_file(name) opens a track file for reading (bytes); make_i2s(rate)
    returns an object with write(buf) (blocks while the DMA buffer is full)
    and deinit(); clock() is ticks_ms. service() feeds one chunk and must be
    called often while playing."""

    def __init__(self, open_file, make_i2s, clock=None, diff=None, buffer_bytes=I2S_BUFFER_BYTES,
                 mute=None):
        self.open_file = open_file
        self.make_i2s = make_i2s
        self.clock = clock or time.ticks_ms
        self.diff = diff or time.ticks_diff
        self.buffer_bytes = buffer_bytes
        self.mute = mute            # mute(True/False): the DAC's XSMT pin, or None
        self.buf = bytearray(CHUNK_BYTES)
        self.track = 0
        self.state = STATE_STOPPED
        self.length_ms = 0
        self.rate = 48000
        self.file = None
        self.i2s = None
        self.start_ms = 0           # track position the play started from
        self.written = 0            # bytes into I2S since then
        self.left = 0               # data bytes still to send
        self.eof_ms = None          # when the last data went in
        self.stopped_pos = 0
        self.events = []            # lines to send unasked ("ENDED n")

    @staticmethod
    def file_name(track):
        return "%03d.wav" % track

    def probe(self, track):
        """(rate, data_start, data_bytes) of a track, or raises."""
        if not 1 <= track <= 999:
            raise WavError("range")
        try:
            f = self.open_file(self.file_name(track))
        except OSError:
            raise WavError("nofile")
        try:
            return f, wav_info(f)
        except WavError:
            f.close()
            raise WavError("format")

    def bytes_per_ms(self):
        return self.rate * 4 / 1000

    def load(self, track):
        """Check a track; length in ms. Refused while playing."""
        if self.state == STATE_PLAYING:
            raise WavError("busy")
        f, (rate, start, size) = self.probe(track)
        f.close()
        self.close()
        self.track = track
        self.rate = rate
        self.length_ms = size * 1000 // (rate * 4)
        self.state = STATE_STOPPED
        self.stopped_pos = 0
        return self.length_ms

    def play(self, track, from_ms):
        """Start (or restart) track at from_ms."""
        f, (rate, start, size) = self.probe(track)
        length_ms = size * 1000 // (rate * 4)
        if not 0 <= from_ms < length_ms:
            f.close()
            raise WavError("range")
        self.close()
        skip = (from_ms * rate // 1000) * 4
        f.seek(start + skip)
        self.file = f
        self.track = track
        self.rate = rate
        self.length_ms = length_ms
        self.start_ms = from_ms
        self.written = 0
        self.left = size - skip
        self.eof_ms = None
        self.i2s = self.make_i2s(rate)
        if self.mute:
            self.mute(False)
        self.state = STATE_PLAYING

    def stop(self):
        """Stop at once (safe to repeat). The position stays where it was."""
        if self.state != STATE_STOPPED:
            self.stopped_pos = self.position_ms()
        self.close()
        self.state = STATE_STOPPED

    def close(self):
        if self.mute:
            self.mute(True)
        if self.i2s is not None:
            self.i2s.deinit()
            self.i2s = None
        if self.file is not None:
            self.file.close()
            self.file = None

    def position_ms(self):
        if self.state == STATE_PLAYING:
            if self.eof_ms is not None:
                # Everything is in; the buffer drains on the clock.
                gone = self.written - self.buffer_bytes + self.diff(self.clock(), self.eof_ms) * self.bytes_per_ms()
                played = min(self.written, max(0, gone))
            else:
                played = max(0, self.written - self.buffer_bytes)
            return min(self.length_ms, self.start_ms + int(played / self.bytes_per_ms()))
        if self.state == STATE_ENDED:
            return self.length_ms
        return self.stopped_pos

    def service(self):
        """Feed one chunk. True if it did anything."""
        if self.state != STATE_PLAYING:
            return False
        if self.eof_ms is None:
            n = self.file.readinto(self.buf) if self.left > 0 else 0
            n = min(n, self.left)
            n -= n % 4
            if n > 0:
                self.i2s.write(memoryview(self.buf)[:n])
                self.written += n
                self.left -= n
                return True
            # The end of the data: push silence through so the last samples
            # play, and time the drain.
            self.eof_ms = self.clock()
            for i in range(len(self.buf)):
                self.buf[i] = 0
            return True
        if self.diff(self.clock(), self.eof_ms) * self.bytes_per_ms() >= self.buffer_bytes:
            self.close()
            self.state = STATE_ENDED
            self.events.append("ENDED %d" % self.track)
            return True
        # Keep the DAC fed with silence while the real data drains.
        self.i2s.write(memoryview(self.buf)[:CHUNK_BYTES // 4])
        return True

    def status(self):
        return self.track, self.position_ms(), self.state, self.length_ms


class LineProtocol:
    """The UART protocol: feed(bytes) -> reply lines (str, no newline)."""

    def __init__(self, player, tracks=None):
        self.player = player
        self.tracks = tracks        # tracks() -> list of track numbers
        self.pending = b""

    def feed(self, data):
        self.pending += data
        out = []
        while b"\n" in self.pending:
            line, self.pending = self.pending.split(b"\n", 1)
            line = line.strip()
            if line:
                out.append(self.command(line.decode("ascii", "replace")))
        if len(self.pending) > 200:
            self.pending = b""          # no line that long is valid
        return out

    def command(self, line):
        words = line.split()
        name = words[0].upper()
        p = self.player
        try:
            if name == "PING" and len(words) == 1:
                return "PONG %s %s" % (APP_NAME, APP_VERSION)
            if name == "STATUS" and len(words) == 1:
                return "STATUS %d %d %d %d" % p.status()
            if name == "STOP" and len(words) == 1:
                p.stop()
                return "OK STOP"
            if name == "LIST" and len(words) == 1:
                return "TRACKS " + ",".join(str(t) for t in (self.tracks() if self.tracks else []))
            if name == "LOAD" and len(words) == 2:
                track = int(words[1])
                try:
                    return "OK LOAD %d %d" % (track, p.load(track))
                except WavError as e:
                    return "ERR LOAD %d %s" % (track, e.args[0] if e.args[0] in ("nofile", "format", "range", "busy") else "format")
            if name == "PLAY" and len(words) == 3:
                track, from_ms = int(words[1]), int(words[2])
                try:
                    p.play(track, from_ms)
                    return "OK PLAY %d" % track
                except WavError as e:
                    return "ERR PLAY %d %s" % (track, e.args[0] if e.args[0] in ("nofile", "format", "range") else "format")
        except ValueError:
            pass
        return "ERR ?"


def list_tracks(listdir, root):
    out = []
    for name in listdir(root):
        if len(name) == 7 and name[3:].lower() == ".wav" and name[:3].isdigit():
            out.append(int(name[:3]))
    return sorted(out)


def main():
    """Run on the ESP32 (main.py calls this)."""
    import os
    from machine import I2S, Pin, SDCard, UART

    try:
        import player_config as cfg
    except ImportError:
        cfg = None

    def get(name, default):
        return getattr(cfg, name, default)

    root = "/sd"
    sd = SDCard(slot=get("SD_SLOT", 2), sck=Pin(get("SD_SCK_PIN", 18)), mosi=Pin(get("SD_MOSI_PIN", 23)),
                miso=Pin(get("SD_MISO_PIN", 19)), cs=Pin(get("SD_CS_PIN", 5)))
    os.mount(sd, root)

    def make_i2s(rate):
        return I2S(0, sck=Pin(get("I2S_BCK_PIN", 26)), ws=Pin(get("I2S_WS_PIN", 25)),
                   sd=Pin(get("I2S_DIN_PIN", 22)), mode=I2S.TX, bits=16, format=I2S.STEREO,
                   rate=rate, ibuf=I2S_BUFFER_BYTES)

    mute = None
    if get("DAC_XSMT_PIN", None) is not None:
        xsmt = Pin(get("DAC_XSMT_PIN", None), Pin.OUT, value=0)
        mute = lambda on: xsmt.value(0 if on else 1)

    player = AudioPlayer(lambda name: open(root + "/" + name, "rb"), make_i2s, mute=mute)
    proto = LineProtocol(player, lambda: list_tracks(os.listdir, root))
    uart = UART(get("UART_ID", 2), baudrate=get("UART_BAUD", 115200), tx=get("UART_TX_PIN", 17),
                rx=get("UART_RX_PIN", 16))
    print("%s %s: tracks %s" % (APP_NAME, APP_VERSION, list_tracks(os.listdir, root)))
    while True:
        try:
            if uart.any():
                for reply in proto.feed(uart.read()):
                    uart.write(reply + "\n")
            while player.events:
                uart.write(player.events.pop(0) + "\n")
            if not player.service():
                time.sleep_ms(2)
        except Exception as e:
            # Fail-stop: the music stops, so the LTC stops and the modules stop.
            print("player error:", e)
            player.stop()
            time.sleep_ms(100)
