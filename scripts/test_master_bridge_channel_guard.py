from __future__ import print_function

import argparse
import sys
import time

import serial

sys.path.insert(0, "scripts")
import raw_micropython_upload as raw


CODE = r'''
import __main__ as master

FORCED_CHANNEL = {forced_channel}

print("SAVED_CHANNEL", master.gui.radio_channel)
print("FORCE_CHANNEL", FORCED_CHANNEL, master.set_local_radio_channel(FORCED_CHANNEL))
print("BEFORE_STATS", master.get_local_radio_stats())
master.gui.discover_modules()
print("AFTER_STATS", master.get_local_radio_stats())
print("MODULE_COUNT", len(master.gui.modules))
for dev in master.gui.modules:
    print("MODULE", dev.get("unique_id"), dev.get("group_id"), dev.get("module_online"))
'''


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--master", default="COM21")
    parser.add_argument("--forced-channel", type=int, default=40)
    parser.add_argument("--timeout", type=float, default=80.0)
    args = parser.parse_args()

    code = CODE.format(forced_channel=args.forced_channel)

    with serial.Serial(args.master, 115200, timeout=0.05, write_timeout=2) as port:
        raw.stop_running_program(port)
        raw.enter_raw_repl(port)
        try:
            output = raw.exec_raw(port, code, timeout=args.timeout).decode("utf-8", "replace")
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
