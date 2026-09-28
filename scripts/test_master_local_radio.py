from __future__ import print_function

import argparse
import sys
import time

import serial

sys.path.insert(0, "scripts")
import raw_micropython_upload as raw


CODE = r'''
import __main__ as master

print("RADIO_PING", master.radio_ping(1500))
print("RADIO_INFO", master.get_local_radio_info(1500))
print("RADIO_STATS", master.get_local_radio_stats(1500))
try:
    print("ENSURE_CHANNEL", master.gui.radio_channel, master.ensure_local_radio_channel(master.gui.radio_channel, 1500))
except Exception as e:
    print("ENSURE_CHANNEL_ERROR", e)
'''


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--master", default="COM21")
    parser.add_argument("--baud", type=int, default=115200)
    parser.add_argument("--timeout", type=float, default=10.0)
    args = parser.parse_args()

    with serial.Serial(args.master, args.baud, timeout=0.05, write_timeout=2) as port:
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
