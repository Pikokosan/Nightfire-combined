from __future__ import print_function

import argparse
import sys
import time

import serial

sys.path.insert(0, "scripts/lib")
import raw_micropython_upload as raw


MASTER_CODE = """
import nfcp

tx = nfcp.NFCP(1)
sync_frame = tx.build_time_sync_broadcast(1234)
start_frame = tx.build_script_start_broadcast(0x22, 2000, 0, 4567)
print("SYNC_LEN", len(sync_frame))
print("START_LEN", len(start_frame))
print("HAS_TIME_SYNC", hasattr(nfcp, "CMD_TIME_SYNC"))
"""


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--master", default="COM21")
    args = parser.parse_args()

    with serial.Serial(args.master, 115200, timeout=0.05, write_timeout=2) as port:
        raw.stop_running_program(port)
        raw.enter_raw_repl(port)
        try:
            output = raw.exec_raw(port, MASTER_CODE, timeout=5).decode("utf-8", "replace")
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
