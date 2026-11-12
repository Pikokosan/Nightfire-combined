"""Read or set the bridge's status LED brightness (usb-master-0.4.4+) like the app.

    py -3 scripts/diagnostics/probe_led_brightness.py --port COM12
    py -3 scripts/diagnostics/probe_led_brightness.py --port COM12 --set 80 --hold 5

--set needs an app-style host session, which this script keeps alive while it
runs (--hold seconds after setting, so the new level can be seen). The bridge
saves the new level in nrf_bridge_config.py. It refuses while modules may be
armed. When the script exits the host session expires, as it does when the
app disconnects.
"""
import argparse
import os
import sys
import time
from pathlib import Path

import serial

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "lib"))
from probe_usb_bridge import request  # noqa: E402


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--port", required=True)
    parser.add_argument("--set", type=int, help="new brightness, 1..255 (default level is 12)")
    parser.add_argument("--hold", type=float, default=3, help="seconds to keep the session after setting")
    options = parser.parse_args()
    with serial.Serial(options.port, 115200, timeout=0.2, write_timeout=2) as port:
        sequence = 0

        def call(command, payload=b""):
            nonlocal sequence
            sequence = (sequence + 1) & 255
            return bytes(request(port, sequence, command, payload).payload)

        info = call(0x03)
        print("Firmware:", info[9:9 + info[8]].decode("ascii", "replace"))
        session = os.urandom(4)
        call(0x18, session)
        # A new session tag makes the bridge broadcast STOP/DISARM first;
        # settings are refused until that has gone out (about 150 ms).
        time.sleep(0.5)
        call(0x18, session)
        status = call(0x17)
        if not status[1] & 64:
            raise SystemExit("This bridge has no LED brightness command (needs usb-master-0.4.4+).")
        print("Brightness:", call(0x1B)[0])
        if options.set is not None:
            print("Set to:", call(0x1B, bytes([options.set]))[0])
            end = time.monotonic() + options.hold
            while time.monotonic() < end:
                call(0x18, session)
                time.sleep(0.5)


if __name__ == "__main__":
    main()
