"""Watch the bridge's ARM key safety latch (usb-master-0.4.3+) like the app.

Keeps an app-style host session alive and prints the switch position and the
latch once a second. With --try-arm it also asks the bridge to ARM a module
UID that does not exist (0x00000001), to show whether the bridge accepts or
refuses ARM right now. No real module is armed; when the probe exits, the
host session expires and the bridge broadcasts STOP/DISARM as usual.

    py -3 scripts/diagnostics/probe_arm_key_latch.py --port COM12 --seconds 30 --try-arm
"""
import argparse
import os
import sys
import time
from pathlib import Path

import serial

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "lib"))
from probe_usb_bridge import request  # noqa: E402  (also puts main/ on the path)
import nfcp  # noqa: E402

SWITCH = {0: "unknown", 1: "SAFE", 2: "ARM"}
NO_SUCH_MODULE = 0x00000001


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--port", required=True)
    parser.add_argument("--seconds", type=float, default=30)
    parser.add_argument("--try-arm", action="store_true")
    options = parser.parse_args()
    # Node id 1, as the app sends: the bridge rejects other master frames as
    # malformed, which would look like a latch refusal.
    arm_frame = nfcp.NFCP(1).build_arm_unique(NO_SUCH_MODULE)
    with serial.Serial(options.port, 115200, timeout=0.2, write_timeout=2) as port:
        sequence = 0

        def call(command, payload=b""):
            nonlocal sequence
            sequence = (sequence + 1) & 255
            return request(port, sequence, command, payload)

        info = bytes(call(0x03).payload)
        print("Firmware:", info[9:9 + info[8]].decode("ascii", "replace"))
        session = os.urandom(4)   # a new tag per run, as the app does per connect
        end = time.monotonic() + options.seconds
        last = None
        while time.monotonic() < end:
            call(0x18, session)
            status = bytes(call(0x17).payload)
            switch, faults = status[2], status[5]
            latched = bool(faults & 32)
            arm = ""
            if options.try_arm:
                try:
                    call(0x10, arm_frame)
                    arm = "ARM accepted"
                except RuntimeError:
                    arm = "ARM refused"
            line = "switch %-7s latched %-5s %s" % (SWITCH.get(switch, switch), latched, arm)
            if line != last:
                print(time.strftime("%H:%M:%S"), line, flush=True)
                last = line
            time.sleep(1)


if __name__ == "__main__":
    main()
