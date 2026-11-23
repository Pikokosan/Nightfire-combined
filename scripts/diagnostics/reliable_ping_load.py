"""Load a USB bridge with reliable pings to one module and report how often
a delivery needed a retry, and the round-trip times. No ARM and no FIRE.

Drives the bridge like the app: host heartbeats, then COUNT reliable pings
(CMD_SEND_NFCP_RELIABLE) one after another. A retry means the module's
reply to the first attempt never reached the bridge. Compare two bridge
firmwares by running this on each, alternating, in the same session: the
radio conditions drift over minutes.

    py -3 scripts/diagnostics/reliable_ping_load.py --bridge COM12 --uid D96E30DD --count 300

A bridge bundle that prints "BUSY ..." or "PROF ..." lines (timing builds)
has them collected and printed at the end.
"""
import argparse
import os
import statistics
import sys
import time

import serial

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(ROOT, "main"))
import bridge_radio  # noqa: E402
import nfcp  # noqa: E402

SESSION = os.urandom(4)      # a new app session, like the app on each connect
HEARTBEAT_S = 0.8
TIMEOUT_MS = 1500


class Bridge:
    def __init__(self, port_name):
        self.port = serial.Serial(port_name, 115200, timeout=0.01, write_timeout=2)
        self.parser = bridge_radio.BridgeFrameParser()
        self.buffer = bytearray()
        self.console = []
        self.last_beat = 0.0

    def send(self, seq, cmd, payload=b""):
        frame = bridge_radio.build_frame(seq, cmd, payload)
        self.port.write(b"NFUSB1 " + frame.hex().encode() + b"\n")

    def heartbeat(self):
        if time.monotonic() - self.last_beat > HEARTBEAT_S:
            self.send(200, bridge_radio.CMD_HOST_HEARTBEAT, SESSION)
            self.last_beat = time.monotonic()

    def frames(self):
        self.buffer += self.port.read(4096)
        found = []
        while b"\n" in self.buffer:
            line, _, rest = bytes(self.buffer).partition(b"\n")
            self.buffer = bytearray(rest)
            line = line.strip()
            if line.startswith(b"NFUSB1 "):
                try:
                    found.extend(self.parser.feed_bytes(bytes.fromhex(line[7:].decode())))
                except ValueError:
                    pass
            elif line.startswith((b"BUSY", b"PROF")):
                self.console.append(line.decode(errors="replace"))
        return found


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--bridge", default="COM12")
    ap.add_argument("--uid", required=True, help="module unique id, hex")
    ap.add_argument("--count", type=int, default=300)
    args = ap.parse_args()
    uid = int(args.uid, 16)
    protocol = nfcp.NFCP(1)
    bridge = Bridge(args.bridge)
    time.sleep(0.5)
    # A new app session makes a master bridge send STOP and refuse commands
    # until that is done (about 120 ms), so start after it.
    bridge.heartbeat()
    end = time.monotonic() + 0.5
    while time.monotonic() < end:
        bridge.frames()
        time.sleep(0.02)
    attempts, trips, failed = [], [], 0
    for index in range(args.count + 1):
        bridge.heartbeat()
        seq = 1 + index % 150
        start = time.monotonic()
        bridge.send(seq, bridge_radio.CMD_SEND_NFCP_RELIABLE,
                    uid.to_bytes(4, "big") + TIMEOUT_MS.to_bytes(2, "big") +
                    protocol.build_ping(uid, b"\x01"))
        result = None
        while result is None and time.monotonic() - start < 4:
            for frame in bridge.frames():
                if frame.cmd == bridge_radio.CMD_SEND_NFCP_RELIABLE and frame.seq == seq:
                    result = bytes(frame.payload)
            bridge.heartbeat()
        if index == 0:
            continue            # warm-up: the bridge learns the route
        if result is None or len(result) < 7 or result[0] != 0:
            failed += 1
            print("ping %d failed: %s" % (index, "no answer" if result is None else
                                          "bridge answered %s" % result.hex()))
            continue
        attempts.append(result[6])
        trips.append((time.monotonic() - start) * 1000)
        time.sleep(0.03)
    end = time.monotonic() + 3
    while time.monotonic() < end:
        bridge.frames()
        time.sleep(0.05)
    if not trips:
        print("no ping was delivered (%d failed)" % failed)
        return 1
    trips.sort()
    print("pings %d  failed %d  retried %d  median %.0f ms  p95 %.0f ms  max %.0f ms" % (
        len(attempts), failed, sum(1 for a in attempts if a > 1), statistics.median(trips),
        trips[int(len(trips) * 0.95)], trips[-1]))
    for line in bridge.console:
        print(line)
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
