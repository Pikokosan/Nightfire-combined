from __future__ import print_function

import argparse
import sys
import time

import serial

sys.path.insert(0, "scripts")
import raw_micropython_upload as raw


DIAGNOSTIC_CODE = r'''
from machine import UART, Pin
import time
import e77_radio

uart = UART(2, baudrate=256000, tx=Pin(15), rx=Pin(16))
parser = e77_radio.E77FrameParser()
seq = 0xB7
uart.write(e77_radio.build_frame(seq, e77_radio.CMD_BOOTLOADER))

deadline = time.ticks_add(time.ticks_ms(), 2500)
ok = False
while time.ticks_diff(deadline, time.ticks_ms()) > 0:
    if uart.any():
        data = uart.read(64) or b""
        for b in data:
            frame = parser.feed(b)
            if frame and frame.seq == seq and frame.cmd == e77_radio.CMD_BOOTLOADER:
                ok = not (frame.flags & e77_radio.FLAG_ERROR)
                print("BOOTLOADER_ACK", ok)
                break
        if ok:
            break
    else:
        time.sleep_ms(5)

if not ok:
    print("BOOTLOADER_ACK", False)
'''


def main():
    parser = argparse.ArgumentParser(
        description="Ask the master device to put its attached nRF bridge into UF2 bootloader mode."
    )
    parser.add_argument("--master", default="COM21", help="Master device USB serial port")
    parser.add_argument("--baud", type=int, default=115200)
    args = parser.parse_args()

    with serial.Serial(args.master, args.baud, timeout=0.05, write_timeout=2) as port:
        raw.stop_running_program(port)
        raw.enter_raw_repl(port)
        try:
            output = raw.exec_raw(port, DIAGNOSTIC_CODE, timeout=5).decode("utf-8", "replace")
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
