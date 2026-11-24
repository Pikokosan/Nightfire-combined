"""Bench check of the audio box (audio_box/) through the USB bridge.

Needs a bridge running usb-master-0.4.7 or later and an audio box on the
same network and channel. Sends only the box's own commands (DISCOVER,
INFO, AUDIO_*, DISARM, OUTPUT_STATUS): never ARM, FIRE or SCRIPT_START, so
nothing can fire.

It checks: the box is discovered (device type 0x30, 0 cues) and names
itself; LOAD, PLAY, STATUS and STOP work; DISARM is ACKed and output status
says nothing is armed; the status position runs at real time.

The bridge passes only module replies to USB, not broadcasts, so SHOW_TIME
is checked from the bridge's radio counter (frames received): about 12 a
second while playing (4 messages, 3 copies each), about 12 more at the end
of a track or a stop, then none. With --box-port, the box's own counter of
SHOW_TIME messages sent is read over its USB port at the end (this stops
its program for a moment and restarts it).

Run: py -3 scripts/bench/test_audio_box_bench.py --port COM12 --box-port COM18
"""
import argparse
import os
import sys
import time
from pathlib import Path

import serial

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "main"))
import bridge_radio  # noqa: E402
import nfcp  # noqa: E402

TRACK = 7
TRACK_MS = 600000      # audio_box/configs/audio_box.py FAKE_TRACK_MS
HEARTBEAT_S = 0.3
CTRL_C, CTRL_D, CRLF = bytes((3,)), bytes((4,)), bytes((13, 10))


class Bridge:
    def __init__(self, port):
        self.port = port
        self.seq = 0
        self.session = os.urandom(4)
        self.last_beat = 0.0
        self.parser = bridge_radio.BridgeFrameParser()
        self.heard = []          # (pc time, nfcp packet, rssi)
        self.replies = {}        # seq -> frame

    def write(self, cmd, payload=b""):
        self.seq = (self.seq + 1) & 0xFF or 1
        # Sequence numbers wrap after 255: forget the old reply with this one.
        self.replies.pop(self.seq, None)
        frame = bridge_radio.build_frame(self.seq, cmd, payload)
        self.port.write(b"NFUSB1 " + frame.hex().encode("ascii") + b"\n")
        return self.seq

    def pump(self, seconds):
        """Read USB for `seconds`, keeping the host session alive."""
        end = time.monotonic() + seconds
        while True:
            now = time.monotonic()
            if now - self.last_beat >= HEARTBEAT_S:
                self.last_beat = now
                self.write(bridge_radio.CMD_HOST_HEARTBEAT, self.session)
            if now >= end:
                return
            line = self.port.readline().strip()
            if not line.startswith(b"NFUSB1 "):
                continue
            try:
                data = bytes.fromhex(line[7:].decode("ascii"))
            except ValueError:
                continue
            t = time.monotonic()
            for value in data:
                frame = self.parser.feed(value)
                if not frame:
                    continue
                if frame.cmd == bridge_radio.CMD_NFCP_RX:
                    rx = bridge_radio.parse_nfcp_rx(frame.payload)
                    packets = nfcp.NFCPStreamParser()
                    for b in rx["nfcp_frame"] if rx else b"":
                        pkt = packets.feed(b)
                        if pkt:
                            self.heard.append((t, pkt, rx["rssi"]))
                elif frame.flags & bridge_radio.FLAG_RESPONSE:
                    self.replies[frame.seq] = frame

    def request(self, uid, frame, want_cmd, timeout=3.0):
        """Reliable delivery to uid; the first response from uid with
        want_cmd (or a NACK), or None."""
        since = len(self.heard)
        seq = self.write(bridge_radio.CMD_SEND_NFCP_RELIABLE,
                         uid.to_bytes(4, "big") + int(timeout * 1000 - 300).to_bytes(2, "big") + frame)
        end = time.monotonic() + timeout
        while time.monotonic() < end:
            self.pump(0.05)
            reply = self.replies.get(seq)
            if reply is not None and reply.flags & bridge_radio.FLAG_ERROR:
                raise RuntimeError("bridge refused the command (is the app connected, or the bridge old?)")
            for t, pkt, rssi in self.heard[since:]:
                if (pkt["frame_type"] == nfcp.SOF_RESPONSE and pkt["src"] == uid and
                        pkt["cmd"] in (want_cmd, nfcp.CMD_NACK)):
                    return pkt
        return None

    def rx_frames(self):
        """The bridge radio's count of frames received."""
        seq = self.write(bridge_radio.CMD_GET_STATS)
        end = time.monotonic() + 2
        while time.monotonic() < end:
            self.pump(0.05)
            reply = self.replies.get(seq)
            if reply is not None:
                return bridge_radio.parse_stats_payload(reply.payload)["rx_ok"]
        return None

    def rx_rate(self, seconds):
        """Frames a second the bridge receives over `seconds`."""
        start, t0 = self.rx_frames(), time.monotonic()
        self.pump(seconds)
        end, t1 = self.rx_frames(), time.monotonic()
        return (end - start) / (t1 - t0)


RESULTS = []


def check(label, ok, detail=""):
    RESULTS.append(ok)
    print("%s  %-40s %s" % ("PASS" if ok else "FAIL", label, detail), flush=True)


def ack(bridge, uid, frame, cmd):
    pkt = bridge.request(uid, frame, nfcp.CMD_ACK)
    if pkt is None:
        return None
    d = bytes(pkt["data"])
    return ("ACK" if pkt["cmd"] == nfcp.CMD_ACK else "NACK", d[1]) if len(d) == 2 and d[0] == cmd else None


def status(bridge, uid):
    pkt = bridge.request(uid, nfcp.NFCP(1).build_audio_status_request(uid), nfcp.CMD_AUDIO_STATUS_RSP)
    if pkt is None or pkt["cmd"] != nfcp.CMD_AUDIO_STATUS_RSP or len(pkt["data"]) != 12:
        return None
    d = bytes(pkt["data"])
    return int.from_bytes(d[0:2], "big"), int.from_bytes(d[2:6], "big"), d[6], d[7], int.from_bytes(d[8:12], "big")


def main():
    args = argparse.ArgumentParser()
    args.add_argument("--port", default="COM12")
    args.add_argument("--box-port", help="the box's own USB port, to read its SHOW_TIME counter")
    args.add_argument("--play-s", type=float, default=20.0, help="how long to read status while playing")
    options = args.parse_args()
    master = nfcp.NFCP(1)

    with serial.Serial(options.port, 115200, timeout=0.02, write_timeout=2) as port:
        bridge = Bridge(port)
        bridge.pump(1.0)
        info_seq = bridge.write(bridge_radio.CMD_GET_INFO)
        bridge.pump(0.5)
        info = bridge.replies.get(info_seq)
        version = bytes(info.payload[9:]).decode() if info else "?"
        check("bridge firmware", version >= "usb-master-0.4.7", version)

        # Discover: find the box by its device type.
        since = time.monotonic()
        bridge.write(bridge_radio.CMD_SEND_NFCP, master.build_discover_broadcast(0xB0C5))
        bridge.pump(1.5)
        boxes = {}
        for t, pkt, rssi in bridge.heard:
            if t >= since and pkt["cmd"] == nfcp.CMD_DISCOVER_RSP and len(pkt["data"]) >= 10:
                d = bytes(pkt["data"])
                print("      found uid=0x%08X type=0x%02X cues=%d rssi=%d" % (pkt["src"], d[7], d[6], rssi))
                if d[7] == 0x30:
                    boxes[pkt["src"]] = d
        check("box discovered (type 0x30, 0 cues)", len(boxes) == 1 and list(boxes.values())[0][6] == 0,
              ", ".join("0x%08X" % u for u in boxes))
        if len(boxes) != 1:
            return 1
        uid = list(boxes)[0]

        pkt = bridge.request(uid, master.build_info_request(uid), nfcp.CMD_INFO_RSP)
        fields, d = [], bytes(pkt["data"]) if pkt else b""
        while d:
            fields.append(d[1:1 + d[0]].decode("utf-8", "replace"))
            d = d[1 + d[0]:]
        check("box INFO", fields[:2] == ["audio-box", "0.1.1"], " | ".join(fields[:4]))

        check("DISARM acked", ack(bridge, uid, master.build_disarm_unique(uid), nfcp.CMD_DISARM) == ("ACK", 0))
        pkt = bridge.request(uid, master.build_output_status_request(uid), nfcp.CMD_OUTPUT_STATUS_RSP)
        check("output status: nothing armed", pkt is not None and pkt["cmd"] == nfcp.CMD_OUTPUT_STATUS_RSP and
              bytes(pkt["data"])[1] == 0, bytes(pkt["data"]).hex() if pkt else "no reply")

        check("LOAD unknown track 0 refused", ack(bridge, uid, master.build_audio_load(uid, 0),
                                                  nfcp.CMD_AUDIO_LOAD) == ("NACK", nfcp.NACK_INVALID_VALUE))
        check("LOAD track", ack(bridge, uid, master.build_audio_load(uid, TRACK), nfcp.CMD_AUDIO_LOAD) == ("ACK", 0))
        s = status(bridge, uid)
        check("status after LOAD", s == (TRACK, 0, nfcp.SHOW_STOPPED, 0xFF, TRACK_MS), str(s))

        idle = bridge.rx_rate(5.0)
        print("      bridge hears %.1f frames/s with the box idle" % idle)

        # Play: SHOW_TIME adds about 12 frames a second at the bridge.
        played = time.monotonic()
        check("PLAY", ack(bridge, uid, master.build_audio_play(uid, TRACK, 0), nfcp.CMD_AUDIO_PLAY) == ("ACK", 0))
        reads = []
        for unused in range(int(options.play_s / 2)):
            s = status(bridge, uid)
            if s:
                reads.append((time.monotonic() - played, s))
            bridge.pump(1.0)
        ok = reads and all(s[0] == TRACK and s[2] == nfcp.SHOW_PLAYING for t, s in reads)
        late = [abs(s[1] - t * 1000) for t, s in reads]
        check("status while playing", bool(ok and max(late) < 400),
              "%d reads, position within %d ms of the PC clock" % (len(reads), max(late) if late else -1))
        if len(reads) >= 2:
            (t0, s0), (t1, s1) = reads[0], reads[-1]
            ppm = ((s1[1] - s0[1]) / ((t1 - t0) * 1000.0) - 1) * 1e6
            print("      box clock vs PC over %.0f s: %+.0f ppm (status round trips included)" % (t1 - t0, ppm))
        playing = bridge.rx_rate(5.0)
        check("SHOW_TIME on air while playing", 9 <= playing - idle <= 15,
              "%.1f frames/s, %.1f more than idle (4 a second x 3 copies = 12)" % (playing, playing - idle))

        # Replay near the end: the track ends, then the box goes quiet.
        check("PLAY near the end", ack(bridge, uid, master.build_audio_play(uid, TRACK, TRACK_MS - 2000),
                                       nfcp.CMD_AUDIO_PLAY) == ("ACK", 0))
        bridge.pump(4.0)
        s = status(bridge, uid)
        check("status ended", s is not None and s[1:3] == (TRACK_MS, nfcp.SHOW_ENDED), str(s))
        after_end = bridge.rx_rate(5.0)
        check("quiet after the end", after_end - idle < 1.5,
              "%.1f frames/s (idle %.1f)" % (after_end, idle))

        # Play, then STOP.
        check("PLAY again", ack(bridge, uid, master.build_audio_play(uid, TRACK, 1000),
                                nfcp.CMD_AUDIO_PLAY) == ("ACK", 0))
        bridge.pump(1.0)
        check("STOP", ack(bridge, uid, master.build_audio_stop(uid, TRACK), nfcp.CMD_AUDIO_STOP) == ("ACK", 0))
        bridge.pump(2.0)
        s = status(bridge, uid)
        check("status stopped", s is not None and s[2] == nfcp.SHOW_STOPPED and 1900 <= s[1] <= 2600, str(s))
        after_stop = bridge.rx_rate(5.0)
        check("quiet after STOP", after_stop - idle < 1.5, "%.1f frames/s (idle %.1f)" % (after_stop, idle))
        busy_s = time.monotonic() - played

    if options.box_port:
        # Read the box's counters in its REPL, then restart its program.
        with serial.Serial(options.box_port, 115200, timeout=0.2) as box:
            box.write(CTRL_C + CTRL_C)
            time.sleep(0.4)
            box.reset_input_buffer()
            box.write(b"import audio_main as a; print('SENT', a.box.sent, a.box.player.state)" + CRLF)
            time.sleep(0.6)
            text = box.read(4000).decode("utf-8", "replace")
            box.write(CTRL_D)              # soft reset: the box program starts again
        sent = [line.split() for line in text.splitlines() if line.startswith("SENT ")]
        if sent:
            # Plays: options.play_s + ~10 s of reads, 2 s to the end, 1 s; plus 4 + 4 tail messages.
            print("      box sent %s SHOW_TIME messages over %.0f s; state now %s" % (sent[0][1], busy_s, sent[0][2]))
            check("box counter read", True)
        else:
            check("box counter read", False, text[-200:])

    print("%d of %d passed" % (sum(RESULTS), len(RESULTS)))
    return 0 if all(RESULTS) else 1


if __name__ == "__main__":
    sys.exit(main())
