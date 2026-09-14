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
import e77_radio

uart = UART(2, baudrate=256000, tx=Pin(15), rx=Pin(16))
parser = e77_radio.E77FrameParser()

def read_frame(seq, timeout_ms=9000):
    deadline = time.ticks_add(time.ticks_ms(), timeout_ms)
    while time.ticks_diff(deadline, time.ticks_ms()) > 0:
        if uart.any():
            data = uart.read(64) or b""
            for b in data:
                frame = parser.feed(b)
                if frame and frame.seq == seq:
                    return frame
        else:
            time.sleep_ms(2)
    return None

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
            "avg": e77_radio.int_from_be(payload[index + 1:index + 3], signed=True),
            "min": e77_radio.int_from_be(payload[index + 3:index + 5], signed=True),
            "max": e77_radio.int_from_be(payload[index + 5:index + 7], signed=True),
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

seq = 0x54
uart.write(e77_radio.build_frame(seq, e77_radio.CMD_SCAN_CHANNELS))
frame = read_frame(seq)
if not frame:
    print("SCAN_TIMEOUT")
elif frame.flags & e77_radio.FLAG_ERROR:
    print("SCAN_ERROR", bytes(frame.payload))
else:
    for item in parse_scan(frame.payload):
        print(
            "CH {channel:3d} Q {score:3d} max {max:4d} avg {avg:4d} busy {busy:2d}/{count:2d}".format(
                **item
            )
        )
'''


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--master", default="COM21")
    args = parser.parse_args()

    with serial.Serial(args.master, 115200, timeout=0.05, write_timeout=2) as port:
        raw.stop_running_program(port)
        raw.enter_raw_repl(port)
        try:
            output = raw.exec_raw(port, MASTER_CODE, timeout=12).decode("utf-8", "replace")
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
