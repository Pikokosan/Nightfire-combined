import argparse
import time

import serial

from test_nrf_radio_pair import (
    CTRL_B,
    enter_raw_repl,
    exec_raw,
    soft_reset,
    stop_running_program,
)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--master", required=True)
    parser.add_argument("--target", required=True, help="Target unique id, for example 0x52840015")
    parser.add_argument("--cmd", choices=["ping", "continuity"], default="ping")
    parser.add_argument("--network", default="0x04D2")
    parser.add_argument("--channel", type=int, default=40)
    parser.add_argument("--address", default="0x67676767")
    parser.add_argument("--timeout-ms", type=int, default=2500)
    args = parser.parse_args()

    network_id = int(args.network, 0)
    address = int(args.address, 0)
    target = int(args.target, 0)

    master = serial.Serial(args.master, 115200, timeout=0.1, write_timeout=1)
    try:
        print("Stopping master app on", args.master)
        print(repr(stop_running_program(master)[-200:]))
        enter_raw_repl(master)

        code = """
import time
import nrf_radio
import nfcp

NETWORK_ID = {network_id}
CHANNEL = {channel}
ADDRESS = {address}
TARGET = {target}
TEST_CMD = "{cmd}"
TIMEOUT_MS = {timeout_ms}

nrf_radio.init(channel=CHANNEL, tx_power=8, address=ADDRESS)
tx = nfcp.NFCP(0x01)
parser = nfcp.NFCPStreamParser()
if TEST_CMD == "ping":
    frame = tx.build_ping(TARGET, b"nfcp-ping")
else:
    frame = tx.build_continuity_request(TARGET)
nrf_radio.send((NETWORK_ID & 0xFFFF).to_bytes(2, "big") + frame)

deadline = time.ticks_add(time.ticks_ms(), TIMEOUT_MS)
count = 0
while time.ticks_diff(deadline, time.ticks_ms()) > 0:
    payload = nrf_radio.recv(100)
    if not payload or len(payload) < 3:
        continue
    if int.from_bytes(payload[:2], "big") != NETWORK_ID:
        continue
    for b in payload[2:]:
        pkt = parser.feed(b)
        if pkt:
            data = pkt.get("data", b"")
            print(
                "PKT frame=%s src=0x%08X cmd=0x%02X data=%s rssi=%s" %
                (
                    pkt.get("frame_type"),
                    pkt.get("src", 0),
                    pkt.get("cmd", 0),
                    " ".join("%02X" % x for x in data),
                    nrf_radio.last_rssi(),
                )
            )
            count += 1
print("COUNT", count)
""".format(
            network_id=network_id,
            channel=args.channel,
            address=address,
            target=target,
            cmd=args.cmd,
            timeout_ms=args.timeout_ms,
        )

        out = exec_raw(master, code, timeout=(args.timeout_ms / 1000) + 5)
        print(out.decode("utf-8", "replace"))
        master.write(CTRL_B)
        time.sleep(0.2)
        print("Resetting master app...")
        print(repr(soft_reset(master)[-300:]))
    finally:
        master.close()


if __name__ == "__main__":
    main()
