# Script engine shared by the field and dummy module firmware: time sync,
# script upload, start/stop/heartbeat, step mode, chase mode (cues follow an
# audio box's SHOW_TIME, docs/audio_sync_design.md) and the scheduler.
#
# The firmware passes a "node" object. ScriptEngine calls:
#   node.fire(mask)        -> ACK_OK or a NACK code
#   node.fire_blocked()    -> None when firing is allowed, otherwise
#                             (nack_code, reason) with reason "fault" or "disarmed"
#   node.stop_outputs()    -> switch any active outputs off
#
# Keep every top-level name here unique: the field image bundles this file
# into one namespace with field_main.py.

import time

from nfcp import *


class ScriptEngine:
    def __init__(self, node, link, max_entries=192, heartbeat_timeout_ms=1500, sync_valid_ms=10000,
                 chase_timeout_ms=1500, chase_wait_ms=10000):
        self.node = node
        self.link = link
        self.max_entries = max_entries
        self.heartbeat_timeout_ms = heartbeat_timeout_ms
        self.sync_valid_ms = sync_valid_ms
        # Chase mode (docs/audio_sync_design.md): cues follow an audio box's
        # SHOW_TIME. Stop if it goes quiet for chase_timeout_ms once locked,
        # or never arrives within chase_wait_ms of the START.
        self.chase_timeout_ms = chase_timeout_ms
        self.chase_wait_ms = chase_wait_ms
        self.chase = None            # None, "wait", "lock" or "pause"
        self.chase_source = 0
        self.chase_track = 0
        self.chase_offset_ms = 0
        self.chase_heard_ms = 0
        self.chase_jumps = 0
        self.id_loaded = None
        self.entries = []
        self.running = False
        self.start_ms = 0
        self.next_index = 0
        self.last_heartbeat_ms = 0
        self.last_event = "idle"
        self.time_synced = False
        self.master_offset_ms = 0
        self.time_sync_local_ms = 0

    # --- state -------------------------------------------------------------
    def clear(self, script_id=None):
        self.id_loaded = script_id
        self.entries = []
        self.running = False
        self.start_ms = 0
        self.next_index = 0
        self.last_heartbeat_ms = 0
        self.last_event = "clear"
        self.chase = None
        print("SCRIPT CLEAR id=%s" % ("--" if script_id is None else "%04X" % script_id))

    def halt(self):
        """Stops a running script without touching the loaded entries."""
        self.running = False
        self.chase = None

    def status_text(self, suffix=""):
        script_id_text = "--" if self.id_loaded is None else "%04X" % self.id_loaded
        hb_age = -1
        if self.last_heartbeat_ms:
            hb_age = time.ticks_diff(time.ticks_ms(), self.last_heartbeat_ms)
        chase = ""
        if self.chase is not None:
            chase = " chase=%s jumps=%d" % (self.chase, self.chase_jumps)
        return "id=%s entries=%d run=%d next=%d hb=%d%s %s%s" % (
            script_id_text,
            len(self.entries),
            1 if self.running else 0,
            self.next_index,
            hb_age,
            chase,
            self.last_event,
            suffix,
        )

    # --- time sync ---------------------------------------------------------
    @staticmethod
    def arrival_ms(pkt):
        """Local ticks_ms when the packet arrived (module_link stamps rx_us),
        not when Python got round to handling it."""
        rx_us = pkt.get("rx_us")
        if rx_us is None:
            return time.ticks_ms()
        back_ms = (time.ticks_diff(time.ticks_us(), rx_us) + 500) // 1000
        return time.ticks_add(time.ticks_ms(), -back_ms)

    @staticmethod
    def age_ms(pkt):
        """How old the master's timestamp already was when the packet arrived:
        bridge hold, repeats and relay waits (the mesh age field)."""
        return (pkt.get("mesh_age_us", 0) + 500) // 1000

    def time_sync(self, pkt):
        data = pkt.get("data", b"")
        if len(data) < 4:
            return
        master_ms = int.from_bytes(data[0:4], "big") & 0x3FFFFFFF
        master_ms = (master_ms + self.age_ms(pkt)) & 0x3FFFFFFF
        local_ms = self.arrival_ms(pkt) & 0x3FFFFFFF
        self.master_offset_ms = time.ticks_diff(master_ms, local_ms)
        self.time_sync_local_ms = time.ticks_ms()
        self.time_synced = True
        self.last_event = "time sync"

    def time_sync_valid(self):
        return (
            self.time_synced and
            time.ticks_diff(time.ticks_ms(), self.time_sync_local_ms) <= self.sync_valid_ms
        )

    def master_to_local_ms(self, master_ms):
        return time.ticks_add(master_ms & 0x3FFFFFFF, -self.master_offset_ms)

    # --- upload ------------------------------------------------------------
    @staticmethod
    def parse_load(data):
        if len(data) < 4:
            return None, None, None, NACK_BAD_LENGTH

        script_id = int.from_bytes(data[0:2], "big") & 0xFFFF
        chunk_index = data[2]
        entry_count = data[3]
        payload = data[4:]
        if len(payload) != entry_count * SCRIPT_ENTRY_BYTES:
            return None, None, None, NACK_BAD_LENGTH

        entries = []
        pos = 0
        for _ in range(entry_count):
            event_ms = int.from_bytes(payload[pos:pos + 4], "big")
            cue_mask = int.from_bytes(payload[pos + 4:pos + 12], "big") & 0xFFFFFFFFFFFFFFFF
            pos += SCRIPT_ENTRY_BYTES
            if cue_mask:
                entries.append((event_ms, cue_mask))

        return script_id, chunk_index, entries, ACK_OK

    def index_at_or_after(self, offset_ms):
        index = 0
        while index < len(self.entries):
            if self.entries[index][0] >= offset_ms:
                break
            index += 1
        return index

    def handle_clear(self, pkt):
        data = pkt.get("data", b"")
        if len(data) != 2:
            self.link.nack(pkt, CMD_SCRIPT_CLEAR, NACK_BAD_LENGTH)
            return
        self.clear(int.from_bytes(data, "big") & 0xFFFF)
        self.link.ack(pkt, CMD_SCRIPT_CLEAR)

    def handle_load(self, pkt):
        script_id, chunk_index, entries, code = self.parse_load(pkt.get("data", b""))
        if code != ACK_OK:
            self.link.nack(pkt, CMD_SCRIPT_LOAD, code)
            return

        if self.id_loaded != script_id:
            self.clear(script_id)

        if len(self.entries) + len(entries) > self.max_entries:
            self.link.nack(pkt, CMD_SCRIPT_LOAD, NACK_BUSY)
            return

        self.entries.extend(entries)
        self.entries.sort(key=lambda item: item[0])
        self.last_event = "load"
        print("SCRIPT LOAD id=%04X chunk=%d add=%d total=%d" % (script_id, chunk_index, len(entries), len(self.entries)))
        self.link.ack(pkt, CMD_SCRIPT_LOAD)

    # --- run ---------------------------------------------------------------
    def handle_start(self, pkt):
        blocked = self.node.fire_blocked()
        if blocked:
            self.last_event = "start " + blocked[1]
            self.link.nack(pkt, CMD_SCRIPT_START, blocked[0])
            return

        data = pkt.get("data", b"")
        if len(data) < 4:
            self.link.nack(pkt, CMD_SCRIPT_START, NACK_BAD_LENGTH)
            return

        script_id = int.from_bytes(data[0:2], "big") & 0xFFFF
        start_delay_ms = int.from_bytes(data[2:4], "big")
        offset_ms = int.from_bytes(data[4:8], "big") if len(data) >= 8 else 0
        start_master_ms = int.from_bytes(data[8:12], "big") & 0x3FFFFFFF if len(data) >= 12 else None

        if self.id_loaded != script_id or not self.entries:
            self.last_event = "start invalid"
            self.link.nack(pkt, CMD_SCRIPT_START, NACK_INVALID_VALUE)
            return

        if len(data) >= SCRIPT_START_CHASE_BYTES:
            self.start_chase(pkt, data)
            return

        self.chase = None
        self.next_index = self.index_at_or_after(offset_ms)
        if start_master_ms is not None and self.time_sync_valid():
            self.start_ms = time.ticks_add(self.master_to_local_ms(start_master_ms), -offset_ms)
        else:
            sent_ms = time.ticks_add(self.arrival_ms(pkt), -self.age_ms(pkt))
            self.start_ms = time.ticks_add(sent_ms, start_delay_ms - offset_ms)
        self.running = True
        self.last_heartbeat_ms = time.ticks_ms()
        self.last_event = "start"
        print("SCRIPT START id=%04X entries=%d offset=%d next=%d" % (script_id, len(self.entries), offset_ms, self.next_index))
        self.link.ack(pkt, CMD_SCRIPT_START)

    # --- chase mode: follow an audio box ----------------------------------
    # Corrections, in ms. Within CHASE_DEADBAND_MS of the audio box: nothing.
    # Within CHASE_JUMP_MS: move start_ms half way, at most CHASE_STEP_MS per
    # SHOW_TIME. Beyond it: a jump (the track moved), taken at once. After a
    # lock or jump, cues more than CHASE_LATE_MS overdue are skipped, never
    # fired; cues already fired never fire again.
    CHASE_DEADBAND_MS = 2
    CHASE_JUMP_MS = 250
    CHASE_STEP_MS = 10
    CHASE_LATE_MS = 100

    def start_chase(self, pkt, data):
        source = int.from_bytes(data[12:16], "big")
        if not source:
            self.last_event = "start invalid"
            self.link.nack(pkt, CMD_SCRIPT_START, NACK_INVALID_VALUE)
            return
        offset = int.from_bytes(data[2:4], "big")
        if offset >= 0x8000:
            offset -= 0x10000
        now = time.ticks_ms()
        self.chase = "wait"
        self.chase_source = source
        self.chase_track = int.from_bytes(data[16:18], "big")
        self.chase_offset_ms = offset
        self.chase_heard_ms = now
        self.chase_jumps = 0
        self.next_index = 0
        self.start_ms = now
        self.running = True
        self.last_heartbeat_ms = now
        self.last_event = "chase wait"
        print("SCRIPT START id=%04X entries=%d chase=%08X track=%04X audio_offset=%d" % (
            self.id_loaded, len(self.entries), source, self.chase_track, offset))
        self.link.ack(pkt, CMD_SCRIPT_START, ACK_CHASE)

    def stop_chase(self, reason):
        self.running = False
        self.chase = None
        self.node.stop_outputs()
        self.last_event = reason
        print("SCRIPT stopped - %s" % reason)

    def lock_chase(self, implied_start_ms, event):
        """Take the audio box's position as it is (first lock, resume after a
        pause, or a jump), skipping cues that are now well overdue."""
        self.start_ms = implied_start_ms
        elapsed = time.ticks_diff(time.ticks_ms(), implied_start_ms)
        index = self.index_at_or_after(elapsed - self.CHASE_LATE_MS)
        if index > self.next_index:
            print("SCRIPT CHASE skip %d cues" % (index - self.next_index))
            self.next_index = index
        self.chase = "lock"
        self.last_event = event

    def handle_show_time(self, pkt):
        if self.chase is None or not self.running:
            return
        if pkt.get("mesh_origin") != self.chase_source:
            return
        data = pkt.get("data", b"")
        if len(data) < 7 or int.from_bytes(data[0:2], "big") != self.chase_track:
            return
        if self.node.fire_blocked():
            return      # run() stops the script
        position = int.from_bytes(data[2:6], "big")
        state = data[6]
        self.chase_heard_ms = time.ticks_ms()
        if state == SHOW_PAUSED:
            if self.chase == "lock":
                self.chase = "pause"
                self.last_event = "chase pause"
            return
        if state != SHOW_PLAYING:
            self.stop_chase("audio ended" if state == SHOW_ENDED else "audio stopped")
            return

        # When the box read `position`; cue time 0 is position 0 plus the
        # audio offset.
        sent_ms = time.ticks_add(self.arrival_ms(pkt), -self.age_ms(pkt))
        implied = time.ticks_add(sent_ms, self.chase_offset_ms - position)
        if self.chase != "lock":
            self.lock_chase(implied, "chase lock")
            return
        error = time.ticks_diff(implied, self.start_ms)
        size = error if error >= 0 else -error
        if size <= self.CHASE_DEADBAND_MS:
            return
        if size <= self.CHASE_JUMP_MS:
            step = error // 2
            if step > self.CHASE_STEP_MS:
                step = self.CHASE_STEP_MS
            elif step < -self.CHASE_STEP_MS:
                step = -self.CHASE_STEP_MS
            self.start_ms = time.ticks_add(self.start_ms, step)
            return
        self.chase_jumps += 1
        print("SCRIPT CHASE jump %d ms" % error)
        self.lock_chase(implied, "chase jump")

    def handle_stop(self, pkt):
        # STOP halts the schedule and outputs but leaves the module armed and
        # the script loaded, so the master can resume from an offset.
        self.running = False
        self.chase = None
        self.last_heartbeat_ms = 0
        self.last_event = "stop"
        self.node.stop_outputs()
        print("SCRIPT STOP")
        self.link.ack(pkt, CMD_SCRIPT_STOP)

    def handle_heartbeat(self, pkt):
        data = pkt.get("data", b"")
        if len(data) < 2:
            return
        script_id = int.from_bytes(data[0:2], "big") & 0xFFFF
        if self.running and self.id_loaded == script_id:
            self.last_heartbeat_ms = time.ticks_ms()
            self.last_event = "heartbeat"

    def fire_event(self, event_ms):
        fired = 0
        for entry_ms, cue_mask in self.entries:
            if entry_ms == event_ms:
                self.node.fire(cue_mask)
                fired += 1
        return fired

    def handle_step(self, pkt):
        blocked = self.node.fire_blocked()
        if blocked:
            self.last_event = "step " + blocked[1]
            return

        data = pkt.get("data", b"")
        if len(data) != 6:
            return

        script_id = int.from_bytes(data[0:2], "big") & 0xFFFF
        event_ms = int.from_bytes(data[2:6], "big")
        if self.id_loaded != script_id:
            self.last_event = "step invalid"
            return

        if not self.fire_event(event_ms):
            self.last_event = "step missing"
            return

        self.last_event = "step"

    def run(self):
        """Fires every entry that is due. Call this from the main loop."""
        if not self.running:
            return
        blocked = self.node.fire_blocked()
        if blocked:
            self.running = False
            self.chase = None
            self.node.stop_outputs()
            self.last_event = "stopped " + blocked[1]
            return
        if time.ticks_diff(time.ticks_ms(), self.last_heartbeat_ms) > self.heartbeat_timeout_ms:
            self.running = False
            self.chase = None
            self.node.stop_outputs()
            self.last_event = "hb timeout"
            print("SCRIPT stopped - heartbeat timeout")
            return
        if self.chase is not None:
            quiet = time.ticks_diff(time.ticks_ms(), self.chase_heard_ms)
            if self.chase == "wait":
                if quiet > self.chase_wait_ms:
                    self.stop_chase("timecode never came")
                return
            if quiet > self.chase_timeout_ms:
                self.stop_chase("timecode lost")
                return
            if self.chase != "lock":
                return

        elapsed = time.ticks_diff(time.ticks_ms(), self.start_ms)
        while self.next_index < len(self.entries):
            event_ms, cue_mask = self.entries[self.next_index]
            if event_ms > elapsed:
                break
            self.last_event = "fire"
            print("SCRIPT FIRE mask=0x%06X event=%d elapsed=%d" % (cue_mask, event_ms, elapsed))
            self.node.fire(cue_mask)
            self.next_index += 1

        if self.next_index >= len(self.entries):
            self.running = False
            self.chase = None
            self.last_event = "complete"

    def wait_ms(self, default_ms):
        """Receive timeout that wakes the main loop when the next cue is due,
        so recv() never makes a cue late."""
        if not self.running or self.next_index >= len(self.entries):
            return default_ms
        if self.chase is not None and self.chase != "lock":
            return default_ms
        left = self.entries[self.next_index][0] - time.ticks_diff(time.ticks_ms(), self.start_ms)
        if left < default_ms:
            return left if left > 1 else 1
        return default_ms

    def handle(self, pkt):
        """Handles script and time-sync commands. Returns True if handled."""
        cmd = pkt.get("cmd")
        if cmd == CMD_SCRIPT_CLEAR:
            self.handle_clear(pkt)
        elif cmd == CMD_SCRIPT_LOAD:
            self.handle_load(pkt)
        elif cmd == CMD_SCRIPT_START:
            self.handle_start(pkt)
        elif cmd == CMD_SCRIPT_STOP:
            self.handle_stop(pkt)
        elif cmd == CMD_SCRIPT_HEARTBEAT:
            self.handle_heartbeat(pkt)
        elif cmd == CMD_TIME_SYNC:
            self.time_sync(pkt)
        elif cmd == CMD_SCRIPT_STEP:
            self.handle_step(pkt)
        elif cmd == CMD_SHOW_TIME:
            self.handle_show_time(pkt)
        else:
            return False
        return True
