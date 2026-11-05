import argparse
import time

import serial
from probe_usb_bridge import request


def status(port, sequence):
    data = bytes(request(port, sequence, 0x17).payload)
    if len(data) != 15 or data[0] != 1:
        raise RuntimeError("Unsupported interface status")
    print("STATUS", {"switch": data[2], "leds": data[3], "session": data[4],
                     "faults": data[5], "switch_pin": data[10], "led_pin": data[11]})
    return data


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", required=True)
    options = parser.parse_args()
    with serial.Serial(options.port, 115200, timeout=0.1, write_timeout=2) as port:
        request(port, 1, 0x18, b"TEST")
        initial = status(port, 2)
        if initial[4] != 1:
            raise RuntimeError("Heartbeat did not activate the session")
        time.sleep(3.3)
        expired = status(port, 3)
        if expired[4] != 2 or not expired[5] & 4:
            raise RuntimeError("Session watchdog did not expire")
        request(port, 4, 0x18, b"TEST")
        renewed = status(port, 5)
        if renewed[4] != 1 or renewed[5] & 4:
            raise RuntimeError("Heartbeat did not renew the session")
        print("Interface heartbeat/watchdog verified; GPIOs were not changed.")


if __name__ == "__main__":
    main()
