import argparse
import sys
import time
from pathlib import Path

import serial

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "main"))
import bridge_radio


def request(port, sequence, command, payload=b""):
    frame = bridge_radio.build_frame(sequence, command, payload)
    port.write(b"NFUSB1 " + frame.hex().encode("ascii") + b"\n")
    deadline = time.monotonic() + 4
    parser = bridge_radio.BridgeFrameParser()
    console = []
    while time.monotonic() < deadline:
        line = port.readline().strip()
        if not line.startswith(b"NFUSB1 "):
            if line:
                console.append(line)
            continue
        try:
            data = bytes.fromhex(line[7:].decode("ascii"))
        except ValueError:
            continue
        for value in data:
            reply = parser.feed(value)
            if reply and reply.seq == sequence and reply.flags & bridge_radio.FLAG_RESPONSE:
                if reply.flags & bridge_radio.FLAG_ERROR:
                    raise RuntimeError("Bridge rejected command: %s" % bytes(reply.payload).hex())
                expected = bridge_radio.CMD_PONG if command == bridge_radio.CMD_PING else command
                if reply.cmd == expected:
                    return reply
    raise TimeoutError("No USB bridge response. Console: %r" % console[-5:])


def main():
    arguments = argparse.ArgumentParser()
    arguments.add_argument("--port", required=True)
    options = arguments.parse_args()
    with serial.Serial(options.port, 115200, timeout=0.2, write_timeout=2) as port:
        request(port, 1, bridge_radio.CMD_PING)
        print("USB bridge PONG received")
        info = request(port, 2, bridge_radio.CMD_GET_INFO)
        print("INFO", bridge_radio.parse_get_info(info.payload))
        stats = request(port, 3, bridge_radio.CMD_GET_STATS)
        print("STATS", bridge_radio.parse_stats_payload(stats.payload))


if __name__ == "__main__":
    main()
