from __future__ import print_function

import argparse
import sys
import time

import serial

sys.path.insert(0, "scripts")
import raw_micropython_upload as raw


CODE = r'''
import __main__ as master

devices = master.discover()
print("DISCOVER_COUNT", len(devices))
for dev in devices:
    uid_text = dev.get("unique_id", "0x00000000")
    print("DISCOVER", uid_text, "CH", dev.get("group_id", "--"))
    try:
        uid = int(uid_text, 16) & 0xFFFFFFFF
        info = master.request_info_for_uid(uid)
    except Exception as e:
        info = None
        print("INFO_ERROR", uid_text, e)
    if info:
        print("INFO", uid_text, info)
    else:
        print("INFO_TIMEOUT", uid_text)
'''


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--master", default="COM21")
    parser.add_argument("--timeout", type=float, default=40.0)
    args = parser.parse_args()

    with serial.Serial(args.master, 115200, timeout=0.05, write_timeout=2) as port:
        raw.stop_running_program(port)
        raw.enter_raw_repl(port)
        try:
            output = raw.exec_raw(port, CODE, timeout=args.timeout).decode("utf-8", "replace")
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
