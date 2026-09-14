from __future__ import print_function

import argparse
import sys
import time

import serial

sys.path.insert(0, "scripts")
import raw_micropython_upload as raw


BOOTLOADER_CODE = """
import machine
machine.bootloader()
"""

def touch_1200_baud(port_name):
    try:
        port = serial.Serial(port_name, 1200, timeout=0.05, write_timeout=1)
        port.dtr = False
        port.rts = False
        time.sleep(0.1)
        port.close()
        return True
    except Exception as exc:
        print("1200-baud touch failed: %s" % exc)
        return False


def request_via_micropython(port_name, baud):
    with serial.Serial(port_name, baud, timeout=0.05, write_timeout=1) as port:
        try:
            port.dtr = False
            port.rts = False
            time.sleep(0.1)
        except Exception:
            pass

        raw.stop_running_program(port, timeout=3)
        raw.enter_raw_repl(port)
        raw.exec_raw(port, BOOTLOADER_CODE, timeout=2)


def looks_like_bootloader_disconnect(exc):
    text = str(exc)
    return (
        "ClearCommError failed" in text or
        "The device does not recognize the command" in text or
        "The system cannot find the file specified" in text
    )


def main():
    parser = argparse.ArgumentParser(
        description="Put a directly connected nRF52840 MicroPython board into UF2 bootloader mode."
    )
    parser.add_argument("--port", required=True, help="nRF board USB serial port, for example COM15")
    parser.add_argument("--baud", type=int, default=115200)
    parser.add_argument(
        "--touch-first",
        action="store_true",
        help="Try the 1200-baud UF2 touch before machine.bootloader().",
    )
    args = parser.parse_args()

    if args.touch_first and touch_1200_baud(args.port):
        print("1200-baud bootloader touch sent on %s." % args.port)
        time.sleep(1)
        return

    try:
        request_via_micropython(args.port, args.baud)
        print("MicroPython bootloader requested on %s." % args.port)
    except Exception as exc:
        if looks_like_bootloader_disconnect(exc):
            print("MicroPython bootloader request sent; %s disconnected." % args.port)
            time.sleep(1)
            return

        print("MicroPython bootloader request failed: %s" % exc)
        if touch_1200_baud(args.port):
            print("1200-baud bootloader touch sent on %s." % args.port)

    time.sleep(1)


if __name__ == "__main__":
    main()
