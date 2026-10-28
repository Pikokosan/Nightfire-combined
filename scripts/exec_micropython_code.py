from __future__ import print_function

import argparse
import sys
import time

import serial

sys.path.insert(0, "scripts")
import raw_micropython_upload as raw


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", required=True)
    parser.add_argument("--baud", type=int, default=115200)
    parser.add_argument("--timeout", type=float, default=10.0)
    parser.add_argument(
        "--startup-delay",
        type=float,
        default=0.0,
        help="Seconds to wait after opening a port that resets the device.",
    )
    parser.add_argument("code_file")
    args = parser.parse_args()

    with open(args.code_file, "r") as f:
        code = f.read()

    with serial.Serial(args.port, args.baud, timeout=0.05, write_timeout=2) as port:
        try:
            port.dtr = False
            port.rts = False
            time.sleep(0.2)
        except Exception:
            pass

        port.reset_input_buffer()
        if args.startup_delay > 0:
            time.sleep(args.startup_delay)
            port.reset_input_buffer()
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
