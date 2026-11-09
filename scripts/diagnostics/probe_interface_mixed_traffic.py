import argparse
import time

import serial
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "lib"))
from probe_usb_bridge import request
import bridge_radio
import nfcp


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", required=True)
    options = parser.parse_args()
    protocol = nfcp.NFCP(1)
    started = time.monotonic()
    heartbeat_at = started
    discover_at = started
    sequence = 10
    uid = None
    commands_sent = set()
    pending = {}
    with serial.Serial(options.port, 115200, timeout=0.2, write_timeout=2) as port:
        request(port, 1, bridge_radio.CMD_PING)
        while time.monotonic() - started < 15:
            now = time.monotonic()
            outgoing = []
            if now >= heartbeat_at:
                outgoing.extend([(0x18, b"MIXD"), (0x17, b"")])
                heartbeat_at = now + 1
            if now >= discover_at and now - started < 4:
                outgoing.append((0x10, protocol.build_discover_broadcast(0xC0DE)))
                discover_at = now + 0.9
            if uid is not None:
                for offset, command in ((5, 0x52), (7, 0x24), (11, 0x52)):
                    if now - started >= offset and offset not in commands_sent:
                        outgoing.append((0x10, protocol.build_unique_command(uid, command)))
                        commands_sent.add(offset)
            for command, payload in outgoing:
                sequence = (sequence + 1) & 255
                pending[sequence] = (command, now)
                frame = bridge_radio.build_frame(sequence, command, payload)
                port.write(b"NFUSB1 " + frame.hex().encode("ascii") + b"\n")
            line = port.readline().strip()
            if not line.startswith(b"NFUSB1 "):
                if line:
                    print("CONSOLE", line.decode("utf-8", "replace"), flush=True)
                continue
            try:
                data = bytes.fromhex(line[7:].decode("ascii"))
            except ValueError:
                continue
            frames = bridge_radio.BridgeFrameParser()
            for value in data:
                reply = frames.feed(value)
                if not reply:
                    continue
                if reply.flags & bridge_radio.FLAG_RESPONSE:
                    sent = pending.pop(reply.seq, None)
                    if sent:
                        print("ACK command=0x%02X elapsed=%.2fs" % (reply.cmd, now - sent[1]), flush=True)
                elif reply.cmd == bridge_radio.CMD_NFCP_RX:
                    received = bridge_radio.parse_nfcp_rx(reply.payload)
                    packets = nfcp.NFCPStreamParser()
                    for byte in received["nfcp_frame"]:
                        packet = packets.feed(byte)
                        if packet:
                            uid = packet["src"]
                            print("FIELD uid=0x%08X cmd=0x%02X" % (uid, packet["cmd"]), flush=True)
        overdue = [(command, time.monotonic() - sent) for command, sent in pending.values() if time.monotonic() - sent > 3]
        if overdue:
            raise RuntimeError("Unanswered mixed-traffic requests: %r" % overdue)
        print("Mixed heartbeat and radio query traffic completed.")


if __name__ == "__main__":
    main()
