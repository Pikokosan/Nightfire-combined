import argparse
import time

import serial

from probe_usb_bridge import request
import bridge_radio
import nfcp


def receive(port, timeout):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        line = port.readline().strip()
        if not line.startswith(b"NFUSB1 "):
            continue
        try:
            raw = bytes.fromhex(line[7:].decode("ascii"))
        except ValueError:
            continue
        parser = bridge_radio.BridgeFrameParser()
        frame = None
        for value in raw:
            result = parser.feed(value)
            if result:
                frame = result
        if not frame or frame.cmd != bridge_radio.CMD_NFCP_RX:
            continue
        received = bridge_radio.parse_nfcp_rx(frame.payload)
        if not received:
            continue
        packets = nfcp.NFCPStreamParser()
        for value in received["nfcp_frame"]:
            packet = packets.feed(value)
            if packet and packet["frame_type"] == nfcp.SOF_RESPONSE:
                yield packet, received["rssi"]


def main():
    arguments = argparse.ArgumentParser()
    arguments.add_argument("--port", required=True)
    options = arguments.parse_args()
    protocol = nfcp.NFCP(1)
    with serial.Serial(options.port, 115200, timeout=0.1, write_timeout=2) as port:
        request(port, 1, bridge_radio.CMD_PING)
        modules = {}
        nonce = 0xC0DE
        for sequence in range(2, 5):
            request(port, sequence, bridge_radio.CMD_SEND_NFCP, protocol.build_discover_broadcast(nonce))
            for packet, rssi in receive(port, 1):
                data = packet["data"]
                if packet["cmd"] != nfcp.CMD_DISCOVER_RSP or len(data) not in (10, 12):
                    continue
                if len(data) == 12 and int.from_bytes(data[10:12], "big") != nonce:
                    continue
                modules[packet["src"]] = data
                print("DISCOVER uid=0x%08X cues=%d type=0x%02X protocol=0x%02X rssi=%d" %
                      (packet["src"], data[6], data[7], data[8], rssi))
        sequence = 10
        for uid in modules:
            for command, expected in ((nfcp.CMD_INFO_REQ, nfcp.CMD_INFO_RSP),
                                      (nfcp.CMD_RESISTANCE_REQ, nfcp.CMD_RESISTANCE_RSP),
                                      (nfcp.CMD_CONTINUITY_REQ, nfcp.CMD_CONTINUITY_RSP)):
                sequence += 1
                request(port, sequence, bridge_radio.CMD_SEND_NFCP, protocol.build_unique_command(uid, command))
                found = False
                for packet, rssi in receive(port, 3):
                    if packet["src"] == uid and packet["cmd"] in (expected, nfcp.CMD_NACK):
                        print("REPLY uid=0x%08X cmd=0x%02X data=%s" % (uid, packet["cmd"], bytes(packet["data"]).hex()))
                        found = True
                if not found:
                    print("TIMEOUT uid=0x%08X query=0x%02X" % (uid, command))
        if not modules:
            print("No discovery replies received.")


if __name__ == "__main__":
    main()
