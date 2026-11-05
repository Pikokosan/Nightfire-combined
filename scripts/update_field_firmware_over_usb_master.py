"""Safely replace one disarmed field-module file through the USB master."""
import argparse
import sys
import time
from pathlib import Path

import serial

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "main"))

import bridge_radio
import nfcp
from probe_usb_bridge import request
from probe_usb_modules import receive


class Updater:
    def __init__(self, port, uid):
        self.port = port
        self.uid = uid
        self.sequence = 0
        self.protocol = nfcp.NFCP(1)
        self.last_heartbeat = 0.0

    def bridge(self, command, payload=b""):
        self.sequence = self.sequence % 254 + 1
        return request(self.port, self.sequence, command, payload)

    def heartbeat(self, force=False):
        now = time.monotonic()
        if force or now - self.last_heartbeat >= 1.0:
            self.bridge(0x18, b"MSTR")
            self.last_heartbeat = now

    def _send_nfcp(self, frame, timeout, accept):
        self.sequence = self.sequence % 254 + 1
        sequence = self.sequence
        command = bridge_radio.build_frame(
            sequence, bridge_radio.CMD_SEND_NFCP, frame
        )
        self.port.write(b"NFUSB1 " + command.hex().encode("ascii") + b"\n")
        deadline = time.monotonic() + timeout
        bridge_ok = False
        packets = []
        while time.monotonic() < deadline:
            line = self.port.readline().strip()
            if not line.startswith(b"NFUSB1 "):
                continue
            try:
                raw = bytes.fromhex(line[7:].decode("ascii"))
            except ValueError:
                continue
            parser = bridge_radio.BridgeFrameParser()
            reply = None
            for value in raw:
                parsed = parser.feed(value)
                if parsed:
                    reply = parsed
            if reply is None:
                continue
            if reply.seq == sequence and reply.flags & bridge_radio.FLAG_RESPONSE:
                if reply.flags & bridge_radio.FLAG_ERROR:
                    raise RuntimeError(
                        "Bridge rejected NFCP command: %s" % bytes(reply.payload).hex()
                    )
                bridge_ok = True
            elif reply.cmd == bridge_radio.CMD_NFCP_RX:
                received = bridge_radio.parse_nfcp_rx(reply.payload)
                if received:
                    nfcp_parser = nfcp.NFCPStreamParser()
                    for value in received["nfcp_frame"]:
                        packet = nfcp_parser.feed(value)
                        if (
                            packet
                            and packet["frame_type"] == nfcp.SOF_RESPONSE
                            and accept(packet)
                        ):
                            packets.append((packet, received["rssi"]))
            if bridge_ok and packets:
                break
        if not bridge_ok:
            raise TimeoutError("No USB bridge transmit response")
        return packets

    def exchange(self, command, frame, retries=8, timeout=2.5):
        for attempt in range(retries):
            self.heartbeat()
            rejection = None
            def relevant(packet):
                data = bytes(packet["data"])
                return (
                    packet["src"] == self.uid
                    and packet["cmd"] in (nfcp.CMD_ACK, nfcp.CMD_NACK)
                    and len(data) == 2
                    and data[0] == command
                )

            for packet, unused_rssi in self._send_nfcp(frame, timeout, relevant):
                if packet["src"] != self.uid:
                    continue
                data = bytes(packet["data"])
                if packet["cmd"] == nfcp.CMD_ACK and data == bytes((command, 0)):
                    return
                if packet["cmd"] == nfcp.CMD_NACK and len(data) == 2 and data[0] == command:
                    rejection = data[1]
            if rejection is not None and attempt + 1 >= retries:
                raise RuntimeError(
                    "Field module repeatedly rejected 0x%02X (code %d)"
                    % (command, rejection)
                )
            if attempt + 1 < retries:
                reason = "NACK %d" % rejection if rejection is not None else "timeout"
                print(
                    "Retry 0x%02X (%d/%d, %s)"
                    % (command, attempt + 2, retries, reason),
                    flush=True,
                )
        raise TimeoutError("No field ACK for command 0x%02X" % command)

    def query(self, request_frame, response_command, timeout=3.0, retries=4):
        for attempt in range(retries):
            self.heartbeat()
            for packet, rssi in self._send_nfcp(
                request_frame,
                timeout,
                lambda packet: packet["src"] == self.uid
                and packet["cmd"] == response_command,
            ):
                if packet["src"] == self.uid and packet["cmd"] == response_command:
                    return bytes(packet["data"]), rssi
            if attempt + 1 < retries:
                print(
                    "Retry query 0x%02X (%d/%d)"
                    % (response_command, attempt + 2, retries),
                    flush=True,
                )
        raise TimeoutError("No field response 0x%02X" % response_command)


def info_fields(data):
    fields = []
    offset = 0
    while offset < len(data):
        length = data[offset]
        offset += 1
        if offset + length > len(data):
            raise RuntimeError("Malformed module INFO")
        fields.append(data[offset:offset + length].decode("utf-8", "replace"))
        offset += length
    return fields


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", required=True)
    parser.add_argument("--uid", required=True, type=lambda value: int(value, 0))
    parser.add_argument("--file", required=True, type=Path)
    parser.add_argument("--target", default="field_main.mpy")
    parser.add_argument("--expected-version", default="0.3.0")
    parser.add_argument("--transfer-id", type=lambda value: int(value, 0))
    options = parser.parse_args()

    image = options.file.read_bytes()
    if not image or len(image) > 256 * 1024:
        raise ValueError("Firmware must be 1 byte to 256 KiB")
    transfer = (
        options.transfer_id & 0xFFFF
        if options.transfer_id is not None
        else int(time.time()) & 0xFFFF
    )
    checksum = sum(image) & 0xFFFFFFFF

    with serial.Serial(options.port, 115200, timeout=0.08, write_timeout=2) as port:
        updater = Updater(port, options.uid)
        updater.bridge(bridge_radio.CMD_PING)
        updater.heartbeat(force=True)
        before, unused_rssi = updater.query(
            updater.protocol.build_info_request(options.uid), nfcp.CMD_INFO_RSP
        )
        before_fields = info_fields(before)
        if len(before_fields) < 2:
            raise RuntimeError("Field firmware identity unavailable")
        print(
            "Before: %s %s; transfer=0x%04X"
            % (before_fields[0], before_fields[1], transfer),
            flush=True,
        )

        begin = updater.protocol.build_ota_update_begin(
            options.uid, transfer, options.target, len(image), checksum
        )
        updater.exchange(nfcp.CMD_OTA_UPDATE_BEGIN, begin)
        try:
            for offset in range(0, len(image), 48):
                chunk = image[offset:offset + 48]
                updater.exchange(
                    nfcp.CMD_OTA_UPDATE_WRITE,
                    updater.protocol.build_ota_update_write(
                        options.uid, transfer, offset, chunk
                    ),
                )
                complete = offset + len(chunk)
                if complete == len(image) or complete % 2000 == 0:
                    print("Transferred %d/%d bytes" % (complete, len(image)), flush=True)
            updater.exchange(
                nfcp.CMD_OTA_UPDATE_END,
                updater.protocol.build_ota_update_end(options.uid, transfer),
            )
        except Exception:
            try:
                updater.exchange(
                    nfcp.CMD_OTA_UPDATE_ABORT,
                    updater.protocol.build_ota_update_abort(options.uid, transfer),
                    retries=1,
                )
            except Exception:
                pass
            raise

        print("Firmware stored; rebooting field module", flush=True)
        try:
            updater.exchange(
                nfcp.CMD_OTA_UPDATE_REBOOT,
                updater.protocol.build_ota_update_reboot(options.uid),
                retries=2,
            )
        except TimeoutError:
            # A reset immediately after ACK can race the RF reply.
            pass
        time.sleep(3.0)
        updater.heartbeat(force=True)
        after, rssi = updater.query(
            updater.protocol.build_info_request(options.uid), nfcp.CMD_INFO_RSP, timeout=4.0
        )
        fields = info_fields(after)
        if len(fields) < 2 or fields[0] != "field-module" or fields[1] != options.expected_version:
            raise RuntimeError("Updated firmware identity was not confirmed: %r" % fields[:2])

        status, unused_rssi = updater.query(
            updater.protocol.build_output_status_request(options.uid),
            nfcp.CMD_OUTPUT_STATUS_RSP,
        )
        if len(status) != 27 or status[0] != 1 or status[1] & 0x09 or any(status[3:11]):
            raise RuntimeError("Field output status is not safely disarmed: %s" % status.hex())
        print(
            "Verified %s %s, output status v%d, %d cues, safely disarmed, RSSI %d"
            % (fields[0], fields[1], status[0], status[2], rssi),
            flush=True,
        )


if __name__ == "__main__":
    main()
