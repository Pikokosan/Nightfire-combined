from __future__ import print_function

import argparse
import sys
import time

import serial

sys.path.insert(0, "scripts")

from test_nrf_radio_pair import (
    CTRL_B,
    enter_raw_repl,
    exec_raw,
    soft_reset,
    stop_running_program,
)


CODE = r'''
import time
import nrf_radio
import nfcp

NETWORK_ID = {network_id}
ADDRESS = {address}
TARGET = {target}
CURRENT_CHANNEL = {current_channel}
NEW_CHANNEL = {new_channel}
TIMEOUT_MS = {timeout_ms}

tx = nfcp.NFCP(0x01)
parser = nfcp.NFCPStreamParser()

def send_frame(channel, frame):
    nrf_radio.init(channel=channel, tx_power=8, address=ADDRESS)
    nrf_radio.send((NETWORK_ID & 0xFFFF).to_bytes(2, "big") + frame)

def wait_packets(channel, timeout_ms):
    nrf_radio.init(channel=channel, tx_power=8, address=ADDRESS)
    deadline = time.ticks_add(time.ticks_ms(), timeout_ms)
    out = []
    while time.ticks_diff(deadline, time.ticks_ms()) > 0:
        payload = nrf_radio.recv(100)
        if not payload or len(payload) < 3:
            continue
        if int.from_bytes(payload[:2], "big") != NETWORK_ID:
            continue
        for b in payload[2:]:
            pkt = parser.feed(b)
            if pkt:
                out.append(pkt)
                data = pkt.get("data", b"")
                print(
                    "PKT ch=%d frame=%s src=0x%08X cmd=0x%02X data=%s rssi=%s" %
                    (
                        channel,
                        pkt.get("frame_type"),
                        pkt.get("src", 0),
                        pkt.get("cmd", 0),
                        " ".join("%02X" % x for x in data),
                        nrf_radio.last_rssi(),
                    )
                )
    return out

def wait_ack(channel, orig_cmd):
    packets = wait_packets(channel, TIMEOUT_MS)
    for pkt in packets:
        if (pkt.get("src", 0) & 0xFFFFFFFF) != (TARGET & 0xFFFFFFFF):
            continue
        data = pkt.get("data", b"")
        if pkt.get("cmd") == nfcp.CMD_ACK and len(data) >= 2 and data[0] == orig_cmd:
            return data[1] == nfcp.ACK_OK
    return False

def discover(channel):
    send_frame(channel, tx.build_discover_broadcast(0xBEEF))
    found = []
    for pkt in wait_packets(channel, TIMEOUT_MS):
        if pkt.get("cmd") == nfcp.CMD_DISCOVER_RSP:
            found.append(pkt.get("src", 0) & 0xFFFFFFFF)
    print("DISCOVER_CH", channel, ["0x%08X" % uid for uid in found])
    return found

print("SET_TARGET_TO", NEW_CHANNEL)
send_frame(CURRENT_CHANNEL, tx.build_set_radio_channel(TARGET, NEW_CHANNEL))
print("SET_ACK", wait_ack(CURRENT_CHANNEL, nfcp.CMD_SET_RADIO_CHANNEL))
time.sleep_ms(200)
discover(NEW_CHANNEL)

print("RESTORE_TARGET_TO", CURRENT_CHANNEL)
send_frame(NEW_CHANNEL, tx.build_set_radio_channel(TARGET, CURRENT_CHANNEL))
print("RESTORE_ACK", wait_ack(NEW_CHANNEL, nfcp.CMD_SET_RADIO_CHANNEL))
time.sleep_ms(200)
discover(CURRENT_CHANNEL)
'''


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--master", default="COM13")
    parser.add_argument("--target", required=True)
    parser.add_argument("--current-channel", type=int, default=40)
    parser.add_argument("--new-channel", type=int, default=84)
    parser.add_argument("--network", default="0x04D2")
    parser.add_argument("--address", default="0x67676767")
    parser.add_argument("--timeout-ms", type=int, default=3000)
    args = parser.parse_args()

    code = CODE.format(
        network_id=int(args.network, 0),
        address=int(args.address, 0),
        target=int(args.target, 0),
        current_channel=args.current_channel,
        new_channel=args.new_channel,
        timeout_ms=args.timeout_ms,
    )

    master = serial.Serial(args.master, 115200, timeout=0.1, write_timeout=1)
    try:
        print("Stopping master app on", args.master)
        print(repr(stop_running_program(master)[-200:]))
        enter_raw_repl(master)
        out = exec_raw(master, code, timeout=(args.timeout_ms / 1000) * 4 + 8)
        print(out.decode("utf-8", "replace"))
        master.write(CTRL_B)
        time.sleep(0.2)
        print("Resetting master app...")
        print(repr(soft_reset(master)[-300:]))
    finally:
        master.close()


if __name__ == "__main__":
    main()
