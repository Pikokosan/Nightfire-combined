from __future__ import print_function

import argparse
import sys
import time

import serial


CTRL_A = b"\x01"
CTRL_B = b"\x02"
CTRL_C = b"\x03"
CTRL_D = b"\x04"


def log(text):
    print(text)
    sys.stdout.flush()


def read_until(port, marker, timeout):
    end = time.time() + timeout
    data = bytearray()

    while time.time() < end:
        waiting = port.in_waiting
        if waiting:
            data.extend(port.read(waiting))
            if marker in data:
                return bytes(data)
        else:
            time.sleep(0.02)

    raise RuntimeError("Timed out waiting for %r. Last=%r" % (marker, bytes(data[-300:])))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", required=True)
    parser.add_argument("--baud", type=int, default=115200)
    parser.add_argument("--timeout", type=float, default=30.0)
    parser.add_argument("code_file")
    args = parser.parse_args()

    with open(args.code_file, "r") as f:
        code = f.read().encode("utf-8")

    log("OPEN %s" % args.port)
    with serial.Serial(args.port, args.baud, timeout=0.05, write_timeout=2) as port:
        try:
            port.dtr = False
            port.rts = False
            time.sleep(0.2)
        except Exception:
            pass

        port.reset_input_buffer()

        log("CTRL-C")
        port.write(CTRL_C)
        time.sleep(0.2)
        port.write(CTRL_C)
        time.sleep(0.5)
        out = port.read(port.in_waiting or 1)
        if out:
            sys.stdout.write(out.decode("utf-8", "replace"))
            sys.stdout.flush()

        log("CTRL-A")
        port.write(CTRL_A)
        raw_banner = read_until(port, b"raw REPL; CTRL-B to exit\r\n>", 8)
        sys.stdout.write(raw_banner.decode("utf-8", "replace"))
        sys.stdout.flush()

        log("EXEC")
        port.write(code)
        port.write(CTRL_D)

        response = read_until(port, b"\x04\x04>", args.timeout)
        ok_at = response.find(b"OK")
        if ok_at < 0:
            raise RuntimeError("Raw REPL did not accept command: %r" % response[-300:])

        payload = response[ok_at + 2:]
        first_eof = payload.find(CTRL_D)
        second_eof = payload.find(CTRL_D, first_eof + 1)
        if first_eof < 0 or second_eof < 0:
            raise RuntimeError("Incomplete raw response: %r" % response[-300:])

        normal = payload[:first_eof]
        error = payload[first_eof + 1:second_eof]

        sys.stdout.write(normal.decode("utf-8", "replace"))
        sys.stdout.flush()

        if error:
            raise RuntimeError(error.decode("utf-8", "replace"))

        log("CTRL-B")
        port.write(CTRL_B)
        time.sleep(0.2)


if __name__ == "__main__":
    main()
