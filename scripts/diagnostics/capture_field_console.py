import argparse
import sys
import time
from pathlib import Path

import serial


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", required=True)
    parser.add_argument("--seconds", type=float, default=45)
    parser.add_argument("--output", type=Path, required=True)
    options = parser.parse_args()
    deadline = time.monotonic() + options.seconds
    with serial.Serial(options.port, 115200, timeout=0.1) as port, options.output.open("wb") as output:
        print("Listening to field console on", options.port, flush=True)
        while time.monotonic() < deadline:
            data = port.read(port.in_waiting or 1)
            if data:
                output.write(data)
                output.flush()
                sys.stdout.write(data.decode("utf-8", "replace"))
                sys.stdout.flush()


if __name__ == "__main__":
    main()
