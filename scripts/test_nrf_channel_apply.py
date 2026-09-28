from __future__ import print_function

import argparse
import sys
import time

import serial

sys.path.insert(0, "scripts")
import raw_micropython_upload as raw


MASTER_CODE = r'''
from machine import UART, Pin
import time
import bridge_radio
import nfcp

CURRENT_CHANNEL = __CURRENT_CHANNEL__
SELECT_RANK = __RANK__
CHANNEL_OVERRIDE = __CHANNEL_OVERRIDE__
NETWORK_ID = __NETWORK_ID__
TIMEOUT_MS = __TIMEOUT_MS__
DISCOVER_REPEAT_MS = 700

uart = UART(2, baudrate=256000, tx=Pin(15), rx=Pin(16))
radio_parser = bridge_radio.BridgeFrameParser()
nfcp_parser = nfcp.NFCPStreamParser()
tx = nfcp.NFCP(0x01)
seq = 0x60

def next_seq():
    global seq
    out = seq & 0xFF
    seq = (seq + 1) & 0xFF
    if seq == 0:
        seq = 1
    return out

def read_local_frame(want_seq, timeout_ms=2000):
    deadline = time.ticks_add(time.ticks_ms(), timeout_ms)
    while time.ticks_diff(deadline, time.ticks_ms()) > 0:
        if uart.any():
            data = uart.read(64) or b""
            for b in data:
                frame = radio_parser.feed(b)
                if frame and frame.seq == want_seq:
                    return frame
        else:
            time.sleep_ms(2)
    return None

def local_command(cmd, payload=b"", timeout_ms=2000):
    s = next_seq()
    uart.write(bridge_radio.build_frame(s, cmd, payload))
    return read_local_frame(s, timeout_ms)

def parse_scan(payload):
    out = []
    if not payload:
        return out
    count = payload[0]
    index = 1
    entry_size = 7
    if len(payload) >= 1 + count * 10:
        entry_size = 10
    for _ in range(count):
        if index + entry_size > len(payload):
            break
        item = {
            "channel": payload[index],
            "avg": bridge_radio.int_from_be(payload[index + 1:index + 3], signed=True),
            "min": bridge_radio.int_from_be(payload[index + 3:index + 5], signed=True),
            "max": bridge_radio.int_from_be(payload[index + 5:index + 7], signed=True),
            "count": 0,
            "busy": 0,
            "score": 0,
        }
        if entry_size >= 10:
            item["count"] = payload[index + 7]
            item["busy"] = payload[index + 8]
            item["score"] = payload[index + 9]
        out.append(item)
        index += entry_size
    return out

def bridge_set_channel(channel):
    frame = local_command(bridge_radio.CMD_SET_CHANNEL, bytes([channel & 0xFF]), 1500)
    return bool(frame and frame.cmd == bridge_radio.CMD_SET_CHANNEL and not (frame.flags & bridge_radio.FLAG_ERROR))

def send_nfcp(frame, timeout_ms=2200):
    return local_command(bridge_radio.CMD_SEND_NFCP, frame, timeout_ms)

def poll_nfcp(timeout_ms=500):
    deadline = time.ticks_add(time.ticks_ms(), timeout_ms)
    out = []
    while time.ticks_diff(deadline, time.ticks_ms()) > 0:
        if uart.any():
            data = uart.read(128) or b""
            for b in data:
                local = radio_parser.feed(b)
                if not local or local.cmd != bridge_radio.CMD_NFCP_RX:
                    continue
                rx = bridge_radio.parse_nfcp_rx(local.payload)
                if not rx:
                    continue
                for nb in rx["nfcp_frame"]:
                    pkt = nfcp_parser.feed(nb)
                    if pkt:
                        pkt["rssi"] = rx.get("rssi", 0)
                        out.append(pkt)
        else:
            time.sleep_ms(2)
    return out

def discover(timeout_ms=TIMEOUT_MS):
    global nfcp_parser
    nfcp_parser = nfcp.NFCPStreamParser()
    frame = tx.build_discover_broadcast(0xCAFE)
    send_nfcp(frame, 2500)
    found = {}
    last_broadcast_ms = time.ticks_ms()
    deadline = time.ticks_add(time.ticks_ms(), timeout_ms)
    while time.ticks_diff(deadline, time.ticks_ms()) > 0:
        now_ms = time.ticks_ms()
        if time.ticks_diff(now_ms, last_broadcast_ms) >= DISCOVER_REPEAT_MS:
            send_nfcp(frame, 2500)
            last_broadcast_ms = now_ms
        for pkt in poll_nfcp(100):
            if pkt.get("frame_type") != nfcp.SOF_RESPONSE or pkt.get("cmd") != nfcp.CMD_DISCOVER_RSP:
                continue
            data = pkt.get("data", b"")
            if len(data) < 10:
                continue
            uid = int.from_bytes(data[2:6], "big")
            found[uid] = pkt.get("rssi", 0)
    return found

def wait_ack(uid, orig_cmd, timeout_ms=2200):
    deadline = time.ticks_add(time.ticks_ms(), timeout_ms)
    while time.ticks_diff(deadline, time.ticks_ms()) > 0:
        for pkt in poll_nfcp(100):
            if (pkt.get("src", 0) & 0xFFFFFFFF) != (uid & 0xFFFFFFFF):
                continue
            data = pkt.get("data", b"")
            if len(data) >= 2 and data[0] == orig_cmd:
                if pkt.get("cmd") == nfcp.CMD_ACK:
                    return data[1] == nfcp.ACK_OK
                if pkt.get("cmd") == nfcp.CMD_NACK:
                    return False
    return False

def set_module_channel(uid, channel):
    for attempt in range(2):
        send_nfcp(tx.build_set_radio_channel(uid, channel), 2500)
        if wait_ack(uid, nfcp.CMD_SET_RADIO_CHANNEL):
            return True
        time.sleep_ms(100 + attempt * 100)
    return False

def save_master_channel(channel):
    try:
        import ujson as json
    except ImportError:
        import json

    try:
        with open("nfcp_settings.json", "r") as f:
            settings = json.load(f)
    except Exception:
        settings = {}

    settings["radio_channel"] = channel
    with open("nfcp_settings.json", "w") as f:
        json.dump(settings, f)

bridge_set_channel(CURRENT_CHANNEL)
scan_frame = local_command(bridge_radio.CMD_SCAN_CHANNELS, b"", 9000)
if not scan_frame or scan_frame.flags & bridge_radio.FLAG_ERROR:
    print("SCAN_FAILED")
else:
    scan = parse_scan(scan_frame.payload)
    for item in scan:
        print(
            "SCAN CH {channel:3d} Q {score:3d} max {max:4d} avg {avg:4d} busy {busy:2d}/{count:2d}".format(
                **item
            )
        )
    if CHANNEL_OVERRIDE >= 0:
        target_channel = CHANNEL_OVERRIDE
    elif len(scan) > SELECT_RANK:
        target_channel = scan[SELECT_RANK]["channel"]
    elif scan:
        target_channel = scan[0]["channel"]
    else:
        target_channel = CURRENT_CHANNEL

    print("TARGET_CHANNEL", target_channel)
    bridge_set_channel(CURRENT_CHANNEL)
    before = discover()
    print("DISCOVER_BEFORE", " ".join("0x%08X" % uid for uid in sorted(before.keys())))
    if not before:
        print("NO_MODULES_FOUND")
        raise SystemExit

    ok_count = 0
    fail_count = 0
    for uid in sorted(before.keys()):
        if set_module_channel(uid, target_channel):
            print("MODULE_SET_OK 0x%08X" % uid)
            ok_count += 1
        else:
            print("MODULE_SET_FAIL 0x%08X" % uid)
            fail_count += 1

    if fail_count:
        print("ABORT_BRIDGE_STAYS", CURRENT_CHANNEL)
    elif bridge_set_channel(target_channel):
        print("BRIDGE_SET_OK", target_channel)
        save_master_channel(target_channel)
        print("MASTER_SETTINGS_CHANNEL", target_channel)
        after = discover()
        print("DISCOVER_AFTER", " ".join("0x%08X" % uid for uid in sorted(after.keys())))
    else:
        print("BRIDGE_SET_FAIL")
'''


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--master", default="COM21")
    parser.add_argument("--current-channel", type=int, default=40)
    parser.add_argument("--rank", type=int, default=0)
    parser.add_argument("--channel", type=int, default=-1)
    parser.add_argument("--network", default="0x04D2")
    parser.add_argument("--timeout-ms", type=int, default=3000)
    args = parser.parse_args()

    code = MASTER_CODE
    code = code.replace("__CURRENT_CHANNEL__", str(args.current_channel))
    code = code.replace("__RANK__", str(args.rank))
    code = code.replace("__CHANNEL_OVERRIDE__", str(args.channel))
    code = code.replace("__NETWORK_ID__", str(int(args.network, 0)))
    code = code.replace("__TIMEOUT_MS__", str(args.timeout_ms))

    with serial.Serial(args.master, 115200, timeout=0.05, write_timeout=2) as port:
        raw.stop_running_program(port)
        raw.enter_raw_repl(port)
        try:
            output = raw.exec_raw(port, code, timeout=35).decode("utf-8", "replace")
            sys.stdout.write(output)
            sys.stdout.flush()
        finally:
            try:
                raw.exit_raw_repl(port)
            except Exception:
                pass
            try:
                port.write(raw.CTRL_D)
                time.sleep(0.5)
            except Exception:
                pass


if __name__ == "__main__":
    main()
