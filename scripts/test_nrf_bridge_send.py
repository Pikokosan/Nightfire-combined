from __future__ import print_function

import argparse
import sys
import time

import serial

sys.path.insert(0, "main")
sys.path.insert(0, "scripts")

import e77_radio
import raw_micropython_upload as raw


CODE = r'''
from machine import UART, Pin
import e77_radio
import time

uart = UART(2, baudrate=256000, tx=Pin(15), rx=Pin(16))
parser = e77_radio.E77FrameParser()

def read_one(timeout_ms=1500):
    deadline = time.ticks_add(time.ticks_ms(), timeout_ms)
    while time.ticks_diff(deadline, time.ticks_ms()) > 0:
        if uart.any():
            data = uart.read(64) or b""
            for b in data:
                frame = parser.feed(b)
                if frame:
                    return frame
        else:
            time.sleep_ms(5)
    return None

def command(seq, cmd, payload=b"", timeout_ms=1500):
    uart.write(e77_radio.build_frame(seq, cmd, payload))
    frame = read_one(timeout_ms)
    if not frame:
        print("CMD", seq, cmd, "TIMEOUT")
        return None
    print("CMD", seq, "RX", frame.cmd, frame.flags, bytes(frame.payload))
    return frame

command(1, e77_radio.CMD_GET_INFO)
command(2, e77_radio.CMD_CLEAR_STATS)
command(3, e77_radio.CMD_GET_STATS)
command(4, e77_radio.CMD_SEND_NFCP, b"abc", 3000)
command(5, e77_radio.CMD_GET_STATS)
'''


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--master", default="COM21")
    args = parser.parse_args()

    with serial.Serial(args.master, 115200, timeout=0.05, write_timeout=2) as port:
        raw.stop_running_program(port)
        raw.enter_raw_repl(port)
        try:
            output = raw.exec_raw(port, CODE, timeout=12).decode("utf-8", "replace")
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
