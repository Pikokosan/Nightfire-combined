# The audio box's side of the ESP32 player (audio_box/player/player_main.py,
# audio_box/design.md step 4): UART commands to it, and the play position.
#
# UartPlayer has the same interface as audio_main.FakePlayer, so the box
# doesn't care which one it has. Until the box decodes the LTC from the DAC
# (step 4, native capture), the position runs on the box's own clock from
# the moment the player confirms PLAY, and is checked against the player's
# STATUS every POLL_MS:
# - the player reports the track ended (or sends ENDED): the track ends;
# - the player reports it stopped, or its position is more than DRIFT_MS
#   from the box's, or it misses MISS_LIMIT polls: the box stops (fail-stop:
#   SHOW_TIME says stopped and the modules stop).
#
# load/play/stop wait for the player's answer (up to REPLY_MS); the polls
# don't wait: their answers are read on later calls.

import time

from nfcp import ACK_OK, NACK_BUSY, NACK_INTERNAL_ERROR, NACK_INVALID_VALUE
from nfcp import SHOW_ENDED, SHOW_PLAYING, SHOW_STOPPED

PLAYER_REPLY_MS = 80
PLAYER_POLL_MS = 500
PLAYER_MISS_LIMIT = 2
PLAYER_DRIFT_MS = 150


class UartPlayer:
    def __init__(self, uart, sleep_ms=None):
        self.uart = uart
        self.sleep_ms = sleep_ms or time.sleep_ms
        self.track = 0
        self.state = SHOW_STOPPED
        self.length_ms = 0
        self.base_ms = 0
        self.played_us = 0
        self.last_us = 0
        self.lengths = {}           # track -> length_ms, from LOAD
        self.rx = b""
        self.poll_sent_ms = None    # when the unanswered STATUS went out
        self.last_poll_ms = 0
        self.misses = 0
        self.fault = None           # why the box stopped the track, if it did
        self.stopped_now = False    # advance(): the box stopped it (fault)
        self.player_pos = None      # last position the player reported

    # --- UART -------------------------------------------------------------
    def send(self, line):
        self.uart.write((line + "\n").encode())

    def lines(self):
        """Complete lines received so far."""
        if self.uart.any():
            self.rx += self.uart.read() or b""
        out = []
        while b"\n" in self.rx:
            line, self.rx = self.rx.split(b"\n", 1)
            line = line.strip()
            if line:
                out.append(line.decode())
        if len(self.rx) > 200:
            self.rx = b""
        return out

    def request(self, line, prefixes):
        """Send a command; its reply (a line starting with one of
        prefixes), or None after PLAYER_REPLY_MS. Other lines are handled
        as they come (STATUS replies to polls, ENDED)."""
        self.send(line)
        start = time.ticks_ms()
        while time.ticks_diff(time.ticks_ms(), start) < PLAYER_REPLY_MS:
            for got in self.lines():
                if got.startswith(prefixes):
                    return got
                self.unasked(got)
            self.sleep_ms(1)
        return None

    def unasked(self, line):
        words = line.split()
        if words[0] == "ENDED" and len(words) == 2 and int(words[1]) == self.track:
            if self.state == SHOW_PLAYING:
                self.end()
        elif words[0] == "STATUS" and len(words) == 5:
            self.poll_sent_ms = None
            self.misses = 0
            self.check_status(int(words[1]), int(words[2]), int(words[3]))

    # --- the FakePlayer interface ----------------------------------------
    def length_of(self, track):
        return self.lengths.get(track)

    def load(self, track):
        if self.state == SHOW_PLAYING:
            return NACK_BUSY
        reply = self.request("LOAD %d" % track, ("OK LOAD", "ERR LOAD"))
        if reply is None:
            return NACK_INTERNAL_ERROR
        words = reply.split()
        if words[0] == "ERR":
            return NACK_BUSY if words[-1] == "busy" else NACK_INVALID_VALUE
        self.lengths[track] = int(words[3])
        self.track = track
        self.length_ms = int(words[3])
        self.state = SHOW_STOPPED
        self.base_ms = 0
        self.played_us = 0
        return ACK_OK

    def play(self, track, from_ms, now_us):
        if track not in self.lengths:
            # Its length comes from LOAD, which the player refuses while it
            # plays: stop first.
            if self.state == SHOW_PLAYING:
                self.stop(now_us)
            code = self.load(track)
            if code != ACK_OK:
                return code
        length = self.lengths[track]
        if not 0 <= from_ms < length:
            return NACK_INVALID_VALUE
        reply = self.request("PLAY %d %d" % (track, from_ms), ("OK PLAY", "ERR PLAY"))
        if reply is None:
            return NACK_INTERNAL_ERROR
        if reply.startswith("ERR"):
            return NACK_INVALID_VALUE
        # The player starts the DAC right after it answers: position from_ms now.
        self.track = track
        self.length_ms = length
        self.base_ms = from_ms
        self.played_us = 0
        self.last_us = time.ticks_us()
        self.state = SHOW_PLAYING
        self.fault = None
        self.misses = 0
        self.poll_sent_ms = None
        self.last_poll_ms = time.ticks_ms()
        self.player_pos = None
        return ACK_OK

    def stop(self, now_us):
        """Stop at once, whatever the player says. True if it was playing."""
        playing = self.state == SHOW_PLAYING
        if playing:
            self.step(now_us)
            self.base_ms = self.position_ms()
            self.played_us = 0
        self.state = SHOW_STOPPED
        self.request("STOP", ("OK STOP",))
        return playing

    def advance(self, now_us):
        """Bring the position up to now_us and check on the player. True if
        the track just ended or the box just stopped it (see stopped_now)."""
        self.stopped_now = False
        was = self.state
        for line in self.lines():
            self.unasked(line)
        if self.state == SHOW_PLAYING:
            self.step(now_us)
            if self.base_ms + self.played_us // 1000 >= self.length_ms:
                self.end()
            else:
                self.service_poll(time.ticks_ms())
        return was == SHOW_PLAYING and self.state != SHOW_PLAYING

    def position_ms(self):
        return self.base_ms + self.played_us // 1000

    # --- internals ---------------------------------------------------------
    def step(self, now_us):
        d = time.ticks_diff(now_us, self.last_us)
        self.last_us = now_us
        if d > 0:
            self.played_us += d

    def end(self):
        self.base_ms = self.length_ms
        self.played_us = 0
        self.state = SHOW_ENDED

    def halt(self, why):
        """The box stops the track: the player can't be trusted."""
        self.base_ms = self.position_ms()
        self.played_us = 0
        self.state = SHOW_STOPPED
        self.fault = why
        self.stopped_now = True
        self.send("STOP")
        print("AUDIO player fault: %s" % why)

    def poll(self, now_ms):
        self.send("STATUS")
        self.poll_sent_ms = now_ms
        self.last_poll_ms = now_ms

    def service_poll(self, now_ms):
        if self.poll_sent_ms is not None and time.ticks_diff(now_ms, self.poll_sent_ms) >= PLAYER_POLL_MS:
            self.poll_sent_ms = None
            self.misses += 1
            if self.misses >= PLAYER_MISS_LIMIT:
                self.halt("not answering")
                return
        if self.poll_sent_ms is None and time.ticks_diff(now_ms, self.last_poll_ms) >= PLAYER_POLL_MS:
            self.poll(now_ms)

    def check_status(self, track, pos, state):
        self.player_pos = pos
        if self.state != SHOW_PLAYING:
            return
        if track != self.track:
            self.halt("playing track %d" % track)
        elif state == 3:
            self.end()
        elif state != 1:
            self.halt("player stopped")
        else:
            off = pos - self.position_ms()
            if off > PLAYER_DRIFT_MS or off < -PLAYER_DRIFT_MS:
                self.halt("player %+d ms off" % off)
