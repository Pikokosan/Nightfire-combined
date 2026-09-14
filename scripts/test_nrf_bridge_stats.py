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

DO_CLEAR = {do_clear}
uart = UART(2, baudrate=256000, tx=Pin(15), rx=Pin(16))
parser = e77_radio.E77FrameParser()

def read_frame(seq, timeout_ms=1000):
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

seq = 0x44
if DO_CLEAR:
    uart.write(e77_radio.build_frame(seq, e77_radio.CMD_CLEAR_STATS))
    frame = read_frame(seq, 1500)
    if not frame:
        print("CLEAR_TIMEOUT")
    elif frame.flags & e77_radio.FLAG_ERROR:
        print("CLEAR_ERROR", bytes(frame.payload))
    else:
        print("CLEAR_OK")
    seq = (seq + 1) & 0xFF

uart.write(e77_radio.build_frame(seq, e77_radio.CMD_GET_STATS))
frame = read_frame(seq, 1500)
if not frame:
    print("STATS_TIMEOUT")
elif frame.flags & e77_radio.FLAG_ERROR:
    print("STATS_ERROR", bytes(frame.payload))
else:
    stats = e77_radio.parse_stats_payload(frame.payload)
    print("STATS", stats)
'''


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--master", default="COM21")
    parser.add_argument("--clear", action="store_true")
    args = parser.parse_args()

    with serial.Serial(args.master, 115200, timeout=0.05, write_timeout=2) as port:
        raw.stop_running_program(port)
        raw.enter_raw_repl(port)
        try:
            code = MASTER_CODE.format(do_clear="True" if args.clear else "False")
            output = raw.exec_raw(port, code, timeout=5).decode("utf-8", "replace")
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
