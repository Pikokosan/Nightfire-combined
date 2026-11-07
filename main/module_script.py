# Script engine shared by the field and dummy module firmware: time sync,
# script upload, start/stop/heartbeat, step mode and the scheduler.
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
    def __init__(self, node, link, max_entries=192, heartbeat_timeout_ms=1500, sync_valid_ms=10000):
        self.node = node
        self.link = link
        self.max_entries = max_entries
        self.heartbeat_timeout_ms = heartbeat_timeout_ms
        self.sync_valid_ms = sync_valid_ms
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
        print("SCRIPT CLEAR id=%s" % ("--" if script_id is None else "%04X" % script_id))

    def halt(self):
        """Stops a running script without touching the loaded entries."""
        self.running = False

    def status_text(self, suffix=""):
        script_id_text = "--" if self.id_loaded is None else "%04X" % self.id_loaded
        hb_age = -1
        if self.last_heartbeat_ms:
            hb_age = time.ticks_diff(time.ticks_ms(), self.last_heartbeat_ms)
        return "id=%s entries=%d run=%d next=%d hb=%d %s%s" % (
            script_id_text,
            len(self.entries),
            1 if self.running else 0,
            self.next_index,
            hb_age,
            self.last_event,
            suffix,
        )

    # --- time sync ---------------------------------------------------------
    def time_sync(self, pkt):
        data = pkt.get("data", b"")
        if len(data) < 4:
            return
        master_ms = int.from_bytes(data[0:4], "big") & 0x3FFFFFFF
        local_ms = time.ticks_ms() & 0x3FFFFFFF
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

        self.next_index = self.index_at_or_after(offset_ms)
        if start_master_ms is not None and self.time_sync_valid():
            self.start_ms = time.ticks_add(self.master_to_local_ms(start_master_ms), -offset_ms)
        else:
            self.start_ms = time.ticks_add(time.ticks_ms(), start_delay_ms - offset_ms)
        self.running = True
        self.last_heartbeat_ms = time.ticks_ms()
        self.last_event = "start"
        print("SCRIPT START id=%04X entries=%d offset=%d next=%d" % (script_id, len(self.entries), offset_ms, self.next_index))
        self.link.ack(pkt, CMD_SCRIPT_START)

    def handle_stop(self, pkt):
        # STOP halts the schedule and outputs but leaves the module armed and
        # the script loaded, so the master can resume from an offset.
        self.running = False
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
            self.node.stop_outputs()
            self.last_event = "stopped " + blocked[1]
            return
        if time.ticks_diff(time.ticks_ms(), self.last_heartbeat_ms) > self.heartbeat_timeout_ms:
            self.running = False
            self.node.stop_outputs()
            self.last_event = "hb timeout"
            print("SCRIPT stopped - heartbeat timeout")
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
            self.last_event = "complete"

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
        else:
            return False
        return True
